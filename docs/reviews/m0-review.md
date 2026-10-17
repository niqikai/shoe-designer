# M0 里程碑评审（M0.R）

- 日期：2026-10-17
- 分支：`m0/skeleton`，评审起点 `b012f9a`（M0.1–M0.10 全部合入）
- 评审：O·high（实现计划 M0.R）
- 依据：[implementation-plan.md](../implementation-plan.md) 第 1、2 节，[architecture.md](../architecture.md)，[ADR 0001–0006](../adr/)，[spikes/m0.6a-meshing.md](../spikes/m0.6a-meshing.md)

本文的每个"通过"都附实测值和产生它的命令；只给出断言阈值、没有实测值的项写明"仅断言"。真实鞋楦的结果是**暂定**的：H1（确认开发用鞋楦与归一化变换）尚未完成，归一化记录为 `orientation_confirmed = false`。

## 1. 结论

**M0 验收通过，可以进入 H2（目视检查 M0 鞋）。** 条件和保留意见：

1. 真实鞋楦的结果暂定，待 H1 确认坐标变换后复跑一次（命令见第 8 节，约 80 s）。
2. M0 的鞋底把晶格孔隙完全封死（真实鞋楦上是一个 269,170 mm³ 的封闭腔）。这不影响 M0 的验收（M0 不打印），但与 ADR 0002"开放结构易排粉"的论证相反，是 M1 必须先解决的设计问题（第 9 节）。
3. 薄片软检查在 0.5 mm 体素下分辨不了 1.2 mm 外皮，真实鞋楦上的薄片 finding 全部来自外皮，是分辨率极限，不是实测薄壁（第 8.4 节）。

评审发现并修复了 11 个问题（第 3 节），其中 1 个属于"未实测就写 pass"（空的 prebuild 组被汇总成 pass），1 个属于跳块正确性的漏检（`Extrude2D` 远场余量没有检查）。没有发现需要升级模型重做的正确性问题。

## 2. 评审方法与总体数据

| 项 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `uv run ruff check .`、`uv run ruff format --check .` | 无问题 |
| 默认测试（单元 + 冒烟） | `uv run pytest` | 评审前 458 passed / 163.6 s；评审后 463 passed / 138.5 s |
| 慢测试 | `uv run pytest -m slow` | 6 passed / 127.5 s |
| 真实鞋楦 | `shoegen build ... --voxel-mm 0.5`，4 次 | 全部退出码 0，`shoe.stl` 与 `graph.json` 四次逐位相同（第 8 节） |
| 代码通读 | `src/shoegen/` 全部模块、`tests/` 全部文件 | 第 3、4 节 |

为取得断言背后的实测值，评审用测试自身的辅助函数另跑了一个只打印数值的脚本（不入库），下文标为"评审实测"。

## 3. 评审发现的问题与处理

| # | 问题 | 处理 | 证据 |
|---|---|---|---|
| R1 | **未实测就写 pass**：`report.json` 的 `summary.prebuild` 在没有任何 prebuild 记录（或全是 `not_applicable`）时写 `"pass"` | 新增 `_group_summary`：有 fail 为 fail，至少一条 pass 才为 pass，否则 `not_applicable`；hard 组同样处理 | `tests/test_checks.py::test_report_never_passes_unrun_checks` |
| R2 | ADR 0004 的 soft 检查里，排粉和支撑没有实现，报告里也不出现 | 列为 `not_run` 并写明原因（`summary.soft_not_run = ["drainage", "supports"]`） | `test_pore_estimate` |
| R3 | **跳块漏检**：`Extrude2D` 的二维栅格外改用"到包围盒的距离"，在栅格边界向下跳变（评审实测：跨栅格边界的点对比值达 344，栅格内 1.004），但只有采样场有远场余量检查 | `GraphAnalysis.extrude_reach` 记录 `Extrude2D` 上方的累计偏置；`check_far_field_margins` 要求栅格余量 > 偏置 + 节点自身的正 `offset`，否则退出码 3。M0 鞋：余量 30 mm、需要 3 mm | `tests/test_evaluate_blocks.py::test_extrude2d_far_field_margin_must_exceed_offset_reach` |
| R4 | CLI 把图阶段之后抛出的带码异常一律当内部错误（1）：块规划的远场检查本应是 3，组装前的内存估算 `MeshMemoryError` 本应是 6 | `_code_of` 按异常的 `exit_code` 映射（3/4/6），其余为 1 | `tests/test_cli.py::test_typed_errors_keep_their_exit_code` |
| R5 | 鞋楦 loader 的授权字段沿用 PR #2 的 `REQUIRES_PURCHASE_AUTHORIZATION_REVIEW`，与 ADR 0003 不一致 | 只报告文件头声明（`license_declared`，真实鞋楦为 `CC BY-NC-ND 4.0`）和 ADR 0003 的用法 `usage_policy = DEVELOPMENT_ONLY_LOCAL_ONLY`，不作法律判断；CLI 的 `run.last` 也记录 | `tests/test_last_import.py`（3 个测试）、冒烟测试 |
| R6 | 看门狗测试没有检查"没有 PPID 为 1 的残留"（M0.1 验收原文） | 给辅助进程树打环境变量标记，扫描 PPID = 1 且带标记的进程；加一个先故意制造孤儿、确认扫描能看到它的自检。父进程被 SIGKILL 的测试收紧到计划原文的 2 s | `tests/test_watchdog.py`（3 个测试） |
| R7 | `last/projection.py` 的三个 numba 内核没有 `nogil=True`（M0.1 要求长内核放开 GIL，好让看门狗线程运行） | 补上 | 代码 |
| R8 | 看门狗线程会漏采峰值：真实鞋楦 workers = 1 时看门狗报 1.346 GB，内核 `ru_maxrss` 是 1.487 GB，进程外采样 1.487 GB | 报告和清单增加 `rusage_peaks`（主进程、最大子进程的 `ru_maxrss`），不依赖线程；限制写进 ADR 0005、计划 M0.10 | 第 8.2 节 |
| R9 | 薄片检查的 finding 没有说明分辨率：0.5 mm 体素下 1.2 mm 外皮会被标记 | 记录里加 `flagged_reliably_below_mm`、`not_flagged_above_mm`（`min_wall ∓ 2h`）；ADR 0004 写明 | `test_plate_below_min_wall_is_thin_above_is_not`；第 8.4 节 |
| R10 | `real_last` 测试把授权鞋楦的派生数据写进系统临时目录（违反计划 1.4） | 改写到 `output/` 下；可用 `SHOEGEN_REAL_LAST`（原地读取）与 `SHOEGEN_REAL_LAST_NORMALIZATION` 指定输入 | `tests/test_cli.py`、`tests/test_last_fields.py` |
| R11 | 默认测试 163.6 s，接近计划 1.3 的预算（单元 ≤ 3 min + 冒烟 ≤ 1 min） | heel_rise、hemisphere 两个芯部体积变体（EDT 参考 8–15 s）移到 `slow`；rounded 的同一检查留在默认集，0.5 mm 的三个鞋楦仍在 `slow` 里全查 | 第 10 节 |

