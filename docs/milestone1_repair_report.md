# Milestone 1 — 基础鞋型修复验收报告

## 1. 完成状态

**PARTIAL**。技术检查 PASS；外形及用户验收 PENDING。水密性和单实体不代表基础鞋型、制造或穿着验收通过。

| 验收层级 | 实际状态 |
| --- | --- |
| CAD_and_export_pipeline | PASS |
| mesh_integrity | PASS |
| roundtrip | PASS |
| surface_samples | PASS |
| exterior_junction_samples | PASS |
| bottom_round_samples | PASS |
| parameter_change | PASS |
| tests | PASS |
| global_minimum_wall_thickness | NOT_VERIFIED |
| full_Phase0_end_to_end | NOT_TESTED |
| manufacturing_rules | NOT_VERIFIED |
| physical_print | NOT_TESTED |
| wearability | NOT_VERIFIED |

## 2. 已完成功能

算法版本：`exact-upper-periodic-rocker-sole-assembly-v7`。具体成形方法、拟合过程和实际参数以 `geometry_report.json` 为准。没有对应运行证据的能力不计为通过。

鞋面保留真实 CAD 足部曲面并做局部法向偏移，鞋口采用平顺曲线切削，鞋底使用低足部截面驱动的周期轮廓及真实外表面过渡带。端部和底边已按实际 CAD 检查。原计划的真实截面重建也已实现和测试，但鞋楦踝部的分叉截面使分片融合无法稳定满足要求，因此未选作生产成形方法；失败证据保留在调试目录。选定方法不重拟合源曲面，其源形状拟合误差为零；偏移精度另由独立采样测量，不能把源曲面误差当作壁厚误差。

## 3. 实际生成文件

目录：`/Users/niqikai/.codex/worktrees/882e/shoe-designer/output/m1_repair`。当前实际存在的文件：

- `acceptance_report.json`
- `artifact_manifest.json`
- `base_shoe.step`
- `base_shoe.stl`
- `build.log`
- `cavity.stl`
- `comparison.png`
- `execution_report.json`
- `geometry_report.json`
- `last_metadata.json`
- `last_normalized.step`
- `last_normalized.stl`
- `mesh_export_report.json`
- `parameter_change_report.json`
- `preview.png`
- `repeatability_report.json`
- `roundtrip_report.json`
- `shoe_spec.json`
- `surface_report.json`
- `test_report.json`
- `tests.log`
- `upper.stl`
- `upper_cavity_overlay.png`
- `validation_report.json`
- `variant_build.log`

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

通过 199；失败 0；未执行/跳过 0。原始结果保留在 `test_report.json`，合成几何测试不替代真实鞋楦测试。

| 要求 | 实际测试状态 |
| --- | --- |
| T01 | PASS |
| T02 | PASS |
| T03 | PASS |
| T04 | PASS |
| T05 | PASS |
| T06 | PASS |
| T07 | PASS |
| T08 | NOT_TESTED |
| T09 | NOT_TESTED |
| T10 | NOT_TESTED |
| T11 | NOT_TESTED |
| T12 | NOT_TESTED |
| T13 | NOT_TESTED |
| T14 | PASS |
| T15 | PASS |
| T16 | PASS |
| T17 | NOT_TESTED |
| T18 | PASS |
| T19 | PASS |
| T20 | NOT_TESTED |

没有已记录的失败测试；未执行项仍未执行。

## 6. 几何与采样检查

真实源鞋楦：1 实体、235 曲面；实测尺寸 `[276.65844268011335, 101.03195901944696, 135.95251439004483]` mm。

