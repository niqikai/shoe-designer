# shoe-designer

参数化生成 3D 打印晶格鞋的几何引擎。几何内核是 SDF 场景图；鞋底用杆状晶格，鞋面用网眼或仿针织的表面图案；鞋楦来自 STEP 文件；打印工艺通过 ManufacturingProfile 切换，放到最后再定。

- [架构总览](docs/architecture.md)
- [架构决策记录](docs/adr/)
- [实现计划](docs/implementation-plan.md)

当前状态：M0 端到端骨架已可运行（见下方快速开始）。

## 快速开始

```bash
uv sync
uv run shoegen build --graph examples/m0.json --last input/lasts/<file>.step \
    --profile pbf-tpu-generic@0 -o output/m0 [--voxel-mm 0.5] [--max-rss-gb 6] [--workers 4] \
    [--normalization normalization.json]
```

- `--graph` 是装配参数文件（杆半径、Cell、领口高度等都在里面改）。
- 不给 `--normalization` 时鞋楦坐标变换取恒等，并在报告里标记为"待用户确认"；鞋楦需已是 X 脚跟到脚尖、Y 宽、Z 向上。
- 输出（在 `-o` 目录）：`shoe.stl`、`graph.json`（二者逐位确定）、`report.json`、`manifest.json`。
- 报告里的制造结论恒为 `NOT_VERIFIED`。hard 检查失败（退出码 5）时不写 `shoe.stl`。

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 内部错误 |
| 2 | 用法错误 |
| 3 | 输入、Graph 或构建前参数检查不通过（不生成网格） |
| 4 | 鞋楦导入失败 |
| 5 | hard 检查失败 |
| 6 | 资源超限（RSS 看门狗） |