另外，各任务交付时报告的 10 项已知偏差（文档与代码不一致或验收未覆盖）见第 7 节。

## 4. 重点审查项

### 4.1 稠密数组（计划 1.1、ADR 0005）

逐个检查了 `src/` 里所有按栅格分配的数组。没有覆盖整鞋包围盒的体素分辨率浮点数组。按设计存在、规模受控的稠密数组：

| 数组 | 分辨率 | 真实鞋楦上的规模 | 依据 |
|---|---|---|---|
| 鞋楦与高度曲面的粗栅格 | 2 mm，包围盒外扩 30 mm | 170 × 83 × 99（5.3 MiB）与 178 × 91 × 49（3.0 MiB）float32 | ADR 0003 明确规定 |
| 细栅格砖块索引 | 每砖 4 mm（8 × 0.5 mm） | 75 × 31 × 39 与 79 × 35 × 14 int32（0.3 / 0.1 MiB） | 只是索引；窄带砖块 22,056 与 12,758 块（61.3 / 35.5 MiB），只覆盖表面附近 |
| 足底轮廓 `Grid2D`、高度图 | 0.5 mm，二维 | 675 × 325 float32（0.8 MiB） | 二维 |
| 块计划 `status`、`centre_values` | 每块一个值 | 108 块 | 9 字节/块 |
| 每块的采样、MC 中间数组、检查用掩码 | 65³ / 64³（含 halo 时 76³） | 每块约 7 MB（float64 采样 2.2 MB、MC 边表 3.3 MB 等），在途 ≤ 8 块 | 与区域大小无关 |

块内所有坐标由全局整数索引一次乘法得到；网格组装的内存在分配前按 `BYTES_PER_RAW_VERTEX/TRIANGLE` 估算并与预算比较（真实鞋楦估算 0.66 GB）。

### 4.2 确定性（ADR 0005）

- **单一内核路径**：`kernels.eval_point` 是唯一计算场值的函数，`eval_points`、`eval_grid` 只是循环调用；所有 `@njit` 都是 `cache=True, nogil=True`，全仓库没有 `fastmath` 或 `parallel=`（`grep` 核对）。
- **全局整数坐标**：坐标为 `float64(i) × h`，`i` 相对世界原点；`test_different_regions_share_global_samples`：9,966 个探针在两个不同区域里逐位相同。顶点坐标只取决于边的两个采样值和全局索引（`mesh/mc.py`）。
- **块顺序与 worker 数**：结果按块序号收集；`test_workers_1_and_4_bitwise_identical`（采样数组）、`test_workers_1_vs_4_identical_stl`（STL sha256 `bce3fee2c76d27fa…` 两种 worker 数相同）。
- **真实鞋楦**：4 次构建（评审修复前、修复后冷缓存、热缓存、`--workers 1`）的 `shoe.stl` sha256 均为 `35e6d35acd54ae75…`，`graph.json` 均为 `890b2a1ba87d3237…`。
- `shoe.stl` 头是固定字符串，`graph.json` 是排序键的规范 JSON，二者都不含时间戳。

### 4.3 跳块正确性

- 判据 `|d(中心)| > 1.5 × (半对角线 + h)` 与 ADR 0005 一致；块中心是栅格点。
- 测试：所有被跳过的块都做全量采样，验证不含零交叉（`test_sampled_blocks_bitwise_and_skipped_blocks_uniform`：box/capsules/sphere 分别跳过 26/63/9 块，被跳块内最小 |d| 8.4–10.4 mm）；跳块与不跳块的网格完全相同（`test_skip_vs_no_skip_identical`）。
- 粗/远场切换：`test_skipping_across_coarse_far_switch` 实测跳变 14.35 mm，大于 k 留出的 7.30 mm 余量，靠"远场保号且 |d| ≥ 余量"保证安全（远场最小 |d| 10.25 mm ≥ 余量 10 mm）。评审补上了 `Extrude2D` 的同类检查（R3）。
- 真实鞋楦：108 块中只跳过 2 块（块边长 32 mm，鞋底与鞋面都薄，几乎每块都跨表面），跳块在这里几乎不省时间；被跳过的 2 块都判为外部，网格水密、不贴区域边界（`boundary_inside_blocks = 0`）。

