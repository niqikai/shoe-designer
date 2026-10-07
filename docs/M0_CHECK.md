# 开工检查：M0 与模板初查

检查日期：2026-10-07（Asia/Shanghai）。

## 当前边界

已完成环境检测、素材清单与只读解压、官方 `.blend` 初查，以及本地 Git 初始化。尚未编写业务代码；待用户确认后进入 M0.5，完成时再次停下展示结果。

## 架构理解

- `designs/current.json` 是设计的唯一真相；对话只调整参数，默认值、范围及相关参数约束集中管理。引擎统一通过 `build_shoe(params)` 生成几何。
- 每次设计修改前保存 `designs/history/v###.json`，追加中文变更原因；撤销、回到指定版本也产生可追踪的操作记录。参数版本与代码 Git 版本分别记录。
- JSON 解耦对话与 Blender。生成链为参数校验及截断 → 生成 → 网格与工艺校验 → 允许导出的结果 → 四视角预览与中文报告。失败状态不得冒充可打印通过。
- 语义规则集中在 `docs/semantic_map.md`，新词记录推断依据。性能只描述预估倾向，建议打印局部样验证。
- 鞋楦读取收敛到 `engine/shoe/last.py`。只维护一只鞋，另一只镜像；参数限定在任务第一阶段范围内。
- 素材和衍生网格保留本机；Git 跟踪代码、参数和文字文档，忽略 `assets/` 与 `out/`。

待确认的一点：将“不得直接手写网格／顶点数据”理解为禁止在对话层和业务代码中硬编码或手改顶点清单；允许参数化引擎内部使用 Blender 形变／修改器，以及 M2 所要求的 Marching Cubes 自动抽取网格。

## M0 环境结果

| 项目 | 实测 |
| --- | --- |
| 系统 | macOS 27.0.1，arm64 |
| `BLENDER_PATH` | 未设置 |
| PATH 入口 | `/opt/homebrew/bin/blender`，Homebrew 包装脚本 |
| 实际程序 | `/Applications/Blender.app/Contents/MacOS/Blender` |
| Blender | 5.2.2 LTS，build hash `d13f752e3b9c` |
| Blender 自带 Python | 3.13.13 |
| Blender 自带 NumPy | 2.3.4 |
| 无头测试 | `background=True`，退出码 0 |
| 系统 Python | 3.9.6；不作为几何依赖环境 |
| Git | 2.54.0（Apple Git-157）；目录原先没有仓库 |

测试命令：

```sh
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python-expr 'import bpy,sys,numpy; print(bpy.app.version_string, bpy.app.background, sys.version.split()[0], numpy.__version__)'
```

执行环境限制：同一无头启动在 Codex 沙箱内于 Metal 初始化阶段退出，退出码 139；获准在沙箱外运行后通过。此 macOS 构建只接受 Metal GPU 后端，不能用 OpenGL 参数规避。后续 Blender 运行需沿用已获准的执行方式；无需重新安装 Blender。

未安装任何额外依赖。M2 再按选定实现评估是否需要项目虚拟环境与 `scikit-image` 等包。

## 素材清单与保护

已先执行 `unzip -l`，共 12 个文件，解压总大小 52,748,471 字节，再解压至 `assets/last/raw/`：

- `LICENSE.txt`
- `sneaker_ankle_height.stl`
- `sneaker_last.stp`
- `sneaker_last_highpoly.stl`
- `sneaker_last_lowpoly.obj`
- `sneaker_reference_collar_instep.stl`
- `sneaker_reference_overhangs.stl`
- `sneaker_reference_shoe_height.stl`
- `sneaker_reference_texturemap.stl`
- `sneaker_reference_thickness.stl`
- `sneaker_sole_thickness_region.stl`
- `Zellerfeld_sneaker_template_files.blend`

