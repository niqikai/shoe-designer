# P0 规格：全晶格一体打印鞋参数化引擎

本文汇总 P0 的全部已确认决定，是实现的依据。术语以 [GLOSSARY.md](../GLOSSARY.md) 为准，关键取舍的理由见 [docs/adr](adr/)。

**数字的三类标记**：
- **[初值]**：数据表（Tuning Table、比例表、尺码表、Archetype 数据等）中的起始值。标定时可以修改，修改只需给数据表升版本，不算设计变更。
- **[实现]**：实现参数。实现者可以选定或调整，选定的值记录在代码常量或 Build Manifest 中。
- **未标注**：设计约束。修改需要回到设计讨论。

长度单位一律为 mm（GLB 除外）。

---

## 1. 范围与完成标准

### 1.1 目标形态

未来 SaaS 的完整流程是：

```
自然语言 → LLM → DesignSpec → 引擎 → Print Mesh / Preview Mesh
```

P0 只实现其中"DesignSpec → 引擎 → 产出"这一段。

### 1.2 P0 包含

- **输入**：DesignSpec 0.1（JSON）、存档的 BuildPlan，或一种 Coupon。
- **流水线**：Resolver 阶段 A → Shape Source（Blender 无界面子进程）→ Shape Bundle → Resolver 阶段 B（含局部探测）→ 晶格引擎（Zone 推导、场合成、等值面提取、Island 清理、简化）→ Printability Check → 导出。
- **产出**：
  - 每只鞋一个 Print Mesh（STL）和一个 Preview Mesh（GLB）
  - 一份 Build Manifest 和一份 BuildPlan
  - Crop Build 与 Coupon Build 的产出
- **交付形态**：Python 库加 CLI（`shoegen`）。
- **唯一合法组合**：ManufacturingMode = `OnePiece3DPrint`，StructureType = `FullLattice`，Archetype = `low_top_slip_on`。

### 1.3 P0 不包含

- LLM 调用
- HTTP 服务和任务队列
- FEA 或力学仿真
- 鞋底花纹
- 打印摆放与嵌套
- 杆系晶格、Voronoi、Diamond、sheet 变体
- 多零件制造、HybridLattice
- 分性别的鞋楦
- 鞋垫
- 形状参数的 Override

### 1.4 完成标准

P0 在以下三条全部满足时完成：

1. 第 15 节规定的自动化测试全部通过。
2. 至少一只整鞋的 Print Build 满足：所有 hard 级 Printability Check 通过，且薄壁占比和困粉占比都不超过阈值（见 10.2）。
3. 按第 16 节的流程完成裁切件与 Coupon 的验证后，这只鞋被实际打印出来。

---

## 2. 系统总览

```
DesignSpec ─► Resolver 阶段 A ─► 草稿 BuildPlan ─► Shape Source（blender -b）─► Shape Bundle
                                                                                  │
               BuildPlan（含 plan_hash）◄─ Resolver 阶段 B（定 N、随形约束、预检、局部探测）◄┘
                     │
                     ▼
晶格引擎：Shell 与 Zone 推导 → 场合成 → 分块等值面提取 → Island 清理 → 简化
        → Printability Check → 导出（STL / GLB / Build Manifest）
```

| 阶段 | 职责 | 运行位置 |
|---|---|---|
| Resolver 阶段 A | 版本处理、校验、按 Tuning Table 解析、应用 Override、欧氏部分约束求解（按需求值 Cell Properties）、生成形状参数 | 引擎进程 |
| Shape Source | 根据形状参数生成 Shape Bundle | `blender -b` 子进程（ADR 0001） |
| Resolver 阶段 B | 确定周向 Cell 数 N、随形部分约束求解、构建体积与资源预检、计算 plan_hash、局部探测（Print Build 的闸门，7.6） | 引擎进程 |
| 晶格引擎 | 推导 Shell 与 Zone、合成隐式场、提取网格、清理与简化 | 引擎进程（ADR 0002、0004、0005） |
| 校验 | Printability Check 与 Donning Check | 引擎进程 |
| 导出 | 写出 STL、GLB、Build Manifest，先写临时位置，通过后改名 | 引擎进程 |

引擎内部按 (ManufacturingMode, StructureType) 组合分派到对应流水线，不做插件框架。

---

## 3. DesignSpec 0.1

### 3.1 结构与默认值

```jsonc
{
  "schema_version": "0.1",                     // 引擎输入层必填，格式 MAJOR.MINOR
  "manufacturing_mode": "OnePiece3DPrint",     // 默认 OnePiece3DPrint
  "structure_type": "FullLattice",             // 默认 FullLattice
  "manufacturing_profile": "pbf-tpu-generic",  // 默认 pbf-tpu-generic
  "archetype": "low_top_slip_on",              // 默认 low_top_slip_on
  "fit": {
    "size": { "system": "EU", "value": 42 },   // 必填
    "width": "standard",                       // narrow | standard | wide，默认 standard
    "side": "pair"                             // left | right | pair，默认 pair
  },
  "sole": {
    "heel_stack_mm": 30,                       // 20–40，默认 30
    "drop_mm": 8,                              // 0–12，默认 8
    "toe_spring": "medium",                    // low | medium | high，默认 medium
    "cushioning": {
      "heel": "balanced",                      // soft | balanced | firm，默认 balanced
      "forefoot": "balanced"
    }
  },
  "upper": {
    "openness": "balanced",                    // airy | balanced | dense，默认 balanced
    "collar_height": "low"                     // low | mid，默认 low
  },
  "overrides": {}                              // BuildPlan 的 JSON Pointer → 值，见 7.4
}
```

以上默认值属于 DesignSpec 0.1 契约：修改任何默认值都必须升 minor 版本（3.3），不适用 [初值] 规则。

### 3.2 校验规则

- **严格 schema**：所有对象都不允许出现未知字段，出现即报错，不会被静默忽略。
- **必填字段**：只有 `schema_version` 和 `fit.size`。
- **组合限制**：P0 只接受 `OnePiece3DPrint × FullLattice`，其他组合报 `E_SPEC_UNSUPPORTED_COMBINATION`。
- **跨字段约束**：Forefoot Stack = `heel_stack_mm − drop_mm`，必须 ≥ 15 mm。违反时直接校验失败（`E_SPEC_FOREFOOT_STACK`），不做静默修正。
- **尺码范围**：必须落在 4.1 规定的范围内。

### 3.3 版本演进

- **必填**：引擎输入层的 `schema_version` 必填。LLM 可以省略，由 SaaS 注入。
- **支持区间**：引擎声明支持的版本区间，P0 为 `[0.1, 0.1]`。
  - 区间内的旧版本：用纯函数逐级迁移（v0.1→v0.2→…）到当前版本，再解析。
  - 比引擎新的版本：拒绝，报 `E_SPEC_VERSION_TOO_NEW`。
  - 比支持下限还旧的版本：拒绝，报 `E_SPEC_VERSION_UNSUPPORTED`。
- **默认值也是语义**：修改默认值必须升 minor 版本。
- **记录**：Build Manifest 同时保存原始 spec、迁移后的 spec，以及默认值全部展开后的规范化 spec。

### 3.4 Schema 导出

`shoegen schema --target <t>`：

| target | 内容 |
|---|---|
| `full` | 完整的 DesignSpec JSON Schema；`overrides` 的键用白名单枚举约束 |
| `llm` | 面向 LLM 结构化输出的投影，处理见下表 |
| `buildplan` | BuildPlan 的 JSON Schema；可覆盖的叶子标记为 `x-overridable: true`，并带取值范围 |
| `manifest` | Build Manifest 的 JSON Schema |

`llm` 投影的处理：

| 处理 | 原因 |
|---|---|
| 去掉 `overrides`、`fit`、`schema_version` | 后两者由 SaaS 注入 |
| 所有对象都设为 `additionalProperties: false` | 结构化输出要求 |
| 枚举写成 `anyOf[{const, description}]`，逐值说明含义 | 让 LLM 理解每个档位 |
| 数值范围同时写进 `description` | Anthropic 结构化输出会剥掉 minimum/maximum，引擎校验是唯一的纠错通道 |

### 3.5 校验错误格式

错误统一为 JSON 数组，元素结构如下。CLI 带 `--errors-json` 时把数组写到 stderr；Build Manifest 中则写在 `errors` 字段。

```json
[{ "class": "spec", "code": "E_SPEC_FOREFOOT_STACK", "message": "...",
   "json_pointer": "/sole/drop_mm", "expected": "heel_stack_mm − drop_mm ≥ 15",
   "suggestion": "drop_mm ≤ 5（当前 heel_stack_mm = 20）" }]
```

`class` 取值：`spec`、`shape`、`printability`、`resource`、`timeout`、`internal`。

---

## 4. 尺码与 Last

### 4.1 尺码换算

- **定义方式**：EU、UK、US 按 Last Length 定义；Mondopoint 按 Foot Length 定义。
- **数据文件**：`sizes.v1` 直接存"码 ↔ Last Length"。
- **换算关系**：Foot Length = Last Length − Toe Allowance。

| 体系 | 定义 | 支持范围 | 步长 |
|---|---|---|---|
| EU | Last Length = EU × 20/3 mm（巴黎点） | 35–48 | 0.5 |
| UK | Last Length = (UK + 25) × 25.4/3 mm（大麦粒） | 3–12.5 | 0.5 |
| US_M | UK = US_M − 1 **[初值]** | 4–13.5 | 0.5 |
| US_W | UK = US_W − 2.5 **[初值]** | 5.5–15 | 0.5 |
| Mondopoint | Foot Length（mm） | 225–305 | 5 **[初值]** |

UK、US_M、US_W 与 Mondopoint 的支持范围，都是由 EU 35–48 对应的 Last Length 区间 [233.33, 320.00] mm 推出来的；Mondopoint 再按 Toe Allowance 12 mm 换算成 Foot Length。`sizes.v1` 修订 US 偏移时，这些范围随之重算，不算设计变更。

- **偏移出处**：US 与 UK 之间的偏移各品牌不统一，`sizes.v1` 中需注明所采用的约定和出处。
- **范围检查**：只针对各体系自身的数值，在应用 Toe Allowance 的 Override 之前进行。超出范围报 `E_SPEC_SIZE_OUT_OF_RANGE`。
- **Toe Allowance**：默认 12 mm。用 Override 修改时：
  - EU/UK/US：同一尺码的 Last Length 不变，Foot Length 随之改变；
  - Mondopoint：Foot Length 不变，Last Length 随之改变。
- **比例表定义域**：考虑到 Toe Allowance 的覆盖范围，比例表的 Foot Length 定义域取 213–312 mm。

### 4.2 Last 比例模型

每个维度写成 `a + b × Foot Length`（仿射，不做等比缩放），存于 `proportions.v1`。P0 只有一套男女通用表。

| 维度 | 系数 | 来源 |
|---|---|---|
| Ball Width（脚） | a = 34.5，b = 0.25 **[初值]** | Jurca 2019 男性回归，以 Foot Length 270 mm 为锚 |
| Instep Height | a = 38.4，b = 0.09 **[初值]** | 同上 |
| Heel Width | a = 10.8，b = 0.21 **[初值]** | 同上 |
| Ball Girth | 待定（见第 19 节） | 实现阶段查证并注明出处 |
| Long Heel Girth | 待定（见第 19 节） | 同上 |

- **Width 档位**：只平移 Ball Width 与 Ball Girth 的截距，不改斜率。

  | 档位 | Ball Width | Ball Girth |
  |---|---|---|
  | narrow | −5 mm **[初值]** | −10 mm **[初值]** |
  | standard | 0 | 0 |
  | wide | +5 mm **[初值]** | +10 mm **[初值]** |

  Ball Girth 平移约为 Ball Width 的两倍，目的是保持跖部截面形状。
- **从脚到 Last**：表中都是脚的尺寸，Last 的尺寸由脚尺寸加放余量规则推导。放余量规则同样写在 `proportions.v1` 中。

### 4.3 Last 的固定特征

- **Footbed**：固定形状的标准楦底曲面，带后跟凹窝和轻微足弓。P0 不开放参数。
- **鞋垫**：P0 不设鞋垫，Last 也不预留鞋垫厚度。
- **Toe Spring**：同时抬起 Last 底面和 Sole Envelope 底面。沿 Ground Surface 法向计，趾下鞋底厚度保持不变。
  - 抬起段从 `forefoot_stack_point`（0.75 Last Length）之后开始 **[初值]**。
  - 鞋尖抬起高度：low 10 / medium 15 / high 20 mm **[初值]**。
- **测量位置**：Heel Stack 在 Last Length 12% 处的中线上测量；Forefoot Stack 在 75% 处的中线上测量。

### 4.4 Collar 形状参数

鞋口几何由 `geometry.shape.collar` 中的形状参数控制。Shape Source 据此生成 `collar_line`、`throat_point`、`ankle_dip_*` 等 Landmark。

| 参数 | 取值 |
|---|---|
| 后跟处 Collar Height | low 50 / mid 65 mm **[初值]** |
| Throat 位置（自后跟起，占 Last Length 的比例） | 0.55 **[初值]** |
| Ankle Dip 深度 | 8 mm **[初值]** |

---

## 5. Shape Source 与 Shape Bundle

### 5.1 坐标系

- 单位 mm，右手系。
- **轴向**：+X 指向鞋头；+Z 向上；+Y 指向穿着者左侧（对右脚而言即内侧）。
- **原点**：
  - z = 0：地面，即后跟处鞋底最低点所在的水平面；
  - x = 0：经过 Sole Envelope 最后端点的平面；
  - y = 0：Last 纵轴所在的竖直面。
- **母版**：只为右脚建模，左脚由镜像得到（8.6）。

### 5.2 子进程契约

