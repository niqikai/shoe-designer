# 实现计划

本文把 [architecture.md](architecture.md) 与 [ADR 0001–0005](adr/) 拆成里程碑和单会话任务。M0 写到任务级，M1–M3 只写到目标和验收，等 M0 合并后再细化。

**记号**
- 模型：**O** = Opus（`claude-opus-5-5`），**S** = Sonnet（`claude-sonnet-5-5`），**H** = Haiku（`claude-haiku-5-5`）。
- 推理强度：low / medium / high / xhigh。
- 规模按 agent 会话数估：S ≤ 3，M 4–8，L ≥ 9。
- **H0–H4** 是人工检查点（第 4 节）。

**选模型的原则**
- **O·high 及以上**：要做设计取舍、数值正确性难以靠测试兜底的任务（GeometryGraph、跳块判据、焊接、鞋体装配），以及每个里程碑的评审任务。
- **S·high**：接口已定、但有几何细节、数值精度和边界情况的实现任务（如鞋楦采样场、晶格节点）。
- **S·medium**：照着现成代码移植、写 CLI 和报告这类套路化工作。
- **H·low/medium**：脚手架、配置文件、格式化这类纯机械工作。
- 任务卡住或评审发现正确性问题时，升一级重做，不在原模型上反复重试。

---

## 0. 总览

| 里程碑 | 目标 | 任务（模型·强度） | 依赖 | 规模 |
|---|---|---|---|---|
| **M0** 端到端骨架 | 一条命令：鞋楦 → 鞋底均匀杆状晶格 + 实体鞋面薄壳 → 水密 STL + 报告 | M0.1 H·medium；M0.2 S·medium；M0.3 S·high；M0.4 **O·high**；M0.5 **O·high**；M0.6 **O·xhigh**；M0.7 S·high；M0.8 **O·high**；M0.9 S·medium；M0.10 S·medium；M0.R **O·high** | H0 | L（11） |
| **M1** Coupon 与选工艺 | 输出参数网格化的试块；两种工艺各打一批；压缩测试选工艺 | 待细化（预计 S 为主，标定分析 O·high） | M0；H2 | M |
| **M2** 真正的鞋 | 杆径渐变、随形鞋面晶格、外底实体、边界节点不留断头、3MF | 待细化（随形布点与边界处理 O·xhigh） | M1；H3 | L |
| **M3** 语义层 | DesignSpec → Resolver → GeometryGraph；尺码变形；确定性回归 | 待细化 | M2 | L |

```
H0 ─► M0.1 ─┬─► M0.2 ─► M0.3 ──────────────────┐
            └─► M0.4 ─┬─► M0.5 ─► M0.6 ─────────┼─► M0.8 ─► M0.9 ─► M0.10 ─► M0.R ─► H2 ─► M1
                      └─► M0.7（读 M0.1 的 profile）┘
                     H1（放置鞋楦文件）在 M0.10 之前完成即可
```

- 关键路径：M0.1 → M0.4 → M0.5 → M0.6 → M0.8 → M0.9 → M0.10。
- M0.2/M0.3 与 M0.4–M0.6 可由两个 worktree 并行；M0.7 在 M0.4 合入后可与 M0.5/M0.6 并行。M0.8 需要 M0.3、M0.6、M0.7 都完成。
- **同一时刻只跑一个重计算任务**（第 1.1 节）。

---

## 1. 全局工程规则

### 1.1 内存（16 GB 开发机）
- 凡是会求值超过约 1000 万体素、或做整鞋构建的命令，都在 CLI 的 RSS 看门狗下运行（M0.1 实现，默认 6 GB）。
- 任何阶段都不分配覆盖整鞋包围盒的稠密浮点数组（ADR 0005）；代码评审把这一条当硬性检查项。
- 进程池默认 4 个 worker，不按可用内存自适应。
- 多个 agent 共用一台机器：重计算前用 `mkdir .scratch/heavy.lock` 取互斥锁，结束后删除。

