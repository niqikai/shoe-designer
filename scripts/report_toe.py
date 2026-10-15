"""Summarize the bounded toe repair without approving appearance or manufacture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shoe_poc.reporting import artifact_manifest, sha256, utc_now, write_json

REQUIRED_BUILD_OUTPUTS = {
    "toe_report.json", "design_report.json", "geometry_report.json", "shoe_spec.json",
    "base_shoe.step", "base_shoe.stl", "last_normalized.step", "last_metadata.json",
    "roundtrip_report.json", "validation_report.json", "surface_report.json", "mesh_export_report.json",
}
PARAMETER_FILES = {"base_shoe.step", "base_shoe.stl", "last_normalized.step", "shoe_spec.json"}


def _verify_file_records(directory, records, required):
    missing = required-set(records)
    if missing:
        raise ValueError(f"Missing artifact hash records: {', '.join(sorted(missing))}")
    for name, record in records.items():
        path = directory/name
        if (not path.is_file() or sha256(path) != record.get("sha256")
                or path.stat().st_size != record.get("bytes")):
            raise ValueError(f"Current build artifact hash/size mismatch: {name}")


def _test_status(tests):
    """A summary cannot hide a recorded failure or an unexecuted test."""
    rows, counts = tests.get("tests", {}), tests.get("counts", {})
    statuses = [row.get("status") for row in rows.values()]
    if "FAIL" in statuses or counts.get("FAIL", 0) or tests.get("pytest_exit_code") not in (None, 0):
        return "FAIL"
    actual = {status: statuses.count(status) for status in ("PASS", "FAIL", "NOT_TESTED")}
    complete = (statuses and all(status in actual for status in statuses)
                and counts == actual and tests.get("collected") == len(statuses))
    return ("PASS" if complete and tests.get("pytest_exit_code") == 0
            and actual["PASS"] > 0 and actual["NOT_TESTED"] == 0 else "NOT_VERIFIED")


def _parameter_status(parameters, execution, output):
    if parameters.get("baseline", {}).get("run_id") != execution["run_id"]:
        return "NOT_TESTED", "Parameter baseline does not identify the current run"
    if parameters.get("status") == "FAIL" or any(value is False for value in parameters.get("checks", {}).values()):
        return "FAIL", "A recorded parameter check failed"
    try:
        if parameters.get("status") != "PASS" or not parameters.get("checks") or not all(
                value is True for value in parameters["checks"].values()):
            raise ValueError("Missing complete passing parameter checks")
        for role in ("baseline", "variant"):
            record = parameters[role]
            directory = Path(record["directory"]).resolve()
            if (role == "baseline" and directory != output.resolve()) or (role == "variant" and directory == output.resolve()):
                raise ValueError("Parameter baseline/variant directory mismatch")
            live = json.loads((directory/"execution_report.json").read_text())
            if (record.get("run_id") != live.get("run_id") or live.get("status") != "PASS"
                    or record.get("execution_status") != "PASS"
                    or record.get("input_sha256") != execution["input_sha256"]
                    or live.get("input_sha256") != execution["input_sha256"]):
                raise ValueError("Parameter files no longer identify the recorded source/run")
            _verify_file_records(directory, record.get("files", {}), PARAMETER_FILES)
            _verify_file_records(directory, live.get("outputs", {}), PARAMETER_FILES)
        return "PASS", None
    except (KeyError, ValueError, OSError, TypeError) as error:
        return "NOT_VERIFIED", str(error)


def _guard_evidence(output):
    """Separate actual pytest supplement, bound to the script it exercises."""
    metadata_path = output/"debug/toe_report_guard_provenance.json"
    if not metadata_path.is_file():
        return {"status": "NOT_TESTED", "reason": "No separate acceptance-report guard run recorded"}
    try:
        metadata = json.loads(metadata_path.read_text())
        report_path = output/"debug/toe_report_guard_tests.json"
        test_source = Path(__file__).resolve().parents[1]/"tests/unit/test_toe_acceptance_report.py"
        if (metadata.get("report_script_sha256") != sha256(Path(__file__))
                or metadata.get("test_source_sha256") != sha256(test_source)
                or metadata.get("pytest_report_sha256") != sha256(report_path)):
            raise ValueError("Supplemental report guard evidence is stale")
        tests = json.loads(report_path.read_text())
        return {"status": _test_status(tests), "counts": tests.get("counts", {}),
                "report_script_sha256": metadata["report_script_sha256"],
                "pytest_report_sha256": metadata["pytest_report_sha256"],
                "scope": "Separate report guard tests; not aggregated with main geometry pytest counts"}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "NOT_VERIFIED", "reason": str(error)}


def report(output: Path, document: Path):
    def read(name):
        path = output / f"{name}.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    execution, geometry, toe, tests, parameters, design = [read(name) for name in (
        "execution_report", "geometry_report", "toe_report", "test_report",
        "parameter_change_report", "design_report")]
    if not execution.get("run_id") or toe.get("run_id") != execution["run_id"]:
        raise ValueError("Missing current toe build evidence")
    _verify_file_records(output, execution.get("outputs", {}), REQUIRED_BUILD_OUTPUTS)
    source = execution.get("input_sha256")
    if (not source or toe.get("source_sha256") != source
            or design.get("run_id") != execution["run_id"] or design.get("source_sha256") != source
            or read("last_metadata").get("source", {}).get("sha256") != source):
        raise ValueError("Toe/design/source evidence does not identify the current run and source")
    if toe.get("baseline_directory"):
        baseline_step = Path(toe["baseline_directory"])/"base_shoe.step"
        if not baseline_step.is_file() or sha256(baseline_step) != toe.get("baseline_step_sha256"):
            raise ValueError("Toe baseline CAD no longer matches the measured comparison")
    provenance = tests.get("build_provenance", {})
    tests_current = (provenance.get("run_id") == execution["run_id"]
        and provenance.get("directory") == str(output.resolve())
        and provenance.get("base_shoe_stl_sha256") == sha256(output/"base_shoe.stl"))
    counts = tests.get("counts", {}) if tests_current else {}
    tests_status = _test_status(tests) if tests_current else "NOT_VERIFIED"
    parameter_status, parameter_reason = _parameter_status(parameters, execution, output)
    guards = _guard_evidence(output)
    core = {}
    for requirement in ("T01", "T02", "T03", "T05", "T06", "T07", "T16", "T19"):
        matching = [row["status"] for name, row in tests.get("tests", {}).items()
                    if name.split("::")[-1].startswith("test_"+requirement)] if tests_current else []
        core[requirement] = ("FAIL" if "FAIL" in matching else "PASS"
                            if matching and all(v == "PASS" for v in matching) else "NOT_TESTED")
    core_status = "FAIL" if "FAIL" in core.values() else "PASS" if all(v == "PASS" for v in core.values()) else "NOT_VERIFIED"
    levels = {"CAD_STL_pipeline": execution["status"], "toe_finite_measurements": toe["geometry_status"],
              "design_finite_checks": design.get("geometry_status", "NOT_TESTED"),
              "tests": tests_status, "real_source_regression": core_status,
              "same_spec_repeatability": core["T19"], "sole_height_variant": parameter_status}
    if guards["status"] != "NOT_TESTED":
        levels["acceptance_report_guard_tests"] = guards["status"]
    technical = "FAIL" if "FAIL" in levels.values() else "PASS" if all(v == "PASS" for v in levels.values()) else "PARTIAL"
    result = {"generated_at": utc_now(), "run_id": execution["run_id"],
              "overall_status": "PARTIAL", "technical_status": technical,
              "appearance_acceptance": "PENDING", "acceptance_levels": levels,
              "core_test_requirements": core,
              "tests_current": tests_current, "test_counts": counts,
              "parameter_evidence_reason": parameter_reason,
              "supplemental_acceptance_guard": guards,
              "test_report_sha256": sha256(output/"test_report.json") if (output/"test_report.json").is_file() else None,
              "algorithm_version": geometry["algorithm_version"],
              "baseline_comparison": toe.get("baseline_comparison", {}),
              "representation_repair": geometry.get("representation_repair"),
              "performance": {"elapsed_seconds": execution.get("elapsed_seconds"),
                  "process_peak_rss_bytes": execution.get("environment", {}).get("process_peak_rss_bytes")},
              "scope": "Repair the toe shelf and lower front profile; freeze style choices pending actual-image review",
              "limitations": [
                  "Finite toe descriptors and material samples do not prove global wall thickness or curvature.",
                  "Native STEP geometric roundtrip is separate from native STEP remeshing: the diagnostic STEP reimport reproduced the missing offset-face triangulation. Reapply bounded native ShapeFix before remeshing STEP. Final STL is exported from corrected native CAD, with complete face coverage and no mesh hole filling.",
                  "Auxiliary cavity STL remains separately qualified; the actual closed cavity STEP and native Booleans supply fit evidence.",
                  "Forefoot width and overall styling are not approved by this toe repair.",
                  "Manufacturing NOT_VERIFIED; physical printing NOT_TESTED; wearability NOT_VERIFIED."]}
    write_json(output/"acceptance_report.json", result)
    rows = []
    for lane in toe.get("baseline_comparison", {}).get("lane_changes", []):
        flat = lane["maximum_terminal_floor_low_slope_run_mm"]
        wall = lane["maximum_front_near_vertical_run_mm"]
        rows.append(f"| {lane['y_mm']:.1f} | {flat['baseline_mm']:.2f} → {flat['candidate_mm']:.2f} | {wall['baseline_mm']:.2f} → {wall['candidate_mm']:.2f} |")
    footbed = design.get("underfoot", {})
    spec = read("shoe_spec")
    text = f"""# M1 鞋底前端修复记录

