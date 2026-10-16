"""Bind actual M2 evidence and write a milestone report without modifying M1."""
from argparse import ArgumentParser
import json
from pathlib import Path

from shoe_poc.lattice.pipeline import accepted_baseline
from shoe_poc.reporting import sha256, write_json, artifact_manifest, utc_now
from shoe_poc.schemas import ShoeSpec


def report(output: Path, document: Path):
    def read(name):
        return json.loads((output/name).read_text())
    execution, meta, tests = read('execution_report.json'), read('lattice_metadata.json'), read('test_report.json')
    validation, parameters, domain = read('validation_report.json'), read('parameter_change_report.json'), read('domain_metadata.json')
    m1_replay = read('debug/m1_frozen_replay_tests.json')
    baseline = execution['baseline']
    current = accepted_baseline(Path(baseline['directory']), ShoeSpec.load(output/'shoe_spec.json'), Path(baseline['acceptance_file']))
    if current != baseline or tests['lattice_build_provenance']['run_id'] != execution['run_id']:
        raise ValueError('Tests/baseline do not bind the current M2 run')
    for evidence in (execution['outputs'], tests['lattice_build_provenance']['files']):
        for name, record in evidence.items():
            if sha256(output/name) != record['sha256'] or (output/name).stat().st_size != record['bytes']:
                raise ValueError(f'Changed M2 evidence: {name}')
    if parameters['primary_run_id'] != execution['run_id'] or parameters['primary_stl_sha256'] != sha256(output/'lattice.stl'):
        raise ValueError('Parameter verification is not for the current mesh')
    for name, run in parameters['runs'].items():
        path = output/'variants'/name/'lattice.stl'
        if (run['status'] != 'PASS' or sha256(path) != run['lattice_sha256']
                or path.stat().st_size != run['lattice_bytes']
                or json.loads((path.parent/'verification_report.json').read_text()) != run):
            raise ValueError(f'Parameter verification evidence changed: {name}')
    if parameters['runs']['repeat']['lattice_sha256'] != sha256(output/'lattice.stl'):
        raise ValueError('Repeated actual STL differs from primary model')
    before = read('debug/m1_artifacts_before.json')
    changed = []
    for folder, records in before.items():
        for name, record in records.items():
            path = Path(baseline['directory']).parent/folder/name
            if not path.is_file() or sha256(path) != record['sha256'] or path.stat().st_size != record['bytes']:
                changed.append(str(path))
    preservation = {'status': 'PASS' if not changed else 'FAIL', 'checked_files': sum(map(len,before.values())),
                    'changed_files': changed, 'snapshot_sha256': sha256(output/'debug/m1_artifacts_before.json')}
    write_json(output/'m1_preservation_report.json', preservation)
    performance = {'primary_run_id': execution['run_id'], 'generated_at': utc_now(),
        'primary_cli_elapsed_seconds': execution['elapsed_seconds'],
        'primary_timings_seconds': execution['timings_seconds'],
        'primary_process_peak_rss_bytes': execution['environment']['process_peak_rss_bytes'],
        'original_cad_domain_sampling_seconds': domain['runtime_seconds'],
        'domain_source': execution['domain_source'],
        'voxel_count': domain['voxel_count'], 'spacing_mm': domain['spacing_mm'],
        'meshing_allocation_estimate_bytes': meta['meshing']['peak_memory_estimate_bytes'],
        'stl_bytes': (output/'lattice.stl').stat().st_size,
        'triangles': meta['meshing']['integrity']['triangles'],
        'variants': {name: {'elapsed_seconds': run['elapsed_seconds'],
                    'triangles': run['meshing']['integrity']['triangles'],
                    'bytes': run['lattice_bytes'], 'worker': run['worker'],
                    'process_peak_rss_bytes': run['environment']['process_peak_rss_bytes']}
                    for name,run in parameters['runs'].items()},
        'resource_scope': parameters['parallel_execution'],
        'note': 'Measured per-process RSS high-water marks and sampled guards; no aggregate OS hard limit. Cached original CAD sampling is recorded separately from replay timing.'}
    write_json(output/'performance_report.json', performance)
    passed = (execution['status'] == validation['geometry_status'] == parameters['status'] == preservation['status'] == 'PASS'
              and tests['counts']['FAIL'] == 0 and tests['counts']['NOT_TESTED'] == 0 and tests['pytest_exit_code'] == 0
              and m1_replay['counts']['FAIL'] == m1_replay['counts']['NOT_TESTED'] == 0
              and m1_replay['counts']['PASS'] > 0 and m1_replay['pytest_exit_code'] == 0)
    acceptance = {'milestone': 2, 'run_id': execution['run_id'], 'generated_at': utc_now(),
        'technical_status': 'PASS' if passed else 'FAIL', 'milestone_status': 'PARTIAL', 'user_acceptance': 'PENDING',
        'test_counts': tests['counts'], 'm1_preservation': preservation,
        'm1_replay': {'counts': m1_replay['counts'], 'report_sha256': sha256(output/'debug/m1_frozen_replay_tests.json')},
        'geometry_status': validation['geometry_status'], 'parameter_status': parameters['status'],
        'M3_status': 'NOT_STARTED', 'manufacturing_status': 'NOT_VERIFIED', 'physical_printing': 'NOT_TESTED',
        'wear_testing': 'NOT_TESTED', 'global_minimum_wall_thickness': 'NOT_VERIFIED',
        'field_threshold_is_wall_thickness_mm': False,
        'files': {n:{'sha256':sha256(output/n),'bytes':(output/n).stat().st_size} for n in
                  ['lattice.stl','lattice_metadata.json','preview.png','test_report.json','validation_report.json','parameter_change_report.json','performance_report.json']}}
    write_json(output/'acceptance_report.json', acceptance)
    write_json(output/'artifact_manifest.json', artifact_manifest(output))
    integrity = meta['meshing']['integrity']
    exported = validation['mesh']
    dims = ' × '.join(f'{v:.3f}' for v in exported['extents_mm'])
    z_range = ' 至 '.join(f'{0. if abs(v)<1e-9 else v:.3f}' for v in domain['sole_z_bounds_mm'])
    certified_candidates = domain['candidate_count']-domain['conservatively_omitted_candidate_count']
    extra_nodes = domain['certified_negative_node_count']-certified_candidates
    native = validation['native_domain_samples']
    residual = validation['gyroid_residual']
    nesting = validation['mesh']['shell_nesting']
    positive_shells = sum(row['signed_volume_mm3'] > 0 for row in nesting['shells'])
    negative_shells = nesting['negative_void_shell_count']
    rows=[]
    numeric_notes=[]
    for name, run in parameters['runs'].items():
        g=run['validation']['mesh'];p=run['settings']
        rows.append(f"| {name} | {p['period_mm']} | {p['threshold']} | {p['resolution_mm']} | {g['volume_mm3']:.3f} | {g['triangles']:,} | {run['status']} |")
        if run['meshing'].get('numerical_level_inset_dimensionless', 0.) > 0:
            m=run['meshing']
            numeric_notes.append(f"`{name}` 请求阈值 {p['threshold']}，目标 level 0；严格非退化检查后实际 level={m['level']:.12g}，有效 Gyroid 阈值={m['effective_gyroid_threshold']:.12g}，额外域内缩={m['numerical_domain_inset_mm']:.12g} mm。原始标量场保持不变；这是有界数值向内近似，不能声称精确使用请求零等值面。全部尝试和有效参数在变体报告中；解析误差阈值未放宽。")
    text=f'''# Milestone 2 — 晶格引擎验收报告

生成时间：{utc_now()}。运行 ID：`{execution['run_id']}`。算法：`{meta['algorithm_version']}`。

## 1. 完成状态

**技术检查：{acceptance['technical_status']}；M2 总体：PARTIAL；用户验收：PENDING。** M1 已接受的 16 mm 外形被冻结。本轮只实现 Step 3/4，M3 尚未开始。

## 2. 已完成功能

基于真实 M1 STEP 的材料域与鞋腔，生成 NumPy Gyroid 隐式场，限制计算边界与资源，再用 Lewiner Marching Cubes 提取闭合晶格。所有视图来自重新导入的实际 STL。没有平滑顶点、补洞或删除组件。

默认域 Z 范围约为 `{z_range}` mm（完整精度在域元数据中），限制在鞋楦基准面以下的实际鞋底材料。它故意不覆盖基准面以上的翘头部分；这是一块用于引擎验证的保守域，**不是整鞋填充方案**。实验性预留 {domain['clearance_mm']} mm，加上 {domain['certificate']['cell_diagonal_reserve_mm']:.6f} mm 全体素对角线数值保护，会减少实际保留区域，不能把这些值当作均匀外壳厚度。

EDT 只用于选择候选，共 {domain['candidate_count']:,} 个，其中保守省略 {domain['conservatively_omitted_candidate_count']:,} 个。精确 CAD 证书球还覆盖了候选选择器以外 {extra_nodes:,} 个合法节点，最终保留 {domain['certified_negative_node_count']:,} 个负节点。未证明的区域没有填入。域证书依赖 OCCT 的几何容差；额外的有限点/线检查不构成独立的全局包含或最小壁厚证明。

## 3. 实际文件

文件在 `output/m2/`：`lattice.stl`、`gyroid_field.npz`、`domain_field.npz`、`lattice_metadata.json`、`domain_metadata.json`、`preview.png`、`validation_report.json`、`execution_report.json`、`test_report.json`、`parameter_change_report.json`、`m1_preservation_report.json`、`performance_report.json`、`parameter_sections.png` 和 `acceptance_report.json`。`variants/` 保存完整参数变体 STL 及独立验证。`artifact_manifest.json` 记录主目录文件的字节数与 SHA-256；变体报告另绑定各自 STL。

## 4. 执行命令

```sh
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2.json --base-dir output/m1_toe_refine --output output/m2
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2.json --base-dir output/m1_toe_refine --output output/m2 --domain-cache output/m2/debug/attempt_01
.venv/bin/python scripts/verify_lattice.py --output output/m2 --resolution-domain output/m2/debug/domain/trial_06 --jobs 2
.venv/bin/python scripts/verify_lattice.py --output output/m2 --resolution-domain output/m2/debug/domain/trial_06 --jobs 2 --reuse-passed
.venv/bin/python -m pytest tests/unit tests/integration/test_lattice_real.py -q --report-json output/m2/test_report.json
.venv/bin/python -m pytest tests/integration/test_toe_refine.py -q --report-json output/m2/debug/m1_frozen_replay_tests.json
.venv/bin/python scripts/report_m2.py --output output/m2 --document docs/milestone2_report.md
```

第一条全新采样命令的域认证完成，但旧验证器因负向内孔壳与裁剪影响区误分类而返回失败，证据保存在 `debug/attempt_01/`。第二条命令严格验证同一域的源输入、参数、文件哈希和网格证书后复用它，并重新检查 STL。报告将原始域认证用时与缓存重放命令用时分开。`run` 仍只执行 M1。M2 的输入锁独立绑定支持 CAD 和原始执行报告，历史 M1 用户确认文件保持原样。嵌套输出与生成文件软链接在启动 RSS 保护线程之前被拒绝。

参数首轮的精确零等值面在阈值 0.45 产生浮点微小退化三角，严格拦截的报告保存在 `debug/parameter_attempt_01_failure.json` 和 `debug/threshold_exact_zero_failure/`。有界数值向内重提取后，仅重跑该阈值版的完整验证；其余三版在来源、运行、参数和 STL 哈希匹配后复用原 PASS 证据，未伪称重新执行。

## 5. 测试结果

本轮主回归 {tests['counts']['PASS']} 通过、{tests['counts']['FAIL']} 失败、{tests['counts']['NOT_TESTED']} 未运行；另有冻结 M1 真实 CAD/STL 重放 {m1_replay['counts']['PASS']} 项通过。测试包含旧协议/CLI 回归、场解析值与坐标、参数变化、资源预检、开放/退化/非流形/负体积、鞋腔与 Z 越界、分类未知状态、原生线覆盖不足、cap 非法材料、冻结输入与输出保护，以及保存的真实 M1/M2 证据重放。未重新构建 M1；历史失败日志保留在 `debug/`，不作为当前成功结果。

| 变体 | 周期 mm | 无量纲阈值 | 间距 mm | 体积 mm³ | 三角面 | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| primary | {spec_period(meta)} | {meta['field']['threshold']} | {meta['field']['spacing_mm']} | {exported['volume_mm3']:.3f} | {integrity['triangles']:,} | {execution['status']} |
{chr(10).join(rows)}

{chr(10).join(numeric_notes)}

同一已认证域重复场计算、独立曲面提取和 STL 导出，SHA-256 完全一致：`{parameters['repeatability']['identical_exported_stl_sha256']}`。这不是第二次完整 CAD 域重建的证明。分辨率变体重新认证原生 CAD 域，其体素对角线余量随间距变化，**不能当作固定域 Marching Cubes 收敛试验**。

## 6. 实际几何检查

导出并重导入后的尺寸为 **{dims} mm**；体积 {exported['volume_mm3']:.3f} mm³；{integrity['triangles']:,} 个三角面；连通组件 {integrity['connected_components']}；Euler 数 {integrity['euler_number']}。边界边 {integrity['boundary_edges']}、非流形边 {integrity['nonmanifold_edges']}、奇异顶点 {integrity['singular_vertex_count']}。STL 重导入、正材料净体积、壳嵌套方向与跨壳接触/交叉检查通过。实际边界包含 {positive_shells} 个正向材料外壳和 {negative_shells} 个反向封闭空隙内壳；负向内壳不等于倒置实体。{positive_shells-1} 个额外正向小壳仍保留，未做碎片删除，晶格尚未与鞋体外壳融合。这里没有宣称最终整鞋已连通。

原生 CAD 点样本 {native['sample_count']} 个，域外 {native['outside_sole_count']}、鞋腔侵入 {native['inside_cavity_count']}、Z 越界 {native['outside_z_bounds_count']}。独立线扫描结果与完整诊断见 `validation_report.json`。解析 Gyroid 内部曲面最大无量纲残差 {residual['interior']['maximum_absolute_residual']:.8f}，阈值 {residual['residual_tolerance_dimensionless']:.8f}；caps 单独分类，残差不是毫米误差或壁厚。

## 7. 性能

本次缓存复核 CLI 用时 {execution['elapsed_seconds']:.2f} s；原始 CAD 域采样实际用时 {domain['runtime_seconds']:.2f} s（已记录的先前采样，未计为本次重新执行）；采样 {domain['voxel_count']:,} 个体素；分辨率 {domain['spacing_mm']} mm；精确 CAD 距离查询 {domain['certificate']['query_count']:,} 次。曲面提取分配峰值估算 {meta['meshing']['peak_memory_estimate_bytes']/1024**2:.2f} MiB；进程高水位 RSS {execution['environment']['process_peak_rss_bytes']/1024**2:.2f} MiB；STL {(output/'lattice.stl').stat().st_size/1024**2:.2f} MiB。各阶段计时与参数变体性能在执行报告和变体报告中。RSS 为进程高水位，100 ms 采样保护不是 OS 硬上限。

## 8. 问题与边界

首轮真实网格检查将闭合空隙的反向表面误当成倒置实体。当前按嵌套深度奇偶判断方向，同时独立检查跨壳接触/交叉；倒置、错误嵌套与相交壳的负面测试必须失败。裁剪影响区按完整 MC 体素对角线区分，避免将 Lewiner 混合单元内部 fan 顶点当作纯 Gyroid 零等值面；解析残差阈值未放宽，全部顶点重新检查。

原型 `.8 mm` 试验发现真实曲面材料点被 OCCT 点分类器报告 OUT，但独立三轴完整 CAD 布尔截线均判定在材料内。原始失败和解决证据保留在 `debug/domain/`。当前算法交叉核验原生有向截线，并对歧义使用三轴布尔确认；不能消除的分歧会失败。不能因此把原生点分类器的全局可靠性记为 PASS。

`threshold` 是无量纲参数，不是毫米壁厚。真实壁厚标定、全局最小特征、自相交全面检测、粉末排出、晶格与外壳连接、力学性能、制造、实体打印与穿着验证仍未完成。Docker/Linux 本轮未运行。冻结快照比较 {preservation['checked_files']} 个 M1 文件，变化 {len(changed)} 个。

## 9. 后续任务

向用户提交实际 M2 STL、预览、参数与测试证据。用户确认 M2 后，再进入 M3 鞋面网孔、外底/鞋面/晶格融合和完整制造模型检查。

## 10. 验收结论

当前已执行的 M2 技术检查为 **{acceptance['technical_status']}**。M2 总体维持 **PARTIAL**，等待用户验收；制造/实体打印/穿着验证分别保持 NOT_VERIFIED / NOT_TESTED。
'''
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(text)
    if not passed:
        raise RuntimeError('M2 technical acceptance failed; inspect actual evidence')
    return acceptance


def spec_period(meta):
    return meta['field']['period_mm']


if __name__ == '__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/m2'))
    parser.add_argument('--document', type=Path, default=Path('docs/milestone2_report.md'))
    args=parser.parse_args()
    report(args.output,args.document)
