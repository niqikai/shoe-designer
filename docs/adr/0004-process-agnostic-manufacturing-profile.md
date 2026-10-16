# 工艺差异全部放进 ManufacturingProfile

打印工艺尚未确定（2026-10-16）。代码中不写死任何工艺阈值；所有阈值从带版本的 ManufacturingProfile 读取。先提供两份占位 profile：`pbf-tpu-generic@0`（粉末床 TPU，MJF/SLS）和 `dls-epu-generic@0`（光固化弹性体）。数值标注"占位，待服务商指南核实"，不得当作已验证的工艺窗口。

## Considered Options

- **先选定一种工艺再开发**：选择需要实物数据，而实物数据需要先有能生成试块的工具，会形成死锁。拒绝。
- **为每种工艺写一条流水线**：重复且早期无必要。拒绝。
- **数据驱动的 profile（采用）**：同一套流水线，换 profile 即换阈值和输出格式。

## Consequences

- Profile 字段：`min_strut_diameter_mm`、`min_wall_mm`、`min_pore_mm`、`min_fragment_volume_mm3`、`build_volume_mm`、`requires_drain_holes`、`requires_supports`、`output_formats`、`status`（`placeholder` / `vendor_guideline` / `measured`）。
- 检查分三类：
  - **构建前参数检查**（退出码 3）：杆直径 ≥ `min_strut_diameter_mm`，体素 ≤ 最小杆直径 / 4。设计本身不满足 profile，就不必生成网格。
  - **通用 hard**（退出码 5，对所有工艺都拦截）：网格水密、连通分量数 = 1、构建体积。
  - **soft**（只报告）：网格上的薄片（局部厚度 < `min_wall_mm`）、封闭腔、最小孔径、排粉、支撑。工艺相关项在 `status = placeholder` 时一律只报告；薄片在 M2 消除裁切断头后升为 hard。
- M1 用 Coupon 在两种工艺上打样，按压缩测试结果选择工艺，再把对应 profile 升级为 `vendor_guideline` 或 `measured`。
- 报告中的制造结论一律写 NOT_VERIFIED，直到实物验证。
