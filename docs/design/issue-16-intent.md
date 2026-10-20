# #16 文字/图片 → 结构化意图(intent)

目标:把用户的文字或图片翻译成**严格的、流水线今天真能实现的**参数;做不到的需求明确报告,不静默忽略、不编造。AI 只做翻译,不产出几何。

实现:`src/shoe_designer/intent.py`;Schema:`schemas/intent.schema.json`;设计空间与预设:`config/intent.toml`;演示:`scripts/intent_demo.py`;测试:`tests/test_intent.py`。
依赖:jsonschema 在 dev 依赖组(`uv sync` 即装,CI 因此真正运行测试);Claude 后端需要可选 extra `uv sync --extra intent`(anthropic,延迟导入,测试用假 client 不需要它)。

## 1. 设计空间(今天能变的东西)

| 维度 | 取值 | 来源 |
|---|---|---|
| 模板 | `dlp_solid`、`dlp_hollow`(各码为同一设计的放缩) | 仅有的两个可用设计 |
| 图案 | PatternSpec(#13):none / hex_perforation / voronoi_perforation / wave_displacement / rib_displacement / image | #13 图案层 |
| 脚背松量 | 2.0–3.0 mm,默认 2.5 | `config/ease_zones.toml` instep 区 |

**不在设计空间**:跟高、鞋头型、鞋底厚度、颜色、鞋带/鞋口形式、靴/凉鞋等其他鞋类、脚型/宽度调整。这些**不进 schema**;用户提到时列入 `unsupported_requests`,不影响其他字段(清单与匹配规则在 `config/intent.toml` 的 `[[unsupported]]`,可扩充)。

## 2. Schema 字段

顶层与 `pattern` 均 `additionalProperties: false`,必填:`template, pattern, style_tags, instep_ease_mm, notes, unsupported_requests`。

- `template`:枚举 `dlp_solid | dlp_hollow`。
- `pattern`:PatternSpec,或 `null`(用户没提图案 → 取第一个 style tag 的预设,否则 `none`)。字段:`kind`(必填)、`scale_mm`、`open_ratio`、`depth_mm`、`zones`(side/top/toe/heel 的非空子集,默认 side+top)、`seed`、`image`、`image_use`(perforation | displacement)。
- `style_tags`:`sporty | casual | minimal | organic | geometric | knit` 的不重复列表。每个 tag 对应一组 PatternSpec 预设(`config/intent.toml [style.*]`)。
- `instep_ease_mm`:数值或 null(null = 保持配置默认)。
- `notes`:自由文本,**只记录,不参与流水线**。
- `unsupported_requests`:字符串列表,用户提到但做不到的需求。

### 参数收敛(clamp)边界

| 字段 | 范围 |
|---|---|
| `scale_mm` | [4, 30] |
| `open_ratio` | [0.05, 0.6](镂空类) |
| `depth_mm` | [0, 2.0](位移类) |
| `instep_ease_mm` | [2.0, 3.0] |
| `seed` | [0, 2³¹−1] |

范围在 schema 里以 `x-clamp` 标注(parser 输出允许越界,由代码收敛);`strict_schema()` 把它变成 `minimum/maximum`,最终结果必须通过它。范围在 `config/intent.toml [limits]`,测试保证两处一致。

**参数与 kind**:`scale_mm` 对所有非 none 类有效;`open_ratio` 只用于镂空类(及 `image` + perforation);`depth_mm` 只用于位移类(及 `image` + displacement)。用户给了但该 kind 用不上的参数会被丢弃,**并记入 changes**(不静默)。

**预设合并**:用户明确的 kind 优先;预设 kind 与之相同时只补用户没给的参数,不同时不借用预设参数(用全局默认);多个 tag 时第一个 tag 决定。缺省参数取 `[defaults]`。

### 数值原样输出、由代码收敛

系统提示(Claude 与 DeepSeek 共用)要求模型**照抄用户写的数值**,不要自己取整、封顶或猜范围;范围数值不写进提示,也不在给模型的 schema(`api_schema()`)里,模型无从预先收敛。越界值保留在原字段,由 `normalise` clamp 并写入 `changes`(原值、新值、原因),例如孔径 50 mm → 30、脚背松量 5 → 3.0。数值越界**不是**"不支持的需求",不应进 `unsupported_requests`,也不应把字段置 null。

已知行为:若模型仍把越界项放进 `unsupported_requests` 或置 null,代码**不做语义检测**,原样接受(其他字段不受影响,该数值就不会被收敛/报告)。真实模型验证时留意这一点。

### "镂空"的规则(模板 vs 图案)

- "镂空 / hollow / perforated / mesh / openwork" 选择的是**模板** `dlp_hollow`;不决定图案类型。
- 图案类型只由明确的图案词(六边形/蜂窝 → hex_perforation,泰森/voronoi → voronoi_perforation,波浪 → wave_displacement,罗纹/条纹 → rib_displacement)或 style tag 预设决定。
- 两者同时出现互不影响:"波浪纹的镂空运动鞋" = `dlp_hollow` + `wave_displacement`(+ sporty 标签,但 kind 已显式给出,预设参数不借用);"镂空鞋"(无图案词)= `dlp_hollow` + pattern `none`;"六边形图案"(无镂空词)= `dlp_solid` + `hex_perforation`。
- 同时说"实心"和"镂空"→ 保持默认 `dlp_solid` 并在 notes 记一笔。
- `unsupported_requests` 只放能力范围之外的需求(跟高、鞋头型、颜色等)。

## 3. 结果与报告

`parse_intent(text, backend, images=...)` 返回 `IntentResult`:
`ok`、`intent`(完整的、已收敛的 intent)、`changes`(每项:字段、原值、新值、原因——包括 clamp、因 kind 丢弃的参数、图案图覆盖 kind)、`warnings`、`errors`、`attempts`。

安全网:即便后端是 LLM,也会用 `[[unsupported]]` 的关键词扫描原文,后端漏列的不支持项会被追加并写入 `warnings`。

## 4. 失败行为

- 多余字段、类型错误、枚举外的值、缺必填、非有限数(NaN)、非 JSON:**拒绝**,错误含字段路径与原因。
- 校验失败**重试一次**(把错误清单回传给后端);再失败返回 `ok=False`、`intent=None`、`errors`,**不编造任何值**。
- 后端异常(网络、拒答 `refusal`、输出截断)不重试,直接返回失败。
- 图片问题(不存在、格式不对、超大、魔数不符、角色非法、多于一张图案图)在调用后端**之前**就失败。

## 5. 图片输入

`images=[(path, "reference" | "pattern")]`。格式 png/jpg/jpeg/webp,≤5 MiB,最多 4 张(至多 1 张图案图);检查存在、扩展名、文件头。

- `reference`:参考鞋图,交给模型读出风格 tag 与图案倾向;规则后端读不了图,只在 `notes` 里说明。
- `pattern`:图案图,**由代码**直接设为 `PatternSpec.image`、`kind=image`(`image_use` 取模型/默认 perforation);模型不得自己写路径(写了别的路径会被拒绝),若模型选了别的 kind,会被覆盖并记入 changes。

## 6. 后端

接口:`IntentBackend.complete(IntentRequest) -> str | dict`。

- `RuleBackend`:关键词规则(中英文词表在 `config/intent.toml [keywords.*]`),确定性,供测试与离线使用。
- `ClaudeBackend`:Claude Messages API,图片用 base64 block,`output_config.format` 为 JSON schema(`api_schema()` 去掉了结构化输出不支持的关键字,被去掉的约束由本地校验兜底),不用强制 `tool_choice`。模型 id 在 `config/intent.toml [claude]`,可用 `SHOE_INTENT_MODEL` 覆盖;API key 只由 SDK 从环境变量读取,不落盘、不打印。测试用假 client,不联网。

## 7. 后端对比:Claude 与 DeepSeek

| | `ClaudeBackend` | `DeepSeekBackend` |
|---|---|---|
| 调用方式 | anthropic SDK(可选 extra `intent`,延迟导入) | 标准库 `urllib` 直调 REST,零依赖 |
| 环境变量 | `ANTHROPIC_API_KEY`;模型覆盖 `SHOE_INTENT_MODEL` | `DEEPSEEK_API_KEY`;模型覆盖 `SHOE_INTENT_DEEPSEEK_MODEL` |
| 默认模型 | `claude-sonnet-5-5`(`[claude]`) | `deepseek-flash`(`[deepseek]`) |
| 结构约束 | `output_config.format` JSON schema:服务端强制 schema 形状 | `response_format=json_object`:**只保证是合法 JSON,不保证符合 schema** |
| 图片输入 | 支持(base64 block) | 仅 `deepseek-flash`(`vision_models` 配置);`deepseek-v4-pro` 不支持,带图请求会直接报错并提示换模型,不会静默丢图;图片只放 user 消息 |
| thinking | 模型自带,effort=low | 请求里 `thinking: {"type": "disabled"}`(简单抽取任务) |

两者共用同一套保障:`validate_intent`(jsonschema)→ clamp 并报告 → 校验失败重试一次 → 仍失败则返回失败、不编造。对 DeepSeek 这一套是真正的 schema 保证,而不是补充。

DeepSeek 的特殊处理:提示中必须有 "json" 字样和示例对象(示例用占位符,要求替换);空内容(官方说明偶尔出现)按"输出为空"走校验失败的那一次重试;`finish_reason=length`/`content_filter` 等视为后端失败,不重试;HTTP 非 200(401/402/429/5xx)返回失败,错误信息只含状态码与服务端 message,key 在错误信息中会被遮蔽。严格模式(tool calls strict,Beta)未使用,因为 schema 有可选字段。
