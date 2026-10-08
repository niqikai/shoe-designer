# OpenShoeDesigner 专项评估

日期：2026-10-08。仓库：[tobiasfs/openshoedesigner](https://github.com/tobiasfs/openshoedesigner)。本次读取默认分支 `master`，快照为 [`111912bd4f498cf00b31a8ada4ee18bc4577b3ee`](https://github.com/tobiasfs/openshoedesigner/commit/111912bd4f498cf00b31a8ada4ee18bc4577b3ee)，提交日期 2026-10-05；提交内容涉及 ARAP 与展平 SVG 导出。

## 结论

**值得加入 M1 的优先研究名单。** 它将脚部测量、鞋的姿态和鞋面设计分开组织，再通过构建依赖生成各部件；这与本项目的参数化方向相关。已有可阅读的量脚参数、鞋垫轮廓、鞋楦规范化和鞋面展平代码。但当前整体流程尚不完整，不能直接当作自动量脚适配、晶格中底或打印验收引擎。

作者在 [README](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/README.md) 中明确说明尚未达到 MVP。其主要制造路线是鞋楦、鞋垫、鞋跟和鞋面裁片，尤其面向手工制鞋。这个“制造”范围与本项目 M3 的壁厚、排粉、构建空间和切片验收有区别。

本次为静态源码阅读，没有安装、编译或运行第三方程序，也未下载附带的 STL／Blender 素材。以下“已实现”仅表示存在具体实现，运行效果仍待验证。

## 与 M1／M2／M3 的对应

| 阶段 | 相关程度 | 可借鉴的部分 | 当前边界 |
| --- | --- | --- | --- |
| M1：鞋楦、尺寸和外形 | 高 | 量脚数据与鞋型分开；公式默认值被实测替换；参数化鞋垫轮廓；导入鞋楦的方向识别 | 鞋楦适配入口为占位；详细分析未从当前入口启用；从鞋垫生成完整封闭鞋楦尚缺拼接 |
| M2：晶格与鞋面结构 | 鞋面方向相关 | 鞋面设计在曲面上构造，再将面片展平为 2D；可为未来裁片制版、纹样定位提供思路 | 本次查看的目录和核心构建链没有看到 Gyroid／Diamond／Octet、分区密度或晶格中底生成实现 |
| M3：制造与导出 | 传统鞋面制版相关 | SVG 裁片输出、展平后的边长误差显示 | 没有看到本项目所需的实际最小壁厚、有限尺寸排粉通路、FDM 悬垂及设备切片验收流程；鞋楦导出入口也未接完 |

## 关键实现与完成程度

### 1. 量脚数据和参数公式：最值得先借鉴

[FootMeasurements.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/configuration/FootMeasurements.cpp#L36) 将脚长、前掌宽度、前掌围度、腰围、跟围和跟宽分别建模。默认值允许引用其他参数；例如前掌宽度从脚长推算，围度再从宽度推算。这些是程序初始估计，不能当作经过验证的个人脚型。

[ParameterEvaluator.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/configuration/ParameterEvaluator.cpp#L186) 解析参数之间的引用，确定求值顺序，并报告缺失引用和循环依赖。对本项目最有价值的是这一数据关系，而非照搬其中的经验比例。

建议：将来加入量脚功能时，在 `designs/current.json` 内区分实测值、公式估计值和鞋型设计值，并显示生成后对应尺寸的误差；继续保持该 JSON 为唯一真相。不能把一个名义 EU 尺码推算出的全部值当成实测数据。

### 2. 鞋垫轮廓：有具体的参数化构造

[InsoleConstruct.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/InsoleConstruct.cpp#L186) 从脚长、前掌宽、跟宽、前掌测量角、趾部角度、鞋头尖度和前端余量建立标志点及轮廓曲线。它明确区分脚的长度与鞋楦前端预留量。

可借鉴到 M1 的点是：用有定义的标志点和尺寸控制外形，避免只有整体缩放。当前项目由现有鞋楦提取足底轮廓，后续若补充这套方法，应先作独立对照，不必替换现有足弓贴合流程。

其 [Configuration.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/configuration/Configuration.cpp#L34) 同时配置脚跟／前掌高度、鞋头上翘与鞋跟角度。参数的含义和基准必须重新核对，不能仅因名称相似就映射成本项目的脚跟／前掌底厚。

### 3. 鞋楦规范化可参考，量脚适配尚未完成

[LastNormalize::Run](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/LastNormalize.cpp#L97) 在启用重定向时依次执行 PCA、对称性、足底、前后和左右识别。适合参考导入不同来源鞋楦时的方向处理；算法正确率没有经过本轮实测。

需要注意以下入口状态：

- [LastAnalyse::Run](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/LastAnalyse.cpp#L93) 中，主要分析调用 `AnalyseForm()` 被注释。虽然同文件有围度截面、标志点和轮廓相关函数，不能因此把当前入口描述成已经完成的自动分析流程。
- [LastUpdate::Run](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/LastUpdate.cpp#L124) 只复制输入，输出未实现提示，然后更新状态标记；没有按量脚参数进行形变。
- [LastConstruct::Run](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/LastConstruct.cpp#L99) 已有曲面提取操作，但代码明确保留了将鞋垫与鞋楦底部拼接的待办项。不能把它视为已生成完整封闭鞋楦。

因此，本项目可研究它的测量定义和规范化方法；现阶段继续保留 `engine/shoe/last.py` 中已有受控形变与检查。

### 4. 鞋面展平：有实际算法，适合未来制版扩展

[DesignSolve.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/DesignSolve.cpp#L95) 更新鞋面设计面片；[UpperConstruct.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/UpperConstruct.cpp#L90) 用曲面坐标系将面片映射到 3D。[UpperFlatten.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/UpperFlatten.cpp#L84) 则记录原始边长，从 UV 初始化，经初步缩放和 ARAP 求解得到平面面片。

[ARAP.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/math/ARAP.cpp#L109) 有 Eigen 稀疏线性求解与局部旋转计算；其误差显示比较展开前后的边长。这是几何畸变提示，不能直接推导皮革／织物的真实应力、舒适度或可缝制性。

[Upper::SaveSVG](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/object/Upper.cpp#L34) 提取面片轮廓并写出带毫米尺寸的 SVG，内部坐标在输出时乘以 1000。若将来研究移植，应明确米／毫米转换；本项目几何约定仍为毫米。该函数的排版留白不能当作已经加入缝份或刀具补偿。

若未来扩展“打印鞋底＋布／皮鞋面”，这部分价值较高。对当前一体打印鞋，展平不构成晶格、鞋面厚度或打印质量检查。

### 5. 构建依赖：可供后续自动修改流程参考

[Builder.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/Builder.cpp#L748) 传播参数变动与所需输出，再执行满足条件的操作。可以借鉴“修改影响哪些部件、哪些结果需要重算”的组织方式；对后续 M4 的参数修改闭环有启发。

但 `MarkValid` 表示该构建系统内部的计算状态，并非本项目的网格健康或制造通过。前面的适配占位入口也会将输出标记为有效，后续借鉴时必须保留独立几何／制造验证。

### 6. 保存和导出：不能只根据 README 状态表下结论

[Project.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/Project.cpp#L184) 已有配置与左右脚测量的 JSON 读写，以及调用鞋面 SVG 导出的入口。因此 README 将读写列为待完成，不等于完全没有相关代码。

同文件的 `SaveLast` 中实际写出鞋楦的调用仍被注释，`SaveSkin` 也有类似情况；[HeelConstruct.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/operation/HeelConstruct.cpp) 的生成入口目前只清空输出并更新标记。这些限制说明已有模块仍未构成完整制造输出闭环。

## 技术与采用建议

- 技术栈是 C++17、CMake、wxWidgets、Eigen、OpenGL／GLEW，见[顶层构建配置](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/CMakeLists.txt)、[应用配置](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/CMakeLists.txt)与 [3D 库配置](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/3D/CMakeLists.txt)。它是桌面应用，不是可直接装入 Blender 的 Python 库；本轮未验证 macOS 构建。
- 仓库 [LICENSE](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/LICENSE) 为 GPLv3，所阅源文件头也有 GPL 授权说明。若复制实现，应核对相应授权要求；附带模型的来源和授权另查。本项目当前鞋楦的使用限制继续适用。
- **近期优先参考**：量脚与设计参数的区分、明确的测量定义、鞋楦方向处理，以及“估计值与实测值”标注。新增能力仍需默认值、范围、关联约束和回归对照。
- **按后续方向研究**：鞋面 ARAP 展平和 SVG 制版、按依赖重算部件。
- **目前不作为现成能力采用**：自动量脚形变、完整鞋楦构造与导出、晶格中底、打印制造验收。

本次只新增／更新调研文档，没有修改设计参数、引入依赖或推进 M3。