P0 的 Shape Source 用 Blender 程序化参数控制笼加细分曲面生成形状。

**调用方式**：

```
$SHOEGEN_BLENDER -b --factory-startup --python-exit-code 1 \
  --python <pkg>/shape/blender/build_bundle.py -- \
  --input <tmp>/shape_input.json --output <tmp>/bundle
```

- 未设置 `$SHOEGEN_BLENDER` 时，从 PATH 中查找 `blender`。
- 必须带 `--python-exit-code`：否则脚本抛异常时 Blender 仍以 0 退出。

**输入**：`shape_input.json` 的内容为 `{"shape": geometry.shape, "shape_hash": "<引擎计算的哈希>"}`。

**脚本的职责**：
- 把 `shape_hash` 原样写入 `shape.json`，同时写入 `bpy.app.version_string` 与 build hash。
- 失败时写出 `<output>/error.json`（`{code, message}`），并以非零码退出。

**引擎侧处理**：

| 情况 | 错误码 | 退出码 |
|---|---|---|
| 子进程非零退出或崩溃 | `E_SHAPE_SOURCE_FAILED` | 4 |
| Bundle 违反不变量 | `E_SHAPE_INVALID` | 4 |

- 不变量检查通过后，才把临时目录改名为 `<out>/shape/`。
- **版本校验**：
  - 启动前解析 `blender --version` 的第一行；
  - 运行后，`shape.json` 记录的主次版本必须等于引擎常量 `BLENDER_REQUIRED = "5.2"`，否则报 `E_SHAPE_BLENDER_VERSION`（退出码 4）；
  - patch 版本不同只发警告。
  - 单元测试加载 fixture 时，只核对 fixture 自带的 `shape.json`，不需要本机安装 Blender。

### 5.3 Shape Bundle 目录

| 文件 | 内容 | 要求 |
|---|---|---|
| `last.ply` | Last | 二进制 PLY，单位 mm，引擎坐标系；闭合、2-流形、已三角化（导出时 `export_triangulated_mesh=True`）；不带 UV |
| `sole_envelope.ply` | Sole Envelope | 同上 |
| `landmarks.json` | 点、折线、面集 | 见 5.4 |
| `last_param.npz` | Surface Parameterization | 可选，见 5.5 |
| `shape.json` | 来源信息 | Shape Source 名称与版本、Blender 版本与 build hash、`shape_hash`、输入的形状参数 |

**不变量**：

1. `last.ply` 与 `sole_envelope.ply` 读入后不做任何焊接即为水密 2-流形。
2. Footbed 位于 Sole Envelope 之内。
3. 在 Footbed Edge 处，Sole Envelope 的侧壁至少高出 Footbed 6 mm **[初值]**，与 Shell 重叠，保证两者的并集无缝。
4. `ground` 面集的外法向都有 n_z < 0。
5. `shape.json` 中的 `shape_hash` 等于 BuildPlan 中 `geometry.shape` 的哈希；不一致即拒绝构建。

### 5.4 `landmarks.json`

```jsonc
{
  "landmarks_version": "1",
  "points": {
    "heel_point": [x, y, z], "toe_tip": [...],
    "heel_stack_point": [...], "forefoot_stack_point": [...],   // 12% / 75% 处中线上的 Footbed 点
    "throat_point": [...], "ankle_dip_medial": [...], "ankle_dip_lateral": [...]
  },
  "polylines": {
    "ball_line": { "points": [[...], [...]], "order": "medial_to_lateral" },
    "footbed_edge": { "last_vertex_loop": [int, ...] },
    "collar_line":  { "last_vertex_loop": [int, ...] }
  },
  "face_sets": {
    "ground":  [int, ...],     // sole_envelope.ply 的面索引：Ground Surface，含 Toe Spring 与后跟上翘段，止于侧壁开始处
    "footbed": [int, ...]      // last.ply 的面索引：Footbed，边界必须等于 footbed_edge
  }
}
```

**闭合环的约定**：
- 不重复首点；
- 起点是后跟中线处的顶点；
- 从 +Z 俯视按逆时针排列，与 5.5 中 s 增加的方向一致。

### 5.5 `last_param.npz`（Surface Parameterization）

**覆盖范围**：
- `last.ply` 在同一细分级下的一个面子集；
- 覆盖 Footbed Edge 到 Collar Line 之间的环带，并向 Footbed 一侧延伸至少 6 mm（测地距离），供 Footbed Edge 附近的最近点查询使用（Welt 与坐标系分界，8.1、ADR 0011）。

**数组**：

数组长度记号：V 为顶点数，F 为面数，K 为接缝顶点对数。

| 数组 | 形状与类型 | 含义 |
|---|---|---|
| `vertices` | (V,3) float64 | 接缝处的顶点拆为两份 |
| `faces` | (F,3) int32 | 三角面 |
| `last_vertex_index` | (V,) int32 | 每个参数顶点对应的 `last.ply` 顶点；拆开的接缝顶点映射到同一个 last 顶点 |
| `last_face_index` | (F,) int32 | 每个参数面对应的 `last.ply` 面 |
| `tau` | (V,) float64 | 归一化高度 τ。τ 等值线是闭合环：`footbed_edge` 处 τ = 0，`collar_line` 处 τ = 1，延伸区 τ < 0。P0 取控制笼的环向细分线 |
| `s` | (V,) float64 | 周向坐标 ∈ [0,1]：τ 等值环上的归一化弧长，从后跟中线起，+Z 俯视逆时针；延伸区沿用 τ = 0 环上的 s；拆开的接缝顶点分别取 0 和 1 |
| `t_mm` | (V,) float64 | 沿 s = 常数线、自 `footbed_edge` 量起的弧长；延伸区为负 |
| `seam_vertex_pairs` | (K,2) int32 | (s=0 的索引, s=1 的索引) |

**额外约束**：
- 参数网格的每一行是一条 τ 等值环。`footbed_edge` 是 τ = 0 的那一行（该行 t_mm = 0），`collar_line` 是 τ = 1 的那一行；两者都与 `landmarks.json` 共用顶点索引。
- 记号：
  - L(s)：Collar Line 处的 t_mm。L(s) 沿周向变化很大，后跟处约 45 mm，经鞋头到 Throat 可达约 150 mm，所以 t_mm 的等值线一般不闭合，周向量一律按 τ 定义。
  - P(τ)：τ 等值环的三维长度。P(0) 是 `footbed_edge` 的全长，P(1) 是 `collar_line` 的全长。

**查询方法**：
1. 在 `last.ply` 上找最近点；
2. 用该点所在面号经 `last_face_index` 找到对应的参数面；
3. 在参数面上做重心插值，得到 (s, τ, t_mm)；
4. s 取 mod 1。

**缺失时**：P0 报 `E_SHAPE_PARAM_MISSING`。引擎自行计算参数化的功能在 P1 实现。

---

## 6. BuildPlan

### 6.1 结构

BuildPlan 分为 `geometry`（几何段）和 `resolution`（分辨率段），按 Part × Zone × Fill 组织（ADR 0008）。

```jsonc
{
  "buildplan_version": "0.1",
  "profile": "pbf-tpu-generic@1",
  "geometry": {
    "fit": { "foot_length_mm": 268.0, "last_length_mm": 280.0, "toe_allowance_mm": 12.0 },
    "shape": {                                   // 交给 Shape Source；其哈希即 shape_hash
      "archetype": "low_top_slip_on@1",
      "last": { "ball_width_mm": …, "ball_girth_mm": …, "instep_height_mm": …,
                "heel_width_mm": …, "long_heel_girth_mm": …, "toe_spring_mm": 15.0 },
      "sole": { "heel_stack_mm": 30.0, "forefoot_stack_mm": 22.0, "wall_overlap_mm": 6.0 },
      "collar": { "heel_height_mm": 50.0, "throat_ratio": 0.55, "ankle_dip_mm": 8.0 }
    },
    "parts": [{
      "id": "body",
      "shell": { "wall_thickness_mm": 4.0, "cell_inplane_mm": 8.0,
                 "circumferential_cells": 41 },  // N，阶段 B 派生，不可覆盖
      "zones": {
        "outsole":      { "frame": "euclidean", "thickness_mm": 3.0,
                          "fill": { "kind": "lattice", "family": "gyroid", "variant": "network", "rho": 0.35 } },
        "midsole":      { "frame": "euclidean",
                          "fill": { "kind": "lattice", "family": "gyroid", "variant": "network",
                                    "cell_mm": 10.0, "rho_heel": 0.20, "rho_forefoot": 0.20 },
                          "footbed_layer": { "thickness_mm": 4.0, "rho_delta": 0.05 } },
        "upper":        { "frame": "conformal",
                          "fill": { "kind": "lattice", "family": "gyroid", "variant": "network", "rho": 0.25 } },
        "heel_counter": { "frame": "conformal", "extent_x_ratio": 0.25,
                          "fill": { "kind": "lattice", "family": "gyroid", "variant": "network", "rho": 0.35 } }
      },
      "transitions": { "lateral_width_mm": 6.0, "vertical_max_mm": 6.0 },
      "features": {
        "collar_rim":  { "kind": "rim",  "along": "collar_line", "height_mm": 2.0, "thickness": "wall" },
        "ground_skin": { "kind": "skin", "zone": "outsole", "surface": "ground", "thickness_mm": 1.2 },
        "welt":        { "kind": "welt", "at": "footbed_edge", "height_mm": 1.5 }
      }
    }]
  },
  "resolution": {
    "voxel_mm": 0.25,                  // Print Build、Crop Build 与 Coupon Build 使用
    "preview_voxel_mm": 0.5,           // 所有 GLB 使用
    "voxel_origin_offset": 0.5,        // 以体素为单位
    "chunk_voxels": 128,               // [实现]；一经选定即固定，不随内存或线程数调整（ADR 0006）
    "simplify_tolerance_mm": 0.05,     // Print Mesh 使用
    "preview_max_triangles": 2000000   // 所有 GLB 使用 [实现]
  },
  "outputs": { "sides": ["right", "left"] }   // 不计入 plan_hash
}
```

### 6.2 派生与组织规则

- **鞋底 Cell**：鞋底只存一个 Cell C（`midsole.fill.cell_mm`），Outsole 晶格层、Midsole 与 Footbed Layer 共用，层间只以 ρ 区分（ADR 0010）。C 与 C/2 一类的混合会在过渡带内产生颈厚从 0 起的新生 Ligament，不可打印（实测见 ADR 0010）。
- **Welt**：`features.welt` 是 Footbed 高度的实体条，鞋底（欧氏）与 Shell（随形）两个坐标系的晶格在其中硬切换（8.1、8.3、ADR 0011）。两个坐标系之间没有场混合，因此没有 `cross_frame_width_mm`。
- **Shell 级参数**：`shell.cell_inplane_mm` 和 `circumferential_cells` 放在 Shell 级，Upper 与 HeelCounter 共用；两者只以 ρ 区分。
- **Zone 集合**：由 Archetype 数据文件（`archetypes/low_top_slip_on.v1`）声明，包括有哪些 Zone、各用什么坐标系、`heel_counter` 的切分方式与比例。HeelCounter 按平面 x − x_heel ≤ ratio × Last Length 切分 **[初值]**，其中 x_heel 是 `heel_point` 的 x 坐标。所有按 Last Length 比例给出的位置都从 `heel_point` 量起。
- **P0 的 Fill**：`fill.kind` 只有 `lattice`，`family` 只有 `gyroid`，`variant` 只有 `network`。

### 6.3 规范化与哈希

- **量化**：Resolver 写入前，把 `geometry` 中的长度量化到 1e-4 mm，比例量化到 1e-6（银行家舍入），并把 −0.0 规范为 0.0。BuildPlan 中存储的就是量化后的值。schema 中长度一律为 number，整数字面量按浮点处理。
- **plan_hash**：

  ```
  plan_hash = SHA-256(UTF-8(json.dumps({"buildplan_version", "profile", "geometry"},
              sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False)))
  ```

  浮点按 Python 的最短往返 repr 输出。
- **shape_hash**：用同一个函数对 `geometry.shape` 计算，由引擎传给 Blender，Blender 侧不自行计算。
- **共用**：同一设计的 Preview Build 与 Print Build 有相同的 plan_hash，只是使用 `resolution` 中的不同字段。局部探测（7.6）不修改 BuildPlan，因此 Preview Build 不跑探测也不影响 plan_hash 相同。
- **补打**：以存档的 BuildPlan 为准，见 7.1 的 `--plan` 路径。

---

## 7. Resolver

### 7.1 处理顺序

**阶段 A（Shape Source 之前）**：

1. **版本处理**：按 3.3 迁移或拒绝。
2. **Schema 校验**：执行 3.2 的严格校验和跨字段校验。
3. **规范化**：把默认值全部展开。
4. **语义解析**：按 Tuning Table 生成草稿 BuildPlan，并记录 Resolution Note。
5. **应用 Override**：见 7.4。被覆盖的值被"钉住"，后续步骤不得修改。
6. **欧氏部分约束求解**：对 Midsole、Footbed Layer、Outsole 执行 7.3。
7. **生成形状参数**：根据尺码表、比例表和 Collar 形状参数初值，生成 `geometry.shape` 与 `shape_hash`。

**运行 Shape Source**：见 5.2。

**阶段 B（Shape Source 之后）**：

