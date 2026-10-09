"""M4 reports and preview comparisons, kept under the ignored output root."""
import base64
from datetime import datetime
import html
import json
import math
from pathlib import Path
import shutil
import subprocess
import time

from engine.params import read_schema
from tools.design_store import atomic_write, changes, dump
from tools.semantic import EditError

PRINT_FILES = [f"shoe_{side}{pose}.{ext}" for side in ("right", "left")
               for pose in ("", "_print") for ext in ("stl", "glb")]
PREVIEW_FILES = ["four_views.png", "cutaway.png", "thin_locations.png"] + [
    f"previews/{name}.png" for name in ("shoe_side", "shoe_top", "shoe_front", "shoe_iso",
                                         "cutaway_side", "cutaway_iso", "thin_side", "thin_iso", "build_iso")]


def output_path(root, value):
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to((root / "out").resolve()) or path == (root / "out").resolve():
        raise EditError("输出目录须位于本项目 out/ 下的子目录，避免覆盖代码或原始素材。")
    if any(path.is_relative_to((root / name).resolve()) for name in ("out/m4", "out/logs")):
        raise EditError("out/m4/ 与 out/logs/ 用于历史预览和日志，请另选生成目录。")
    return path


def cache_previews(root, output, params):
    report_path = output / "report.json"
    if not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_bytes())
    except (ValueError, UnicodeError):
        return None
    if not isinstance(report, dict) or report.get("effective_params") != params or report.get("original_sources_unchanged") is not True:
        return None
    if report.get("status") not in ("pass", "warning", "fail"):
        return None
    destination = root / "out/m4/versions" / f"v{params['revision']:03d}"
    destination.mkdir(parents=True, exist_ok=True)
    for name in PREVIEW_FILES:
        source = output / name
        target = destination / name
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        else:
            target.unlink(missing_ok=True)
    atomic_write(destination / "report.json", dump(report))
    atomic_write(destination / "params.json", dump(params))
    return destination


def invalidate_output(output):
    output.mkdir(parents=True, exist_ok=True)
    for name in PRINT_FILES + PREVIEW_FILES + ["report.json", "features.json", "effective_params.json", "manufacturing_report.md", "edit_report.json", "edit_report.md"]:
        (output / name).unlink(missing_ok=True)


def run_backend(root, output, params):
    logs = root / "out/logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    log = logs / f"edit-v{params['revision']:03d}-{stamp}.log"
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        completed = subprocess.run([str(root / "tools/run.sh"), str(root / "designs/current.json"), "--out", str(output)],
                                   cwd=root, stdout=handle, stderr=subprocess.STDOUT, check=False)
    path = output / "report.json"
    report = json.loads(path.read_bytes()) if path.is_file() else {}
    return {"exit_code": completed.returncode, "report": report,
            "log": str(log), "elapsed_seconds": time.monotonic() - started}


def validate_result(result, output, params):
    report = result.get("report", {})
    report = report if isinstance(report, dict) else {}
    measured = report.get("manufacturing", {})
    measured = measured if isinstance(measured, dict) else {}
    valid_version = report.get("effective_params") == params
    allowed = measured.get("export_allowed") is True
    mesh = report.get("mesh_health", {})
    checks = [mesh] + [measured.get(key, {}) for key in
             ("thickness", "powder_removal", "build_volume", "overhang")]
    required_ok = all(isinstance(item, dict) and item.get("status") in ("pass", "warning", "not_applicable") for item in checks)
    overall_ok = (report.get("status") in ("pass", "warning")
                  and isinstance(mesh, dict) and mesh.get("status") == "pass"
                  and measured.get("status") in ("pass", "warning") and measured.get("blockers") == [])
    if result.get("exit_code") == 0 and valid_version and allowed and required_ok and overall_ok and report.get("original_sources_unchanged") is True:
        units = {"stl_coordinate_unit": "mm", "glb_coordinate_unit": "m",
                 "print_stl_coordinate_unit": "mm", "print_glb_coordinate_unit": "m"}
        exports = report.get("exports", {})
        exports = exports if isinstance(exports, dict) else {}
        names = {key: f"shoe_{params['foot_side']}{'_print' if key.startswith('print_') else ''}.{key.rsplit('_', 1)[-1]}"
                 for key in ("stl", "glb", "print_stl", "print_glb")}
        raw_paths = [exports.get(key) for key in names]
        files = [Path(value) for value in raw_paths] if all(isinstance(value, str) and value for value in raw_paths) else []
        pngs = [output / name for name in PREVIEW_FILES if name in
                ("four_views.png", "previews/shoe_side.png", "previews/shoe_top.png", "previews/shoe_front.png", "previews/shoe_iso.png", "previews/build_iso.png")]
        if params["midsole_structure"] == "lattice":
            pngs.append(output / "cutaway.png")
        if (len(files) == 4 and all(path.resolve() == output / name and path.is_file() for path, name in zip(files, names.values()))
                and all(exports.get(key) == unit for key, unit in units.items())
                and all(path.is_file() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for path in pngs)):
            return 0, report.get("status", "warning"), "生成、制造筛查、预览与候选导出完成。"
    # Even a malformed success response must never leave apparently valid files.
    for name in PRINT_FILES:
        (output / name).unlink(missing_ok=True)
    if result.get("exit_code") == 2 and valid_version and not allowed and report.get("status") == "fail":
        return 2, "fail", "本轮未通过制造门槛，已阻止打印导出；参数已保存，可撤销或继续调整。"
    return 1, "error", "本轮生成未完成或结果与当前设计不一致，已阻止打印导出；参数已保存，可撤销。"


