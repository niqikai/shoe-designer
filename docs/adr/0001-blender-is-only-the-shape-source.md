# Blender 只充当 Shape Source

Blender 以无界面子进程（`blender -b`）运行，只充当 Shape Source：从参数生成 Shape Bundle（Last、Sole Envelope、Landmarks、可选的 Surface Parameterization 及来源信息）并写出到磁盘；晶格生成、融合、校验与导出全部放在 Blender 之外的独立 Python 3.13 包中。这样晶格引擎不依赖 Blender 即可测试，轻量的形状阶段与计算密集的晶格阶段在 SaaS 中可以独立扩缩，不污染 Blender 自带的 Python，Blender 也能被其他 Shape Source（鞋楦模板、脚型扫描）替换。

## Considered Options

- 全流程在 Blender 自带 Python 中运行：要往 Blender 的 Python 里装科学计算依赖，晶格引擎与 Blender 版本绑死。
- 在同一进程中调用 PyPI 的 `bpy` 模块：`bpy` 与 Blender 版本一一绑定（5.2 要求 Python ==3.13.*），引擎升级 Python 或 Blender 时必须同步，部署镜像变重，耦合依旧。

## Consequences

- Blender 脚本抛出异常时默认仍以 0 退出，子进程必须带 `--python-exit-code` 调用，并通过约定的错误文件回传原因。
