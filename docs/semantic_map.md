# 自然语言与参数映射

参数规则按里程碑上线。M4 已在 M1 造型、M2 晶格和 M3 制造设置上提供本地口语命令，几何仍由 `build_shoe(params)` 自动处理。下列没有明确数值的增量是对话默认解释，依据受限局部形变与渐进调整原则；用户明确数值优先，最终值按 schema 范围截断并说明。旧版设计省略 `midsole_structure` 时仍采用实心中底，只有显式选择 `lattice` 才启用晶格。表格用于助手理解语义，命令直接识别的文字变体以末尾规则区为准；尚未登记的同义表达需补充规则或用 `--set` 明确参数。

| 用户表达 | 参数修改 | 说明 |
| --- | --- | --- |
| 右脚／左脚 | `foot_side=right`／`left` | 单只生成；左脚由右脚基准镜像得到 |
| EU N 码／N 码 | `size_eu=N` | 35–46 的整数；未明确尺码体系时说明按 EU 解释，名义尺码须试穿验证 |
| 大一码／小一码 | 当前 `size_eu` 加／减 1 | 尺码采用非等比缩放，不能视为精确合脚保证 |
| 窄脚／标准脚宽／宽脚 | `foot_width=narrow`／`standard`／`wide` | 宽度档位形变限制在 ±15% |
| 再宽一点／再窄一点 | 脚宽档位上调／下调一档 | 档位顺序为 narrow → standard → wide；已到边界则说明保持边界 |
| 圆头 | `toe_roundness=0.10`，`toe_height_scale=1.08`，`edge_radius_mm=3` | 同时增加鞋头横向圆宽程度、鞋头空间与底缘圆弧，避免仅加宽薄鞋头 |
| 鞋头再圆一点／收一点 | 当前 `toe_roundness` 加／减 0.03；圆一点时 `toe_height_scale` 加 0.02 | 局部形变幅度按各自范围截断 |
| 鞋头高一点／低一点 | 当前 `toe_height_scale` 加／减 0.05 | 增减鞋头空间，不改变鞋口高度 |
| 低帮／鞋口低一些（明确切换款型时） | `collar_style=low`，初始 `collar_height_mm=75` | 用户已确认当前基准；低帮范围 60–85 mm |
| 中帮／鞋口高一些（明确切换款型时） | `collar_style=mid`，初始 `collar_height_mm=100` | 中帮范围 86–115 mm |
| 鞋口提高／降低 N 毫米 | 在当前 `collar_height_mm` 上加／减 N | 保持款型；超范围截断并说明结果 |
| 厚底 | `heel_sole_mm=32`，`forefoot_sole_mm=24` | 默认厚底造型；厚度含外底与中底，从基准楦底到最下底面垂直计量 |
| 鞋底更厚一点／更薄一点 | 当前脚跟、前掌底厚各加／减 3 mm | 两处各按范围截断，报告实际调整量 |
| 脚跟／前掌加厚 N 毫米 | 对应 `heel_sole_mm`／`forefoot_sole_mm` 加 N | 局部厚度控制，不承诺回弹或稳定性变化 |
| 底边更圆一点／更利落一点 | 当前 `edge_radius_mm` 加／减 0.5 mm | 调整外底外缘圆角，范围 0.5–4 mm |
| 圆滑收口／去掉薄尖尾 | `boundary_rounding_mm=1.2` | 几何默认解释：用连续形态学收口处理中底自由末端和鞋口边沿，保留楦底接触曲面；按 0–1.5 mm 及有效鞋面围条厚度的一半截断并说明。半径只是算法控制值，不能据此承诺最小壁厚、曲率或力学性能，须重新测量生成网格 |
| 恢复原始裁切／关闭边缘收口 | `boundary_rounding_mm=0` | 旧版设计缺省为 0；恢复原始自由末端裁切后仍须通过制造检查才能导出 |
| 板鞋底／鞋底宽一圈 | `outsole_flare_mm=6` | 轮廓向外扩，允许范围 4–8 mm |
| 鞋底再外扩一点／收一点 | 当前 `outsole_flare_mm` 加／减 1 mm | 在当前楦底轮廓基础上调整 |
| 鞋面更厚一点／更薄一点 | 当前 `upper_thickness_mm` 加／减 0.3 mm | 鞋面围条目标厚度 1.8–4 mm，M3 另检查生成网格厚度 |
| 晶格中底／镂空中底 | `midsole_structure=lattice` | 未指定细节时采用 Gyroid，目标密度脚跟 0.32、足弓 0.45、前掌 0.35，杆径／等效片厚 2 mm；当前设计中已有明确值时保留 |
| 实心中底 | `midsole_structure=solid` | 保留已记录的晶格参数，供再次切换时使用 |
| Gyroid／螺旋曲面晶格 | `midsole_structure=lattice`，`lattice_type=gyroid` | 周期曲面结构；名称用于几何识别，不等于性能评价 |
| Diamond／钻石曲面晶格 | `midsole_structure=lattice`，`lattice_type=diamond` | Diamond 周期曲面；不将其解释为 Octet 杆网 |
| Octet／八面体杆网 | `midsole_structure=lattice`，`lattice_type=octet` | 周期杆网结构；实际连接和制造能力需校验 |
| 脚跟／足弓／前掌密度 N | 对应 `lattice_density_heel`／`lattice_density_arch`／`lattice_density_forefoot` 设为 N | 分区目标相对密度范围 0.20–0.60；明确写百分数时除以 100，例如 35% → 0.35；省略结构时沿用当前中底结构，实心时说明本次参数暂不影响几何 |
| 脚跟／足弓／前掌更疏一点／更密一点 | 对应分区密度减／加 0.05 | 工程默认步长为 5 个百分点，便于比较局部几何变化；不是强度或重量增减 5% |
| 杆更细一点／更粗一点 | 当前 `lattice_rod_mm` 减／加 0.2 mm | 范围 1.5–3 mm；对 Gyroid、Diamond 为未裁切解析片层的保守厚度尺度，对 Octet 为最稀疏区杆径；保持密度时增粗会同步放大单胞，参数值不代表已测得的最小厚度 |
| 快速预览／先看效果 | `resolution=preview` | 较低体素分辨率，适合对话迭代 |
| 精细导出／导出打印文件 | `resolution=export` | 较高体素分辨率；通过必要几何及制造检查后才导出打印候选；失败时说明拦截原因，设备和材料仍需实物试样验证 |

