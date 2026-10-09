# 对话式 3D 打印鞋设计

**本项目当前仅限个人／非商业使用。** 使用的 Zellerfeld Last Family 鞋楦受 [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/) 约束；按项目要求，不分发该鞋楦及其衍生网格，不将素材或生成模型提交到仓库。商用须获得 Zellerfeld 授权或替换为自有且允许商用的鞋楦。详见 [许可证说明](docs/LICENSE_NOTICE.md)。

目标是让没有建模经验的用户通过自然语言调整鞋款，由 JSON 参数驱动 Blender 无头生成、校验、导出和渲染。

当前阶段：**v004 Gyroid 已通过 M3 项目必要几何门槛，保留排粉通路与材料设备警告，等待看图验收；M4 尚未进入。** 当前为右脚、低帮 75 mm、SLS。连续边缘收口替代按薄点补球，保留足底接触面；高精度候选已生成。

本轮实测见 [M3 收口修复报告](docs/M3_REPAIR_REPORT.md)，此前失败证据保存在 [v003 制造筛查报告](docs/M3_REPORT.md)。规则及厂商差异见 [材料与制造依据](docs/materials.md)；此前成果见 [M2](docs/M2_REPORT.md)、[M1](docs/M1_REPORT.md)、[M0](docs/M0_CHECK.md) 和 [M0.5](docs/M0_5_REPORT.md)。

素材保存在本机 `assets/last/`；原 ZIP 及 `raw/` 解压文件已设为只读。`assets/`、`out/` 均被 Git 忽略。

## 运行当前设计

已验证环境为 Blender 5.2.2 LTS、其内置 Python 3.13。启动器先读取 `BLENDER_PATH`，再查 PATH 和常见安装路径。原始素材仅保存在本机；首次使用先运行 M0.5，得到规范化鞋楦。

M1／M2 使用隐函数场与 Marching Cubes 合并鞋底、晶格和鞋面，避免对高面数曲面反复布尔。额外依赖安装在项目忽略的 `.venv` 中，Python 版本与 Blender 一致；版本记录在 `requirements-geometry.txt`。

```sh
python3 tools/setup_geometry.py
tools/run.sh designs/current.json
```

默认在 `out/m3/` 生成：

- `shoe_right.stl`、`shoe_right.glb`：只有必要检查通过时生成的设计姿态候选；`shoe_right_print.stl/.glb` 使用已核验的设备摆放姿态。STL 坐标为毫米，GLB 按标准转换为米。左脚对应 `shoe_left*`。当前候选通过项目门槛；实际打印建议使用高精度目录 `out/m3-export/` 中的 `_print.stl`，并由打印方复核。
- `previews/shoe_side.png`、`shoe_top.png`、`shoe_front.png`、`shoe_iso.png` 与 `four_views.png`：四视角和拼图，附 50 mm 比例尺。
- `cutaway.png`：侧面与斜视剖切拼图，用于观察晶格、实心顶层和足弓；剖切副本不导出。
- `thin_locations.png`（检出薄边时）、`previews/build_iso.png`：红点标记最薄采样位置，以及设备空间内的推荐摆放。红点只供观察。
- `report.json`、`manufacturing_report.md`：结构化与中文结果，包含壁厚、排粉净空、悬垂、设备空间、连续收口、导出门槛和限制。
- `features.json`、`effective_params.json`：形变后的特征与本次实际使用的参数。

每次运行的 Blender 日志保存在 `out/logs/build-*.log`。制造检查未通过时保留诊断与预览，退出码为 2；运行错误退出码为 1。每次运行清除该输出目录下本工具命名的旧鞋 STL／GLB 和薄点诊断图，防止误用旧文件。旧 M1／M2 目录中的历史导出不等于通过了 M3。

输出目录和采样精度可显式指定：

```sh
tools/run.sh designs/current.json --out out/m3-export --voxel-mm 0.5
```

