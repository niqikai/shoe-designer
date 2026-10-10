# BuildPlan 从 P0 起按 Part × Zone × Fill 组织

BuildPlan 的几何段在 P0 就组织为 `parts[]`：每个 Part 含 `shell`（Wall Thickness、面内 Cell 等 Shell 级参数）、`zones{名称: {frame, fill{kind, …}}}` 与按 id 键控的 `features{}`（Rim、Skin）。尽管 OnePiece3DPrint 下永远只有一个 Part `body`，FullLattice 下 `fill.kind` 永远是 `lattice`，也不简化。原因是 Override 以 BuildPlan 的 JSON Pointer 为键，Build Manifest 与输出文件名（`{side}.{part}.stl`）也都引用这一结构，它一旦对外就很难再改；而未来的多零件 ManufacturingMode 与 HybridLattice（`fill.kind = solid`）在这一结构下只是新增数据。Zone 集合与各 Zone 的坐标系由 Archetype 数据文件声明，不写死在代码里。

## Considered Options

- 扁平 BuildPlan（P0 最简单）：第一个多零件模式就要破坏性迁移所有已存 spec 中的 Override，以及所有 Build Manifest 的消费方。

## Consequences

- `features` 按 id 键控而不是数组，Override 路径不依赖元素顺序。
- 必须在整圈 Shell 内保持一致的参数（面内 Cell、周向 Cell 数）放在 `shell` 级，不放在 Zone 级，避免 Upper 与 HeelCounter 被分别覆盖而产生相位接缝。
- 鞋底欧氏 Zone 只存一个基准 Cell（Midsole）；Outsole 与 Footbed Layer 的 Cell 派生为其一半，以保持固定周期场混合时的公度。
