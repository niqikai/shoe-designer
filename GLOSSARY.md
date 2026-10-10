# Lattice Shoe Engine

把参数化的鞋设计描述转换为可 3D 打印的全晶格一体鞋几何的引擎；未来 AI SaaS 的几何后端。

## Language

### 设计输入

**DesignSpec**（设计规格）:
面向用户与 LLM 的、带版本号的鞋设计描述；参数是语义化且有界的。
_Avoid_: 参数化 JSON、config、prompt

**Fit**（合脚）:
DesignSpec 中由顾客选择的尺码、Width 与 Side，与设计意图分开；BuildPlan 中由它解析出的尺寸量（含 Toe Allowance）不是同一概念。

**BuildPlan**（构建计划）:
由 DesignSpec 解析得到的、全部为具体数值的构建参数集合；连同引擎版本与 Shape Bundle 足以复现一次 Build。
_Avoid_: params、设置

**Tuning Table**（调校表）:
把 DesignSpec 语义档位映射为 BuildPlan 数值的带版本数据表。
_Avoid_: 映射表、preset、配置

**Tier Band**（档位带宽）:
一个语义档位允许 Adjustment 落入的 Relative Density 区间（以相邻档位名义值的中点为界），连同该 Zone 的 Cell 上限。

**Foot Length**（脚长）:
以 mm 表示的内部规范尺寸量；EU/UK/US 尺码先换算为 Last Length 再减去 Toe Allowance 得到它，Mondopoint 直接给出它。
_Avoid_: 用"尺码 / size"指代规范量

**Width**（宽度档位）:
跖宽与跖围的语义档位（narrow / standard / wide）。
_Avoid_: 肥瘦、楦型

**Side**（脚别）:
Build 所生成的脚：左脚、右脚或一双；左脚是右脚的镜像。

**Cushioning**（缓震）:
Midsole 软硬程度的语义档位（soft / balanced / firm），后跟与前掌可分别设定。
_Avoid_: 硬度、hardness

**Openness**（通透度）:
Upper 晶格疏密程度的语义档位（airy / balanced / dense）。
_Avoid_: 透气度

**Override**（覆盖）:
DesignSpec 中由专家直接指定、Resolver 不得再改动的某个 BuildPlan 参数值。

**Resolution Note**（解析说明）:
BuildPlan 中由引擎代为确定、但不偏离 DesignSpec 意图的参数取值，例如随形 Zone 周向 Cell 数的取整。
_Avoid_: 把它称作 Adjustment

**Adjustment**（修正）:
BuildPlan 中因 ManufacturingProfile 约束而偏离 DesignSpec 所表达意图、但仍在 Tier Band 内的参数值。
_Avoid_: clamp、钳制、自动修复

**Infeasible**（不可行）:
DesignSpec 的意图在 ManufacturingProfile 约束下无法于 Tier Band 内实现的状态。
_Avoid_: 用 Adjustment 掩盖

### 制造

**ManufacturingMode**（制造方式）:
鞋被制造出来时的零件拆分与装配方式。
_Avoid_: 工艺、process

**OnePiece3DPrint**:
整只鞋作为单一 Part 一次打印成型、无需任何装配或粘接的 ManufacturingMode。

**Part**（零件）:
一次打印成型的单个实体。
_Avoid_: 用 Zone 指代零件

**ManufacturingProfile**（制造配置）:
一组具体的工艺、材料与机器约束（最小壁厚、最小孔喉、构建体积等）；与 ManufacturingMode 相互独立。
_Avoid_: 打印机设置、用 ManufacturingMode 指代工艺

**Neck Thickness**（颈厚）:
一根 Ligament 最细处的内切球直径；最小壁厚约束针对它。
_Avoid_: 局部厚度、壁厚（作度量时）

**Pore Throat**（孔喉）:
Lattice 空隙中粉末能够通过的最大球径。
_Avoid_: 孔径（含义模糊）、晶格间隙

**Feasibility Table**（可行性表）:
按 Lattice Family、Variant、Cell 形状、混合权重与 Relative Density 给出等值面阈值、Neck Thickness 与 Pore Throat 的带版本数据表。

### 形状

**Archetype**（鞋型原型）:
鞋的整体结构形态类别（例如低帮一脚蹬、系带鞋、穆勒鞋），决定鞋有哪些 Zone。
_Avoid_: 款式、style

**Shape Source**（形状来源）:
产出 Last、Sole Envelope、Landmarks 与（可选的）Surface Parameterization 的来源，例如程序化控制笼、鞋楦模板、脚型扫描。

**Shape Bundle**（形状包）:
Shape Source 的落盘产出及其来源信息。

**Last**（鞋楦）:
定义鞋内脚腔形状的闭合体。
_Avoid_: 脚模、foot model

