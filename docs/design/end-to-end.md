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
# 路线 B-knit:#47 的建模自由度变体 v1_base … v10_structure(config/lattice_variants.toml)
uv run --extra fast make-shoe --form B-knit --variant v7_hex --out-dir reports/make_shoe/knit
uv run --extra fast make-shoe --form B-knit --variant v6_image --pattern-image my.png   # 图像驱动线径;不给图用内置测试图
# B / B-knit 参数覆盖:在 --style / --variant 预设之上逐项改(可重复;列表用 JSON)
uv run --extra fast make-shoe --form B-knit --variant v7_hex --set pitch_mm=10 --set yarn_mm=2.0
uv run --extra fast make-shoe --form B-knit --variant v1_base --set 'raise_pts=[[0,45],[0.5,45],[0.64,0],[1,0]]'
uv run make-shoe --form B-knit --variant v7_hex --list-params      # 列出可用键、默认值与范围
```

`scripts/make_shoe.py` 是同一入口的薄封装。参数见 `make-shoe --help`:`--size`(43/44/45,默认 44)、`--template`(solid/hollow,默认取意图,再默认 solid)、`--process`(默认 `config/process_sls_tpu.toml`)、`--image`/`--pattern-image`(参考图 / 图案图)、`--pattern`(强制图案种类)。

## 流程

1. **意图**:`intent.parse_intent`(后端 rules / claude / deepseek)。打印限幅报告(被夹到设计空间的数值、不支持的请求);没有文字也没有图时用设计空间默认值。
2. **模板与内腔**(A、B 共用):模板包络 → 鞋楦摆位 → 内腔(鞋楦 + 松量,意图的脚背松量在这里生效)。
3. **A**:闭包(工艺文件允许时)→ `compose` → 图案层 `apply_pattern`(意图的 pattern,或风格标签预设)→ 可选鞋底点阵。**B**:`lattice_conformal.build_whole`,取 `config/lattice_conformal*.toml` 里的样式,轮廓函数经 `outline.clamp` 限幅后并入限幅报告。**B-knit**:同一份内腔(进程内计算,等价于 `scripts/lattice_variants.py --prep` 的 cache2.npz)交给 `lattice_variants.build_variant`;外观由 `--variant` 决定(未知名字会列出全部可选项),`check_variant` 的可打印标志进审计("变体可打印性":预览体素偏粗时记为注意)。`--pattern-image` / `--image` 只对图像驱动的变体(v6_image)生效。B 的外观由 `--style` 决定,意图里的 pattern / 风格标签不生效(报告里写明)。
4. **工艺检查与审计**:水密、单一实体、无封闭空腔(场上的封闭气泡 + STL 内壳)、最小壁厚(实心用射线,点阵用形态学开运算)、开口与排粉(最窄通道 ≥ `min_channel_mm`、粉末够不到的内腔)、成型尺寸(各机型)、Gate/D8(设计文档 §2,仅 A)、重量(占位密度 1.2 g/cm³)。
5. **输出**(`--out-dir`):`shoe.stl`、`report.md`(中文摘要)、`params.json`(输入、解析后的意图、限幅、参数、用时)、`audit.json`。

退出码:0 通过(可含"注意"项),1 有硬性检查未通过(文件仍会写出),2 输入错误(缺密钥、缺素材、尺码不存在)。

## 参数覆盖 `--set`

键是 spec 字段(B:`ConformalSpec`,B-knit:`VariantSpec`),值按 JSON 解析(数字、布尔、列表),不是 JSON 就当字符串;走 spec 自己的 `from_dict`,校验与 toml 加载器一致。未知键、类型不符、`--form A` 用 `--set` 都退出 2,并列出可用键。有设计范围的数值(`spec_overrides.RANGES`)会被夹到范围内,写进限幅报告(格式同意图 / 轮廓的限幅);引擎自己的限制(线径低于工艺下限、厚度小于最粗线径、体素大于最细特征 / 4)保持拒绝,在 25 秒的模板阶段之前就以引擎的原话退出 2(要看粗体素预览可加 `--set preview=true`)。已应用的覆盖与限幅写入 `report.md` 和 `params.json`(`overrides`、`override_clamps`)。

## 已知限制

- Gate/D8 的 `need_fit` 最大值那一行按 §2 严格判"不适用",四款模板都如此,D8 口径待定;命令把它记为"注意"而不是"未通过"。面积阈值同为暂定值。
- Gate 的硬补壁面积取推移前的原始值(`hard_fill_only_area_frac`),不含推移后的残余。
- 壁厚/筋宽上限 2% 是暂定值;重量用占位密度,等厂家数据。
- B 的开口检查在约 1 mm 的降采样场上做。
- 输出由 CC BY-NC-ND / 来源不明的素材衍生,不得分发,`reports/` 已被 git 忽略。