本轮技术状态 **{technical}**；M1 总体 **PARTIAL**，外形确认 **PENDING**。本轮处理鞋底前端的平台与直立端头，风格、前掌比例及装饰不继续扩展。基础轮廓确认后可冻结并进入后续阶段，制造和穿着另行验证。

## 实际修改

将源鞋楦末端处提前收平的翘度曲线替换为连续五次曲线。起点与平底二阶连续，向前持续转起；切削体终止面位于鞋体之外，避免再切出平台。与实际曲壁相交后保留原 R2 底边圆角检查。算法 `{geometry['algorithm_version']}`。

`toe_roll_radius_mm={spec['sole']['profile_parameters']['toe_roll_radius_mm']}` 控制未裁剪曲线端点的曲率，不代表最终鞋头是等半径圆弧。实际轮廓以原生 CAD 测量和重新导入 STL 的预览为准。原鞋楦、鞋腔、鞋面和鞋口配置保留，底高仍为 `{spec['sole']['height_mm']}` mm。

## 同位置原生 CAD 剖面

测量间距不超过 0.25 mm；3° 只是近水平／近直立段的描述约定，不是鞋类或制造标准。PASS 表示有限测量完成，不代表外形自动通过。

| Y 位置 mm | 末端近水平段 mm，旧 → 新 | 前端近直立段 mm，旧 → 新 |
| --- | --- | --- |
{chr(10).join(rows)}

