# GitHub 调研：M1 外形、M2 晶格、M3 制造

调研日期：2026-10-08。范围：与本项目相似的鞋设计项目，以及可以补充鞋楦形变、晶格生成、鞋面花纹和制造检查的工具。累计筛看 **33 个仓库：M1 9 个、M2 14 个、M3 10 个**；补充了用户指定的 OpenShoeDesigner，详见 [专项评估](OPENSHOEDESIGNER_REVIEW.md)。

这是静态调研：查看项目说明、目录、相关实现及部分测试文件；没有安装这些项目，没有运行其测试，也没有验证它们在本机 Blender 5.2.2 / Python 3.13 中的兼容性。“源码”表示阅读了相关实现，不表示审计了整个仓库；“文档”表示主要依据说明和目录；“案例”表示主要提供设计文件或成品展示。链接指向调研时的默认分支，后续可能变化。

## 结论与项目现状

本轮没有发现一个能直接覆盖本项目全部流程、按现有接口替换的成熟整鞋项目。以下优先级是结合当前代码作出的判断：

| 阶段 | 优先看的项目 | 对本项目最有价值的补充 |
| --- | --- | --- |
| M1：鞋楦与整鞋外形 | OpenShoeDesigner、3dp-shoes、FreeCAD Shoe Last、SHOECAD、PyGeM | 量脚与设计参数分离、鞋楦截面围度、脚型标志点、局部受控形变、参数化外底花纹 |
| M2：晶格与表面结构 | microgen、ASLI、LisbonTPMS-tool、Tissue | 密度标定、壁厚／孔径与周期的关联、渐变结构、镂空鞋面扩展 |
| M3：制造检查 | trimesh、PoreSpy、Manifold、OrcaSlicer / PrusaSlicer | 最终网格厚度测量、有限尺寸孔道可达性、局部样件切取、设备与切片验证 |

当前工作区的 M1、M2 已有生成及几何检查。`engine/validate.py` 仍明确报告 `not_checked_M3`；`engine/shoe/lattice.py` 的孔隙检查只覆盖体素连通性。下面的制造建议是后续方案，本次调研没有推进 M3，也没有修改设计参数或生成网格。

## M1：鞋楦、外形与参数化造型（9 个）

