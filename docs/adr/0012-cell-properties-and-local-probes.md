# 以按需求值的 Cell Properties 与局部探测取代离线 Feasibility Table

Resolver 不再查离线生成的 Feasibility Table，而是调用 Cell Properties 求值器：输入 (Lattice Family, Variant, a_s = L_s/L_n, a_t = L_t/L_n, ρ)，只在 Resolver 真正用到的点上求值，不插值。等值面阈值 c 与 Cell 形状无关（各轴独立缩放不改变体积分数），退化为一维数据表 `gyroid_c.v1`；颈厚取自 Gyroid 的解析临界点（每个 Cell 12 根 Ligament，分成若干对称类），每类用 Direction C 细阶段在解析周期场上测量，端部受限 Ligament 的约定与 Printability Check 相同；孔喉在周期 Cell 上做逾渗瓶颈计算。结果写入持久缓存：随引擎发布的种子 `cellprops.seed.v1` 是跨平台权威的数据表，本地缓存按"方法 + 代码摘要 + uv.lock + 平台"分命名空间，同环境下与重算逐位一致，种子之外的冷查询跨平台只保证 ≤ τ。ρ 定义域改为 [0.12, 0.46]，Override 白名单中的 ρ 改为 0.12–0.45。

另在 Resolver 阶段 B 末尾（定稿之后）加局部探测：按 Landmark、Zone 与 Shell 参数选出至多 16 个窗口，在真实合成场上以 0.25 mm 执行体素类 Printability Check。窗口是 Direction C 的分块（核心加 halo，对齐整鞋全局栅格），不裁切 Shoe Body，所以核心内的颈厚读数与整鞋校验同源，没有人为的 Cut Edge；窗口外表面只在连通类检查中起作用，视为依托或外部。探测只做闸门，不触发 Adjustment：失败按 hard 级 Printability Check 提前判定，退出码 5，错误码 `E_PROBE_*`。

原因有两个。第一，离线全表约需 45 核·天，还有插值误差，且覆盖不到非周期区域：Grading 带、Transition、曲面 Shell、Welt 处熔合、T < C 的薄 Midsole、Toe Spring 的倾斜层。第二，Resolver 放行不等于最终可打印（ADR 0010：检查点跳过死区，放行的设计在 Printability Check 中以 9–10 % 的薄壁失败），而整鞋 Build 要跑完 10 分钟才暴露。按需求值把成本降到典型 4 次随形查询，局部探测把非周期区域的偏差提前到定稿之后一分钟内暴露。

## Considered Options

- 离线全表：13 × 13 种 Cell 形状 × 111 个 ρ ≈ 1.9 万组，参考方法单组 16–300 s，合计约 45 核·天，有插值误差，覆盖不到非周期区域。拒绝。
- 利用"L_s 无关"裁剪的离线表：约 800 组，4 个 worker 约 11 小时。依赖只在 L_s ∈ {6, 8, 11} 上验证过的假设，仍有插值误差，仍覆盖不到非周期区域。拒绝。
- 用 Direction C 整套方法（含粗阶段）在周期 Cell 上求值：粗阶段的 σ 固定导致漏检与虚假输出（胖薄向 Cell 有 23 次漏检、59 个虚假输出）。拒绝；改用解析拓扑，没有粗阶段。
- 用参考方法按需求值：每次 20 s 到 5 分钟，Resolver 冷启动最坏约 9 分钟（4 个 worker）。拒绝。
- 局部探测按裁切处理窗口面：窗口面成为 Shoe Body 边界，面附近的 Ligament 被切成楔，被端部受限判定归入"裁切残边"，核心内看不到真实读数，窗口小时占核心相当部分。拒绝；改用 halo。
- 局部探测失败时触发 Adjustment 循环（把缺口折算成 ρ 约束，重跑 7.3 第 4 步，重探受影响的窗口，最多 2 轮）：(1) Preview Build 不跑探测，若只在 Print Build 中改 ρ，就破坏"Preview 与 Print 共用 plan_hash"；(2) 已知会让周期模型失效的机制（混合新生、跨坐标系混合）都已通过设计变更消除，剩下的探测失败更可能是引擎缺陷或 [初值] 阈值不当，自动提高 ρ 会把它们藏起来；(3) 闸门没有搜索 Tier Band，不应称为 Infeasible；(4) 每轮重探约 1 分钟，且改 ρ 会改变窗口内的薄颈分布，结果不一定单调。拒绝，列为 P1 选项。
- ρ 定义域收窄到 0.15–0.45：soft 的 Tier Band 下沿是 0.125，收窄会让 soft 档带内 Adjustment 失效；Footbed Layer 在 `rho_delta` 覆盖到 0.15 时派生 ρ 可达 0.455。拒绝。
- 把真值扩到 ρ 0.05–0.60：没有设计用途（P0 档位不用，Shell 在 ρ 0.175 以下已不可行），ρ ≥ 约 0.5 时是否仍是单一三价网络未知，并且要为几乎没人用的 Override 区间承担验证成本。拒绝。