同角度局部对比：`{output.resolve()}/toe_comparison.png`；整体对比：`comparison.png`；新预览：`preview.png`。图像均来自实际导出并重新导入的 STL。

## 几何、测试与性能

原鞋面遗漏材料 `{geometry.get('original_upper_omitted_volume_mm3')}` mm³；鞋楦、鞋腔、鞋口相交体积分别 `{geometry.get('last_intersection_volume_mm3')}`、`{geometry.get('cavity_intersection_volume_mm3')}`、`{geometry.get('opening_intersection_volume_mm3')}` mm³，容差仍为 0.01 mm³。底边圆角、连接、单实体、水密性和 STEP 几何重导入以各报告为准。

足底采样 `{len(footbed.get('samples', []))}` 点，最小观测材料区间 `{footbed.get('minimum_measured_directional_material_mm')}` mm；这是有限方向采样，不是全局最小壁厚证明。

本轮测试 `{tests_status}`，实际结果 `{json.dumps(counts, ensure_ascii=False)}`；测试绑定当前运行 `{execution['run_id']}` 的状态 `{tests_current}`。真实鞋楦回归 `{core_status}`，独立 CLI 重跑及同配置两种体积积分协议的可重复性 `{core['T19']}`；逐项结果见 `test_report.json`，原始体积数据在 `debug/pytest_final/test_T19_real_headless_cli_M1_0/volume_repeatability_measurements.json`。底高完整变体检查 `{parameter_status}`，具体生成高度和实际尺寸差见 `parameter_change_report.json`。不把旧版模型的测试计为当前算法结果。

