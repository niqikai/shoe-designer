"""Flat-shaded engineering views from a strictly reimported assembly STL."""
from pathlib import Path

import numpy as np
import trimesh

from shoe_poc.preview import rasterize
from shoe_poc.reporting import sha256, write_json
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection


def render_assembly_preview(mesh, output: Path, *, source_stl: Path, base_mesh=None) -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    frame = mesh.bounds
    fig, axes = plt.subplots(2, 2, figsize=(16, 11), facecolor='#f3f5f7')
    for ax, elev, azim, label in [
            (axes[0, 0], 28, -55, 'Assembled shoe · perspective'),
            (axes[0, 1], 90, -90, 'Assembled shoe · top'),
            (axes[1, 0], 0, -90, 'Assembled shoe · side')]:
        ax.imshow(rasterize(mesh, elev, azim, width=1000, height=540, frame_bounds=frame))
        ax.set_title(label)
        ax.set_axis_off()
    # This is an explicitly open display cutaway, never a manufacturing mesh.
    middle_y = float(frame[:, 1].mean())
    selected = mesh.triangles_center[:, 1] >= middle_y
    cutaway = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces[selected], process=False)
    axes[1, 1].imshow(rasterize(cutaway, 15, -85, width=1000, height=540, frame_bounds=frame))
    axes[1, 1].set_title(f'Display cutaway · triangles with centroid Y >= {middle_y:.3f} mm')
    axes[1, 1].set_axis_off()
    fig.suptitle('M3 · actual STL reimport · flat face shading', fontsize=18)
    fig.text(.5, .012, 'Cutaway is display-only. No smoothing. Manufacturing and physical tests are recorded separately.',
             ha='center', fontsize=10)
    fig.tight_layout(rect=(0, .025, 1, .95))
    fig.savefig(output/'preview.png', dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(15, 10), facecolor='#f3f5f7')
    cross_x = float(frame[0, 0] + .72 * np.ptp(frame[:, 0]))
    for ax, origin, normal, coordinates, title in [
            (axes[0], [0, middle_y, 0], [0, 1, 0], [0, 2], f'Longitudinal section Y={middle_y:.3f} mm'),
            (axes[1], [cross_x, 0, 0], [1, 0, 0], [1, 2], f'Transverse section X={cross_x:.3f} mm')]:
        entries = [(mesh, '#078fa6', 'M3 actual STL')]
        if base_mesh is not None:
            entries.insert(0, (base_mesh, '#a2a8ad', 'Frozen M1 STL'))
        for current, color, label in entries:
            lines = trimesh.intersections.mesh_plane(current, plane_normal=normal, plane_origin=origin)
            if len(lines):
                ax.add_collection(LineCollection(lines[:, :, coordinates], colors=color, linewidths=.7, label=label))
        ax.autoscale()
        ax.set_aspect('equal')
        ax.set_title(title)
        ax.set_xlabel('X (mm)' if coordinates[0] == 0 else 'Y (mm)')
        ax.set_ylabel('Z (mm)')
        ax.grid(alpha=.2)
        ax.legend()
    fig.suptitle('M3 · unchanged triangle / plane intersections', fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, .95))
    fig.savefig(output/'sections.png', dpi=140)
    plt.close(fig)
    report = {'status': 'PASS', 'source_stl': str(Path(source_stl).resolve()), 'source_sha256': sha256(source_stl),
        'triangles': len(mesh.faces), 'vertices': len(mesh.vertices), 'bounds_mm': mesh.bounds.tolist(),
        'display_policy': 'Actual strictly reimported STL; flat shading; no smoothing or mesh repair',
        'cutaway_policy': 'Display-only centroid half-space triangle selection; not a closed/exported engineering mesh',
        'cutaway_y_mm': middle_y, 'section_x_mm': cross_x,
        'images': {name: sha256(output/name) for name in ('preview.png', 'sections.png')}}
    write_json(output/'preview_report.json', report)
    return report
