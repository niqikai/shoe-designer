# Shape Source 只交付形状，Shell 与 Zone 由引擎推导

Shape Source 的产出被限定为：Last（闭合、三角化）、Sole Envelope、Landmarks（含 Collar Line）以及可选的 Surface Parameterization。Shell 由引擎在 Footbed 以上把 Last 向外偏置 Wall Thickness 得到，Shoe Body = (Sole Envelope ∪ Shell) − Last；Zone 划分也由引擎根据这些输入与 BuildPlan 推导，不由 Blender 预先切好。这样未来的鞋楦模板、脚型扫描等 Shape Source 只需交付形状本身，不必懂得"怎么切中底"或"鞋面多厚"；Wall Thickness 成为纯 BuildPlan 参数，修改它无需重跑 Shape Source，Conformal Lattice"法向恰好一个 Cell"也能精确成立。

## Considered Options

- Blender 直接输出每个 Zone 的闭合实体：分区知识散落到每一种 Shape Source 中，扫描类来源几乎无法实现。
- Shoe Body = 完整鞋外包络 − Last，鞋面厚度由 Blender 生成的包络决定：细分曲面不是 Last 的精确偏置，壁厚改动必须重跑 Blender，且 Resolver 必须先于 Blender 运行。

## Consequences

- Surface Parameterization 是可选产物：缺失时由引擎根据 Last 与 Landmarks 自行计算（P1 实现），P0 由程序化控制笼提供。
- Last 网格不携带 UV；把 UV 塞进 PLY 会在接缝处拆开顶点，使 Last 不再闭合。