“更软一点”“更稳一点”等性能表达采用下列明确声明的设计假设。密度只是周期单元的目标几何控制，实际材料体积分数还受杆径／片厚、表皮、外底、区域连接及分辨率影响；不同晶格类型不以同一密度视为等效性能。

| 用户表达 | 工程默认解释 | 依据与汇报要求 |
| --- | --- | --- |
| 脚跟更软一点／前掌更软一点 | 晶格已开启时，对应分区密度减 0.05 | 在材料、晶格类型等其余条件相同时，减少局部材料占比可能降低刚度；只作设计倾向，不能保证柔软或回弹结果，先打印局部试样比较 |
| 更软一点（未指定部位） | 晶格已开启时，脚跟和前掌密度各减 0.05，足弓保持当前值 | 默认保留足弓区域；汇报中明确这只是初版假设，用户明确区域优先 |
| 足弓更稳一点／更有支撑 | 晶格已开启时，足弓密度加 0.05 | 增加局部材料占比作为支撑设计假设；稳定性还受鞋型、材料和人体使用方式影响，不能只由密度判定 |
| 更稳一点（未指定部位） | 晶格已开启时，脚跟和足弓密度各加 0.05 | 默认针对后足与足弓作渐进调整；不承诺防扭、抗侧翻或伤害预防效果 |
| 轻一点 | 晶格已开启时，脚跟和前掌密度各减 0.05，足弓保持当前值 | 用料可能下降；报告实际几何用料变化，重量还取决于材料密度和打印工艺，不报未经实测的克数 |