**Last Length**（楦长）:
Last 从后跟点到鞋尖点的长度；EU/UK/US 尺码按它定义，等于 Foot Length 加 Toe Allowance。
_Avoid_: 鞋长

**Toe Allowance**（前余量）:
Last Length 超出 Foot Length 的部分。

**Ball Width**（跖宽）:
脚在第一与第五跖骨头处的宽度。

**Ball Girth**（跖围）:
绕过第一与第五跖骨头的足部或 Last 围长。

**Instep Height**（脚背高）:
脚背最高点到脚底平面的高度。

**Heel Width**（后跟宽）:
脚跟最宽处的宽度。

**Long Heel Girth**（兜跟围）:
绕过后跟后下端与足背—小腿交界处（踝前横纹）的围长；决定脚能否穿过 Collar。

**Surface Parameterization**（楦面参数化）:
Shell 所覆盖的 Last 表面上的二维坐标；Conformal Lattice 的面内方向由它确定。
_Avoid_: UV（作领域词时）、展开图

**Sole Envelope**（鞋底包络）:
鞋底外表面所围成的闭合区域。
_Avoid_: 鞋体包络、Shoe Envelope、鞋模、外壳

**Ground Surface**（接地面）:
Sole Envelope 外表面中朝下的底面部分，包括 Toe Spring 上翘段；Outsole 与其 Skin 由到它的距离界定。
_Avoid_: 用"地面"指代（地面专指 z = 0 平面）

**Footbed**（脚床）:
脚底所踩的表面，即 Last 的底面。
_Avoid_: 鞋垫、insole（鞋垫是独立零件）

**Footbed Edge**（脚床边线）:
Footbed 与 Last 侧面的分界闭合线；Surface Parameterization 中高度坐标的零点。

**Shell**（薄壳）:
Footbed Edge 与 Collar Line 之间、距 Last 表面不超过 Wall Thickness 的区域。
_Avoid_: 鞋面层、外壳

**Wall Thickness**（壁厚）:
Shell 从 Last 表面向外的厚度。

**Shoe Body**（鞋体）:
Sole Envelope 与 Shell 之并再减去 Last 后的区域；鞋的全部材料都位于其中。
_Avoid_: 用"鞋体"泛指整只鞋、其外表面或材料本身

**Landmark**（关键点）:
Last 或 Sole Envelope 上命名的构造参考点、参考线或参考面，例如后跟点、鞋尖点、Footbed Edge、Collar Line、Ground Surface。
_Avoid_: 锚点、marker

**Collar**（鞋口）:
脚进入鞋的开口及其周边。
_Avoid_: 领口、opening

**Collar Line**（鞋口线）:
Shell 的上边界，即鞋口沿 Last 表面的轮廓线。

**Collar Height**（鞋口高度）:
后跟中线处 Collar Line 到 Footbed 的高度。

**Throat**（喉口）:
Collar 前端在脚背中线上的点；位置用占 Last Length 的比例表示。

**Ankle Dip**（踝下凹）:
Collar Line 在内、外踝下方的下凹。

**Heel Stack**（后跟厚度）:
Last Length 12% 处的中线上，从地面到 Footbed 的鞋底厚度。
_Avoid_: 跟高（易与高跟鞋的跟高混淆）

**Forefoot Stack**（前掌厚度）:
Last Length 75% 处的中线上，从地面到 Footbed 的鞋底厚度。

**Drop**（落差）:
Heel Stack 与 Forefoot Stack 之差。
_Avoid_: offset

**Toe Spring**（鞋头翘度）:
Last 底面与鞋底在鞋头处一同离地上翘的程度。

### 分区

**Zone**（分区）:
Shoe Body 的功能性划分，每一点恰属一个 Zone；每个 Zone 有自己的 Fill 与一组 Lattice 参数，参数可在 Zone 内 Grading。
_Avoid_: region、area、part（part 指零件）

**Fill**（填充）:
一个 Zone 内材料的组织方式；FullLattice 下所有 Zone 的 Fill 都是 Lattice。

**Grading**（渐变）:
同一 Zone 内 Lattice 参数随位置连续变化。
_Avoid_: 过渡、gradient

**Transition**（过渡带）:
Zone 边界两侧、相邻 Zone 的 Lattice 连续过渡的带状区域。
_Avoid_: 接缝、seam、渐变（渐变专指 Zone 内的变化）

**Outsole**（大底）:
Shoe Body 最底部的 Zone，由紧贴 Ground Surface 的 Skin 与其上的一层 Lattice 组成。

**Midsole**（中底）:
Sole Envelope 内除 Outsole 以外的 Zone，包括 Footbed 下方的主体与高出 Footbed、与 Shell 重叠的侧壁；承担缓震。

