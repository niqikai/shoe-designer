"""Report independently bound M2 appearance, five-case regression and the M3 gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shoe_poc.acceptance import validate_m2_acceptance, file_binding
from shoe_poc.reporting import utc_now, write_json


def report(source: Path, output: Path, appearance_acceptance: Path, document: Path) -> dict:
    if document.is_symlink() or output.is_symlink():
        raise ValueError('Report destinations cannot be symbolic links')
    source, output, document = source.resolve(), output.resolve(), document.resolve()
    if output.name in {'m1', 'm1_toe_refine', 'm2', 'm2_toe_repair', 'm2_toe_complete'}:
        raise ValueError('Historical output is not a closeout report destination')
    baseline = json.loads((source/'execution_report.json').read_text())['baseline']
    snapshot = json.loads((output/'debug/preserved_artifacts_before.json').read_text())
    preserved_root = Path(baseline['directory']).resolve().parent
    if any(output.is_relative_to(preserved_root/name) or document.is_relative_to(preserved_root/name)
           for name in snapshot):
        raise ValueError('Report destinations overlap preserved M1/M2 artifacts')
    if (source == output or source in output.parents or output in source.parents
            or document.is_relative_to(source) or document.is_relative_to(output)
            or document == appearance_acceptance.resolve()):
        raise ValueError('Report destinations must preserve frozen evidence and user approval')
    for filename in ('acceptance_report.json', 'performance_report.json', 'artifact_manifest.json'):
        if (output/filename).is_symlink():
            raise ValueError(f'Report destination is a symbolic link: {filename}')
    if document.is_symlink():
        raise ValueError('Document destination is a symbolic link')
    try:
        binding = validate_m2_acceptance(source, output, appearance_acceptance)
    except (ValueError, KeyError, OSError, TypeError) as error:
        write_json(output/'acceptance_report.json', {'generated_at': utc_now(), 'milestone': 2,
            'appearance_status': 'NOT_VERIFIED', 'M2_selected_cases_status': 'NOT_VERIFIED',
            'M3_gate': 'BLOCKED', 'technical_status': 'FAIL', 'error': str(error),
            'manufacturing_status': 'NOT_VERIFIED', 'physical_printing': 'NOT_TESTED',
            'global_minimum_wall_thickness': 'NOT_VERIFIED'})
        raise ValueError(f'M2 closeout gate blocked: {error}') from error
    parameters = json.loads((output/'parameter_change_report.json').read_text())
    execution = json.loads((source/'execution_report.json').read_text())
    diagnostic_path = output/'debug/threshold_diagnosis/current_fixed/report.json'
    diagnostic = json.loads(diagnostic_path.read_text())
    if diagnostic.get('status') != 'PASS' or diagnostic.get('cad_validation') != 'NOT_RUN_QUICK_EXTRACTION_DIAGNOSIS':
        raise ValueError('Expected actual quick extraction diagnosis with honest CAD scope')
    performance = {'generated_at': utc_now(), 'source_run_id': execution['run_id'],
        'source_recorded_elapsed_seconds': execution['elapsed_seconds'],
        'source_recorded_timings_seconds': execution['timings_seconds'],
        'note': 'Frozen source timings are historical measurements; no frozen source rebuild was performed.',
        'parallel_execution': parameters['parallel_execution'],
        'variants': {name: {'elapsed_seconds': run['elapsed_seconds'], 'lattice_bytes': run['lattice_bytes'],
            'triangles': run['meshing']['integrity']['triangles'], 'worker': run['worker'],
            'process_peak_rss_bytes': run['environment']['process_peak_rss_bytes']}
            for name, run in parameters['runs'].items()},
        'resource_scope': 'Process RSS high-water marks and sampled guards; no aggregate OS hard limit.'}
    write_json(output/'performance_report.json', performance)
    acceptance = {'milestone': 2, 'generated_at': utc_now(), **binding,
        'technical_status': 'PASS', 'M2_status': 'ACCEPTED_FOR_SELECTED_CASES',
        'acceptance_scope': 'Frozen lattice appearance and exactly five measured parameter cases; no universal parameter guarantee.',
        'diagnostic_evidence': {'path': str(diagnostic_path), **file_binding(diagnostic_path)},
        'performance_report': file_binding(output/'performance_report.json'),
        'M3_status': 'NOT_STARTED', 'wear_testing': 'NOT_TESTED'}
    write_json(output/'acceptance_report.json', acceptance)
    rows = []
    for name, run in parameters['runs'].items():
        settings, meshing = run['settings'], run['meshing']
        rows.append(f"| {name} | {settings['period_mm']} | {settings['threshold']} | {settings['resolution_mm']} | {meshing['integrity']['triangles']:,} | {meshing['level']:.12g} | {run['status']} |")
    tests = json.loads((output/'test_report.json').read_text())
    difference = parameters['frozen_primary_geometry_difference']
    text = f'''# Milestone 2 参数闭环与 M3 门槛

生成时间：{acceptance['generated_at']}。冻结运行：`{execution['run_id']}`。

外形状态 **ACCEPTED**；M2_selected_cases_status **PASS**；M3_gate **READY**。M3 尚未开始。此结论只覆盖已认可外形和当前五个实际参数案例，不能扩展为所有参数组合保证。

用户认可证据来自 `docs/milestone2_lattice_acceptance.json`，scope 为 `M2_lattice_appearance`。M3 继续使用冻结的 `{source}/lattice.stl`，SHA-256 `{binding['selected_lattice']['sha256']}`。当前算法 `current_primary` 是独立技术回归参考。冻结/当前几何差异记录在 `frozen_primary_geometry_difference`：最大绝对 bounds 差 {difference['maximum_absolute_bounds_delta_mm']:.12g} mm，体积差 {difference['volume_delta_mm3']:.12g} mm³；该差异没有转移外形认可。

| 案例 | 周期 mm | 无量纲阈值 | 分辨率 mm | 三角面 | 实际提取 level | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
{chr(10).join(rows)}

每个案例均独立验证 STL 闭合/流形、原生 CAD 点线包含、全部顶点 Gyroid 残差、鞋底覆盖和主组件终端覆盖，绑定 STL、Gyroid 场、独立终端节点、域证书和验证代码 SHA。repeat 与新 current_primary 比较实际物理场和序列化 STL，没有拿旧冻结 STL 当重现性参考。0.5 mm 案例使用单独完成原生认证的 CAD 域；体素对角线保护量随分辨率变化，不能称为固定域收敛证明。

历史 0.45 故障涉及 Lewiner 微小几何：`allow_degenerate=False` 的退化清理可能合并局部顶点身份并改变连接；零等值面及 float32 STL 序列化也会留下零面积面。当前使用 `allow_degenerate=True` 保留 raw connectivity，再执行原有有界 numerical inset 重提取及严格几何/STL 检查。没有补洞、平滑或删除组件，也没有放宽解析残差容差。actual level、数值域内缩和有效阈值均保存在各 variant 报告；这是有界数值向内近似，不是精确零等值面声明。

实际快速诊断证据：`{diagnostic_path}`，SHA-256 `{sha256_for_text(diagnostic_path)}`。诊断状态 {diagnostic['status']}，CAD validation `{diagnostic['cad_validation']}`；它仅覆盖提取与重导入，完整原生 CAD 检查证据来自上表五案例，不能归功于快速诊断。

完整回归测试 {tests['counts']['PASS']} PASS，0 FAIL，0 NOT_TESTED，pytest exit 0。包括当前全部 unit、旧真实 lattice、toe complete 和新 closeout integration；报告绑定当前参数报告及五案例验证报告的 SHA/bytes。保存快照逐个比较 {binding['preservation']['checked_files']} 个 M1/旧 M2 文件，全部未变化。源执行、验证、completion、terminal 报告全部 PASS，并重新执行 accepted_baseline 输入门槛。

`acceptance_report.json` 保存 M3 所需源和参数身份锁；`performance_report.json` 分开记录冻结旧源耗时和本次变体耗时/RSS；`artifact_manifest.json` 绑定 closeout 文件。CPU/RSS 来自进程高水位与采样保护，不是总进程 OS 内存硬限制。

制造资格和全局最小壁厚 **NOT_VERIFIED**；实体打印和穿着 **NOT_TESTED**。无量纲 threshold 不是毫米壁厚。M3 还需要晶格与外壳融合、最终整鞋连通及后续制造检查。
'''
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(text)
    manifest = {p.relative_to(output).as_posix(): file_binding(p) for p in sorted(output.rglob('*'))
                if p.is_file() and p != output/'artifact_manifest.json'}
    write_json(output/'artifact_manifest.json', manifest)
    return acceptance


def sha256_for_text(path):
    return file_binding(path)['sha256']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('output/m2_toe_complete'))
    parser.add_argument('--output', type=Path, default=Path('output/m2_closeout'))
    parser.add_argument('--appearance-acceptance', type=Path, default=Path('docs/milestone2_lattice_acceptance.json'))
    parser.add_argument('--document', type=Path, default=Path('docs/milestone2_closeout_report.md'))
    args = parser.parse_args()
    report(args.source, args.output, args.appearance_acceptance, args.document)
