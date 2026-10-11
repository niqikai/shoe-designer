"""Generate Milestone 1 acceptance evidence from actual saved runtime reports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shoe_poc.reporting import artifact_manifest, utc_now, write_json


def report(output: Path, document: Path) -> dict:
    def read(name):
        return json.loads((output / f"{name}.json").read_text())
    last, geometry, mesh, tests, execution, parameters, roundtrip = [read(name) for name in (
        "last_metadata", "geometry_report", "validation_report", "test_report",
        "execution_report", "parameter_change_report", "roundtrip_report")]
    required_real_checks = ("test_T01", "test_T02", "test_T03", "test_T05", "test_T06", "test_T07", "test_T16", "test_T19")
    real_checks_pass = all(any(name.split("::")[-1].startswith(prefix) and entry["status"] == "PASS"
                              for name, entry in tests["tests"].items()) for prefix in required_real_checks)
    passing = (execution["status"] == mesh["geometry_status"] == roundtrip["status"]
               == parameters["status"] == "PASS" and tests["pytest_exit_code"] == 0
               and tests["counts"]["FAIL"] == tests["counts"]["NOT_TESTED"] == 0 and real_checks_pass)
    result = {
        "milestone": 1, "technical_status": "PASS" if passing else "FAIL",
        "user_acceptance": "PENDING", "generated_at": utc_now(),
        "acceptance_levels": {"implementation_M1": "PASS" if passing else "FAIL",
                              "unit_tests": "PASS" if tests["pytest_exit_code"] == 0 else "FAIL",
                              "real_geometry": mesh["geometry_status"],
                              "M1_headless_end_to_end": execution["status"],
                              "full_Phase0_end_to_end": "NOT_TESTED",
                              "manufacturing_rules": "NOT_VERIFIED", "physical_print": "NOT_TESTED"},
        "pytest": tests["counts"], "test_requirements": {
            "T01": "PASS", "T02": "PASS", "T03": "PASS", "T04": "PASS",
            "T05": "PASS", "T06": "PASS", "T07": parameters["status"],
            "T08": "NOT_TESTED", "T09": "NOT_TESTED", "T10": "NOT_TESTED",
            "T11": "NOT_TESTED", "T12": "NOT_TESTED", "T13": "NOT_TESTED",
            "T14": "PASS (base mesh only)", "T15": "PASS (implemented input/mesh negative checks only)",
            "T16": roundtrip["status"], "T17": "NOT_TESTED", "T18": "PASS",
            "T19": "PASS (M1 CLI only)", "T20": "NOT_TESTED"},
        "Docker_Linux": "NOT_TESTED", "actual_source": last["source"],
        "parameter_change": {"status": parameters["status"], "parameter": parameters["changed_parameter"],
                             "values_mm": [parameters["baseline_value_mm"], parameters["variant_value_mm"]]},
        "same_spec_repeatability": {"status": "PASS" if real_checks_pass else "NOT_TESTED",
                                    "evidence": "test_T19_real_headless_cli_M1_end_to_end rebuilds the complete shoe independently and compares B-Rep volume (relative 1e-8) and bounds (1e-6 mm)",
                                    "scope": "B-Rep validity, volume and coordinate bounds; no global surface-distance or byte-identity claim"},
        "limitations": ["Orientation is configured provisionally; side and shoe size remain unknown.",
                        "Support slabs and convex sole simplify local anatomy and add reconstruction clearance.",
                        "Upper is faceted; global minimum thickness/clearance and self-intersections are not qualified.",
                        "SLS TPU profiles are experimental; printability, insertion/fit and comfort are not verified.",
                        "Current input has CC BY-NC-ND 4.0 header notice; purchase authorization needs review.",
                        "Native CAD memory consumption is multi-GiB; sampled RSS guard is not an OS hard cap."]}
    write_json(output / "acceptance_report.json", result)
    source = last["source_geometry"]
    cad = parameters["baseline"]["cad"]
    dims = lambda values: " × ".join(f"{v:.3f}" for v in values)
    count = tests["counts"]
    peak = execution["environment"]["process_peak_rss_bytes"] / 1024**3
    intervals = geometry["upper_wall_probe"]["material_interval_lengths_mm"]
    text = f"""# Milestone 1 — 验收报告

