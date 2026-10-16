"""Bind actual terminal completion evidence without transferring old approvals."""
from argparse import ArgumentParser
import json
from pathlib import Path

from shoe_poc.lattice.pipeline import accepted_baseline, validate_output_paths
from shoe_poc.reporting import artifact_manifest, sha256, utc_now, write_json
from shoe_poc.schemas import ShoeSpec


def report(directory, document):
    directory = directory.resolve()
    def read(name):
        return json.loads((directory/name).read_text())
    execution, validation = read('execution_report.json'), read('validation_report.json')
    terminal, previous = read('terminal_coverage_report.json'), read('debug/previous_terminal_coverage_report.json')
    domain, mesh = read('domain_metadata.json'), read('lattice_metadata.json')
    tests, repeated, preview = read('test_report.json'), read('repeatability_report.json'), read('terminal_preview_report.json')
    replay = read('debug/m1_toe_frozen_replay_tests.json')
    baseline = execution['baseline']
    base = Path(baseline['directory'])
    validate_output_paths(directory, base)
    if accepted_baseline(base, ShoeSpec.load(directory/'shoe_spec.json'), Path(baseline['acceptance_file'])) != baseline:
        raise ValueError('Accepted CAD changed')
    provenance = tests['terminal_build_provenance']
    required_suites = {'tests/unit', 'tests/integration/test_lattice_real.py',
                       'tests/integration/test_lattice_toe_repair.py',
                       'tests/integration/test_lattice_toe_complete.py'}
    invocation = tests.get('invocation_args', [])
    if (not required_suites <= set(invocation)
            or any(arg in ('-k', '-m', '--deselect', '--ignore', '--ignore-glob', '--lf', '--last-failed')
                   or arg.startswith(('-k=', '-m=', '--deselect=', '--ignore=', '--ignore-glob=')) for arg in invocation)
            or set(tests['tests']) != set(tests.get('collected_nodeids', []))
            or tests['collected'] != len(tests['tests'])
            or not any(name.startswith('tests/integration/test_lattice_toe_complete.py::') for name in tests['tests'])):
        raise ValueError('Completion acceptance requires the full declared unfiltered regression suites')
    if provenance['run_id'] != execution['run_id'] or provenance['directory'] != str(directory):
        raise ValueError('Tests did not identify this completed build')
    for name, record in provenance['files'].items():
        if sha256(directory/name) != record['sha256'] or (directory/name).stat().st_size != record['bytes']:
            raise ValueError(f'Tested evidence changed: {name}')
    # Bind the original immutable geometry outputs explicitly; later verification
    # artifacts are additionally bound by tested provenance and the final manifest.
    for name in ('lattice.stl', 'domain_field.npz', 'domain_metadata.json', 'gyroid_field.npz',
                 'domain_provenance.json', 'validation_report.json', 'lattice_metadata.json', 'shoe_spec.json'):
        record = execution['outputs'][name]
        if sha256(directory/name) != record['sha256'] or (directory/name).stat().st_size != record['bytes']:
            raise ValueError(f'Build geometry changed: {name}')
    for key, name in (('lattice_sha256', 'lattice.stl'), ('domain_sha256', 'domain_field.npz'),
                      ('gyroid_sha256', 'gyroid_field.npz'), ('expected_nodes_sha256', 'debug/terminal_expectations.json')):
        if terminal[key] != sha256(directory/name):
            raise ValueError(f'Terminal report source changed: {name}')
    if previous['expected_nodes_sha256'] != terminal['expected_nodes_sha256']:
        raise ValueError('Old and new did not use identical native expectations')
    if previous['lattice_sha256'] != sha256(base.parent/'m2_toe_repair/lattice.stl'):
        raise ValueError('Historical failure does not bind the preserved STL')
    if (repeated['primary_stl_sha256'] != sha256(directory/'lattice.stl')
            or preview['new_lattice_sha256'] != sha256(directory/'lattice.stl')
            or preview['image_sha256'] != sha256(directory/'terminal_comparison.png')):
        raise ValueError('Repeat or preview identifies different geometry')
    preservation = {}
    for folder, files in read('debug/preserved_artifacts_before.json').items():
        changed = [name for name, record in files.items() if not (base.parent/folder/name).is_file()
                   or (base.parent/folder/name).stat().st_size != record['bytes']
                   or sha256(base.parent/folder/name) != record['sha256']]
        preservation[folder] = {'status': 'FAIL' if changed else 'PASS',
                                'checked_file_count': len(files), 'changed_files': changed}
    refinement = domain['refinement']
    passed = (execution['status'] == validation['geometry_status'] == terminal['geometry_status']
              == repeated['geometry_status'] == 'PASS' and previous['geometry_status'] == 'FAIL'
              and repeated['identical_replayed_domain']
              and repeated['domain_query_replay']['native_distance_queries'] == 0
              and refinement['status'] == 'COMPLETE' and not refinement['query_budget_reached']
              and tests['pytest_exit_code'] == tests['counts']['FAIL'] == tests['counts']['NOT_TESTED'] == 0
              and replay['pytest_exit_code'] == replay['counts']['FAIL'] == replay['counts']['NOT_TESTED'] == 0
              and all(row['status'] == 'PASS' for row in preservation.values()))
    limitations = {'M2_status': 'PARTIAL', 'user_acceptance': 'PENDING', 'M3_status': 'NOT_STARTED',
        'global_coverage': 'NOT_VERIFIED', 'global_minimum_wall_thickness': 'NOT_VERIFIED',
        'manufacturing': 'NOT_VERIFIED', 'physical_printing': 'NOT_TESTED', 'wear_testing': 'NOT_TESTED',
        'parameter_robustness': 'PARTIAL',
        'parameter_note': 'No new period, threshold or resolution variants claimed. Historical threshold 0.45 failure remains preserved in m2_toe_repair/debug/parameter_attempt_01.'}
    acceptance = {'generated_at': utc_now(), 'run_id': execution['run_id'],
        'toe_completion_status': 'PASS' if passed else 'FAIL', **limitations,
        'principal_x_before_mm': previous['mesh']['principal_bounds_mm'][1][0],
        'principal_x_after_mm': terminal['mesh']['principal_bounds_mm'][1][0],
        'refinement': refinement, 'tests': tests['counts'], 'frozen_M1_replay': replay['counts'],
        'preservation': preservation,
        'evidence': {name: sha256(directory/name) for name in
            ('lattice.stl', 'terminal_coverage_report.json', 'terminal_preview_report.json',
             'repeatability_report.json', 'test_report.json', 'validation_report.json')}}
    write_json(directory/'m1_preservation_report.json', preservation)
    performance = {'run_id': execution['run_id'], 'elapsed_seconds': execution['elapsed_seconds'],
        'timings_seconds': execution['timings_seconds'], 'domain_source': execution['domain_source'],
        'refinement': refinement, 'grid_nodes': domain['voxel_count'],
        'process_peak_rss_bytes': execution['environment']['process_peak_rss_bytes'],
        'stl_bytes': (directory/'lattice.stl').stat().st_size,
        'repeat_seconds': repeated['runtime_seconds'],
        'resource_scope': 'Process RSS high-water mark; sampled RSS guard is not a machine-wide hard cap.'}
    write_json(directory/'performance_report.json', performance)
    write_json(directory/'completion_report.json', acceptance)
    write_json(directory/'acceptance_report.json', acceptance)
    before, after = acceptance['principal_x_before_mm'], acceptance['principal_x_after_mm']
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(f'''# M2 鞋头主体补全

生成时间：{utc_now()}；运行 `{execution['run_id']}`。

本次实际技术检查 **{acceptance['toe_completion_status']}**；M2 总体 **PARTIAL**，用户外形验收待定，M3 未开始。M1 已确认鞋型和原有输出保持不变。

## 原因与修正

上一版曲线屋顶恢复了前掌上翘区，但 2 mm 间隔的稀疏认证点仍漏掉狭窄鞋头。原报告将孤立碎片的 X 最大值 274.494 mm 当作前端指标，主体实际只到 {before:.3f} mm。独立 CAD 测量识别出 {terminal['eligible_sample_count']} 个满足预留条件的节点；旧版漏掉 {len(previous['missing_domain_samples'])} 个，另有 {len(previous['detached_domain_samples'])} 个落在分离的设计域。旧的低比例分区检查没有发现整层断面，不能用其 PASS 证明主体完整。

现在在 X≥{refinement['corridor_start_x_mm']:.3f} mm 的末端区域自适应查询真实 CAD 边界，并补入经过距离证明的球域。原有负节点全部保留；屋顶、真实鞋体、鞋腔、2 mm 物理预留和完整体素对角线保护没有放宽。算法 `{refinement['version']}`；场算法 `{mesh['algorithm_version']}`。深度阈值为 {refinement['required_node_boundary_distance_mm']:.6f} mm，其中覆盖近似容差为 {refinement['coverage_tolerance_mm']} mm。完整性声明只覆盖指定走廊内深于该阈值的网格节点，不是连续域的全局精确偏移证明。

屋顶/计算边界及实际 CAD 最近点提供安全上界，减少无需进行的 CAD 查询。恢复文件只保存绑定输入与种子场的精确查询，必须重新完成覆盖遍历；它不是可直接验收的完整缓存。首次 16,000 次查询预算失败证据保存在 `debug/attempt_01_query_budget/`，未当作通过。

## 实际模型验证

主体前端由 **{before:.3f} mm → {after:.3f} mm**，实际延伸 {after-before:.3f} mm。此指标来自最大正体积连通 STL 表面，而非所有碎片的包围盒。独立预先测量的 CAD 节点缺失 {len(terminal['missing_domain_samples'])}、落在分离域 {len(terminal['detached_domain_samples'])}；末端失败截面 {len(terminal['failed_terminal_planes'])}。每个截面均重新计算解析 Gyroid 期望：有材料时要求主体 STL 三角面实际穿过，切面相切或孤立碎片不能满足检查。

真实 STL 共 {terminal['mesh']['surface_component_count']} 个边界表面，正向材料表面 {terminal['mesh']['positive_component_count']} 个；其余表面完整保留并在图中标为橙色。主体伸达通过不代表整鞋融合或所有碎片已连接。灰色是冻结 M1，蓝色为主体；剖面绿点代表域应存在的位置，可能落在合法 Gyroid 孔隙中。红叉表示测量位置不具备所需预留空间。最前端薄区仍不能强行填充晶格；M3 的外壳及实体过渡尚未生成。

原生 CAD 归属、鞋腔排除、网格水密/流形、方向、解析场误差及底域覆盖检查通过。使用绑定的精确查询记录重新遍历认证域，数组完全相同：`{repeated['identical_replayed_domain']}`，新增原生距离查询为 {repeated['domain_query_replay']['native_distance_queries']}。随后独立重新采样 Gyroid、提取及实际 STL 序列化 SHA 相同：`{repeated['identical_stl_sha256']}`。这是缓存查询重放，不是第二次全新原生 CAD 采样。

## 证据和性能

回归测试：{tests['counts']['PASS']} PASS，{tests['counts']['FAIL']} FAIL，{tests['counts']['NOT_TESTED']} NOT_TESTED。冻结 M1 重放：{replay['counts']['PASS']} PASS。保存的各目录文件核对总数 {sum(r['checked_file_count'] for r in preservation.values())}，变化 {sum(len(r['changed_files']) for r in preservation.values())}。

自适应查询 {refinement['query_count']} 次，新增负节点 {refinement['added_negative_node_count']:,}；网格 {domain['voxel_count']:,} 节点。实际主命令 {execution['elapsed_seconds']:.2f} s，峰值进程 RSS {performance['process_peak_rss_bytes']/1024**2:.2f} MiB。具体新增/复用原生查询与阶段时间在 `performance_report.json`。旧 CAD 域的采样时间单列在来源记录，未计成重新采样。

交付目录 `output/m2_toe_complete/`：`lattice.stl`、原始域/Gyroid 场、参数、主验证、`terminal_coverage_report.json`、同角度真实网格 `terminal_comparison.png`、重复性、测试、性能及文件哈希清单。STEP 继续使用冻结 M1 基础 CAD；隐式晶格输出 STL，不提供伪造的晶格 STEP。

```sh
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2_toe_complete.json --base-dir output/m1_toe_refine --domain-cache output/m2_toe_repair --output output/m2_toe_complete --max-voxels 25000000 --max-field-memory-mb 6144 --max-rss-mb 8192
.venv/bin/python scripts/verify_terminal_lattice.py
.venv/bin/python scripts/repeat_terminal_lattice.py
.venv/bin/python scripts/preview_terminal_lattice.py
.venv/bin/python -m pytest tests/unit tests/integration/test_lattice_real.py tests/integration/test_lattice_toe_repair.py tests/integration/test_lattice_toe_complete.py -q --report-json output/m2_toe_complete/test_report.json
.venv/bin/python scripts/report_terminal_lattice.py
```

本轮没有重新验证周期、阈值、分辨率变体；历史 0.45 失败仍保留，参数稳健性保持 PARTIAL。全局壁厚、制造、打印及穿着分别为 NOT_VERIFIED / NOT_TESTED。复杂几何诊断和初始重建由 GPT-6 Astra / high 执行；GPT-6.1 Sol / high 独立审查发现并复现 MC 网格平面验收误判。主任务完成整合、资源优化、该误判修正、实际运行、独立验证及报告；后续 Astra 配额不足后未声称其完成余下修改。
''')
    write_json(directory/'artifact_manifest.json', artifact_manifest(directory))
    if not passed:
        raise RuntimeError('Actual completion evidence failed; inspect completion_report.json')
    return acceptance


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/m2_toe_complete'))
    parser.add_argument('--document', type=Path, default=Path('docs/milestone2_toe_complete_report.md'))
    args = parser.parse_args()
    report(args.output, args.document)