8. **确定 N**：N = max(1, round(P(0.5) / cell_inplane_mm))。P(0.5) 是中间高度环（τ = 0.5）的周长（5.5，ADR 0005）。N 写入 `shell.circumferential_cells`，并记一条 Resolution Note。
9. **随形部分约束求解**：对 Upper 与 HeelCounter 执行 7.3。两端分别使用以下 Cell 形状（周向, t 向, 法向）：

   | 端 | Cell 形状 |
   |---|---|
   | Collar 端 | (P(1)/N, cell_inplane, Wall Thickness) |
   | Footbed Edge 端 | (P(0)/N, cell_inplane, Wall Thickness) |

   放大 `cell_inplane` 时重新计算 N。可选改进 **[实现]**：随形检查点由"两端"改为 P(τ) 的最小与最大处；P 单调时两者相同，否则更稳妥，求值器使这一改动不增加成本。
10. **预检**：
    - 用 Bundle 包围盒做构建体积预检。不通过时报 `E_BUILD_VOLUME`，class 为 printability，退出码 5。它与 10.2 的构建体积 hard 检查同码，只是在生成晶格之前提前判定。
    - 用 Bundle 体积做资源预检（第 13 节）。
11. **定稿**：计算 plan_hash，写出 `buildplan.json`。
12. **局部探测**（闸门，7.6）：在定稿之后的若干探测窗口里，于真实合成场上执行体素类检查；不修改 BuildPlan，只放行或判失败。Print Build（含 `--plan`）默认执行。

**`--plan` 路径**（按存档的 BuildPlan 重建）：

- 跳过步骤 1–7，`geometry.shape` 直接取自存档。
- 照常运行 Shape Source。
- 阶段 B 以"只校验"模式执行：
  - 重算的 N 必须等于存档值，否则报 `E_PLAN_SHAPE_MISMATCH`（退出码 4）；
  - 约束不满足报 `E_INFEASIBLE`（退出码 3）；
  - 不产生任何 Adjustment；
  - 判定放宽 τ，即接受 value ≥ 要求 − τ（7.5），避免存档的 BuildPlan 在另一平台上因浮点噪声被判 `E_INFEASIBLE`；同平台下不起作用；
  - 预检照常执行；
  - 局部探测照常执行（闸门，7.6）。

### 7.2 Tuning Table（`tuning.v1`）

全部为 Gyroid network。

| Zone / 层 | 坐标系 | Cell | Cell 上限 | ρ 名义值与 Tier Band |
|---|---|---|---|---|
| Midsole | 欧氏 | C = 10 mm | 12 mm **[初值]** | soft 0.15 [0.125, 0.175)；balanced 0.20 [0.175, 0.235)；firm 0.27 [0.235, 0.305] **[初值]** |
| Footbed Layer（Midsole 顶部 4 mm） | 欧氏 | C（同 Midsole） | 随 C | Midsole 当地 ρ + 0.05；Tier Band 随 Midsole 档位平移 |
| Outsole（3 mm 晶格层，下方是 1.2 mm Skin） | 欧氏 | C | 随 C | 0.35 [0.32, 0.38] **[初值]** |
| Upper（Wall Thickness 4 mm） | 随形 | 面内 8 mm（中间高度；t 向恒为此值）；法向 = Wall Thickness | 面内 10 mm **[初值]** | airy 0.20 [0.175, 0.225)；balanced 0.25 [0.225, 0.285)；dense 0.32 [0.285, 0.355] **[初值]** |
| HeelCounter | 随形 | 与 Upper 共用 | 与 Upper 共用 | 0.35 [0.32, 0.38] **[初值]** |

- **Tier Band 的取法**：
  - 以相邻档位名义值的中点为界，这是设计约束（R4Q12）；
  - 最外侧两档向外取对称的半宽，单档的 Zone 取 ±0.03 **[初值]**。
- **Midsole 纵向 Grading**：ρ 在 `heel_stack_point` 与 `forefoot_stack_point` 之间随 x 线性 Grading，两点之外保持常数 **[初值]**。
- **Tuning Table 自检**（单元测试）：
  - 检查范围：每个 Zone 每个档位的名义值，在 `pbf-tpu-generic`、默认鞋底几何（heel 30 / drop 8）下，覆盖 EU35、EU42、EU48 三个尺码；
  - 要求：运行 7.3，均不得产生 Adjustment；
  - 参数域的极端角落（例如 heel 20 / drop 5 时前掌 soft）允许产生 Adjustment。这类情况由 G2 回归断言 Adjustment 被正确记录。
- **定稿方式**：Openness 与 HeelCounter 的最终 ρ 以 Cell Properties（7.5）的求值结果定稿。

### 7.3 约束求解

**检查点**：

| 坐标系 | 检查点 |
|---|---|
| 欧氏 | 见下方说明 |
| 随形 | Upper 与 HeelCounter，各取 7.1 第 9 步的两端 |

欧氏部分的检查点：
- **位置**：沿中线在 [0, Last Length] 上每 5 mm **[实现]** 取一个采样点，另加 `heel_stack_point` 与 `forefoot_stack_point`；Midsole、Footbed Layer、Outsole 都在这些位置检查。
- **取值**：每个点用当地 ρ 和 Cell C。
- **当地 Midsole 主体厚度 T**：在阶段 A 估算，记 t_layers = t_skin + t_out + t_fbl（见 8.1）。
  - x ≤ `heel_stack_point` 处：T = Heel Stack − t_layers；
  - x ≥ `forefoot_stack_point` 处：T = Forefoot Stack − t_layers（Toe Spring 段趾下厚度不变）；
  - 两点之间：线性插值。
- **Transition 与 Grading 带**：带内 ρ 单调插值（8.2），Neck Thickness 与 Pore Throat 对 ρ 单调，因此只检查带的两端（即两侧层各自的 ρ），不取中间点。

**可行区间**：由 Cell Properties（7.5）在检查点的 (Cell 形状, ρ) 上直接求值，不插值，得到 [ρ_min, ρ_max]。
- ρ_min：使 Neck Thickness ≥ 最小壁厚 + `neck_margin_mm`（Profile 字段，见 9.1）的最小 ρ。
- ρ_max：使 Pore Throat ≥ 该点所在孔喉级别下限（9.1）的最大 ρ。
- 孔喉级别：
  - 薄层：Shell 中各 Zone、Footbed Layer 及其 Grading 带（d_F ≤ 1.5·t_fbl）、Outsole 及其过渡带，以及 Midsole 中 T < C 的点（厚度不足一个 Cell，排粉路径短）。
  - 芯部：Midsole 的其余点。

**求解顺序**：

1. **钉住的值不可行**：被钉住的值在任一检查点落在区间外，或超出 Cell Properties 的定义域（ρ ∈ [0.12, 0.46]，a_s、a_t ∈ [0.5, 6]） → 报 `E_INFEASIBLE_OVERRIDE`。
   - Footbed Layer 的 ρ 是 Midsole ρ 的派生量：`rho_heel` 或 `rho_forefoot` 被钉住时，对应一端的 Footbed Layer ρ 也视为被钉住。
   - `rho_delta` 只有在 Midsole ρ 未被钉住时，才可以通过 Adjustment 修改。
2. **名义值可行**：名义 ρ 在所有检查点都落在区间内 → 完成。
3. **放大 Cell**：仅当 Cell Properties 表明"在不超过 Cell 上限的范围内放大 Cell，能让名义 ρ 在所有检查点都落入区间"时，才放大 Cell：
   - 欧氏部分放大 C，各向同性。颈厚与孔喉都按 C 线性缩放，所需 C = max（各检查点上 1.05 / neck(ρ)，孔喉限值 / pore(ρ)），向上取到 0.05 mm 取值格 **[实现]**，再与 Cell 上限比较。这是闭式计算，不发生新的求值。
   - 随形部分放大 `cell_inplane`，Wall Thickness 不变。候选从名义值起按 0.25 mm **[实现]** 递增，直到上限 10 mm，共 ≤ 8 个，按顺序线性扫描。每个候选要重算 N 与两端的 a_s、a_t。不能用二分：N 取整使 a_s 阶梯变化，颈厚对 a_t 的单调性没有保证。

   观察：实测（L_s 取 6、8、11）Shell 的最小颈厚几乎与 L_s 无关，放大 `cell_inplane` 主要改善孔喉。求值器在实际形状上判定，不依赖这一点；颈厚与孔喉分别求值。

   取满足条件的最小 Cell，记一条 Adjustment。
4. **在 Tier Band 内移动 ρ**：否则 Cell 保持名义值，按以下顺序调整：
   - 用 x ≤ `heel_stack_point` 的检查点确定 `rho_heel`，用 x ≥ `forefoot_stack_point` 的检查点确定 `rho_forefoot`。两者各自在本档位的 Tier Band 内，取离名义值最近的可行值。Adjustment 产生的 ρ 落在 0.005 取值格上（Tier Band 的端点都在取值格上）；随形部分在取值格上二分（≤ 14 点，≤ 4 步），每步求两端。Override 与派生值（Midsole ρ + `rho_delta`）等取值格外的 ρ，在原值上直接求值。
   - Footbed Layer 不可行时，只调整 `rho_delta`，其 Tier Band 随 Midsole 档位平移。
   - 其他 Zone 各自在本档位的 Tier Band 内取离名义值最近的可行值。
   - 最后检查两测点之间的检查点。颈厚与孔喉对 ρ 单调，所以带内与两测点之间的检查点用区间端点夹逼：颈厚取区间最小 ρ，孔喉取区间最大 ρ，中间点不另行求值。
   
   每处改动记一条 Adjustment。
5. **不可行**：仍不可行 → 报 `E_INFEASIBLE`。错误信息附上可行的替代方案，例如"该 Zone 当前可行的 ρ 区间是 …；可改用 … 档"。

每条 Adjustment 都写进 Build Manifest，包含路径、原值、新值和原因（`neck`、`pore` 或 `cell`）。Infeasible 的退出码为 3。

**求值次数**：Resolver 只通过 `get_many` 取 Cell Properties，并把每次查询记入 Build Manifest（11.3）。典型设计（名义值可行）：欧氏查询 ≤ 6 次，全部命中种子；随形查询 4 次（Upper 与 HeelCounter 各两端）。最坏情形：欧氏 ≤ 14 次，随形 ≤ 52 次（放大 `cell_inplane` 的 8 个候选 × 4，加 Tier Band 内移动 ρ 的 16 次）。冷查询的成本见第 13 节。

### 7.4 Override

**键与格式**：
- 键是 BuildPlan 的 JSON Pointer（RFC 6901），必须逐字出现在白名单中。
- 白名单在 `buildplan` schema 中以 `x-overridable` 标记。
- P0 白名单 **[初值]**；扩充白名单只需升 BuildPlan 的 minor 版本。

| 路径 | 范围 |
|---|---|
| `/geometry/parts/0/zones/midsole/fill/cell_mm` | 8–12 |
| `/geometry/parts/0/zones/midsole/fill/rho_heel` | 0.12–0.45 |
| `/geometry/parts/0/zones/midsole/fill/rho_forefoot` | 0.12–0.45 |
| `/geometry/parts/0/zones/midsole/footbed_layer/rho_delta` | 0–0.15 |
| `/geometry/parts/0/zones/outsole/fill/rho` | 0.12–0.45 |
| `/geometry/parts/0/zones/upper/fill/rho` | 0.12–0.45 |
| `/geometry/parts/0/zones/heel_counter/fill/rho` | 0.12–0.45 |
| `/geometry/parts/0/shell/wall_thickness_mm` | 3.0–6.0 |
| `/geometry/parts/0/shell/cell_inplane_mm` | 6–10 |
| `/geometry/parts/0/transitions/lateral_width_mm` | 2–12 |
| `/geometry/parts/0/features/collar_rim/height_mm` | 1–6 |
| `/geometry/parts/0/features/welt/height_mm` | 1.5–3.0 |
| `/geometry/parts/0/features/ground_skin/thickness_mm` | 1.0–1.5 |
| `/geometry/fit/toe_allowance_mm` | 8–20 |
| `/resolution/voxel_mm` | 0.15–1.0 |
| `/resolution/simplify_tolerance_mm` | 0.01–0.2 |

**规则**：
- **非法键或值**：键不在白名单、类型错误或超出范围 → 报 `E_SPEC_OVERRIDE`（退出码 3）。
- **钉住的值不可行**：在 7.3 中报 `E_INFEASIBLE_OVERRIDE`，不提供绕过。派生的 Footbed Layer ρ（Midsole ρ + `rho_delta`）落在 Cell Properties 的定义域（ρ ≤ 0.46）之外，同样报 `E_INFEASIBLE_OVERRIDE`，无需另设规则。
- **白名单收窄**：五个 ρ 字段的范围按定义域取 0.12–0.45（ADR 0012）。收窄需升 BuildPlan 的 minor 版本；P0 尚未发布，直接写进 0.1。
- **优先级**：Override 总是优先于语义字段；Build Manifest 记录被覆盖的语义（`overridden_semantics`）。
- **LLM 投影**：Override 不出现在 `llm` 投影中。
- **形状参数**：`geometry.shape` 在 P0 不可覆盖，只能通过 DesignSpec 的语义字段修改。

### 7.5 Cell Properties（`cellprops`）

由引擎按需求值，并带持久缓存（ADR 0012）；不再有离线全表。

**求值对象**：无切面的周期 Cell 上，给定 (Lattice Family, Variant, a_s = L_s/L_n, a_t = L_t/L_n, ρ) 时的以下量，均以法向 Cell L_n 归一化，使用时乘以实际 L_n：
- 等值面阈值 c：使材料体积分数等于 ρ。
- 每个对称类的 Neck Thickness，以及其中的最小值。定义与 Printability Check 相同（10.2、ADR 0009）。
- Pore Throat：三轴逾渗瓶颈及其最小值。

欧氏 Cell 的形状是 (1, 1)，L_n = C。P0 只有 Gyroid network。只在 Resolver 真正用到的点上求值，不插值。

