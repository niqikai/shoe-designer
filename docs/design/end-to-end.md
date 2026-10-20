# S7 端到端命令 `make-shoe`

对应 issue #10 的 S7 与路线图 #23。一条命令从用户输入走到最终 STL 加审计报告;各阶段都是已合并的模块,命令只负责串起来(`src/shoe_designer/make_shoe.py`)。

## 用法

```bash
export SHOE_ASSETS_ROOT=~/Downloads            # 鞋楦与模板网格,不在 git 里
uv sync --extra fast                            # 强烈建议:embreex 光线查询,没有它信封阶段会慢到不可用

# 路线 A:模板重建 + 图案层(意图由规则后端离线解析)
uv run --extra fast make-shoe "波浪纹的镂空运动鞋" --size 44 --out-dir reports/make_shoe/a
# 同上,加鞋底点阵(#14)
uv run --extra fast make-shoe "极简风格" --lattice-sole --out-dir reports/make_shoe/a2
# 意图用 DeepSeek / Claude(密钥只从环境变量读,不会打印)
DEEPSEEK_API_KEY=... uv run --extra fast make-shoe "sporty perforated sneaker" --backend deepseek
# 路线 B:点阵鞋(#46),样式与轮廓函数可选
uv run --extra fast make-shoe --form B --style k2_triangle --out-dir reports/make_shoe/b
uv run --extra fast make-shoe --form B --outline '[{"fn":"sole_wall","sole_mm":20}]'
```

`scripts/make_shoe.py` 是同一入口的薄封装。参数见 `make-shoe --help`:`--size`(43/44/45,默认 44)、`--template`(solid/hollow,默认取意图,再默认 solid)、`--process`(默认 `config/process_sls_tpu.toml`)、`--image`/`--pattern-image`(参考图 / 图案图)、`--pattern`(强制图案种类)。

## 流程

1. **意图**:`intent.parse_intent`(后端 rules / claude / deepseek)。打印限幅报告(被夹到设计空间的数值、不支持的请求);没有文字也没有图时用设计空间默认值。
2. **模板与内腔**(A、B 共用):模板包络 → 鞋楦摆位 → 内腔(鞋楦 + 松量,意图的脚背松量在这里生效)。
3. **A**:闭包(工艺文件允许时)→ `compose` → 图案层 `apply_pattern`(意图的 pattern,或风格标签预设)→ 可选鞋底点阵。**B**:`lattice_conformal.build_whole`,取 `config/lattice_conformal*.toml` 里的样式,轮廓函数经 `outline.clamp` 限幅后并入限幅报告。B 的外观由 `--style` 决定,意图里的 pattern / 风格标签不生效(报告里写明)。
4. **工艺检查与审计**:水密、单一实体、无封闭空腔(场上的封闭气泡 + STL 内壳)、最小壁厚(实心用射线,点阵用形态学开运算)、开口与排粉(最窄通道 ≥ `min_channel_mm`、粉末够不到的内腔)、成型尺寸(各机型)、Gate/D8(设计文档 §2,仅 A)、重量(占位密度 1.2 g/cm³)。
5. **输出**(`--out-dir`):`shoe.stl`、`report.md`(中文摘要)、`params.json`(输入、解析后的意图、限幅、参数、用时)、`audit.json`。

退出码:0 通过(可含"注意"项),1 有硬性检查未通过(文件仍会写出),2 输入错误(缺密钥、缺素材、尺码不存在)。

## 已知限制

- Gate/D8 的 `need_fit` 最大值那一行按 §2 严格判"不适用",四款模板都如此,D8 口径待定;命令把它记为"注意"而不是"未通过"。面积阈值同为暂定值。
- Gate 的硬补壁面积取推移前的原始值(`hard_fill_only_area_frac`),不含推移后的残余。
- 壁厚/筋宽上限 2% 是暂定值;重量用占位密度,等厂家数据。
- B 的开口检查在约 1 mm 的降采样场上做。
- 输出由 CC BY-NC-ND / 来源不明的素材衍生,不得分发,`reports/` 已被 git 忽略。
