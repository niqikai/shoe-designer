# M2 鞋头主体补全

生成时间：2026-10-16T16:06:18.395753+00:00；运行 `27bc88ca-51ed-4707-98f5-f97606958f86`。

本次实际技术检查 **PASS**；M2 总体 **PARTIAL**，用户外形验收待定，M3 未开始。M1 已确认鞋型和原有输出保持不变。

## 原因与修正

上一版曲线屋顶恢复了前掌上翘区，但 2 mm 间隔的稀疏认证点仍漏掉狭窄鞋头。原报告将孤立碎片的 X 最大值 274.494 mm 当作前端指标，主体实际只到 270.815 mm。独立 CAD 测量识别出 52 个满足预留条件的节点；旧版漏掉 45 个，另有 2 个落在分离的设计域。旧的低比例分区检查没有发现整层断面，不能用其 PASS 证明主体完整。

现在在 X≥259.105 mm 的末端区域自适应查询真实 CAD 边界，并补入经过距离证明的球域。原有负节点全部保留；屋顶、真实鞋体、鞋腔、2 mm 物理预留和完整体素对角线保护没有放宽。算法 `adaptive-certified-toe-grid-v1`；场算法 `m2-certified-curved-gyroid-v2`。深度阈值为 2.792828 mm，其中覆盖近似容差为 0.1 mm。完整性声明只覆盖指定走廊内深于该阈值的网格节点，不是连续域的全局精确偏移证明。

屋顶/计算边界及实际 CAD 最近点提供安全上界，减少无需进行的 CAD 查询。恢复文件只保存绑定输入与种子场的精确查询，必须重新完成覆盖遍历；它不是可直接验收的完整缓存。首次 16,000 次查询预算失败证据保存在 `debug/attempt_01_query_budget/`，未当作通过。

## 实际模型验证

主体前端由 **270.815 mm → 276.849 mm**，实际延伸 6.034 mm。此指标来自最大正体积连通 STL 表面，而非所有碎片的包围盒。独立预先测量的 CAD 节点缺失 0、落在分离域 0；末端失败截面 0。每个截面均重新计算解析 Gyroid 期望：有材料时要求主体 STL 三角面实际穿过，切面相切或孤立碎片不能满足检查。

真实 STL 共 809 个边界表面，正向材料表面 804 个；其余表面完整保留并在图中标为橙色。主体伸达通过不代表整鞋融合或所有碎片已连接。灰色是冻结 M1，蓝色为主体；剖面绿点代表域应存在的位置，可能落在合法 Gyroid 孔隙中。红叉表示测量位置不具备所需预留空间。最前端薄区仍不能强行填充晶格；M3 的外壳及实体过渡尚未生成。

原生 CAD 归属、鞋腔排除、网格水密/流形、方向、解析场误差及底域覆盖检查通过。使用绑定的精确查询记录重新遍历认证域，数组完全相同：`True`，新增原生距离查询为 0。随后独立重新采样 Gyroid、提取及实际 STL 序列化 SHA 相同：`True`。这是缓存查询重放，不是第二次全新原生 CAD 采样。

## 证据和性能

回归测试：554 PASS，0 FAIL，0 NOT_TESTED。冻结 M1 重放：5 PASS。保存的各目录文件核对总数 509，变化 0。

自适应查询 19829 次，新增负节点 22,658；网格 16,689,750 节点。实际主命令 3752.40 s，峰值进程 RSS 2697.67 MiB。具体新增/复用原生查询与阶段时间在 `performance_report.json`。旧 CAD 域的采样时间单列在来源记录，未计成重新采样。

交付目录 `output/m2_toe_complete/`：`lattice.stl`、原始域/Gyroid 场、参数、主验证、`terminal_coverage_report.json`、同角度真实网格 `terminal_comparison.png`、重复性、测试、性能及文件哈希清单。STEP 继续使用冻结 M1 基础 CAD；隐式晶格输出 STL，不提供伪造的晶格 STEP。

```sh
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2_toe_complete.json --base-dir output/m1_toe_refine --domain-cache output/m2_toe_repair --output output/m2_toe_complete --max-voxels 25000000 --max-field-memory-mb 6144 --max-rss-mb 8192
.venv/bin/python scripts/verify_terminal_lattice.py
.venv/bin/python scripts/repeat_terminal_lattice.py
.venv/bin/python scripts/preview_terminal_lattice.py
.venv/bin/python -m pytest tests/unit tests/integration/test_lattice_real.py tests/integration/test_lattice_toe_repair.py tests/integration/test_lattice_toe_complete.py -q --report-json output/m2_toe_complete/test_report.json
.venv/bin/python scripts/report_terminal_lattice.py
```

本轮没有重新验证周期、阈值、分辨率变体；历史 0.45 失败仍保留，参数稳健性保持 PARTIAL。全局壁厚、制造、打印及穿着分别为 NOT_VERIFIED / NOT_TESTED。复杂几何诊断和初始重建由 GPT-6 Astra / high 执行；GPT-6.1 Sol / high 独立审查发现并复现 MC 网格平面验收误判。主任务完成整合、资源优化、该误判修正、实际运行、独立验证及报告；后续 Astra 配额不足后未声称其完成余下修改。
