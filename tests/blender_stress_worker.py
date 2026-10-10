"""M5 actual-geometry worker, run inside a fresh Blender process.

The driver supplies a fixed manifest and a bounded list of case IDs. Every
case uses its effective sampling setting and the existing full mesh-health
checks. This worker never renders, exports a mesh, or edits design versions.
"""
import argparse
from datetime import datetime
import gc
import hashlib
import json
import math
from pathlib import Path
import resource
import sys
import time
import traceback
from zoneinfo import ZoneInfo

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.build import build_shoe
from engine.shoe.last import load_last, source_manifest
from tools.stress_inputs import normalized_last_signature, valid_last_signature


def timestamp():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def append_record(path, value):
    """Make each completed record durable before attempting the next case."""
    import os
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def design_snapshot():
    """Protect the sole design truth, version history, and edit audit files."""
    paths = [ROOT / "designs" / name for name in ("current.json", "CHANGELOG.md", "operations.jsonl")]
    history = ROOT / "designs/history"
    if history.exists():
        paths.extend(path for path in history.rglob("*") if path.is_file())
    snapshot = {}
    for path in sorted(paths):
        key = path.relative_to(ROOT).as_posix()
        if path.is_file():
            raw = path.read_bytes()
            snapshot[key] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        else:
            snapshot[key] = None
    return snapshot


def peak_memory():
    # Darwin ru_maxrss is bytes; Linux ru_maxrss is KiB. It is a process
    # high-water mark, not the current RSS or a per-case allocation estimate.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    unit = "bytes" if sys.platform == "darwin" else "KiB"
    divisor = 1024 * 1024 if unit == "bytes" else 1024
    return {"ru_maxrss": raw, "ru_maxrss_unit": unit,
            "process_peak_rss_mib": raw / divisor}


def data_snapshot():
    return {name: set(getattr(bpy.data, name)) for name in ("objects", "meshes", "materials")}


def clean_generated_data(baseline):
    """Also remove orphan data created before a failed build could return."""
    for name in ("objects", "meshes", "materials"):
        collection = getattr(bpy.data, name)
        for item in set(collection) - baseline[name]:
            collection.remove(item, do_unlink=True)
    gc.collect()
    if data_snapshot() != baseline:
        raise RuntimeError("本轮生成数据未完整清理，或基线 Blender 数据被修改。")


def require_recorded_health(health, label):
    """Retain the complete engine gate and assert the recorded contract."""
    if not isinstance(health, dict):
        raise ValueError(label + " 缺少健康报告。")
    zeros = ("boundary_edges", "non_manifold_edges", "non_manifold_vertices",
             "inconsistent_winding_edges", "degenerate_faces", "duplicate_vertices",
             "near_duplicate_vertices", "self_intersection_pairs")
    volume = health.get("signed_volume_mm3", float("nan"))
    if (health.get("status") != "pass" or health.get("closed") is not True
            or health.get("finite_coordinates") is not True
            or health.get("connected_components") != 1
            or health.get("normal_orientation") != "outward"
            or any(health.get(key) != 0 for key in zeros)
            or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume <= 0):
        raise ValueError(label + " 的完整网格健康门槛未通过：" + repr(health))


def read_cases(manifest_path, requested_ids):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("Manifest 必须含 cases 数组。")
    cases = {}
    for case in manifest["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Manifest case 必须是对象。")
        case_id = str(case.get("case_id", ""))
        if not case_id or case_id in cases:
            raise ValueError("Manifest case_id 缺失或重复：" + case_id)
        if not isinstance(case.get("effective"), dict) or not isinstance(case.get("requested"), dict):
            raise ValueError("Case 缺少 requested / effective 参数对象：" + case_id)
        if not isinstance(case.get("fingerprint"), str) or not case["fingerprint"]:
            raise ValueError("Case 缺少 fingerprint：" + case_id)
        cases[case_id] = case
    ids = [item.strip() for item in requested_ids.split(",")]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("--case-ids 必须是互不重复的非空 ID 列表。")
    missing = [item for item in ids if item not in cases]
    if missing:
        raise ValueError("Manifest 没有这些 case：" + ", ".join(missing))
    return manifest, [cases[item] for item in ids]