**c(ρ)**：c 与 Cell 形状无关（各轴独立缩放是线性映射，体积分数不变），所以 c 不进缓存，退化为一张一维数据表 `gyroid_c.v1`：
- ρ ∈ [0.10, 0.50]，步长 0.001，在 256³ 周期栅格上二分求得；
- 查询时在相邻表项之间线性插值；
- 场合成（8.3）对每个点调用同一个函数，逐点确定。

**定义域（设计约束）**：ρ ∈ [0.12, 0.46]，a_s、a_t ∈ [0.5, 6]。
- 域外查询按不可行处理（7.3 第 1 步）。
- 求值器先做拓扑断言：8 个 f 极大值与 12 个一阶鞍点都在材料相中，材料相与空隙相各为一个三轴逾渗的周期分量。断言失败视为域外。
- **覆盖要求**：对 7.4 白名单内 `wall_thickness_mm` 与 `cell_inplane_mm` 的任意组合，7.1 第 9 步两端的 (a_s, a_t) 都必须落在定义域内；白名单角点与 fixture 的形状同样如此。由单元测试断言，测试使用 G1–G4 fixture 实测的 P(0)、P(1)、P(0.5)。
- ρ 上界取 0.46 而不是 0.45：Footbed Layer 在 `rho_delta` 覆盖到 0.15 时派生 ρ 可达 0.455。ρ 下界取 0.12：soft 的 Tier Band 下沿是 0.125。

**方法与精度**：
- Neck Thickness：`neck-cell-dirc@1`。
  - 拓扑取自 Gyroid 的解析临界点：节点是 f 的极大值（每个 Cell 8 个），Ligament 是从 12 个一阶鞍点出发的最陡上升对，按节点距离分成对称类。各轴缩放不改变临界点的参数坐标，所以任何形状都是同一张 12 根 Ligament 的网络。没有粗阶段，也就没有粗阶段的漏检与虚假输出。
  - 每类取一根代表，在周期场 φ（8.3 的归一化式）上跑 Direction C 细阶段（10.2 第 3 步）。平面搜索半径从 0.3 mm 放宽到该类节点距离的 0.3 倍，以容纳解析路径与 r 脊线之间的偏差。
  - 端部受限（junction）的类：读节点处的值，约定与 Printability Check 相同。
- Pore Throat：`pore-perc@1`。在周期 Cell 栅格上算空隙的有符号距离，取三轴逾渗的最宽路径瓶颈。体素 h_p = L_n/100；栅格超过 1500 万体素时按只依赖形状的固定规则放粗到 1500 万以内。栅格瓶颈系统性偏低，结果是保守的，误差约一个 h_p。
- 精度：pass 类 Neck Thickness 与解析参考方法之差 ≤ 0.021 mm（Direction C 近阈值实测的最大误差，期望实际 ≤ 0.01 mm）。
- **测试基准（不参与求值）**：解析参考方法（解析梯度加 Newton 求山口，误差 ≤ 0.01 mm），由 15.1 的单元测试使用。
- 精度账与 `neck_margin_mm` 的关系见 9.1。

**键与查找顺序**：`key = SHA-256(canonical_json({schema: "cellprops-key/1", family, variant, a_s, a_t, rho, methods}))`，序列化方式同 6.3；a_s、a_t 量化到 1e-4，ρ 量化到 1e-6。查找顺序：
1. **种子** `src/shoegen/spec/data/cellprops.seed.v1.jsonl`：只读，按 key 排序，计入 Build Manifest 的数据表登记。记录中的 `methods` 必须等于引擎当前的方法 id，否则加载失败。种子是数据表，跨平台权威。
2. **本地缓存** `$SHOEGEN_CACHE_DIR/cellprops/<namespace>/<key 前两位>/<key>.json`，默认 `~/.cache/shoegen`。`namespace = SHA-256(方法 id, 求值器源码摘要, uv.lock sha256, 平台标签)`。记录含输入、输出、方法 id、引擎 git sha、耗时与记录体自身的 sha256；读到损坏记录时重算并覆盖。
3. **计算并写入本地缓存**：先写临时文件再原子改名。同一 namespace 下并发写入的内容逐位相同。

方法实现改变时升方法 id（如 `neck-cell-dirc@2`），种子必须重生成并升版本。测试一律用临时目录作缓存。

**确定性**（ADR 0006）：求值器是 key 的纯函数：单线程数值核心，固定容差与迭代次数，numba 不用 `parallel` 与 `fastmath`，并列时取最小索引。`get_many` 的冷查询按 key 排序，用固定的 ≤ 4 个 worker 计算，返回顺序与输入一致，结果与 worker 数无关。

| 场景 | 保证 |
|---|---|
| 同平台、同引擎版本：缓存值与重算值 | 逐位相同 |
| 同平台：冷缓存与热缓存的 Build | BuildPlan、plan_hash 与 Print Mesh 逐字节相同 |
| 跨平台：种子命中的查询 | 值相同（种子权威），Resolver 判定相同 |
| 跨平台：种子之外的冷查询 | 差异 ≤ τ：颈厚 τ_n = 0.002 mm，孔喉 τ_p = h_p·L_n。只有查询值离判定阈值不到 τ 时，BuildPlan 才可能不同；Build Manifest 对这类约束标 `near_threshold: true` |
| `--plan` 只校验模式 | 判定放宽 τ（7.1） |

**种子的生成与守门**：`scripts/gen_cellprops_seed.py`，在 memguard 6 GB 下运行，≤ 4 worker。
- 欧氏部分全覆盖：形状 (1, 1)，ρ 取定义域内 0.005 取值格，共 69 点。
- 随形部分为"录制闭包"：在一个规格矩阵上跑 Resolver 阶段 A 与阶段 B 第 8–9 步（不跑探测），用记录型 Provider 收集全部查询；矩阵含 6 份形状 fixture、Openness 3 档、G1–G5 与 Tuning Table 自检的 spec。预计 ≤ 400 条记录、约 300 KB，生成约 20 核·分钟。
- 守门（pre-merge 必跑）：(a) 种子在参考平台上抽样重算逐位一致；(b) G1–G5 黄金回归与 Tuning Table 自检的全部查询都能命中种子；(c) 种子的 sha256 与数据表登记一致。
- 种子的参考平台写在种子头部（开发机 macOS arm64）。其他尺码、其他 Override 下发生的冷查询进入用户本地缓存，不回写种子。

**冷热成本**：冷查询每次 ≤ 3 s（单核）；热查询 ≤ 5 ms。典型 Resolver 冷启动只需 4 次随形查询，最坏 52 次，4 个 worker 约 40 s（第 13 节）。

### 7.6 局部探测（Local Probe）

在 Resolver 阶段 B 的第 12 步，定稿之后，于真实合成场上执行体素类检查的闸门（ADR 0012）。探测不修改 BuildPlan，只放行或判失败，所以结果可以引用 plan_hash。预检在前：构建体积不合格的鞋不必花探测的时间。

**各 Build 的默认模式**：

| Build | 默认 |
|---|---|
| Print Build（含 `--plan`） | `gate` |
| Preview Build | `off`：守住 ≤ 1 分钟；探测不改 BuildPlan，所以 plan_hash 仍与 Print Build 相同 |
| Crop Build | `off`：Crop Build 本身就是该区域的完整校验 |
| Coupon Build | 不适用：没有 Shape Bundle |

CLI 的 `--probes gate`、`warn`、`off` 三种模式（12.1）。`warn` 把探测失败降为 soft 警告并继续，由整鞋 Printability Check 裁决，用于调试。Build Manifest 记录所用模式。

**探测窗口：halo，不裁切**。每个探测是一个 Direction C 分块（核心加 halo），对齐整鞋全局栅格：
- **栅格**：h_probe = 0.25 mm **[实现]**，不随 `voxel_mm` 改变；原点与偏移同 6.1 的全局栅格，核心盒的角点对齐全局体素索引。`voxel_mm` > 0.25 时，Print Build 的体素类检查是非权威的（10.2），探测反而成为唯一权威的体素类检查。
- **核心**：默认 16 mm 立方（64 体素）；Welt 窗口 32 × 16 × 16 mm，长轴取最接近 Footbed Edge 切向的坐标轴；鞋底柱 16 × 16 mm × （当地鞋底高度 + 4 mm）。
- **halo**：40 体素（10 mm）；精确性证书失败时回退到 64 体素（16 mm），回退后仍失败报内部错误（退出码 1）。
- **场**：在核心加 halo 上按块生成真实合成场（8.3），不建任何整体浮点栅格。
- **不裁切**：窗口面不是 Shoe Body 边界，也不产生 Cut Edge。核心内的 u 鞍点由本窗口拥有，d、u、上升路径与不分块计算逐位相同，所以核心内 Ligament 的颈厚与整鞋校验同源。ToMATo 与截面检验在探测内只看局部盆地图，靠近窗口边缘的簇身份可能与全局不同，因此只预期一致，不承诺逐位一致。

**选取规则**（Archetype 数据 `archetypes/low_top_slip_on.v1` 的 `probes` 段，**[初值]**）。中心只依赖 Shape Bundle（Landmarks、Surface Parameterization、网格曲率）与 BuildPlan 的几何参数（Wall Thickness、`extent_x_ratio`、层厚），与 ρ 无关。Shell 窗口的中心取 Last 点沿法向外移 Wall Thickness/2；Collar 端窗口的核心上缘比 Collar Line 高 4 mm。

| 类别 | 窗口 | 中心 | 主要覆盖 |
|---|---|---|---|
| 鞋底柱 | S-heel | `heel_stack_point` 的 (x, y) | Skin、Outsole 晶格层、Outsole 与 Midsole 的 Grading 带、后跟 Midsole 芯部、Footbed Layer 的 Grading、后跟凹窝 |
| 鞋底柱 | S-arch | x 取两测点中点，y 取该截面内侧 Footbed 半宽的 1/2 | 纵向 Grading 中段、足弓处倾斜的 Footbed |
| 鞋底柱 | S-ff | `forefoot_stack_point` | 前掌 ρ；最薄 Midsole；Toe Spring 起点 |
| 鞋底柱 | S-toe | x = x_heel + 0.90·Last Length，中线 | Toe Spring 段倾斜的 Ground Surface 与 Footbed |
| Welt | W-heel | `footbed_edge` 上 s = 0 的点，沿外法向移 (Wall + e)/2，z 以 z_W 为中心 | 硬切换与 Welt 熔合；后跟侧壁；s = 0/1 的参数化接缝 |
| Welt | W-med | `footbed_edge` 上离 `ball_line` 内侧端最近的点 | 前掌内侧最宽处；最宽周向 Cell |
| Welt | W-lat | 同上，取外侧端 | 前掌外侧 |
| Welt | W-toe | `footbed_edge` 上 x 最大的点 | 鞋头 Welt；Toe Spring 侧壁；鞋头水平曲率 |
| Collar 端 | C-heel | `collar_line` 上 s = 0 | Rim；HeelCounter ρ；最窄周向 Cell；参数化接缝 |
| Collar 端 | C-throat | `throat_point` | Rim；Upper；L(s) 最大处 |
| Collar 端 | C-ankle-med / C-ankle-lat | `ankle_dip_medial` / `ankle_dip_lateral` | Rim 的下凹段；靠近 HeelCounter 边界 |
| Transition | T-med / T-lat | 平面 x = x_heel + `extent_x_ratio`·Last Length 与 τ = 0.5 环的内侧 / 外侧交点 | Upper 与 HeelCounter 的 Transition（ρ 插值带） |
| 数据驱动 | K-max | τ ∈ [0.15, 0.85] 内，Last 平均曲率绝对值（8 mm 测地平滑）最大的顶点 | 最大曲率处的实际三维 Cell 变形 |
| 数据驱动 | D-max | 同一范围内，参数化畸变最大的顶点 | 随形 Cell 的剪切 |

- 鞋底柱、Welt、Collar 端窗口的核心均按上文取默认尺寸；数据驱动两项与已选中心的距离须 ≥ 24 mm。
- 按表中顺序处理，与先前中心距离 < 12 mm 的探测舍去，记入 Build Manifest；数据驱动项并列时取 Last 顶点索引最小者；探测总数上限 16 **[初值]**。

**窗口检查与外表面语义**。每个窗口按 10.2 执行体素类检查，只统计核心。窗口外表面不是 Shoe Body 边界，所以颈厚的站点排除与端部受限判定只看真实的 Shoe Body 边界与 Skin、Rim、Welt。不做网格类检查：探测不提取网格。

| 检查 | 窗口内做法 | 窗口外表面的处理 |
|---|---|---|
| 颈厚与薄壁 | 10.2 第 1–5 步，只统计 u 鞍点在核心内的 Ligament；体素归属只在核心内计算 | 由 halo 保证精确，不涉及外表面 |
| 裁切残边与实体接触 | 同 10.2，只报告；同时作为定稿 [初值] 阈值的额外样本 | — |
| 悬挂材料 | 1.0 mm 闭球开运算 | 开运算结果中，与窗口外表面或最大分量相连的材料视为有依托（乐观） |
| 困粉 | 10.2 的 V_2、V_4 与 d ∈ {2, 4} | 窗口外表面外侧计入 E，视为外部（乐观） |
| Island | 不触及窗口外表面的实体分量 | 体积 ≥ 10 mm³ 判失败；更小的只计数 |
| 封闭腔 | 不触及窗口外表面、也不连通 E 的空隙分量 | 判失败 |

**判定（只做闸门，不触发 Adjustment）**：

