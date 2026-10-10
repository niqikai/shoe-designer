#!/usr/bin/env python3
"""One command: interpret/edit/undo -> version -> Blender -> M3 -> previews/report."""
import argparse
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.design_store import DesignStore, changes
from tools.edit_outputs import (PRINT_FILES, cache_previews, change_lines, compare_versions,
                                invalidate_output, output_path, run_backend, validate_result, write_edit_report)
from tools.semantic import EditError, apply_sets, interpret, number


def revision(value):
    text = re.sub(r"^(?:第|[vV])|(?:版)$", "", str(value).strip())
    result = number(text)
    if not isinstance(result, int) or result < 0:
        raise EditError("版本号须为非负整数，例如 4 或 v004。")
    return result


def oral_control(text):
    if text is None:
        return None
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).strip("。！!，,")
    text = re.sub(r"^(?:请|帮我)", "", text)
    if re.fullmatch(r"撤销(?:上(?:一|1)次(?:修改)?)?|undo", text, re.I):
        return "undo", None
    if re.fullmatch(r"对比上一版|与上一版对比", text):
        return "compare", []
    match = re.fullmatch(r"(?:回到|恢复)(?:第)?([vV]?\d+|[零〇一二两三四五六七八九十百千]+)版?", text)
    if match:
        return "restore", revision(match.group(1))
    token = r"([vV]?\d+|[零〇一二两三四五六七八九十百千]+)"
    match = re.fullmatch(r"对比(?:第)?" + token + r"版?(?:和|与)(?:第)?" + token + r"版?", text)
    if match:
        return "compare", [revision(match.group(1)), revision(match.group(2))]
    return None


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("text", nargs="?", help="口语要求，例如：鞋头再圆一点，脚跟再软一点")
    control = result.add_mutually_exclusive_group()
    control.add_argument("--undo", action="store_true", help="撤销上一项设计修改；保留递增版本")
    control.add_argument("--restore", help="回到指定版本的参数，例如 v004")
    control.add_argument("--compare", nargs="*", help="零参数对比上一版，一参数对比该版与当前，两参数对比两版")
    control.add_argument("--list", action="store_true", help="列出现有版本，不生成模型")
    result.add_argument("--set", dest="assignments", action="append", default=[], metavar="KEY=VALUE", help="显式指定已支持参数，可重复")
    result.add_argument("--patch", type=Path, help="参数补丁 JSON 文件，不允许指定版本元数据")
    result.add_argument("--reason", help="追加到设计记录的修改原因")
    result.add_argument("--out", default="out/m3", help="项目 out/ 下的输出子目录")
    result.add_argument("--dry-run", action="store_true", help="只解释和比较，不写设计、历史、输出或运行 Blender")
    result.add_argument("--json", action="store_true", help="最终结果输出为 JSON；构建日志单独保存")
    return result