def run_case(case, source, baseline, output):
    started = time.monotonic()
    record = {"case_id": case["case_id"], "fingerprint": case["fingerprint"],
              "status": "fail", "requested": case["requested"],
              "effective_params": case["effective"], "clamps": case.get("clamps", []),
              "started_at": timestamp(), "mesh_health": None, "deformed_last_health": None,
              "voxel_mm": None, "grid_shape": None, "edge_finish": None, "mesh_extraction": None,
              "error": None, "traceback": None}
    append_record(output / "events.jsonl", {"event": "case_started", "case_id": case["case_id"],
                  "fingerprint": case["fingerprint"], "at": record["started_at"], **peak_memory()})
    built = None
    try:
        built = build_shoe(case["effective"], last_mesh=source, manufacturing=False)
        report = built.report
        record.update({key: report[key] for key in ("mesh_health", "deformed_last_health", "voxel_mm", "grid_shape")})
        record.update({key: report.get(key) for key in ("edge_finish", "mesh_extraction")})
        if report["effective_params"] != case["effective"]:
            raise ValueError("构建再次规范化改变了 manifest 的有效参数，拒绝把它算作原始 case。")
        require_recorded_health(record["deformed_last_health"], "形变鞋楦")
        require_recorded_health(record["mesh_health"], "整鞋")
        record["geometry_elapsed_seconds"] = report["geometry_elapsed_seconds"]
        record["clamp_messages"] = report["clamp_messages"]
        record["status"] = "pass"
    except Exception as error:
        record["error"] = str(error)
        record["traceback"] = traceback.format_exc()
    finally:
        # No exception or traceback object is retained in the result ledger.
        built = None
        try:
            clean_generated_data(baseline)
        except Exception as error:
            record["status"] = "fail"
            record["error"] = ((record["error"] + "\n") if record["error"] else "") + "清理失败：" + str(error)
            record["traceback"] = ((record["traceback"] + "\n") if record["traceback"] else "") + traceback.format_exc()
    record["elapsed_seconds"] = time.monotonic() - started
    record["ended_at"] = timestamp()
    record.update(peak_memory())
    append_record(output / "results.jsonl", record)
    append_record(output / "events.jsonl", {"event": "case_finished", "case_id": case["case_id"],
                  "fingerprint": case["fingerprint"], "status": record["status"],
                  "elapsed_seconds": record["elapsed_seconds"], "at": record["ended_at"], **peak_memory()})
    print(f"M5 case {case['case_id']}: {record['status']} ({record['elapsed_seconds']:.2f}s)", flush=True)
    return record["status"]


def run(manifest_path, requested_ids, output):
    started = time.monotonic()
    output = output.resolve()
    out_root = (ROOT / "out").resolve()
    if output == out_root or not output.is_relative_to(out_root):
        raise ValueError("Worker 输出必须位于项目 out/ 的独立批次子目录。")
    output.mkdir(parents=True, exist_ok=True)
    report = {"stage": "M5_geometry_batch", "status": "running", "manifest": str(manifest_path.resolve()),
              "blender_version": bpy.app.version_string, "started_at": timestamp(),
              "sources_unchanged": False, "designs_unchanged": False,
              "normalized_last_expected": None, "normalized_last_before": None,
              "normalized_last_after": None, "normalized_last_unchanged": False,
              "completed_count": 0, "pass_count": 0, "fail_count": 0,
              "renders_or_mesh_exports": False,
              "license": "本鞋楦仅限个人／非商业使用，不得分发原始鞋楦和衍生网格。"}
    write_json(output / "report.json", report)
    source_before = design_before = last_before = None
    try:
        manifest, cases = read_cases(manifest_path, requested_ids)
        report["requested_count"] = len(cases)
        report["case_ids"] = [case["case_id"] for case in cases]
        expected_last = manifest.get("normalized_last")
        report["normalized_last_expected"] = expected_last
        if not valid_last_signature(expected_last):
            raise ValueError("Manifest 缺少有效的规范化鞋楦摘要；请使用当前 driver 重新生成计划。")
        last_before = normalized_last_signature(ROOT)
        report["normalized_last_before"] = last_before
        if last_before != expected_last:
            raise RuntimeError("规范化鞋楦与计划的输入摘要不一致，批次未启动。")
        source_before = source_manifest()
        design_before = design_snapshot()
        source = load_last()
        baseline = data_snapshot()
        for case in cases:
            status = run_case(case, source, baseline, output)
            report["completed_count"] += 1
            report[status + "_count"] += 1
        report["sources_unchanged"] = source_manifest() == source_before
        report["designs_unchanged"] = design_snapshot() == design_before
        report["normalized_last_after"] = normalized_last_signature(ROOT)
        report["normalized_last_unchanged"] = last_before == report["normalized_last_after"] == expected_last
        if not all(report[key] for key in ("sources_unchanged", "designs_unchanged", "normalized_last_unchanged")):
            raise RuntimeError("批次期间源素材、规范化鞋楦或设计／历史发生变化，批次结果无效。")
        report["status"] = "fail" if report["fail_count"] else "pass"
        code = 2 if report["fail_count"] else 0
    except Exception as error:
        report["status"] = "error"
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        if source_before is not None:
            try:
                report["sources_unchanged"] = source_manifest() == source_before
            except Exception:
                report["sources_unchanged"] = False
        if design_before is not None:
            try:
                report["designs_unchanged"] = design_snapshot() == design_before
            except Exception:
                report["designs_unchanged"] = False
        if last_before is not None:
            try:
                report["normalized_last_after"] = normalized_last_signature(ROOT)
                report["normalized_last_unchanged"] = (last_before == report["normalized_last_after"]
                                                        == report["normalized_last_expected"])
            except Exception:
                report["normalized_last_unchanged"] = False
        code = 1
    report["exit_code"] = code
    report["ended_at"] = timestamp()
    report["elapsed_seconds"] = time.monotonic() - started
    report.update(peak_memory())
    write_json(output / "report.json", report)
    print(f"M5 batch {report['status']}: {report['completed_count']} completed, "
          f"{report['pass_count']} pass, {report['fail_count']} fail", flush=True)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case-ids", required=True)
    parser.add_argument("--out", type=Path, required=True)
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    options = parser.parse_args(arguments)
    return run(options.manifest, options.case_ids, options.out)


if __name__ == "__main__":
    # Blender may map Python SystemExit to its --python-exit-code setting;
    # report.json always retains the intended batch code (0 / 2 / 1).
    exit_code = main()
    if exit_code:
        raise SystemExit(exit_code)