### 4.4 Lipschitz

评审实测（随机点对的 |Δd| / |Δx| 最大值，点对与测试相同）：

| 对象 | 实测最大值 | 断言 |
|---|---|---|
| Box / RoundedBox / Capsule / HalfSpace | 0.99996 / 0.99998 / 0.99999 / 0.99997 | ≤ 1 + 1e-9 |
| 组合图的每个节点（Union、Intersect、Subtract、SmoothUnion、Offset、ShellOutward） | 0.99997 | ≤ 1 + 1e-9 |
| 采样场（细栅格窄带） | 1.0058 | ≤ 1.05 |
| 高度曲面采样场（窄带） | 1.0000 | ≤ 1.05 |
| 合成鞋楦采样场（细栅格窄带，M0.3） | 1.0230 | ≤ 1.05 |
| `Extrude2D`（栅格内） | 1.0039 | ≤ 1.05 |
| `Extrude2D`（跨栅格边界） | 344.5 | 不适用：远场是下界，由 R3 的余量检查保证安全 |
| StrutLattice r = 0.75 / 2.5 | 0.99994 / 0.99676 | ≤ 1 + 1e-9 |

### 4.5 焊接

- 顶点按全局边编号 `(i, j, k, axis)` 焊接；同一编号在不同块里坐标不同就抛 `WeldError`（没有容差）。
- 单块与 2 × 2 × 2 块提取的规范化网格完全相同（球、圆角盒、两胶囊，h = 1.0 和 0.7，χ = 2）；跨 8 块交点无裂缝（开放边 0）；随机零值场（每种 8 个场）都闭合、流形、与分块无关。
- 真实鞋楦：1,525,986 个原始顶点焊成 1,488,190 个（37,796 个跨块共享），1,190 个顶点重合的栅格点（采样值为 0 或被吸附为 0）全部合并成功，0 个被拒后移位；开放边、非流形边、退化三角形均为 0。

### 4.6 度量属性 exact / bound

逐条核对 `graph/analysis.py` 的推导表与 ADR 0001：叶子 exact；`Union` 外侧、`Intersect`/`Subtract` 内侧保持 exact，其余 bound；`Offset(+)` 要求子节点外侧 exact，`Offset(−)` 要求内侧 exact，`ShellOutward` 要求外侧 exact；`StrutLattice`、`SmoothUnion` 两侧 bound。表与 ADR 一致，证明在 `analysis.py` 文档里。M0 鞋图里唯一的向内偏置 `sole.core = Offset(envelope, −1.2)` 的子节点内侧 exact（`test_graph_structure_metric_and_checks`）；采样场累计偏置 `{"envelope.cap": 1.2, "last": 3.0}` ≤ 6 − 1。

### 4.7 退出码

| 码 | 证据 |
|---|---|
| 0 | 冒烟测试、真实鞋楦 |
| 1 | 其他异常（`_code_of` 单元测试） |
| 2 | `test_usage_errors_exit_2`（缺参数、体素为负） |
| 3 | 参数文件错误、杆径低于下限（不生成网格）、领口高于鞋楦、块规划远场检查（R4） |
| 4 | 非 STEP 文件、文件不存在、`LastMeshError` |
| 5 | 构建体积过小（打补丁的 profile），不写 `shoe.stl` |
| 6 | 看门狗测试（辅助脚本）；`MeshMemoryError`（R4） |

### 4.8 有没有未实测就写 pass 的条目

- 修复了 R1（空组汇总为 pass）。
- 每条 hard 记录的 `measured` 都是网格上算出来的值（开放边数、非流形顶点数、正体积壳数、包围盒尺寸）；prebuild 记录是图里的杆径与体素。
- soft 记录只有 `report_only`、`not_applicable`、`not_run` 三种结果，不会是 pass；检查出错时是 `not_run` 带错误信息（`run_grid_checks`）；排粉与支撑现在显式 `not_run`（R2）。
- 硬检查失败时不写 `shoe.stl`（PR #2 原则"失败候选不得冒充成功"）。
- 制造结论恒为 `NOT_VERIFIED`；profile 的 `status = placeholder` 写在每条记录的 `source` 里。

## 5. ADR 0001–0005 逐条核对

