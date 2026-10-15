"""Report actual M1 evidence without treating mesh validity as shape acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex

from shoe_poc.reporting import artifact_manifest, sha256, utc_now, write_json


def report(output: Path, document: Path) -> dict:
    def read(name):
        path = output / f"{name}.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    last, geometry, mesh, tests, execution, parameters, roundtrip, surface = [read(name) for name in (
        "last_metadata", "geometry_report", "validation_report", "test_report",
        "execution_report", "parameter_change_report", "roundtrip_report", "surface_report")]
    repeatability = read("repeatability_report")
    design = read("design_report")
    component_previews = read("component_preview_report")
    test_path = output / "test_report.json"
    if (repeatability.get("run_id") != execution.get("run_id") or not test_path.is_file()
            or repeatability.get("test_report_sha256") != sha256(test_path)):
        repeatability = {}
    evidence_provenance = {}
    if execution.get("run_id"):
        expected = execution["run_id"]
        provenance = tests.get("build_provenance", {})
        model = output / "base_shoe.stl"
        tests_current = (provenance.get("directory") == str(output.resolve())
                         and provenance.get("run_id") == expected and model.is_file()
                         and provenance.get("base_shoe_stl_sha256") == sha256(model))
        parameters_current = parameters.get("baseline", {}).get("run_id") == expected
        evidence_provenance = {"run_id": expected, "test_report_matches_current_build": tests_current,
                               "parameter_report_matches_current_build": parameters_current}
        if not tests_current:
            tests = {}
        if not parameters_current:
            parameters = {}
    requirements = {}
    supplementary = {
        4: lambda name: "tests/unit/test_spec.py::" in name,
        14: lambda name: name.endswith("test_closed_mesh") or name.endswith("test_open_mesh_is_detected"),
        15: lambda name: "tests/unit/test_mesh_validation.py::" in name,
        18: lambda name: "tests/unit/test_spec.py::test_invalid_values_are_rejected" in name,
    }
    for index in range(1, 21):
        prefix = f"test_T{index:02d}"
        matching = [entry["status"] for name, entry in tests.get("tests", {}).items()
                    if name.split("::")[-1].startswith(prefix)
                    or supplementary.get(index, lambda name: False)(name)]
        requirements[f"T{index:02d}"] = ("FAIL" if "FAIL" in matching else
                                            "PASS" if matching and all(s == "PASS" for s in matching)
                                            else "NOT_TESTED")
    core_tests = all(requirements[f"T{i:02d}"] == "PASS" for i in (1, 2, 3, 5, 6, 7, 16, 19))
    tests_fail = (tests.get("pytest_exit_code", 0) != 0
                  or tests.get("counts", {}).get("FAIL", 0) > 0
                  or any(entry.get("status") == "FAIL" for entry in tests.get("tests", {}).values()))
    tests_pass = (tests.get("pytest_exit_code") == 0
                  and tests.get("counts", {}).get("FAIL") == 0
                  and core_tests and not tests_fail)
    executed = {
        "CAD_and_export_pipeline": execution.get("status", "NOT_TESTED"),
        "mesh_integrity": mesh.get("geometry_status", "NOT_TESTED"),
        "roundtrip": roundtrip.get("status", "NOT_TESTED"),
        "surface_samples": surface.get("geometry_status", "NOT_TESTED"),
        "exterior_junction_samples": geometry.get("junction", {}).get("geometry_status", "NOT_TESTED"),
        "bottom_round_samples": geometry.get("sole", {}).get("bottom_round_validation", {}).get("geometry_status", "NOT_TESTED"),
        "parameter_change": parameters.get("status", "NOT_TESTED"),
        "tests": "FAIL" if tests_fail else "PASS" if tests_pass else "NOT_TESTED",
    }
    if design:
        executed["annotated_design_geometry"] = (design.get("geometry_status", "NOT_TESTED")
            if design.get("run_id") == execution.get("run_id") else "NOT_TESTED")
    technical = ("FAIL" if "FAIL" in executed.values() else
                 "PASS" if all(value in {"PASS", "NOT_REQUIRED"} for value in executed.values()) else "PARTIAL")
    result = {
        "milestone": 1, "overall_status": "PARTIAL",
        "technical_status": technical, "appearance_acceptance": "PENDING",
        "user_acceptance": "PENDING", "generated_at": utc_now(),
        "acceptance_levels": {**executed, "global_minimum_wall_thickness": "NOT_VERIFIED",
                              "full_Phase0_end_to_end": "NOT_TESTED", "manufacturing_rules": "NOT_VERIFIED",
                              "physical_print": "NOT_TESTED", "wearability": "NOT_VERIFIED"},
        "test_requirements": requirements, "pytest": tests.get("counts", {}),
        "evidence_provenance": evidence_provenance,
        "same_spec_repeatability": {
            "status": requirements["T19"],
            "relative_deltas_by_protocol": repeatability.get("relative_deltas_by_protocol", {}),
            "absolute_volume_accuracy": "NOT_VERIFIED",
            "scope": "Independent M1 CLI build compared in CAD volume and coordinates; no byte identity claim"},
        "algorithm_version": geometry.get("algorithm_version", "Not recorded by this generation"),
        "annotated_design": {key: design.get(key) for key in (
            "geometry_status", "metrics_status", "appearance_acceptance", "baseline_directory",
            "baseline_files", "baseline_comparison", "forefoot_constraint") } if design else {},
        "annotation_assessment": {
            "1": "IMPROVED_PENDING_REVIEW", "2": "CONSTRAINED_UNRESOLVED",
            "3": "PARTLY_IMPROVED_PENDING_REVIEW", "4": "IMPROVED_PENDING_REVIEW",
            "5": "IMPROVED_PENDING_REVIEW", "6": "IMPROVED_PENDING_REVIEW",
            "7": "CONSTRAINED_UNRESOLVED", "8": "CONSTRAINED_UNRESOLVED",
        } if design else {},
        "component_preview_meshes": {name: value.get("geometry_status")
            for name, value in component_previews.get("components", {}).items()},
        "actual_source": last.get("source", {}),
        "limitations": geometry.get("limitations", []) + [
            "Appearance requires actual-image review and user acceptance; topology alone cannot qualify the shoe template.",
            "Sampled clearance/thickness are not a proof of the global minimum or physical fit.",
            "Orientation and foot side follow explicit configuration; unconfirmed values stay unconfirmed.",
            "SLS TPU parameters are experimental; manufacturing, physical printing and wear are not qualified.",
            "Purchased STEP and derived geometry remain local and ignored by Git."],
    }
    write_json(output / "acceptance_report.json", result)
    files = sorted(path.name for path in output.iterdir() if path.is_file())
    source = last.get("source_geometry", {})
    counts = tests.get("counts", {})
    tables = "\n".join(f"| {name} | {value} |" for name, value in result["acceptance_levels"].items())
    requirement_rows = "\n".join(f"| {name} | {status} |" for name, status in requirements.items())
    failures = [f"- `{name}`: {entry.get('reason', 'Failed')}" for name, entry in tests.get("tests", {}).items()
                if entry["status"] == "FAIL"]
    measurement = {key: value for key, value in surface.items()
                   if key not in {"samples", "excluded_samples", "checks", "unmeasurable_regions"}}
    geometry_summary = {key: geometry.get(key) for key in (
        "algorithm_version", "solid_count", "topology_valid", "last_intersection_volume_mm3",
        "cavity_intersection_volume_mm3", "opening_intersection_volume_mm3", "upper_wall_probe")}
    junction = geometry.get("junction", {}).get("validation", {})
    geometry_summary["junction_sampling"] = {key: junction.get(key) for key in (
        "geometry_status", "angle_limit_degrees", "exterior_sample_count", "regions",
        "excluded_boundary_sample_count", "measurement_error_count", "tolerance_scope")}
    rounding = geometry.get("sole", {}).get("bottom_round_validation", {})
    geometry_summary["bottom_round_sampling"] = {key: value for key, value in rounding.items() if key != "records"}
    json_block = lambda value: "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"
    resolved = read("shoe_spec")
    spec_command = shlex.quote(execution.get("spec_source", "input/specs/shoe_spec.json"))
    out_command = shlex.quote(str(output))
    doc_command = shlex.quote(str(document))
    variant_height = float(resolved.get("sole", {}).get("height_mm", 20))+5
    baseline_command = (f" --design-baseline {shlex.quote(design['baseline_directory'])}" if design.get("baseline_directory") else "")
    comparison = execution.get("comparison_input") or ("output/m1_repair/base_shoe.stl" if design else "output/m1/base_shoe.stl")
    compare_command = f" --compare-with {shlex.quote(comparison)}"
    test_environment = (f"SHOE_POC_SPEC={spec_command} SHOE_POC_REPAIR_OUTPUT={out_command} SHOE_POC_DESIGN_OUTPUT={out_command} " if design else f"SHOE_POC_REPAIR_OUTPUT={out_command} ")
    design_text = ""
    if design:
        underfoot = design.get("underfoot", {})
        design_text = f"""