上述性能表达在实心中底时只记录为意图，并说明晶格参数尚未影响几何；不静默改变结构。“回弹更好”没有可靠的单一参数方向，保留当前值并以试样测量作为下一步。“更透气”不直接修改密度，因为中底孔隙并不等于鞋内通风。M3 已加入工艺、材料及设备设置；外底花纹尚未加入当前 schema。

仅说“鞋口高一点／低一点”而没有切换款型时，默认高度加／减 5 mm 并保留当前款型。每次真实修改先备份当前设计并追加变更原因；新表达先查此表，缺失时记录推断依据。

## M3 制造设置

用户已确认默认 SLS；TPU、Shore A 90、250 × 250 × 250 mm 成型空间仍为工程假设，应在汇报中说明。这里只支持 TPU；材料标称硬度不能直接代表晶格整鞋硬度。SLS／MJF 的 1.2 mm、FDM 的 1.5 mm 是项目的固定经验最小壁厚／杆径阈值，由制造 profile 提供；实际设备、粉末或丝材、方向和后处理可能要求更厚，不能作为供应商保证。

| 用户表达 | 参数修改 | 解释与约束 |
| --- | --- | --- |
| 用 SLS／MJF／FDM 打印 | `print_process=SLS`／`MJF`／`FDM` | 切换制造检查 profile，保留输入鞋型参数；FDM 还需关注支撑及悬垂，不把空间放得下视为可打印 |
| TPU／用 TPU | `material=TPU` | 目前唯一支持材料；其他材料需扩展制造 profile，不能只改名称复用结论 |
| TPU 90A／邵氏 A 硬度 N | `tpu_shore_a=N` | 标称 Shore A，70–95 的整数；只记录材料选择，不直接修改晶格密度或推算回弹 |
| 换更软的 TPU／硬度低一点 | 当前 `tpu_shore_a` 减 5 | 仅在明确讨论材料时使用此默认步长；材料可购牌号与工艺兼容性另行确认，不保证鞋变软的幅度 |
| 换更硬的 TPU／硬度高一点 | 当前 `tpu_shore_a` 加 5 | 按 70–95 截断并告知；只是目标材料规格，不改变几何 |
| 打印空间 X × Y × Z 毫米 | `build_size_x_mm=X`、`build_size_y_mm=Y`、`build_size_z_mm=Z` | 按设备轴序，每轴 100–1000 mm；“250 方”默认三个轴均 250 mm，说明该假设 |
| 打印边距 N 毫米 | `build_margin_mm=N` | 范围 0–10；各轴有效空间减去 2N。XY 两侧各 N；最低 Z=0 接触底板，Z 的两份余量留在上方 |
| 自动摆放／斜着放看能否装下 | `build_orientation=auto` | 搜索刚体摆放，只给成型空间适配建议；不缩小鞋、不拆分鞋、不改变设计坐标中的几何 |
| 保持原方向／按设计方向摆放 | `build_orientation=as_designed` | 用设计坐标轴的包围盒检查；装不下时报告尺寸，不偷偷旋转或缩放 |
| 能否打印／制造检查 | 沿用当前制造设置并运行校验 | 说明当前工艺和设备假设、通过项与未通过项；数字几何校验不能替代供应商工艺确认和局部试样 |

“更软一点”仍按上方的分区密度假设处理，只有明确提到 TPU 或材料硬度时才修改 `tpu_shore_a`，避免一次意图同时改变材料与几何。无法确认新工艺或材料时保留当前设计参数并说明所缺信息；不得把未知材料映射为 TPU。制造参数同样经 schema 默认值、类型校验和范围截断，修改前保留版本备份。

## M4 本地口语规则

