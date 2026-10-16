"""Compare preserved old and repaired M2 STLs against the same frozen CAD sole.

Run only after primary generation. Accepted M1 and historical M2 are read-only.
"""
from argparse import ArgumentParser
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator
import trimesh

from shoe_poc.lattice.envelope import SoleEnvelope
from shoe_poc.lattice.pipeline import accepted_baseline, validate_output_paths
from shoe_poc.preview import rasterize
from shoe_poc.reporting import sha256, utc_now, write_json
from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.lattice_coverage import NativeVerticalSoleSections, validate_lattice_coverage


def _field(path):
    with np.load(path, allow_pickle=False) as saved:
        values = saved['field_mm']
        origin, spacing = saved['origin_mm'], float(saved['spacing_mm'])
    axes = tuple(origin[i]+np.arange(values.shape[i])*spacing for i in range(3))
    return RegularGridInterpolator(axes, values, bounds_error=False, fill_value=spacing)


def compare(old: Path, new: Path, baseline: Path):
    from build123d import import_step
    old, new, baseline = old.resolve(), new.resolve(), baseline.resolve()
    validate_output_paths(new, baseline)
    if old == new or old.is_relative_to(new) or new.is_relative_to(old):
        raise ValueError('Historical and repaired outputs must be separate non-nested directories')
    for name in ('toe_coverage_report.json', 'toe_comparison.png'):
        if (new/name).is_symlink():
            raise ValueError('Toe comparison output must not be a symbolic link')
    execution = json.loads((new/'execution_report.json').read_text())
    bound = execution['baseline']
    current = accepted_baseline(baseline, ShoeSpec.load(new/'shoe_spec.json'), Path(bound['acceptance_file']))
    if current != bound:
        raise ValueError('Toe comparison baseline differs from the accepted repair inputs')
    metadata = json.loads((new/'domain_metadata.json').read_text())
    parameters = json.loads((new/'lattice_metadata.json').read_text())['actual_lattice_parameters']
    envelope = SoleEnvelope.from_metadata(metadata['envelope'])
    if envelope is None:
        raise ValueError('Toe repair comparison requires the explicit curved design envelope')
    if envelope.metadata['source_geometry_report_sha256'] != sha256(baseline/'geometry_report.json'):
        raise ValueError('Curved envelope does not bind the accepted geometry guide')
    shoe, cavity = import_step(baseline/'base_shoe.step'), import_step(baseline/'cavity.step')
    native = NativeVerticalSoleSections(shoe, cavity, upper_z=envelope.upper_z, z_min_mm=envelope.z_bounds[0])
    cached_sections = lru_cache(maxsize=4096)(native)
    datum = envelope.z_bounds[0]+json.loads((baseline/'shoe_spec.json').read_text())['sole']['height_mm']
    result = {'generated_at': utc_now(), 'geometry_status': 'NOT_VERIFIED',
        'scope': 'Independent finite native CAD vertical sections in the same intended curved domain; measured old and new actual STL material intervals',
        'primary_lattice_sha256': sha256(new/'lattice.stl'),
        'old_lattice_sha256': sha256(old/'lattice.stl'),
        'reference_shoe_sha256': sha256(baseline/'base_shoe.step'),
        'reference_cavity_sha256': sha256(baseline/'cavity.step'),
        'source_domain_sha256': sha256(new/'domain_metadata.json'),
        'source_domain_field_sha256': sha256(new/'domain_field.npz'),
        'old_domain_field_sha256': sha256(old/'domain_field.npz'),
        'envelope_source_geometry_sha256': envelope.metadata['source_geometry_report_sha256'],
        'same_expected_CAD_domain_for_both_models': True,
        'M1_status': 'FROZEN', 'M3_status': 'NOT_STARTED', 'manufacturing_status': 'NOT_VERIFIED'}
    for label, directory in [('old_lattice', old), ('new_lattice', new)]:
        print(f'Measuring {label} against frozen CAD and repaired design envelope', flush=True)
        result[label] = validate_lattice_coverage(directory/'lattice.stl',
            native_sections=cached_sections, bounds_mm=native.bounds_mm,
            period_mm=parameters['period_mm'], resolution_mm=parameters['resolution_mm'],
            clearance_mm=metadata['clearance_mm'], domain_field=_field(directory/'domain_field.npz'),
            reference_datum_mm=datum)
        result[label]['actual_mesh_bounds_mm'] = trimesh.load_mesh(directory/'lattice.stl', process=False).bounds.tolist()
    result['geometry_status'] = result['new_lattice']['geometry_status']
    result['old_slab_regression_status'] = 'PASS' if result['old_lattice']['geometry_status'] == 'FAIL' else 'FAIL'
    write_json(new/'toe_coverage_report.json', result)
    render_comparison(old/'lattice.stl', new/'lattice.stl', baseline/'base_shoe.stl', new/'toe_comparison.png', result)
    return result