| 情况（核心内） | 结果 | 错误码（退出码 5，class `printability`） |
|---|---|---|
| 内部薄壁材料 ÷ 核心 Lattice 材料 > `probe_thin_fraction` | 失败 | `E_PROBE_THIN_WALL` |
| 困粉体积 ÷ 核心空隙 > `probe_trapped_fraction` | 失败 | `E_PROBE_TRAPPED_POWDER` |
| 悬挂材料 > `probe_hanging_mm3` | 失败 | `E_PROBE_HANGING_MATERIAL` |
| 不触及窗口外表面、体积 ≥ 10 mm³ 的 Island | 失败 | `E_PROBE_ISLAND` |
| 封闭腔 | 失败 | `E_PROBE_CAVITY` |
| 任何内部薄颈（> 0 但不超阈值），或困粉 > 0 | soft 警告，记入 Build Manifest | — |
| 证书在回退 halo 之后仍失败 | 内部错误 | `E_INTERNAL`（退出码 1） |

阈值是 Profile 字段（9.1），初值 1 %、1 %、1 mm³。探测的 1 % 是核心内的局部占比，比全鞋严格，是有意的保守；误伤的出口是 `--probes warn`。

- **错误对象**：失败可归因于某个 Zone 的 ρ 时（薄壁、困粉），带 `json_pointer`（杠杆参数）、`expected` 与 `suggestion`。`suggestion` 由求值器算出：把实测缺口（例如 1.05 − 探测最小内部颈厚）加到周期模型的要求上，求出 Tier Band 内的 ρ，或建议改档；消息中附探测 id、位置与该 Zone 的周期预测值。结构性失败（悬挂材料、Island、封闭腔）没有杠杆参数，只给位置。
- **杠杆参数**（只用于 `suggestion`）：取失败 Ligament 鞍点所在的 Zone，在带内时取带两侧中 ρ 较低的一侧。Midsole 芯部：x ≤ 两测点中点用 `rho_heel`，否则用 `rho_forefoot`；Footbed Layer 本身用 `rho_delta`；Outsole、Upper、HeelCounter 用各自的 `rho`。杠杆参数被 Override 钉住时，`suggestion` 写明"该值已被 Override 钉住"。
- **为什么不产生 Adjustment**：(1) Preview Build 不跑探测，探测若改动 BuildPlan，就会破坏 6.3 的 plan_hash 共用；(2) 已知会让周期模型失效的机制都已通过设计变更消除（ADR 0010、0011），剩下的探测失败更可能是引擎缺陷或 [初值] 阈值不当，自动提高 ρ 会把它们藏起来；(3) 闸门没有搜索 Tier Band，不能称为 Infeasible，所以不报 `E_INFEASIBLE_*`（退出码 3），按 hard 级 Printability Check 提前失败处理，与 `E_BUILD_VOLUME` 预检同类。有界的 Adjustment 循环是 P1 选项（18.2）。

**预算**：≤ 60 s、峰值 ≤ 2 GB，固定 3 个 worker **[实现]**，不随内存或负载自适应；窗口之间并行，按窗口序号合并，结果与 worker 数无关。探测在网格阶段之前完成并释放全部数组，不与第 13 节的网格阶段同时驻留；探测时间计入 Print Build 的 10 分钟。超预算时通过 Archetype 数据减少窗口数或缩小核心，不改代码。

**结果缓存**（可选，**[实现]**）：键为 (plan_hash, 探测集数据 sha256, 引擎版本, 平台)，命中时跳过探测。

**覆盖与残余风险**见 18.1。

## 8. Zone、晶格与几何内核

### 8.1 区域与 Zone 推导（ADR 0004）

**距离量**（都由 libigl 一类的点到网格距离计算）：
- d_Last：到 `last.ply` 的有符号距离，Last 外侧为正。
- d_G：到 `face_sets.ground` 的无符号距离。
- d_F：到 `face_sets.footbed` 的无符号距离。

**区域**：
- **Shell** = {0 < d_Last ≤ Wall Thickness，且最近点落在参数面上，且 0 ≤ τ ≤ 1}。最近点落在 Footbed 面上的点不属于 Shell。
- **Shoe Body** = (Sole Envelope ∪ Shell) − Last。

**层厚记号**（均取自 BuildPlan）：
- t_skin = `features.ground_skin.thickness_mm`
- t_out = `zones.outsole.thickness_mm`
- t_fbl = `zones.midsole.footbed_layer.thickness_mm`

覆盖 t_skin 时 t_out 不变，Outsole 晶格层整体上移。

**坐标系分界（Welt Surface，ADR 0011）**：z_W(p) 为 `footbed_edge` 上离 p 最近点的 z。Shoe Body 中 z > z_W(p) 且最近点不在 Footbed 面上的点（含 Sole Envelope 高出 Footbed 的侧壁）属于 Shell 坐标系，按 x 判为 HeelCounter 或 Upper，用随形晶格；其余点属于鞋底坐标系，按下表前三行判定。表中"在 Sole Envelope 内"三行只适用于 z ≤ z_W(p) 的点。随形坐标在 d_Last > Wall Thickness 处照常延续（法向 Cell 仍为 Wall Thickness），不产生新生 Ligament。Welt 是实体特征，不是 Zone（同 Rim）；其内的点仍按所在坐标系判 Zone。

**每一点恰属一个 Zone**，按下表从上到下判定，取第一个满足的：

| 条件 | 所属 |
|---|---|
| 在 Sole Envelope 内，且 d_G ≤ t_skin | Outsole 的 Skin |
| 在 Sole Envelope 内，且 t_skin < d_G ≤ t_skin + t_out | Outsole 的晶格层 |
| 在 Sole Envelope 内，其余 | Midsole（其中 d_F ≤ t_fbl 的部分是 Footbed Layer） |
| 属于 Shell 坐标系，且 x − x_heel ≤ `extent_x_ratio` × Last Length | HeelCounter |
| 属于 Shell 坐标系，其余 | Upper |

**Skin 的范围**：
- Outsole 与 Skin 的厚度都沿 Ground Surface 的法向计，Toe Spring 段同样如此。
- Sole Envelope 侧壁、Footbed、Shell 内外表面都不加 Skin；Welt 不是 Skin。
- 鞋底侧壁在 Welt 以下保持开放晶格，作为排粉通道（ADR 0007）。
- 唯一的例外：Skin 在 Ground Surface 边界处会露出约 t_skin 高的截面。

**Midsole 主体厚度**：T(x, y) = z_F(x, y) − t_fbl − (z_G(x, y) + t_skin + t_out)，沿竖直方向量。其中 z_F、z_G 分别是 Footbed 与 Ground Surface 在该 (x, y) 处的高度。

### 8.2 Grading 与 Transition

**通则**：

1. 所有带状区都以对应边界为中心，半宽 W/2；插值权重 w 按到该边界的距离线性变化。w 只是 ρ 的插值权重，不是 Cell 的混合权重。
2. 鞋底不做 Cell 的空间变化：Outsole 晶格层、Midsole、Footbed Layer 共用一个 Cell C，Grading 与 Transition 只改变阈值（ρ）（ADR 0002、ADR 0010）。任何情况下都不得把位置相关的 Cell 代入坐标。
3. ρ 的变化只改变阈值，不改变周期。
4. 多条带重叠时，按 8.1 表的顺序，先列出的 Zone 优先。

| 位置 | 边界 | 宽度 W | 做法 |
|---|---|---|---|
| Outsole ↔ Midsole | d_G = t_skin + t_out | min(`vertical_max_mm`, t_out)，默认 3 mm | w_Outsole 在边界 ± W/2 内（默认 d_G ∈ [2.7, 5.7]）从 1 线性降到 0；ρ 在带内线性插值（ρ = ρ_Midsole(x) + (ρ_Outsole − ρ_Midsole(x)) · w_Outsole），Cell 不变 |
| Midsole 内 Footbed Layer 的 Grading | d_F = t_fbl | t_fbl，默认 4 mm；不受 `transitions/*` 影响 | w_FBL 在 d_F ∈ [t_fbl/2, 3·t_fbl/2]（默认 [2, 6]）内从 1 线性降到 0；ρ = ρ_Midsole(x) + `rho_delta` · w_FBL；d_F ≤ t_fbl/2 处是均匀的 ρ_Midsole(x) + `rho_delta`，Cell 仍为 C |
| Upper ↔ HeelCounter | x = x_heel + `extent_x_ratio` × Last Length | `lateral_width_mm`，默认 6 mm | Cell 相同，只插值 ρ |
| 跨坐标系（鞋底坐标系 ↔ Shell 坐标系） | Welt Surface（z = z_W） | 无过渡带 | 两侧晶格在 Welt 的中面硬切换，接缝藏在实体 Welt 内（8.3、ADR 0011）。不做场混合：两张不同网络的任何混合都会新生颈厚从 0 起的 Ligament，并使密度在 w≈0.5 处跌到约 60 % |

Midsole 厚度 T < C 的区域不做特殊处理：它只是不足一个 Cell 的晶格，Cell 与 ρ 都不变，只在 7.3 归入薄层孔喉级别。

### 8.3 场

**欧氏 Gyroid**：
- f_C(p) = sin X cos Y + sin Y cos Z + sin Z cos X，其中 (X, Y, Z) = 2π·p/C。
- 材料相为 {f > c}。

**随形 Gyroid**：
- 坐标为 (X, Y, Z) = (2π·N·s, 2π·t_mm / cell_inplane, 2π·d_Last / Wall Thickness)。
- (s, t_mm) 按 5.5 的方法查询。

**归一化**（ADR 0002）。本节用 φ 表示归一化后的场，N 只表示周向 Cell 数 `circumferential_cells`：

φ_X = clamp((c_X − f_X) / max(|∇f_X|, g_min), −δ, +δ)

- 材料相为 {φ < 0}。
- g_min = 0.5 · 2π / Cell_min **[实现]**（鞋底 Cell_min = C，即 g_min = 0.5 · 2π / C）；δ = 1.0 mm **[实现]**。
- |∇f_X|：欧氏侧用解析式；随形侧当前在体素栅格上做中心差分，分块时 halo 加 1，以保证确定性。
- **点求值要求（设计约束）**：最终合成场（坐标系切换、与 Rim/Skin/Welt 的平滑并集、随形映射，含 φ 的零集）必须能在任意（非栅格）点上确定性求值（ADR 0006），因为 10.2 的细阶段需要对零集做 Newton 投影并在体素之间采样。随形侧的 |∇f_X| 因此需要一个可在任意点求值的等价实现（例如对 (s, t_mm, d_Last) 映射做解析或点上有限差分，不依赖体素栅格）；同一点的结果不得依赖分块。

**鞋底场**：

φ_sole = φ_C，阈值 c(ρ(p))，ρ(p) 按 8.2 逐点取值，c 取自 `gyroid_c.v1`（7.5），与 Cell 形状无关。

鞋底内部没有 Cell 混合，因此不产生新生 Ligament（ADR 0010）。

**坐标系切换与 Welt**（ADR 0011）：
- φ_lattice = φ_shell（z > z_W(p)）或 φ_sole（z ≤ z_W(p)）。两侧参数按该点所属坐标系与 Zone 取值（8.1）。切换面上不做混合，φ 在此不连续，由其中的实体 Welt 覆盖。
- Welt = {|z − z_W(p)| ≤ `welt.height_mm`/2，且 p 不在 Footbed 正下方（最近的 Last 点不在 Footbed 面上）} ∩ Shoe Body，为实体；自 Footbed Edge 一直延伸到 Shoe Body 外表面。
- 点求值：z_W(p) 与最近点判定用 5.5 的最近点查询，对任意点确定性可求。

**Rim 与 Skin**：
- Rim = {t_mm ≥ L(s) − `height_mm`，0 < d_Last ≤ Wall Thickness}，为实体。
- Skin = Outsole 中 d_G ≤ `thickness_mm` 的部分，为实体。

**合成**：

材料实体 M = (Lattice ∪ Rim ∪ Skin ∪ Welt) ∩ Shoe Body

- 并与交都用光滑运算，光滑半径 0.3 mm **[初值]**。
- Print Mesh 与 Preview Mesh 都是 M 的等值面。

### 8.4 网格提取

- 分块尺寸固定，块与块之间共享一层体素；
- 各块独立做 marching cubes，再按坐标精确合并，合并顺序固定；
- 体素原点偏移半个体素。

### 8.5 Island 清理与简化

**Island 清理**：在简化之前的 MC 网格上进行。
1. 按边连通性拆分出各个壳，计算每个壳的有符号体积：V > 0 为实体壳，V < 0 为空腔壳。
2. 只删除最大实体壳之外、V < 10 mm³ **[初值]** 的实体壳。任何空腔壳都不删除。
3. Build Manifest 按 Zone 汇总被删除的数量与体积。
4. 第 10.2 节的体素类指标使用扣除已删 Island 之后的材料相。

**Print Mesh 简化**：
- 容差 0.05 mm，使用确定性实现。
- 简化后做双向采样，把实测最大偏差写进 Build Manifest。
- 偏差超出容差时，退回更保守的简化比例重做。

**Preview Mesh**：
1. 以 `preview_voxel_mm` 单独提取；
2. 简化到 `preview_max_triangles` 以内；
3. 按顶点位置求 Zone 标签并着色（调色板放在数据表中）。

着色必须在简化之后进行，因为简化不保留顶点属性。Print Build 也按同样方式产出 GLB。

### 8.6 左脚

- 左脚 = 右脚网格的镜像：y → −y，同时翻转三角形绕序，顶点颜色照搬。
- 镜像之后，才对 GLB 做坐标变换（11.2）。
- 因此一双鞋要么两只都成功，要么都失败。

---

## 9. ManufacturingProfile

### 9.1 `pbf-tpu-generic@1`

文件为 `profiles/pbf-tpu-generic.v1`，不绑定具体机器。