## 标注修复的独立记录

实际设计描述符来自 CAD 截面、开口交线和最终实体法线测量，`metrics_status={design.get('metrics_status')}`；数值描述不自动成为外形通过。源楦/原腔体对称差、每一项检查与受约束前掌宽度见 `design_report.json`；参考腔体来源见 `baseline/reference_provenance.json`，不能把重建参考冒充原输出已有的 STEP。

鞋体最高点 `{design.get('shoe_peak_z_mm')}` mm。独立足底采样 `{underfoot.get('sample_count', len(underfoot.get('samples', [])))}` 点，最小观测材料区间 `{underfoot.get('minimum_measured_directional_material_mm')}` mm，检查状态 `{underfoot.get('geometry_status')}`；后跟、中足、前掌覆盖和误差逐项记录。鞋面采样排除鞋底加强区，不能替代该测量；两类有限采样都不证明全局最小厚度。

足底出口分类诊断 `{len(underfoot.get('classification_diagnostics', []))}` 处；这些位置的点分类与实际边界交叉不一致，测量必须由完整包围盒长度的独立 CAD 布尔直线确认。原短线复测会被任意线端截断，失败日志和119点诊断保留在 `debug/failed_footbed_replay/`、`debug/footbed_119_diagnosis.json`。复测使用独立几何长度、逐点覆盖和原1e-4mm容差；不以丢点或放宽标准消除失败。已有CAD/网格保持不变，导出后的原生STEP测量修订见 `post_build_revalidations`。