## Consequences

- `neck_margin_mm` 保持 0.05 mm，论证改写：近阈值的校验精度要求 ≤ 0.021 mm（与实测最大误差一致，规格 10.2），加求值器误差 ≤ 0.021 mm（同核同法，期望 ≤ 0.01 mm），不再有插值项，端部受限类与校验同约定，余量至少剩 0.008 mm；Print Mesh 简化造成的颈厚损失由规格 8.5 的简化后复测兜住，不进这笔账。求值器误差实测 > 0.021 mm（与测试阈值相同）时回到设计讨论，不擅自加大余量。
- 缓存的确定性契约（ADR 0006）：求值器是 key 的纯函数，同环境下缓存值与重算值逐位相同，冷热缓存的 BuildPlan 与 Print Mesh 逐字节相同；种子命中的查询跨平台权威；种子之外的冷查询跨平台只保证 ≤ τ（颈厚 0.002 mm，孔喉 h_p，即孔喉栅格的一个体素 L_n/100），Build Manifest 标 `near_threshold`；`--plan` 只校验模式的判定放宽 τ。
- 新增退出路径：局部探测失败退出码 5，错误码 `E_PROBE_THIN_WALL`、`E_PROBE_TRAPPED_POWDER`、`E_PROBE_HANGING_MATERIAL`、`E_PROBE_ISLAND`、`E_PROBE_CAVITY`；可归因于某个 Zone 的 ρ 时附 `json_pointer` 与由求值器算出的 `suggestion`。`--probes gate|warn|off` 可降级，Preview Build 与 Crop Build 默认不探测。
- 探测的残余风险：4 根鞋底柱的核心约占鞋底 Lattice 的 5 %（16 × 16 mm × 4 对 EU42 约 21 000 mm² 的鞋底面积；算上 halo 才到 20–25 %，但只统计核心），Shell 窗口的核心约占 Shell 的 5 %，Welt 窗口约覆盖 Footbed Edge 的 20 %，其余位置的非周期偏差仍只在整鞋 Printability Check 中发现（仍是权威判据）；一个 16 mm 核心只采到约 1.6 个 C = 10 的 Cell，相位采样不足；连通类检查对窗口外表面取乐观处理；局部 1 % 比全鞋 1 % 严格，可能误报；窗口读数与整鞋读数的等价性只是预期，由 M6.11 实测。
- P1 重新考虑 Adjustment 循环的条件：Build Manifest 记录每次探测失败的缺口，若 P0 期间的失败大多是"小 ρ 缺口且杠杆明确"，再引入有界循环，并同时让 Preview 跑探测，或放宽 plan_hash 共用规则。
- 种子必须随方法 id 升版本重生成；它的随形部分是录制闭包，`tuning.v1`、比例表、Collar 形状参数或形状 fixture 改版后也要重生成；种子守门（抽样逐位重算、G1–G5 与 Tuning Table 自检全部命中、sha256）进入 pre-merge。
