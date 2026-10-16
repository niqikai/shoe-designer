# Milestone 2 — 晶格引擎验收报告

生成时间：2026-10-16T04:03:16.456028+00:00。运行 ID：`638ed5e6-1086-4e99-823c-ea46300136ec`。算法：`m2-certified-gyroid-v1`。

## 1. 完成状态

**技术检查：PASS；M2 总体：PARTIAL；用户验收：PENDING。** M1 已接受的 16 mm 外形被冻结。本轮只实现 Step 3/4，M3 尚未开始。

## 2. 已完成功能

基于真实 M1 STEP 的材料域与鞋腔，生成 NumPy Gyroid 隐式场，限制计算边界与资源，再用 Lewiner Marching Cubes 提取闭合晶格。所有视图来自重新导入的实际 STL。没有平滑顶点、补洞或删除组件。

默认域 Z 范围约为 `-16.000 至 0.000` mm（完整精度在域元数据中），限制在鞋楦基准面以下的实际鞋底材料。它故意不覆盖基准面以上的翘头部分；这是一块用于引擎验证的保守域，**不是整鞋填充方案**。实验性预留 2.0 mm，加上 0.692820 mm 全体素对角线数值保护，会减少实际保留区域，不能把这些值当作均匀外壳厚度。

EDT 只用于选择候选，共 2,510,853 个，其中保守省略 198,994 个。精确 CAD 证书球还覆盖了候选选择器以外 115,923 个合法节点，最终保留 2,427,782 个负节点。未证明的区域没有填入。域证书依赖 OCCT 的几何容差；额外的有限点/线检查不构成独立的全局包含或最小壁厚证明。

## 3. 实际文件

文件在 `output/m2/`：`lattice.stl`、`gyroid_field.npz`、`domain_field.npz`、`lattice_metadata.json`、`domain_metadata.json`、`preview.png`、`validation_report.json`、`execution_report.json`、`test_report.json`、`parameter_change_report.json`、`m1_preservation_report.json`、`performance_report.json`、`parameter_sections.png` 和 `acceptance_report.json`。`variants/` 保存完整参数变体 STL 及独立验证。`artifact_manifest.json` 记录主目录文件的字节数与 SHA-256；变体报告另绑定各自 STL。

## 4. 执行命令

```sh
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2.json --base-dir output/m1_toe_refine --output output/m2
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2.json --base-dir output/m1_toe_refine --output output/m2 --domain-cache output/m2/debug/attempt_01
.venv/bin/python scripts/verify_lattice.py --output output/m2 --resolution-domain output/m2/debug/domain/trial_06 --jobs 2
.venv/bin/python scripts/verify_lattice.py --output output/m2 --resolution-domain output/m2/debug/domain/trial_06 --jobs 2 --reuse-passed
.venv/bin/python -m pytest tests/unit tests/integration/test_lattice_real.py -q --report-json output/m2/test_report.json
.venv/bin/python -m pytest tests/integration/test_toe_refine.py -q --report-json output/m2/debug/m1_frozen_replay_tests.json
.venv/bin/python scripts/report_m2.py --output output/m2 --document docs/milestone2_report.md
```

第一条全新采样命令的域认证完成，但旧验证器因负向内孔壳与裁剪影响区误分类而返回失败，证据保存在 `debug/attempt_01/`。第二条命令严格验证同一域的源输入、参数、文件哈希和网格证书后复用它，并重新检查 STL。报告将原始域认证用时与缓存重放命令用时分开。`run` 仍只执行 M1。M2 的输入锁独立绑定支持 CAD 和原始执行报告，历史 M1 用户确认文件保持原样。嵌套输出与生成文件软链接在启动 RSS 保护线程之前被拒绝。

参数首轮的精确零等值面在阈值 0.45 产生浮点微小退化三角，严格拦截的报告保存在 `debug/parameter_attempt_01_failure.json` 和 `debug/threshold_exact_zero_failure/`。有界数值向内重提取后，仅重跑该阈值版的完整验证；其余三版在来源、运行、参数和 STL 哈希匹配后复用原 PASS 证据，未伪称重新执行。

## 5. 测试结果

本轮主回归 490 通过、0 失败、0 未运行；另有冻结 M1 真实 CAD/STL 重放 5 项通过。测试包含旧协议/CLI 回归、场解析值与坐标、参数变化、资源预检、开放/退化/非流形/负体积、鞋腔与 Z 越界、分类未知状态、原生线覆盖不足、cap 非法材料、冻结输入与输出保护，以及保存的真实 M1/M2 证据重放。未重新构建 M1；历史失败日志保留在 `debug/`，不作为当前成功结果。