def render_comparison(old_path, new_path, base_path, output, report):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    meshes = [trimesh.load_mesh(path, process=False) for path in (old_path, new_path)]
    base = trimesh.load_mesh(base_path, process=False)
    frame = np.array([base.bounds[0], base.bounds[1]], float)
    frame[0] = np.minimum(frame[0], np.min([mesh.bounds[0] for mesh in meshes], axis=0))
    frame[1] = np.maximum(frame[1], np.max([mesh.bounds[1] for mesh in meshes], axis=0))
    sole_frame = frame.copy()
    sole_frame[1, 2] = max(mesh.bounds[1, 2] for mesh in meshes)+4.
    toe_frame = sole_frame.copy()
    toe_frame[0, 0] = frame[1, 0]-max(5*report['new_lattice']['period_mm'], .23*np.ptp(frame[:, 0]))
    fig, axes = plt.subplots(4, 2, figsize=(16, 17), facecolor='#f3f5f7')
    configurations = [(28, -55, frame, 'Perspective'), (0, -90, sole_frame, 'Side / sole band'),
                      (90, -90, frame, 'Top'), (0, -90, toe_frame, 'Toe detail / side')]
    for row, (elev, azim, bounds, title) in enumerate(configurations):
        ghost = rasterize(base, elev, azim, width=1100, height=500, frame_bounds=bounds)
        ghost = .17*np.repeat(ghost.mean(axis=2)[:, :, None], 3, axis=2)+.83*.97
        for col, (mesh, label) in enumerate(zip(meshes, ('Old planar slab', 'Repaired curved sole'))):
            pixels = rasterize(mesh, elev, azim, width=1100, height=500, frame_bounds=bounds)
            image = ghost.copy()
            visible = np.any(np.abs(pixels-.97) > 1e-7, axis=2)
            image[visible] = pixels[visible]
            axes[row, col].imshow(image)
            axes[row, col].set_title(f'{label} · {title} · shared camera/frame')
            axes[row, col].set_axis_off()
    fig.suptitle('M2 toe repair · unchanged actual STL triangles\nGray: accepted frozen M1 · colored: actual exported lattice · no M3 fusion', fontsize=17)
    fig.text(.03, .015, 'Independent measured native CAD/STL sections: toe_coverage_report.json. Finite probes do not prove global coverage or manufacturing suitability.', fontsize=10)
    fig.tight_layout(rect=(0, .035, 1, .95))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=140)
    plt.close(fig)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, default=Path('output/m2'))
    parser.add_argument('--new', type=Path, default=Path('output/m2_toe_repair'))
    parser.add_argument('--baseline', type=Path, default=Path('output/m1_toe_refine'))
    args = parser.parse_args()
    result = compare(args.old, args.new, args.baseline)
    print(json.dumps({'new_status': result['geometry_status'], 'old_slab_regression': result['old_slab_regression_status'],
                      'old_failed_bins': result['old_lattice']['failed_bin_indices'],
                      'new_failed_bins': result['new_lattice']['failed_bin_indices']}, indent=2))
    if result['geometry_status'] != 'PASS' or result['old_slab_regression_status'] != 'PASS':
        raise SystemExit('Toe coverage comparison failed; inspect toe_coverage_report.json')
