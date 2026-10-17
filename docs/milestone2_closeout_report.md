# Milestone 2 参数闭环与 M3 门槛

生成时间：2026-10-17T10:09:47.790375+00:00。冻结运行：`27bc88ca-51ed-4707-98f5-f97606958f86`。

外形状态 **ACCEPTED**；M2_selected_cases_status **PASS**；M3_gate **READY**。M3 尚未开始。此结论只覆盖已认可外形和当前五个实际参数案例，不能扩展为所有参数组合保证。

用户认可证据来自 `docs/milestone2_lattice_acceptance.json`，scope 为 `M2_lattice_appearance`。M3 继续使用冻结的 `/Users/niqikai/.codex/worktrees/882e/shoe-designer/output/m2_toe_complete/lattice.stl`，SHA-256 `b20f1037c9978afcaae316785d028fa49c282b50727d0149c6be35999ce5157c`。当前算法 `current_primary` 是独立技术回归参考。冻结/当前几何差异记录在 `frozen_primary_geometry_difference`：最大绝对 bounds 差 2.08616256714e-07 mm，体积差 0.00582526957442 mm³；该差异没有转移外形认可。

| 案例 | 周期 mm | 无量纲阈值 | 分辨率 mm | 三角面 | 实际提取 level | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| current_primary | 12.0 | 0.6 | 0.4 | 1,949,366 | 0 | PASS |
| period_16 | 16.0 | 0.6 | 0.4 | 1,541,958 | 0 | PASS |
| threshold_045 | 12.0 | 0.45 | 0.4 | 1,875,524 | -1.59930877999e-05 | PASS |
| repeat | 12.0 | 0.6 | 0.4 | 1,949,366 | 0 | PASS |
| resolution_0p5 | 12.0 | 0.6 | 0.5 | 1,217,180 | 0 | PASS |

每个案例均独立验证 STL 闭合/流形、原生 CAD 点线包含、全部顶点 Gyroid 残差、鞋底覆盖和主组件终端覆盖，绑定 STL、Gyroid 场、独立终端节点、域证书和验证代码 SHA。repeat 与新 current_primary 比较实际物理场和序列化 STL，没有拿旧冻结 STL 当重现性参考。0.5 mm 案例使用单独完成原生认证的 CAD 域；体素对角线保护量随分辨率变化，不能称为固定域收敛证明。

历史 0.45 故障涉及 Lewiner 微小几何：`allow_degenerate=False` 的退化清理可能合并局部顶点身份并改变连接；零等值面及 float32 STL 序列化也会留下零面积面。当前使用 `allow_degenerate=True` 保留 raw connectivity，再执行原有有界 numerical inset 重提取及严格几何/STL 检查。没有补洞、平滑或删除组件，也没有放宽解析残差容差。actual level、数值域内缩和有效阈值均保存在各 variant 报告；这是有界数值向内近似，不是精确零等值面声明。

实际快速诊断证据：`/Users/niqikai/.codex/worktrees/882e/shoe-designer/output/m2_closeout/debug/threshold_diagnosis/current_fixed/report.json`，SHA-256 `dcc018d2daff6238e088c29f4dd198c2c704e195775e37487916c30083acd5b4`。诊断状态 PASS，CAD validation `NOT_RUN_QUICK_EXTRACTION_DIAGNOSIS`；它仅覆盖提取与重导入，完整原生 CAD 检查证据来自上表五案例，不能归功于快速诊断。

完整回归测试 635 PASS，0 FAIL，0 NOT_TESTED，pytest exit 0。包括当前全部 unit、旧真实 lattice、toe complete 和新 closeout integration；报告绑定当前参数报告及五案例验证报告的 SHA/bytes。保存快照逐个比较 572 个 M1/旧 M2 文件，全部未变化。源执行、验证、completion、terminal 报告全部 PASS，并重新执行 accepted_baseline 输入门槛。

`acceptance_report.json` 保存 M3 所需源和参数身份锁；`performance_report.json` 分开记录冻结旧源耗时和本次变体耗时/RSS；`artifact_manifest.json` 绑定 closeout 文件。CPU/RSS 来自进程高水位与采样保护，不是总进程 OS 内存硬限制。

制造资格和全局最小壁厚 **NOT_VERIFIED**；实体打印和穿着 **NOT_TESTED**。无量纲 threshold 不是毫米壁厚。M3 还需要晶格与外壳融合、最终整鞋连通及后续制造检查。
