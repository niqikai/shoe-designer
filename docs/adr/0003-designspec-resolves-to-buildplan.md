# DesignSpec 与 BuildPlan 分两层

LLM 和用户只写 DesignSpec（语义化、有界、带版本号），引擎再把它解析为全显式数值的 BuildPlan，并随 Build 一起落盘。让 LLM 直接写胞元尺寸、杆径这类底层几何参数，极易产出物理上不合理的组合；分两层把"设计意图"与"可复现的几何参数"分开，DesignSpec 成为面向 LLM 的稳定契约，BuildPlan 连同 Build Manifest 记录的引擎版本与 Shape Bundle 保证复现（复现的精度见 ADR 0006）。DesignSpec 保留可选的 Override，供专家与调试使用，但不出现在面向 LLM 的 schema 投影中。

## Consequences

- BuildPlan 与 DesignSpec 各带独立的版本号；Override 以 BuildPlan 路径为键，因此 BuildPlan 的结构也是对外契约。
- 补打以存档的 BuildPlan 为准（`build --plan`），跳过解析。