| 图中问题 | 本轮处理 | 状态界限 |
| --- | --- | --- |
| 1 / 4 鞋背高峰、侧面轮廓 | 前移鞋口前缘，使用三维切削曲面降低并展宽鞋背峰 | 有尺寸及同角度图证据，外形仍待确认 |
| 5 鞋口线条生硬 | 后跟与两侧采用连续高度变化；检查实际相交口沿闭环及切向 | 有限连续性检查不代替视觉和穿入验证 |
| 3 厚重直侧墙 | 减小底高与下缘外扩，增加曲壁控制 | 实际截面结果及图像为准，不能把输入位移当实测弧度 |
| 6 前掌上翘不足 | 提高外底翘度并提前翘度起点 | 实测外底升高，不改变楦内脚位 |
| 2 / 7 / 8 鞋头偏重、前掌宽、水滴比例 | 减少可避免的鞋底扩张，保留真实楦与1mm净空、2.5mm壁厚 | 前掌比例尚未解决，不能通过侵腔或缩楦宣称修复 |

标注 3 仍只得到有限改善：独立候选比较中，五个位置各七条实际 CAD 水平线的近竖直跨度占比没有下降，弦挠度变化左右混合；16mm底高减轻体量，不等于直侧墙已消除。2mm收腰造成新交线并因15.578°连接角度失败，不能选作通过结果。保留的1mm方案通过既有几何门禁，但不把这些结果写成八处外形全部修复。

"""
    text = f"""# Milestone 1 — 基础鞋型修复验收报告

## 1. 完成状态

**{result['overall_status']}**。技术检查 {technical}；外形及用户验收 PENDING。水密性和单实体不代表基础鞋型、制造或穿着验收通过。

| 验收层级 | 实际状态 |
| --- | --- |
{tables}

## 2. 已完成功能

算法版本：`{result['algorithm_version']}`。具体成形方法、拟合过程和实际参数以 `geometry_report.json` 为准。没有对应运行证据的能力不计为通过。

鞋面保留真实 CAD 足部曲面并做局部法向偏移，鞋口采用平顺曲线切削，鞋底使用低足部截面驱动的周期轮廓及真实外表面过渡带。端部和底边已按实际 CAD 检查。原计划的真实截面重建也已实现和测试，但鞋楦踝部的分叉截面使分片融合无法稳定满足要求，因此未选作生产成形方法；失败证据保留在调试目录。选定方法不重拟合源曲面，其源形状拟合误差为零；偏移精度另由独立采样测量，不能把源曲面误差当作壁厚误差。

## 3. 实际生成文件

目录：`{output.resolve()}`。当前实际存在的文件：

{chr(10).join('- `' + name + '`' for name in files)}

原 `output/m1/` 用于对照，不被本次修复覆盖。所有源资产和派生 STEP/STL/图像保持本地、Git 忽略。

## 4. 执行命令

执行记录、参数 SHA-256 和耗时以 `execution_report.json` 为准。修复验证使用以下命令；命令列在此处不代表执行成功：

```sh
.venv/bin/python -m shoe_poc run --spec {spec_command} --output {out_command}{compare_command}{baseline_command}
.venv/bin/python scripts/verify_parameters.py --spec {spec_command} --baseline {out_command} --output {out_command}/variant --height-mm {variant_height:g}
{test_environment}.venv/bin/python -m pytest -q --report-json {out_command}/test_report.json
.venv/bin/python scripts/report_m1.py --output {out_command} --document {doc_command}
```

## 5. 测试结果