### 1.2 确定性（ADR 0005）
- 块大小 64³、体素原点、遍历与合并顺序固定；numba 禁用 `fastmath`、`parallel=True`。
- `shoe.stl`、`graph.json` 不含时间戳；JSON 键排序；没有随机数。
- 改动求值代码的提交，必须通过"同一点在不同分块下逐位一致"和"跳块与不跳块网格相同"两个测试。
- 确定性只承诺在同一台机器、同一份 `uv.lock` 下，且只针对 `shoe.stl` 与 `graph.json`。

### 1.3 测试分层
| 层 | 内容 | 默认执行 | 耗时 |
|---|---|---|---|
| 单元 | 节点 SDF、Lipschitz、哈希、分块一致性、焊接水密、杆径检查；只用合成鞋楦 | 是 | 全部 ≤ 3 分钟 |
| 冒烟 | 合成鞋楦 1 mm 体素端到端（杆半径 2 mm） | 是 | ≤ 1 分钟 |
| 慢测试 `@pytest.mark.slow` | 0.5 mm 端到端 | 否 | 分钟级 |
| 真实鞋楦 `@pytest.mark.real_last` | 需要本地授权鞋楦文件；文件不存在就 skip | 否 | 分钟级 |

### 1.4 资产与授权
- 鞋楦文件放在 `input/lasts/`（gitignore），派生几何放在 `output/`（gitignore）。都不入库、不上传。
- 测试只用 `tests/fixtures/` 下由代码生成的解析合成鞋楦。

### 1.5 Git
- 里程碑分支：`m0/skeleton`、`m1/coupons`……，从 master 拉出；并行任务各用一个 worktree。
- 提交前缀：`feat:`、`test:`、`fix:`、`chore:`、`docs:`。
- 里程碑完成后开 PR，描述里逐条列出验收证据（数值、耗时、RSS 峰值）和评审结论。由用户合并。

---

## 2. M0：端到端骨架

**范围**：鞋底是一块平底包络，顶面跟随鞋楦底面，里面填均匀 BCC 杆状晶格，外面一层实体皮，贴着鞋楦一层足床板；鞋面是贴着鞋楦向外的 2 mm 实体薄壳，领口用一个高度平面切开。随形晶格、杆径渐变、外底花纹都不在 M0。

### M0.1 工程脚手架与 Profile 数据 — H·medium
- `pyproject.toml`（Python 3.11+，uv 管理）、`uv.lock`、包 `src/shoegen/`、pytest 配置（默认 `-m "not slow and not real_last"`）、ruff、`.gitignore`。
- 依赖：numpy、scipy、numba、trimesh、manifold3d、libigl、pydantic、psutil、build123d（只用于 STEP 导入）。
- `src/shoegen/profile.py`：ManufacturingProfile 的 pydantic 模型（字段见 ADR 0004）；`profiles/pbf-tpu-generic@0.json`、`profiles/dls-epu-generic@0.json`，`status = placeholder`，每个数值附"待核实"注释和留空的来源字段。放在 M0.1，是因为 M0.6、M0.7 都要读它。
- `src/shoegen/resources.py`：RSS 看门狗。后台线程每 0.5 s 用 psutil 统计主进程及全部子进程的 RSS 总和，超限时写失败报告并 `os._exit(6)`。
- **验收**：`uv sync && uv run pytest` 通过；看门狗测试：子进程分配超过上限的数组时，父进程以退出码 6 结束并留下失败报告。

### M0.2 移植鞋楦导入与报告工具 — S·medium
- 从 `codex/3DPrintedFootwear` 移植 `last/loader.py`、`last/normalizer.py`、`reporting.py`，包名改为 `shoegen`；保留 `FILE_NAME` 头修复、显式刚体归一化、`license_status` 字段。去掉对 PR #2 其他模块的依赖。提交说明注明出处提交号。
- **验收**：测试中用 build123d 生成一个圆角盒并导出 STEP；导入后用一个非平凡的旋转加平移归一化；把原形状三角化后的顶点直接乘同一个变换，与归一化后形状三角化的顶点比较，最大误差 ≤ 1e-4 mm。不用包围盒比较（OCCT 的包围盒含公差扩张，旋转后也不再对应）。