ZIP 与解压文件权限为 `0444`，解压目录权限为 `0555`。ZIP 的 SHA-256 在解压前后相同；内容未改动。详细本地清单保存在 `out/inspection/source_manifest.json`。

## 官方 `.blend` 的初步查看

使用 Blender 无头读取，禁用自动执行场景脚本，未保存或覆盖原场景。统计包含集合、对象、材质、节点组和自定义属性；完整本地清单位于 `out/inspection/template_inventory.json`。

| 内容 | 结果 |
| --- | --- |
| 文件版本元数据 | `(4, 1, 24)` |
| 场景／集合 | 1 个场景，1 个集合 `Collection` |
| 对象 | 9 个，全部是网格，名称与 9 个 STL/OBJ 对应 |
| 高模 | 51,547 顶点、103,090 三角面 |
| 低模 | 516 顶点、528 多边形，三角化后 1,028 面 |
| 材质 | 1 个 `Dots Stroke`，未分配给这 9 个对象 |
| Geometry Nodes | 无节点组 |
| 修改器／约束 | 所有对象均无修改器和约束 |
| 自定义属性 | 对象、网格、集合无自定义设计属性；场景／材质仅残留 Cycles 设置 |
| 内嵌文本 | `License`，未自动执行 |
| 外部库 | 无 |

高模坐标包围盒尺寸为 **276.656 × 101.026 × 135.952**（X/Y/Z），X 从约 0.031 到 276.688，Z 从约 26.161 到 162.114。数值与任务中的毫米测量基本一致，但原场景单位元数据是 **METRIC / METERS / scale_length=1**，不能直接依此将尺寸解释成米。M0.5 将独立读取 STL、对照参考物后确定并记录毫米约定。

模板低模对象记录了绕 X 轴约 +90° 的旋转，应单独核对原 OBJ 坐标。其世界包围盒与高模并非完全一致；尚未做点到面的对齐误差测量。

初步取舍：复用高模鞋楦、低模代理与厚度／踝高／鞋口等参考对象，另建参数化鞋底、鞋面及晶格。现有 `.blend` 是参考网格合集，没有可直接复用的生成式鞋底、晶格或节点流程。片状参考只用于读取分区和高度，不参与布尔。

以上尚不构成鞋楦健康报告：源 STL 的封闭性、法线、重复顶点、自交、左右脚和尺码仍待 M0.5 实测。

## M0.5 具体计划（待确认后执行）

1. 独立导入 highpoly STL 与 lowpoly OBJ，在副本上体检。输出尺寸／坐标／单位依据、面数、连通分量、边界与非流形、法线方向、重复顶点和自交检查结果；左右脚、EU 尺码附判断依据与不确定性。
2. 分别处理 STL 与 OBJ 的轴向，统一为毫米、鞋头 +Y、鞋底 −Z。依脚跟后端中心及鞋底基准定位原点，保存变换矩阵，避免把全部参考物的最小包围盒误当鞋楦基准。
3. 仅修复副本的小洞、重复点及法线问题，并记录修复前后差异。应用相同物理基准后，测量高低模双向点到面的偏差，报告最大值、分位数和测量口径；不靠非等比强行贴合来隐藏误差。
4. 保存本地 `assets/last/last_normalized.blend` 与 `.obj`；提取底轮廓、鞋头／脚跟、最宽位置、足弓与脚背特征、纵向中线及脚跟／足弓／前掌边界至 `last_features.json`。
5. 实现按鞋口高度裁切并封口的参数化操作，对低帮／中帮候选逐一检查封闭性。高度从规范化鞋底基准计量；先按楦形测得安全区间，再提出候选值供用户确认。
6. 渲染侧、俯、前、45° 四视角，附方向标识和毫米比例；亲自查看后提交健康报告、偏差数据、候选鞋口高度及预览，等待用户确认识别结果，再进入 M1。

60 秒预览、STL/GLB 导出与 1000 组参数测试属于后续验收，当前尚未验证。