| ADR | 要求 | 结论 | 证据 |
|---|---|---|---|
| 0001 | 图就是 BuildPlan：规范 JSON → `graph_hash` | 符合 | `test_graph_hash.py`（往返不变、改任一参数即变、插入顺序无关、浮点 `repr` 往返） |
| 0001 | 采样场按内容哈希计入（源文件 sha256、采样参数、数组 sha256） | 符合 | `test_array_sha_participates_in_hash`；编译时重算数组哈希，不符即退出码 3 |
| 0001 | 节点白名单，`graph_version = 1` | 符合 | `test_graph_version_is_one`；未知类型、未知字段被拒 |
| 0001 | 每种解析节点 Lipschitz ≤ 1，有单元测试 | 符合 | 4.4 |
| 0001 | 不提供"高度图除以 √(1+G²)"的节点 | 符合 | 白名单里没有；回归测试：半球底面 G = 11.5，坡度缩放的芯部在碗底只剩 788 mm³，本实现 7,120 mm³ |
| 0001 | exact / bound 及偏置、壳的构建时检查（退出码 3） | 符合 | 4.6；`test_offset_of_bound_side_is_rejected` 等 |
| 0001 | 两端半径不同的杆用 round-cone | 不适用（M1） | — |
| 0002 | 杆状 BCC，周期公式枚举 27 个 Cell，固定顺序指数平滑并集，k = 0.15 r | 符合 | `kernels._strut_lattice`；轴线中段误差 4.0e-12 mm |
| 0002 | `kind` 字段 | 偏差，已接受 | `kind` 由节点类型表达（`StrutLattice`，TPMS 以后是新节点），ADR 0002 已注明 |
| 0002 | 杆径与体素的构建前检查（退出码 3） | 符合 | `test_parameter_check_*`、CLI 冒烟 |
| 0002 | 被裁切的杆并入外皮，不留断头 | 符合 | 装配写成 `envelope − (core − lattice)`；外皮厚度实测（M0.8） |
| 0002 | "开放结构易排粉" | **不成立于 M0 鞋底** | 孔隙被封死，第 9 节；ADR 0002 已记为开放问题 |
| 0003 | STEP 导入（含 `FILE_NAME` 修复）、显式刚体归一化 | 符合 | 真实鞋楦头记录被修复（`header_repaired_in_temporary_copy = true`），原文件不变 |
| 0003 | 双层采样、偏置量 ≤ 窄带 − 1 mm、`.npy` + mmap | 符合 | 4.6；`NpyDirectoryStore` |
| 0003 | winding number 定号 | 偏差，已接受 | 改为伪法向（ADR 0003 已更新），第 7 节第 3 项 |
| 0003 | 授权与本地化 | 符合（R5 修正了字段） | 鞋楦与派生几何只在 `output/`、`input/`，均被 gitignore |
| 0004 | Profile 字段齐全，两份占位 profile，数值来源留空 | 符合 | `profiles/*.json`，`sources` 全为 null，`status = placeholder` |
| 0004 | 三类检查、退出码 3/5、soft 只报告 | 符合 | 4.7；`test_soft_findings_never_change_exit_code` |
| 0004 | 连通分量数 = 1 | 符合，口径已写明 | 数正体积壳，第 7 节第 8 项 |
| 0004 | 制造结论 NOT_VERIFIED | 符合 | 所有报告 |
| 0005 | 64³ 块、跳块判据 k = 1.5、65³ 采样 | 符合 | `evaluate/blocks.py` |
| 0005 | 全局边编号焊接、零值按外部、合并与检查 | 符合 | 4.5；第 7 节第 5 项 |
| 0005 | 坐标由全局整数索引计算 | 符合 | 4.2 |
| 0005 | 固定 4 worker、按块序号合并、无 fastmath/parallel | 符合 | 4.2 |
| 0005 | RSS 上限覆盖主进程与子进程，超限先杀子进程再 `os._exit(6)` | 符合，有已知限制 | 看门狗测试；GIL 限制见 R8 |
| 0005 | 远场余量检查 | 符合（R3 补齐 `Extrude2D`） | 4.3 |

## 6. M0.x 验收逐条核对

"实测"列是测试或评审脚本算出的数值；"仅断言"表示测试只断言阈值，评审没有另取数值。除注明 `slow` 外都在默认测试集里。

### M0.1 脚手架与看门狗

| 验收 | 实测 | 结论 |
|---|---|---|
| `uv sync && uv run pytest` 通过 | 463 passed / 138.5 s | 通过 |
| 父进程退出码 6 并留下失败报告 | 退出码 6；报告含 `rss_bytes > limit_bytes`（限值 300 MB）与子进程 PID 列表 | 通过 |
| 报告里的 worker PID 5 s 内全部不存在；没有 PPID 为 1 的残留 | 全部消失；PPID = 1 且带进程树标记的进程 0 个（自检能找到故意制造的孤儿） | 通过（评审补齐，R6） |
| 父进程被 SIGKILL：worker 2 s 内自行退出 | 0.53 s | 通过（评审把测试收紧到 2 s） |

### M0.2 鞋楦导入

| 验收 | 实测 | 结论 |
|---|---|---|
| 圆角盒 STEP，非平凡旋转 + 平移，归一化后与直接变换的顶点比较 ≤ 1e-4 mm | 1.6e-14 mm（评审实测） | 通过 |
| 保留 `FILE_NAME` 修复、显式刚体归一化、`license_status` 字段 | 修复只改临时副本，原文件字节不变；授权字段已按 ADR 0003 改写（R5） | 通过 |

### M0.3 鞋楦采样场

| 验收 | 实测 | 结论 |
|---|---|---|
| 细栅格窄带误差 ≤ 0.05 mm | 离中轴 > 1 mm：0.0205 mm；含中轴：0.146 mm（半球） | 有条件通过（条件写入 ADR 0003） |
| 粗栅格区域误差 ≤ 1 mm | 离中轴 > 4 mm：0.058 mm；全部：0.990 mm | 通过（贴近上限） |
| 更远处的值不大于真值 | 粗栅格外的随机点全部满足，且全部为正（仅断言） | 通过 |
| 细栅格窄带 Lipschitz ≤ 1.05 | 1.0230（39,764 对） | 通过 |
| 高度图误差 ≤ 0.05 mm（坡度 < 60°） | 栅格节点 0.0031 mm；双线性插值 0.0277 mm；半球 0.0209 mm | 通过 |
| 高度曲面场窄带误差 ≤ 0.05 mm，含轮廓边缘陡坡（半球） | 离折痕 0.0278 mm（边缘附近同为 0.0278 mm）；含折痕 0.116 mm | 有条件通过（同上） |
| 足底轮廓（额外） | 0.0184 mm；含骨架附近 0.118 mm | — |

