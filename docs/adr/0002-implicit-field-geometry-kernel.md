# 晶格与融合采用隐式场内核

Last、Sole Envelope、Lattice、Rim、Skin 都表示为空间中的隐式场，Zone 之间的 Transition 与整体融合都是场运算（光滑并 / 交），每种产出（Print Mesh、Preview Mesh）各做一次等值面提取得到单一网格。选它是因为输出天然水密，TPMS 晶格是原生表达，Relative Density 的 Grading 就是阈值调制。拒绝了"把杆件建成网格再做网格布尔"：它表达不了 TPMS，大量杆件的布尔运算又慢又脆。

## Consequences

- Last、Sole Envelope、Rim、Skin 是距离场；TPMS 不是距离场（Gyroid 场值与真实距离之比在单个 Cell 内相差约 2 倍，梯度在临界点趋近零），参与以 mm 计的场运算前需要带截断的近似归一化。
- 水密是天然的，只有一个连通分量不是：与 Shoe Body 求交和跨坐标系 Transition 都会产生 Island，必须由 Island 删除与 hard 校验保证。
- Cell Size 的空间变化不得通过位置相关的坐标缩放（把 Cell(x) 代入 2π·x/Cell）实现：离原点越远相位畸变越大，实测在 x≈250 mm 处碎成数十块。Grading 与 Transition 一律采用固定周期场按权重混合，周期之间取整数比以保持公度。
- 分辨率受内存约束（开发机 16 GB），必须分块计算。
- 尖锐特征会被圆化到体素尺度。
- Print Mesh 面数巨大，因此 Preview Mesh 必须单独生成，而不是由 Print Mesh 简化而来。