| 字段 | 值 |
|---|---|
| 工艺 | 粉末床熔融 TPU（取 MJF 级约束） |
| 最小壁厚 | Neck Thickness 1.0 mm（ADR 0009） |
| Resolver 颈厚余量 `neck_margin_mm` | 0.05 mm **[初值]**，与 10.2 的测量精度一致；Resolver 放行的设计，不会因测量误差在校验中失败。依据：近阈值实测最大误差 0.021 mm（Printability Check），加上 Cell Properties 求值误差 ≤ 0.021 mm（同核同法，期望 ≤ 0.01 mm）；Resolver 在查询点上直接求值，不再有插值误差，端部受限 Ligament 与校验同约定；合计 ≤ 0.042 mm，余量至少剩 0.008 mm。余量其余部分留给真实合成场相对周期 Cell 的偏差，由局部探测抽查。求值器误差若实测 > 0.025 mm，回到设计讨论，不擅自加大余量。不要调大：余量每加 0.1 mm，大约失去一个 Openness 档位 |
| 最小 Pore Throat | 薄层 ≥ 2.0 mm；芯部 ≥ 4.0 mm（级别划分见 7.3，ADR 0007） |
| hard 阈值 | 薄壁：材料体积占比 1%；困粉：Shoe Body 内空隙体积占比 1%（均可配置） |
| 局部探测阈值 | `probe_thin_fraction` 1 %；`probe_trapped_fraction` 1 %；`probe_hanging_mm3` 1 mm³ **[初值]**（7.6），存放在 Profile 中 |
| 构建体积 | 370 × 274 × 375 mm；6 种轴对齐朝向中任意一种放得下即通过 |
| 悬垂限制 | 留空（粉末床不需要） |
| 摆放与嵌套 | 不在 P0 范围，交给服务商 |

### 9.2 选定服务商之后

实际打印前，为选定的服务商新增一个专属 Profile。它是纯数据文件，并需要对照该服务商的设计指南复核一遍（第 16 节）。

部分 SLS 服务商的 TPU 指南要求"晶格间隙"≥ 5 mm。这是服务商的口径，大致相当于本规格的 Pore Throat。按这个要求，Shell 与 Footbed Layer 都不可行，所以优先选 MJF。

---

## 10. 校验

### 10.1 Resolver 级校验

有 DesignSpec 输入时，执行第 3 节的 DesignSpec 校验和 7.3 的约束求解。

所有 Build 都执行构建体积预检与资源预检（第 13 节）：
- Print、Preview 与 Crop Build：在阶段 B 使用 Shape Bundle 的包围盒与体积。
- Coupon Build：没有 Shape Bundle，使用 12.3 规定的固定尺寸。

Print Build（含 `--plan`）在阶段 B 执行局部探测（7.6）；Preview 与 Crop Build 默认不执行；Coupon Build 不适用。

`--plan` 路径的处理见 7.1。

### 10.2 Printability Check

在 Print Build、Crop Build 与 Coupon Build 中执行。

**网格类检查**：在最终网格上执行。

| 检查 | 级别 | 定义 |
|---|---|---|
| 网格有效性 | hard | 每条边恰被两个三角形共享，且方向一致；没有退化面；没有自相交（简化后用 BVH 做三角形两两相交检测） |
| 连通分量 | hard | 实体壳数为 1（按体积符号统计，例如 manifold3d 的 `decompose()`） |
| 无封闭腔 | hard | 空腔壳数为 0 |
| 构建体积 | hard | 6 种轴对齐朝向中至少有一种放得下；把可行的朝向记入 Build Manifest |

**体素类检查**：
- 在检查栅格上执行，h_check = `voxel_mm`。
- 栅格从最终合成场分块重采样，带 halo。
- 在释放场与网格之后执行。
- 连通性用跨块并查集，做全局 26 连通标记。
- **粗体素时的处理**：Neck Thickness 的精度要求（≤ 0.05 mm）只在 h_check ≤ 0.25 mm 时适用。`voxel_mm` > 0.25 mm 的 Print Build 与 Crop Build 照常执行网格类检查；体素类检查的结果一律降为 soft，Build Manifest 标记 `authoritative: false`，产出照常写出。15.3 的 0.5 mm 整鞋回归就按这种方式运行。

| 检查 | 级别 | 定义 |
|---|---|---|
| 薄壁占比 | > 1% 为 hard 失败；> 0 为 soft 警告 | 见下文"薄壁占比的计算" |
| 实体接触占比 | soft | 与"裁切残边占比"同样定义，但端部受限的 Ligament 其最小内切球位于 Skin、Rim 或 Welt 表面 1.0 mm **[初值]** 以内，而不是触及 Shoe Body 边界；只报告，不判失败 |
| 悬挂材料 | hard | 用直径 1.0 mm（= 最小壁厚）的闭球对材料做开运算，开运算结果中与最大实体分量不连通的材料体积 > 0 **[初值：> 1 mm³]** 即失败。它保证端部受限豁免不会放过"只靠细特征挂着"的材料，也能发现局部脱开的接缝（全局分量数仍为 1 时） |
| 裁切残边占比 | soft | 距 Shoe Body 边界 ≤ 0.5 mm 的 Ligament 站点中，所在 Ligament 的局部颈厚（该站点的 2R）< 最小壁厚的站点所对应的材料体积占比，另含按步骤 4 的端部受限判定归入边界的 Ligament；只报告，不判失败 |
| 困粉占比 | > 1% 为 hard 失败；> 0 为 soft 警告 | 见下文"困粉占比的计算" |

**薄壁占比的计算**（ADR 0009）。不做曲线骨架化；颈厚的定义见 ADR 0009。

1. **粗阶段**：在检查栅格（h_check = `voxel_mm`）上，由 φ 的过零点取粗表面点；d = 各 Lattice 材料体素（不含 Skin、Rim 与 Welt）到表面点的距离；u = G_σ * d，σ = 0.5 mm **[初值]**（自适应 σ，如局部最大 d 的 0.5–0.7 倍，是可能的改进）。
2. 在材料体素上对 u 做 26 邻域最陡上升分水岭，并用持续性（ToMATo）简化，阈值 **[实现]**。相邻且截面检验通过的每一对簇是一根 Ligament：两个节点是两个 u 极大值，路径是从 u 鞍点出发、到两端极大值的最陡上升折线（节点到节点）。
3. **细阶段**：对每根 Ligament，在路径周围的管状区域内用 φ 构造表面点云（间距 hs 约 0.07–0.16 mm **[实现]**，Newton 投影到 φ = 0），r = 到点云的距离；沿路径逐站点（节点到节点全程）在垂直于路径切线的平面内取最大 r，记为 R(s)，在最小值附近细化。Neck Thickness = 2 · min R(s)。没有内部山口时，最小值落在节点处。
4. 站点有效性：距 Shoe Body 边界 ≤ 0.5 mm、或在 Lattice 区域（不含 Skin、Rim、Welt）之外的站点不参与；一根 Ligament 若没有任何可用站点，不参与薄壁判据，其材料只计入"裁切残边占比"。**端部受限判定**：若一根 Ligament 的 min R(s) 落在与排除区相邻的端部站点，且该处内切球触及 Shoe Body 边界（r ≥ 到边界距离 − 0.1 mm）或位于 Skin / Rim / Welt 表面 1.0 mm **[初值]** 以内，即 R(s) 朝边界或实体单调减小，则该 Ligament 不参与薄壁判据，其材料计入"裁切残边占比"（边界）或"实体接触占比"（实体），均为 soft。依据：此类读数是被边界切断的楔或擦碰实体的桥，不是两节点间的山口；Ligament 自身的山口在内部站点另有读数。不能只靠距离排除站点：桥向实体单调变窄，最小值总落在排除带边缘；悬挂材料检查（hard）兜住这条豁免，防止它放过只靠细特征挂着的材料。这些阈值（1.0 mm、0.1 mm、1 mm³）都是首次估计，见 20。
5. 每个 Lattice 材料体素归属于最近的 Ligament 路径；节点处并列时，归属于 Neck Thickness 较大者，再并列取索引最小者。
6. 分块：核心区固定为 128³ 体素、按固定顺序遍历；默认 halo 10 mm（40 体素）**[实现]**。场按块生成，不保留全局浮点栅格。每个块对每个体素做精确性检查：经上升到达的盆地极大值必须可证明与不分块时相同。检查失败的块改用 16 mm halo 重算；重算后仍有违例则为内部错误。块不输出成品 Ligament，而是按盆地对输出记录：计数、最佳穿越点（鞍点，平局规则与不分块相同）、上升路径与最大 d。父进程按全局盆地对编号精确合并，再在全局盆地图上做持久性简化与截面检验。每个被选中的鞍点恰好归属一个块。结果必须与不分块逐位一致。细阶段对各 Ligament 相互独立，可并行。
7. 薄壁占比 = 颈厚 < 最小壁厚的 Ligament 所占材料体积 ÷ Lattice 材料总体积（不含 Skin、Rim 与 Welt）；已按步骤 4 归入裁切残边或实体接触的 Ligament 不计入分子。
8. 警告中附带前 20 个 **[实现]** 聚集区的位置与体积。

**困粉占比的计算**：

1. 按 7.3 的孔喉级别，把 Shoe Body 内的空隙 V 分成薄层区 V_2 与芯部区 V_4；每个空隙体素按其所在位置的 Zone 与权重判定级别。
2. 取 V 再并上 Shoe Body 外部一圈厚 d 的外壳 E。Crop Build 与 Coupon Build 的裁切面外侧也计入 E。
3. 对每个 d ∈ {2.0, 4.0}：
   - 在整个 V ∪ E 上，用直径 d 的闭球做开运算，得到 O_d。闭球的判定是：体素中心到球心的距离 ≤ d/2。
   - 从 E 出发，对 O_d 做全局 26 连通重建，得到 R_d。
4. 困粉体积 = |(O_2 − R_2) ∩ V_2| + |(O_4 − R_4) ∩ V_4|。
5. 困粉占比 = 困粉体积 ÷ |V|。

困粉只计"d 球放得进、但出不去"的空隙；任何 d 球都覆盖不到的角隅空隙不计入。粉末可以跨越级别边界排出，例如薄层的粉经由芯部排出。

**精度与检出要求**：
- 参考颈厚 ≤ 1.5 mm 的 Ligament，测量误差 ≤ 0.05 mm；其余（含受节点限制的 Ligament，其节点约定带来约 0.04 mm 差异）≤ 0.2 mm。
- 检出：参考颈厚 ≤ 1.5 mm 的 Ligament 100% 检出，且不出现读数低于最小壁厚 + `neck_margin_mm`（即 1.05 mm）的虚假输出。
- 对照基准是：对 G1 各 Zone 的单个周期 Cell，按同一定义（山口定义）用解析梯度加 Newton 求出的参考值（不依赖细栅格，估计误差 ≤ 0.01 mm）。h_check = 0.25 mm 的结果与之比较。
- 验证必须覆盖每个 Zone 的 Cell 形状，含随形部分两端的 Cell 形状；鞋底 ρ 过渡带（Outsole↔Midsole 与 Footbed Layer 的 Grading）以带两端 ρ 的周期 Cell 真值，加缓变 ρ 平板（以局部 ρ 的周期真值作近似参考，容差 0.05 mm）验证。
- 局部探测在窗口内执行本节的体素类检查，窗口外表面不是 Shoe Body 边界（7.6）。
- 由 15.1 的单元测试保证。

### 10.3 Donning Check

- 级别为 soft。
- Collar 开口周长 ÷ Long Heel Girth 估值 < 1.00 **[初值]** 时发出警告。
- 阈值由 `heel-collar` 裁切件的试穿结果标定。

### 10.4 Preview Build

- 只做 10.1 的检查，不跑几何 hard 校验。
- Build Manifest 标记 `authoritative: false`。

---

## 11. 输出契约

### 11.1 输出目录

```
<out>/
  manifest.json                  # Build 开始时写入，结束时原子替换
  buildplan.json
  shape/                         # 本次 Build 用到的 Shape Bundle
  right.body.stl  left.body.stl  # Print Build：{side}.{part}.stl
  right.body.glb  left.body.glb  # Print Build 与 Preview Build 都会产出
  crop.<region>.stl              # Crop Build
  coupon.cushioning.<tier>.stl   # Coupon Build
```

- **目录检查**：`-o` 目录非空时报错（退出码 2）。传 `--force` 时，先清理旧的 `*.stl`、`*.glb`、`manifest.json`、`buildplan.json` 与 `shape/`。
- **收尾顺序**：
  1. 所有产物先在 `<out>/.tmp/` 中写好；
  2. 校验全部通过后逐个改名；
  3. 最后把 `manifest.json` 原子替换为 succeeded。
  
  中途失败时，删除 `.tmp/` 和已改名的产物。失败的 Build 不留下任何 STL 或 GLB。

### 11.2 文件格式

- **STL**：二进制，单位 mm，引擎坐标系，文件头不含时间戳。
- **GLB**：
  - 单位为米，+Y 向上，(x_g, y_g, z_g) = 0.001 × (y_e, z_e, x_e)；
  - 顶点颜色为所属 Zone 的颜色。

### 11.3 Build Manifest

带 `manifest_version: "0.1"`。schema 可通过 `shoegen schema --target manifest` 导出，用 if/then 表达不同状态下的必填字段。

**`status = running`** 时，只要求以下字段：

| 字段 | 内容 |
|---|---|
| `manifest_version`、`status`、`kind` | — |
| `provenance.engine` | 版本、git sha、dirty 标记 |
| `inputs` | spec 或 plan 文件的 sha256 |

读取方遇到 `status = running`、且已没有进程持有的 `manifest.json` 时（例如进程崩溃或被内存不足强制终止），一律按 failed 处理（R4Q16）。

**`status = succeeded`** 时，以下字段全部必填；**`status = failed`** 时，`errors` 必填，其余字段可以为 null。