### M0.4 GeometryGraph

| 验收 | 实测 | 结论 |
|---|---|---|
| 每种解析节点有真值测试 | `test_graph_nodes.py`（仅断言） | 通过 |
| Lipschitz ≤ 1 + 1e-9 | 解析节点与组合图最大 0.99999（4.4） | 通过 |
| 不同批大小、不同顺序逐位一致 | `test_batch_size_and_order_do_not_change_bits`（仅断言，逐位比较） | 通过 |
| 序列化往返哈希不变，改任一参数哈希改变 | `test_round_trip_keeps_hash`、`test_changing_any_parameter_changes_hash`（仅断言） | 通过 |
| 对 bound 节点偏置以退出码 3 拒绝；exact 节点及其 Intersect/Subtract 向内偏置与真值一致 | 拒绝路径与 JSON 载入路径都测；向内偏置与解析真值比较（仅断言） | 通过 |
| 采样场偏置 ≤ 窄带 − 1 mm | `test_offset_on_sampled_field_limited_by_band`、`test_band_check_takes_max_over_shared_paths` | 通过 |

### M0.5 块稀疏求值

| 验收 | 实测 | 结论 |
|---|---|---|
| 细采样块上跳块与不跳块逐位一致；被跳过的块全量采样无零交叉 | box / capsules / sphere：跳过 26 / 63 / 9 块，被跳块内最小 \|d\| 8.9 / 8.4 / 10.4 mm | 通过 |
| worker 1 与 4 逐位一致 | 采样数组 sha256 相同（capsules `6b089d73…`、box `dd1e5983…`）；真实鞋楦 STL 相同 | 通过 |
| 报告块总数、细采样块数、峰值 RSS | 合成：125 块 / 99 细采样 / 1,569 MiB；真实鞋楦：108 / 106 / 2.906 GB | 通过 |

### M0.6a 网格提取方案评估

已在 [spikes/m0.6a-meshing.md](../spikes/m0.6a-meshing.md) 完成，结论自研；评审核对了 ADR 0005 与 M0.6 按其结论更新。本次未复跑 spike。

### M0.6 分块网格提取与焊接

| 验收 | 实测 | 结论 |
|---|---|---|
| 解析球（R = 20 mm，1 mm 体素）体积误差 ≤ 1% | −0.150% | 通过 |
| 球、圆角盒、两胶囊：单块与多块完全相同，水密，欧拉示性数正确 | h = 1.0、0.7 共 6 例全部相同，χ = 2 | 通过 |
| 跨 8 块交点无裂缝 | 开放边 0，与单块网格相同 | 通过 |
| 大量采样值恰为 0：水密、无退化三角形 | 5 种形状，2,010–4,005 个恰为 0 的采样，全部合并成功，0 个移位 | 通过 |
| 晶格块（`slow`，80 × 80 × 40 mm，0.25 mm） | 3,827,628 个三角形，χ = −2774（与 M0.6a 一致），体积 −0.54%，8.7 s，峰值 3.32 GiB | 通过 |

### M0.7 杆状晶格

| 验收 | 实测 | 结论 |
|---|---|---|
| 杆轴中段 d = −r（≤ 1e-9） | 4.0e-12 mm | 通过 |
| 节点中心 d ≤ −r | 等于 −r − k·ln 8（仅断言，1e-9） | 通过 |
| Lipschitz | r = 0.75：0.99994；r = 2.5：0.99676 | 通过 |
| Cell 边界两侧跳变 ≤ 1e-6 mm | 1.8e-15 mm | 通过 |
| 相对密度随 r 单调；0.25 mm 下与解析公式误差 ≤ 10% | r = 1.0 / 1.5 / 2.0：−0.23% / +4.09% / +5.88%；r = 0.75：+12.5%（杆上只有 6 个体素），改在 0.1 mm 下检查：+3.38% | 有条件通过：r = 0.75 在 0.25 mm 下不满足，测试改用 0.1 mm；r = 0.75（直径 1.5 mm）正好是 pbf 占位下限，0.25 mm 体素满足"体素 ≤ 直径 / 4"，所以这是"每杆 6 个体素时体积误差 12%"的实测事实，记在这里 |
| 构建时参数检查（退出码 3） | 杆径、体素两条，覆盖每个晶格节点 | 通过 |

### M0.8 鞋体装配（1 mm 体素、杆半径 2 mm；`slow` 里 0.5 mm、杆半径 1 mm 再查一遍）