### M0.3 鞋楦 → 带符号距离采样场 — S·high
- 用 build123d 三角化（弦差 0.01 mm）；按面输出的顶点先合并，再检查水密与单连通；用 libigl `signed_distance`（winding number 定号）。
- 双层采样，见 ADR 0003：0.5 mm 细栅格只存 |d| ≤ 6 mm 的窄带；2 mm 粗栅格覆盖包围盒外扩 30 mm；更远处用到鞋楦包围盒的距离。
- 同时导出两份二维数据，供 M0.6 使用：足底轮廓（鞋楦在 XY 平面的投影）和底面高度图 `h(x, y)`（每个 XY 位置上鞋楦的最低 z），都用 0.5 mm 栅格，由三角网格竖直射线求交得到。
- 存为 `.npy`，键为鞋楦 sha256 + 采样参数；worker 用 `mmap_mode='r'` 打开。
- **验收**（在解析合成鞋楦上，真值可算）：
  - 细栅格窄带内误差 ≤ 0.05 mm；粗栅格区域误差 ≤ 1 mm；更远处的值不大于真值；
  - 细栅格窄带内数值 Lipschitz ≤ 1.05；
  - 高度图误差 ≤ 0.05 mm（坡度 < 60° 处）。

### M0.4 GeometryGraph 核心 — O·high
- 节点白名单：`Box`、`RoundedBox`、`Capsule`、`HalfSpace`、`SampledField`（鞋楦）、`Extrude2D`（二维 SDF 沿 Z 拉伸并截取 z 区间）、`BelowHeightField`（`(z − h(x,y)) / √(1+G²)`，G 是高度图的最大坡度）、`Union`、`Intersect`、`Subtract`、`SmoothUnion(k)`、`Offset(d)`、`ShellOutward(t)`（`max(d − t, −d)`，即向外长出厚度 t 的壳）、`StrutLattice`（M0.7 填实现）。
- pydantic 模型 + 规范化 JSON（键排序、浮点用 `repr` 精确往返）→ `graph_hash`；采样场以内容哈希（源文件 sha256、采样参数、数组 sha256）计入（ADR 0001）；`graph_version = 1`。
- 每种节点只有一条 numba 内核；批量求值只是循环调用它（ADR 0005）。
- 构建时检查：图里对 `SampledField` 的偏置量 ≤ 窄带宽度 − 1 mm（ADR 0003）。
- **验收**：
  - 每种解析节点都有真值测试；
  - Lipschitz 测试：随机点对上 |Δd| / |Δx| ≤ 1 + 1e-9（`SampledField` 按 M0.3 的标准）；
  - 同一组点按不同批大小、不同顺序求值，结果逐位一致；
  - 序列化往返后哈希不变；改任一参数哈希改变。

### M0.5 块稀疏求值与跳块 — O·high
- 全局栅格：原点 = 包围盒最小角向下取整到体素整数倍；坐标一律由全局整数索引算出（`origin + i × h`）；块 64³，按 (i, j, k) 字典序编号。
- 跳块判据按 ADR 0005（k = 1.5）。只对跨表面块细采样，每块实际采样 65³（含与相邻块共享的边界面）。
- 进程池 4 worker，按块序号收集结果。
- **验收**：
  - 在所有被细采样的块上，跳块与不跳块的体素值逐位一致；所有被跳过的块用全量采样验证不含零交叉；
  - worker 数 1 和 4 的结果逐位一致；
  - 报告块总数、细采样块数、峰值 RSS。

### M0.6 分块网格提取与焊接 — O·xhigh
- 自写 numba Marching Cubes（标准查表），顶点以全局边编号 (i, j, k, axis) 标识，按编号焊接（ADR 0005）；采样值恰为 0 按外部处理，焊接后再按坐标合并一次、删除退化三角形。
- 焊接后用 manifold3d 校验流形与水密；删除体积 < `profile.min_fragment_volume_mm3` 的碎块，记录数量与体积。
- 输出规范化：顶点按坐标字典序、三角形按顶点编号排序，便于比较和哈希。
- **验收**：
  - 解析球体（R = 20 mm，1 mm 体素）体积误差 ≤ 1%；
  - 球体、圆角盒、两个相交胶囊：单块与多块提取的规范化网格完全相同，网格水密，欧拉示性数正确；
  - 一个跨 8 个块交点的形状，焊接后无裂缝；
  - 一个让大量采样值恰为 0 的形状（与栅格对齐的平面），网格水密且无退化三角形。