| 字段 | 内容 |
|---|---|
| `kind` | `print`、`preview`、`crop` 或 `coupon` |
| `authoritative` | Preview Build 为 false |
| `spec` | 原始、迁移后、规范化三份 DesignSpec（`--plan` 与 Coupon Build 时为 null） |
| `buildplan` | 同 `buildplan.json`，另附 `plan_hash`；Coupon Build 时为 null |
| `coupon` | 仅 Coupon Build：档位、所用 `tuning.v1` 值、各层的 ρ 与 Cell、试块包含的 Cell 数 |
| `resolution_notes`、`adjustments`、`overridden_semantics` | 见第 7 节 |
| `provenance` | 引擎包版本（PEP 440）、git sha 与 dirty 标记；每张数据表的 `id@version` 与 sha256（含 `gyroid_c.v1`、`cellprops.seed.v1`），Cell Properties 的方法 id 与本地 namespace；uv.lock 的 sha256；平台；Blender 完整版本与 build hash；Shape Bundle 各文件的 sha256 以及 `shape.json` |
| `cell_properties` | `provider`：种子 `id@version` 与 sha256、本地 namespace、方法 id；`queries`：每条含 key、a_s、a_t、ρ、L_n、`neck_min_mm`、`pore_throat_mm`、`source`（seed、local 或 computed）以及用到它的检查点 id；`decision_margins`：每项约束到阈值的最小距离，以及 `near_threshold` |
| `probes` | `mode`；`set`：Archetype 数据 `id@version` 与 sha256；`windows`：每个窗口的 `id`、`rule`、中心、核心体素索引盒、halo 与是否回退、核心内出现的 Zone、`metrics`（Ligament 数、最小内部颈厚及位置、内部薄壁占比、裁切残边与实体接触体积、悬挂材料体积、困粉的 V_2 与 V_4 体积、Island、封闭腔）、该 Zone 的周期预测值（`neck_min`、`pore_throat`）、缺口、`verdict`、耗时；`dropped`：因去重舍去的探测；合计耗时与峰值内存；`coverage`：核心内 Lattice 材料占全鞋的估计比例，以及未被任何核心覆盖的 Zone、带与特征 |
| `metrics` | 体积、表面积、包围盒、实体壳数、空腔壳数、三角面数、简化实测最大偏差、按 Zone 汇总的 Island 删除情况、各阶段耗时、峰值内存 |
| `checks` | 每项 Printability Check 与 Donning Check 的级别、结果与位置 |
| `known_limitations` | P0 固定为三条："平整大底，仅限干燥室内试穿"；"Last 比例未经试穿校准"；"Cushioning 未经力学标定" |
| `outputs` | 文件名与 sha256 |
| `errors` | ErrorObject 数组，结构同 3.5 |

---

## 12. CLI 与失败模型

### 12.1 命令

```
shoegen build   <spec.json> -o <dir> [--timeout <s>] [--errors-json] [--force] [--probes gate|warn|off]
shoegen build   --plan <buildplan.json> -o <dir>        # 按存档重建
shoegen build   <spec.json> -o <dir> --crop <region>    # Crop Build
shoegen preview <spec.json> -o <dir>
shoegen coupon  cushioning -o <dir>                     # 每档 Cushioning 一块 Coupon
shoegen schema  --target full|llm|buildplan|manifest
```

环境变量 `SHOEGEN_CACHE_DIR` 指定 Cell Properties 本地缓存目录（7.5），默认 `~/.cache/shoegen`。

### 12.2 Crop Region

| 名称 | 定义 | 用途 |
|---|---|---|
| `heel-collar` | HeelCounter 全高（Shell 中 x − x_heel ≤ `extent_x_ratio` × Last Length、位于 Sole Envelope 之外的部分），加上 t_mm ≥ L(s) − 20 mm **[初值]** 的整圈 Shell | 试穿：验证穿脱与包跟；包含 Rim |
| `sole-section` | 以 `forefoot_stack_point` 为中心、沿 X 方向长 40 mm **[初值]** 的整宽鞋底切块 | 排粉试验 |
| `heel-transition` | 以后跟中线与 Welt 外表面的交点为中心、边长 40 mm **[初值]** 的立方体，包含 Welt 及其上下两侧的晶格（HeelCounter 与鞋底侧壁的接缝；Rim 由 `heel-collar` 覆盖） | 0.25 mm 黄金回归；复核 Welt 处的实体接触与悬挂材料 |

Crop Region 与局部探测窗口（7.6）共用"由 Landmark 与 BuildPlan 参数定义区域"和"全局栅格对齐"的机制：Crop Build 使用整鞋的全局栅格（同一原点与偏移），只截取子区域，使其与整鞋在同一位置的结果可比。探测窗口不是 Crop Build，不产出网格，也不把窗口面当作 Shoe Body 边界；`heel-transition` 的 Crop Build 与 W-heel 探测在同一位置，互相核对。

裁切面属于 Shoe Body 边界：
- 困粉检查中，它被视为外部；
- 裁切面附近的残边计入"裁切残边占比"。

### 12.3 Coupon

`coupon cushioning` 为 soft、balanced、firm 各生成一块"鞋底切片"：

- **尺寸**：40 × 40 mm **[初值]**，总高 20 mm **[初值]**。
- **分层**：按真实鞋底，自下而上依次为：
  - Skin 1.2 mm
  - Outsole 3 mm
  - Midsole 主体 11.8 mm
  - Footbed Layer 4 mm
- **晶格与坐标系**：使用该档位的 Tuning Table 值，在独立的欧氏坐标系中生成，三层共用一个 Cell C，层间只以 ρ 区分；Transition 与 Grading 同 8.2。
- **边界**：四个侧面是开放的裁切面，顶面是 Footbed Layer，不加 Skin。
- **用途**：压缩试验，标定 Cushioning 的刚度量级。Build Manifest 中记录试块包含的 Cell 数。
- **流程**：Coupon Build 不经过 Shape Source，也不经过 Resolver 阶段 B。三层的 ρ 与 Cell C 仍按 7.3 的欧氏部分做约束求解，检查点取试块中心的竖线。

### 12.4 退出码

| 码 | 含义 | `errors[].class` |
|---|---|---|
| 0 | 成功（可能带 soft 警告） | — |
| 1 | 内部错误 | internal |
| 2 | CLI 用法错误，包括输出目录非空 | — |
| 3 | DesignSpec 无效、版本不支持、Override 非法或 Infeasible | spec |
| 4 | Shape Source 失败：子进程失败、Bundle 违反不变量、Blender 版本不符、`--plan` 形状不一致 | shape |
| 5 | hard 级 Printability Check 失败，含阶段 B 的构建体积预检（`E_BUILD_VOLUME`）与局部探测（`E_PROBE_THIN_WALL`、`E_PROBE_TRAPPED_POWDER`、`E_PROBE_HANGING_MATERIAL`、`E_PROBE_ISLAND`、`E_PROBE_CAVITY`） | printability |
| 6 | 资源预检不通过（确定性失败，重试无用） | resource |
| 7 | 超时（可重试） | timeout |

---

## 13. 性能与资源

| 指标 | 目标 | 基准 |
|---|---|---|
| 单只鞋 Print Build（0.25 mm，含校验与导出） | ≤ 10 分钟 | Apple M4 / 16 GB |
| 峰值内存 | ≤ 8 GB | 同上 |
| Preview Build（0.5 mm） | ≤ 1 分钟 | 同上 |
| 单只鞋 Print Mesh | 预期约 400–500 万三角面、200–250 MB | — |
| 局部探测 | ≤ 60 s、峰值 ≤ 2 GB（3 个 worker，计入 Print Build 的 10 分钟） | 同上 |
| Cell Properties 冷查询 | ≤ 3 s/次（典型）；Resolver 最坏冷启动约 40 s（4 个 worker）；热查询 ≤ 5 ms | 同上 |
| Cell Properties 种子生成 | 约 20 核·分钟 | 同上 |

- **已有实测**：0.25 mm 下鞋底大小区域的网格阶段峰值约 5.8 GB；整鞋包围盒（2.5–3.5 亿体素）上，Hildebrand–Rüegsegger 局部厚度（该方法已被 ADR 0009 否决）与困粉两项体素检查共 29–43 s，峰值约 5.5 GB。分块粗阶段实测（合成鞋底，1580 万 Lattice 体素，7,301 根 Ligament）：1 个 worker 4.8 分钟 / 0.49 GB；4 个 worker 1.3 分钟 / 1.95 GB；6 个 worker 1.05 分钟 / 2.5 GB。细阶段约占总时间 95%（单核 0.037 s/Ligament）。最坏情形外推（全三维 Lattice、鞋底 Cell 取下限 8 mm 时韧带最多）：4 个 worker 约 3–4.5 分钟、约 3 GB。默认 worker 数为 4 **[实现]**（16 GB M4）。
- **内存的阶段性**：网格阶段与校验阶段不同时驻留。体素类校验在释放场与网格之后进行。
- **资源预检**：由 Shape Bundle 体积与体素尺寸估算各阶段峰值内存，估算系数由基准测试测定 **[实现]**。估算值超过预算（默认 8 GB，可配置）时提前退出，退出码 6。
- **超时**：由 `--timeout` 控制。超时后写入 failed 状态的 Build Manifest，退出码 7。

---

## 14. 确定性与复现（ADR 0006）

- **逐字节一致**：同一份 BuildPlan，在同一平台、同一引擎版本（git sha 相同且无未提交改动，内置数据表随之相同）、同一 uv.lock、同一 Blender 完整版本（含 patch 与 build hash）下，Print Mesh 逐字节一致。
- **跨平台或 Blender patch 不同**：只保证指标等价，即体积 ±0.1%、表面积 ±0.5%、包围盒 ±0.05 mm、连通分量数相同。
- **Blender 版本**：主次版本不符即拒绝构建（退出码 4）；patch 版本不同只发警告并记录。
- **并行**：只在分块之间并行，合并顺序固定。
- **Cell Properties**：求值器是 key 的纯函数；同环境下本地缓存与重算逐位一致，冷热缓存的 BuildPlan 与 Print Mesh 逐字节相同；种子命中的查询跨平台值相同，种子之外的冷查询跨平台只保证 ≤ τ（7.5、ADR 0012）。
- **局部探测**：窗口位置与核心盒由 Shape Bundle 与 BuildPlan 确定性推出并对齐全局体素索引；窗口之间并行、按序号合并，结果与 worker 数无关（7.6）。
- **第三方步骤**：必须使用确定性实现。manifold3d 的 simplify 与 fast-simplification 已在单机上跨进程复测一致；跨平台的一致性待验证。

---

## 15. 测试

### 15.1 单元测试（不依赖 Blender）

**解析与规则**：
- 尺码换算、各体系端点值、Mondopoint 与 Toe Allowance 的例外规则
- 比例表
- Tuning Table 自检：范围见 7.2
- Cell Properties：重算与缓存逐位一致、冷热缓存的 BuildPlan 相同、种子 sha 与方法 id 校验、定义域边界与拓扑断言、ρ 取值格上的单调性、`neck-cell-dirc@1` 对 17 组真值加扩展真值误差 ≤ 0.021 mm（pass 类）、`gyroid_c.v1` 复现 harness 的 c
- 约束求解：
  - Adjustment
  - Infeasible
  - Tier Band 边界
  - 随形 Zone 放大 Cell 无效时不放大
  - Midsole ρ 的 Override 使 Footbed Layer 不可行 → `E_INFEASIBLE_OVERRIDE`
- Override 白名单与钉住语义
- 版本迁移与拒绝
- 四种 schema 导出，其中 `llm` 投影的所有对象都不允许额外字段
- 错误格式
- plan_hash 的固定测试向量：
  - G1、G2 和带 Override 的 G5，各给出完整 BuildPlan 的期望哈希；
  - EU35.5、EU42.5 只测阶段 A 草稿中 `geometry.fit`、`geometry.shape` 的量化结果，以及 `shape_hash`
- Cell Properties 定义域覆盖：见 7.5 的覆盖要求
- 局部探测：
  - 选取的确定性：同一 Shape Bundle 两次结果逐位相同；
  - 合成场上窗口与整块分块计算的核心内 Ligament 一致；
  - 外表面语义：构造的跨窗口依托不被误判为悬挂材料；
  - 注入窗口内的薄颈、悬挂材料与封闭腔，各触发对应的 `E_PROBE_*`；
  - `warn` 与 `off` 模式

**几何**：
- 场函数
- Grading 与位置无关：同一 Grading 放在 x = 0 与 x = 280 处，连通分量数与 ρ 一致
- Zone 推导，含 Welt Surface 两侧的坐标系归属（Sole Envelope 高出 Footbed 的侧壁归 Shell 坐标系）
- Welt 补丁：平壁 Shell 加鞋底的补丁，扫描 `welt.height_mm`（1.5 与 3.0）、悬挑 e（4 与 6 mm）、ρ 组合、方位与相位；断言内部最小颈厚不低于 Shell 自身的值，悬挂材料为 0，接缝不脱开（上下端材料路径瓶颈不低于纯 Shell），困粉为 0（harness：`.scratch/neck-acc/crossframe/`）
- 端部受限判定：纯 Shell 平板（ρ 0.32 与 0.35）与 Skin 上的 Outsole 晶格，被边界或实体限制的读数归入裁切残边或实体接触，薄壁占比为 0；人工构造的悬挂材料被悬挂材料检查检出
- 鞋底侧壁上，距 ground 面集边界超过 t_skin 加 1 个体素的部分，Skin 面积为 0
- 坐标变换与 GLB 变换、镜像
- Island 清理不删除空腔壳：外盒内含反向腔时，腔被保留，"无封闭腔"检查失败
- Neck Thickness 精度与检出：参考为基准式（benchmark）解析山口真值（解析梯度加 Newton，误差 ≤ 0.01 mm）
  - 参考颈厚 ≤ 1.5 mm 的 Ligament 误差 ≤ 0.05 mm，其余 ≤ 0.2 mm
  - 参考颈厚 ≤ 1.5 mm 的 Ligament 100% 检出；无读数低于 1.05 mm 的虚假输出
  - 覆盖每个 Zone 的 Cell 形状，含随形部分两端的 Cell 形状；鞋底 ρ 过渡带（Outsole↔Midsole 与 Footbed Layer 的 Grading）以带两端 ρ 的周期 Cell 真值加缓变 ρ 平板验证
  - 分块与不分块的结果逐位一致（Ligament 集合与颈厚），含跨块边界，并含强制小块的情形
  - 场的点求值：同一点在不同分块下结果一致

