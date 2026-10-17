# M3 核心壳体原型报告

生成日期：2026-10-17。使用 M2 gate `READY` 及用户认可的冻结输入 `output/m2_toe_complete/lattice.stl`（SHA-256 `b20f1037c9978afcaae316785d028fa49c282b50727d0149c6be35999ce5157c`）。原型输出：`output/m3_core_shell_prototype/`。

本轮将冻结 M1 鞋壳与 M2 晶格、保存的保守内域组合成核心壳体原型，操作为 `A = (B - C) union (L intersect B)`，与目标集合 `((B - C) union L) intersect B` 等价。先裁入鞋体再并壳，避免鞋外晶格碎片进入最终并集。所有 804 个输入正向壳体都保留，没有删除几何组件。

输出原型有 2,450,682 个三角面。拓扑边配对、方向一致和顶点面环检查通过；输出正向壳体计数为 57。由于大型壳体嵌套和全局自交筛查未完成，**实际物理实体数未能证明为单一实体**，全局自交状态为 `NOT_VERIFIED`。集合残差体积均在配置的 `1e-5 mm³` 容差内；该体积比较不是表面距离或最小壁厚证明。原型整体保持 `PARTIAL`。

已从输出网格重导入并比较 ASCII STL 和 3MF 的三角坐标；两者状态均为 `PARTIAL`，坐标重导入一致。`preview.glb` 因 float32 位置量化产生坐标碰撞、零面积面和方向塌陷，标记 `FAIL`，不可用来判断制造 STL。两张 PNG 则从与 STL 完全相同的 float64 三角坐标直接进行平面着色渲染，不做平滑或几何修改；剖切图只用于观察内部结构，不是封闭模型。

外侧透视图显示鞋壳遮住大部分晶格，这是预期的壳体覆盖；剖切图显示若干晶格条带及锯齿状/尖刺状小片，不能作为完整连续晶格或可打印性的证据。鞋面六角减重孔和脚床粉末清理通道仍为 `BLOCKED_NOT_IMPLEMENTED`，粉末清理和制造资格为 `NOT_VERIFIED`，实体打印与穿着测试为 `NOT_TESTED`。所以本产物只适合继续审查 M3 几何连接与组件连通性，不是最终鞋件。

验证：M3 几何、门槛与导出测试通过。M2 与 M3 相关的完整 unit/integration 回归为 638 passed、0 failed，exit 0，4 项上游 `build123d` 弃用警告。实际 M3 装配及 STL/3MF 重导入记录保存在 `output/m3_core_shell_prototype/assembly_report.json`、`validation_report.json` 和 `artifact_manifest.json`。