**Footbed Layer**（脚床层）:
Midsole 中紧贴 Footbed 的一层，Cell 更小、Relative Density 更高；属于 Midsole，不是 Zone，但在约束求解中单独校验。
_Avoid_: 鞋垫、insole、脚床过渡

**Upper**（鞋面）:
Shell 中位于 Sole Envelope 之外、包裹脚背与脚侧的 Zone。

**HeelCounter**（主跟）:
Shell 中位于 Sole Envelope 之外、包裹脚跟后部与两侧、提供后跟支撑的 Zone。
_Avoid_: 后跟杯、heel cup

### 结构

**StructureType**（结构类型）:
整只鞋的材料在空间中的组织方式。

**FullLattice**（全晶格）:
一种 StructureType：鞋体材料除 Rim 与 Skin 外全部是 Lattice。
_Avoid_: 晶格鞋底

**Lattice**（晶格）:
由重复的 Cell 构成的多孔结构材料。
_Avoid_: 网格、mesh（这两个词保留给三角网格）

**Cell**（胞元）:
Lattice 中的单个重复单元。
_Avoid_: unit、单元格

**Cell Size**（胞元尺寸）:
Cell 在各方向上的周期长度；Conformal Lattice 的法向 Cell Size 等于 Wall Thickness。
_Avoid_: 单元大小、晶格大小

**Ligament**（韧带）:
network Lattice 中连接两个节点的杆状材料段。
_Avoid_: strut、杆（杆系晶格专用）

**Lattice Family**（晶格族）:
由同一种几何定义生成的一类 Lattice，例如 Gyroid、Diamond。
_Avoid_: 晶格类型、lattice type

**Lattice Variant**（晶格变体）:
同一 Lattice Family 的形态变体：sheet（片状）或 network（骨架状）。

**Relative Density**（相对密度）:
Lattice 中材料体积占其所在空间体积的比例。
_Avoid_: 填充率、infill、单说"密度"

**Euclidean Lattice**（欧氏晶格）:
Cell 沿固定直角坐标轴排列的 Lattice。

**Conformal Lattice**（随形晶格）:
Cell 沿 Surface Parameterization 展开、法向恰好一个 Cell 的 Lattice。
_Avoid_: 共形晶格

**Rim**（收边）:
沿 Collar Line、位于 Shoe Body 内的实体收边条。
_Avoid_: 包边、border

**Skin**（皮）:
位于 Shoe Body（或 Coupon）内、紧贴指定外表面的薄实体层，厚度有上限。
_Avoid_: 外壳、实体面、大底耐磨层

**Cut Edge**（裁切残边）:
Lattice 被 Shoe Body 边界切断处形成的薄楔形材料。

### 产出

**Build**（构建）:
引擎完整运行一次并产出结果的过程；输入是一份 DesignSpec、一份 BuildPlan，或一种 Coupon。
_Avoid_: job、run、渲染

**Print Build**（打印构建）:
产出整鞋 Print Mesh 的 Build。

**Preview Build**（预览构建）:
只产出 Preview Mesh 的快速 Build；与对应的 Print Build 共用同一份 BuildPlan 几何参数。

**Crop Region**（裁切区域）:
由 Landmark 与 BuildPlan 参数在引擎坐标系中定义的命名空间区域。
_Avoid_: 用 Zone 指代

**Crop Build**（裁切构建）:
以 Shoe Body 与一个 Crop Region 的交集为几何的 Build；与整鞋共用同一份 BuildPlan。
_Avoid_: 用 Coupon 或"试样"指代裁切件

**Coupon**（试块）:
不取自鞋几何、按 Tuning Table 档位独立生成的标准小块几何，用于标定。
_Avoid_: 样块、demo、用它指代裁切件

**Print Mesh**（打印件）:
单只鞋的全分辨率、水密、只有一个连通分量的三角网格，用于打印。
_Avoid_: 模型（含义过宽）、单连通（数学上指无洞，晶格体不满足）

**Preview Mesh**（预览件）:
单只鞋的低分辨率三角网格，用于可视化；不保证与 Print Mesh 逐面一致。

**Island**（碎块）:
与最大实体壳不相连的孤立材料块。
_Avoid_: 漂浮物

**Printability Check**（可打印性校验）:
针对每个将被打印的网格（Print Mesh、Crop Build 与 Coupon 的产出）的可打印性几何检查，分 hard 与 soft 两级。

**Donning Check**（穿脱检查）:
比较 Collar 开口周长与 Long Heel Girth 估值、判断脚能否穿入的几何检查。

**Build Manifest**（构建清单）:
一次 Build 的完整记录：输入、BuildPlan、所用 Shape Bundle 的来源与指纹、引擎版本与运行平台、Adjustment、指标与校验报告。
