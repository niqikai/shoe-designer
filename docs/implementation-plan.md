# P0 实现计划

本文把 [p0-spec.md](p0-spec.md) 拆成可独立验收的里程碑与单会话任务。依据只有规格、[GLOSSARY.md](../GLOSSARY.md) 与 [docs/adr](adr/) 0001–0012；本计划不新增需求。规格没有说清、实现前必须由人拍板的地方，集中列在第 10 节"规格缺口"，各任务里只引用编号（如 **[缺口 G3]**），不擅自决定。

**已确认的方向**：
- 从原型转入实现。§10.2 新规则（端部受限判定、悬挂材料检查）**不再单独做合成端到端验证**；改为把"G1（全默认）整鞋端到端通过全部 hard 检查"作为第一项验收，并用 G1 的实测数据定稿相关 **[初值]** 阈值（实体接触距离 1.0 mm、端部触及边界容差 0.1 mm、悬挂材料 1 mm³）。
- 顺序：本计划 → Shape Source（先做 Blender 程序化控制笼，它是 §20 中唯一尚未碰过的风险，尽早产出 G1–G4 形状供目视检查）→ 与之并行的 Resolver / DesignSpec（纯逻辑）→ Cell Properties 求值器与种子、定稿 `tuning.v1`（含 airy）→ 晶格引擎与校验（移植分块 Direction C、单一鞋底 Cell、Welt）→ G1 端到端验收 → 实物验证。
- 局部探测为 Print Build 阶段 B 的闸门，不改 BuildPlan（ADR 0012）。

**记号**：模型 O = Opus（`claude-opus-5-5`），S = Sonnet（`claude-sonnet-5-5`），H = Haiku（`claude-haiku-5-5`）；effort 为 low / medium / high / xhigh / max。规模按 agent 会话数估：S ≤ 3，M 4–8，L ≥ 9。**H1–H7** 为人工检查点（第 0.3 节）。

---

## 0. 总览

### 0.1 里程碑汇总

| 里程碑 | 目标 | 任务（模型·effort） | 依赖 | 规模 |
|---|---|---|---|---|
| **M0** 工程骨架 | 能装、能测、公共基础件就位 | M0.1 H·medium；M0.2 S·medium；M0.3 S·medium | — | S（3） |
| **M1** Shape Source | Blender 程序化控制笼产出合规 Shape Bundle；G1–G4 形状目视签收；6 份 fixture 入库 | M1.1 S·high；M1.2 S·medium；M1.3 S·high；M1.4 S·medium；**M1.5 O·xhigh**；M1.6 O·high；M1.7 O·high；M1.8 O·high；M1.9 S·high；M1.10 S·medium；**M1.R O·high** | M0；H1 | L（11） |
| **M2** DesignSpec 与 Resolver | 阶段 A / B、Override、约束求解、schema 导出、错误格式（用桩求值器） | M2.1 S·medium；M2.2 S·medium；M2.3 S·medium；M2.4 H·medium；M2.5 S·high；M2.6 S·medium；**M2.7 O·high**；M2.8 S·high；M2.9 S·medium；**M2.R O·high** | M0（与 M1 并行）；M2.8 需 M1.3 | L（10） |
| **M3** Cell Properties 与 `tuning.v1` | 按需求值的 Cell Properties 求值器与持久缓存、`gyroid_c.v1` 与种子，定稿 Openness 与 HeelCounter 的 ρ，冻结 G2 Adjustment 与哈希向量 | M3.1 S·high；M3.2 O·high；**M3.3 O·high**；M3.4 O·high；**M3.5 O·high**；M3.6 S·high；M3.7 S·medium；**M3.8 O·high**；M3.9 S·medium；**M3.R O·high** | M3.1–M3.5：M0；M3.6 起需 M2.6/M2.7；M3.8 需 M3.6、H1；M3.7 需 M1.10 与 H2 批准的 `tuning.v1`；M3.9 需 M3.7 | L（10） |
| **M4** 几何引擎 | Zone 推导、单一鞋底 Cell、Welt、随形场、合成、分块提取、清理、简化、导出 | M4.1 O·high；M4.2 O·high；M4.3 S·high；**M4.4 O·xhigh**；M4.5 O·high；M4.6 S·high；M4.7 S·high；M4.8 S·medium；M4.9 S·medium；M4.10 S·medium；**M4.R O·xhigh** | M1.3（合成 Bundle 起步）、M3.1；收尾需 M1.10、M3.6 | L（11） |
| **M5** Printability Check | 移植分块 Direction C（逐位一致）、端部受限判定、悬挂材料、困粉、网格类检查、Donning | M5.1 S·high；**M5.2 O·high**；M5.3 O·high；M5.4 O·high；M5.5 O·high；M5.6 S·high；M5.7 S·low；M5.8 S·high；M5.9 O·high；**M5.10 O·high**；**M5.R O·xhigh** | M5.1/M5.2：M0（解析场）；M5.3 起需 M4.2、M4.4 | L（11） |
| **M6** 集成与 G1 验收 | CLI、Build Manifest、Crop / Coupon Build；G1 0.25 mm 整鞋全部 hard 通过；阈值定稿；黄金回归 | M6.1 S·high；M6.2 S·medium；M6.3 S·medium；M6.4 S·high；M6.5 S·medium；M6.6 S·low；M6.7 S·high；**M6.8 O·xhigh**；M6.9 S·medium；M6.10 S·high；**M6.11 O·high**；**M6.R O·xhigh** | M2、M3、M4、M5；H3 | L（12） |
| **M7** 实物验证 | 服务商 Profile、裁切件与 Coupon 实打、修订数据表、整鞋实打 | M7.1 S·high；M7.2 S·low；M7.3 O·high；M7.4 S·medium；**M7.R O·high** | M6；H4–H7 | M（5 + 外部周期） |

### 0.2 关键路径与并行

```
M0 ─┬─► M1（Shape Source）──────────────► H1 ─┐
    ├─► M2（Resolver，桩表）─────────────────────┤
    ├─► M3.1–M3.5（参考方法、孔喉、求值器 spike、缓存）─┼─► M3.6、M3.8 ─► H2 ─► M3.7、M3.9 ─┐
    ├─► M5.1–M5.2（Direction C，解析场）─────────┘                      │
    └─────────► M4（几何引擎；起步用合成 Bundle）─► M5.3–M5.9 ───────────┼─► M6 ─► H3 ─► H4 ─► M7
```

- 关键路径：M0 → M1 → H1 → M3.8 → H2 → M3.7 → M6.8。M3 不再有离线长计算。局部探测（M5.10、M6.10）在 M4、M5 之后接入。控制笼越早出形状，越早得到 fixture 实测的 P(0)、P(0.5)、P(1)，Cell Properties 的定义域覆盖（§7.5 覆盖要求）才能定下。
- M1 与 M2 由两个 worktree 并行开发；M3.1–M3.5、M5.1–M5.2 只依赖解析场，可在机器空闲时穿插。**同一时刻只允许一个重计算任务**（第 1.1 节）。

### 0.3 人工检查点

| 编号 | 时点 | 用户要做 / 决定的事 |
|---|---|---|
| H0 | 现在 | 审阅本计划；对第 10 节缺口给出决定，或指定在哪个里程碑前决定 |
| H1 | M1.10 之后 | 目视签收 G1–G4 的 Last 与 Sole Envelope（出图 + GLB）；确认 `proportions.v1`、`sizes.v1` 引用的文献出处；决定 [缺口 G1、G2、G3] |
| H2 | M3.8 之后 | 批准 `tuning.v1` 的定稿值（Openness、HeelCounter 的 ρ）、ρ 定义域 [0.12, 0.46] 与白名单收窄、求值器方法与精度（M3.3 的 spike 结论） |
| H3 | M6.8 之后 | 批准由 G1 实测定稿的 [初值] 阈值（1.0 mm、0.1 mm、1 mm³）；审阅 soft 警告清单；批准探测集（窗口数、尺寸、局部阈值）与 M6.11 的等价性结论；合并 G1 验收 PR |
| H4 | M7 开始前 | 选定服务商（优先 MJF），提供其 TPU 设计指南；决定 [缺口 G11] |
| H5 | M7.2 之后 | 下单打印裁切件与 Coupon（付款由用户本人完成）；做排粉试验、`heel-collar` 试穿、三档 Coupon 压缩试验并记录结果 |
| H6 | M7.3 之后 | 批准数据表修订（升版本） |
| H7 | M7.4 之后 | 下单打印整鞋；实物到手即 P0 完成（§1.4 第 3 条） |

另外，每个里程碑的 PR 由用户合并进 master。

---

## 1. 全局工程规则

### 1.1 内存安全（16 GB M4）

- **一律经 memguard**：凡是会采样超过约 1000 万体素、调用 Blender、生成 Cell Properties 种子与扩展真值、执行 0.5 mm 及更细的 Print / Crop Build、或带 `slow` 标记的测试，都必须用 `scripts/memguard.sh <GB> <cmd>`（M0.1 从 `.scratch/memguard.sh` 收入仓库，原样保留其"整棵进程树 RSS 超限即杀"的行为）。默认上限：
  - 开发实验、harness、单元级重测试：**6 GB**；
  - Blender 子进程与 fixture 生成：**4 GB**；
  - G1 0.25 mm 整鞋 Print Build 与 Crop Build：**9 GB**（规格预算 8 GB，§13；超过 8 GB 即视为未达标，9 GB 是保护机器的硬上限）。
- **不建整体浮点栅格**：任何阶段都不得分配覆盖整只鞋包围盒的浮点数组（0.25 mm 下整鞋 2.5–3.5 亿体素，单个 float32 数组就超过 1 GB）。场按块生成、用完即释放；跨块信息只保留记录（盆地对、并查集、网格片）。代码评审把这一条当硬性检查项。
- **worker ≤ 4**：进程池默认 4 个 worker（§13 实测：4 个 worker 约 1.95 GB，6 个约 2.5 GB，最坏外推约 3 GB）。不得按可用内存自适应地增减 worker 或块大小（ADR 0006）。
- **先估算再执行**：每次重计算前，在任务记录（PR 描述或提交说明）里写出估算：体素数 × 字节/体素 + 每进程解释器约 0.15 GB。参考系数：Direction C 粗阶段每块 55–68 B/体素、每 worker ≤ 0.75 GB；网格阶段在鞋底大小区域实测峰值约 5.8 GB（§13）。估算超过上限的 70% 时，先缩小规模或改成分块再执行。
- **一次只执行一个重计算**：并行开发的多个 agent 共用一台机器。重计算前用 `mkdir .scratch/heavy.lock`（原子操作）取得互斥锁，结束后删除；取不到锁就等待或改做轻任务。
- 长任务放后台执行，输出写到 `.scratch/` 下的日志；不在前台阻塞会话。
- 测试必须把 `SHOEGEN_CACHE_DIR` 设为临时目录，避免污染用户的 Cell Properties 本地缓存。