```json
{
  "algorithm_version": "exact-upper-periodic-rocker-sole-assembly-v7",
  "solid_count": 1,
  "topology_valid": true,
  "last_intersection_volume_mm3": 0.0,
  "cavity_intersection_volume_mm3": 0.0,
  "opening_intersection_volume_mm3": 0.0,
  "upper_wall_probe": {
    "method": "CAD common of upper and transverse Y line",
    "x_mm": 193.66090987607924,
    "z_mm": 27.000000000000004,
    "material_interval_lengths_mm": [
      4.356347459957156,
      2.78233547273058
    ],
    "scope": "Local directional thickness only; global minimum NOT_VERIFIED"
  },
  "junction_sampling": {
    "geometry_status": "PASS",
    "angle_limit_degrees": 2.0,
    "exterior_sample_count": 472,
    "regions": {
      "heel": {
        "geometry_status": "PASS",
        "sample_count": 49,
        "maximum_normal_angle_degrees": 0.7223634940062778
      },
      "middle": {
        "geometry_status": "PASS",
        "sample_count": 321,
        "maximum_normal_angle_degrees": 0.5436518549270789
      },
      "toe": {
        "geometry_status": "PASS",
        "sample_count": 102,
        "maximum_normal_angle_degrees": 0.5624427728529903
      }
    },
    "excluded_boundary_sample_count": 1000,
    "measurement_error_count": 0,
    "tolerance_scope": "Experimental M1 numerical shape target; not a manufacturing standard or global G1 certificate"
  },
  "bottom_round_sampling": {
    "geometry_status": "PASS",
    "method": "Actual trim-classified CAD face principal curvature near the rocker floor",
    "target_radius_mm": 2.0,
    "sample_count": 1014,
    "minimum_measured_radius_mm": 1.999431216898959,
    "maximum_measured_radius_mm": 2.0005503258241437,
    "maximum_radius_deviation_mm": 0.000568783101041026,
    "numerical_radius_tolerance_mm": 0.02,
    "tolerance_scope": "Experimental M1 CAD numerical check, not a manufacturing tolerance",
    "measurement_errors": [],
    "limitations": [
      "Finite principal-curvature samples cannot certify all points or global curvature continuity."
    ]
  }
}
```

独立采样摘要：

```json
{
  "method": "Trim-classified CAD last UV points and outward face normals; exact B-Rep line intersections with final shoe and midpoint solid classification",
  "scope": "Sampled outward-normal clearance and directional thickness; not global shortest-distance wall thickness or manufacturing qualification",
  "targets_mm": {
    "clearance": 1.0,
    "thickness": 2.5
  },
  "tolerances_mm": {
    "clearance_absolute_error": 0.25,
    "thickness_absolute_error": 0.35,
    "cad_intersection": 1e-06
  },
  "quality_descriptors": {
    "last": {
      "topology_valid": true,
      "solid_count": 1,
      "face_count": 235,
      "surface_area_mm2": 77505.80812416591,
      "planar_face_area_fraction": 0.032965616604843144,
      "scope": "Topology and face-type descriptors only; smoothness and appearance require visual review."
    },
    "shoe": {
      "topology_valid": true,
      "solid_count": 1,
      "face_count": 248,
      "surface_area_mm2": 127823.32074622417,
      "planar_face_area_fraction": 0.08885122599132261,
      "scope": "Topology and face-type descriptors only; smoothness and appearance require visual review."
    },
    "cavity": {
      "topology_valid": true,
      "solid_count": 1,
      "face_count": 261,
      "surface_area_mm2": 79528.35269034834,
      "planar_face_area_fraction": 0.03262365657448812,
      "scope": "Topology and face-type descriptors only; smoothness and appearance require visual review."
    },
    "opening": {
      "topology_valid": true,
      "solid_count": 1,
      "face_count": 7,
      "surface_area_mm2": 102526.81025452688,
      "planar_face_area_fraction": 0.9056161936304985,
      "scope": "Topology and face-type descriptors only; smoothness and appearance require visual review."
    }
  },
  "sampled_clearance_mm": {
    "count": 122,
    "minimum_mm": 0.9999999999999784,
    "maximum_mm": 1.0000000000000322,
    "mean_mm": 1.0000000000000002,
    "maximum_absolute_deviation_mm": 3.219646771412954e-14
  },
  "sampled_thickness_mm": {
    "count": 122,
    "minimum_mm": 2.4999999999999716,
    "maximum_mm": 2.5000000000000178,
    "mean_mm": 2.5,
    "maximum_absolute_deviation_mm": 2.842170943040401e-14
  },
  "coverage": {
    "candidate_count": 2096,
    "planned_site_count": 512,
    "maximum_site_count": 512,
    "last_face_count": 235,
    "sampled_face_count": 235,
    "faces_without_valid_uv_site": [],
    "faces_omitted_by_budget": []
  },
  "exclusion_policy": {
    "sole_connection_below_z_mm": 3.5000000000000027,
    "sole_junction_distance_mm": 1.0,
    "opening_rim_distance_mm": 4.5,
    "description": "Exclude sole junction and points within or near the actual opening cutter; record each excluded point. No generator section data is used."
  },
  "geometry_status": "PASS",
  "overall_status": "PARTIAL",
  "manufacturing_status": "NOT_VERIFIED"
}
```

