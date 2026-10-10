#!/usr/bin/env python3
"""Run reproducible M5 whole-shoe geometry cases in bounded Blender batches."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.design_store import atomic_write, dump
from tools.find_blender import find_blender
from tools.stress_cases import make_manifest
from tools.stress_inputs import normalized_last_signature, valid_last_signature


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def engine_fingerprint(root=ROOT):
    paths = sorted((root / "engine").rglob("*.py")) + [root / name for name in (
        "schema/shoe_params.schema.json", "requirements-geometry.txt",
        "tools/stress_cases.py", "tools/stress_inputs.py", "tools/run_stress.py", "tests/blender_stress_worker.py")]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0" + path.read_bytes() + b"\0")
    digest.update(b"normalized-last-input\0" + dump(normalized_last_signature(root)))
    return digest.hexdigest()


def read_json(path, default=None):
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError):
        return default


def read_lines(path):
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError):
            continue  # A killed worker may have left one partial final line.
        if isinstance(item, dict):
            result.append(item)
    return result


def healthy(item):
    if not isinstance(item, dict):
        return False
    volume = item.get("signed_volume_mm3")
    return (item.get("status") == "pass" and item.get("closed") is True
            and item.get("finite_coordinates") is True and type(item.get("connected_components")) is int and item["connected_components"] == 1
            and item.get("normal_orientation") == "outward"
            and all(type(item.get(key)) is int and item[key] == 0 for key in ("boundary_edges", "non_manifold_edges", "non_manifold_vertices",
                                                  "degenerate_faces", "duplicate_vertices", "near_duplicate_vertices",
                                                  "self_intersection_pairs", "inconsistent_winding_edges"))
            and isinstance(volume, (int, float)) and not isinstance(volume, bool) and math.isfinite(volume) and volume > 0)


def accepted_record(record, case):
    matches = (record.get("case_id") == case["case_id"] and record.get("fingerprint") == case["fingerprint"]
               and record.get("effective_params") == case["effective"])
    if not matches or record.get("status") not in ("pass", "fail"):
        return False
    return record["status"] != "pass" or (healthy(record.get("mesh_health")) and healthy(record.get("deformed_last_health")))


def verified_batch(directory, manifest, cases):
    metadata = read_json(directory / "driver.json", {})
    report = read_json(directory / "report.json", {})
    if not isinstance(metadata, dict) or not isinstance(report, dict):
        return {}
    expected_input = manifest.get("normalized_last")
    if (metadata.get("engine_sha256") != manifest["engine_sha256"]
            or metadata.get("plan_sha256") != manifest["plan_sha256"]
            or not valid_last_signature(expected_input)
            or metadata.get("normalized_last") != expected_input
            or any(report.get(key) != expected_input for key in
                   ("normalized_last_expected", "normalized_last_before", "normalized_last_after"))
            or report.get("normalized_last_unchanged") is not True
            or metadata.get("driver_error") is not None or not metadata.get("ended_at")
            or report.get("sources_unchanged") is not True or report.get("designs_unchanged") is not True
            or report.get("status") not in ("pass", "fail")):
        return {}
    ids = metadata.get("case_ids")
    raw = read_lines(directory / "results.jsonl")
    fail_count = sum(record.get("status") == "fail" for record in raw)
    expected_worker_code = 2 if report["status"] == "fail" else 0
    allowed_process_codes = (1, 2) if expected_worker_code == 2 else (0,)
    all_cases = {case["case_id"]: case for case in manifest["cases"]}
    if (not isinstance(ids, list) or not ids or not all(isinstance(key, str) and key in all_cases for key in ids)
            or len(set(ids)) != len(ids) or report.get("case_ids") != ids
            or type(metadata.get("exit_code")) is not int or metadata["exit_code"] not in allowed_process_codes
            or type(report.get("exit_code")) is not int or report["exit_code"] != expected_worker_code
            or type(report.get("requested_count")) is not int or report["requested_count"] != len(ids)
            or type(report.get("completed_count")) is not int or report["completed_count"] != len(ids)
            or len(raw) != len(ids) or [record.get("case_id") for record in raw] != ids
            or any(record.get("status") not in ("pass", "fail") for record in raw)
            or type(report.get("pass_count")) is not int or report["pass_count"] != len(raw) - fail_count
            or type(report.get("fail_count")) is not int or report["fail_count"] != fail_count
            or (expected_worker_code == 0 and fail_count) or (expected_worker_code == 2 and not fail_count)):
        return {}
    expected = {case["case_id"]: case for case in cases}
    records = {}
    for record in raw:
        case = expected.get(record.get("case_id"))
        if case and accepted_record(record, case):
            records[case["case_id"]] = {**record, "batch_directory": str(directory)}
    return records


def stop_process(process):
    """Bound shutdown of a running worker, including one ignoring SIGTERM."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_batch(root, executable, manifest_path, manifest, cases, directory, case_timeout, stop_event=None):
    directory.mkdir(parents=True, exist_ok=False)
    command = [executable, "-b", "--factory-startup", "--disable-autoexec", "--threads", "1", "--python-exit-code", "1",
               "--python", str(root / "tests/blender_stress_worker.py"), "--", "--manifest", str(manifest_path),
               "--case-ids", ",".join(case["case_id"] for case in cases), "--out", str(directory)]
    metadata = {"engine_sha256": manifest["engine_sha256"], "plan_sha256": manifest["plan_sha256"],
                "normalized_last": manifest["normalized_last"],
                "case_ids": [case["case_id"] for case in cases], "command": command, "started_at": timestamp()}
    atomic_write(directory / "driver.json", dump(metadata))
    failure = None
    launched = time.time()
    if stop_event is not None and stop_event.is_set():
        failure = "用户中断运行，批次未启动；本批证据不可复用。"
    else:
        try:
            if engine_fingerprint(root) != manifest["engine_sha256"]:
                failure = "运行前引擎代码或规范化鞋楦已变化，拒绝混用测试证据。"
        except (OSError, ValueError) as error:
            failure = "运行前无法复核代码／输入摘要，拒绝启动批次：" + str(error)
    if failure is None:
        environment = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
        try:
            with (directory / "blender.log").open("wb") as log:
                process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, env=environment)
                try:
                    while process.poll() is None:
                        if stop_event is not None and stop_event.is_set():
                            failure = "用户中断运行，已停止工作进程；本批证据不完整，不可复用。"
                            stop_process(process)
                            break
                        events = directory / "events.jsonl"
                        activity = events.stat().st_mtime if events.exists() else launched
                        if time.time() - activity > case_timeout:
                            failure = f"案例超过 {case_timeout:g} 秒未完成，已停止批次。"
                            stop_process(process)
                            break
                        time.sleep(.25)
                finally:
                    stop_process(process)
                metadata["exit_code"] = process.returncode
        except OSError as error:
            failure = str(error)
    try:
        if engine_fingerprint(root) != manifest["engine_sha256"]:
            failure = "运行期间引擎代码或规范化鞋楦变化，本批证据作废；请用新输入重新运行。"
    except (OSError, ValueError) as error:
        failure = "运行后无法复核代码／输入摘要，本批证据作废：" + str(error)
    metadata.update(ended_at=timestamp(), driver_error=failure)
    atomic_write(directory / "driver.json", dump(metadata))
    records = {} if failure else verified_batch(directory, manifest, cases)
    for case in cases:
        if case["case_id"] not in records:
            records[case["case_id"]] = {"case_id": case["case_id"], "fingerprint": case["fingerprint"],
                                         "effective_params": case["effective"], "status": "error",
                                         "error": failure or "工作进程没有完整结果或未通过素材／设计完整性复核。",
                                         "batch_directory": str(directory)}
    return records