### 1.2 确定性（ADR 0006，§14）

- 块尺寸（`chunk_voxels` = 128、Direction C 核心 128³、halo 40 / 回退 64 体素）、体素原点偏移、遍历与合并顺序全部固定；并行只发生在块之间，结果按块的固定序号合并。
- 场必须可以在任意点上确定性求值，同一点的结果与分块方式、数组位置无关（§8.3"点求值要求"）。凡改动场代码的提交，都要通过"同一点在不同分块下逐位一致"的测试。
- numba 代码禁用 `parallel=True` 与 `fastmath=True`；向量化路径与逐点路径不得走不同的浮点运算顺序（例如一处用 FMA、一处不用）。
- 第三方步骤只用确定性实现（manifold3d 的 simplify、fast-simplification 已在单机跨进程复测一致，§14）。
- 输出不含时间戳（STL 文件头、GLB）；dict / set 的遍历一律先排序；P0 没有随机种子。
- Cell Properties 求值器是 key 的纯函数：单线程、固定容差与迭代次数；本地缓存记录与重算逐位一致（§7.5）。
- 依赖由 `uv.lock` 锁定；Build Manifest 记录 `uv.lock` 的 sha256、每张数据表的 `id@version` 与 sha256（§11.3）。
- 数据表内容一旦改变就升版本，不就地改写已发布版本。

### 1.3 测试分层（§15）

| 层 | 内容 | 默认是否执行 | 典型耗时 |
|---|---|---|---|
| 单元（§15.1） | 解析与规则、几何单元、Neck Thickness 精度与检出、分块逐位一致；只读 `tests/fixtures/` 的 Shape Bundle，不需要 Blender | 是（`uv run pytest`） | 全部 ≤ 10 分钟 |
| 冒烟（§15.4） | 1 mm 体素的 G1 端到端，只验证流程跑通；用 `tests/fixtures/` 的 Shape Bundle，不调用 Blender | 是 | ≤ 2 分钟 |
| 慢测试（§15.4，`@pytest.mark.slow`） | 需要 Blender 的测试；全分辨率 Print Build；Welt 补丁全量扫描；留出验证 | 否（`-m slow` 显式执行） | 分钟到小时 |
| 黄金回归（§15.3，属 slow） | G1–G5 0.5 mm 整鞋指标与 Adjustment 列表；`heel-transition`、`heel-collar` 0.25 mm Crop Build | 否 | 每例分钟级 |

- `pyproject.toml` 中 pytest 的 `addopts` 默认带 `-m "not slow"`。
- 体量较大的单元测试（Neck 精度基准）在单元层只跑一个代表子集；完整 241 例基准与 Welt 补丁全量放在 slow 层。

### 1.4 Fixture

- **形状 fixture**：`tests/fixtures/shape-*/`，共 6 份（§15.2）：`shape-g1g5-eu42`、`shape-g2-eu35n-h20d5`、`shape-g3-eu48w-h40d12-collarmid`、`shape-g4-eu42-toehigh`、`shape-eu35-default`、`shape-eu48-default`。只由 `scripts/regen_fixtures.py` 从 `examples/specs/*.json` 生成，不手改。比例表或控制笼变化后必须重新生成；过期 fixture 会被 `shape_hash` 检查拒绝。体积与入库方式见 [缺口 G10]。
- **合成 Bundle**：`tests/fixtures/synthetic/` 由代码生成的解析形状（圆角盒 Last 加平板 Sole Envelope，含满足 §5.4、§5.5 约定的 Landmarks 与 Surface Parameterization），供不需要真实鞋形的单元测试使用，使 M4、M5 不必等 M1 完成。
- **Neck 真值**：从 harness 移入 `tests/fixtures/neck_truth/`（`c_of_rho.json` 与 17 份 `truth_*.json`），由 M3.1 的参考方法可复现。
- **Cell Properties**：`gyroid_c.v1` 与 `cellprops.seed.v1` 入库；M3 之前用桩求值器 `tests/fixtures/cellprops_stub.py`（单调的解析函数，只用于测 Resolver 逻辑）。

### 1.5 Blender 测试

- 需要 Blender 的测试都标 `slow`；未安装 Blender、或 `blender --version` 主次版本不是 `BLENDER_REQUIRED = "5.2"` 时自动 skip 并写明原因（本机为 5.2.2）。
- 执行：`scripts/memguard.sh 4 uv run pytest -m slow tests/shape`。
- 单元测试只核对 fixture 自带的 `shape.json`，不调用 Blender（§5.2）。
- Blender 侧脚本 `src/shoegen/shape/blender/build_bundle.py` 只用 Blender 自带 Python 与 `bpy`、标准库，不 import `shoegen`（ADR 0001）；它与引擎之间只通过 `shape_input.json` 和 Bundle 目录交互。

### 1.6 Git 工作流

- 每个里程碑一个分支，从 master 拉出：`p0/m0-scaffold`、`p0/m1-shape-source`、`p0/m2-resolver`、`p0/m3-cellprops`、`p0/m4-geometry`、`p0/m5-validation`、`p0/m6-integration`、`p0/m7-physical`。并行的里程碑各用一个 worktree。
- 每个任务一个或几个提交，前缀沿用现有风格（`feat:`、`test:`、`fix:`、`chore:`、`docs:`、`data:`），提交与 PR 按会话要求附署名行。
- 里程碑完成后开 PR 进 master，PR 描述包含：验收标准逐条的证据（数值、日志路径、memguard 峰值）、评审任务的结论与处理、新增或变动的数据表版本。由用户合并。
- 跨里程碑的依赖以"已合并进 master 的接口"为准；需要提前使用时，从对方分支 cherry-pick 接口提交，不直接依赖未合并分支。
- `.scratch/` 保持 gitignore；原型代码移植进 `src/` 时随提交注明出处（`.scratch/neck-acc/...` 的文件名）。

### 1.7 完成定义（DoD）

**任务**：
1. 代码、测试与必要的数据表在同一分支提交；`uv run pytest` 全绿；任务相关的 slow 测试在 memguard 下全绿。
2. 没有整体浮点栅格；涉及场或网格的改动通过分块逐位一致测试。
3. 术语符合 GLOSSARY（第 1.8 节）。
4. 规格没有说清的地方没有被默默决定：要么引用第 10 节的缺口编号并按用户决定实现，要么在 PR 中新增缺口。标 [实现] 的取值写进代码常量或 Build Manifest。

**里程碑**：验收标准全部达成并在 PR 中附证据；评审任务（`*.R`）的发现已处理或由用户明确接受；PR 已合并。

### 1.8 术语与命名

- 文档与注释使用 GLOSSARY 的术语，遵守各条的 _Avoid_：例如三角网格才叫"网格"，体素栅格叫"栅格"；Relative Density 写 ρ，不单说"密度"；Footbed 不叫鞋垫；Grading 不叫过渡；Welt、Transition 不叫接缝；分块之间的边界叫"块边界"。
- 代码标识符用 GLOSSARY 英文词的 snake_case：`design_spec`、`build_plan`、`shape_bundle`、`neck_thickness`、`pore_throat`、`relative_density`（或 `rho`）、`heel_counter`、`footbed_layer`、`welt`、`rim`、`skin`。

### 1.9 原型复用原则

- 已验证的原型（`.scratch/neck-acc/harness` 的 `dirC_saddle.py`、`dirC_tiled.py`、`truth.py`、`gyroid.py`、`cases.py`、`scoring.py`；`blend/blendlib.py` 的周期孔喉；`crossframe/cflib.py`、`cfrun.py` 的 Welt 补丁、端部受限分类、悬挂材料、困粉）**按算法逐步移植，不重新发明**。移植后的第一个测试必须在原型的同一批用例上复现原型的数值（逐位或在文档化的容差内）。
- 原型中的已知瑕疵按报告修正后再移植：harness 精化步骤在有效站点少于 3 个时误用无效站点（crossframe.md §1 已修正为只用有效站点）。
- 原型自带的场（`cflib.Field`、`tilecases.ShoeSlab`）只用来复现原型数值；正式测试必须驱动 `shoegen` 自己的场实现。

---

## 2. M0 工程骨架与公共基础

- **目标**：仓库可安装、可测试；错误模型、规范化与哈希、数据表登记这三件各阶段共用的东西先落地，使 M1 与 M2 可以并行。
- **范围**：§3.5、§6.3、§11.3（provenance 中数据表部分）、§12.4、§17。
- **交付物**：`pyproject.toml`（uv、Python 3.13；§17 列出的依赖加 numba）、`uv.lock`、`src/shoegen/{spec,shape,zones,lattice,fuse,validate,export}/__init__.py`、`src/shoegen/{pipeline,cli,errors}.py`、`src/shoegen/spec/canonical.py`、`src/shoegen/spec/data/__init__.py`（数据表登记）、`scripts/memguard.sh`、`tests/`、`examples/specs/`。
- **验收标准**：
  1. 在干净 checkout 上 `uv sync && uv run pytest` 通过；`uv run shoegen --help` 输出子命令列表（尚未实现的子命令以退出码 1、class `internal` 报"未实现"）。
  2. 规范化测试：长度量化到 1e-4 mm、比例到 1e-6（银行家舍入，[缺口 G13]）；−0.0 规范为 0.0；`allow_nan=False` 遇 NaN 报错；整数字面量按浮点处理；同一数值经不同运算顺序得到的浮点差异不改变哈希（ADR 0006）。
  3. 错误对象序列化与 §3.5 示例逐字段一致；12.4 的每个退出码都有对应的 class。
- **依赖**：无。
- **风险**：低。numba 的 cp313 macOS arm64 安装包需确认（§17 列表里没有它，见 [缺口 G12]）。
- **规模**：S（3 会话）。

| ID | 任务 | 交付 | 模型·effort | 理由 |
|---|---|---|---|---|
| M0.1 | 仓库骨架 | pyproject、目录、pytest 选项（`slow` 标记、默认排除）、CLI 入口占位、收入 memguard.sh | H·medium | 纯样板，有现成清单 |
| M0.2 | 错误模型与退出码 | `errors.py`：ErrorObject、class 枚举、异常层级到退出码的映射 | S·medium | 规格明确，但要给后续所有阶段定接口 |
| M0.3 | 规范化、哈希与数据表登记 | `spec/canonical.py`、`spec/data/` 登记（`id@version`、sha256）及测试 | S·medium | 浮点 repr 与舍入的边角容易出错，需要细致的测试向量 |

