"""Independent CAD toe probes and principal-component coverage measurements."""
from argparse import ArgumentParser
import json
import math
from pathlib import Path
import time

import numpy as np

from shoe_poc.lattice.envelope import SoleEnvelope
from shoe_poc.lattice.pipeline import accepted_baseline, validate_output_paths
from shoe_poc.reporting import sha256, utc_now, write_json
from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.lattice_terminal import validate_terminal_coverage


def sample_expected(source, output):
    from build123d import import_step, Compound, Vertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from shoe_poc.validation.lattice_coverage import NativeVerticalSoleSections
    started = time.perf_counter()
    execution = json.loads((source/'execution_report.json').read_text())
    baseline = execution['baseline']
    base = Path(baseline['directory'])
    validate_output_paths(output, base)
    spec = ShoeSpec.load(source/'shoe_spec.json')
    assert accepted_baseline(base, spec, Path(baseline['acceptance_file'])) == baseline
    meta = json.loads((source/'domain_metadata.json').read_text())
    envelope = SoleEnvelope.from_metadata(meta['envelope'])
    if envelope is None:
        raise ValueError('Native terminal probes need the explicit curved roof')
    shoe, cavity = import_step(base/'base_shoe.step'), import_step(base/'cavity.step')
    sections = NativeVerticalSoleSections(shoe, cavity, upper_z=envelope.upper_z, z_min_mm=envelope.z_bounds[0])
    boundary = Compound(children=[*shoe.faces(), *cavity.faces()])
    with np.load(source/'domain_field.npz', allow_pickle=False) as saved:
        shape, origin, spacing = saved['field_mm'].shape, saved['origin_mm'], float(saved['spacing_mm'])
    axes = tuple(origin[i]+np.arange(shape[i])*spacing for i in range(3))
    stations = json.loads((base/'geometry_report.json').read_text())['sole']['lower_foot_station_profiles']
    xs = np.array([s['x_mm'] for s in stations])
    centers = np.array([(s['left_y_mm']+s['right_y_mm'])/2 for s in stations])
    halfwidths = np.array([(s['right_y_mm']-s['left_y_mm'])/2 for s in stations])
    box = shoe.bounding_box()
    selected_x = np.flatnonzero((axes[0] >= box.max.X-spec.lattice.period_mm) & (axes[0] <= box.max.X))
    bounds = np.asarray(meta['computation_bounds_mm'], float)
    rows = []
    for ix in selected_x:
        x = float(axes[0][ix])
        center, half = np.interp(x, xs, centers), np.interp(x, xs, halfwidths)
        for y_target in center+half*np.array([-.45, 0., .45]):
            iy = int(np.argmin(abs(axes[1]-y_target)))
            y = float(axes[1][iy])
            native = sections(x, y)
            z_indices = set()
            for low, high in native['sole_intervals_mm']:
                if high-low > spacing:
                    z_indices.update(int(np.argmin(abs(axes[2]-z))) for z in np.linspace(low+.05, high-.05, 7))
            for iz in sorted(z_indices):
                point = np.array([x, y, axes[2][iz]])
                inside = any(a+1e-6 < point[2] < b-1e-6 for a, b in native['sole_intervals_mm'])
                distance = None
                if inside:
                    exact = BRepExtrema_DistShapeShape(Vertex(*point).wrapped, boundary.wrapped)
                    if not exact.IsDone() or not math.isfinite(exact.Value()):
                        raise RuntimeError('Independent terminal CAD boundary distance failed')
                    distance = min(float(exact.Value()), envelope.boundary_distance(point),
                                   float(np.min(point-bounds[0])), float(np.min(bounds[1]-point)))
                rows.append({'grid_index': [int(ix), iy, iz], 'point_mm': point.tolist(),
                    'inside_intended_material': bool(inside), 'minimum_boundary_distance_mm': distance})
        if ix % 5 == 0:
            print(f'Independent terminal CAD probes: X={x:.3f}, {len(rows)} sites', flush=True)
    assert accepted_baseline(base, spec, Path(baseline['acceptance_file'])) == baseline
    result = {'generated_at': utc_now(), 'baseline': baseline, 'envelope': envelope.metadata,
        'origin_mm': origin.tolist(), 'spacing_mm': spacing, 'shape_xyz': list(shape),
        'clearance_mm': meta['clearance_mm'], 'source_grid_directory': str(source.resolve()),
        'runtime_seconds': time.perf_counter()-started, 'nodes': rows,
        'scope': 'Independent finite native Boolean material sections and native exact boundary distances at terminal grid nodes; no generation anchors reused'}
    write_json(output/'debug/terminal_expectations.json', result)
    return result


