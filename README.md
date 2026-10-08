# 对话式 3D 打印鞋设计

**本项目当前仅限个人／非商业使用。** 使用的 Zellerfeld Last Family 鞋楦受 [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/) 约束；按项目要求，不分发该鞋楦及其衍生网格，不将素材或生成模型提交到仓库。商用须获得 Zellerfeld 授权或替换为自有且允许商用的鞋楦。详见 [许可证说明](docs/LICENSE_NOTICE.md)。

目标是让没有建模经验的用户通过自然语言调整鞋款，由 JSON 参数驱动 Blender 无头生成、校验、导出和渲染。

当前阶段：**M2 晶格中底已实现，等待用户看图确认后进入 M3。** 当前设计为右脚、低帮 75 mm、Gyroid 分区晶格；保留贴合足弓的实心顶层、外底和实际开放的鞋口。支持 Gyroid、Diamond、Octet 三种结构与预览／导出两档精度。导出通过当前网格检查，制造壁厚、工艺与设备适配尚待 M3，不代表已完成可打印性验收。

本阶段实测见 [M2 晶格报告](docs/M2_REPORT.md)。实心基准见 [M1 报告](docs/M1_REPORT.md)，历史依据保存在 [M0 开工检查](docs/M0_CHECK.md) 与 [M0.5 鞋楦报告](docs/M0_5_REPORT.md)。

素材保存在本机 `assets/last/`；原 ZIP 及 `raw/` 解压文件已设为只读。`assets/`、`out/` 均被 Git 忽略。

## 运行当前设计

已验证环境为 Blender 5.2.2 LTS、其内置 Python 3.13。启动器先读取 `BLENDER_PATH`，再查 PATH 和常见安装路径。原始素材仅保存在本机；首次使用先运行 M0.5，得到规范化鞋楦。

M1／M2 使用隐函数场与 Marching Cubes 合并鞋底、晶格和鞋面，避免对高面数曲面反复布尔。额外依赖安装在项目忽略的 `.venv` 中，Python 版本与 Blender 一致；版本记录在 `requirements-geometry.txt`。

```sh
python3 tools/setup_geometry.py
tools/run.sh designs/current.json
```

默认在 `out/m2/` 生成：

- `shoe_right.stl`、`shoe_right.glb`：同一只整鞋；STL 坐标为毫米，GLB 按标准转换为米。左脚参数会生成 `shoe_left.*`。
- `previews/shoe_side.png`、`shoe_top.png`、`shoe_front.png`、`shoe_iso.png` 与 `four_views.png`：四视角和拼图，附 50 mm 比例尺。
- `cutaway.png`：侧面与斜视剖切拼图，用于观察晶格、实心顶层和足弓；剖切副本不导出。
- `report.json`：有效参数、形变、网格检查、分区实际密度、材料体积、孔隙采样连通性、尺寸、耗时与素材完整性结果。
- `features.json`、`effective_params.json`：形变后的特征与本次实际使用的参数。

每次运行的 Blender 日志保存在 `out/logs/build-*.log`。生成失败时 `report.json` 标记失败；已有旧导出不会变成本次通过的结果，应以报告状态为准。

输出目录和采样精度可显式指定：

```sh
tools/run.sh designs/current.json --out out/m2-export --voxel-mm 0.5
```

晶格 `resolution: preview` 默认间距 0.8 mm，`export` 为 0.5 mm。预览会随较小的杆径／片厚进一步细化，间距不超过该值的 1/2.5。命令行 `--voxel-mm` 支持 0.5–1 mm，同样受细节尺寸限制；实际间距以报告的 `voxel_mm` 为准。上例保持设计 JSON 不变，仅覆盖输出采样精度。更细采样会增加运行时间与内存；本机实测见 M2 报告。

预览色区区分外底、晶格中底与鞋面；三者合并为单个封闭网格，颜色不代表多材料打印。STL 为研究导出，后续需进行制造验证。**本鞋楦仅限非商业使用，不得分发。**


## M2 参数与版本

| 参数 | 范围／选项 | 当前值与含义 |
| --- | --- | --- |
| `midsole_structure` | `solid` / `lattice` | 当前 `lattice`；旧 JSON 缺省为 `solid`，可重现 M1 |
| `lattice_type` | `gyroid` / `diamond` / `octet` | 当前 `gyroid`；前两者为曲面片层，后者为杆网 |
| `lattice_density_heel/arch/forefoot` | 0.20–0.60 | 0.32／0.45／0.35，表示周期单胞目标材料占比 |
| `lattice_rod_mm` | 1.5–3.0 mm | 2 mm；Octet 为杆径，曲面晶格为未裁切解析片层的保守厚度尺度 |
| `resolution` | `preview` / `export` | 当前 `preview`；控制输出采样间距 |

三个分区共享周期与相位，在分区边界附近平滑过渡。单胞目标密度不等于实际裁切后的密度；整鞋体积还包含固定实心顶层、外底和鞋面。默认顶层垂直厚度 2.4 mm，外底 3 mm。密度与厚度共同决定单胞大小，不能当作独立的软硬开关；解析厚度也不等于最终网格的实测最小壁厚，裁切端部和制造误差需在 M3 检查。

当前为 v002。M1 的 v001 已逐字节备份到 `designs/history/v001.json`，此前 v000 也保留。重现 M1：

```sh
tools/run.sh designs/history/v001.json --out out/m1
```

`designs/current.json` 为唯一真相；修改先备份历史并记入 `designs/CHANGELOG.md`。语义规则见 [自然语言映射](docs/semantic_map.md)。一条命令的设计修改、撤销和版本比较留待 M4。

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

原始低模存在最大约 6.725 mm 双向采样偏差，后续正式造型使用高模；细节见报告。自交检测使用浮点 BVH，制造壁厚、排粉孔、悬垂、构建尺寸等检查留待 M3。

早期受限 Codex 沙箱曾在 Metal 初始化时崩溃；本机终端与当前可访问图形驱动的无头环境已验证可运行。该环境记录保留在 M0 报告中。

## 测试

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_last_tests.py
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_deform_tests.py
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_shoe_tests.py -- --stress
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_lattice_tests.py -- --integration --stress --fine-export
```

几何测试需先完成 M0.5 生成规范化文件；M1 导出往返测试需 `out/m1/`，M2 的 `--integration` 需默认 `out/m2/` 导出，`--fine-export` 需按上面的命令生成 `out/m2-export/`。如果 PATH 中没有 `blender`，使用 `python3 tools/find_blender.py` 查到的路径。

测试覆盖单位／轴向、OBJ/STL/GLB 往返、鞋口、足弓贴合、底厚、左右镜像、受控形变，以及晶格密度标定、连续相位、分区过渡、厚度与单胞关系、三种整鞋拓扑和精度对比。M1 `--stress` 增加四个边界组合，M2 `--stress` 增加两个组合；这些不代替 M5 的 1000 组随机测试。测试结果及当前限制见 M2 报告。