---

## 3. M1 Shape Source

- **目标**：由 `geometry.shape` 经 `blender -b` 生成满足 §5 全部契约的 Shape Bundle；引擎侧能读取、校验并做参数查询；G1–G4 形状尽早交用户目视（§20 最后一行的风险）。
- **范围**：§4（尺码、比例、固定特征、Collar）、§5（全部）、§7.1 第 7 步、§15.1 尺码与比例测试、§15.2、§19 前三行。
- **交付物**：
  - `src/shoegen/spec/data/sizes.v1`、`proportions.v1`（含出处）；`src/shoegen/spec/shape_params.py`（阶段 A 第 7 步）。
  - `src/shoegen/shape/bundle.py`（读写、不变量 1–5、`landmarks.json` 与 `last_param.npz` 的结构校验）、`shape/source.py`（子进程、版本校验、临时目录改名）、`shape/param_query.py`（最近点 → 面号 → 重心插值得 (s, τ, t_mm)，§5.5 查询方法）、`shape/measure.py`（在网格上实测比例量）。
  - `src/shoegen/shape/blender/build_bundle.py`。
  - `scripts/regen_fixtures.py`；`examples/specs/g1.json … g5.json`、`eu35-default.json`、`eu48-default.json`；6 份 `tests/fixtures/shape-*`；`tests/fixtures/synthetic/` 生成器。
- **验收标准**：
  1. §15.1：尺码换算各体系端点值、Mondopoint 与 Toe Allowance 的例外规则（§4.1）、比例表（§4.2，含 Width 档位只平移截距）全部有测试并通过；超出范围报 `E_SPEC_SIZE_OUT_OF_RANGE`。
  2. 6 份 fixture 全部由 `scripts/regen_fixtures.py` 在 memguard 4 GB 下生成；每份都通过不变量 1–5（§5.3）：两份 PLY 不焊接即水密 2-流形；Footbed 在 Sole Envelope 内；Footbed Edge 处侧壁高出 Footbed ≥ `wall_overlap_mm`（6 mm）；`ground` 面法向 n_z < 0；`shape_hash` 一致。
  3. `landmarks.json` 符合 §5.4：闭合环不重复首点、起点在后跟中线、从 +Z 俯视逆时针；`footbed` 面集的边界等于 `footbed_edge`。
  4. `last_param.npz` 符合 §5.5：每行是一条 τ 等值环；`footbed_edge` 行 τ = 0 且 t_mm = 0，`collar_line` 行 τ = 1，与 `landmarks.json` 共用顶点索引；向 Footbed 一侧延伸的测地距离 ≥ 6 mm 且 τ < 0；接缝顶点对的 s 分别为 0 与 1；任意 Last 表面点的参数查询结果在参数面上连续（接缝处取 mod 1）。
  5. `shape/measure.py` 在 6 份 fixture 上实测 Heel Stack（12% 处）、Forefoot Stack（75% 处）、Toe Spring 抬起高度、后跟处 Collar Height、Throat 位置、Ankle Dip 深度，与 `geometry.shape` 的偏差在容差内；Ball Width、Ball Girth、Instep Height、Heel Width、Long Heel Girth 与 Last 目标值的偏差在容差内（容差待定，[缺口 G3]）。同时记录每份 fixture 的 P(0)、P(0.5)、P(1) 与 L(s) 范围。
  6. 同一输入、同一 Blender 完整版本生成两次，Bundle 各文件逐字节一致（ADR 0006 逐字节复现的前提）。
  7. 引擎侧：子进程非零退出 → `E_SHAPE_SOURCE_FAILED`；不变量违例 → `E_SHAPE_INVALID`；主次版本不符 → `E_SHAPE_BLENDER_VERSION`；patch 不同只警告；缺少 `last_param.npz` → `E_SHAPE_PARAM_MISSING`；退出码均为 4。用 fixture 与人为破坏的 Bundle 测试，不调用 Blender。
  8. 单元测试断言每个 G 用例的 `shape_hash` 都能在 `tests/fixtures/` 中找到（§15.2）。
  9. **H1**：用户目视签收 G1–G4（每份 4 个视角的出图与 GLB）。
- **依赖**：M0（规范化与哈希）。
- **风险**：
  - 控制笼生成的 Last 形状质量（§20）：girth 类尺寸只能在细分之后测得，需要"调笼—细分—实测"的迭代拟合，可能收敛慢或不唯一。
  - Collar Line 的绝对高度沿周向从约 45 mm 变到约 150 mm（§5.5），而参数网格的每一行必须是一条 τ 等值环、第 τ = 1 行恰好是 Collar Line：控制笼的环向细分线必须跟随 Collar 形状，不能是水平截面。
  - Sole Envelope 的侧壁悬挑与后跟上翘段没有规定数值，而 Welt 只在悬挑 4 与 6 mm 上验证过 [缺口 G1]。
  - Blender 导出的逐字节确定性（导出器、细分修改器）需要实测。
- **规模**：L（11 会话）。

| ID | 任务 | 交付 | 模型·effort | 理由 |
|---|---|---|---|---|
| M1.1 | 尺码与比例数据 | `sizes.v1`（US/UK 偏移约定与出处）、`proportions.v1`（Ball Girth、Long Heel Girth 的 (a, b) 与出处；脚到 Last 的放余量规则与出处；§19 前三行） | S·high | 文献查证与引用，需要细心但不涉及算法；结果交 H1 复核 |
| M1.2 | 形状参数生成 | `spec/shape_params.py`：尺码 → Foot Length / Last Length → 比例 → Last 尺寸 → `geometry.shape` 与 `shape_hash`；§15.1 尺码、比例测试 | S·medium | 规格给了全部公式 |
| M1.3 | Bundle 契约（引擎侧） | `shape/bundle.py`、`shape/param_query.py`、合成 Bundle 生成器与测试 | S·high | 契约细节多（环方向、接缝、延伸区），但都写在规格里 |
| M1.4 | 子进程与版本校验 | `shape/source.py`：调用命令行（§5.2）、`--version` 解析、`error.json`、临时目录改名、超时处理 | S·medium | 明确的进程管理 |
| M1.5 | 控制笼设计 spike | 在 `.scratch/` 用 Blender 做最小原型：控制笼拓扑（环向边环从 Footbed Edge 到 Collar Line、Footbed 面片、Collar 以上的封闭部分）、尺寸到控制点的映射、girth 拟合策略、Sole Envelope 的构造方式、确定性导出选项；结论写进 `build_bundle.py` 的模块说明与 PR 描述，附 G1 草图出图 | **O·xhigh** | §20 唯一未触碰的风险；拓扑选择决定 τ 行、接缝与 Collar 能否同时满足，属于设计判断 |
| M1.6 | Last 生成 | `build_bundle.py` 的 Last 部分：控制笼 → 细分 → girth 拟合迭代 → 三角化导出（`export_triangulated_mesh=True`，不带 UV） | O·high | 几何拟合与水密性 |
| M1.7 | Sole Envelope 与 `ground` 面集 | Sole Envelope（Heel Stack、Drop、Toe Spring 同时抬起 Last 与 Sole Envelope 底面且趾下厚度不变、侧壁高出 Footbed 6 mm）、`ground` 面集（含 Toe Spring 与后跟上翘段，止于侧壁开始处） | O·high | 几何构造与不变量 2–4 的同时满足 |
| M1.8 | Landmarks 与 Surface Parameterization | `landmarks.json`（点、`ball_line`、`footbed_edge`、`collar_line`、面集）与 `last_param.npz`（τ、s、t_mm、接缝拆分、延伸区）导出 | O·high | 参数化的拓扑与接缝精确性，错一点就会让随形晶格出相位断裂 |
| M1.9 | 形状实测工具 | `shape/measure.py`（引擎侧，不依赖 Blender）及其在 fixture 上的测试 | S·high | 测量定义需要对照 GLOSSARY 与 §4.3 逐条实现 |
| M1.10 | fixture、示例与出图 | `examples/specs/` 七份 DesignSpec、`scripts/regen_fixtures.py`、6 份 fixture、出图脚本（4 视角 PNG）与 GLB、覆盖检查测试、确定性慢测试 | S·medium | 流程性工作 |
| M1.R | 独立评审 | 由未参与 M1 的新 Opus 会话审查：不变量与 §5 契约逐条核对、参数化接缝、确定性、Bundle 大小 | O·high | 控制笼是高风险、难回退的基础件 |

---

## 4. M2 DesignSpec 与 Resolver

- **目标**：完整的阶段 A、阶段 B 与 `--plan` 只校验模式；Override、约束求解、schema 导出、错误格式全部按规格实现。Cell Properties 先用桩求值器，M3 换成正式求值器。
- **范围**：§3、§6、§7（7.5 只做接口，7.6 只留钩子）、§9.1（数据）、§10.1、§15.1 的"解析与规则"部分。
- **交付物**：`spec/designspec.py`、`spec/buildplan.py`、`spec/manifest_model.py`（pydantic v2 严格数据模型）；`spec/migrate.py`；`spec/schema_export.py`；`spec/resolver/{stage_a,overrides,solver,stage_b}.py`；`spec/cellprops_api.py`（`CellQuery`、`CellProps`、`CellPropertiesProvider` 协议，[实现]）；数据表 `tuning.v1`、`archetypes/low_top_slip_on.v1`、`profiles/pbf-tpu-generic.v1`、`palette.v1`；`tests/fixtures/cellprops_stub.py`。
- **验收标准**（全部对应 §15.1"解析与规则"）：
  1. 严格 schema：任何对象出现未知字段都报错；只有 `schema_version` 与 `fit.size` 必填；非 `OnePiece3DPrint × FullLattice` 报 `E_SPEC_UNSUPPORTED_COMBINATION`；Forefoot Stack < 15 mm 报 `E_SPEC_FOREFOOT_STACK`，错误对象带 `json_pointer`、`expected`、`suggestion`（§3.5 示例可原样复现）。
  2. 版本：`[0.1, 0.1]` 区间；更新的版本报 `E_SPEC_VERSION_TOO_NEW`，更旧的报 `E_SPEC_VERSION_UNSUPPORTED`；迁移框架用一个测试专用的假 v0.0 → v0.1 迁移验证"逐级纯函数迁移"。
  3. 四种 schema 导出：`llm` 投影中所有对象 `additionalProperties: false`、去掉 `overrides`/`fit`/`schema_version`、枚举为 `anyOf[{const, description}]`、数值范围写进 `description`；`buildplan` 中白名单叶子带 `x-overridable: true` 与范围；`full` 中 `overrides` 的键用白名单枚举。
  4. Override：白名单 16 条路径逐条测试范围与类型（五个 ρ 字段为 0.12–0.45）；非法键或值报 `E_SPEC_OVERRIDE`；被覆盖的值被钉住；Build Manifest 的 `overridden_semantics` 记录被覆盖的语义。
  5. 约束求解（桩求值器上）：Adjustment、Infeasible（附可行替代方案）、Tier Band 边界（半开区间、最外侧对称半宽、单档 ±0.03）、随形 Zone 放大 Cell 无效时不放大、Midsole ρ 的 Override 使 Footbed Layer 不可行 → `E_INFEASIBLE_OVERRIDE`、`rho_delta` 只在 Midsole ρ 未被钉住时可调、放大 `cell_inplane` 后重算 N；求值次数与查询记录符合 §7.3。
  6. 阶段 B：N = max(1, round(P(0.5)/cell_inplane))（舍入方式见 [缺口 G14]）并记 Resolution Note；构建体积预检 6 种朝向，失败报 `E_BUILD_VOLUME`、退出码 5；`--plan` 路径中 N 不一致报 `E_PLAN_SHAPE_MISMATCH`、约束不满足报 `E_INFEASIBLE`、不产生 Adjustment。用 M1 fixture（或合成 Bundle）提供 P(τ)。
  7. 每条 Adjustment 带路径、原值、新值、原因（`neck`、`pore`、`cell`）。