验收报告保护测试单独记录为 `{guards['status']}`，计数 `{json.dumps(guards.get('counts', {}), ensure_ascii=False)}`；该补充运行绑定当前报告脚本和测试源码的哈希，不并入主几何测试计数。记录见 `debug/toe_report_guard_tests.json` 与 `debug/toe_report_guard_provenance.json`。

正式生成耗时 `{execution.get('elapsed_seconds')}` 秒，进程峰值 RSS `{result['performance']['process_peak_rss_bytes']}` bytes；详见 `execution_report.json` 和资源记录。并行执行会影响耗时。

## 失败与原生表示修复

前两候选的 R2 圆角失败：切削体终止面落在鞋体内，产生额外平台和圆角轮廓；失败 STEP／日志保存在 `debug/candidate1/`、`debug/candidate2/`。第三候选消除该终止面干涉，未放宽几何门槛。

第三候选有一处偏移曲面的边界参数无法直接三角化。仅对新布尔结果做 ShapeFix 的首轮正式构建虽然覆盖全部曲面，仍漏掉外鞋面的一片三角形，水密性失败；该失败及初次测试的实际失败与中断记录保存在 `debug/failed_formal1/`。没有把该轮写成通过。

选定流程先在临时 STEP 中规范化原生曲线表示，再做 ShapeFix；三种诊断网格精度均闭合，详见 `debug/formal_mesh_diagnosis/`。正式构建要求规范化和修复合计包围盒差 ≤1e−5 mm、CAD 对称差 ≤0.01 mm³，再执行全部排除和厚度检查。修复前 CAD 保存在 `debug/before_representation_repair.step`，实际修复量见 `geometry_report.json`。

独立候选验证的对称差为 0 mm³；3,826 个双向实际面边界距离样本最大约 1.74e−13 mm，另有20点补充覆盖稀薄面。完整记录及文件哈希在 `debug/candidate3/meshing_repair/shape_fix_combined_bounded_verification.json`。有限采样不宣称全局精确等价。

**STEP 再网格化限制：** 诊断中，修复后的 STEP 重新导入仍会重现偏移面漏三角化，需要再次应用相同的原生表示修复。STEP 几何重导入与 STEP 直接再网格化是不同检查。交付 STL 来自已修复的原生 CAD，检查全部原生面覆盖；没有补洞、移动网格顶点或平滑法线。辅助鞋腔 STL 的限制另见 `component_preview_report.json`，不能用该预览证明鞋腔拓扑。

原 `output/m1/`、`output/m1_repair/`、`output/m1_design_refine/` 保留，文件哈希核对见 `debug/prior_artifacts_preservation.json`。制造 **NOT_VERIFIED**，实体打印 **NOT_TESTED**，穿着 **NOT_VERIFIED**。
"""
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(text)
    write_json(output/"artifact_manifest.json", artifact_manifest(output))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/m1_toe_refine"))
    parser.add_argument("--document", type=Path, default=Path("docs/milestone1_toe_refine_report.md"))
    args = parser.parse_args()
    result = report(args.output, args.document)
    print(result["technical_status"], result["overall_status"])