完整位置、排除区域、无法测量区域和偏差见 `surface_report.json`。壁厚与间隙只对实际采样位置负责，不证明全局最小值。鞋口边缘、鞋底加强区和其他排除位置不能被统计结果掩盖。

连接检查来自最终 CAD 的实际相邻面外向法向；完整记录见 `geometry_report.json` 的 `junction.validation`。2° 法向角度上限和 0.02 mm 底边半径误差上限均为本轮实验性数值目标，有限检查不证明全局 G1 连续或制造合格。

参数变化：PASS；底高 `20.0 → 25.0` mm；实际 CAD 体积变化 `116467.96395215217` mm³。

同参数重复构建采用固定的 Gauss–Kronrod / BSpline-span 体积协议，point 与 XY-plane 两项均须满足原 `1e-8` 相对重复性阈值；细化结果和两协议的绝对差异见 `repeatability_report.json`。默认非自适应体积积分的失败记录保留在调试目录，不能当成真实形状变化，也不通过放宽容差消除失败。积分误差估计只涉及 quadrature，不是完整 CAD 绝对体积误差界。

```json
{
  "status": "PASS",
  "relative_deltas_by_protocol": {
    "point": 4.76540378457796e-09,
    "xy_plane": 4.490975172136474e-10
  },
  "absolute_volume_accuracy": "NOT_VERIFIED",
  "scope": "Independent M1 CLI build compared in CAD volume and coordinates; no byte identity claim"
}
```

## 7. 性能

CLI 耗时 `237.175759958016` 秒；阶段耗时 `{'import_last': 0.15945816598832607, 'normalize_last': 0.0017458330257795751, 'export_normalized_last': 0.09850291602197103, 'reimport_normalized_last': 0.1288327920192387, 'build_base': 128.8588448750088, 'export_upper_preview_stl': 0.04992641697754152, 'export_cavity_preview_stl': 0.09900045799440704, 'export_last_normalized_preview_stl': 0.04027200001291931, 'render_upper_gate': 2.743682333995821, 'export_step': 0.09945641597732902, 'export_stl': 0.10507183300796896, 'reimport_step': 0.47311937500489876, 'reimport_validate_stl': 0.451876875013113, 'measure_surface_samples': 69.63276954100002, 'render_preview': 5.671766124985879, 'render_comparison': 6.29103625001153}`；进程峰值 RSS `3250421760` 字节；STL 三角面 `70468`。采样 RSS 保护不是操作系统硬限制。Linux/Docker 未实际运行则保持 NOT_TESTED。

## 8. 当前问题

- Uniform minimum wall thickness not measured.
- Physical insertion, fit, comfort and material performance are not verified.
- Finite exterior-junction normal samples cannot certify global tangent continuity; failed samples remain explicit.
- Appearance requires actual-image review and user acceptance; topology alone cannot qualify the shoe template.
- Sampled clearance/thickness are not a proof of the global minimum or physical fit.
- Orientation and foot side follow explicit configuration; unconfirmed values stay unconfirmed.
- SLS TPU parameters are experimental; manufacturing, physical printing and wear are not qualified.
- Purchased STEP and derived geometry remain local and ignored by Git.

外形评审要求：真实 STL 同角度同尺度对照中，没有突兀鞋口竖墙、大面积折面、平切端盖或鞋面/鞋底台阶。`comparison.png` 和剖面叠加图作为证据，未生成或未评审则不能通过外形验收。失败几何与日志保留在本地调试目录。

## 9. 后续任务

先完成本次修复的几何和外形验收。根据 `docs/PROMPT.md`，用户确认 M1 后才进入 M2；当前不实施晶格、AI 或制造平台。

## 10. 验收结论

**M1 当前状态 PARTIAL。** 技术状态 PASS，外形及用户验收尚待确认。制造、打印与穿着验证独立记录，未知状态不得自动转换为 PASS。