- **依赖**：M0；M2.8 需要 M1.3 的 Bundle 读取与合成 Bundle。
- **风险**：约束求解的组合边角（钉住的派生量、带两端检查点、放大 Cell 时颈厚与孔喉分别求值、阶段 A 与 B 两次求解的交互）。Tuning Table 自检要等 M3 的正式求值器才能最终通过。
- **规模**：L（10 会话）。

| ID | 任务 | 交付 | 模型·effort | 理由 |
|---|---|---|---|---|
| M2.1 | 数据模型 | DesignSpec 0.1、BuildPlan 0.1、Build Manifest 0.1 的严格数据模型与默认值 | S·medium | 结构在 §3.1、§6.1、§11.3 已给全 |
| M2.2 | 版本迁移与校验错误 | 迁移框架、跨字段校验、尺码范围、错误对象的 `json_pointer`/`suggestion` 生成 | S·medium | 规则明确 |
| M2.3 | schema 导出 | `shoegen schema` 四个 target 的生成逻辑与测试 | S·medium | 规则明确，测试驱动 |
| M2.4 | 数据表 | 按 §7.2、§9.1、§6.2 抄录 `tuning.v1`（名义值与 Tier Band 标 [初值]）、`archetypes/low_top_slip_on.v1`、`profiles/pbf-tpu-generic.v1`（含 `probe_*` 阈值）、`palette.v1`；白名单 ρ 范围为 0.12–0.45（§7.4） | H·medium | 机械抄录；由 M2.9 的测试对照规格表格复核 |
| M2.5 | 语义解析与 Override | 阶段 A 第 1–5 步：规范化、按 Tuning Table 生成草稿 BuildPlan、Resolution Note、Override 应用与钉住 | S·high | 步骤多，钉住语义要贯穿后续步骤 |
| M2.6 | Cell Properties 接口与桩 | `spec/cellprops_api.py`：`CellQuery`、`CellProps`、`CellPropertiesProvider` 协议（`get`、`get_many`）、定义域边界（域外按不可行）；桩求值器 `tests/fixtures/cellprops_stub.py` | S·medium | 接口清楚 |
| M2.7 | 约束求解 | §7.3 全部：检查点（每 5 mm 加两测点）、T 的估算与插值、孔喉级别划分、五步求解顺序、Adjustment 与 Infeasible 信息；经 `get_many` 取值；欧氏 C 闭式放大；随形 `cell_inplane` 按 0.25 mm 线性扫描；ρ 在 0.005 取值格上二分；区间端点夹逼；查询记录 | **O·high** | 边角情况多且彼此耦合，属于"Resolver 约束求解边角"类 |
| M2.8 | 阶段 B 与 `--plan` | 第 8–12 步：N、随形求解、构建体积与资源预检接口（系数由 M6.7 测定）、plan_hash、探测钩子（M6.10 之前为空操作）；`--plan` 只校验模式，判定放宽 τ（§7.5） | S·high | 规格明确，但与 M2.7 的接口要对齐 |
| M2.9 | 规则类测试补全 | §15.1"解析与规则"清单逐条对照补测；数据表对照规格表格的一致性测试（只比对未标 [初值] 的设计约束；[初值] 只在首版数据表上核对一次，之后的标定只升数据表版本，见规格开头"数字的三类标记"） | S·medium | 测试编写 |
| M2.R | 独立评审 | 新 Opus 会话按 §7 逐句对照实现，专找会放行不可行设计或误报 Infeasible 的路径 | O·high | 求解器错误会在 Printability Check 才暴露，代价高 |

---

## 5. M3 Cell Properties 与 `tuning.v1` 定稿

- **目标**：实现按需求值的 Cell Properties 求值器与持久缓存（ADR 0012）：`gyroid_c.v1`、`neck-cell-dirc@1`、`pore-perc@1`、种子；以求值结果定稿 Openness 与 HeelCounter 的 ρ（含 airy 在 Collar 端的复核）；冻结 G2 的期望 Adjustment 与 plan_hash 测试向量。
- **范围**：§7.2（定稿方式、自检）、§7.5、§9.1（`neck_margin_mm`）、§15.1（Tuning Table 自检、Cell Properties、定义域覆盖、plan_hash 测试向量）、§19 第 4–5 行、§20"Openness 定稿值"与"Cell Properties 的精度与成本"两行。
- **交付物**：`src/shoegen/lattice/gyroid.py`（场、梯度、c(ρ)）、`lattice/reference_neck.py`（参考方法，测试基准）、`lattice/cellprops.py`（求值器、键、种子 → 本地缓存 → 计算、`get_many`）、`lattice/pore_perc.py`、`scripts/gen_gyroid_c.py`、`scripts/gen_cellprops_seed.py`、`spec/data/gyroid_c.v1`、`spec/data/cellprops.seed.v1.jsonl`、定稿的 `tuning.v1`（升版本）、`tests/fixtures/neck_truth/`、`tests/spec/test_plan_hash_vectors.py`、`tests/golden/g2_adjustments.json`。
- **验收标准**：
  1. 17 组真值复现：参考方法在 harness 真值上复现原值（差 ≤ 1e-6 mm），例如 C = 10 时 ρ 0.15 / 0.20 / 0.27 的颈厚 2.0886 / 2.4791 / 2.9596 mm，(6, 8, 4) ρ 0.20 的三类 1.1055 / 1.3074 / 1.2181 mm。
  2. 孔喉复现：`pore-perc@1` 在已发表的点上复现 ADR 0010 的数值：C = 10 时 Outsole（ρ 0.35）≥ 5.15 mm、芯部（firm 上沿 0.305）≈ 5.44·C/10 mm（容差由 M3.2 写明）。
  3. `neck-cell-dirc@1` 在 pass 类上的误差 ≤ 0.021 mm，并记录 junction 类与 Direction C 一致；冷查询 ≤ 3 s（M3.3 的 spike 结论）。
  4. 扩展真值达标（M3.4 列出的欧氏与随形真值组），拓扑断言在 ρ = 0.12 与 0.46 两端成立，求值器在端点处恰好找到 12 根 Ligament；定义域覆盖测试（§7.5、§15.1）：用 6 份 fixture 实测的 P(0)、P(0.5)、P(1)，对白名单内 `wall_thickness_mm` × `cell_inplane_mm` 的端点组合，7.1 第 9 步两端的 (a_s, a_t) 都落在定义域内。
  5. 逐位一致与冷热 BuildPlan 相同：缓存值与重算值逐位相同；用一个随形查询全部落在种子之外的设计（例如 G1 加 `cell_inplane_mm` = 9.75 的 Override），清空本地缓存冷跑一次、再热跑一次，BuildPlan 与 plan_hash 相同（G1–G5 的查询全部命中种子，测不到本地缓存）；1 与 4 个 worker 的 `get_many` 结果相同。
  6. 种子守门：抽样重算逐位一致、G1–G5 与 Tuning Table 自检的全部查询命中种子（命中率 100 %）、sha256 与数据表登记一致。
  7. Tuning Table 自检（§7.2）：`pbf-tpu-generic`、heel 30 / drop 8，EU35、EU42、EU48 三个尺码，每个 Zone 每个档位的名义值运行 7.3 均不产生 Adjustment。
  8. airy 在 Collar 端的颈厚 ≥ 1.0 + `neck_margin_mm` = 1.05 mm（benchmark 值 1.106 mm）；若定稿值不满足，按 §20 微调 ρ，并保证 Tier Band 仍以相邻名义值中点为界。
  9. G2 的期望 Adjustment 列表由求值结果生成并入库（单一鞋底 Cell 下鞋底部分预期为空，ADR 0010），G1、G3 为空（§15.3）；plan_hash 测试向量：G1、G2、带 Override 的 G5 的完整 BuildPlan 期望哈希，EU35.5、EU42.5 阶段 A 草稿中 `geometry.fit`、`geometry.shape` 的量化结果与 `shape_hash`（§15.1）。
  10. **H2**：用户批准 `tuning.v1` 定稿值、ρ 定义域与白名单收窄、求值器方法与精度。
- **依赖**：M3.1–M3.5 只依赖 M0；M3.6 起需要 M2.6、M2.7；M3.8 需要 M3.6 与 H1 之后的定稿 `proportions.v1`（否则哈希向量与种子会随比例表变化）；M3.7 需要 M1.10 的 fixture 与 H2 批准后的 `tuning.v1`（种子的随形部分是录制闭包，ρ 一变就要重录，§7.5）；M3.9 需要 M3.7。
- **风险**：
  - 求值器误差或冷查询成本不达标时，余量论证（§9.1）或 Resolver 时长需回到设计讨论（M3.3 先实测）。
  - 孔喉栅格的内存：体素 L_n/100，上限 1500 万体素（受 1.1 节内存规则约束）。
  - ρ 在 0.12 与 0.46 两端的拓扑与真值约定：由 M3.4 解决 [缺口 G7]。
  - 种子录制闭包若覆盖不了全部黄金回归的查询，说明 Resolver 的查询序列依赖了不应依赖的状态，需要修 Resolver。
- **规模**：L（10 会话）。