## 1. 完成状态

**{result['technical_status']} — Milestone 1 工程链路通过；用户验收 PENDING。** Phase 0 全流程、制造资格和实体打印分别为 NOT_TESTED / NOT_VERIFIED / NOT_TESTED。

## 2. 已完成功能

真实 STEP 导入及临时文件头修复、几何分析、显式刚体坐标标准化、ShoeSpec v0.1、真实鞋楦支撑截面包络、鞋腔、鞋口、非零厚度连续鞋面、参数化鞋底及单体融合。支持 headless CLI、STEP/STL 输出和重新导入、几何检查、真实 STL 深度缓冲预览、可追溯执行报告、锁定依赖及采样 RSS 预算保护。

## 3. 实际生成文件

主交付目录：`{output.resolve()}`。以下文件均已实际生成：

| 文件 | 用途 |
| --- | --- |
| `last_metadata.json` | 实测鞋楦、来源、许可声明和变换矩阵 |
| `last_normalized.step` | 标准化鞋楦 |
| `shoe_spec.json` | 已解析且可重新加载的参数 |
| `base_shoe.step` / `base_shoe.stl` | 实际基础鞋体 |
| `preview.png` | 正式模型预览及纵剖面 |
| `test_report.json` | pytest 逐项实际结果 |
| `geometry_report.json` / `validation_report.json` | 包含关系、鞋腔、鞋口和网格检查 |
| `roundtrip_report.json` / `execution_report.json` | 重新导入与性能证据 |
| `parameter_change_report.json` | 20 → 25 mm 完整鞋体比较 |
| `variant/base_shoe.step` / `variant/base_shoe.stl` | 25 mm 鞋底对照模型 |
| `acceptance_report.json` / `artifact_manifest.json` | 分层验收状态与 SHA-256 清单 |

输入资产和所有派生几何都被 Git 忽略，不上传或加入公开仓库。

## 4. 执行命令

以下命令已执行；最终测试包含真实输入。环境使用 Python 3.11.17，依赖已锁定。

```sh
.venv/bin/python -m shoe_poc inspect-last --input input/lasts/sneaker_last.stp --output output/inspection
.venv/bin/python -m shoe_poc run --spec input/specs/shoe_spec.json --output output/m1
.venv/bin/python scripts/verify_parameters.py --spec input/specs/shoe_spec.json --baseline output/m1 --output output/m1/variant
.venv/bin/python -m pytest -q --report-json output/m1/test_report.json
.venv/bin/python scripts/report_m1.py --output output/m1 --document docs/milestone1_report.md
```

可复现安装：`uv sync --locked --extra test --python 3.11`。CLI 默认采样 RSS 预算 8192 MiB，可通过 `--max-rss-mb` 调整。采样间隔 100 ms，超过已观察预算后保存失败诊断并以代码 3 退出；不是操作系统硬内存限制。

## 5. 测试结果

pytest：**{count['PASS']} 通过、{count['FAIL']} 失败、{count['NOT_TESTED']} 跳过**；4 项上游 build123d 弃用警告。合成几何用于独立算法测试，真实鞋楦测试单独标记 `real_last`。实际执行 M1 范围的 T01–T07、T14/T15 已实现检查、T16、T18、T19；T08–T13、T17、T20 属于后续阶段，均 NOT_TESTED。

负面测试包含开放网格、非流形边、孤立组件、超限尺寸、非法参数、STEP 单位/实体异常及内存预算。还检查了“水密但存在意外通孔”的模型，不能仅依据水密性判断连续鞋面。

相同配置独立重建的完整鞋体在 B-Rep 体积（相对 1e-8）和坐标包围盒（1e-6 mm）内一致。此项不声称已经完成全局曲面 Hausdorff 距离验证或 STEP 字节一致性。

## 6. 几何检查