晶格 `resolution: preview` 默认间距 0.8 mm，`export` 为 0.5 mm。预览会随较小的杆径／片厚进一步细化，间距不超过该值的 1/2.5。命令行 `--voxel-mm` 支持 0.5–1 mm，同样受细节尺寸限制；实际间距以报告的 `voxel_mm` 为准。上例保持设计 JSON 不变，仅覆盖输出采样精度。更细采样会增加运行时间与内存；本机实测见 M3 收口修复报告；失败同样生成诊断预览。

预览色区区分外底、晶格中底与鞋面；三者合并为单个封闭网格，颜色不代表多材料打印。M3 放行的 STL 仍是按项目规则筛查的候选，需按所选材料、设备和切片配置复核。**本鞋楦仅限非商业使用，不得分发。**


## M2 参数与版本

| 参数 | 范围／选项 | 当前值与含义 |
| --- | --- | --- |
| `midsole_structure` | `solid` / `lattice` | 当前 `lattice`；旧 JSON 缺省为 `solid`，可重现 M1 |
| `lattice_type` | `gyroid` / `diamond` / `octet` | 当前 `gyroid`；前两者为曲面片层，后者为杆网 |
| `lattice_density_heel/arch/forefoot` | 0.20–0.60 | 0.32／0.45／0.35，表示周期单胞目标材料占比 |
| `lattice_rod_mm` | 1.5–3.0 mm | 2 mm；Octet 为杆径，曲面晶格为未裁切解析片层的保守厚度尺度 |
| `resolution` | `preview` / `export` | 当前 `preview`；控制输出采样间距 |

三个分区共享周期与相位，在分区边界附近平滑过渡。单胞目标密度不等于实际裁切后的密度；整鞋体积还包含固定实心顶层、外底和鞋面。默认顶层垂直厚度 2.4 mm，外底 3 mm。密度与厚度共同决定单胞大小，不能当作独立的软硬开关；解析厚度也不等于最终网格的实测最小壁厚，裁切端部和制造误差需在 M3 检查。

当前为 v004；v003 已逐字节备份到 `designs/history/v003.json`，此前 v000–v002 也保留。生成旧版实心外形并按当前 M3 规则检查：

```sh
tools/run.sh designs/history/v001.json --out out/m1-review
```

`designs/current.json` 为唯一真相；修改先备份历史并记入 `designs/CHANGELOG.md`。语义规则见 [自然语言映射](docs/semantic_map.md)。一条命令的设计修改、撤销和版本比较留待 M4。

## M3 参数、修复与导出门槛

| 参数 | 默认／范围 |
| --- | --- |
| `print_process` | `SLS`；可选 `SLS` / `MJF` / `FDM` |
| `material`、`tpu_shore_a` | `TPU`、90；硬度整数 70–95，仅表达材料意图 |
| `build_size_x_mm/y_mm/z_mm` | 各 250 mm；各轴可配 100–1000 mm |
| `build_margin_mm` | 2 mm；0–10 mm |
| `build_orientation` | `auto` / `as_designed`；默认自动搜索 |
| `boundary_rounding_mm` | 旧设计默认 0；0–1.5 mm，且不超过有效鞋面厚度的一半；当前 1.2 mm |

项目壁／杆厚度下限：SLS／MJF 1.2 mm，FDM 1.5 mm；粉末工艺至少两处 4 mm 净空出口；FDM 悬垂经验阈值 45°。这些是筛查规则，不是通用材料认证。

连续收口从整个材料场的内距中心域重建圆滑边缘，并做受保护的表面平滑；足底顶面附近保持原场和原顶点。它会增减材料，实际体积和孔道必须重新测量；参数不是保证的最终圆角或制造厚度。半径 0 完全关闭此几何步骤，旧设计保留原始裁切，已移除失败点驱动的补球循环。

启用收口时，最终网格使用 192,000 个面积分层向内法线射线；未启用时保留 24,000 次筛查。报告缺测和覆盖口径，任何检出薄点或缺测仍阻止导出。粉末通道的净空留量包含体素误差和表面平滑的最大位移；鞋口不算中底出口。有限采样不证明全局最薄值，通路狭窄或材料厂商未确定时保留警告。