| ID | 任务 | 交付 | 模型·effort | 理由 |
|---|---|---|---|---|
| M3.1 | 移植 gyroid、c(ρ) 与参考方法 | `lattice/gyroid.py`、`lattice/reference_neck.py`（测试基准）、`scripts/gen_gyroid_c.py` 与 `gyroid_c.v1`、`tests/fixtures/neck_truth/`；复现 17 组真值（≤ 1e-6 mm） | S·high | 移植已验证原型 |
| M3.2 | 周期 Cell 孔喉 | `pore-perc@1`：h_p = L_n/100，≤ 1500 万体素的确定放粗规则，复现 ADR 0010 的孔喉 | O·high | 周期拓扑与内存（自 `blend/blendlib.py` 的 `periodic_components`、`pore_throat`） |
| M3.3 | 求值器方法 spike | 在 17 组真值上实测 `neck-cell-dirc@1` 的误差与耗时（解析路径 + 放宽平面搜索）；junction 类读数与 Direction C 是否一致；L_s 敏感性抽样（只作信息）；冷查询的成本分解；若误差 > 0.025 mm 或耗时 > 3 s，带数据交 H2 | **O·high** | 决定求值器方法与余量论证是否成立 |
| M3.4 | 扩展真值与 ρ 定义域 [缺口 G7] | 补充真值（后台，memguard 6 GB，约 1–2 核·小时）：欧氏 (1, 1) 的 ρ ∈ {0.12, 0.125, 0.38, 0.42, 0.46}；随形取 G1 两端形状与白名单角点形状（Wall 3/6 × `cell_inplane` 6/10）的 ρ ∈ {0.12, 0.175, 0.38, 0.46}；ρ = 0.12 与 0.46 两端的拓扑断言；评分 | O·high | 判断端点的拓扑与真值约定 |
| M3.5 | 求值器与缓存 | `lattice/cellprops.py`：键、种子 → 本地 → 计算、namespace、原子写、损坏重算、`get_many` 的确定并行；逐位一致、冷热 BuildPlan 相同、跨 worker 数一致的测试 | **O·high** | 确定性契约（ADR 0006）是这里的主要风险 |
| M3.6 | 接入约束求解 | 替换桩；Build Manifest 的 `cell_properties`；定义域覆盖测试（fixture 与白名单角点） | S·high | 接口已在 M2.6 定好 |
| M3.7 | 种子生成与守门 | 在 `tuning.v1` 定稿并经 H2 批准之后录制：`scripts/gen_cellprops_seed.py`（录制闭包）、`cellprops.seed.v1`、pre-merge 守门测试（逐位抽样、命中率 100 %、sha） | S·medium | 编排性工作 |
| M3.8 | 定稿 `tuning.v1` | 按求值结果定 Openness 与 HeelCounter 的 ρ；复核 airy 在 G1–G4 Collar 端实际形状上 ≥ 1.05 mm；Tuning Table 自检；数据表升版本 | **O·high** | 设计判断：在余量、Tier Band 中点规则与档位可用性之间取舍 |
| M3.9 | 冻结回归期望 | G2 的 Adjustment 列表、plan_hash 测试向量 | S·medium | 机械地记录求值结果 |
| M3.R | 独立评审 | 新 Opus 会话用 harness 的 `truth.py` 与 `blendlib.py` 走另一条代码路径，抽查 ≥ 20 条求值结果；复核余量论证（§9.1）、冷热与跨 worker 的逐位一致、种子守门 | O·high | 求值器是 Resolver 与校验一致性的基石 |

---

## 6. M4 几何引擎

- **目标**：从 BuildPlan 与 Shape Bundle 得到 Print Mesh 与 Preview Mesh：Shell 与 Zone 推导、单一鞋底 Cell、Welt 处坐标系硬切换、随形场、合成、分块等值面提取、Island 清理、简化、导出。
- **范围**：§8 全部、§11.2、§12.3（Coupon 几何）、§15.1"几何"中除 Neck 与校验外的各项。
- **交付物**：`shape/distance.py`（d_Last、d_G、d_F、最近点、z_W(p)）；`zones/derive.py`、`zones/weights.py`；`lattice/fields.py`（欧氏与随形 Gyroid、归一化）；`fuse/compose.py`（Rim、Skin、Welt、Shoe Body、光滑并与交）；`fuse/extract.py`（分块 marching cubes 与精确合并）；`fuse/islands.py`；`fuse/simplify.py`；`fuse/preview.py`；`export/{stl,glb,atomic}.py`；`fuse/coupon.py`。
- **验收标准**：
  1. 场函数（§15.1）：欧氏 Gyroid 与解析式一致；随形 Gyroid 的 |∇f| 用链式法则逐点求值，与有限差分一致到 1e-6；φ 的归一化与截断（g_min、δ = 1.0 mm）符合 §8.3。
  2. **点求值确定性**：在合成 Bundle 与 G1 fixture 上随机取 10⁵ 个点，按 3 种不同分块（含强制小块）求合成场，结果逐位一致（§15.1 最后一条、§20"分块精确性"）。
  3. Grading 与位置无关：同一 Grading 放在 x = 0 与 x = 280 处，连通分量数与 ρ 一致。
  4. Zone 推导：每点恰属一个 Zone，按 §8.1 表的顺序判定；Welt Surface 两侧的坐标系归属正确（Sole Envelope 高出 Footbed 的侧壁归 Shell 坐标系）；随形坐标在 d_Last > Wall Thickness 处照常延续。
  5. 鞋底侧壁上，距 `ground` 面集边界超过 t_skin 加 1 个体素的部分，Skin 面积为 0。
  6. Island 清理不删除空腔壳：外盒内含反向腔时腔被保留（"无封闭腔"检查在 M5.6 中失败）；只删除最大实体壳之外、V < 10 mm³ 的实体壳，按 Zone 汇总。
  7. 简化后双向采样的实测最大偏差 ≤ `simplify_tolerance_mm`（0.05 mm），超差时自动退回更保守的比例。
  8. 坐标变换与 GLB 变换、左脚镜像（y → −y、翻转绕序、颜色照搬，镜像之后再做 GLB 变换）测试通过；STL 文件头不含时间戳。
  9. G1 的 Preview Mesh（0.5 mm，经临时驱动脚本，M6 再接入 CLI）：≤ `preview_max_triangles`，按 Zone 着色，Welt、Rim、Skin 目视位置正确；用时记录（目标 ≤ 1 分钟，§13，正式验收在 M6.6）。
  10. 同一 BuildPlan 生成两次，STL 逐字节一致（0.5 mm 上验证）。
  11. 内存：G1 在 0.5 mm 下全流程 memguard 峰值记录在 PR 中，且没有整体浮点栅格（评审核对）。
- **依赖**：M1.3（Bundle 读取、参数查询、合成 Bundle），M3.1（`gyroid_c.v1`）；收尾验收需要 M1.10 的 fixture 与 M3.6 的正式求值器。
- **风险**：
  - 整鞋距离查询的耗时：0.25 mm 下需要对数亿点求 d_Last、d_G、d_F 与最近点（trimesh 不可用，§17）。需要按块剔除完全落在 Shoe Body 之外的块（剔除依据只取决于几何，保持确定性）。
  - 随形坐标在面片之间分段线性，梯度在面片边界不连续；只要逐点确定即可，但要确认不会在 φ 零集上产生可见折痕。
  - 网格阶段内存：鞋底大小区域已实测 5.8 GB（§13），整鞋合并后、简化前的三角面数可能逼近 8 GB 预算。必要时把合并结果放在磁盘映射数组上（[实现]）。
- **规模**：L（11 会话）。

| ID | 任务 | 交付 | 模型·effort | 理由 |
|---|---|---|---|---|
| M4.1 | 距离与参数查询 | `shape/distance.py`：libigl 有符号与无符号距离、最近点与面号、(s, τ, t_mm) 批量查询、z_W(p)（`footbed_edge` 上最近点的 z）、块级剔除；逐点确定性测试与吞吐基准 | O·high | 几何内核，性能与确定性并重 |
| M4.2 | Zone 推导 | `zones/derive.py`：Shell 判定、Welt Surface 坐标系切换、§8.1 Zone 表、Midsole 主体厚度 T、Footbed Layer、HeelCounter 切分 | O·high | 几何内核，判定顺序与边界情况多 |
| M4.3 | Grading 与 Transition 权重 | `zones/weights.py`：§8.2 三条带的权重与 ρ(p)、Midsole 纵向 Grading、重叠时的优先级 | S·high | 公式全部在规格中 |
| M4.4 | 场合成 | `lattice/fields.py`、`fuse/compose.py`：欧氏与随形 Gyroid、归一化（c 取自 `gyroid_c.v1`）、Rim / Skin / Welt 实体、Shoe Body、光滑并与交（半径 0.3 mm）；任意点可求值且与分块无关 | **O·xhigh** | 全系统最核心的数值内核，错误会在所有下游放大 |
| M4.5 | 分块等值面提取 | `fuse/extract.py`：固定 128 体素块、共享一层体素、半体素原点偏移、按全局边键精确合并、固定合并顺序、流式内存 | O·high | 块边界精确性与内存 |
| M4.6 | Island 清理 | `fuse/islands.py`：按边连通拆壳、有符号体积、只删小实体壳、按 Zone 汇总 | S·high | 规则明确，需注意大网格性能 |
| M4.7 | Print Mesh 简化 | `fuse/simplify.py`：确定性简化、双向采样偏差、超差回退 | S·high | 第三方库集成加确定性测试 |
| M4.8 | Preview Mesh 与着色 | `fuse/preview.py`：0.5 mm 单独提取、减面、简化后按顶点位置求 Zone 着色 | S·medium | 明确的组合工作 |
| M4.9 | 导出与镜像 | `export/stl.py`、`export/glb.py`、`export/atomic.py`（`.tmp/` 写好后改名） | S·medium | 格式明确 |
| M4.10 | Coupon 几何 | `fuse/coupon.py`：40 × 40 × 20 mm 鞋底切片、四层、独立欧氏坐标系、四侧为裁切面 | S·medium | 复用鞋底场，几何简单 |
| M4.R | 独立评审 | 新 Opus 会话专查：点求值是否真与分块无关、Welt 与坐标系切换是否符合 ADR 0011、有无整体浮点栅格、是否有把位置相关的 Cell 代入坐标（ADR 0002 禁止） | O·xhigh | 内核错误代价最高，且下游校验依赖其逐位确定性 |

---

## 7. M5 Printability Check