通过 {counts.get('PASS', '未运行')}；失败 {counts.get('FAIL', '未运行')}；未执行/跳过 {counts.get('NOT_TESTED', '未运行')}。原始结果保留在 `test_report.json`，合成几何测试不替代真实鞋楦测试。

证据模式：`{tests.get('evidence_mode', 'Single pytest execution')}`。存在定向复测时，`source_runs` 保留原完整运行与后续实际结果及哈希；聚合表示各测试最后一次实际结果，不能解释为一次完整运行全部通过，原失败记录仍保留。

| 要求 | 实际测试状态 |
| --- | --- |
{requirement_rows}

{chr(10).join(failures) if failures else '没有已记录的失败测试；未执行项仍未执行。'}

## 6. 几何与采样检查

真实源鞋楦：{source.get('solid_count', '未知')} 实体、{source.get('face_count', '未知')} 曲面；实测尺寸 `{source.get('bbox_mm', {}).get('size', '未测量')}` mm。

{json_block(geometry_summary)}

独立采样摘要：

{json_block(measurement)}

完整位置、排除区域、无法测量区域和偏差见 `surface_report.json`。壁厚与间隙只对实际采样位置负责，不证明全局最小值。鞋口边缘、鞋底加强区和其他排除位置不能被统计结果掩盖。

辅助 STL 预览网格状态 `{result['component_preview_meshes']}`，原生面三角化覆盖见 `mesh_export_report.json`。鞋腔预览发生缺面时，不能把该 STL 用作鞋腔水密性证明或打印实体；使用 `cavity.step` 和真实 CAD 包含/对称差检查。最终整鞋 STL 由独立 `validation_report.json` 验证。失败的鞋腔三角化尝试保留在 `debug/cavity_export_diagnostics/`。

连接检查来自最终 CAD 的实际相邻面外向法向；完整记录见 `geometry_report.json` 的 `junction.validation`。2° 法向角度上限和 0.02 mm 底边半径误差上限均为本轮实验性数值目标，有限检查不证明全局 G1 连续或制造合格。

参数变化：{parameters.get('status', 'NOT_TESTED')}；底高 `{parameters.get('baseline_value_mm', '未知')} → {parameters.get('variant_value_mm', '未知')}` mm；实际 CAD 体积变化 `{parameters.get('cad_volume_delta_mm3', '未测量')}` mm³。

同参数重复构建采用固定的 Gauss–Kronrod / BSpline-span 体积协议，point 与 XY-plane 两项均须满足原 `1e-8` 相对重复性阈值；细化结果和两协议的绝对差异见 `repeatability_report.json`。默认非自适应体积积分的失败记录保留在调试目录，不能当成真实形状变化，也不通过放宽容差消除失败。积分误差估计只涉及 quadrature，不是完整 CAD 绝对体积误差界。

{json_block(result['same_spec_repeatability'])}

{design_text}
## 7. 性能

CLI 耗时 `{execution.get('elapsed_seconds', '未记录')}` 秒；阶段耗时 `{execution.get('timings_seconds', {})}`；进程峰值 RSS `{execution.get('environment', {}).get('process_peak_rss_bytes', '未记录')}` 字节；STL 三角面 `{mesh.get('triangles', '未记录')}`。采样 RSS 保护不是操作系统硬限制。Linux/Docker 未实际运行则保持 NOT_TESTED。

## 8. 当前问题

{chr(10).join('- ' + item for item in result['limitations'])}

外形评审要求：真实 STL 同角度同尺度对照中，没有突兀鞋口竖墙、大面积折面、平切端盖或鞋面/鞋底台阶。`comparison.png` 和剖面叠加图作为证据，未生成或未评审则不能通过外形验收。失败几何与日志保留在本地调试目录。

## 9. 后续任务

先完成本次修复的几何和外形验收。根据 `docs/PROMPT.md`，用户确认 M1 后才进入 M2；当前不实施晶格、AI 或制造平台。

## 10. 验收结论

**M1 当前状态 {result['overall_status']}。** 技术状态 {technical}，外形及用户验收尚待确认。制造、打印与穿着验证独立记录，未知状态不得自动转换为 PASS。
"""
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(text, encoding="utf-8")
    manifest = artifact_manifest(output)
    variant = output / "variant"
    if variant.is_dir():
        manifest.update({f"variant/{name}": record for name, record in artifact_manifest(variant).items()})
    write_json(output / "artifact_manifest.json", manifest)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/m1_repair"))
    parser.add_argument("--document", type=Path, default=Path("docs/milestone1_repair_report.md"))
    args = parser.parse_args()
    result = report(args.output, args.document)
    print(f"M1 {result['overall_status']}; technical {result['technical_status']}; appearance PENDING")
    return 3 if result["technical_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
