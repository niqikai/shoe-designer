# 薄壳 Zone 用随形晶格，鞋底 Zone 用欧氏晶格

同一只鞋里混用两种 Cell 坐标系：Shell 中的 Zone（Upper、HeelCounter）用沿 Surface Parameterization 展开、法向恰好一个 Cell 的 Conformal Lattice；鞋底 Zone（Midsole、Outsole）用 Euclidean Lattice。区别不在"薄"，而在"曲"：Shell 是包裹脚的曲面薄壳，欧氏晶格在其中会被斜切成不完整的 Cell 和 Island；鞋底各层近似平面，欧氏晶格配合竖向 Grading 即可。

## Considered Options

- 全鞋欧氏晶格、Upper 用小 Cell：实现最简单，但曲面薄壳中碎片化严重。
- Upper 用打孔薄壳（2D 图案）：严格说不是 3D Lattice，与 FullLattice 的定义冲突。

## Consequences

- 法向 Cell Size 被 Wall Thickness 锁死。按 Neck Thickness 计（ADR 0009），3 mm 壁下 airy、balanced 两档的颈厚低于 1.0 mm（实测 6×6×3：ρ 0.15 → 0.70–0.75 mm，0.22 → 0.80–1.02 mm），因此 Wall Thickness 定为 4 mm，P0 只用 Gyroid network。放大面内 Cell 能否补救，由 Feasibility Table 判定，不作为前提。
- Shell 区域拓扑上是环带：Surface Parameterization 以归一化高度 τ（Footbed Edge 为 0，Collar Line 为 1）的等值环为周向，周向周期，接缝在后跟中线（落在 HeelCounter 内）。Collar Line 的绝对高度沿周向从后跟约 45 mm 变到 Throat 约 150 mm，绝对弧长的等值线不闭合，所以周向必须按 τ 定义。整圈只有一个整数周向 Cell 数 N = round(τ = 0.5 环的周长 / 面内 Cell)，因此 Upper 与 HeelCounter 共用同一面内 Cell，只以 Relative Density 区分。
- 随形 Cell 三个方向各不相同：周向宽 = τ 环周长 / N，从 Collar 端约 6 mm 变化到 Footbed Edge 端约 11 mm（名义面内 8 mm 指中间高度）；高度方向按弧长恒为面内 Cell；法向等于 Wall Thickness。Feasibility Table 以两个比值描述 Cell 形状，Resolver 在两端分别校验。
- N 依赖 Shape Bundle 的实测周长，所以随形部分的约束求解只能在 Shape Source 运行之后进行。
- 欧氏与随形 Zone 之间没有可插值的参数，Transition 只能在场层面做截断归一化后的交叉淡化，会产生少量 Island，由 Island 删除与 hard 校验兜底。
- 鞋底竖向分层之间的过渡宽度取 min(6 mm, 较薄一侧层厚)，局部 Midsole 太薄时以粗细两种 Cell 的固定周期场混合实现 Cell 缩小。