- **目标**：§10.2 全部检查；Neck Thickness 用移植的分块 Direction C（与不分块逐位一致）；端部受限判定、实体接触、悬挂材料、困粉；网格类检查；Donning Check。
- **范围**：§10.2、§10.3、§15.1"几何"中 Neck 精度与检出、分块逐位一致、端部受限、Welt 补丁、Island 空腔各项；§20 第 3、5、9 行。
- **交付物**：`validate/neck/{coarse,tiled,fine,attribution,classify}.py`、`validate/hanging.py`、`validate/trapped_powder.py`、`validate/mesh_checks.py`、`validate/donning.py`、`validate/check_grid.py`（分块重采样、跨块并查集）、`validate/window.py`（探测窗口运行器）；`tests/neck/{cases,scoring}.py`（自 harness）、`tests/geometry/test_welt_patch.py`、`tests/geometry/test_end_limited.py`。
- **验收标准**：
  1. **Neck 精度与检出**（§10.2、§15.1）：在 harness 241 例上复现 `dirC_v2` 的逐 Ligament 结果（近阈值 298 根 100% 检出、最大误差 ≤ 0.021 mm、1.0 mm 判定 0/52 假通过）；参考颈厚 ≤ 1.5 mm 的 Ligament 误差 ≤ 0.05 mm、其余 ≤ 0.2 mm；无读数低于 1.05 mm 的虚假输出。覆盖 G1 每个 Zone 的 Cell 形状、两种随形端部形状与鞋底 ρ Grading 带（真值取法：带两端 ρ 的周期 Cell 真值加缓变 ρ 平板，容差 0.05 mm，[缺口 G8] 已解决）。
  2. **分块逐位一致**：分块与不分块的 Ligament 集合与颈厚逐位一致，含跨块边界与强制 40³ 小块；并在 `shoegen` 自己的合成场（G1 的 `heel-transition` 区域）上重做一次（§20"分块精确性"）。
  3. **端部受限判定**：纯 Shell 平板（ρ 0.32 与 0.35）与 Skin 上的 Outsole 晶格，被边界或实体限制的读数归入裁切残边或实体接触，薄壁占比为 0；人工构造的悬挂材料被悬挂材料检查检出（§15.1）。
  4. **Welt 补丁**（§15.1）：平壁 Shell 加鞋底的补丁，扫描 `welt.height_mm` 1.5 与 3.0、悬挑 e 4 与 6 mm、ρ 组合、方位与相位：内部最小颈厚不低于 Shell 自身的值，悬挂材料为 0，上下端材料路径瓶颈不低于纯 Shell，困粉为 0。单元层跑代表子集，slow 层跑全量；场必须是 `shoegen` 的实现。
  5. 困粉：在已知困粉体积的构造体（含"球放得进出不去"的腔与"任何球都进不去"的角隅）上，计算值与解析值一致；Crop 与 Coupon 的裁切面外侧计入外部带 E。
  6. 网格类检查：边流形与方向一致、无退化面、BVH 自相交、实体壳数、空腔壳数、构建体积 6 种朝向；Island 空腔用例中"无封闭腔"失败。
  7. 粗体素降级：`voxel_mm` > 0.25 时体素类检查一律降为 soft、`authoritative: false`（§10.2）。
  8. 性能：在 harness 的合成鞋底平板上，4 worker 时 Neck 全流程 ≤ 1.5 分钟、memguard 峰值 ≤ 2.5 GB（复现 tiling.md 的 1.3 分钟 / 1.95 GB 量级）。
  9. 留出验证（§20）：新的真值组（种子 3–5、C = 12、实际端部比例的随形 Cell）上满足第 1 条的同一标准；不达标则把差距与可能的自适应 σ 改进写进 PR，交用户决定，不擅自改参数。
  10. 探测窗口运行器（§7.6）：在合成场上，窗口核心内的 Ligament 与整块分块计算逐一一致；窗口外表面语义正确（构造的跨窗口依托不被误判为悬挂材料，窗口内的悬挂材料、Island 与封闭腔仍被检出）。
- **依赖**：M5.1、M5.2 只依赖 M0（解析场）；M5.3–M5.5、M5.8 需要 M4.2、M4.4 的点分类与合成场。
- **风险**：
  - σ、persistence 只在一个种子上调定（§20）；胖薄向 Cell 有已知的漏检与虚假输出（均 ≥ 1.2 mm，不影响 1.0 mm 判定）。
  - 端部受限与悬挂材料规则只在后处理里近似实现过；按用户决定，其阈值在 M6.8 用 G1 实测定稿。
  - 悬挂材料与困粉都需要跨块的全局 26 连通，分块形态学的 halo 必须足以保证开运算结果与不分块一致。
  - 探测窗口的外表面语义若错，会开出假通过通道（把真实缺陷判为有依托）。
- **规模**：L（11 会话）。

| ID | 任务 | 交付 | 模型·effort | 理由 |
|---|---|---|---|---|
| M5.1 | 移植 Direction C（不分块）与基准 | `validate/neck/{coarse,fine}.py`（自 `dirC_saddle.py`，含只用有效站点的修正）；`tests/neck/` 基准与评分；复现 `dirC_v2` 结果 | S·high | 移植已验证原型，以逐 Ligament 复现为准 |
| M5.2 | 移植分块粗阶段 | `validate/neck/tiled.py`（自 `dirC_tiled.py`）：固定核心、halo 40 / 回退 64、逐体素精确性证书、按全局盆地对合并、ToMATo、截面检验、细阶段进程池；逐位一致测试 | **O·high** | 分块精确性属"tiling exactness"，必须 Opus |
| M5.3 | 薄壁占比与分类 | `validate/neck/{attribution,classify}.py`：§10.2 第 4、5、7、8 步——站点有效性、端部受限判定（0.1 mm、1.0 mm 两个 [初值] 作为 Profile 或数据表参数，位置见 [缺口 G9]）、裁切残边与实体接触占比、体素归属、前 20 个聚集区 | O·high | 规则新且只近似验证过，判定错误会开出假通过通道 |
| M5.4 | 悬挂材料 | `validate/hanging.py`：直径 = 最小壁厚（默认 1.0 mm）的闭球开运算的分块实现、跨块并查集、与最大实体分量不连通的体积（自 `cfrun.topo`） | O·high | 分块形态学与全局连通的精确性 |
| M5.5 | 困粉 | `validate/trapped_powder.py`：V_2 / V_4 分级、外部带 E、两级孔喉下限 d（取自 Profile，默认 2、4 mm）的开运算与自 E 的 26 连通重建、分块（自 `cfrun.trapped_powder`） | O·high | 同上，且分级依赖 Zone 与权重 |
| M5.6 | 网格类检查 | `validate/mesh_checks.py`：流形、方向、退化面、BVH 自相交（数百万面）、实体壳与空腔壳计数（manifold3d `decompose()`）、构建体积 | S·high | 定义明确，性能需留意 |
| M5.7 | Donning Check | `validate/donning.py`：开口周长 ÷ Long Heel Girth 估值 < 1.00 时 soft 警告（量的取法见 [缺口 G4]） | S·low | 简单比值 |
| M5.8 | 端部受限与 Welt 补丁测试 | `tests/geometry/test_end_limited.py`、`test_welt_patch.py`：用 `cflib.Wall` 的解析几何构造补丁 Bundle，驱动 `shoegen` 的场与校验 | S·high | 移植 harness 的用例设计，断言来自规格 |
| M5.9 | 留出验证 | 生成新的真值组（后台、memguard 6 GB）并评分；写出结论 | O·high | 需要判断差距是方法缺陷还是真值约定差异 |
| M5.10 | 探测窗口运行器 | `validate/window.py`：在一个核心加 halo 的窗口上，复用 M5.2–M5.5 的模块；核心所有权、证书与回退、局部 ToMATo、核心内体素归属；悬挂材料、困粉、Island、封闭腔的外表面语义（§7.6）。合成场上与整块分块计算比较核心内的 Ligament | **O·high** | 属于分块精确性一类，且外表面语义若错会开出假通过通道 |
| M5.R | 独立评审 | 新 Opus 会话以"找假通过通道"为目标审查 M5：漏检的 Ligament 是否会把材料归给邻居、端部受限豁免能否放过真实薄颈、悬挂材料与困粉的块边界、窗口运行器的外表面语义是否会把真实缺陷判为有依托 | O·xhigh | 校验是 P0 安全网，假通过的代价是打废整鞋 |

---

## 8. M6 集成与 G1 端到端验收

- **目标**：CLI 与 Build Manifest 完整；Crop 与 Coupon Build 可用；**G1 在 0.25 mm 下的整鞋 Print Build 通过全部 hard 检查**，并用实测定稿 §10.2 的 [初值] 阈值；G1–G5 黄金回归与确定性测试入库。这是 §1.4 第 1、2 条。
- **范围**：§1.4、§2、§7.6、§10、§11、§12、§13、§14、§15.3、§15.4、§20 第 3、8、9 行（端部受限、局部探测、Welt 处的实体接触）。
- **交付物**：`pipeline.py`（按 (ManufacturingMode, StructureType) 分派、阶段化内存释放、超时）、`cli.py`、`export/manifest.py`、`fuse/crop.py`（Crop Region）、`spec/resolver/probes.py`（探测选取与闸门）、资源预检系数（代码常量）、`tests/golden/{g1..g5}/`（指标与 Adjustment 期望）、`tests/test_smoke.py`、G1 验收报告（PR 描述）。
- **验收标准**：
  1. CLI：§12.1 全部命令；`-o` 非空报错（退出码 2），`--force` 只清理规定的文件；`--errors-json` 把错误数组写到 stderr；12.4 每个退出码至少一个端到端测试。
  2. Build Manifest：开始时写 `running`，结束时原子替换；三种状态都通过 `shoegen schema --target manifest` 的 if/then 校验；`provenance` 含引擎版本、git sha 与 dirty 标记、数据表 `id@version` 与 sha256、`uv.lock` sha256、平台、Blender 完整版本与 build hash、Bundle 文件 sha256 与 `shape.json`、Cell Properties 的方法 id 与本地 namespace（§11.3）；`known_limitations` 固定三条；失败的 Build 不留下任何 STL 或 GLB。
  3. 冒烟测试：G1 1 mm 端到端通过（默认测试集内）。Preview Build（0.5 mm）≤ 1 分钟（§13）。
  4. 资源预检：估算系数由 G1–G4 实测拟合；估算值超过 8 GB 预算时退出码 6；预检对 G1 0.25 mm 放行。
  5. **Crop Build**：`heel-transition` 与 `heel-collar` 在 0.25 mm 下执行完整 hard 校验并通过（§15.3），栅格使用整鞋全局栅格（[缺口 G16] 采纳）；`heel-transition` 中 Welt 处的实体接触与悬挂材料逐项复核（§20 第 9 行）。
  6. **G1 整鞋 Print Build（0.25 mm，右脚）**：
     - 网格类 hard 全过：网格有效、实体壳数 1、空腔壳数 0、构建体积有可行朝向；
     - 体素类 hard 全过：薄壁占比 ≤ 1%、困粉占比 ≤ 1%、悬挂材料 ≤ 阈值；
     - 单只鞋 ≤ 10 分钟、memguard 峰值 ≤ 8 GB（§13）；
     - 裁切残边、实体接触与悬挂材料三项的分布在 PR 中逐项列出（§20 第 3 行"在 G1 整鞋上核对"）。
  7. **阈值定稿**：用 G1 与两个 Crop Build 的实测数据，定稿实体接触距离（1.0 mm）、端部触及边界容差（0.1 mm）、悬挂材料（1 mm³）三个 [初值]，所在数据表升版本 [缺口 G9]。定稿时，把探测窗口内的裁切残边与实体接触读数一并纳入样本；定稿必须同时满足：(a) 在 G1 上不误报；(b) 人工注入的缺陷仍被检出——在 G1 的若干位置把 Ligament 削细到 0.8 mm、制造只靠细特征挂着的材料：削细的 Ligament 必须出现在薄壁 soft 警告的聚集区位置中，削细总量超过 Lattice 材料的 1 % 时判 hard 失败；挂着的材料必须判悬挂材料 hard 失败。若 G1 的失败无法靠调整 [初值] 消除（例如真实缺陷，或规则本身不成立），停下回到设计讨论。**H3**。
  8. 黄金回归（§15.3）：G1–G5 在 0.5 mm 下整鞋几何的第 14 节指标与期望一致、Adjustment 列表逐条一致（G1、G3 为空）；不比对哈希。
  9. 确定性：G1 0.25 mm 连续两次 Print Build 的 STL 逐字节一致；左右脚同时成功。
  10. Coupon Build：soft、balanced、firm 三块全部通过 hard 检查，Build Manifest 记录各层 ρ、Cell 与试块包含的 Cell 数。
  11. 局部探测：G1 的局部探测全部通过（`gate`），且 G1 整鞋的 Print Build 未出现探测窗口内本应检出的失败；探测耗时 ≤ 60 s、memguard 峰值 ≤ 2 GB（M6.11）。