def verify(directory, expected_path, report_path=None):
    started = time.perf_counter()
    expected = json.loads(expected_path.read_text())
    execution = json.loads((directory/'execution_report.json').read_text())
    spec = ShoeSpec.load(directory/'shoe_spec.json')
    baseline = accepted_baseline(Path(execution['baseline']['directory']), spec,
                                 Path(execution['baseline']['acceptance_file']))
    if expected['baseline'] != baseline:
        raise ValueError('Terminal expectation CAD differs from the current accepted inputs')
    meta = json.loads((directory/'domain_metadata.json').read_text())
    if meta['envelope'] != expected['envelope'] or meta['clearance_mm'] != expected['clearance_mm']:
        raise ValueError('Terminal expectation changes the intended curved domain or clearance')
    with np.load(directory/'domain_field.npz', allow_pickle=False) as saved:
        field, origin, spacing = saved['field_mm'], saved['origin_mm'], float(saved['spacing_mm'])
    if (list(field.shape) != expected['shape_xyz'] or spacing != expected['spacing_mm']
            or not np.allclose(origin, expected['origin_mm'], rtol=0, atol=1e-9)):
        raise ValueError('Terminal expectation grid differs from the measured field')
    with np.load(directory/'gyroid_field.npz', allow_pickle=False) as saved:
        lattice = saved['values']
        field_metadata = json.loads(str(saved['metadata_json']))
    lattice_metadata = json.loads((directory/'lattice_metadata.json').read_text())
    policy = spec.lattice.domain.refinement
    tolerance = .1 if policy is None else policy.coverage_tolerance_mm
    result = validate_terminal_coverage(directory/'lattice.stl', field_mm=field, gyroid_values=lattice,
        origin_mm=origin, spacing_mm=spacing, expected_nodes=expected['nodes'],
        clearance_mm=meta['clearance_mm'], approximation_tolerance_mm=tolerance,
        analytic_parameters={'period_mm': spec.lattice.period_mm, 'threshold': spec.lattice.threshold,
            'phase_origin_mm': spec.lattice.phase_origin_mm, 'cap_inset_mm': field_metadata['cap_inset_mm']},
        actual_level=lattice_metadata['meshing']['level'])
    result.update({'run_id': execution['run_id'], 'generated_at': utc_now(),
        'runtime_seconds': time.perf_counter()-started, 'expected_nodes_sha256': sha256(expected_path),
        'lattice_sha256': sha256(directory/'lattice.stl'), 'domain_sha256': sha256(directory/'domain_field.npz'),
        'gyroid_sha256': sha256(directory/'gyroid_field.npz'), 'baseline': baseline})
    target = report_path or directory/'terminal_coverage_report.json'
    validate_output_paths(target.parent, Path(baseline['directory']))
    if target.is_symlink():
        raise ValueError('Terminal report must not be a symbolic link')
    write_json(target, result)
    print(json.dumps({key: result[key] for key in ('geometry_status', 'eligible_sample_count', 'mesh')}, indent=2))
    return result


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/m2_toe_complete'))
    parser.add_argument('--grid-source', type=Path, default=Path('output/m2_toe_repair'))
    parser.add_argument('--sample-only', action='store_true')
    parser.add_argument('--expected', type=Path)
    parser.add_argument('--report', type=Path, help='Separate report destination when examining preserved historical artifacts')
    args = parser.parse_args()
    if args.sample_only:
        sample_expected(args.grid_source, args.output)
    else:
        result = verify(args.output, args.expected or args.output/'debug/terminal_expectations.json', args.report)
        if result['geometry_status'] != 'PASS':
            raise SystemExit('Principal terminal coverage failed; inspect terminal_coverage_report.json')