| 验收 | 实测（rounded / heel_rise / hemisphere） | 结论 |
|---|---|---|
| 水密、单连通 | 1 mm：均为 1 个实体，空腔壳 5 / 14 / 59；0.5 mm：1 个实体，空腔壳 1 / 13 / 45 | 通过 |
| 鞋楦内部没有材料 | 鞋楦内 2 万点上鞋的场最小值 0.997 / 1.001 / 0.993 mm（应 ≥ 间隙 1.0 − 0.05）；射线奇偶 0 个异常 | 通过 |
| 领口开着：≥ 90% 射线先到足床板 | 100% / 100% / 100%（1,401 / 1,001 / 1,889 条）；真实鞋楦 100%（1,090 条） | 通过 |
| 后跟、鞋头下有鞋底材料 | 6 个位置全部从腔底（±1 体素）到地面（±1 体素） | 通过 |
| 外皮厚度 1.2 mm ± 1 体素（网格实测） | 1 mm：1.055–1.792 mm；0.5 mm：1.197–1.307 mm；场值 1.197–1.204 mm | 通过 |
| 芯部体积与 EDT 参考差 ≤ 5% | 1 mm：+0.96% / +0.70% / +0.87%（后两个在 `slow`）；0.5 mm：+1.52% / +0.94% / +1.07% | 通过 |
| 每个截面芯部面积 > 0 | 最小芯部面积 521.8 / 526.1 / 1,318.5 mm²，最小孔隙面积 213.7 / 217.2 / 711.5 mm² | 通过 |
| 半球回归（全局坡度缩放会吃掉芯部） | G = 11.5；碗底芯部 7,120 mm³，坡度缩放版 788 mm³；中心柱 8.6 mm | 通过 |

### M0.9 检查与报告

| 验收 | 实测 | 结论 |
|---|---|---|
| 构建前检查汇总进报告 | 杆径与体素两条记录，带实测值与阈值 | 通过 |
| hard（退出码 5）：水密、连通分量 = 1、构建体积 | 真实鞋楦四项均有实测值；构建体积失败用例退出码 5、不写 STL | 通过 |
| soft：薄片、封闭腔、最小孔径 | 平板 0.6 mm 被标记、2.5 mm 不被标记；封闭腔：空心球报告、开孔后不报告；喉径解析值 | 通过（薄片分辨率见 R9） |
| 栅格检查按块进行，不分配整鞋稠密数组 | 每块只留 32 KiB 的位掩码；最大临时数组为一块加 halo（76³） | 通过 |
| `report.json` 含实测值、阈值、profile 与 status；制造结论 `NOT_VERIFIED` | `test_report_json_is_deterministic_sorted_and_not_verified` | 通过 |

### M0.10 CLI 与端到端

| 验收 | 实测 | 结论 |
|---|---|---|
| 冒烟：合成鞋楦、1 mm、杆半径 2 mm、Cell 12 mm，≤ 1 分钟，水密单连通 | 11.4 s，137,016 个三角形，1 个实体；看门狗峰值 1.50 GB | 通过 |
| 同一输入两次，`shoe.stl`、`graph.json` sha256 相同 | `fe376afc…` / `39d475a1…` 两次相同；真实鞋楦 4 次相同 | 通过 |
| 杆径低于下限：退出码 3，不生成网格 | 半径 0.2 mm：退出码 3，旧的 `shoe.stl` 被清掉，hard 为 `not_run` | 通过 |
| 退出码 0–6 | 4.7 | 通过 |
| `real_last`：0.5 mm、杆半径 1 mm、6 GB 内完成，报告耗时与峰值 RSS | 80.3 s，峰值 2.906 GB（暂定，第 8 节） | 暂定通过（待 H1） |

## 7. 已知偏差的核实与处置

| # | 偏差 | 核实 | 处置 |
|---|---|---|---|
| 1 | M0.1：build123d 0.10.0 需要 `ocp-gordon==0.1.17`；看门狗验收缺"无 PPID 1 残留" | `pyproject.toml` 已固定并注明原因 | 计划 M0.1、ADR 0003 补记依赖；测试补齐（R6） |
| 2 | M0.2：`license_status` 与 ADR 0003 不一致 | 属实 | 改代码（R5） |
| 3 | M0.3：伪法向定号代替 winding number；自适应高度曲面三角化；有限曲面只在其 XY 矩形内精确；粗栅格中轴附近误差约 1 mm | 都属实。伪法向的前提（闭合、流形、取向一致、单连通）由 `check_closed_solid` 保证，不满足退出码 4；装配检查曲面矩形覆盖包络轮廓（退出码 3）。另发现：细栅格 ≤ 0.05 mm 的断言只在离中轴 > 1 mm 处成立（含中轴 0.146 mm），计划原文没有这个条件 | ADR 0003、计划 M0.3 更新，误差按"离中轴多远"分开写 |
| 4 | M0.4：`SmoothUnion` 是二次 smin，ADR 0002 写的是晶格节点的指数形式；`Extrude2D` 没有远场检查 | 两个函数各有用处，文档没区分；远场检查确实缺 | ADR 0002、计划 M0.4 写明区别；远场检查已实现（R3） |
| 5 | M0.6：ADR 0005 的零值处理（\|v\| < h/1024 视同 0、有条件合并、移位） | 逐条对照 `mesh/mc.py`、`mesh/assemble.py`：阈值 `h × 2⁻¹⁰`、只合并零长边相连的同坐标顶点、五种拒绝条件、移位量 `max(h/1024, 4 ulp_f32)`、反复检查、float32 量化，全部一致 | ADR 文字无需改 |
| 6 | M0.8：外皮与足床板封死孔隙 | 属实（第 9 节）；M0.9 的封闭腔检查确实报告为 finding | ADR 0002、architecture、计划 M1 记为开放设计问题；冒烟测试断言该 finding 存在 |
| 7 | M0.8：杯口薄唇（跟高鞋楦上约 0.66 mm）；75 mm 领口未在真实鞋楦上验证 | 薄唇数值来自 M0.8 报告，评审未复测；真实鞋楦上 z > 22.5 mm 没有任何薄片样本，杯口（z_top = 37.9 mm）处没有被标记。领口：真实鞋楦高 136 mm，75 mm 平面在鞋楦截面内的 1,090 条射线全部先落到足床板（100%） | 领口沿用 75 mm；薄唇列入 M1 开放问题 |
| 8 | M0.9：hard 连通分量只数正体积壳 | 属实，合理 | ADR 0004、计划 M0.9 写明口径 |
| 9 | M0.10：看门狗 GIL 饥饿；STEP 时间戳使缓存失效；`sheet_margin` 默认值 | GIL：真实鞋楦 workers = 1 时看门狗少报 0.14 GB（R8）。时间戳：只影响每次重新导出的合成 STEP，真实鞋楦热缓存命中（第 8 节 B 次）。`sheet_margin`：`FieldParams` 默认 30 mm，CLI 用 `max(8, outline_margin + 5)` | 计划 M0.10、ADR 0003/0005 记录；报告加 `rusage_peaks` |
| 10 | 默认测试约 163 s | 实测 163.6 s | 见 R11 与第 10 节 |