- **依赖**：M2、M3、M4、M5 全部合并；M6.10 需要 M2.8、M5.10；M6.11 需要 M6.10 与 M6.8 的 Crop Build。
- **风险**：
  - G1 可能因端部受限阈值或真实缺陷而 hard 失败；用户已决定由 G1 驱动阈值定稿，迭代可能需要回到 M4 或 M5 修复（预留 2–3 个修复会话）。
  - 10 分钟与 8 GB 预算：距离查询、网格合并与细阶段是三大开销；Neck 细阶段需 4 worker（§13）。
  - 先跑 Crop Build 再跑整鞋：`heel-transition` 是最小、最早能暴露 Welt 与端部受限问题的整合用例。
- **规模**：L（12 会话 + 修复迭代）。

| ID | 任务 | 交付 | 模型·effort | 理由 |
|---|---|---|---|---|
| M6.1 | 流水线 | `pipeline.py`：阶段串联、网格阶段与校验阶段不同时驻留、超时写 failed Build Manifest（退出码 7）、峰值内存与阶段耗时计量 | S·high | 组装已有模块，内存时序要严谨 |
| M6.2 | Build Manifest | `export/manifest.py` 与 schema；原子替换；`running` 遗留按 failed 处理的读取方工具函数 | S·medium | 字段表已给全 |
| M6.3 | CLI | `cli.py`：§12.1 命令、输出目录规则、收尾顺序 | S·medium | 明确 |
| M6.4 | Crop Build | `fuse/crop.py`：三个 Crop Region 的定义、使用整鞋全局栅格（同一原点与偏移）、裁切面作为 Shoe Body 边界参与困粉与裁切残边 | S·high | 区域定义依赖 Landmark 与 Welt 位置，需仔细 |
| M6.5 | Coupon Build | 不经 Shape Source 与阶段 B；按试块中心竖线做欧氏约束求解；预检用固定尺寸 | S·medium | 明确 |
| M6.6 | 冒烟与 Preview 验收 | 1 mm 冒烟测试；G1 Preview ≤ 1 分钟的计时测试 | S·low | 简单 |
| M6.7 | 资源预检与性能 | 在 G1–G4 上测各阶段峰值内存与耗时，拟合估算系数；整理 §13 达标证据 | S·high | 需要严谨的测量与 memguard 纪律 |
| M6.8 | G1 验收与阈值定稿 | 验收顺序：先局部探测（M6.10），再 `heel-transition`，再整鞋；依次执行 `heel-transition`、`heel-collar`、`sole-section`、G1 整鞋；逐项判读失败原因；注入缺陷实验；提出阈值定稿值；交 H3 | **O·xhigh** | 区分"真实缺陷"与"判据误报"是本项目最关键的判断 |
| M6.9 | 黄金回归与确定性 | `tests/golden/`：G1–G5 0.5 mm 指标与 Adjustment、两个 0.25 mm Crop Build、逐字节确定性测试 | S·medium | 记录与比对 |
| M6.10 | 探测选取与阶段 B 接入 | `spec/resolver/probes.py`：Archetype 数据中的 `probes` 段、由 Landmark 推出的中心、数据驱动的两项（曲率、参数化畸变）、去重、对齐全局栅格；闸门判定、`E_PROBE_*`、`suggestion`；`--probes`；Manifest 的 `probes`；可选的结果缓存 | S·high | 规则明确，需细心处理边界情况 |
| M6.11 | 探测标定与验收 | 在 G1–G4（0.25 mm）上运行：耗时 ≤ 60 s、memguard 峰值 ≤ 2 GB；窗口读数与整鞋 Print Build 同位置读数比较（逐 Ligament）；W-heel 与 `heel-transition` 的 Crop Build 互相核对；在窗口内注入超过探测阈值的缺陷，必须被 `E_PROBE_*` 提前检出（低于阈值的必须出现在 soft 警告中）；在窗口外注入缺陷，用来记录残余风险；提出探测集 [初值] 定稿值，交 H3 | **O·high** | 判断探测是否值得它的时间，以及漏掉了什么 |
| M6.R | P0 完成度审计 | 新 Opus 会话对照 §1.4、§15 全部条目逐条核对证据，列出未覆盖项 | O·xhigh | 合并前最后一道独立把关 |

---

## 9. M7 实物验证（§16）

- **目标**：完成 §16 的四步，使 §1.4 第 3 条成立。
- **范围**：§9.2、§16、§19 最后一行、§20 第 4 行（Welt 力学与外观）。
- **交付物**：`profiles/<服务商>.v1`；裁切件与 Coupon 的打印包（STL、Build Manifest）；按试验结果修订并升版本的数据表；整鞋 Print Build 的产出与 Build Manifest。
- **验收标准**：
  1. 服务商 Profile 对照其设计指南逐项复核最小壁厚、孔喉与构建体积，差异写入 PR（§16 第 1 步）。
  2. `sole-section`、`heel-collar` 两个 Crop Build 与三块 Coupon 在服务商 Profile 下全部通过 hard 检查（§16 第 2 步）。
  3. 试验结果（排粉、试穿、压缩）以结构化记录入库；据此修订的数据表（Donning 阈值、Collar 形状参数、Tuning Table 等）全部升版本；若形状参数或 `tuning.v1` 变化，则重新生成 fixture、重新录制 Cell Properties 种子（§7.5）并重跑黄金回归（§16 第 3 步）。
  4. 整鞋 Print Build：所有 hard 检查通过，薄壁占比与困粉占比 ≤ 1%，soft 警告逐条复核并记入 Build Manifest（§16 第 4 步）。
  5. 实物打印完成（H7）。
- **依赖**：M6；H4（服务商选定与设计指南）。
- **风险**：服务商的孔喉口径若接近部分 SLS 指南的"≥ 5 mm"，Shell 将不可行（§9.2；单一鞋底 Cell 下 Footbed Layer 与 Outsole 不受影响），因此优先 MJF；试验周期由外部决定。Coupon 与 Crop Build 如何选用服务商 Profile 尚无入口 [缺口 G11]。
- **规模**：M（5 会话 + 外部周期）。

| ID | 任务 | 交付 | 模型·effort | 理由 |
|---|---|---|---|---|
| M7.1 | 服务商 Profile | 新 Profile 数据文件与复核记录 | S·high | 逐条阅读设计指南并映射到规格量，要细心 |
| M7.2 | 打印包 | 裁切件与 Coupon 的 Build、打包与清单 | S·low | 执行已有命令 |
| M7.3 | 修订数据表 | 解读试验结果、提出修订值、升版本、必要时重生成 fixture、Cell Properties 种子与黄金回归 | O·high | 从物理结果反推参数需要判断 |
| M7.4 | 整鞋 Print Build | 服务商 Profile 下的整鞋 Build、soft 警告复核清单 | S·medium | 执行与整理 |
| M7.R | 收尾审计 | 新 Opus 会话核对 §16 四步与 §1.4 三条的证据 | O·high | 收尾把关 |

---

## 10. 规格缺口

以下各项规格未写清，实现前需要用户决定（或明确授权按建议处理）。括号中是受影响的任务。