def execute(options, root, runner):
    store = DesignStore(root)
    output = output_path(store.root, options.out)
    spoken = oral_control(options.text)
    explicit_control = options.undo or options.restore is not None or options.compare is not None or options.list
    if explicit_control and options.text:
        raise EditError("口语要求与 --undo／--restore／--compare／--list 不能同时使用。")
    action = "edit"
    target = None
    if spoken:
        action, target = spoken
    elif options.undo:
        action = "undo"
    elif options.restore is not None:
        action, target = "restore", revision(options.restore)
    elif options.compare is not None:
        action, target = "compare", [revision(value) for value in options.compare]
    elif options.list:
        action = "list"
    if action != "edit" and (options.assignments or options.patch):
        raise EditError("撤销、回到版本、对比、列表操作不能同时附加参数修改。")
    if action == "edit" and not (options.text or options.assignments or options.patch):
        raise EditError("请输入口语修改、--set、--undo、--restore 或 --compare。")
    readonly = options.dry_run or action in ("compare", "list")
    with store.locked(readonly=readonly):
        _, before, initial_clamps = store.read_current()
        if action == "list":
            return 0, {"action": action, "revision": before["revision"], "revisions": store.revisions(), "summary": "现有版本：" + ", ".join(f"v{value:03d}" for value in store.revisions())}
        if action == "compare":
            if len(target) > 2:
                raise EditError("对比最多指定两个版本。")
            if not target:
                earlier = [value for value in store.revisions() if value < before["revision"]]
                if not earlier:
                    raise EditError("当前没有上一版可对比。")
                left, right = max(earlier), before["revision"]
            elif len(target) == 1:
                left, right = target[0], before["revision"]
            else:
                left, right = target
            if not options.dry_run:
                cache_previews(store.root, store.root / "out/m3", before)
            return 0, compare_versions(store, left, right, dry_run=options.dry_run)
        notes, clamps, matched = [], list(initial_clamps), []
        candidate = dict(before)
        if action == "undo":
            target = store.undo_target(before["revision"])
            if target is None:
                raise EditError("当前没有可撤销的设计修改。")
        if action in ("undo", "restore"):
            candidate = store.read_revision(target)
            notes.append(f"恢复 v{target:03d} 的有效参数；历史保留，新版本号递增。")
        elif options.text:
            interpreted = interpret(options.text, before, store.root / "docs/semantic_map.md")
            candidate, notes, matched = interpreted.params, interpreted.notes, interpreted.matched_rules
            clamps.extend(interpreted.clamps)
        assignments = list(options.assignments)
        if options.patch:
            values = json.loads(options.patch.read_bytes())
            if not isinstance(values, dict):
                raise EditError("--patch 必须是参数 JSON 对象。")
            assignments.extend(key + "=" + json.dumps(value, ensure_ascii=False) for key, value in values.items())
        if assignments:
            candidate, explicit_clamps = apply_sets(candidate, assignments)
            clamps.extend(explicit_clamps)
        delta = changes(before, candidate)
        reason = options.reason or options.text or (f"M4 {action}" + (f" v{target:03d}" if target is not None else "; ".join(assignments)))
        report = {"action": action, "request": options.text or reason, "reason": reason, "previous_revision": before["revision"],
                  "restored_from": target, "revision": before["revision"], "changes": delta,
                  "notes": list(dict.fromkeys(notes)), "clamps": list(dict.fromkeys(clamps)), "matched_rules": matched}
        if options.dry_run:
            report.update(status="dry_run", effective_params=candidate, summary="仅预览解释和实际截断结果；未写设计、备份或生成文件。")
            return 0, report
        for path in dict.fromkeys((store.root / "out/m3", output)):
            cache_previews(store.root, path, before)
        candidate, record, delta, extra_clamps = store.save(candidate, action=action, reason=reason, restored_from=target)
        expected_raw = store.current.read_bytes()
        report.update(revision=candidate["revision"], changes=delta, saved=record is not None)
        report["clamps"] = list(dict.fromkeys(report["clamps"] + extra_clamps))
        invalidate_output(output)
        print(f"v{candidate['revision']:03d}：正在生成、校验和渲染……", file=sys.stderr, flush=True)
        try:
            generated = runner(store.root, output, candidate)
        except (OSError, ValueError) as error:
            generated = {"exit_code": 1, "report": {}, "error": str(error)}
        if not isinstance(generated.get("report"), dict):
            generated["report"] = {}
        code, status, summary = validate_result(generated, output, candidate)
        if store.current.read_bytes() != expected_raw:
            for name in PRINT_FILES:
                (output / name).unlink(missing_ok=True)
            code, status, summary = 1, "error", "生成期间 current.json 被外部修改，本次产物已作废；保留外部改动，请重新生成。"
        elif code in (0, 2) and generated.get("report", {}).get("effective_params") == candidate:
            cache_previews(store.root, output, candidate)
        backend = generated.get("report", {})
        backend = backend if isinstance(backend, dict) else {}
        backend_clamps = backend.get("clamp_messages", [])
        if backend.get("effective_params") == candidate and isinstance(backend_clamps, list):
            report["clamps"] = list(dict.fromkeys(report["clamps"] + [
                message for message in backend_clamps if isinstance(message, str) and message.strip()]))
        measured = backend.get("manufacturing", {})
        measured = measured if isinstance(measured, dict) else {}
        report.update(status=status, summary=summary, generation={key: value for key, value in generated.items() if key != "report"},
                      manufacturing=measured,
                      exports=backend.get("exports", {}) if code == 0 else {},
                      previews=backend.get("previews", {}), output_directory=str(output),
                      effective_params=candidate, license_reminder="本鞋楦仅限非商业使用，不得分发。")
        write_edit_report(output, report)
        report["edit_report"] = str(output / "edit_report.md")
        return code, report


def main(argv=None, *, root=ROOT, runner=run_backend):
    options = parser().parse_args(argv)
    try:
        code, report = execute(options, Path(root), runner)
    except (EditError, ValueError, OSError, KeyError) as error:
        code, report = 1, {"status": "error", "summary": str(error)}
    if options.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(report["summary"])
        if report.get("revision") is not None:
            print(f"当前版本：v{report['revision']:03d}")
        for line in change_lines(report.get("changes", [])) + report.get("notes", []) + report.get("clamps", []):
            print("- " + line)
        if report.get("edit_report"):
            print("操作报告：" + report["edit_report"])
        if report.get("comparison_html"):
            print("预览对比：" + report["comparison_html"])
        if report.get("manufacturing"):
            print(report["manufacturing"].get("summary", ""))
            print("本鞋楦仅限非商业使用，不得分发。")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