`tools/apply_edit.py` 从下方规则区读取表达式和参数动作，上面的表提供人话解释，两者在同一文件维护。相对调整从当前有效值出发；多条要求按文字顺序执行，整轮再统一做范围和关联截断。长表达优先于它包含的短表达，避免“TPU 90A”被当成只有“TPU”。未知或否定表达不会只执行一半，工具会保留当前设计并说明尚未识别的部分；助手可明确解释后用 `--set`，新增规则须在此记录依据。数值支持阿拉伯数字和简单中文数字。

“更圆一点”未明确部位时默认鞋头；“变红”“外底花纹”“3MF”等尚未实现的要求不会被吞掉。裸“更软一点”改晶格分区；明确“换更软的 TPU”才改材料硬度。回弹和透气只记录意图，不虚构可测性能。这些新增语句变体依据既有表中的渐进步长，并不扩展几何参数。

| 版本操作 | 行为 |
| --- | --- |
| 撤销／撤销上一次修改 | 恢复上一项实际修改的父版本参数，生成递增新版本；连续撤销继续回退 |
| 回到第 N 版／恢复 v### | 恢复指定历史有效参数，保留历史并生成新版本 |
| 对比上一版 | 当前与上一物理版本比较参数及匹配预览，不运行 Blender |
| 对比第 N 版和第 M 版 | 比较两版；缺失或损坏预览缓存时明确只比较参数 |

版本号支持阿拉伯数字及简单中文数字，可写“回到第 四 版”。`--compare` 零参数表示上一版与当前，一参数表示指定版与当前，两参数指定两版；`--list` 只列出现有版本。版本号由工具管理，不能作为设计补丁传入。

