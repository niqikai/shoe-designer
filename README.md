# 对话式 3D 打印鞋设计

**本项目当前仅限个人／非商业使用。** 使用的 Zellerfeld Last Family 鞋楦受 [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/) 约束；按项目要求，不分发该鞋楦及其衍生网格，不将素材或生成模型提交到仓库。商用须获得 Zellerfeld 授权或替换为自有且允许商用的鞋楦。详见 [许可证说明](docs/LICENSE_NOTICE.md)。

目标是让没有建模经验的用户通过自然语言调整鞋款，由 JSON 参数驱动 Blender 无头生成、校验、导出和渲染。

当前阶段：**M0.5 鞋楦体检、规范化、特征提取与封闭裁切已实现，等待用户看图确认后进入 M1。** 当前输出是鞋楦研究副本；整鞋的鞋底、鞋面、晶格及打印校验将在后续里程碑实现。

环境与初查见 [M0 开工检查](docs/M0_CHECK.md)，本阶段实测见 [M0.5 鞋楦报告](docs/M0_5_REPORT.md)。

素材保存在本机 `assets/last/`；原 ZIP 及 `raw/` 解压文件已设为只读。`assets/`、`out/` 均被 Git 忽略。

## 运行 M0.5

需要 Blender 5.2 LTS，使用其自带 Python 与 NumPy，无额外包。启动器先读取 `BLENDER_PATH`，再查 PATH 和常见安装路径。

```sh
tools/inspect_last.sh
```

默认读取 `designs/current.json`，在副本上体检、规范化、按高度裁切并封口，完成几何检查后保存：

- `assets/last/last_normalized.blend`：毫米单位、鞋头 +Y、底面朝 −Z，高模可见、原始低模代理隐藏。
- `assets/last/last_normalized.obj`：仅规范化高模，坐标按毫米解释，OBJ 本身无单位元数据。
- `assets/last/last_features.json`：底部轮廓、中线、特征、分区、变换及源文件哈希。
- `out/m0_5/health_report.json`：网格、法线、对齐偏差、封口、原始素材完整性报告。
- `out/m0_5/original_four_views.png`、`current_four_views.png`、`collar_comparison.png`、`proxy_alignment.png`：真实 Blender 预览，附 50 mm 比例尺。

当前低帮候选为 75 mm，中帮为 100 mm，均从规范化最低底面基准计量。它们等待用户确认，详细定义见 [设计说明](docs/design_notes.md)。`current_four_views.png` 始终对应当前设计的有效参数；低帮／中帮对比图用于评审候选。

原始低模存在最大约 6.725 mm 双向采样偏差，后续正式造型使用高模；细节见报告。自交检测使用浮点 BVH，制造壁厚、排粉孔、悬垂、构建尺寸等检查留待 M3。

在本机 Codex 沙箱内，Blender 会于 Metal 初始化时崩溃；已验证获准的沙箱外无头执行正常。直接在本机终端运行不受该 Codex 沙箱限制。

## 测试

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
blender -b --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_last_tests.py
```

几何测试需先完成 M0.5 生成规范化文件。覆盖单位／轴向、OBJ 往返、裁切边界与随机高度，以及开口、相交、翻转法线和微小面精度。1000 组整鞋参数测试留待 M5。

设计版本从 v000 开始，`current.json` 为唯一真相；新建初版无需覆盖旧文件。后续修改先备份历史并记入 `designs/CHANGELOG.md`。一条命令的设计修改、撤销和版本比较留待 M4。