| 编号 | 缺口 | 影响 | 建议的决定方式 |
|---|---|---|---|
| G1 | **Sole Envelope 的侧壁悬挑与后跟上翘段没有数值**。§5.3 只规定侧壁高出 Footbed 6 mm；Sole Envelope 外沿相对 Last（或 Shell 外表面）的水平外扩 e、后跟上翘段的长度与角度都没有给出。ADR 0011 的 Welt 只在 e = 4 与 6 mm 上验证过。 | M1.5、M1.7、M5.8 | 在 Archetype 数据文件中定为 [初值]，并要求 e ∈ [4, 6] mm，除非先在 Welt 补丁 harness 中验证新值 |
| G2 | **Last 的若干固定形状没有定量描述**：Footbed 标准楦底曲面（后跟凹窝、足弓）的形状与随尺码的缩放方式；Collar Line 以上 Last 封闭部分的高度与形状（它影响 Long Heel Girth 的测量）。 | M1.5、M1.6 | 由 M1.5 提出方案，H1 目视签收后写入 Archetype 数据文件 |
| G3 | **"比对比例表"没有容差，也没有测量定义**：§20 要求 G1–G4 形状比对比例表，但 Ball Girth、Long Heel Girth 等在 Last 上按什么截面测量、允许多大偏差都未规定。 | M1.6、M1.9 验收第 5 条 | 由 M1.1 依据文献给出测量定义；容差建议长度 ±1 mm、围长 ±2 mm 作为 [初值]，由用户确认 |
| G4 | **Donning Check 的两个量不明确**：§10.3 的"Collar 开口周长"是 P(1)（Last 上 Collar Line 的长度）还是 Shell 外表面或 Rim 内缘的周长；"Long Heel Girth 估值"取自 `geometry.shape.last.long_heel_girth_mm` 还是在 Last 网格上实测。 | M5.7 | 建议分别取 P(1) 与 `geometry.shape` 中的值，待用户确认 |
| G5 | **（已解决）Feasibility Table 的生成成本与"颈厚与 L_s 无关"的适用范围。** 原问题：按全取值集合离线生成约需 45 核·天，且依赖只在 L_s ∈ {6, 8, 11} 上验证过的假设。 | M3.3–M3.5、H2 | 已由 ADR 0012 与规格 §7.5–§7.6 解决：不再有离线全表，改为按需求值的 Cell Properties，L_s 无关不再是前提；求值器精度与成本由 M3.3 实测 |
| G6 | **（已解决）Feasibility Table 的插值方式未规定。** 原问题：在 (L_s/L_n, L_t/L_n, ρ) 上用什么插值。 | M2.6、M3.6 | 已由 ADR 0012 解决：颈厚与孔喉不插值，在实际查询点上求值；`gyroid_c.v1` 在 0.001 步长的相邻表项间线性插值（规格 §7.5） |
| G7 | **（已解决）ρ 取值区间的两端没有验证。** 原问题：Override 范围 0.05–0.60，颈厚真值只覆盖 ρ 0.15–0.35，ρ 接近 0.5 时拓扑存疑。 | M3.1、M3.4、M2.4 白名单 | 已解决：定义域 ρ ∈ [0.12, 0.46]，白名单五个 ρ 字段收窄为 0.12–0.45（规格 §7.4–§7.5，ADR 0012）；M3.4 补真值并断言两端拓扑 |
| G8 | **（已解决）ρ Grading 带的颈厚真值无从取得。** 原问题：参考真值定义在周期 Cell 上，而 ρ 随位置变化的带不是周期结构。 | M5 验收第 1 条 | 已解决（规格 §10.2、§7.6）：精度验证用带两端 ρ 的周期 Cell 真值加缓变 ρ 平板（容差 0.05 mm）；非周期区域的可打印性改由局部探测检查 |
| G9 | **§10.2 新规则阈值与其他几何常量存放在哪里未定**：实体接触距离 1.0 mm、端部触及边界容差 0.1 mm、悬挂材料 1 mm³、σ = 0.5 mm、Island 10 mm³、光滑半径 0.3 mm。ADR 0006 要求它们经数据表版本与哈希追溯，但没有指定哪张表；§19 说 [初值] 由 §16 的实物结果标定，而本轮已决定前三项由 G1 实测定稿。 | M5.3、M6.8 | 建议：校验阈值放入 ManufacturingProfile（`pbf-tpu-generic.v1`），几何常量放入一张新的引擎常量数据表；并在规格中注明前三项由 G1 定稿、其余仍由 §16 标定；**部分解决**：探测的局部阈值（`probe_thin_fraction`、`probe_trapped_fraction`、`probe_hanging_mm3`）已放入 ManufacturingProfile（规格 §9.1），其余常量的存放位置仍待定 |
| G10 | **fixture 的体积与入库方式未定**：6 份 Shape Bundle 是二进制 PLY 与 npz，细分后可能每份数 MB 到数十 MB；Neck 真值与 Cell Properties 种子也是较大的数据文件。是否用 Git LFS 未定。 | M1.10、M3.7 | M1.10 实测体积后由用户决定 |
| G11 | **Coupon Build 无法选择 ManufacturingProfile**：`shoegen coupon cushioning -o <dir>` 没有 Profile 参数，而 §16 要求用服务商专属 Profile 打印 Coupon；另外 DesignSpec 的 `manufacturing_profile` 若以枚举约束，新增服务商 Profile 是否需要升 DesignSpec minor 版本也未说明。 | M6.5、M7.1、M7.2 | 建议给 `coupon` 加 `--profile`，并明确 Profile 的取值不属于 DesignSpec 默认值契约（不升版本），待用户确认 |
| G12 | **依赖清单缺 numba**：§17 的参考依赖不含 numba，而已验证的 Direction C 原型依赖它。规格允许替换，但 numba 的确定性要求（禁用 parallel 与 fastmath）应写明。 | M0.1、M5 | 建议把 numba 加入 §17 列表并注明确定性约束 |
| G13 | **量化的舍入对象不明确**：§6.3 要求"银行家舍入"到 1e-4 mm / 1e-6，但没说是对二进制浮点的精确值舍入（Python `round(x, n)` 的语义），还是对十进制字面量舍入。两者在少数值上结果不同，会改变 plan_hash 测试向量。 | M0.3、M3.9 | 建议以 Python `round(x, n)` 为准，写入规格 |
| G14 | **N 的取整方式未规定**：§7.1 第 8 步 N = max(1, round(P(0.5)/cell_inplane)) 中 `round` 是四舍五入还是银行家舍入，在恰为 .5 时结果不同。 | M2.8 | 建议与 G13 一致，用 Python `round` |
| G15 | **部分错误码没有命名**：§12.4 规定了退出码 1、5、6、7 的类别，但除 `E_BUILD_VOLUME` 外，hard 检查失败（薄壁、困粉、悬挂材料、网格无效、连通分量、封闭腔）、资源预检与超时的 `code` 都没有给出（内部错误已在规格 §7.6 用 `E_INTERNAL`）；`E_SHAPE_PARAM_MISSING` 的退出码也未明写（按 class shape 推断为 4）。 | M0.2、M5、M6 | 建议在 M0.2 提出一组名称（如 `E_PRINT_THIN_WALL`、`E_PRINT_TRAPPED_POWDER`、`E_PRINT_HANGING_MATERIAL`、`E_PRINT_MESH_INVALID`、`E_PRINT_COMPONENTS`、`E_PRINT_CAVITY`、`E_RESOURCE_BUDGET`、`E_TIMEOUT`、`E_INTERNAL`），由用户确认后写入规格；**已扩展**：加入局部探测的五个错误码 `E_PROBE_THIN_WALL`、`E_PROBE_TRAPPED_POWDER`、`E_PROBE_HANGING_MATERIAL`、`E_PROBE_ISLAND`、`E_PROBE_CAVITY`（规格 §12.4） |
| G16 | **Crop Build 的体素栅格是否与整鞋对齐未写明**：§12.2 只说 Crop Build 与整鞋共用 BuildPlan。若裁切件的栅格原点与整鞋不同，`heel-transition` 的 0.25 mm 黄金回归与整鞋在同一位置的结果就不可比。 | M6.4 | **已采纳**：Crop Build 与探测窗口都使用整鞋全局栅格（规格 §12.2） |
| G17 | **Crop Build 的"实体壳数为 1"hard 检查会看到被裁切盒切下的 Lattice 碎块。** 被裁切面切下、只在 Crop Region 之外才与主体相连的 Lattice 碎块，在 Crop Build 中是独立的壳；不足 10 mm³ 的由 §8.5 Island 清理删除，> 10 mm³ 的会幸存并使该检查失败。局部探测因此采用"窗口外表面有依托"的语义，Crop Build 目前没有同样的规则。Coupon Build 的四个侧面也是裁切面（§12.3），同样会遇到，而 M6 验收第 10 条与 M7 验收第 2 条要求三块 Coupon 通过 hard 检查。 | M6.4、M6.5、M6.8 | 在 M6 做 Crop Build 之前定一条规则，例如与探测一致：触及裁切盒的碎块按有依托或外部处理，不计入实体壳数与 Island 判据；待用户确认 |
| G18 | **Midsole 主体厚度 T 有两个定义**：§7.3 在阶段 A 沿中线按 Heel/Forefoot Stack 线性估算，§8.1 按 (x, y) 竖直实测 z_F − z_G。单一鞋底 Cell 后 T 只用来判定 T < C 的薄层孔喉级别，两者在足弓、后跟凹窝、偏离中线处与 Toe Spring 段可能给出不同级别，Resolver 与困粉检查对同一点用的孔喉下限就不一致。 | M2.7、M5.5 | 建议把欧氏约束求解移到阶段 B、统一用 §8.1 的 T，或干脆按 Zone 定级、不再用 T；待用户确认 |
| G19 | **Cell Properties 的求值尺度未定**：结果按 L_n 归一化，键不含 L_n（欧氏形状恒为 (1, 1)），但颈厚方法的常数（hs 0.07–0.16 mm、δ = 1.0 mm、精度 0.021 mm）是绝对毫米。按查询的实际 L_n 求值，同一 key 的值会随先填入的 C 而不同，违背"求值器是 key 的纯函数"；按固定参考尺度求值，毫米误差又随 L_n/L_ref 缩放，§9.1 的余量账要按白名单最大 L_n 重算。 | M3.3、M3.5 | 建议固定参考尺度（如 L_ref = 10 mm）并写进方法 id，§9.1 按最大 L_n 核对误差；待用户确认 |
| G20 | **局部脱开的接缝没有生产期检测**：ADR 0011 的接缝脱开（某段 Welt 上下没有材料通路，但两侧在别处相连，全局分量数仍为 1）不会被悬挂材料检查发现，探测的 W 窗口对外表面又取乐观处理。目前只有 Welt 补丁单测的"材料路径瓶颈"与后跟处的 `heel-transition` Crop Build 能看到。 | M5.4、M5.8、M6.8 | 建议沿 `footbed_edge` 分段做跨 Welt 的材料路径瓶颈检查（hard），至少在 W-* 探测窗口内做；待用户确认 |
| G21 | **粗体素 Print Build 没有权威的体素类闸门**：`voxel_mm` > 0.25 时整鞋体素类检查降为 soft（§10.2），只剩局部探测是权威的；再加 `--probes warn` 或 `off`，Build 照常写出 STL 并以退出码 0 结束，只在 Build Manifest 标 `authoritative: false`。 | M6.1、M6.3 | 建议 `voxel_mm` > 0.25 的 Print Build 不允许 `--probes warn/off`，或不写出 STL、以非零退出码结束；待用户确认 |
| G22 | **悬挂材料的开运算可能误伤合法颈**：§10.2 在 0.25 mm 栅格上用直径 = 最小壁厚（1.0 mm）的闭球开运算，33 体素的模板只有在颈厚 1.05–1.3 mm 的合法 Ligament 轴线接近体素中心线时才放得进。评审期的纯 Python 模拟里，与栅格对齐的 airy (6, 8, 4) ρ 0.20 Shell 出现过 12–24 mm³ 的内部断块；ρ ≥ 0.25 或 0.125 mm 栅格下没有。ADR 0011 的 harness 未见此现象。 | M5.4、M6.8 | 先用 `.scratch/neck-acc/crossframe/` 的 harness 在 memguard 下复现；若复现，把球径改为 d − 2h 或改用亚体素判定；待用户确认 |