| 变体 | 周期 mm | 无量纲阈值 | 间距 mm | 体积 mm³ | 三角面 | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| primary | 12.0 | 0.6 | 0.4 | 57904.769 | 1,818,452 | PASS |
| period_16 | 16.0 | 0.6 | 0.4 | 57501.946 | 1,440,766 | PASS |
| threshold_045 | 12.0 | 0.45 | 0.4 | 43067.223 | 1,753,300 | PASS |
| repeat | 12.0 | 0.6 | 0.4 | 57904.769 | 1,818,452 | PASS |
| resolution | 12.0 | 0.6 | 0.6 | 52224.201 | 721,662 | PASS |

`threshold_045` 请求阈值 0.45，目标 level 0；严格非退化检查后实际 level=-1.59930877999e-05，有效 Gyroid 阈值=0.449984006912，额外域内缩=6.39723511995e-06 mm。原始标量场保持不变；这是有界数值向内近似，不能声称精确使用请求零等值面。全部尝试和有效参数在变体报告中；解析误差阈值未放宽。

同一已认证域重复场计算、独立曲面提取和 STL 导出，SHA-256 完全一致：`True`。这不是第二次完整 CAD 域重建的证明。分辨率变体重新认证原生 CAD 域，其体素对角线余量随间距变化，**不能当作固定域 Marching Cubes 收敛试验**。

## 6. 实际几何检查

导出并重导入后的尺寸为 **252.628 × 101.356 × 10.568 mm**；体积 57904.769 mm³；1,818,452 个三角面；连通组件 696；Euler 数 -1278。边界边 0、非流形边 0、奇异顶点 0。STL 重导入、正材料净体积、壳嵌套方向与跨壳接触/交叉检查通过。实际边界包含 688 个正向材料外壳和 8 个反向封闭空隙内壳；负向内壳不等于倒置实体。687 个额外正向小壳仍保留，未做碎片删除，晶格尚未与鞋体外壳融合。这里没有宣称最终整鞋已连通。

原生 CAD 点样本 4096 个，域外 0、鞋腔侵入 0、Z 越界 0。独立线扫描结果与完整诊断见 `validation_report.json`。解析 Gyroid 内部曲面最大无量纲残差 0.00560488，阈值 0.01106623；caps 单独分类，残差不是毫米误差或壁厚。

## 7. 性能

本次缓存复核 CLI 用时 553.99 s；原始 CAD 域采样实际用时 608.84 s（已记录的先前采样，未计为本次重新执行）；采样 8,443,050 个体素；分辨率 0.4 mm；精确 CAD 距离查询 3,399 次。曲面提取分配峰值估算 1701.10 MiB；进程高水位 RSS 3286.38 MiB；STL 86.71 MiB。各阶段计时与参数变体性能在执行报告和变体报告中。RSS 为进程高水位，100 ms 采样保护不是 OS 硬上限。

## 8. 问题与边界

首轮真实网格检查将闭合空隙的反向表面误当成倒置实体。当前按嵌套深度奇偶判断方向，同时独立检查跨壳接触/交叉；倒置、错误嵌套与相交壳的负面测试必须失败。裁剪影响区按完整 MC 体素对角线区分，避免将 Lewiner 混合单元内部 fan 顶点当作纯 Gyroid 零等值面；解析残差阈值未放宽，全部顶点重新检查。

原型 `.8 mm` 试验发现真实曲面材料点被 OCCT 点分类器报告 OUT，但独立三轴完整 CAD 布尔截线均判定在材料内。原始失败和解决证据保留在 `debug/domain/`。当前算法交叉核验原生有向截线，并对歧义使用三轴布尔确认；不能消除的分歧会失败。不能因此把原生点分类器的全局可靠性记为 PASS。

`threshold` 是无量纲参数，不是毫米壁厚。真实壁厚标定、全局最小特征、自相交全面检测、粉末排出、晶格与外壳连接、力学性能、制造、实体打印与穿着验证仍未完成。Docker/Linux 本轮未运行。冻结快照比较 184 个 M1 文件，变化 0 个。

## 9. 后续任务

向用户提交实际 M2 STL、预览、参数与测试证据。用户确认 M2 后，再进入 M3 鞋面网孔、外底/鞋面/晶格融合和完整制造模型检查。

## 10. 验收结论

当前已执行的 M2 技术检查为 **PASS**。M2 总体维持 **PARTIAL**，等待用户验收；制造/实体打印/穿着验证分别保持 NOT_VERIFIED / NOT_TESTED。
