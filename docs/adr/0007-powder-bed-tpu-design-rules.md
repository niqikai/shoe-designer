# 以粉末床 TPU 为目标工艺及由此而来的设计规则

P0 以粉末床熔融 TPU 为目标工艺：无需支撑、任意晶格可打，但未烧结的粉末必须能从每个空隙中清出。由此得到一组在几何上看不出缘由的规则：鞋底侧壁保持开放晶格作为排粉通道；Footbed 与 Upper 不加 Skin（脚床的舒适性改由更细更密的 Footbed Layer 提供）；不允许封闭腔；最小 Neck Thickness 1.0 mm（ADR 0009）；最小 Pore Throat 分两级——薄层（Shell 中各 Zone、Footbed Layer、Outsole，以及 Midsole 中 Cell 小于基准 Cell 的区域，不论原因是厚度不足还是处在过渡带内）≥2.0 mm，其余 Midsole 芯部 ≥4.0 mm。薄层的排粉路径短，且紧邻大孔喉的芯部或自由表面；芯部的粉要穿过长路径，需要更大的孔喉。

## Considered Options

- FDM TPU：需要自支撑设计与悬垂限制，真正的 3D 晶格很难打。
- 光固化弹性体（DLS/SLA）：需要支撑与排液孔，特征可以更细，但后处理与材料路线不同。
- 孔喉按"到自由表面的几何距离"分级：Outsole 晶格层压在 Skin 上、离侧壁很远，会被判为芯部，而它的孔喉只有约 2.6 mm，必然不可行。

## Consequences

- Resolver 以 [由 Neck Thickness 决定的 ρ 下限, 由 Pore Throat 决定的 ρ 上限] 作为可行区间；名义 ρ 不在区间内时，先在放大 Cell 确实有效时放大 Cell（不超过 Cell 上限），再在 Tier Band 内移动 ρ，仍不可行即判为 Infeasible。
- 薄壁按材料体积计占比，困粉按 Shoe Body 内空隙体积计占比；超过阈值（默认 1%）即 hard 失败。困粉只计"d 球放得进、但出不去"的空隙，不计任何 d 球都覆盖不到的角隅空隙。
- 实打整鞋之前，先打鞋底裁切件验证排粉。