<!-- M4_RULES_START -->
```json
{
  "filler_pattern": "设计(?:一双|一只)?|做(?:一双|一只)?|帮我|我(?:想要|要|想)|请|麻烦|然后|并且|以及|和|鞋子|这双鞋|一下|吧|的",
  "rules": [
    {"id": "size", "pattern": "(?:EU\\s*)?(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*码", "actions": [{"field": "size_eu", "op": "set", "value": {"group": "n"}}], "note": "尺码按名义 EU 解释，合脚程度需量脚和试穿确认。"},
    {"id": "size_up", "pattern": "大(?:一|1)码", "actions": [{"field": "size_eu", "op": "add", "value": 1}]},
    {"id": "size_down", "pattern": "小(?:一|1)码", "actions": [{"field": "size_eu", "op": "add", "value": -1}]},
    {"id": "right_foot", "pattern": "右脚", "actions": [{"field": "foot_side", "op": "set", "value": "right"}]},
    {"id": "left_foot", "pattern": "左脚", "actions": [{"field": "foot_side", "op": "set", "value": "left"}]},
    {"id": "width_standard", "pattern": "标准脚宽", "actions": [{"field": "foot_width", "op": "set", "value": "standard"}]},
    {"id": "width_narrow", "pattern": "窄脚", "actions": [{"field": "foot_width", "op": "set", "value": "narrow"}]},
    {"id": "width_wide", "pattern": "宽脚", "actions": [{"field": "foot_width", "op": "set", "value": "wide"}]},
    {"id": "wider", "pattern": "(?:脚(?:宽)?|鞋(?:子)?)?(?:再|更)?宽(?:一|1)?点", "actions": [{"field": "foot_width", "op": "step", "value": 1, "choices": ["narrow", "standard", "wide"]}]},
    {"id": "narrower", "pattern": "(?:脚(?:宽)?|鞋(?:子)?)?(?:再|更)?窄(?:一|1)?点", "actions": [{"field": "foot_width", "op": "step", "value": -1, "choices": ["narrow", "standard", "wide"]}]},
    {"id": "round_toe", "pattern": "圆头", "actions": [{"field": "toe_roundness", "op": "set", "value": 0.1}, {"field": "toe_height_scale", "op": "set", "value": 1.08}, {"field": "edge_radius_mm", "op": "set", "value": 3}], "note": "圆头同时调整鞋头圆度、高度和底缘圆角。"},
    {"id": "rounder_toe", "pattern": "(?:鞋头)?(?:再|更)?圆(?:一|1)?点", "actions": [{"field": "toe_roundness", "op": "add", "value": 0.03}, {"field": "toe_height_scale", "op": "add", "value": 0.02}], "note": "未指定部位的“更圆一点”按鞋头解释。"},
    {"id": "narrower_toe", "pattern": "鞋头(?:再|更)?收(?:一|1)?点", "actions": [{"field": "toe_roundness", "op": "add", "value": -0.03}]},
    {"id": "toe_height_高", "pattern": "鞋头(?:再|更)?高(?:一|1)?点", "actions": [{"field": "toe_height_scale", "op": "add", "value": 0.05}]},
    {"id": "toe_height_低", "pattern": "鞋头(?:再|更)?低(?:一|1)?点", "actions": [{"field": "toe_height_scale", "op": "add", "value": -0.05}]},
    {"id": "collar_low", "pattern": "低帮", "actions": [{"field": "collar_style", "op": "set", "value": "low"}, {"field": "collar_height_mm", "op": "set", "value": 75}]},
    {"id": "collar_mid", "pattern": "中帮", "actions": [{"field": "collar_style", "op": "set", "value": "mid"}, {"field": "collar_height_mm", "op": "set", "value": 100}]},
    {"id": "collar_relative_提高", "pattern": "鞋口(?:再|更)?提高(?:一|1)?点", "actions": [{"field": "collar_height_mm", "op": "add", "value": 5}], "note": "仅调整鞋口高度，保持当前低帮／中帮款型。"},
    {"id": "collar_relative_降低", "pattern": "鞋口(?:再|更)?降低(?:一|1)?点", "actions": [{"field": "collar_height_mm", "op": "add", "value": -5}], "note": "仅调整鞋口高度，保持当前低帮／中帮款型。"},
    {"id": "collar_relative_高", "pattern": "鞋口(?:再|更)?高(?:一|1)?点", "actions": [{"field": "collar_height_mm", "op": "add", "value": 5}], "note": "仅调整鞋口高度，保持当前低帮／中帮款型。"},
    {"id": "collar_relative_低", "pattern": "鞋口(?:再|更)?低(?:一|1)?点", "actions": [{"field": "collar_height_mm", "op": "add", "value": -5}], "note": "仅调整鞋口高度，保持当前低帮／中帮款型。"},
    {"id": "collar_mm_提高", "pattern": "鞋口提高\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?:毫米|mm)", "actions": [{"field": "collar_height_mm", "op": "add", "value": {"group": "n", "scale": 1}}]},
    {"id": "collar_mm_降低", "pattern": "鞋口降低\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?:毫米|mm)", "actions": [{"field": "collar_height_mm", "op": "add", "value": {"group": "n", "scale": -1}}]},
    {"id": "collar_absolute", "pattern": "鞋口(?:高度)?(?:设为|改为|为|到)?\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?:毫米|mm)", "actions": [{"field": "collar_height_mm", "op": "set", "value": {"group": "n"}}]},
    {"id": "thick_sole", "pattern": "厚底", "actions": [{"field": "heel_sole_mm", "op": "set", "value": 32}, {"field": "forefoot_sole_mm", "op": "set", "value": 24}]},
    {"id": "sole_relative_厚", "pattern": "(?:鞋底)?(?:再|更)?厚(?:一|1)?点", "actions": [{"field": "heel_sole_mm", "op": "add", "value": 3}, {"field": "forefoot_sole_mm", "op": "add", "value": 3}]},
    {"id": "sole_relative_薄", "pattern": "(?:鞋底)?(?:再|更)?薄(?:一|1)?点", "actions": [{"field": "heel_sole_mm", "op": "add", "value": -3}, {"field": "forefoot_sole_mm", "op": "add", "value": -3}]},
    {"id": "sole_zone_加厚", "pattern": "(?P<zone>脚跟|前掌)加厚\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?:毫米|mm)", "actions": [{"field": {"group": "zone", "map": {"脚跟": "heel_sole_mm", "前掌": "forefoot_sole_mm"}}, "op": "add", "value": {"group": "n", "scale": 1}}]},
    {"id": "sole_zone_减薄", "pattern": "(?P<zone>脚跟|前掌)减薄\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?:毫米|mm)", "actions": [{"field": {"group": "zone", "map": {"脚跟": "heel_sole_mm", "前掌": "forefoot_sole_mm"}}, "op": "add", "value": {"group": "n", "scale": -1}}]},
    {"id": "sole_zone_absolute", "pattern": "(?P<zone>脚跟|前掌)(?:底厚|厚度)(?:设为|改为|为)?\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?:毫米|mm)", "actions": [{"field": {"group": "zone", "map": {"脚跟": "heel_sole_mm", "前掌": "forefoot_sole_mm"}}, "op": "set", "value": {"group": "n"}}]},
    {"id": "edge_圆", "pattern": "底边(?:再|更)?圆(?:一|1)?点", "actions": [{"field": "edge_radius_mm", "op": "add", "value": 0.5}]},
    {"id": "edge_利落", "pattern": "底边(?:再|更)?利落(?:一|1)?点", "actions": [{"field": "edge_radius_mm", "op": "add", "value": -0.5}]},
    {"id": "finish", "pattern": "圆滑收口|去掉薄尖尾", "actions": [{"field": "boundary_rounding_mm", "op": "set", "value": 1.2}]},
    {"id": "no_finish", "pattern": "恢复原始裁切|关闭边缘收口", "actions": [{"field": "boundary_rounding_mm", "op": "set", "value": 0}]},
    {"id": "skate_sole", "pattern": "板鞋(?:底)?|鞋底宽一圈", "actions": [{"field": "outsole_flare_mm", "op": "set", "value": 6}]},
    {"id": "flare_外扩", "pattern": "鞋底(?:再|更)?外扩(?:一|1)?点", "actions": [{"field": "outsole_flare_mm", "op": "add", "value": 1}]},
    {"id": "flare_收", "pattern": "鞋底(?:再|更)?收(?:一|1)?点", "actions": [{"field": "outsole_flare_mm", "op": "add", "value": -1}]},
    {"id": "upper_厚", "pattern": "鞋面(?:再|更)?厚(?:一|1)?点", "actions": [{"field": "upper_thickness_mm", "op": "add", "value": 0.3}]},
    {"id": "upper_薄", "pattern": "鞋面(?:再|更)?薄(?:一|1)?点", "actions": [{"field": "upper_thickness_mm", "op": "add", "value": -0.3}]},
    {"id": "lattice", "pattern": "晶格中底|镂空中底", "actions": [{"field": "midsole_structure", "op": "set", "value": "lattice"}]},
    {"id": "solid", "pattern": "实心中底", "actions": [{"field": "midsole_structure", "op": "set", "value": "solid"}]},
    {"id": "gyroid", "pattern": "Gyroid|螺旋曲面晶格", "actions": [{"field": "midsole_structure", "op": "set", "value": "lattice"}, {"field": "lattice_type", "op": "set", "value": "gyroid"}]},
    {"id": "diamond", "pattern": "Diamond|钻石曲面晶格", "actions": [{"field": "midsole_structure", "op": "set", "value": "lattice"}, {"field": "lattice_type", "op": "set", "value": "diamond"}]},
    {"id": "octet", "pattern": "Octet|八面体杆网", "actions": [{"field": "midsole_structure", "op": "set", "value": "lattice"}, {"field": "lattice_type", "op": "set", "value": "octet"}]},
    {"id": "density", "pattern": "(?P<zone>脚跟|足弓|前掌)密度(?:设为|改为|为)?\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?P<pct>%|百分比)?", "actions": [{"field": {"group": "zone", "map": {"脚跟": "lattice_density_heel", "足弓": "lattice_density_arch", "前掌": "lattice_density_forefoot"}}, "op": "set", "value": {"group": "n", "percent_group": "pct"}}], "note": "这是周期单胞目标密度，实心中底时暂不影响几何。"},
    {"id": "zone_疏", "pattern": "(?P<zone>脚跟|足弓|前掌)(?:再|更)?疏(?:一|1)?点", "actions": [{"field": {"group": "zone", "map": {"脚跟": "lattice_density_heel", "足弓": "lattice_density_arch", "前掌": "lattice_density_forefoot"}}, "op": "add", "value": -0.05}], "note": "分区调整只是几何设计假设；实际软硬、回弹和重量需局部试样确认。", "when": "lattice"},
    {"id": "zone_密", "pattern": "(?P<zone>脚跟|足弓|前掌)(?:再|更)?密(?:一|1)?点", "actions": [{"field": {"group": "zone", "map": {"脚跟": "lattice_density_heel", "足弓": "lattice_density_arch", "前掌": "lattice_density_forefoot"}}, "op": "add", "value": 0.05}], "note": "分区调整只是几何设计假设；实际软硬、回弹和重量需局部试样确认。", "when": "lattice"},
    {"id": "zone_软", "pattern": "(?P<zone>脚跟|足弓|前掌)(?:再|更)?软(?:一|1)?点", "actions": [{"field": {"group": "zone", "map": {"脚跟": "lattice_density_heel", "足弓": "lattice_density_arch", "前掌": "lattice_density_forefoot"}}, "op": "add", "value": -0.05}], "note": "分区调整只是几何设计假设；实际软硬、回弹和重量需局部试样确认。", "when": "lattice"},
    {"id": "arch_support", "pattern": "足弓(?:再|更)?稳(?:一|1)?点|足弓更有支撑", "actions": [{"field": "lattice_density_arch", "op": "add", "value": 0.05}], "note": "增加足弓材料占比作为设计假设，不承诺稳定性。", "when": "lattice"},
    {"id": "soft_or_light", "pattern": "(?:再|更)?(?:软|轻)(?:一|1)?点", "actions": [{"field": "lattice_density_heel", "op": "add", "value": -0.05}, {"field": "lattice_density_forefoot", "op": "add", "value": -0.05}], "note": "默认调整脚跟与前掌，保留足弓；只作设计倾向，建议打印局部样比较。", "when": "lattice"},
    {"id": "stable", "pattern": "(?:再|更)?稳(?:一|1)?点", "actions": [{"field": "lattice_density_heel", "op": "add", "value": 0.05}, {"field": "lattice_density_arch", "op": "add", "value": 0.05}], "note": "默认调整脚跟和足弓；不承诺防扭或抗侧翻。", "when": "lattice"},
    {"id": "rod_细", "pattern": "杆(?:径)?(?:再|更)?细(?:一|1)?点", "actions": [{"field": "lattice_rod_mm", "op": "add", "value": -0.2}], "note": "保持密度时，改变杆径／片厚尺度也会改变单胞大小。"},
    {"id": "rod_粗", "pattern": "杆(?:径)?(?:再|更)?粗(?:一|1)?点", "actions": [{"field": "lattice_rod_mm", "op": "add", "value": 0.2}], "note": "保持密度时，改变杆径／片厚尺度也会改变单胞大小。"},
    {"id": "preview", "pattern": "快速预览|先看效果", "actions": [{"field": "resolution", "op": "set", "value": "preview"}]},
    {"id": "export", "pattern": "精细导出|导出打印文件|导出(?:STL|GLB)", "actions": [{"field": "resolution", "op": "set", "value": "export"}], "note": "高精度导出仍须通过必要制造检查；当前格式为 STL 和 GLB。"},
    {"id": "rebound", "pattern": "回弹(?:再|更)?好|更有回弹", "actions": [], "note": "回弹没有可靠的单一参数方向，保留参数并记录意图；建议用局部试样测量。"},
    {"id": "breathable", "pattern": "(?:再|更)?透气", "actions": [], "note": "中底孔隙不等于鞋内通风，本轮保留几何；透气设计需要单独明确鞋面方案。"},
    {"id": "process_SLS", "pattern": "(?:用\\s*)?SLS(?:\\s*打印)?", "actions": [{"field": "print_process", "op": "set", "value": "SLS"}]},
    {"id": "process_MJF", "pattern": "(?:用\\s*)?MJF(?:\\s*打印)?", "actions": [{"field": "print_process", "op": "set", "value": "MJF"}]},
    {"id": "process_FDM", "pattern": "(?:用\\s*)?FDM(?:\\s*打印)?", "actions": [{"field": "print_process", "op": "set", "value": "FDM"}]},
    {"id": "shore", "pattern": "TPU\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*A|邵氏\\s*A\\s*硬度\\s*(?P<n2>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))", "actions": [{"field": "tpu_shore_a", "op": "set", "value": {"group": "n", "fallback_group": "n2"}}, {"field": "material", "op": "set", "value": "TPU"}], "note": "硬度为目标材料规格，不能直接代表整鞋软硬。"},
    {"id": "TPU", "pattern": "(?:用\\s*)?TPU", "actions": [{"field": "material", "op": "set", "value": "TPU"}]},
    {"id": "material_软", "pattern": "换(?:再|更)?软的\\s*TPU|(?:TPU|材料)硬度(?:再|更)?低(?:一|1)?点", "actions": [{"field": "tpu_shore_a", "op": "add", "value": -5}], "note": "只改变目标 TPU 牌号硬度，不同时修改晶格密度。"},
    {"id": "material_硬", "pattern": "换(?:再|更)?硬的\\s*TPU|(?:TPU|材料)硬度(?:再|更)?高(?:一|1)?点", "actions": [{"field": "tpu_shore_a", "op": "add", "value": 5}], "note": "只改变目标 TPU 牌号硬度，不同时修改晶格密度。"},
    {"id": "build_size", "pattern": "打印空间\\s*(?P<x>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*[×xX*]\\s*(?P<y>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*[×xX*]\\s*(?P<z>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?:毫米|mm)?", "actions": [{"field": "build_size_x_mm", "op": "set", "value": {"group": "x"}}, {"field": "build_size_y_mm", "op": "set", "value": {"group": "y"}}, {"field": "build_size_z_mm", "op": "set", "value": {"group": "z"}}]},
    {"id": "build_cube", "pattern": "(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*方", "actions": [{"field": "build_size_x_mm", "op": "set", "value": {"group": "n"}}, {"field": "build_size_y_mm", "op": "set", "value": {"group": "n"}}, {"field": "build_size_z_mm", "op": "set", "value": {"group": "n"}}], "note": "“方”按 X、Y、Z 三轴相同的毫米构建空间解释。"},
    {"id": "margin", "pattern": "打印边距\\s*(?P<n>-?(?:\\d+(?:\\.\\d+)?|[零〇一二两三四五六七八九十百千]+(?:点[零〇一二三四五六七八九]+)?))\\s*(?:毫米|mm)", "actions": [{"field": "build_margin_mm", "op": "set", "value": {"group": "n"}}]},
    {"id": "orientation_auto", "pattern": "自动摆放|斜着放(?:看能否装下)?", "actions": [{"field": "build_orientation", "op": "set", "value": "auto"}]},
    {"id": "orientation_design", "pattern": "保持原方向|按设计方向摆放", "actions": [{"field": "build_orientation", "op": "set", "value": "as_designed"}]},
    {"id": "check", "pattern": "能否打印|制造检查|重新生成|生成预览", "actions": [], "note": "沿用当前工艺和设备假设重新校验；几何通过不代替实物制造验证。"}
  ]
}
```
<!-- M4_RULES_END -->