def write_summary(directory, manifest, selected, records, started, *, running, interrupted=False):
    counts = {status: sum(record["status"] == status for record in records.values()) for status in ("pass", "fail", "error")}
    full = len(selected) == len(manifest["cases"])
    complete = len(records) == len(selected)
    status = "running" if running else ("pass" if complete and counts["pass"] == len(selected) else "fail")
    if interrupted:
        status = "interrupted"
    if status == "pass" and not full:
        status = "partial_pass"
    failures = [{"case_id": record["case_id"], "status": record["status"], "error": record.get("error"),
                 "batch_directory": record["batch_directory"]} for record in records.values() if record["status"] != "pass"]
    summary = {"status": status, "geometry_only": True, "manufacturing_certified": False,
               "seed": manifest["seed"], "plan_sha256": manifest["plan_sha256"], "engine_sha256": manifest["engine_sha256"],
               "normalized_last": manifest["normalized_last"], "interrupted": interrupted,
               "planned": manifest["counts"], "selected": len(selected), "completed": len(records),
               "missing": len(selected) - len(records), "counts": counts, "failures": failures,
               "full_plan": full, "started_at": started, "updated_at": timestamp(),
               "random_pass": sum(case["kind"] == "random" and records.get(case["case_id"], {}).get("status") == "pass" for case in selected),
               "boundary_pass": sum(case["kind"] == "boundary" and records.get(case["case_id"], {}).get("status") == "pass" for case in selected),
               "license_reminder": "本鞋楦仅限非商业使用，不得分发原始鞋楦及衍生网格。"}
    atomic_write(directory / "summary.json", dump(summary))
    ordered = [records[case["case_id"]] for case in selected if case["case_id"] in records]
    atomic_write(directory / "results.jsonl", ("".join(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n" for record in ordered)).encode())
    lines = ["# M5 实际几何压力测试", "", f"状态：{status}；完成 {len(records)} / {len(selected)}。", "",
             f"通过 {counts['pass']}；几何失败 {counts['fail']}；运行错误 {counts['error']}。", "",
             f"固定种子 {manifest['seed']}；随机通过 {summary['random_pass']}；额外边界通过 {summary['boundary_pass']}。", "",
             "每个案例均调用原参数化整鞋引擎，保留两档实际采样精度和全部几何健康门槛。", "",
             "本压力测试不执行渲染、打印导出或 M3 厚度／排粉／工艺认证，几何通过不代表可打印。", "",
             "当前设计与原始素材只读；每批次需通过前后完整性复核。", "",
             "部分选择的运行不能算作完成整个 M5 计划。", "", summary["license_reminder"]]
    if failures:
        lines += ["", "失败案例：", "", *[f"- {item['case_id']}：{item['error']}（详见 {item['batch_directory']}）" for item in failures]]
    if interrupted:
        lines += ["", "用户已中断运行；未启动案例仍记为缺失。恢复时仅复用完成全部完整性复核的批次。"]
    atomic_write(directory / "summary.md", ("\n".join(lines) + "\n").encode())
    return summary


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--count", type=int, default=1000)
    result.add_argument("--seed", type=int, default=20261009)
    result.add_argument("--no-boundaries", action="store_true")
    result.add_argument("--jobs", type=int, default=2)
    result.add_argument("--batch-size", type=int, default=20)
    result.add_argument("--case-timeout", type=float, default=300)
    result.add_argument("--out", default="out/m5")
    result.add_argument("--resume", action="store_true", help="仅复用同计划、同代码且完整性检查通过的案例")
    result.add_argument("--kind", choices=("all", "random", "boundary"), default="all", help="先验证边界时选择 boundary；部分运行不算整个阶段通过")
    result.add_argument("--only", help="只运行指定 case_id，以逗号分隔；部分结果不算整个阶段通过")
    return result


def main(argv=None):
    options = parser().parse_args(argv)
    if not 1 <= options.jobs <= 4 or not 1 <= options.batch_size <= 50 or not math.isfinite(options.case_timeout) or options.case_timeout < 10:
        raise ValueError("并发数 1–4，批次大小 1–50，案例超时至少 10 秒。")
    output = (ROOT / options.out).resolve()
    if not output.is_relative_to(ROOT / "out") or not output.name.startswith("m5"):
        raise ValueError("压力测试输出须位于 out/ 下独立的 m5* 目录。")
    plan = make_manifest(options.count, options.seed, not options.no_boundaries)
    plan["normalized_last"] = normalized_last_signature(ROOT)
    plan["engine_sha256"] = engine_fingerprint()
    directory = output / "runs" / (plan["plan_sha256"][:12] + "-" + plan["engine_sha256"][:12])
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("此计划已有压力测试运行，请等待或查看 summary.json。")
        manifest_path = directory / "manifest.json"
        existing = read_json(manifest_path)
        if existing is not None and existing != plan:
            raise ValueError("运行目录已有不同计划，拒绝覆盖。")
        atomic_write(manifest_path, dump(plan))
        selected = [case for case in plan["cases"] if options.kind == "all" or case["kind"] == options.kind]
        if not selected:
            raise ValueError("所选类别没有案例。")
        if options.only:
            ids = set(options.only.split(","))
            selected = [case for case in selected if case["case_id"] in ids]
            if ids != {case["case_id"] for case in selected}:
                raise ValueError("--only 含不存在的 case_id。")
        batches = directory / "batches"
        batches.mkdir(exist_ok=True)
        records = {}
        if options.resume:
            for previous in sorted(batches.iterdir()):
                if previous.is_dir():
                    records.update(verified_batch(previous, plan, selected))
            records = {key: record for key, record in records.items() if record["status"] == "pass"}
        pending = [case for case in selected if case["case_id"] not in records]
        executable = find_blender()
        started = timestamp()
        write_summary(directory, plan, selected, records, started, running=True)
        atomic_write(output / "latest.json", dump({"run_directory": str(directory), "manifest": str(manifest_path)}))
        print(f"M5：计划 {plan['counts']['random']} 随机 + {plan['counts']['boundary']} 边界；本次选择 {len(selected)}，复用 {len(records)}。", flush=True)
        print(f"结果目录：{directory}", flush=True)
        offset = max([int(path.name) for path in batches.iterdir() if path.name.isdigit()] + [-1]) + 1
        chunks = [pending[index:index + options.batch_size] for index in range(0, len(pending), options.batch_size)]
        stop_event = threading.Event()
        executor = ThreadPoolExecutor(max_workers=options.jobs)
        futures = []
        interrupted = False
        try:
            for index, chunk in enumerate(chunks):
                futures.append(executor.submit(run_batch, ROOT, executable, manifest_path, plan, chunk,
                                               batches / f"{offset + index:04d}", options.case_timeout, stop_event))
            for future in as_completed(futures):
                records.update(future.result())
                summary = write_summary(directory, plan, selected, records, started, running=True)
                print(f"M5：{summary['completed']}/{len(selected)}，通过 {summary['counts']['pass']}，失败 {summary['counts']['fail']}，错误 {summary['counts']['error']}。", flush=True)
        except KeyboardInterrupt:
            interrupted = True
            print("M5：收到中断，正在取消排队批次并停止活动工作进程……", flush=True)
        finally:
            stop_event.set()
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
        if interrupted:
            for future in futures:
                if not future.cancelled():
                    records.update(future.result())
        summary = write_summary(directory, plan, selected, records, started, running=False, interrupted=interrupted)
        print(f"M5 结果：{summary['status']}；{directory / 'summary.md'}", flush=True)
        return 130 if interrupted else (0 if summary["status"] in ("pass", "partial_pass") else 2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print("M5 错误：" + str(error), file=sys.stderr)
        raise SystemExit(1)