自动摆放优先水平旋转；SLS／MJF 必要时再搜索倾斜，FDM 保持底面朝下。每个放行姿态都用全部模型顶点重新检查。每轴预留两倍边距，XY 分配到两侧；最低 Z 保持 0，Z 的两份余量留在上方。输出 `_print` 文件才应用推荐旋转和平移，设计姿态文件及看图坐标保留。空间适配不证明支撑、热变形与排粉效果。

任何必要检查失败或缺测均阻止打印导出；具体材料与设备尚未认证时，即使自动规则通过，总结仍保留警告。当前 v004 的必要检查已通过，实际清粉与制造仍需局部试样验证；看图验收后再决定是否进入 M4。

## 保留 M0.5 体检流程

此流程只依赖 Blender 自带 Python 与 NumPy：

```sh
tools/inspect_last.sh
```

默认读取 `designs/current.json`，在副本上体检、规范化、按高度裁切并封口，完成几何检查后保存：

- `assets/last/last_normalized.blend`：毫米单位、鞋头 +Y、底面朝 −Z，高模可见、原始低模代理隐藏。
- `assets/last/last_normalized.obj`：仅规范化高模，坐标按毫米解释，OBJ 本身无单位元数据。
- `assets/last/last_features.json`：底部轮廓、中线、特征、分区、变换及源文件哈希。
- `out/m0_5/health_report.json`：网格、法线、对齐偏差、封口、原始素材完整性报告。
- `out/m0_5/original_four_views.png`、`current_four_views.png`、`collar_comparison.png`、`proxy_alignment.png`：真实 Blender 预览，附 50 mm 比例尺。

低帮 75 mm 已由用户确认，中帮 100 mm 保留为比较候选。高度均从规范化最低楦底基准计量，详细定义见 [设计说明](docs/design_notes.md)。`current_four_views.png` 对应当前参数的鞋楦裁切；M1 的整鞋预览位于 `out/m1/`。

原始低模存在最大约 6.725 mm 双向采样偏差，后续正式造型使用高模；细节见报告。自交检测使用浮点 BVH，制造壁厚、排粉孔、悬垂和构建尺寸已在 M3 增加独立筛查。

早期受限 Codex 沙箱曾在 Metal 初始化时崩溃；本机终端与当前可访问图形驱动的无头环境已验证可运行。该环境记录保留在 M0 报告中。

## 测试

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_last_tests.py
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_deform_tests.py
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_shoe_tests.py -- --stress
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_lattice_tests.py -- --integration --stress --fine-export
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_manufacturing_pose_tests.py
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_manufacturing_tests.py
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_manufacturing_integration_tests.py
```

几何测试需先完成 M0.5 生成规范化文件；M1 导出往返测试需 `out/m1/`，M2 的 `--integration` 需默认 `out/m2/` 导出，`--fine-export` 读取已保留的 M2 `out/m2-export/` 历史证据。M3 集成测试需先生成当前 `out/m3/`、`out/m3-export/`；它会重导入精细 STL／GLB、独立做 384,000 次测厚及 9 点足底检查，另建立实心测试夹具。如果 PATH 中没有 `blender`，使用 `python3 tools/find_blender.py` 查到的路径。

测试覆盖单位／轴向、OBJ/STL/GLB 往返、鞋口、足弓贴合、底厚、左右镜像、受控形变，以及晶格密度标定、连续相位、分区过渡、厚度与单胞关系、三种整鞋拓扑和精度对比。M1 `--stress` 增加四个边界组合，M2 `--stress` 增加两个组合；这些不代替 M5 的 1000 组随机测试。M3 另覆盖已知薄板／圆杆、缺测、单孔／双孔／窄通道、摆放、悬垂、导出阻断及打印姿态往返。测试结果及当前限制见 M3 报告。