## 8. 真实鞋楦（暂定，待 H1）

### 8.1 输入与命令

- 鞋楦：开发用鞋楦 `sneaker_last.stp`（sha256 `6dd8e7b4…`，1,205,717 字节），原地读取，未复制、未入库、未上传；文件头声明 CC BY-NC-ND 4.0。
- 归一化：PR #2 的变换，旋转为单位阵，平移 `[-0.03027178197006718, -0.06173147834245, -26.160993061873167]` mm，约定 X 脚跟→脚尖、Y 宽、Z 向上，`orientation_confirmed = false`（报告标记 `PROVISIONAL_REQUIRES_USER_CONFIRMATION`）。
- 归一化后鞋楦：59,032 顶点、118,060 三角形；包围盒 x 0.0–276.6、y −50.5–50.5、z 0.0–136.0 mm。
- 参数：`examples/m0.json` 原样（杆半径 1 mm、Cell 10 mm、领口 75 mm）。领口高度检查过：鞋楦顶 136 mm，75 mm 平面截面落在 x ≈ 8–155 mm 的足背与后跟区域，领口开着（8.3）。profile `pbf-tpu-generic@0`，体素 0.5 mm，`--max-rss-gb 6`，4 worker。

```bash
mkdir .scratch/heavy.lock
/usr/bin/time -l .venv/bin/python <外部 RSS 采样脚本，0.2 s 间隔> \
  .venv/bin/shoegen build --graph examples/m0.json \
    --last <开发用鞋楦 STEP，原地> --profile pbf-tpu-generic@0 \
    -o output/m0_real_A --voxel-mm 0.5 --max-rss-gb 6 \
    --normalization output/m0_real_normalization.json --cache-dir output/cache/fields_review
rmdir .scratch/heavy.lock
```

### 8.2 结果

本文的 GB 一律指 2³⁰ 字节（与代码里 `--max-rss-gb` 的换算相同）。

| 项 | A：冷缓存，4 worker | B：热缓存，4 worker | C：热缓存，1 worker |
|---|---|---|---|
| 退出码 | 0 | 0 | 0 |
| 总耗时 | 80.3 s | 24.8 s | 53.1 s |
| 鞋楦导入（STEP → 网格） | 4.2 s | 4.4 s | 4.3 s |
| 采样场（SDF、轮廓、高度图） | 9.1 s | 0.0 s（命中） | 0.0 s |
| 图（含高度曲面采样场） | 46.6 s | 0.0 s（命中） | 0.0 s |
| 网格（采样 + MC + 焊接 + 校验） | 9.1 s | 9.2 s | 23.6 s |
| 检查（其中 soft 10.2 s） | 10.2 s | 10.2 s | 24.3 s |
| 峰值 RSS：看门狗（进程树之和） | 2.906 GB | 2.427 GB | **1.346 GB** |
| 峰值 RSS：进程外采样（进程树之和，0.2 s） | 2.906 GB | 2.427 GB | 1.487 GB |
| 峰值 RSS：主进程 `ru_maxrss` | 2.821 GB | 1.567 GB | 1.487 GB |
| `/usr/bin/time -l` 最大 RSS（单进程） | 3,028,877,312 B（2.82 GiB） | 1.57 GiB | 1.49 GiB |
| `shoe.stl` sha256 | `35e6d35acd54ae75…` | 同左 | 同左 |

评审修复之前的第一次运行（82.0 s，峰值 2.71 GB）也得到同一个 `shoe.stl` 和 `graph.json`。所有运行都远低于 6 GB。C 次看门狗比内核计数少 0.14 GB，说明线程确实会漏采（R8）。冷缓存时最慢的是高度曲面采样场（46.6 s，主进程峰值约 2.8 GB 也出在这一步），是 M1 的性能优化点。

网格与检查：