def change_lines(delta):
    properties = read_schema()["properties"]
    return [f"{properties[item['parameter']].get('title', item['parameter'])}：{item['before']} → {item['after']}"
            for item in delta]


def write_edit_report(output, report):
    atomic_write(output / "edit_report.json", dump(report))
    lines = ["# M4 对话操作报告", "", report["summary"], "",
             f"当前版本：v{report['revision']:03d}；操作：{report['action']}。", "",
             "\n".join("- " + line for line in change_lines(report["changes"])) or "参数没有变化。", "",
             *["- " + note for note in report["notes"] + report["clamps"]], ""]
    measured = report.get("manufacturing", {})
    measured = measured if isinstance(measured, dict) else {}
    for key in ("thickness", "powder_removal", "build_volume"):
        item = measured.get(key, {})
        if isinstance(item, dict) and isinstance(item.get("message"), str):
            lines += [item["message"], ""]
    warnings = measured.get("warnings", [])
    warnings = warnings if isinstance(warnings, list) else []
    lines += [*["- " + note for note in warnings if isinstance(note, str)], "",
              "回弹、重量、稳定性仅为设计倾向，建议先打印脚跟局部样验证。", "",
              "下一步可调整外观、导出精细候选，或撤销／回到历史版本。", "",
              "本鞋楦仅限非商业使用，不得分发。"]
    atomic_write(output / "edit_report.md", ("\n".join(lines) + "\n").encode("utf-8"))


def compare_versions(store, left, right, *, dry_run=False):
    first, second = store.read_revision(left), store.read_revision(right)
    result = {"action": "compare", "left_revision": left, "right_revision": right,
              "changes": changes(first, second), "summary": f"对比 v{left:03d} 与 v{right:03d}；差异 {len(changes(first, second))} 项。"}
    if dry_run:
        return result
    destination = store.root / "out/m4/comparisons" / f"v{left:03d}-v{right:03d}"
    destination.mkdir(parents=True, exist_ok=True)
    figures = []
    for revision, params in ((left, first), (right, second)):
        cache = store.root / "out/m4/versions" / f"v{revision:03d}"
        try:
            cached_params = json.loads((cache / "params.json").read_bytes())
            report = json.loads((cache / "report.json").read_bytes())
            png = (cache / "previews/shoe_iso.png").read_bytes()
            voxel = report.get("voxel_mm") if isinstance(report, dict) else None
            complete = (cached_params == params and isinstance(report, dict) and report.get("effective_params") == params
                        and report.get("original_sources_unchanged") is True and report.get("status") in ("pass", "warning", "fail")
                        and isinstance(voxel, (int, float)) and not isinstance(voxel, bool) and math.isfinite(voxel) and voxel > 0
                        and png[:8] == b"\x89PNG\r\n\x1a\n")
        except (OSError, ValueError, UnicodeError):
            complete = False
        if complete:
            image = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            caption = f"v{revision:03d} · 制造筛查 {report['status']} · {report['voxel_mm']:g} mm 采样"
            figures.append(f'<figure><img src="{image}" alt="v{revision:03d} 鞋款预览"><figcaption>{html.escape(caption)}</figcaption></figure>')
        else:
            figures.append(f'<figure><p>v{revision:03d} 尚无匹配的预览缓存，本次只比较参数。</p></figure>')
    rows = "".join(f"<tr><td>{html.escape(line)}</td></tr>" for line in change_lines(result["changes"]))
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>鞋款版本对比</title><style>body{{font:16px system-ui;margin:32px;background:#f5f7f7;color:#233b3b}}h1{{font-size:24px}}.views{{display:flex;flex-wrap:wrap;gap:20px}}figure{{margin:0;flex:1;min-width:280px}}img{{width:100%;border-radius:12px}}figcaption{{padding:12px}}table{{border-collapse:collapse;margin:24px 0}}td{{padding:10px;border-bottom:1px solid #d4dfdd}}p{{line-height:1.6}}</style>
<h1>{html.escape(result['summary'])}</h1><div class="views">{''.join(figures)}</div><table>{rows or '<tr><td>设计参数相同。</td></tr>'}</table>
<p>这是历史参数和实际预览对比；每个版本的制造结论见各自报告。图片不代表力学测试。</p><p>本鞋楦仅限非商业使用，不得分发。</p></html>'''
    page = destination / "comparison.html"
    atomic_write(page, document.encode("utf-8"))
    result["comparison_html"] = str(page)
    atomic_write(destination / "comparison.json", dump(result))
    return result