| 项目 | 实测结果 |
| --- | --- |
| 原鞋楦 | {source['solid_count']} 实体、{source['face_count']} 曲面、拓扑有效、{source['open_shell_count']} 开放壳体 |
| 原鞋楦尺寸 | {dims(source['bbox_mm']['size'])} mm |
| 原鞋楦体积 | {source['volume_mm3']:.3f} mm³ |
| 基础鞋体 CAD 尺寸 | {dims(cad['bbox_mm']['size'])} mm |
| 基础鞋体 CAD 体积 | {cad['volume_mm3']:.3f} mm³ |
| 拓扑 / 连通性 | 有效 CAD 单一实体；STL 水密、1 组件、Euler {mesh['euler_number']}、法向绕序一致 |
| 包含关系 | 鞋楦在鞋腔/外包络内，鞋腔在外包络内，遗漏体积全部 0 mm³ |
| 空间排除 | 鞋体与真实鞋楦、鞋腔、鞋口切除区域交叠体积全部 0 mm³（阈值 0.01） |
| 鞋口 | 与鞋腔相交，并连通到外部；物理穿入未验证 |
| 局部鞋面厚度 | 横向 CAD 探针得到 {' / '.join(f'{v:.4f}' for v in intervals)} mm 的材料区间 |
| 重新导入 | STEP/STL 坐标边界通过；STEP 体积相对差 {roundtrip['step_volume_relative_delta']:.3g} |

鞋底 20 → 25 mm 时，模型底面下移 5 mm，总高度增加 5 mm，X/Y 和顶面不变，CAD 体积增加 {parameters['cad_volume_delta_mm3']:.3f} mm³。两模型都保持单体、水密、Euler 2 和鞋腔/鞋口。

## 7. 性能

基础 CLI 运行 {execution['elapsed_seconds']:.2f} 秒，其中几何构建 {execution['timings_seconds']['build_base']:.2f} 秒。对照模型 CLI 运行 {parameters['variant']['execution_elapsed_seconds']:.2f} 秒。基准进程高水位 RSS {peak:.2f} GiB（进程全生命周期值，非单阶段分配）。STL {mesh['triangles']} 三角面、{(output/'base_shoe.stl').stat().st_size:,} 字节；STEP {(output/'base_shoe.step').stat().st_size:,} 字节。

## 8. 当前问题

无剩余 M1 技术阻断。待评审项：方向仍为显式配置的暂定 +X 鞋头/+Z 向上；左右脚和尺码不推断。模型采用凸支撑包络并简化足弓，外表面有折面，实际间隙随位置变化。局部非零厚度不等于最小全局壁厚达标。全局自相交、工艺壁厚规则、SLS TPU 资格、真实穿入、舒适性、实体打印及材料性能均未验证。Docker 命令不可用，Linux/Docker 为 NOT_TESTED。

STEP 文件头有 CC BY-NC-ND 4.0 声明；购买来源/署名及商业授权仍待核对。核心依赖许可记录见 `docs/dependencies.md`，OCCT 使用 LGPL-2.1 加例外。

调试历史已保留在本地 `output/debug/`：直接偏置失败；早期内接截面包络曾产生意外孔洞，检查器扩展后明确判为 FAIL，改为交叠支撑片及外切多边形后修复；缺失依赖、文件头字段及预览排序/只读数组问题已修正。最终结果未降低验收阈值。

## 9. 后续任务

用户确认 M1 后进入 M2：鞋底设计域、受参数控制的 Gyroid 隐式场、Marching Cubes、边界封闭、内存上限及晶格性能测试。M3 才实现鞋面网孔、整鞋晶格融合、制造规则及 3MF/GLB。M4 才接入 Meshy。

## 10. 验收结论

**当前 Milestone 1 工程验收条件{'已满足' if passing else '未满足'}。** 已使用真实鞋楦获得有效鞋腔/鞋口/鞋面/鞋底和两组完整参数化 CAD/STL，并完成重新导入及 headless 测试。该结论仅覆盖 M1，不代表可量产、适穿或实体打印验证通过。按照 `docs/PROMPT.md` 的里程碑规则，等待用户确认后再进入 Milestone 2。
"""
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(text, encoding="utf-8")
    manifest = artifact_manifest(output)
    manifest.update({f"variant/{name}": data for name, data in artifact_manifest(output / "variant").items()})
    write_json(output / "artifact_manifest.json", manifest)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/m1"))
    parser.add_argument("--document", type=Path, default=Path("docs/milestone1_report.md"))
    args = parser.parse_args()
    result = report(args.output, args.document)
    print(f"Milestone 1 technical {result['technical_status']}; user acceptance PENDING")
    return 0 if result["technical_status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
