"""Report actual M1 evidence without treating mesh validity as shape acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shoe_poc.reporting import artifact_manifest, sha256, utc_now, write_json


def report(output: Path, document: Path) -> dict:
    def read(name):
        path = output / f"{name}.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    last, geometry, mesh, tests, execution, parameters, roundtrip, surface = [read(name) for name in (
        "last_metadata", "geometry_report", "validation_report", "test_report",
        "execution_report", "parameter_change_report", "roundtrip_report", "surface_report")]
    evidence_provenance = {}
    if execution.get("run_id"):
        expected = execution["run_id"]
        provenance = tests.get("build_provenance", {})
        model = output / "base_shoe.stl"
        tests_current = (provenance.get("run_id") == expected and model.is_file()
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
        "parameter_change": parameters.get("status", "NOT_TESTED"),
        "tests": "FAIL" if tests_fail else "PASS" if tests_pass else "NOT_TESTED",
    }
    technical = ("FAIL" if "FAIL" in executed.values() else
                 "PASS" if all(value == "PASS" for value in executed.values()) else "PARTIAL")
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
            "scope": "Independent M1 CLI build compared in CAD volume and coordinates; no byte identity claim"},
        "algorithm_version": geometry.get("algorithm_version", "Not recorded by this generation"),
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
    json_block = lambda value: "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"
    text = f"""# Milestone 1 — 基础鞋型修复验收报告

## 1. 完成状态

**{result['overall_status']}**。技术检查 {technical}；外形及用户验收 PENDING。水密性和单实体不代表基础鞋型、制造或穿着验收通过。

| 验收层级 | 实际状态 |
| --- | --- |
{tables}

## 2. 已完成功能

算法版本：`{result['algorithm_version']}`。具体成形方法、拟合过程和实际参数以 `geometry_report.json` 为准。没有对应运行证据的能力不计为通过。

## 3. 实际生成文件

目录：`{output.resolve()}`。当前实际存在的文件：

{chr(10).join('- `' + name + '`' for name in files)}

原 `output/m1/` 用于对照，不被本次修复覆盖。所有源资产和派生 STEP/STL/图像保持本地、Git 忽略。

## 4. 执行命令

执行记录、参数 SHA-256 和耗时以 `execution_report.json` 为准。修复验证使用以下命令；命令列在此处不代表执行成功：

```sh
.venv/bin/python -m shoe_poc run --spec input/specs/shoe_spec.json --output output/m1_repair --compare-with output/m1/base_shoe.stl
.venv/bin/python scripts/verify_parameters.py --spec input/specs/shoe_spec.json --baseline output/m1_repair --output output/m1_repair/variant
.venv/bin/python -m pytest -q --report-json output/m1_repair/test_report.json
.venv/bin/python scripts/report_m1.py --output output/m1_repair --document docs/milestone1_repair_report.md
```

## 5. 测试结果

通过 {counts.get('PASS', '未运行')}；失败 {counts.get('FAIL', '未运行')}；未执行/跳过 {counts.get('NOT_TESTED', '未运行')}。原始结果保留在 `test_report.json`，合成几何测试不替代真实鞋楦测试。

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

参数变化：{parameters.get('status', 'NOT_TESTED')}；底高 `{parameters.get('baseline_value_mm', '未知')} → {parameters.get('variant_value_mm', '未知')}` mm；实际 CAD 体积变化 `{parameters.get('cad_volume_delta_mm3', '未测量')}` mm³。

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