- 这是 M0 最容易出隐蔽错误的地方，所以用 O·xhigh。

### M0.7 杆状晶格节点 — S·high
- `StrutLattice(cell_mm, radius_mm, topology="bcc", origin, fillet_k)`：在查询点所在 Cell 及其 26 邻域内按周期公式枚举杆（BCC 每个 Cell 8 根体对角杆）；节点圆角用指数平滑并集 `−k·ln Σ exp(−d_i/k)`，按固定的全局杆顺序求和（ADR 0002）。k 默认 0.15 × r。
- 构建时参数检查（读 M0.1 的 profile，退出码 3）：`2r ≥ min_strut_diameter_mm`；`voxel_mm ≤ 2r / 4`。
- **验收**：
  - 杆轴线中段 d = −r（误差 ≤ 1e-9，远离节点处）；节点中心 d ≤ −r；
  - Lipschitz 测试同 M0.4；
  - 在 Cell 边界两侧取点，求值连续（邻域截断造成的跳变 ≤ 1e-6 mm）；
  - 相对密度随 r 单调，在 0.25 mm 体素下与 BCC 的解析近似公式误差 ≤ 10%。

### M0.8 鞋体装配 — O·high
记 `cavity = Offset(鞋楦, clearance=1.0)`，`h(x, y)` 是鞋楦底面高度图，`z_ground = z_min − H_fore`（`z_min` 是鞋楦最低点，`H_fore` 默认 12 mm）。地面是水平面；鞋楦有跟高，所以后跟处鞋底自然更厚。

- **鞋底包络** = `Extrude2D(足底轮廓外扩 3 mm, z ∈ [z_ground, z_top])` ∩ `BelowHeightField(h + 3 mm)`，再减去 cavity。`z_top` 取鞋楦最高的底面点再加 3 mm，保证有跟高的后跟和翘起的鞋头也被包住。
- **鞋底** = (包络向内收缩 1.2 mm ∩ 晶格) ∪ 包络外皮（1.2 mm）∪ 足床板（`ShellOutward(cavity, 1.5)` ∩ 包络）。被裁切的杆都并入外皮或足床板，不留悬空断头。
- **鞋面** = `ShellOutward(cavity, 2.0)` ∩ `z ≤ 领口高度`。
- **整鞋** = 鞋底 ∪ 鞋面，最后再减一次 cavity。因为两种壳都是向外长的，这一步不会削薄它们。
- 全部用 M0.4 的节点表达，参数写在 `examples/m0.json`。
- **验收**（合成鞋楦，含一个有 30 mm 跟高的变体）：
  - 整鞋网格水密、单连通；
  - 鞋楦内部的采样点处没有材料；
  - 领口真的开着：在领口高度的鞋楦截面内布一个射线网格，沿 −Z 向下发射，≥ 90% 的射线在到达足床板之前不穿过任何材料；
  - 后跟和鞋头处，鞋楦底面以下都有鞋底材料（沿 −Z 的射线在到达 `z_ground` 前穿过材料）。

### M0.9 检查与报告 — S·medium
- 按 ADR 0004 的三类检查实现：
  - **构建前参数检查**：已在 M0.7 实现，这里汇总进报告；
  - **hard**（退出码 5）：水密、连通分量数 = 1、构建体积；
  - **soft**：
    - 薄片：在体素栅格上做距离变换，统计局部厚度 < `min_wall_mm` 的材料体积；
    - 封闭腔：体素泛洪，统计从外部不可达的空腔体积；
    - 最小孔径：M0 只用 Cell 与杆径的解析值估算。
- 所有栅格检查都按块进行，不分配整鞋稠密数组。
- `report.json`：每项检查的实测值、阈值、来源 profile 及其 status；制造结论固定写 `NOT_VERIFIED`。

