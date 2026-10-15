# M1 鞋底前端修复记录

本轮技术状态 **PASS**；M1 总体 **PASS**，外形确认 **ACCEPTED**。本轮处理鞋底前端的平台与直立端头，风格、前掌比例及装饰不继续扩展。制造和穿着另行验证。

用户在 `2026-10-15T08:05:02.759094+00:00` 明确确认「基础外型确认」。当前基础外形已冻结，M2 入口 **READY**。确认绑定当前运行与 STEP、STL、配置和预览文件哈希，见 `user_acceptance.json`。构建时原始报告中的 PENDING 保留为历史状态，当前用户验收以本报告为准。

## 实际修改

将源鞋楦末端处提前收平的翘度曲线替换为连续五次曲线。起点与平底二阶连续，向前持续转起；切削体终止面位于鞋体之外，避免再切出平台。与实际曲壁相交后保留原 R2 底边圆角检查。算法 `exact-upper-periodic-rolled-toe-sole-assembly-v10`。

`toe_roll_radius_mm=15.0` 控制未裁剪曲线端点的曲率，不代表最终鞋头是等半径圆弧。实际轮廓以原生 CAD 测量和重新导入 STL 的预览为准。原鞋楦、鞋腔、鞋面和鞋口配置保留，底高仍为 `16.0` mm。

## 同位置原生 CAD 剖面

测量间距不超过 0.25 mm；3° 只是近水平／近直立段的描述约定，不是鞋类或制造标准。PASS 表示有限测量完成，不代表外形自动通过。

| Y 位置 mm | 末端近水平段 mm，旧 → 新 | 前端近直立段 mm，旧 → 新 |
| --- | --- | --- |
| -5.0 | 11.44 → 0.00 | 3.72 → 0.00 |
| 0.0 | 11.92 → 0.00 | 8.68 → 3.22 |
| 5.0 | 12.44 → 0.00 | 10.91 → 4.71 |

同角度局部对比：`/Users/niqikai/.codex/worktrees/882e/shoe-designer/output/m1_toe_refine/toe_comparison.png`；整体对比：`comparison.png`；新预览：`preview.png`。图像均来自实际导出并重新导入的 STL。

## 几何、测试与性能

原鞋面遗漏材料 `0` mm³；鞋楦、鞋腔、鞋口相交体积分别 `0.0`、`0.0`、`0.0` mm³，容差仍为 0.01 mm³。底边圆角、连接、单实体、水密性和 STEP 几何重导入以各报告为准。

足底采样 `119` 点，最小观测材料区间 `5.405929618053099` mm；这是有限方向采样，不是全局最小壁厚证明。

本轮测试 `PASS`，实际结果 `{"PASS": 272, "FAIL": 0, "NOT_TESTED": 0}`；测试绑定当前运行 `eb48db0c-86a8-4f3f-a0b1-962033f130c6` 的状态 `True`。真实鞋楦回归 `PASS`，独立 CLI 重跑及同配置两种体积积分协议的可重复性 `PASS`；逐项结果见 `test_report.json`，原始体积数据在 `debug/pytest_final/test_T19_real_headless_cli_M1_0/volume_repeatability_measurements.json`。底高完整变体检查 `PASS`，具体生成高度和实际尺寸差见 `parameter_change_report.json`。不把旧版模型的测试计为当前算法结果。

验收报告保护测试单独记录为 `PASS`，计数 `{"PASS": 27, "FAIL": 0, "NOT_TESTED": 0}`；该补充运行绑定当前报告脚本和测试源码的哈希，不并入主几何测试计数。记录见 `debug/toe_report_guard_tests.json` 与 `debug/toe_report_guard_provenance.json`。

正式生成耗时 `592.2173212499474` 秒，进程峰值 RSS `3706880000` bytes；详见 `execution_report.json` 和资源记录。并行执行会影响耗时。

## 失败与原生表示修复

前两候选的 R2 圆角失败：切削体终止面落在鞋体内，产生额外平台和圆角轮廓；失败 STEP／日志保存在 `debug/candidate1/`、`debug/candidate2/`。第三候选消除该终止面干涉，未放宽几何门槛。

第三候选有一处偏移曲面的边界参数无法直接三角化。仅对新布尔结果做 ShapeFix 的首轮正式构建虽然覆盖全部曲面，仍漏掉外鞋面的一片三角形，水密性失败；该失败及初次测试的实际失败与中断记录保存在 `debug/failed_formal1/`。没有把该轮写成通过。

选定流程先在临时 STEP 中规范化原生曲线表示，再做 ShapeFix；三种诊断网格精度均闭合，详见 `debug/formal_mesh_diagnosis/`。正式构建要求规范化和修复合计包围盒差 ≤1e−5 mm、CAD 对称差 ≤0.01 mm³，再执行全部排除和厚度检查。修复前 CAD 保存在 `debug/before_representation_repair.step`，实际修复量见 `geometry_report.json`。

独立候选验证的对称差为 0 mm³；3,826 个双向实际面边界距离样本最大约 1.74e−13 mm，另有20点补充覆盖稀薄面。完整记录及文件哈希在 `debug/candidate3/meshing_repair/shape_fix_combined_bounded_verification.json`。有限采样不宣称全局精确等价。

**STEP 再网格化限制：** 诊断中，修复后的 STEP 重新导入仍会重现偏移面漏三角化，需要再次应用相同的原生表示修复。STEP 几何重导入与 STEP 直接再网格化是不同检查。交付 STL 来自已修复的原生 CAD，检查全部原生面覆盖；没有补洞、移动网格顶点或平滑法线。辅助鞋腔 STL 的限制另见 `component_preview_report.json`，不能用该预览证明鞋腔拓扑。

原 `output/m1/`、`output/m1_repair/`、`output/m1_design_refine/` 保留，文件哈希核对见 `debug/prior_artifacts_preservation.json`。制造 **NOT_VERIFIED**，实体打印 **NOT_TESTED**，穿着 **NOT_VERIFIED**。
