# 工艺差异全部放进 ManufacturingProfile

打印工艺尚未确定，且决定放到最后（M4）再定（2026-10-16）。代码中不写死任何工艺阈值；所有阈值从带版本的 ManufacturingProfile 读取。先提供两份占位 profile：`pbf-tpu-generic@0`（粉末床 TPU，MJF/SLS）和 `dls-epu-generic@0`（光固化弹性体）。数值标注"占位，待服务商指南核实"，不得当作已验证的工艺窗口。

## Considered Options

- **先选定一种工艺再开发**：选择需要实物数据，而实物数据需要先有能生成试块的工具，会形成死锁。拒绝。
- **为每种工艺写一条流水线**：重复且早期无必要。拒绝。
- **数据驱动的 profile（采用）**：同一套流水线，换 profile 即换阈值和输出格式。

## Consequences

- Profile 字段：`min_strut_diameter_mm`、`min_wall_mm`、`min_pore_mm`、`min_fragment_volume_mm3`、`build_volume_mm`、`requires_drain_holes`、`requires_supports`、`output_formats`、`status`（`placeholder` / `vendor_guideline` / `measured`）。
- 检查分三类：
  - **构建前参数检查**（退出码 3）：杆直径 ≥ `min_strut_diameter_mm`，体素 ≤ 最小杆直径 / 4。设计本身不满足 profile，就不必生成网格。
  - **通用 hard**（退出码 5，对所有工艺都拦截）：网格水密、连通分量数 = 1、构建体积。"连通分量"指实体：网格的表面壳里有向体积为正的壳（实体的外表面）。体积为负的壳是实体内部封闭空腔的内表面，不是另一个零件，不计入这一项，而是在记录里单列为 `cavity_shells`，并由 soft 的封闭腔检查报告（M0.9 实现，M0 评审确认）。因此一个带封闭空腔的单一实体通过这一项；空腔里漂着的一块材料（第二个正体积壳）不通过。体积小于 `min_fragment_volume_mm3` 的碎块先被删除并记录。
  - **soft**（只报告）：网格上的薄片（局部厚度 < `min_wall_mm`）、封闭腔、最小孔径、排粉、支撑。工艺相关项在 `status = placeholder` 时一律只报告；薄片在 M1 消除裁切断头后升为 hard。
  - 薄片检查在体素栅格上做，分辨率有限：厚度在 `min_wall − 2h` 与 `min_wall + 2h` 之间的壁，是否被标记取决于它与栅格的相对位置（M0 评审实测：h = 0.5 mm、min_wall = 1.0 mm 时，偏离栅格 0.13 mm 的 1.2 mm 平板被标记，对齐的不被标记；1.5 mm 平板和 2 mm 杆都不被标记）。报告里写出这个区间（`flagged_reliably_below_mm`、`not_flagged_above_mm`）。升为 hard 之前要么用更细的体素，要么把厚度检查改为在网格上实测。
- M1–M3 期间 profile 一直是 `placeholder`。M4 用 Coupon 在两种工艺上打样，按试验结果选择工艺，再把对应 profile 升级为 `vendor_guideline` 或 `measured`。
- 报告中的制造结论一律写 NOT_VERIFIED，直到实物验证。
- M0 实现了 soft 里的薄片、封闭腔和最小孔径（解析估算）；排粉和支撑没有实现，在报告里列为 `not_run` 并写明原因，不省略、不写 pass（M0 评审）。
