"""Revalidate saved native STEP footbed geometry without regenerating the CAD."""
import argparse
import json
from pathlib import Path
import time
from types import SimpleNamespace

from build123d import import_step
from shoe_poc.reporting import sha256, utc_now, write_json
from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.footbed import validate_underfoot_material


def revalidate(output):
    read = lambda name: json.loads((output/name).read_text())
    execution, design = read('execution_report.json'), read('design_report.json')
    if execution['run_id'] != design['run_id']:
        raise ValueError('Design and execution reports belong to different generations')
    for name in ('base_shoe.step', 'base_shoe.stl', 'last_normalized.step', 'cavity.step', 'shoe_spec.json', 'design_report.json'):
        if execution['outputs'][name]['sha256'] != sha256(output/name):
            raise ValueError(f'Artifact changed since the recorded build: {name}')
    spec = ShoeSpec.load(output/'shoe_spec.json')
    if sha256(Path(spec.last.file)) != execution['input_sha256']:
        raise ValueError('Source last changed since the recorded build')
    previous_sha = sha256(output/'design_report.json')
    before = time.perf_counter()
    build = SimpleNamespace(shape=import_step(output/'base_shoe.step'), cavity=import_step(output/'cavity.step'))
    underfoot = validate_underfoot_material(import_step(output/'last_normalized.step'), build, spec)
    design['underfoot'] = underfoot
    for check in design['checks']:
        if check['name'] == 'sampled_underfoot_material_thickness':
            value = underfoot['minimum_measured_directional_material_mm']
            check.update(value=value, status='PASS' if value is not None and value >= underfoot['minimum_directional_material_target_mm'] else 'FAIL')
        elif check['name'] == 'underfoot_clearance_and_region_coverage':
            check.update(value=underfoot['regions'], status=underfoot['geometry_status'])
    statuses = [check['status'] for check in design['checks']]
    missing = [entry for entry in design.get('unmeasurable_regions', [])
               if entry.get('region') not in {'underfoot', 'underfoot coverage'}]
    missing.extend({'region': 'underfoot', **entry} for entry in underfoot['measurement_errors'])
    if underfoot['geometry_status'] == 'NOT_VERIFIED':
        missing.append({'region': 'underfoot coverage', 'reason': 'Selected sites lack verified measurements'})
    design['unmeasurable_regions'] = missing
    design['geometry_status'] = 'FAIL' if 'FAIL' in statuses else 'PARTIAL' if missing or any(s != 'PASS' for s in statuses) else 'PASS'
    revision = {'checked_at': utc_now(), 'scope': 'Underfoot metrology on saved STEP only; CAD geometry and exported meshes are retained',
                'previous_design_report_sha256': previous_sha,
                'model_step_sha256': sha256(output/'base_shoe.step'),
                'validation_code_sha256': sha256(Path(__file__).resolve().parents[1]/'src/shoe_poc/validation/footbed.py'),
                'geometry_status': underfoot['geometry_status'],
                'classification_diagnostic_count': len(underfoot['classification_diagnostics']),
                'elapsed_seconds': time.perf_counter()-before}
    design.setdefault('post_build_revalidations', []).append(revision)
    write_json(output/'design_report.json', design)
    execution.setdefault('post_build_revalidations', []).append(revision)
    execution['design_geometry_status'] = design['geometry_status']
    if design['geometry_status'] != 'PASS':
        execution['status'] = 'FAIL'
    execution['outputs']['design_report.json'] = {'bytes': (output/'design_report.json').stat().st_size,
                                                  'sha256': sha256(output/'design_report.json')}
    write_json(output/'execution_report.json', execution)
    print(output, underfoot['geometry_status'], len(underfoot['samples']), len(underfoot['classification_diagnostics']))
    return underfoot['geometry_status'] == 'PASS'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if revalidate(parser.parse_args().output) else 3)
