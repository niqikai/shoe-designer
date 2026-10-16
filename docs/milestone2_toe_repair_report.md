# M2 脚趾晶格覆盖修复

生成时间：2026-10-16T09:13:32.306338+00:00。主运行：`21fae48f-3250-4965-997c-6b3a0bd11452`。算法：`m2-certified-curved-gyroid-v2`。

## 状态与问题

本次脚趾覆盖修复：**PASS**。M2 保持 **PARTIAL**，用户外形确认待定；M3 未开始。主模型与已执行的周期/阈值 0.5/重复导出检查通过。阈值 0.45 在新域中触发严格非流形检查，保留失败证据，参数稳健性仍为 PARTIAL；不能宣称所有合法参数均已成功。

旧域用水平 Z 上界裁掉上翘鞋头。现在由冻结 M1 的 rocker 和下部足型连接高度构造随 X 变化的屋顶：取 rocker 加名义底厚、连接导线减过渡半带的较低值。1 mm 上限间距的线段定义明确设计域；不是原鞋底 B-Rep 的精确重建。真实鞋体、鞋腔和底面圆角继续限制材料。证书半径同时使用真实 CAD 边界距离和屋顶线段的欧氏距离，再扣除预留及完整体素对角线，不能用竖直间距替代斜面法向距离。

## 实际前端与覆盖

鞋体前端 X=280.410 mm。旧晶格前端 252.890 mm，新晶格 274.494 mm，恢复 **21.604 mm**；与鞋体最前端的 X 间隔由 27.520 mm 缩至 5.916 mm。这是包围盒差值，不是均匀外壳厚度；尖端余量、薄区和保守省略仍存在，尚未生成实体过渡或与外壳融合。

新旧模型都用同一个曲线设计域、同一冻结 CAD 做独立竖向材料区间检查，未用旧晶格自身边界决定检查范围。旧模型覆盖结果 **FAIL**，失败分区 [9, 10]；新模型 **PASS**，失败分区 []。新模型要求覆盖的分区 11 个，其中基准面以上的上翘区分区 2 个。薄区、空区、实际可用区的每个采样位置都在 `toe_coverage_report.json`，有限线长求和不是体积或全局覆盖证明。

周期 16 mm 的末端分区初次稀疏测量落入相位空隙。该分区使用预先确定的 7 × 7 X/Y 点阵加密，保持原覆盖阈值，并保留首次测量。新增位置重新测量真实 CAD 与 STL，未平滑或修改三角面。已通过的其他检查只在原 STL、设计域、CAD 来源哈希匹配时复用；补充测量用时、环境、原报告哈希和新增/复用原生站点数单独记录，未伪称整套变体全部重新运行。

## 文件与预览

结果位于 `output/m2_toe_repair/`：`lattice.stl`、原始场 NPZ、`domain_provenance.json`、元数据、`validation_report.json`、`toe_coverage_report.json`、`toe_comparison.png`、`preview.png`、`parameter_sections.png`、测试/性能/验收报告和文件哈希清单。所有图片来自实际 STL 三角面，没有平滑或删掉碎片。灰色 M1 与新旧晶格使用相同坐标、相同相机和画幅。旧 `output/m2/` 和 M1 全部保留。

## 测试与参数

主回归：520 PASS、0 FAIL、0 NOT_TESTED。冻结 M1 重放：5 PASS。核对 1598 个 M1 历史文件及 80 个旧 M2 文件，变动均为 0。

主 STL 重导入后有 1,934,750 个三角面，边界边 0、非流形边 0、奇异顶点 0；水密、方向及跨壳分离检查通过。867 个边界壳中 862 个为正向材料外壳、5 个为反向内部空隙壳；861 个额外材料碎片仍保留，不能声称最终整鞋已连接。原生 CAD 点检查 4096 个，鞋体外 0、侵入鞋腔 0；独立线扫描和解析场误差检查通过。域候选节点中 239,717 个未获证书而被保守省略，不能把保留域当作全局精确偏移结果。

| 比较 | 周期 mm | 请求阈值 | STL 体积 mm³ | 结果 |
| --- | ---: | ---: | ---: | --- |
| period_16 | 16.0 | 0.6 | 61029.997 | PASS |
| threshold_050 | 12.0 | 0.5 | 50600.416 | PASS |
| repeat | 12.0 | 0.6 | 61087.801 | PASS |

重复导出 SHA 相同：`True`。这是同一认证域的独立场/提取重复，不是第二次 CAD 域重建。本轮未重新生成分辨率变体；原 M2 的分辨率证据保留，但不能代替新曲线域的分辨率验证。

0.45 失败记录：`debug/parameter_attempt_01/threshold_045_failure.json`。该次精确零等值面同时出现微小三角和非流形边，按现有规则立即停止，未用数值偏移绕过拓扑失败。修复了诊断异常的进程间序列化，避免一个几何失败破坏整个 worker pool。另以明确请求的 0.5 验证阈值确实改变真实几何；没有把 0.5 报成 0.45 通过。

## 性能与执行

主命令 1575.63 s，其中新 CAD 域采样 712.33 s；16,689,750 个网格节点，间距 0.4 mm。主进程峰值 RSS 4029.08 MiB；STL 92.26 MiB。RSS 为进程高水位，保护为采样机制，不是整机硬内存限制。

```sh
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2_toe_repair.json --base-dir output/m1_toe_refine --output output/m2_toe_repair --max-voxels 25000000 --max-field-memory-mb 6144 --max-rss-mb 8192
.venv/bin/python scripts/preview_lattice_toe_repair.py
.venv/bin/python scripts/verify_lattice.py --output output/m2_toe_repair --jobs 2 --worker-memory-mb 6144 --threshold-variant 0.5
.venv/bin/python scripts/verify_lattice.py --output output/m2_toe_repair --jobs 2 --worker-memory-mb 6144 --threshold-variant 0.5 --reuse-passed
SHOE_POC_M2_OUTPUT=output/m2_toe_repair .venv/bin/python -m pytest tests/unit tests/integration/test_lattice_real.py tests/integration/test_lattice_toe_repair.py -q --report-json output/m2_toe_repair/test_report.json
.venv/bin/python scripts/report_m2_toe_repair.py
```

几何域由 GPT-6 Astra / high 实现，独立验证由 GPT-6.1 Sol / high 准备，主任务完成真实执行、整合及剩余修正。M1 已确认外形未改动。本阶段仍是独立晶格，保留孤立碎片并报告；外壳融合、末端实体过渡、完整整鞋连通、全局最小壁厚、制造、实体打印和穿着均未验证或未实现。
