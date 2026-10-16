"""Actual STL principal components, detached surfaces and native CAD tip probes."""
from argparse import ArgumentParser
import gc
import json
from pathlib import Path

import numpy as np
import trimesh

from shoe_poc.preview import rasterize
from shoe_poc.reporting import sha256, utc_now, write_json
from shoe_poc.lattice.pipeline import accepted_baseline
from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.lattice_terminal import principal_mesh_component


def render(old, new, baseline, expected_path, output):
    import matplotlib.pyplot as plt
    execution = json.loads((new/'execution_report.json').read_text())
    bound = accepted_baseline(baseline, ShoeSpec.load(new/'shoe_spec.json'),
                              Path(execution['baseline']['acceptance_file']))
    if bound != execution['baseline']:
        raise ValueError('Preview baseline differs from the completed build')
    inputs = {str(path.resolve()): sha256(path) for path in
              (old/'lattice.stl', new/'lattice.stl', baseline/'base_shoe.stl', expected_path)}
    base = trimesh.load_mesh(baseline/'base_shoe.stl', process=False)
    expected = json.loads(expected_path.read_text())
    frame = base.bounds.copy()
    frame[1, 2] = max(17., float(np.max(np.asarray(expected['envelope']['upper_points_xz_mm'])[:, 1])))+3
    detail = frame.copy()
    detail[0, 0] = frame[1, 0]-26
    detail[0, 2] = -7
    detail[1, 2] = 20
    configurations = [(24, -58, base.bounds, 'Perspective'), (90, -90, frame, 'Top'),
                      (0, -90, detail, 'Toe detail')]
    ghosts = []
    for elev, azim, bounds, _ in configurations:
        gray = rasterize(base, elev, azim, width=1050, height=430, frame_bounds=bounds)
        ghosts.append(.18*np.repeat(gray.mean(axis=2)[:, :, None], 3, axis=2)+.82*.97)
    fig, axes = plt.subplots(4, 2, figsize=(16, 18), facecolor='#f3f5f7')
    q = expected['clearance_mm']+np.sqrt(3)*expected['spacing_mm']+8e-6+.1
    points = np.array([row['point_mm'] for row in expected['nodes']])
    # The middle lane remains near the forefoot center; choose an actually
    # measured Y coordinate rather than drawing an invented reference section.
    values = np.unique(points[:, 1])
    y = float(values[np.argmin(abs(values-np.median(points[:, 1])))])
    statistics = []
    for column, directory in enumerate((old, new)):
        mesh, principal_faces, measured = principal_mesh_component(directory/'lattice.stl')
        statistics.append(measured)
        colors = np.tile([.94, .39, .10], (len(mesh.faces), 1))
        colors[principal_faces] = [.15, .64, .73]
        label = 'Previous repair' if column == 0 else 'Adaptive CAD completion'
        for row, ((elev, azim, bounds, title), ghost) in enumerate(zip(configurations, ghosts)):
            pixels = rasterize(mesh, elev, azim, width=1050, height=430,
                               frame_bounds=bounds, face_colors=colors)
            image = ghost.copy()
            visible = np.any(abs(pixels-.97) > 1e-7, axis=2)
            image[visible] = pixels[visible]
            axes[row, column].imshow(image)
            axes[row, column].set_title(f'{label} · {title}\nPrincipal X max {measured["principal_bounds_mm"][1][0]:.3f} mm')
            axes[row, column].set_axis_off()
        axis = axes[3, column]
        main_lines = trimesh.intersections.mesh_plane(mesh, [0, 1, 0], [0, y, 0], local_faces=principal_faces)
        base_lines = trimesh.intersections.mesh_plane(base, [0, 1, 0], [0, y, 0])
        for segments, color, name in ((base_lines, '#8a9098', 'Frozen M1 actual section'),
                                     (main_lines, '#148eaa', 'Principal STL actual section')):
            from matplotlib.collections import LineCollection
            axis.add_collection(LineCollection(segments[:, :, [0, 2]], colors=color, linewidths=.9, label=name))
        for eligible, color, marker, name in ((True, '#27a849', 'o', 'CAD domain eligible (can be gyroid void)'),
                                              (False, '#bc4a49', 'x', 'Inside CAD but insufficient reserved space')):
            selected = [row['point_mm'] for row in expected['nodes'] if abs(row['point_mm'][1]-y) < 1e-7
                and row['inside_intended_material']
                and ((row['minimum_boundary_distance_mm'] > q) == eligible)]
            if selected:
                selected = np.asarray(selected)
                axis.scatter(selected[:, 0], selected[:, 2], c=color, marker=marker, s=14, label=name)
        axis.set_xlim(detail[0, 0], detail[1, 0]+.5)
        axis.set_ylim(detail[0, 2], detail[1, 2])
        axis.set_aspect('equal'); axis.grid(alpha=.2)
        axis.set_xlabel('X mm'); axis.set_ylabel('Z mm')
        axis.set_title(f'Actual section Y={y:.3f} mm and independent CAD probes')
        axis.legend(fontsize=7, loc='upper left')
        del mesh, colors, principal_faces
        gc.collect()
    fig.suptitle('M2 toe completion · actual exported triangles, shared coordinates\nBlue: principal connected surface · orange: other surfaces · gray: frozen M1', fontsize=16)
    fig.text(.025, .015, 'No components deleted or smoothed. Green dots test domain eligibility, not solid gyroid occupancy. The physical 2 mm reserve and voxel guard remain unchanged.', fontsize=9)
    fig.tight_layout(rect=(0, .035, 1, .94))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=140); plt.close(fig)
    if any(sha256(Path(name)) != digest for name, digest in inputs.items()):
        raise ValueError('A preview input changed while rendering')
    write_json(new/'terminal_preview_report.json', {
        'generated_at': utc_now(), 'run_id': execution['run_id'],
        'new_lattice_sha256': inputs[str((new/'lattice.stl').resolve())],
        'previous_lattice_sha256': inputs[str((old/'lattice.stl').resolve())],
        'image_sha256': sha256(output), 'image': str(output.resolve()),
        'inputs': inputs, 'baseline': bound, 'mesh_components': statistics,
        'components_deleted': 0, 'mesh_smoothing': False,
        'geometry_source': 'Reimported actual STL triangles and actual mesh-plane intersections',
        'shared_frame': True,
        'visual_acceptance': 'PENDING_USER_REVIEW'})
    return statistics


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, default=Path('output/m2_toe_repair'))
    parser.add_argument('--new', type=Path, default=Path('output/m2_toe_complete'))
    parser.add_argument('--baseline', type=Path, default=Path('output/m1_toe_refine'))
    parser.add_argument('--expected', type=Path, default=Path('output/m2_toe_complete/debug/terminal_expectations.json'))
    parser.add_argument('--output', type=Path, default=Path('output/m2_toe_complete/terminal_comparison.png'))
    args = parser.parse_args()
    render(args.old, args.new, args.baseline, args.expected, args.output)
