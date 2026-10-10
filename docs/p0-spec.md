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
- **流水线**：Resolver 阶段 A → Shape Source（Blender 无界面子进程）→ Shape Bundle → Resolver 阶段 B → 晶格引擎（Zone 推导、场合成、等值面提取、Island 清理、简化）→ Printability Check → 导出。
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
               BuildPlan（含 plan_hash）◄─ Resolver 阶段 B（定 N、随形约束、预检）◄┘
                     │
                     ▼
晶格引擎：Shell 与 Zone 推导 → 场合成 → 分块等值面提取 → Island 清理 → 简化
        → Printability Check → 导出（STL / GLB / Build Manifest）
```

| 阶段 | 职责 | 运行位置 |
|---|---|---|
| Resolver 阶段 A | 版本处理、校验、按 Tuning Table 解析、应用 Override、欧氏部分约束求解、生成形状参数 | 引擎进程 |
| Shape Source | 根据形状参数生成 Shape Bundle | `blender -b` 子进程（ADR 0001） |
| Resolver 阶段 B | 确定周向 Cell 数 N、随形部分约束求解、构建体积与资源预检、计算 plan_hash | 引擎进程 |
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
- 覆盖 Footbed Edge 到 Collar Line 之间的环带，并向 Footbed 一侧延伸至少 6 mm（测地距离），供跨坐标系 Transition 使用。

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
      "transitions": { "lateral_width_mm": 6.0, "cross_frame_width_mm": 6.0, "vertical_max_mm": 6.0 },
      "features": {
        "collar_rim":  { "kind": "rim",  "along": "collar_line", "height_mm": 2.0, "thickness": "wall" },
        "ground_skin": { "kind": "skin", "zone": "outsole", "surface": "ground", "thickness_mm": 1.2 }
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

- **鞋底 Cell**：鞋底只存一个基准 Cell C（`midsole.fill.cell_mm`）。Outsole 与 Footbed Layer 的 Cell 固定为 C/2，以保持混合时的公度（8.2）。
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
- **共用**：同一设计的 Preview Build 与 Print Build 有相同的 plan_hash，只是使用 `resolution` 中的不同字段。
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

   放大 `cell_inplane` 时重新计算 N。
10. **预检**：
    - 用 Bundle 包围盒做构建体积预检。不通过时报 `E_BUILD_VOLUME`，class 为 printability，退出码 5。它与 10.2 的构建体积 hard 检查同码，只是在生成晶格之前提前判定。
    - 用 Bundle 体积做资源预检（第 13 节）。
11. **定稿**：计算 plan_hash，写出 `buildplan.json`。

**`--plan` 路径**（按存档的 BuildPlan 重建）：

- 跳过步骤 1–7，`geometry.shape` 直接取自存档。
- 照常运行 Shape Source。
- 阶段 B 以"只校验"模式执行：
  - 重算的 N 必须等于存档值，否则报 `E_PLAN_SHAPE_MISMATCH`（退出码 4）；
  - 约束不满足报 `E_INFEASIBLE`（退出码 3）；
  - 不产生任何 Adjustment；
  - 预检照常执行。

### 7.2 Tuning Table（`tuning.v1`）

全部为 Gyroid network。

| Zone / 层 | 坐标系 | Cell | Cell 上限 | ρ 名义值与 Tier Band |
|---|---|---|---|---|
| Midsole | 欧氏 | C = 10 mm | 12 mm **[初值]** | soft 0.15 [0.125, 0.175)；balanced 0.20 [0.175, 0.235)；firm 0.27 [0.235, 0.305] **[初值]** |
| Footbed Layer（Midsole 顶部 4 mm） | 欧氏 | C/2 | 随 C | Midsole 当地 ρ + 0.05；Tier Band 随 Midsole 档位平移 |
| Outsole（3 mm 晶格层，下方是 1.2 mm Skin） | 欧氏 | C/2 | 随 C | 0.35 [0.32, 0.38] **[初值]** |
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
- **定稿方式**：Openness 与 HeelCounter 的最终 ρ 以 Feasibility Table 的实测结果定稿。

### 7.3 约束求解

**检查点**：

| 坐标系 | 检查点 |
|---|---|
| 欧氏 | 见下方说明 |
| 随形 | Upper 与 HeelCounter，各取 7.1 第 9 步的两端 |

欧氏部分的检查点：
- **位置**：沿中线在 [0, Last Length] 上每 5 mm **[实现]** 取一个采样点，另加 `heel_stack_point` 与 `forefoot_stack_point`；Midsole、Footbed Layer、Outsole 都在这些位置检查。
- **取值**：每个点用当地 ρ 和当地有效 Cell，有效 Cell 由 C、C/2 和 8.2 的混合权重 w 决定。
- **当地 Midsole 主体厚度 T**：在阶段 A 估算，记 t_layers = t_skin + t_out + t_fbl（见 8.1）。
  - x ≤ `heel_stack_point` 处：T = Heel Stack − t_layers；
  - x ≥ `forefoot_stack_point` 处：T = Forefoot Stack − t_layers（Toe Spring 段趾下厚度不变）；
  - 两点之间：线性插值。
- **Transition 带**：带内另取 w ∈ {0.25, 0.5, 0.75}。

**可行区间**：查 Feasibility Table（7.5），得到 [ρ_min, ρ_max]。
- ρ_min：使 Neck Thickness ≥ 最小壁厚 + `neck_margin_mm`（Profile 字段，见 9.1）的最小 ρ。
- ρ_max：使 Pore Throat ≥ 该点所在孔喉级别下限（9.1）的最大 ρ。
- 孔喉级别：
  - 薄层：Shell 中各 Zone、Footbed Layer、Outsole，以及 Midsole 中 w_sole > 0 的点。即 Cell 小于基准 C 的点，不论原因是厚度不足，还是处在 Outsole↔Midsole 过渡带或 Footbed Layer 的 Grading 带内。
  - 芯部：Midsole 中 w_sole = 0 的点。

**求解顺序**：

1. **钉住的值不可行**：被钉住的值在任一检查点落在区间外，或超出 Feasibility Table 的定义域 → 报 `E_INFEASIBLE_OVERRIDE`。
   - Footbed Layer 的 ρ 是 Midsole ρ 的派生量：`rho_heel` 或 `rho_forefoot` 被钉住时，对应一端的 Footbed Layer ρ 也视为被钉住。
   - `rho_delta` 只有在 Midsole ρ 未被钉住时，才可以通过 Adjustment 修改。
2. **名义值可行**：名义 ρ 在所有检查点都落在区间内 → 完成。
3. **放大 Cell**：仅当 Feasibility Table 表明"在不超过 Cell 上限的范围内放大 Cell，能让名义 ρ 在所有检查点都落入区间"时，才放大 Cell：
   - 欧氏部分放大 C，各向同性；
   - 随形部分放大 `cell_inplane`，Wall Thickness 不变。
   
   取满足条件的最小 Cell，记一条 Adjustment。
4. **在 Tier Band 内移动 ρ**：否则 Cell 保持名义值，按以下顺序调整：
   - 用 x ≤ `heel_stack_point` 的检查点确定 `rho_heel`，用 x ≥ `forefoot_stack_point` 的检查点确定 `rho_forefoot`。两者各自在本档位的 Tier Band 内，取离名义值最近的可行值。
   - Footbed Layer 不可行时，只调整 `rho_delta`，其 Tier Band 随 Midsole 档位平移。
   - 其他 Zone 各自在本档位的 Tier Band 内取离名义值最近的可行值。
   - 最后检查两测点之间的检查点。
   
   每处改动记一条 Adjustment。
5. **不可行**：仍不可行 → 报 `E_INFEASIBLE`。错误信息附上可行的替代方案，例如"该 Zone 当前可行的 ρ 区间是 …；可改用 … 档"。

每条 Adjustment 都写进 Build Manifest，包含路径、原值、新值和原因（`neck`、`pore` 或 `cell`）。Infeasible 的退出码为 3。

### 7.4 Override

**键与格式**：
- 键是 BuildPlan 的 JSON Pointer（RFC 6901），必须逐字出现在白名单中。
- 白名单在 `buildplan` schema 中以 `x-overridable` 标记。
- P0 白名单 **[初值]**；扩充白名单只需升 BuildPlan 的 minor 版本。

| 路径 | 范围 |
|---|---|
| `/geometry/parts/0/zones/midsole/fill/cell_mm` | 8–12 |
| `/geometry/parts/0/zones/midsole/fill/rho_heel` | 0.05–0.60 |
| `/geometry/parts/0/zones/midsole/fill/rho_forefoot` | 0.05–0.60 |
| `/geometry/parts/0/zones/midsole/footbed_layer/rho_delta` | 0–0.15 |
| `/geometry/parts/0/zones/outsole/fill/rho` | 0.05–0.60 |
| `/geometry/parts/0/zones/upper/fill/rho` | 0.05–0.60 |
| `/geometry/parts/0/zones/heel_counter/fill/rho` | 0.05–0.60 |
| `/geometry/parts/0/shell/wall_thickness_mm` | 3.0–6.0 |
| `/geometry/parts/0/shell/cell_inplane_mm` | 6–10 |
| `/geometry/parts/0/transitions/lateral_width_mm` | 2–12 |
| `/geometry/parts/0/transitions/cross_frame_width_mm` | 2–12 |
| `/geometry/parts/0/features/collar_rim/height_mm` | 1–6 |
| `/geometry/parts/0/features/ground_skin/thickness_mm` | 1.0–1.5 |
| `/geometry/fit/toe_allowance_mm` | 8–20 |
| `/resolution/voxel_mm` | 0.15–1.0 |
| `/resolution/simplify_tolerance_mm` | 0.01–0.2 |

**规则**：
- **非法键或值**：键不在白名单、类型错误或超出范围 → 报 `E_SPEC_OVERRIDE`（退出码 3）。
- **钉住的值不可行**：在 7.3 中报 `E_INFEASIBLE_OVERRIDE`，不提供绕过。
- **优先级**：Override 总是优先于语义字段；Build Manifest 记录被覆盖的语义（`overridden_semantics`）。
- **LLM 投影**：Override 不出现在 `llm` 投影中。
- **形状参数**：`geometry.shape` 在 P0 不可覆盖，只能通过 DesignSpec 的语义字段修改。

### 7.5 Feasibility Table（`feasibility.v1`）

由离线脚本 `scripts/gen_feasibility.py` 生成，并纳入版本管理。

**输入维度**：

| 维度 | 取值 |
|---|---|
| 晶格族与变体 | P0 只有 Gyroid network |
| Cell 形状 (L_s/L_n, L_t/L_n) | 以法向 Cell L_n 归一化；取值集合 {0.5, 0.625, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6} **[实现]**；欧氏 Cell 为 (1, 1) |
| 混合权重 w | 仅欧氏 C : C/2 混合使用，取 {0, 0.25, 0.5, 0.75, 1} |
| ρ | 0.05–0.60，步长 0.005 **[实现]** |

**输出量**（按 L_n 归一化，使用时乘以实际尺寸）：
- 等值面阈值 c：使材料体积分数等于 ρ。混合场的 c 按混合后的实测体积分数标定。
- 最小 Neck Thickness：定义见 10.2，在无切面的周期 Cell 上计算。
- Pore Throat：三轴方向逾渗瓶颈中的最小值。

**计算方法**：
- 体素不大于 L_n/200 **[实现]**；
- 混合场的周期取 C，内含 2×2×2 个 C/2 Cell；
- Neck Thickness 的定义与 Printability Check 完全相同。

**定义域**：
- **覆盖要求（设计约束）**：对 7.4 白名单内 `wall_thickness_mm` 与 `cell_inplane_mm` 的任意组合，7.1 第 9 步两端的 (L_s/L_n, L_t/L_n) 都必须落在定义域内。这一点由单元测试断言，测试使用 G1–G4 fixture 实测的 P(0)、P(1)、P(0.5)。
- **域外查询**：查询落在定义域之外时，按不可行处理。

---

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

**每一点恰属一个 Zone**，按下表从上到下判定，取第一个满足的：

| 条件 | 所属 |
|---|---|
| 在 Sole Envelope 内，且 d_G ≤ t_skin | Outsole 的 Skin |
| 在 Sole Envelope 内，且 t_skin < d_G ≤ t_skin + t_out | Outsole 的晶格层 |
| 在 Sole Envelope 内，其余 | Midsole（其中 d_F ≤ t_fbl 的部分是 Footbed Layer） |
| 在 Shell 内、Sole Envelope 外，且 x − x_heel ≤ `extent_x_ratio` × Last Length | HeelCounter |
| 在 Shell 内、Sole Envelope 外，其余 | Upper |

**Skin 的范围**：
- Outsole 与 Skin 的厚度都沿 Ground Surface 的法向计，Toe Spring 段同样如此。
- Sole Envelope 侧壁、Footbed、Shell 内外表面都不加 Skin。
- 鞋底侧壁保持开放晶格，作为排粉通道（ADR 0007）。
- 唯一的例外：Skin 在 Ground Surface 边界处会露出约 t_skin 高的截面。

**Midsole 主体厚度**：T(x, y) = z_F(x, y) − t_fbl − (z_G(x, y) + t_skin + t_out)，沿竖直方向量。其中 z_F、z_G 分别是 Footbed 与 Ground Surface 在该 (x, y) 处的高度。

### 8.2 Grading 与 Transition

**通则**：

1. 所有带状区都以对应边界为中心，半宽 W/2；权重 w 按到该边界的距离线性变化。
2. Cell 的空间变化一律通过固定周期场的混合实现（ADR 0002）：
   - 欧氏部分在 C 与 C/2 两个场之间按 w 混合；
   - 任何情况下都不得把位置相关的 Cell 代入坐标。
3. ρ 的变化只改变阈值，不改变周期。
4. 多条带重叠时，按 8.1 表的顺序，先列出的 Zone 优先。

| 位置 | 边界 | 宽度 W | 做法 |
|---|---|---|---|
| Outsole ↔ Midsole | d_G = t_skin + t_out | min(`vertical_max_mm`, t_out)，默认 3 mm | w_Outsole 在边界 ± W/2 内（默认 d_G ∈ [2.7, 5.7]）从 1 线性降到 0；ρ 同步插值 |
| Midsole 内 Footbed Layer 的 Grading | d_F = t_fbl | t_fbl，默认 4 mm；不受 `transitions/*` 影响 | w_FBL 在 d_F ∈ [t_fbl/2, 3·t_fbl/2]（默认 [2, 6]）内从 1 线性降到 0；ρ = ρ_Midsole(x) + `rho_delta` · w_FBL；d_F ≤ t_fbl/2 处是均匀的 C/2 与 ρ_Midsole(x) + `rho_delta` |
| Midsole 薄处 | — | — | w_thin = clamp((C − T)/(C/2), 0, 1) |
| Upper ↔ HeelCounter | x = x_heel + `extent_x_ratio` × Last Length | `lateral_width_mm`，默认 6 mm | Cell 相同，只插值 ρ |
| 跨坐标系（鞋底 Zone ↔ Shell Zone） | Sole Envelope 边界 | `cross_frame_width_mm`，默认 6 mm | 见 8.3 |

鞋底某点的最终 Cell 权重取各项中的最大值：w_sole = max(w_Outsole, w_FBL, w_thin)。

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
- g_min = 0.5 · 2π / Cell_min **[实现]**；δ = 1.0 mm **[实现]**。
- |∇f_X|：欧氏侧用解析式；随形侧在体素栅格上做中心差分，分块时 halo 加 1，以保证确定性。

**欧氏混合**：

φ_sole = (1 − w_sole) · φ_C + w_sole · φ_{C/2}

两项使用同一个阈值 c(ρ, w_sole)，取自 Feasibility Table。

**跨坐标系混合**：

φ = (1 − w_x) · φ_sole + w_x · φ_shell，w_x = clamp(0.5 + sd_env / W, 0, 1)

- **符号**：sd_env 是到 Sole Envelope 的有符号距离，包络外为正；W = `cross_frame_width_mm`。
- **计算范围**：w_x 只在 {0 < d_Last ≤ Wall Thickness，且 t_mm ≥ −6} 内按上式计算。
  - 此范围之外，包络内的点 w_x ≡ 0；
  - 包络外的点不属于 Shoe Body，不需要取值。
- **两侧参数的取法**：与该点最终归属哪个 Zone 无关，以保证场在包络边界上连续。
  - φ_sole：按 8.1 表的前三行判定（忽略"在 Sole Envelope 内"这个条件），再按 8.2 的鞋底 Grading 与 Transition 得到 ρ、w_sole 和 c(ρ, w_sole)。
  - φ_shell：按 x 判定为 Upper 或 HeelCounter，取其 ρ（含两者之间的插值）及 c(ρ)。

**Rim 与 Skin**：
- Rim = {t_mm ≥ L(s) − `height_mm`，0 < d_Last ≤ Wall Thickness}，为实体。
- Skin = Outsole 中 d_G ≤ `thickness_mm` 的部分，为实体。

**合成**：

材料实体 M = (Lattice ∪ Rim ∪ Skin) ∩ Shoe Body

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
| Resolver 颈厚余量 `neck_margin_mm` | 0.05 mm **[初值]**，与 10.2 的测量精度一致；Resolver 放行的设计，不会因测量误差在校验中失败 |
| 最小 Pore Throat | 薄层 ≥ 2.0 mm；芯部 ≥ 4.0 mm（级别划分见 7.3，ADR 0007） |
| hard 阈值 | 薄壁：材料体积占比 1%；困粉：Shoe Body 内空隙体积占比 1%（均可配置） |
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
| 裁切残边占比 | soft | 距 Shoe Body 边界 ≤ 0.5 mm 的骨架点中，2r < 最小壁厚的点所对应的材料体积占比；只报告，不判失败 |
| 困粉占比 | > 1% 为 hard 失败；> 0 为 soft 警告 | 见下文"困粉占比的计算" |

**薄壁占比的计算**（ADR 0009）：

1. 对 Lattice 区域（不含 Skin 与 Rim）的材料相做曲线骨架化。
2. 剪除长度小于 1.0 mm **[实现]** 的末梢。
3. 以节点（度数 ≥ 3）和端点把骨架切分为 Ligament。
4. 每个骨架点的半径 r 取该点到材料表面的亚体素距离。
5. 一根 Ligament 的 Neck Thickness = 该 Ligament 上、距 Shoe Body 边界 > 0.5 mm 的点中 2r 的最小值。如果一根 Ligament 的全部骨架点都距边界 ≤ 0.5 mm，它不参与薄壁判据，其材料只计入"裁切残边占比"。
6. 每个 Lattice 材料体素归属于最近的骨架点。若最近点是节点，归属于以该节点为端点、Neck Thickness 最大的那根 Ligament；并列时取索引最小者。
7. 薄壁占比 = 颈厚 < 最小壁厚的 Ligament 所占材料体积 ÷ Lattice 材料总体积（不含 Skin 与 Rim）。
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

**精度要求**：Neck Thickness 的测量误差 ≤ 0.05 mm。对照基准是：对 G1 各 Zone 的单个周期 Cell，在 h ≤ Cell/200 下按同一定义算出的参考值。由 15.1 的单元测试保证。

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
| `provenance` | 引擎包版本（PEP 440）、git sha 与 dirty 标记；每张数据表的 `id@version` 与 sha256；uv.lock 的 sha256；平台；Blender 完整版本与 build hash；Shape Bundle 各文件的 sha256 以及 `shape.json` |
| `metrics` | 体积、表面积、包围盒、实体壳数、空腔壳数、三角面数、简化实测最大偏差、按 Zone 汇总的 Island 删除情况、各阶段耗时、峰值内存 |
| `checks` | 每项 Printability Check 与 Donning Check 的级别、结果与位置 |
| `known_limitations` | P0 固定为三条："平整大底，仅限干燥室内试穿"；"Last 比例未经试穿校准"；"Cushioning 未经力学标定" |
| `outputs` | 文件名与 sha256 |
| `errors` | ErrorObject 数组，结构同 3.5 |

---

## 12. CLI 与失败模型

### 12.1 命令

```
shoegen build   <spec.json> -o <dir> [--timeout <s>] [--errors-json] [--force]
shoegen build   --plan <buildplan.json> -o <dir>        # 按存档重建
shoegen build   <spec.json> -o <dir> --crop <region>    # Crop Build
shoegen preview <spec.json> -o <dir>
shoegen coupon  cushioning -o <dir>                     # 每档 Cushioning 一块 Coupon
shoegen schema  --target full|llm|buildplan|manifest
```

### 12.2 Crop Region

| 名称 | 定义 | 用途 |
|---|---|---|
| `heel-collar` | HeelCounter 全高（Shell 中 x − x_heel ≤ `extent_x_ratio` × Last Length、位于 Sole Envelope 之外的部分），加上 t_mm ≥ L(s) − 20 mm **[初值]** 的整圈 Shell | 试穿：验证穿脱与包跟；包含 Rim |
| `sole-section` | 以 `forefoot_stack_point` 为中心、沿 X 方向长 40 mm **[初值]** 的整宽鞋底切块 | 排粉试验 |
| `heel-transition` | 以后跟中线与 Sole Envelope 边界的交点为中心、边长 40 mm **[初值]** 的立方体，包含跨坐标系过渡带（Rim 由 `heel-collar` 覆盖） | 0.25 mm 黄金回归 |

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
- **晶格与坐标系**：使用该档位的 Tuning Table 值，在独立的欧氏坐标系中生成；Transition 与 Grading 同 8.2。
- **边界**：四个侧面是开放的裁切面，顶面是 Footbed Layer，不加 Skin。
- **用途**：压缩试验，标定 Cushioning 的刚度量级。Build Manifest 中记录试块包含的 Cell 数。
- **流程**：Coupon Build 不经过 Shape Source，也不经过 Resolver 阶段 B。三层的 ρ 与 Cell 仍按 7.3 的欧氏部分做约束求解，检查点取试块中心的竖线。

### 12.4 退出码

| 码 | 含义 | `errors[].class` |
|---|---|---|
| 0 | 成功（可能带 soft 警告） | — |
| 1 | 内部错误 | internal |
| 2 | CLI 用法错误，包括输出目录非空 | — |
| 3 | DesignSpec 无效、版本不支持、Override 非法或 Infeasible | spec |
| 4 | Shape Source 失败：子进程失败、Bundle 违反不变量、Blender 版本不符、`--plan` 形状不一致 | shape |
| 5 | hard 级 Printability Check 失败，含阶段 B 的构建体积预检（`E_BUILD_VOLUME`） | printability |
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

- **已有实测**：0.25 mm 下鞋底大小区域的网格阶段峰值约 5.8 GB；整鞋包围盒（2.5–3.5 亿体素）上，Hildebrand–Rüegsegger 局部厚度（该方法已被 ADR 0009 否决）与困粉两项体素检查共 29–43 s，峰值约 5.5 GB。骨架化尚未实测，见第 20 节。
- **内存的阶段性**：网格阶段与校验阶段不同时驻留。体素类校验在释放场与网格之后进行。
- **资源预检**：由 Shape Bundle 体积与体素尺寸估算各阶段峰值内存，估算系数由基准测试测定 **[实现]**。估算值超过预算（默认 8 GB，可配置）时提前退出，退出码 6。
- **超时**：由 `--timeout` 控制。超时后写入 failed 状态的 Build Manifest，退出码 7。

---

## 14. 确定性与复现（ADR 0006）

- **逐字节一致**：同一份 BuildPlan，在同一平台、同一引擎版本（git sha 相同且无未提交改动，内置数据表随之相同）、同一 uv.lock、同一 Blender 完整版本（含 patch 与 build hash）下，Print Mesh 逐字节一致。
- **跨平台或 Blender patch 不同**：只保证指标等价，即体积 ±0.1%、表面积 ±0.5%、包围盒 ±0.05 mm、连通分量数相同。
- **Blender 版本**：主次版本不符即拒绝构建（退出码 4）；patch 版本不同只发警告并记录。
- **并行**：只在分块之间并行，合并顺序固定。
- **第三方步骤**：必须使用确定性实现。manifold3d 的 simplify 与 fast-simplification 已在单机上跨进程复测一致；跨平台的一致性待验证。

---

## 15. 测试

### 15.1 单元测试（不依赖 Blender）

**解析与规则**：
- 尺码换算、各体系端点值、Mondopoint 与 Toe Allowance 的例外规则
- 比例表
- Tuning Table 自检：范围见 7.2
- Feasibility Table 的读取、插值与定义域边界
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
- Feasibility Table 定义域覆盖：见 7.5 的覆盖要求

**几何**：
- 场函数
- Grading 与位置无关：同一 Grading 放在 x = 0 与 x = 280 处，连通分量数与 ρ 一致
- Zone 推导
- 鞋底侧壁上，距 ground 面集边界超过 t_skin 加 1 个体素的部分，Skin 面积为 0
- 坐标变换与 GLB 变换、镜像
- Island 清理不删除空腔壳：外盒内含反向腔时，腔被保留，"无封闭腔"检查失败
- Neck Thickness 精度：对照 10.2 的要求

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
- 每个用例存一份期望的 Adjustment 列表（路径与原因），逐条比对。G1 与 G3 的期望列表为空。G2 处在参数域角落，期望列表应包含前掌 Midsole 与 Footbed Layer 的修正，具体条目以 Feasibility Table 定稿后的实测为准。
- 另外以 0.25 mm 跑 `heel-transition` 与 `heel-collar` 两个 Crop Build，并执行完整的 hard 校验。

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
  spec/                         # DesignSpec、BuildPlan、Manifest 模型；Resolver；迁移；schema 导出；规范化与哈希
    data/                       # profiles/、sizes.v1、proportions.v1、tuning.v1、feasibility.v1、archetypes/、palette.v1
  shape/                        # Shape Source 接口；子进程调用；Shape Bundle 读写与不变量检查
    blender/                    # 在 Blender 内运行的控制笼脚本（build_bundle.py）
  zones/                        # Shell 与 Zone 推导、Grading 与 Transition 权重
  lattice/                      # TPMS 场、随形坐标、归一化与混合
  fuse/                         # 场合成、分块等值面提取、Island 清理、简化
  validate/                     # Printability Check、Donning Check、检查栅格
  export/                       # STL、GLB、Build Manifest、原子写入
  pipeline.py                   # 按 (ManufacturingMode, StructureType) 分派
  cli.py
scripts/                        # gen_feasibility.py、regen_fixtures.py
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
- Rim 是不可拉伸的实体条。
- 缺少 Surface Parameterization 的 Shape Source 无法使用。

### 18.2 P1 候选

- 鞋底花纹，并把 Outsole 的耐磨层从 Skin 的厚度约束中拆出来
- Diamond、sheet 变体、杆系晶格、Voronoi（届时引入 seed）
- 跨族 Transition
- 由引擎自动计算 Surface Parameterization
- 分性别的比例表
- 可拉伸的 Rim
- 形状参数的 Override
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
| Openness 与 HeelCounter 的最终 ρ | `tuning.v1` | Feasibility Table 实测，满足 7.2 的自检 |
| Feasibility Table 本身 | `feasibility.v1` | 离线脚本生成 |
| 资源预检的估算系数 | 代码常量 | 基准测试 |
| 标 [初值] 的 Collar、Toe Spring、HeelCounter 切分、Island 阈值、Donning 阈值、Crop Region 与 Coupon 尺寸 | 各数据表 | 第 16 节的实物结果 |

---

## 20. 风险与先行验证

以下几项是实现中最不确定的部分，应在相应模块开工前，先用最小原型验证。

| 风险 | 为什么不确定 | 先行验证 |
|---|---|---|
| 整鞋骨架化的耗时与内存 | Neck Thickness 需要对约 2.5–3.5 亿体素做曲线骨架化，尚未实测；分块骨架化在块边界的一致性也有难度 | 在 G1 的 Shell 与 Midsole 上实测；若超预算，再回到设计讨论 |
| Openness 定稿值是否落在可行区间 | 审查期间的颈厚实测用过两种不同算法，数值有差异；airy 0.20 在 Collar 端的颈厚约 1.045 mm，正好落在 1.0 + 0.05 的门槛上 | 先生成 Feasibility Table，再定 `tuning.v1`；airy 可能需要微调到约 0.21 |
| 跨坐标系过渡带的 Island 与薄壁 | 只在简化几何上实测过 | `heel-transition` 的 0.25 mm Crop Build |
| 程序化控制笼生成的 Last 的形状质量 | 决定了能否穿、好不好看，P0 无法试穿校准 | 先单独做 Shape Source，用 G1–G4 的形状目视检查，并比对比例表 |