### 15.2 测试数据

- **形状 fixture**：每一种不同的 `geometry.shape` 提交一份 Shape Bundle，按用例命名：
  - `shape-g1g5-eu42`
  - `shape-g2-eu35n-h20d5`
  - `shape-g3-eu48w-h40d12-collarmid`
  - `shape-g4-eu42-toehigh`
  - `shape-eu35-default`、`shape-eu48-default`：默认鞋底几何，供 7.2 的 Tuning Table 自检使用
- **生成方式**：`scripts/regen_fixtures.py` 从 `examples/specs/*.json` 解析出 `geometry.shape` 后生成。比例表或控制笼一旦变化就必须重新生成；过期的 fixture 会被 `shape_hash` 检查拒绝。
- **覆盖检查**：单元测试断言每个 G 用例的 `shape_hash` 都能在 `fixtures/` 中找到。

### 15.3 黄金回归

**用例**：

| 用例 | 设计 |
|---|---|
| G1 | 全默认，EU42 |
| G2 | EU35 narrow；heel 20 / drop 5（Forefoot Stack 恰为下限 15）；soft/soft；airy |
| G3 | EU48 wide；heel 40 / drop 12；heel firm / forefoot soft；dense；collar mid |
| G4 | EU42；toe_spring high；heel soft / forefoot firm |
| G5 | EU42，带 Override：Midsole `cell_mm` = 12 |

**检查方式**：
- 每个用例在 0.5 mm 下生成整鞋几何，比对第 14 节的指标，不比对哈希。
- 每个用例存一份期望的 Adjustment 列表（路径与原因），逐条比对。G1 与 G3 的期望列表为空。G2 处在参数域角落，期望列表应包含前掌 Midsole 与 Footbed Layer 的修正，具体条目以 Cell Properties 的求值为准。
- 另外以 0.25 mm 跑 `heel-transition` 与 `heel-collar` 两个 Crop Build，并执行完整的 hard 校验。
- 慢测试加：G1 在 0.25 mm 下的局部探测全部通过，1 与 3 个 worker 的探测记录逐位相同。

### 15.4 冒烟测试与慢测试

- **冒烟测试**：1 mm 体素，只验证流程能跑通。
- **慢测试**：需要 Blender 的测试和全分辨率的 Print Build 标记为 slow，默认不运行。

---

## 16. 实物验证流程（P0 收尾）

1. **选定服务商**：优先 MJF。新增专属 Profile，并对照服务商的设计指南复核最小壁厚、孔喉与构建体积。
2. **打印裁切件与 Coupon**：
   - `sole-section`：排粉试验。
   - `heel-collar`：试穿，用于标定 Donning Check 阈值与 Collar 形状参数初值。
   - `coupon cushioning` 三档：压缩试验，用于标定 Tuning Table。
3. **修订数据表**：根据试验结果修订数据表，并升版本号。
4. **打印整鞋**：打印一只整鞋的 Print Build。要求所有 hard 级 Printability Check 通过，即薄壁占比与困粉占比都 ≤ 1%；soft 警告逐条复核，并记入 Build Manifest。

---

## 17. 包结构与依赖

```
pyproject.toml                  # uv，Python 3.13
src/shoegen/
  spec/                         # DesignSpec、BuildPlan、Manifest 模型；Resolver（含 resolver/probes.py：阶段 B 局部探测的选点与判定）；迁移；schema 导出；规范化与哈希
    data/                       # profiles/、sizes.v1、proportions.v1、tuning.v1、gyroid_c.v1、cellprops.seed.v1、archetypes/、palette.v1
  shape/                        # Shape Source 接口；子进程调用；Shape Bundle 读写与不变量检查
    blender/                    # 在 Blender 内运行的控制笼脚本（build_bundle.py）
  zones/                        # Shell 与 Zone 推导、Grading 与 Transition 权重
  lattice/                      # TPMS 场、随形坐标、归一化与混合；cellprops.py（Cell Properties 求值器与缓存）
  fuse/                         # 场合成、分块等值面提取、Island 清理、简化
  validate/                     # Printability Check、Donning Check、检查栅格；window.py（探测窗口运行器）
  export/                       # STL、GLB、Build Manifest、原子写入
  pipeline.py                   # 按 (ManufacturingMode, StructureType) 分派
  cli.py
scripts/                        # gen_gyroid_c.py、gen_cellprops_seed.py、regen_fixtures.py
tests/                          # fixtures/ 下放 Shape Bundle
examples/specs/                 # G1–G5 的 DesignSpec
```

**第三方库**：以下都是已验证可用的参考选择，并且都有 cp313 macOS arm64 的安装包：numpy、scipy、scikit-image、trimesh、manifold3d、libigl、pydantic v2、fast-simplification、pytest。实现者可以替换，但必须满足第 13 节的性能要求和 ADR 0006 的确定性要求。

**已知坑**：trimesh 的 proximity 查询太慢，不可用于整鞋距离场。

---

## 18. 已知限制与 P1 候选

### 18.1 P0 已知限制

- 大底平整无花纹，仅适合干燥的室内地面试穿。Outsole 的 Skin 厚度受上限（≤ 1.5 mm）约束。
- Last 比例使用男女通用表，未经试穿校准。
- Cushioning 未经力学标定。
- 只支持 Gyroid network。
- Rim 与 Welt 是不可拉伸的实体条。Welt 在前掌弯折时约 20 % 压应变，会提高局部弯折刚度并可能起皱；外观上有一条 1.5 mm 的实体线；整鞋增重约 7–13 cm³（晶格材料的 4–7 %）。
- 缺少 Surface Parameterization 的 Shape Source 无法使用。
- 局部探测只抽查约 10–20 % 的 Lattice 材料；其余位置的非周期偏差只在整鞋 Printability Check 中发现。此外，一个 16 mm 核心只采到约 1.6 个 C = 10 的 Cell，相位采样不足；连通类检查（悬挂材料、困粉、封闭腔）对窗口外表面取乐观处理。

### 18.2 P1 候选

- 鞋底花纹，并把 Outsole 的耐磨层从 Skin 的厚度约束中拆出来
- Diamond、sheet 变体、杆系晶格、Voronoi（届时引入 seed）
- 跨族 Transition
- 由引擎自动计算 Surface Parameterization
- 分性别的比例表
- 可拉伸的 Rim；分段或波纹 Welt（须先过 `.scratch/neck-acc/crossframe/` 的 harness）
- 形状参数的 Override
- 探测失败时有界的 Adjustment 循环（同时让 Preview Build 跑探测，或放宽 plan_hash 共用规则）
- 多零件 ManufacturingMode
- HybridLattice

---

## 19. 待实现阶段确定的数值

以下数值写入对应数据表并注明出处，不改变设计。

| 项 | 写入位置 | 确定方式 |
|---|---|---|
| Ball Girth、Long Heel Girth 的系数 (a, b) | `proportions.v1` | 查公开人体测量文献 |
| 脚到 Last 的放余量规则 | `proportions.v1` | 查制鞋资料 |
| US/UK 偏移所采用的约定 | `sizes.v1` | 注明出处 |
| Openness 与 HeelCounter 的最终 ρ | `tuning.v1` | Cell Properties 求值，满足 7.2 的自检 |
| `gyroid_c.v1` 与 `cellprops.seed.v1` | 数据表 | 生成脚本 |
| 探测集（窗口数、核心尺寸、平滑尺度、局部阈值） **[初值]** | `archetypes/low_top_slip_on.v1` 的 `probes` 段与 Profile | G1–G4 实测（M6.11） |
| 资源预检的估算系数 | 代码常量 | 基准测试 |
| 标 [初值] 的 Collar、Toe Spring、HeelCounter 切分、Island 阈值、Donning 阈值、Crop Region 与 Coupon 尺寸 | 各数据表 | 第 16 节的实物结果 |

---

## 20. 风险与先行验证

以下几项是实现中最不确定的部分，应在相应模块开工前，先用最小原型验证。

| 风险 | 为什么不确定 | 先行验证 |
|---|---|---|
| ~~混合区的颈厚~~（已由 ADR 0010 解决） | 规格曾设的 C:C/2 φ 混合在 w≈0.53–0.84 处新生颈厚从 0 起的 Ligament，过渡带 0.4–1.4 mm 宽、薄壁占比 9–10 %（限值 1 %）；相位对齐、并集、阈值补偿、改变 W、硬界面均无效。已改为单一鞋底 Cell，Grading 只改 ρ | 已解决（设计变更）；回归由 15.1 的过渡带用例覆盖 |
| ~~跨坐标系混合~~（已由 ADR 0011 解决） | 规格曾设的 φ 混合在 w_x ∈ (0.40, 0.75–0.90) 内颈厚 0–0.2 mm，混合区材料跌到约 60 %，并且接缝下方 4–6 mm 的重叠柱里 C = 10 鞋底网络在对轴方位上出现空层，使 17–27 % 的 30 mm 接缝段上下完全脱开（全局分量数仍为 1，现有校验看不到）；W、相位、并集、阈值补偿、硬界面、z = H 处的实体接缝均无效。改为 Footbed 高度的实体 Welt，两侧硬切换（.scratch/neck-acc/crossframe/） | 已解决（设计变更）；`heel-transition` Crop Build 复核 Welt 处的实体接触与悬挂材料 |
| 10.2 端部受限读数与悬挂材料 | 0.5 mm 站点排除挡不住被边界切断的 Ligament：垂直平面内的最大内切球可比站点更靠近边界，致密 Shell（ρ 0.32/0.35）按字面规则 14–20 % 判为薄壁，默认 HeelCounter 单项约占全鞋 1.1 %；Outsole 晶格擦着 Skin 的桥约占 0.7 %。新规则（端部受限判定加悬挂材料检查）的 1.0 mm、0.1 mm、1 mm³ 阈值是首次估计，规则本身也只在后处理里近似实现过，未在完整的 10.2 流水线里跑过 | 按新规则重跑 harness，并在 G1 整鞋上核对裁切残边、实体接触与悬挂材料三项；复核阈值是否放过真实缺陷或误报 |
| Welt 的力学与外观 | 1.5 mm 实体条位于前掌中性轴上方约 11 mm，弯折时约 20 % 压应变，可能起皱并提高弯折刚度 | 第 16 节的实物试穿与前掌弯折观察；P1 候选：分段或波纹 Welt（须先过 harness） |
| 分块精确性依赖点求值的逐位确定性 | 接缝精确要求合成场在同一点返回逐位相同的浮点数，与分块方式无关（NumPy 已验证）。GPU、并行或基于栅格差分求梯度模的实现必须保证这一点；上升路径超过 16 mm 回退 halo 时报内部错误。参数（σ、persistence 等）只在一个种子上调定，未做留出验证；自适应 σ 是可能的改进（可回收胖薄向 Cell 中的 23 次漏检与 59 个虚假输出，参考值均 ≥ 1.2 mm） | 在实际合成场实现上重跑分块与不分块的逐位对比（含强制小块）；做留出验证 |
| Openness 定稿值是否落在可行区间 | 审查期间的颈厚实测用过不同算法，数值有差异；按 ADR 0009 的山口定义，benchmark 给出 airy 0.20 在 Collar 端 (·, 8, 4) 的颈厚为 1.106 mm，高于 1.0 + 0.05 的门槛，余量约 0.055 mm。Shell 的该值与 L_s 无关 | 用 Cell Properties 在 G1–G4 的 Collar 端实际形状上求值后定 `tuning.v1`；复核 airy 在 Collar 端的值，若不足再微调 ρ |
| Cell Properties 的精度与成本 | 求值器 `neck-cell-dirc@1` 的误差（期望 ≤ 0.01 mm，上限 0.021 mm）与冷查询耗时（≤ 3 s）尚未实测；`neck_margin_mm` 的余量账建立在这一误差上（9.1） | M3.3 的 spike：在 17 组真值上实测误差与耗时；误差 > 0.025 mm 或耗时 > 3 s 则回到设计讨论 |
| 局部探测的覆盖与等价性 | 覆盖率（约 10–20 % 的 Lattice 材料）、相位采样、连通类检查的乐观性、场吞吐 ≤ 1 µs/体素的假设；窗口读数与整鞋读数在核心内的一致只是预期，局部 ToMATo 的簇身份可能与全局不同 | M6.11：在 G1–G4 上比较窗口与整鞋同位置的逐 Ligament 读数，在窗口内外注入缺陷，记录耗时与误报 |
| Welt 处的实体接触、Island 与悬挂材料 | 只在平壁补丁上实测过（336 个窗口），整鞋曲率下的 z_W 与最近点判定未验证 | `heel-transition` 的 0.25 mm Crop Build |
| 程序化控制笼生成的 Last 的形状质量 | 决定了能否穿、好不好看，P0 无法试穿校准 | 先单独做 Shape Source，用 G1–G4 的形状目视检查，并比对比例表 |
