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
