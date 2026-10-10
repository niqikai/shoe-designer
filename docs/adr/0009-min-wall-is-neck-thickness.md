# 最小壁厚以韧带颈厚计，而不是局部厚度

ManufacturingProfile 的"最小壁厚"在 Lattice 上按 Neck Thickness 判定：每根 Ligament 最细处的内切球直径。Resolver 的可行区间、Feasibility Table 与 Printability Check 使用同一定义；薄壁占比 = 颈厚低于限值的 Ligament 所占材料体积 ÷ 总材料体积，距 Shoe Body 边界 0.5 mm 以内的 Cut Edge 不计入 hard 判据，另作 soft 报告。粉末床打印中细特征的失效方式是细颈处断裂或烧结不良，颈厚直接对应它。

## Considered Options

- Hildebrand–Rüegsegger 局部厚度（增材制造软件常用）：会把被压扁的韧带两侧边缘判为"薄"（一根 2.0×1.16 mm 的扁韧带有 7.5% 体积被判薄），4 mm 壁下 airy、balanced 两档都不可行，全默认的鞋会被判 Infeasible；整鞋 Cut Edge 使薄壁占比达到约 1.8%，超过 1% 的 hard 阈值。要保留它就得把 Wall Thickness 加到 5 mm，或把 Openness 抬到 0.36 以上。

## Consequences

- 颈厚依赖骨架化，判据又落在 1.0 mm 附近，在 0.25 mm 体素上必须做到亚体素精度（与细栅格 h ≤ Cell/200 上的参考值误差 ≤0.05 mm），由单元测试保证。
- 扁韧带的侧缘、Cut Edge 上的薄楔不会令 Build 失败；它们只出现在 soft 报告中。