| 仓库 / 阅读深度 | 已核实的内容与源码入口 | 对本项目的价值、限制 | 许可 / 建议 |
| --- | --- | --- | --- |
| [tobiasfs/openshoedesigner](https://github.com/tobiasfs/openshoedesigner) · 源码 | [FootMeasurements.cpp](https://github.com/tobiasfs/openshoedesigner/blob/111912bd4f498cf00b31a8ada4ee18bc4577b3ee/src/project/configuration/FootMeasurements.cpp) 组织量脚与公式参数；有鞋垫轮廓、鞋楦规范化、ARAP 鞋面展平及 SVG 输出。 | 与参数化鞋设计方向很接近，适合参考测量定义和未来鞋面制版。作者明确尚未达到 MVP；鞋楦适配入口未实现、分析主调用被注释，不能代替晶格或打印验收。详见 [专项评估](OPENSHOEDESIGNER_REVIEW.md)。 | GPL-3.0。**M1 优先参考方法**；未编译运行。 |
| [dnewcome/3dp-shoes](https://github.com/dnewcome/3dp-shoes) · 源码 | [waffle_sole.py](https://github.com/dnewcome/3dp-shoes/blob/main/cad/waffle_sole.py) 用 build123d 生成杯形鞋底、纹路与镜像；另有鞋楦、面片展平和试验性布料流程。 | 最接近“用代码做鞋”的参考之一。适合借鉴外底纹路、左右脚和误差报告；缝合与贴合仍有作者说明的未解决误差，不能据此判断合脚。 | MIT；代码许可与引用鞋楦／设计素材的许可需分别看。**优先看**。 |
| [peterzask/freecad-shoe-last-wb](https://github.com/peterzask/freecad-shoe-last-wb) · 源码 | [foot_meas_data.py](https://github.com/peterzask/freecad-shoe-last-wb/blob/master/foot_meas_data.py)、[girth_checks.py](https://github.com/peterzask/freecad-shoe-last-wb/blob/master/girth_checks.py)：脚部测量驱动截面和 B-spline 鞋楦；用截面曲线长度回查围度。 | 可借鉴“目标围度 → 生成 → 实际围度反馈”。比单独调整脚宽更接近量脚需求；属于 FreeCAD 宏工作流，不能直接接入 Blender。 | 根目录未找到明确项目许可证。**优先参考方法，暂不复制代码**。 |
| [mehmeteray27zx/SHOECAD](https://github.com/mehmeteray27zx/SHOECAD) · 源码 | [MeshSection.cpp](https://github.com/mehmeteray27zx/SHOECAD/blob/main/src/scene/MeshSection.cpp) 做平面截面及曲线连接；[LastAnalysis.cpp](https://github.com/mehmeteray27zx/SHOECAD/blob/main/src/scene/LastAnalysis.cpp)、[LastMesh.cpp](https://github.com/mehmeteray27zx/SHOECAD/blob/main/src/scene/LastMesh.cpp) 提取方向、前掌位置并施加鞋头上翘等形变。 | 可参考鞋楦标志点和围度计算。坐标约定与本项目不同；[ThicknessOp.cpp](https://github.com/mehmeteray27zx/SHOECAD/blob/main/src/geometry/ThicknessOp.cpp) 是沿法线偏移建壳，不能当作实际最小壁厚校验。 | 未找到项目自身明确授权；README 的第三方许可说明不能代替项目代码许可。**优先参考方法**。 |
| [DaiOrrick/SoleShapper-Blender-PlugIn](https://github.com/DaiOrrick/SoleShapper-Blender-PlugIn) · 源码 | [soleshapper.py](https://github.com/DaiOrrick/SoleShapper-Blender-PlugIn/blob/main/soleshapper.py)：区域遮罩、噪声、法线位移、分层混合和预设；仓库有运行冒烟检查脚本。 | 适合借鉴脚跟／足弓／前掌的有界造型控制。默认鞋底由内嵌顶点与面表建立，不能照搬为本项目基模；应继续使用本项目参数化生成的鞋底。 | GPL-3.0。**按造型需求参考**；本轮未运行其检查。 |
| [mathLab/PyGeM](https://github.com/mathLab/PyGeM) · 源码 | [ffd.py](https://github.com/mathLab/PyGeM/blob/master/pygem/ffd.py)、[vffd.py](https://github.com/mathLab/PyGeM/blob/master/pygem/vffd.py)：控制笼自由形变及体积约束；另有 RBF、IDW；存在相关测试。 | 适合增加局部宽度、鞋头、脚背等平滑形变。它是通用形变库，仍需本项目约束足底、鞋口和关键截面，并重新检查自交与围度。 | [LICENSE.rst](https://github.com/mathLab/PyGeM/blob/master/LICENSE.rst) 为 MIT。**优先试验局部形变**。 |
| [fogleman/sdf](https://github.com/fogleman/sdf) · 源码 | [dn.py](https://github.com/fogleman/sdf/blob/main/sdf/dn.py)、[core.py](https://github.com/fogleman/sdf/blob/main/sdf/core.py)：隐式布尔、壳层、重复、分块采样与 Marching Cubes。 | 与现有隐式场组合方法接近，可参考接口和分块生成。稀疏跳过依赖距离界假设，本项目 TPMS 场不能不加验证地套用该优化。 | MIT。**参考架构与采样，暂不整体替换**。 |
| [OTA3D/orthopen](https://github.com/OTA3D/orthopen) · 源码及文档 | [helpers.py](https://github.com/OTA3D/orthopen/blob/master/helpers.py) 及用户说明包含扫描对齐、射线定位、趾部空间和足部支具操作。 | 可参考扫描导入后的交互和空间检查。主要面向矫形支具；旧 Blender 工作流与本机版本兼容性未验证，不能据此推断医疗或合脚效果。 | GPL-3.0。**按扫描需求参考**。 |
| [microelly2/freecad-nurbs](https://github.com/microelly2/freecad-nurbs) · 文档 | README 中包含鞋底草图与动态偏移相关工具，整体是 FreeCAD NURBS 脚本集合。 | 适合了解截面／曲面／鞋底偏移的历史实现；不是完整的当前整鞋引擎，未深入运行或移植。 | 未找到明确根目录许可证。**低优先级背景参考**。 |

### M1 可以落实的借鉴点

1. 在当前 `engine/shoe/features.py` 的轮廓和尺寸之外，增加前掌、脚背等规定位置的截面周长；同时显示目标值、生成后值和误差。截面位置必须有固定定义，不能只取任意一圈。
2. 如需更细的脚型调整，在 `engine/shoe/last.py` 的 `deform_last` 中评估局部控制笼或 RBF；保留毫米单位、脚尖 +Y、Z 向上的约定，以及足底和关键位置的约束。
3. 如需外底纹路，参考 3dp-shoes 的参数化纹路方法，继续由 `engine/shoe/sole.py` 生成几何；纹路深度和残余底厚最终交给 M3 测量。

这些是扩展建议。当前尺寸和形变流程已经可运行，本轮未发现需要立即重写 M1 的理由。

## M2：晶格、中底及镂空鞋面扩展（14 个）

| 仓库 / 阅读深度 | 已核实的内容与源码入口 | 对本项目的价值、限制 | 许可 / 建议 |
| --- | --- | --- | --- |
| [3MAH/microgen](https://github.com/3MAH/microgen) · 源码 | [tpms.py](https://github.com/3MAH/microgen/blob/main/microgen/shape/tpms.py) 包含 TPMS、密度与等值偏移的换算；有[渐变密度示例](https://github.com/3MAH/microgen/blob/main/examples/TPMS/grading/density.py)与相关测试。 | 适合作为当前三种结构之外的公式、标定和独立比较工具。其密度参照体积有明确定义，不能与裁切后整鞋体积分数混用。 | GPL-3.0；有可选 CAD／网格依赖。**优先看密度标定和测试**。 |
| [tpms-lattice/ASLI](https://github.com/tpms-lattice/ASLI) · 源码 | [config.yml](https://github.com/tpms-lattice/ASLI/blob/master/config.yml) 区分体积分数、等值面、壁厚和孔径；[Infill.cpp](https://github.com/tpms-lattice/ASLI/blob/master/src/Infill.cpp) 有相应换算与混合 TPMS。可给任意 STL 内部填充结构。 | 与鞋底裁切和分区结构很相关。适合借鉴“密度、厚度、孔径共同约束”和过渡方式；标定值仍不能代替最终裁切网格实测。 | [LICENSE](https://github.com/tpms-lattice/ASLI/blob/master/LICENSE) 为 AGPL-3.0-or-later；C++ 工具链较重。**优先参考算法**。 |
| [JorgeESantos/LisbonTPMS-tool](https://github.com/JorgeESantos/LisbonTPMS-tool) · 源码 | [TPMS.py](https://github.com/JorgeESantos/LisbonTPMS-tool/blob/main/LisbonTPMStool/TPMS.py) 支持单胞尺寸、等值偏移及渐变；另有表面族与有限元体网格流程。 | Python 原型便于比较新拓扑、渐变周期和密度。等值参数不是毫米壁厚；改变局部周期后的连续性仍需专门检查。 | MIT。**优先作为轻量公式参考**。 |
| [jalovisko/LatticeQuery](https://github.com/jalovisko/LatticeQuery) · 源码 | [gyroid.py](https://github.com/jalovisko/LatticeQuery/blob/master/lq/topologies/gyroid.py) 用 CadQuery 曲面加厚；另有杆系、异质结构、共形及 TPMS 过渡。 | 可比较 CAD 实体路线和曲面贴合。其 BREP／曲面加厚与现有体素隐式场路线差异较大，移植成本高。 | Apache-2.0；文档环境与本机 Python 不同。**按共形结构需求参考**。 |
| [unitcellhub/unitcellengine](https://github.com/unitcellhub/unitcellengine) · 源码 | [geometry/sdf.py](https://github.com/unitcellhub/unitcellengine/blob/main/src/unitcellengine/geometry/sdf.py) 生成杆系／TPMS；包含线性均匀化分析和相关测试。 | 可用于单胞几何与等效性能研究。TPMS 转距离是近似；线性、小变形均匀化不能直接预测 TPU 整鞋的大变形、疲劳或舒适度。 | Apache-2.0；README 标为 alpha。**研究备选**。 |
| [albertforesg/TPMSgen](https://github.com/albertforesg/TPMSgen) · CLI 源码及文档 | [TPMSgen_CLI.py](https://github.com/albertforesg/TPMSgen/blob/main/TPMSgen_CLI.py) 提供 TPMS 参数输入与网格生成入口；README 展示多种片层／骨架结构、GUI 和 STL 输出。 | 可用于拓扑外观和公式交叉参考。文档采用较旧 Python／Blender 环境，不能默认可在当前环境直接使用；本轮未完整追踪全部生成内核。 | MIT。**拓扑备选**。 |
| [alessandro-zomparelli/tissue](https://github.com/alessandro-zomparelli/tissue) · 源码 | [tessellate_numpy.py](https://github.com/alessandro-zomparelli/tissue/blob/master/tessellate_numpy.py) 将组件铺到曲面，支持厚度、分布和顶点组控制；入口声明 Blender 5.0。 | 镂空鞋面、网格花纹、局部分区图案的重要参考。映射到曲面后，孔径、最窄连接条和边界连续性必须重新测量；不能只看平面组件尺寸。 | 源码 SPDX 为 GPL-2.0-or-later。**鞋面扩展优先看**。 |
| [kmarchais/blender-tpms](https://github.com/kmarchais/blender-tpms) · 文档 | Blender 内生成 TPMS 的插件；安装涉及 PyVista 等依赖。README 将渐变、后续属性修改等列为待完成。 | 可用于独立观察结构。当前项目已经具备分区密度，不应把该插件的待办项当成现成功能。 | GPL-3.0。**低优先级可视化参考**。 |
| [Alistairj43/TPMS-Designer](https://github.com/Alistairj43/TPMS-Designer) · 源码及文档 | MATLAB GUI；[computeStiffness.m](https://github.com/Alistairj43/TPMS-Designer/blob/main/dependancies/computeStiffness.m) 用周期体素有限元计算等效刚度。 | 可离线比较单胞趋势，不能直接把结果转换为整鞋回弹和穿着体验；MATLAB 路线的接入成本较高。 | MIT。**研究备选**。 |
| [hiilda/TPMS-Lattice-Generator-LattGen-](https://github.com/hiilda/TPMS-Lattice-Generator-LattGen-) · 源码及文档 | MATLAB 应用和 Windows 包；[grading.m](https://github.com/hiilda/TPMS-Lattice-Generator-LattGen-/blob/main/source%20code/grading.m) 包含目标密度到等值／厚度控制以及渐变计算。 | 可比较密度标定逻辑；需要理解其拓扑、采样和标定口径，再考虑是否重写为当前引擎可用的计算。 | MIT。**标定备选**。 |
| [jnorato/TPMS_Stress_Surrogates](https://github.com/jnorato/TPMS_Stress_Surrogates) · 源码 | [Surrogates_Homogenization.py](https://github.com/jnorato/TPMS_Stress_Surrogates/blob/main/Surrogates_Homogenization.py) 使用 TPMS 代理模型；代码限定泊松比 0.2–0.4，并对不同拓扑限定密度范围。 | 可参考模型适用域和结果展示。不能直接用于接近不可压缩、发生大变形的 TPU 鞋底性能承诺。 | CC0-1.0。**研究备选，先核对材料模型**。 |
| [SoftwareImpacts/SIMPAC-2026-71（Top6Meta）](https://github.com/SoftwareImpacts/SIMPAC-2026-71) · 目录、GUI 源码及文档 | README 覆盖杆系、TPMS、随机及混合结构；但 [python 目录](https://github.com/SoftwareImpacts/SIMPAC-2026-71/tree/main/python) 的 `tpms_core`、`strut_core` 等关键内核只有 `.pyc`。 | 说明很接近需求，但目前不能作为可阅读、可修改的核心算法源码。若后续考虑使用，先取得完整源文件和可复现环境。 | 根目录 MIT；公开内核源文件不完整。**暂不采用为依赖**。 |
| [aajfe/tpms-shoe-midsole](https://github.com/aajfe/tpms-shoe-midsole) · 源码 | [mesh_generator.py](https://github.com/aajfe/tpms-shoe-midsole/blob/main/tpms/mesh/mesh_generator.py) 的生成入口将壁厚作为记录信息，实际提取零等值面；仓库没有 README。 | 名称相似，但该入口没有生成可控壁厚的材料实体。目录中未看到完成的训练／逆设计闭环，不能按完整中底系统评价。 | 未找到明确项目许可证。**不作为现成方案采用**。 |
| [kpsarkar/3d-printed-footwear-with-lattice-structures](https://github.com/kpsarkar/3d-printed-footwear-with-lattice-structures) · 案例及许可 | Gyroid 鞋／拖鞋案例，主要提供 SolidWorks 文件包、说明与展示。 | 可看外形和晶格布置；没有可直接接入本项目的 Python 参数引擎。作者的材料体验描述未经本轮验证。 | [非商业许可](https://github.com/kpsarkar/3d-printed-footwear-with-lattice-structures/blob/main/Non-Commercial_LICENSE) 还禁止修改／衍生；另有付费商业许可。**仅作案例参考**。 |

### M2 最需要区分的三个量

- **单胞目标密度**：周期结构中的材料比例；microgen 的实现也明确选择参照包络体积。
- **实际裁切后的材料比例**：鞋底边界、分区过渡、实心皮层和清理都会影响它。本项目已有分别统计，应继续保留。
- **最终最小壁厚／杆径／连接条宽度**：这是实体网格上的几何测量；既不是密度，也不是输入的厚度尺度。ASLI 的参数关联值得借鉴，制造门槛仍由 M3 检验。

对于后续鞋面镂空，Tissue 最有启发性的是曲面组件和区域权重。建议先保留鞋口、后跟及鞋底连接保护区，再比较花纹；最终要检查映射后的窄条和孔洞，避免仅用花纹间距推断材料连接宽度。

## M3：制造检查、修复与切片（10 个）

| 仓库 / 阅读深度 | 已核实的内容与源码入口 | 对本项目的价值、限制 | 许可 / 建议 |
| --- | --- | --- | --- |
| [mikedh/trimesh](https://github.com/mikedh/trimesh) · 源码及测试文件 | [proximity.py](https://github.com/mikedh/trimesh/blob/main/trimesh/proximity.py) 提供射线和最大切球厚度估计；[test_thickness.py](https://github.com/mikedh/trimesh/blob/main/tests/test_thickness.py) 用已知几何检查厚度。另有包围盒、射线和网格分析。 | M3 首选试验工具：在最终整鞋上生成鞋面／晶格／接合处厚度分布，并评估旋转后的尺寸。采样最小值不能证明全局最薄点；失败、缺测和覆盖率需进入报告。 | MIT。**优先试验**。 |
| [PMEAL/porespy](https://github.com/PMEAL/porespy) · 源码及测试文件 | [_lt_methods.py](https://github.com/PMEAL/porespy/blob/dev/src/porespy/filters/_lt_methods.py) 的 `local_thickness`、`porosimetry` 分析孔隙尺寸和由入口可达的有限尺寸空间；存在 2D／3D 测试。 | 在当前六邻接连通检查上补“有尺寸的通路”。入口必须代表真实外界；结果半径按体素尺寸换算。属于几何排粉代理，不能代替真实粉末排出试验。 | MIT；平台和可选依赖要另试。**优先试验孔道检查**。 |
| [elalish/manifold](https://github.com/elalish/manifold) · 文档及 Python 示例 | Python `manifold3d` 提供实体布尔、裁切和等值面；README 说明输入必须满足相应流形要求。 | 可用于切取脚跟样件、连接造型和减孔。成功完成布尔不能证明壁厚、悬垂或排粉合格；不需要因此替换整个生成器。 | Apache-2.0。**局部样件／布尔工具优先**。 |
| [blender/blender-addons](https://github.com/blender/blender-addons) 的 3D Print Toolbox · 源码 | [mesh_helpers.py](https://github.com/blender/blender-addons/blob/main/object_print3d_utils/mesh_helpers.py)、[operators.py](https://github.com/blender/blender-addons/blob/main/object_print3d_utils/operators.py) 包含抽样射线厚度、法线悬垂、自交等 BMesh 检查。 | 可借鉴适配现有 Blender 的交互和检查思路。厚度是抽样方法；此 GitHub 镜像已归档，不能当作当前 Blender 5 插件的维护来源。 | 源码 GPL-2.0-or-later。**历史实现参考**；当前扩展应另核版本。 |
| [libigl/libigl-python-bindings](https://github.com/libigl/libigl-python-bindings) · 绑定源码 | [find_self_intersections.cpp](https://github.com/libigl/libigl-python-bindings/blob/main/src/predicates/find_self_intersections.cpp)、[signed_distance.cpp](https://github.com/libigl/libigl-python-bindings/blob/main/src/signed_distance.cpp) 暴露自交和不同符号距离方法。 | 可作为当前浮点 BVH 的独立交叉检查，特别是难判定的相交案例。绑定接口存在不等于已验证本机安装或算法对所有共面情况的保证。 | 仓库有 MPL-2.0 与 GPL 许可文件，需按选用模块核对。**按检测缺口试验**。 |
| [OrcaSlicer/OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) · 源码及文档 | [Orient.cpp](https://github.com/OrcaSlicer/OrcaSlicer/blob/main/src/libslic3r/Orient.cpp) 的朝向评价考虑悬垂、底部接触、形状等；提供 FDM 设备配置及切片。 | 用真实设备、喷嘴、材料和支撑设置检查层路径与朝向。自动朝向是多目标取舍，仍要确认装箱；FDM 切片不能代替 SLS／MJF 排粉检查。 | AGPL-3.0。**FDM 切片候选**。 |
| [prusa3d/PrusaSlicer](https://github.com/prusa3d/PrusaSlicer) · 文档及 CLI 入口 | README 说明命令行、FDM 和 mSLA 工作流；[PrusaSlicer.cpp](https://github.com/prusa3d/PrusaSlicer/blob/master/src/PrusaSlicer.cpp) 可追踪 CLI 调用入口。 | 是自动切片的另一候选；具体设备配置、返回结果解析和无界面运行要单独验证。当前不必同时引入两个切片器。 | AGPL-3.0。**切片替代候选**。 |
| [mfranzon/print3d](https://github.com/mfranzon/print3d) · 源码 | [dfm.py](https://github.com/mfranzon/print3d/blob/main/src/print3d/dfm.py)、[printability.py](https://github.com/mfranzon/print3d/blob/main/src/print3d/printability.py)、[studio.py](https://github.com/mfranzon/print3d/blob/main/src/print3d/studio.py) 串起 Blender、检查、Bambu Studio CLI 和 JSON 报告。 | 适合参考失败退出、报告和层路径分析。其设备配置固定于特定打印机／喷嘴，自动朝向关闭；已查看的检查没有实现真正的全模型最小壁厚测量。 | MIT。**参考编排和失败门槛**，不能直接套用设备配置。 |
| [oriolescserr/3d-print-validator](https://github.com/oriolescserr/3d-print-validator) · 源码 | [print3d_check.py](https://github.com/oriolescserr/3d-print-validator/blob/main/print3d_check.py) 抽样向内射线估计厚度并打 0–100 分；主体检查选取最大连接体。 | 可参考报告字段，但厚度测量失败返回空结果，评分因此可能没有厚度扣分；评分主要看厚度中位数，不能作为最薄结构的导出门槛。 | MIT。**不直接采用其评分判定**。 |
| [pyvista/pymeshfix](https://github.com/pyvista/pymeshfix) · 文档 | [README.rst](https://github.com/pyvista/pymeshfix/blob/main/README.rst) 描述基于 MeshFix 的网格修补及许可要求。 | 可了解自动修补；晶格和镂空鞋面中的孔洞可能是设计本身，自动修复必须核对拓扑与体积变化。现有参数化网格失败应优先回到生成参数／算法排查。 | GPL-3.0；README 另列 MeshFix 商业许可要求，需先核对。**暂不作为默认修复步骤**。 |

### M3 的可执行研究顺序

1. **测量最终网格的实际厚度。** 先用已知厚度的板、圆杆、曲面壳和交叉连接检验候选方法，再用当前整鞋比较射线与最大切球结果。报告测量覆盖率、最薄区域位置、采样误差以及失败情况；不能只给一个分数或中位数。
2. **将孔隙连通升级为有限尺寸通路。** 用 PoreSpy 比较当前六邻接结果，明确外部入口和体素间距，并评估孔道瓶颈。`porosimetry` 返回以体素为单位的半径，直径毫米值应按 `2 × 半径 × 体素间距毫米值` 换算；局部厚度图单独存在不能证明整条路线可达。排粉还需结合工艺、方向和试样。
3. **按真实设备查构建空间和朝向。** 当前整鞋约 286 mm 长，超过默认 250 mm 的一个轴；应对候选旋转后的完整网格重新计算边界，再检查打印机有效空间、支撑和工艺余量。不能为了装入构建箱自动缩小鞋码。若已确认设备可用体积不足，再评估分件方案。
4. **选择一个匹配设备的切片入口。** FDM 看实际层路径、细条是否被切出、悬垂和支撑；SLS／MJF 另做相应工艺验证。最后切取脚跟局部样件，在通过其制造检查后打印比较。

## 源码里发现的采用风险

这些结论来自实现或目录，并影响推荐次序：

- **名称相似不等于功能完成。** aajfe 的中底生成入口明确不让壁厚参数改变几何；Top6Meta 的公开目录缺少关键 `.py` 内核。它们适合进一步了解，但当前不能直接担当本项目材料实体生成器。
- **输入厚度不等于实际最小厚度。** SHOECAD 的法线偏移、近似 SDF、TPMS 的等值标定，都需要最终网格验证。裁切边缘、曲面映射和连接处尤其容易偏离名义参数。
- **检查缺测必须保持未知。** 3d-print-validator 在射线失败时可能仍给高分。后续本项目制造检查应明确区分通过、失败和未能测量，必要检查缺测时不放行制造导出。
- **距离场优化有前提。** fogleman/sdf 的分块跳过利用距离界；unitcellengine 也将 TPMS 距离视为近似。对本项目周期场采用类似加速前，应有不会漏面的独立对照。
- **性能模型有适用域。** MATLAB 均匀化和 TPMS 应力代理可用于限定条件下的比较；不能由此直接承诺 TPU 整鞋的重量、回弹、稳定性或寿命。

## 与本项目的接口对应

| 本项目位置 | 参考项目 | 后续可以评估的工作 |
| --- | --- | --- |
| `engine/shoe/last.py`、`features.py` | FreeCAD Shoe Last、SHOECAD、PyGeM | 截面围度、脚型标志点、受约束的局部形变；几何读取仍集中在 `last.py` |
| `engine/shoe/sole.py` | 3dp-shoes、SoleShapper | 参数化外底纹路和区域遮罩，保持由参数生成 |
| `engine/shoe/lattice.py` | microgen、ASLI、LisbonTPMS-tool | 独立密度标定、厚度／孔径关联和新拓扑对照；保留共享相位和现有回归 |
| `engine/shoe/upper.py` | Tissue | 如增加鞋面花纹，评估曲面分布、边界保护和最终连接条检查 |
| `engine/validate.py` | trimesh、PoreSpy、libigl、Blender Toolbox | 厚度、有限尺寸孔道、自交交叉检查和失败报告 |
| `tools/`、构建报告 | Manifold、print3d、一个选定切片器 | 局部样件、设备配置、切片结果和制造检查报告 |

建议先以独立对照脚本评估少量候选，不要一次引入所有库。保持 JSON 参数与 Blender 解耦，新增参数仍需默认值、范围和关联约束。本报告没有调整参数、引入依赖或改变现有里程碑验收。

许可记录用于筛选代码和素材能否复用；标注基于调研时仓库元数据与已查看的许可文件，不代表完成全部依赖许可核查。开源代码许可不会改变当前 Zellerfeld 鞋楦的个人／非商业使用及禁止分发原始与衍生网格的约定。