### M0.10 CLI 与端到端 — S·medium
- `shoegen build --graph examples/m0.json --last input/lasts/<file>.step --profile pbf-tpu-generic@0 -o output/m0 [--voxel-mm 0.5] [--max-rss-gb 6]`。
- 输出：`shoe.stl`、`graph.json`、`report.json`、`manifest.json`（文件 sha256、依赖版本、run_id、耗时、RSS 峰值）。只有前两个承诺逐位确定（ADR 0005）。
- 退出码：0 成功；1 内部错误；2 用法错误；3 输入、Graph 或构建前参数检查不通过；4 鞋楦导入失败；5 hard 检查失败；6 资源超限。
- **验收**：
  - 冒烟测试：合成鞋楦，1 mm 体素、杆半径 2 mm、Cell 12 mm（满足体素 ≤ 杆径 / 4），≤ 1 分钟，水密单连通；
  - 同一输入跑两次，`shoe.stl` 与 `graph.json` 的 sha256 相同；
  - 杆径低于 profile 下限时以退出码 3 结束，且不生成网格；
  - `real_last`：真实鞋楦、0.5 mm 体素、杆半径 1 mm，在 6 GB 上限内完成，报告耗时与峰值 RSS（H1 之后执行）。

### M0.R 里程碑评审 — O·high
- 对照 ADR 0001–0005 和本节验收逐条核对。重点审查：稠密数组、确定性、跳块正确性、Lipschitz、焊接，以及报告里有没有未实测就写 PASS 的条目。
- 发现的问题回到对应任务修复；正确性问题用高一级的模型重做。

---

## 3. M1–M3 目标（待细化）

### M1 Coupon 与选工艺
- `shoegen coupon`：40 × 40 × 20 mm 试块，参数网格 = Cell {8, 10, 12} mm × 杆径 {3–4 档}，与鞋底同样上下加实体皮。
- 两种工艺（MJF TPU、DLS 弹性体）各打一批；压缩测试得到刚度–相对密度曲线。
- 产出：选定工艺；对应 profile 升级为 `vendor_guideline`；`stiffness.v1` 标定表。

### M2 真正的鞋
- 杆径沿鞋底按区域渐变（后跟、前掌、足弓），渐变只改半径不改 Cell（吸取 PR #1 的 ADR 0010 的教训）；变径杆用 round-cone 公式（ADR 0001）。
- 网格薄片检查升为 hard（ADR 0004）。
- 随形鞋面晶格：在鞋楦表面参数化上布节点，沿法向生成 1–2 层；边界节点和杆终止在 Rim 或实体皮上，消除断头。
- 外底实体与花纹、3MF 输出（如果选定 DLS，评估 3MF beam lattice 扩展）。
- 视 M0 性能决定是否换 OpenVDB 或 GPU 求值。

### M3 语义层
- DesignSpec（尺码、缓震档位、鞋面开放度等）→ Resolver → GeometryGraph；Resolver 用 `stiffness.v1` 把档位翻译成杆径。
- 尺码变形节点（ADR 0003），关键尺寸来自尺码表。
- 黄金回归：若干 DesignSpec 的 `graph_hash` 与网格指标。

---

## 4. 人工检查点

| 编号 | 时点 | 需要你做或决定的事 |
|---|---|---|
| H0 | 现在 | 审阅 architecture、ADR 0001–0005 和本计划；回答第 5 节的开放问题，或指定在哪个检查点回答 |
| H1 | M0.10 之前 | 把鞋楦 STEP 放到 `input/lasts/`，确认归一化用的坐标变换（可沿用 PR #2 的配置） |
| H2 | M0 合并后 | 目视检查真实鞋楦生成的 M0 鞋（STL）；选定 M1 打样的两家服务商；下单与付款由你本人完成 |
| H3 | M1 测试后 | 选定工艺；批准 profile 与 `stiffness.v1` |
| H4 | M2 后 | 整鞋打样与试穿 |

---

## 5. 开放问题

| 编号 | 问题 | 影响 | 最晚何时决定 |
|---|---|---|---|
| Q1 | 现有鞋楦文件头为 CC BY-NC-ND 4.0，是否允许变形和商用？是否另购可商用鞋楦？ | 尺码变形（M3）和任何对外使用 | M3 之前；对外展示之前 |
| Q2 | M1 找哪两家服务商（各一种工艺）？ | M1 排期 | H2 |
| Q3 | 鞋面在 M2 是否必须是晶格，还是可以先用带开孔的实体薄壳？ | M2 规模 | M1 结束 |
| Q4 | PR #1、PR #2 的处置：建议在 M0 合并后关闭并保留分支 | 仓库整洁 | M0 合并后 |
