"""Bind measured toe-repair artifacts and preserve failed parameter evidence."""
from argparse import ArgumentParser
import json
from pathlib import Path

from shoe_poc.lattice.pipeline import accepted_baseline, validate_output_paths
from shoe_poc.reporting import artifact_manifest, sha256, utc_now, write_json
from shoe_poc.schemas import ShoeSpec


def report(output: Path, document: Path):
    output = output.resolve()
    def read(name):
        return json.loads((output/name).read_text())
    execution, validation = read('execution_report.json'), read('validation_report.json')
    meta, domain, tests = read('lattice_metadata.json'), read('domain_metadata.json'), read('test_report.json')
    comparison, parameters = read('toe_coverage_report.json'), read('parameter_change_report.json')
    replay = read('debug/m1_frozen_replay_tests.json')
    baseline = execution['baseline']
    base = Path(baseline['directory'])
    validate_output_paths(output, base)
    if accepted_baseline(base, ShoeSpec.load(output/'shoe_spec.json'), Path(baseline['acceptance_file'])) != baseline:
        raise ValueError('Frozen repair input provenance changed')
    provenance = tests['lattice_build_provenance']
    toe_provenance = tests['toe_repair_provenance']
    if provenance['run_id'] != execution['run_id'] or provenance['directory'] != str(output):
        raise ValueError('Tests do not identify this repaired build')
    if toe_provenance['run_id'] != execution['run_id'] or toe_provenance['directory'] != str(output):
        raise ValueError('Toe comparison tests do not identify this repaired build')
    for evidence in (execution['outputs'], provenance['files'], toe_provenance['files']):
        for name, record in evidence.items():
            if sha256(output/name) != record['sha256'] or (output/name).stat().st_size != record['bytes']:
                raise ValueError(f'Changed repair evidence: {name}')
    for key, path in [('primary_lattice_sha256', output/'lattice.stl'),
                      ('old_lattice_sha256', base.parent/'m2/lattice.stl'),
                      ('reference_shoe_sha256', base/'base_shoe.step'),
                      ('reference_cavity_sha256', base/'cavity.step'),
                      ('source_domain_sha256', output/'domain_metadata.json'),
                      ('source_domain_field_sha256', output/'domain_field.npz'),
                      ('old_domain_field_sha256', base.parent/'m2/domain_field.npz'),
                      ('envelope_source_geometry_sha256', base/'geometry_report.json')]:
        if comparison[key] != sha256(path):
            raise ValueError(f'Changed independent coverage input: {key}')
    if parameters['primary_run_id'] != execution['run_id'] or parameters['primary_stl_sha256'] != sha256(output/'lattice.stl'):
        raise ValueError('Parameter checks identify another build')
    for name, run in parameters['runs'].items():
        path = output/'variants'/name
        if (sha256(path/'lattice.stl') != run['lattice_sha256']
                or (path/'lattice.stl').stat().st_size != run['lattice_bytes']
                or json.loads((path/'verification_report.json').read_text()) != run):
            raise ValueError(f'Changed actual parameter artifact: {name}')
    if parameters['runs']['repeat']['lattice_sha256'] != sha256(output/'lattice.stl'):
        raise ValueError('Exact-field repeated STL differs')
    preservation = {}
    for label, snapshot, groups in [
            ('M1', 'debug/m1_artifacts_before.json', None),
            ('original_M2', 'debug/m2_artifacts_before.json', 'm2')]:
        before = read(snapshot)
        before = before if groups is None else {groups: before}
        changed = []
        for folder, records in before.items():
            for name, record in records.items():
                path = base.parent/folder/name
                if not path.is_file() or path.stat().st_size != record['bytes'] or sha256(path) != record['sha256']:
                    changed.append(str(path))
        preservation[label] = {'status': 'FAIL' if changed else 'PASS', 'changed_files': changed,
            'checked_files': sum(map(len, before.values())), 'snapshot_sha256': sha256(output/snapshot)}
    write_json(output/'m1_preservation_report.json', preservation)
    failed_path = output/'debug/parameter_attempt_01/threshold_045_failure.json'
    failed = json.loads(failed_path.read_text())
    if failed['name'] != 'threshold_045' or failed['primary_m2_run_id'] != execution['run_id']:
        raise ValueError('Failed parameter evidence does not bind this run')
    negative = {'requested_threshold': .45, 'status': 'FAIL', 'error_type': failed['error_type'],
        'evidence': str(failed_path), 'evidence_sha256': sha256(failed_path),
        'field_sha256': sha256(output/'variants/threshold_045/gyroid_field.npz'),
        'note': 'Exact zero extraction failed strict non-area manifold checks. No topology tolerance was relaxed or mesh repaired. The separately requested 0.5 comparison is not a retry reported as 0.45.'}
    write_json(output/'parameter_limitations_report.json', negative)
    performance = {'run_id': execution['run_id'], 'primary_elapsed_seconds': execution['elapsed_seconds'],
        'primary_timings_seconds': execution['timings_seconds'], 'voxel_count': domain['voxel_count'],
        'spacing_mm': domain['spacing_mm'], 'domain_source': execution['domain_source'],
        'process_peak_rss_bytes': execution['environment']['process_peak_rss_bytes'],
        'meshing_allocation_estimate_bytes': meta['meshing']['peak_memory_estimate_bytes'],
        'stl_bytes': (output/'lattice.stl').stat().st_size,
        'variants': {name: {'elapsed_seconds': row['elapsed_seconds'],
            'process_peak_rss_bytes': row['environment']['process_peak_rss_bytes'], 'worker': row['worker']}
            for name, row in parameters['runs'].items()},
        'resource_scope': parameters['parallel_execution']}
    write_json(output/'performance_report.json', performance)
    passed = (execution['status'] == validation['geometry_status'] == parameters['status']
        == comparison['geometry_status'] == comparison['old_slab_regression_status'] == 'PASS'
        and all(row['status'] == 'PASS' for row in preservation.values())
        and tests['counts']['FAIL'] == tests['counts']['NOT_TESTED'] == tests['pytest_exit_code'] == 0
        and replay['counts']['FAIL'] == replay['counts']['NOT_TESTED'] == replay['pytest_exit_code'] == 0)
    acceptance = {'run_id': execution['run_id'], 'generated_at': utc_now(),
        'toe_repair_status': 'PASS' if passed else 'FAIL', 'milestone_status': 'PARTIAL',
        'user_acceptance': 'PENDING', 'selected_parameter_comparisons': parameters['status'],
        'parameter_robustness_status': 'PARTIAL', 'known_failed_parameter': negative,
        'test_counts': tests['counts'], 'm1_replay_counts': replay['counts'], 'preservation': preservation,
        'M3_status': 'NOT_STARTED', 'manufacturing_status': 'NOT_VERIFIED',
        'physical_printing': 'NOT_TESTED', 'wear_testing': 'NOT_TESTED',
        'global_minimum_wall_thickness': 'NOT_VERIFIED', 'global_coverage': 'NOT_VERIFIED'}
    write_json(output/'acceptance_report.json', acceptance)
    old_front = comparison['old_lattice']['actual_mesh_bounds_mm'][1][0]
    new_front = comparison['new_lattice']['actual_mesh_bounds_mm'][1][0]
    shoe_front = comparison['new_lattice']['bounds_mm'][1][0]
    old_check, new_check = comparison['old_lattice'], comparison['new_lattice']
    integrity = meta['meshing']['integrity']
    native = validation['native_domain_samples']
    nesting = validation['mesh']['shell_nesting']
    positive_shells = sum(row['signed_volume_mm3'] > 0 for row in nesting['shells'])
    rows = '\n'.join(f"| {name} | {run['settings']['period_mm']} | {run['settings']['threshold']} | {run['validation']['mesh']['volume_mm3']:.3f} | {run['status']} |"
                     for name, run in parameters['runs'].items())
    document.write_text(f'''# M2 脚趾晶格覆盖修复

生成时间：{utc_now()}。主运行：`{execution['run_id']}`。算法：`{meta['algorithm_version']}`。

## 状态与问题

本次脚趾覆盖修复：**{acceptance['toe_repair_status']}**。M2 保持 **PARTIAL**，用户外形确认待定；M3 未开始。主模型与已执行的周期/阈值 0.5/重复导出检查通过。阈值 0.45 在新域中触发严格非流形检查，保留失败证据，参数稳健性仍为 PARTIAL；不能宣称所有合法参数均已成功。

旧域用水平 Z 上界裁掉上翘鞋头。现在由冻结 M1 的 rocker 和下部足型连接高度构造随 X 变化的屋顶：取 rocker 加名义底厚、连接导线减过渡半带的较低值。1 mm 上限间距的线段定义明确设计域；不是原鞋底 B-Rep 的精确重建。真实鞋体、鞋腔和底面圆角继续限制材料。证书半径同时使用真实 CAD 边界距离和屋顶线段的欧氏距离，再扣除预留及完整体素对角线，不能用竖直间距替代斜面法向距离。

## 实际前端与覆盖

鞋体前端 X={shoe_front:.3f} mm。旧晶格前端 {old_front:.3f} mm，新晶格 {new_front:.3f} mm，恢复 **{new_front-old_front:.3f} mm**；与鞋体最前端的 X 间隔由 {shoe_front-old_front:.3f} mm 缩至 {shoe_front-new_front:.3f} mm。这是包围盒差值，不是均匀外壳厚度；尖端余量、薄区和保守省略仍存在，尚未生成实体过渡或与外壳融合。

新旧模型都用同一个曲线设计域、同一冻结 CAD 做独立竖向材料区间检查，未用旧晶格自身边界决定检查范围。旧模型覆盖结果 **{old_check['geometry_status']}**，失败分区 {old_check['failed_bin_indices']}；新模型 **{new_check['geometry_status']}**，失败分区 {new_check['failed_bin_indices']}。新模型要求覆盖的分区 {new_check['coverage_required_bin_count']} 个，其中基准面以上的上翘区分区 {new_check['raised_coverage_required_bin_count']} 个。薄区、空区、实际可用区的每个采样位置都在 `toe_coverage_report.json`，有限线长求和不是体积或全局覆盖证明。

周期 16 mm 的末端分区初次稀疏测量落入相位空隙。该分区使用预先确定的 7 × 7 X/Y 点阵加密，保持原覆盖阈值，并保留首次测量。新增位置重新测量真实 CAD 与 STL，未平滑或修改三角面。已通过的其他检查只在原 STL、设计域、CAD 来源哈希匹配时复用；补充测量用时、环境、原报告哈希和新增/复用原生站点数单独记录，未伪称整套变体全部重新运行。

## 文件与预览

结果位于 `output/m2_toe_repair/`：`lattice.stl`、原始场 NPZ、`domain_provenance.json`、元数据、`validation_report.json`、`toe_coverage_report.json`、`toe_comparison.png`、`preview.png`、`parameter_sections.png`、测试/性能/验收报告和文件哈希清单。所有图片来自实际 STL 三角面，没有平滑或删掉碎片。灰色 M1 与新旧晶格使用相同坐标、相同相机和画幅。旧 `output/m2/` 和 M1 全部保留。

## 测试与参数

主回归：{tests['counts']['PASS']} PASS、{tests['counts']['FAIL']} FAIL、{tests['counts']['NOT_TESTED']} NOT_TESTED。冻结 M1 重放：{replay['counts']['PASS']} PASS。核对 {preservation['M1']['checked_files']} 个 M1 历史文件及 {preservation['original_M2']['checked_files']} 个旧 M2 文件，变动均为 0。

主 STL 重导入后有 {integrity['triangles']:,} 个三角面，边界边 {integrity['boundary_edges']}、非流形边 {integrity['nonmanifold_edges']}、奇异顶点 {integrity['singular_vertex_count']}；水密、方向及跨壳分离检查通过。{len(nesting['shells'])} 个边界壳中 {positive_shells} 个为正向材料外壳、{nesting['negative_void_shell_count']} 个为反向内部空隙壳；{positive_shells-1} 个额外材料碎片仍保留，不能声称最终整鞋已连接。原生 CAD 点检查 {native['sample_count']} 个，鞋体外 {native['outside_sole_count']}、侵入鞋腔 {native['inside_cavity_count']}；独立线扫描和解析场误差检查通过。域候选节点中 {domain['conservatively_omitted_candidate_count']:,} 个未获证书而被保守省略，不能把保留域当作全局精确偏移结果。

| 比较 | 周期 mm | 请求阈值 | STL 体积 mm³ | 结果 |
| --- | ---: | ---: | ---: | --- |
{rows}

重复导出 SHA 相同：`{parameters['repeatability']['identical_exported_stl_sha256']}`。这是同一认证域的独立场/提取重复，不是第二次 CAD 域重建。本轮未重新生成分辨率变体；原 M2 的分辨率证据保留，但不能代替新曲线域的分辨率验证。

0.45 失败记录：`debug/parameter_attempt_01/threshold_045_failure.json`。该次精确零等值面同时出现微小三角和非流形边，按现有规则立即停止，未用数值偏移绕过拓扑失败。修复了诊断异常的进程间序列化，避免一个几何失败破坏整个 worker pool。另以明确请求的 0.5 验证阈值确实改变真实几何；没有把 0.5 报成 0.45 通过。

## 性能与执行

主命令 {execution['elapsed_seconds']:.2f} s，其中新 CAD 域采样 {domain['runtime_seconds']:.2f} s；{domain['voxel_count']:,} 个网格节点，间距 {domain['spacing_mm']} mm。主进程峰值 RSS {performance['process_peak_rss_bytes']/1024**2:.2f} MiB；STL {performance['stl_bytes']/1024**2:.2f} MiB。RSS 为进程高水位，保护为采样机制，不是整机硬内存限制。

```sh
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2_toe_repair.json --base-dir output/m1_toe_refine --output output/m2_toe_repair --max-voxels 25000000 --max-field-memory-mb 6144 --max-rss-mb 8192
.venv/bin/python scripts/preview_lattice_toe_repair.py
.venv/bin/python scripts/verify_lattice.py --output output/m2_toe_repair --jobs 2 --worker-memory-mb 6144 --threshold-variant 0.5
.venv/bin/python scripts/verify_lattice.py --output output/m2_toe_repair --jobs 2 --worker-memory-mb 6144 --threshold-variant 0.5 --reuse-passed
SHOE_POC_M2_OUTPUT=output/m2_toe_repair .venv/bin/python -m pytest tests/unit tests/integration/test_lattice_real.py tests/integration/test_lattice_toe_repair.py -q --report-json output/m2_toe_repair/test_report.json
.venv/bin/python scripts/report_m2_toe_repair.py
```

几何域由 GPT-6 Astra / high 实现，独立验证由 GPT-6.1 Sol / high 准备，主任务完成真实执行、整合及剩余修正。M1 已确认外形未改动。本阶段仍是独立晶格，保留孤立碎片并报告；外壳融合、末端实体过渡、完整整鞋连通、全局最小壁厚、制造、实体打印和穿着均未验证或未实现。
''')
    write_json(output/'artifact_manifest.json', artifact_manifest(output))
    if not passed:
        raise RuntimeError('Toe repair evidence did not qualify; inspect acceptance_report.json')
    return acceptance


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/m2_toe_repair'))
    parser.add_argument('--document', type=Path, default=Path('docs/milestone2_toe_repair_report.md'))
    args = parser.parse_args()
    report(args.output, args.document)