| 项 | 值 |
|---|---|
| 三角形 / 顶点 | 2,982,042 / 1,486,087（STL 149,102,184 字节） |
| 体积 / 面积 | 231,709 mm³ / 289,356 mm² |
| 欧拉示性数 / 亏格 | −4,934 / 2,492 |
| 块 | 9 × 4 × 3 = 108，细采样 106，跳过 2（外部），29,110,250 个采样 |
| hard：水密 | pass：开放边 0、非流形边 0、取向错误边 0、退化三角形 0 |
| hard：流形 | pass：非流形顶点 0 |
| hard：连通分量 | pass：正体积壳 1；表面壳 25，其中空腔壳 24 |
| hard：构建体积 | pass：282.6 × 107.0 × 87.0 mm ≤ 380 × 284 × 380 mm |
| prebuild | pass：杆径 2.0 mm ≥ 1.5 mm；体素 0.5 mm，杆上 4.0 个体素 |
| soft：封闭腔 | **finding**：24 个，合计 269,174 mm³，最大 269,170 mm³（孔隙网络），其余 23 个 ≤ 0.75 mm³ |
| soft：薄片 | finding（分辨率极限，见 8.4）：22,564 mm³，82 个有材料的块中 41 个 |
| soft：最小孔径 | 无 finding：BCC 喉径解析估算 6.16 mm ≥ 1.5 mm |
| soft：排粉、支撑 | not_run（M0 未实现） |
| 碎块 | 删除 0 个 |

### 8.3 目视与几何抽查

`output/m0_real_diag/` 下有 6 张 0.2 mm 像素的截面图（由图的场直接求值：橙色为晶格芯部内的材料，深灰为外皮与足床板，蓝色为鞋面，米色为鞋楦）：纵剖面 y = 0，横剖面 x = 40（后跟）、120（足弓）、220（前掌），水平面 z = −6（鞋底晶格层）与 z = 20。看到的是：平地面、1.2 mm 外皮包住的 BCC 晶格、贴着腔体的足床板、1 mm 间隙、2 mm 鞋面从鞋底侧壁顶部接上去，前掌处鞋面整个包住鞋头，后跟处鞋面一直到 75 mm 领口平面。没有看到断开、悬空或穿进鞋楦的材料。

领口检查（计划 M0.8 的判据，在真实鞋楦上用场值做）：领口平面 z = 75.0 mm 处鞋楦截面内、离表面 > 1 mm 的 1,090 个点，从 z_collar + 5 向下，第一次碰到材料的位置与离开腔体的位置相差 ≤ 0.5 mm 的有 1,090 个（100%，中位差 0.0 mm），即射线都先落到足床板，领口开着。

### 8.4 薄片 finding 的来源

把 180,511 个被标记的样本按位置分类：外皮（包络减芯部、离腔体 > 2 mm）22,558 mm³；晶格芯部 0；足床板及杯口附近 0；只属于鞋面 5.5 mm³。外皮样本的 z 四分位为 −11.5、−11.5、−11.0、−2.0、22.5 mm，主要是鞋底底面那层 1.2 mm 外皮（地面 z = −12.0）。这与评审的平板实验一致：0.5 mm 体素、`min_wall = 1.0 mm` 时，偏离栅格 0.13 mm 的 1.2 mm 平板被整片标记，与栅格对齐的不被标记，1.5 mm 平板与 2 mm 杆都不被标记。所以这项 finding 是检查的分辨率极限，不是测到了 < 1 mm 的壁。报告里现在写出 `flagged_reliably_below_mm = 0.0`、`not_flagged_above_mm = 2.0`（R9）。

## 9. 开放问题（移交 M1/M2）

1. **晶格孔隙被封死（M1 首要设计问题）**：合成鞋楦和真实鞋楦上，孔隙都是一个封闭大腔（真实鞋楦 269,170 mm³）。PBF 困粉、DLS 困树脂。候选方案：侧壁局部开口、外皮开排粉/排液孔、部分开放外皮。选定后封闭腔检查改为连通性检查，并在 `requires_drain_holes = true` 时升为 hard。见 ADR 0002、计划 M1。
2. **薄片检查分辨率**：升为 hard 之前换更细的体素（检查本身按块，0.25 mm 下耗时约为 8 倍）或改为在网格上实测厚度。
3. **杯口薄唇**：M0.8 在跟高合成鞋楦上报告约 0.66 mm；真实鞋楦上未被标记。M1 的边界节点一并处理。
4. **领口**：水平面领口在真实鞋楦上开口很长（x ≈ 8–155 mm），M2 的 Rim 与领口曲线解决。
5. **性能**：冷缓存时高度曲面采样场占 46.6 / 80.3 s 和约 2.8 GB 峰值；块边长 32 mm 时真实鞋楦只跳过 2/108 块。M1 视需要优化（高度曲面的加密策略、块大小或两级跳块）。
6. **看门狗**：进程内线程会漏采（R8）；进程外监督留给 M1 视需要实现。
7. **归一化未确认**：H1 完成后用确认的变换复跑真实鞋楦。
8. **build123d 弃用警告**：测试里 4 个 build123d 0.10.0 的 `DeprecationWarning`，来自依赖本身，不影响结果。

## 10. 测试与提交

- `uv run ruff check .`：无问题；`uv run ruff format --check .`：无问题。
- `uv run pytest`（默认）：463 passed、8 deselected，138.5 s（评审前 458 passed，163.6 s）。其中冒烟构建 11.4 s（CLI 测试合计约 20 s），在 1 分钟预算内；其余单元测试约 118 s，在 3 分钟预算内。
- `uv run pytest -m slow`：6 passed / 127.5 s（0.5 mm 三个合成鞋楦全套 M0.8 验收、两个芯部体积变体、80 × 80 × 40 mm 晶格块：3,827,628 个三角形、χ = −2774、体积比参照 −0.54%、峰值 RSS 3.32 GiB）。
- 每条验收都至少有一个默认执行的测试；移到 `slow` 的只有 heel_rise、hemisphere 的芯部体积变体（rounded 的同一检查仍在默认集）。
