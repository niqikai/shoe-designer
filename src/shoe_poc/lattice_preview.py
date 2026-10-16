"""Engineering views of actual M2 STL with the frozen accepted M1 STL."""
from pathlib import Path

import numpy as np
import trimesh

from shoe_poc.preview import rasterize
import matplotlib.pyplot as plt


def render_lattice_preview(lattice_model: Path, base_model: Path, output: Path) -> None:
    """Reimport both meshes and render unchanged triangles in real coordinates.

    Transparency is an annotated display overlay only. No resampling, smoothing,
    mesh generation, or modification of the accepted base occurs here.
    """
    lattice = trimesh.load_mesh(lattice_model, process=False)
    base = trimesh.load_mesh(base_model, process=False)
    if not isinstance(lattice, trimesh.Trimesh) or not isinstance(base, trimesh.Trimesh):
        raise ValueError("Preview requires nonempty triangle STL meshes")
    if not len(lattice.faces) or not len(base.faces):
        raise ValueError("Preview requires nonempty triangle STL meshes")
    frame = np.stack([np.minimum(base.bounds[0], lattice.bounds[0]),
                      np.maximum(base.bounds[1], lattice.bounds[1])])
    fig, axes = plt.subplots(3, 2, figsize=(16, 14), facecolor="#f3f5f7")
    base_pixels = rasterize(base, 28, -55, width=1000, height=520, frame_bounds=frame)
    lattice_pixels = rasterize(lattice, 28, -55, width=1000, height=520, frame_bounds=frame)
    gray = np.repeat(base_pixels.mean(axis=2)[:, :, None], 3, axis=2)
    overlay = .23*gray+.77*.97
    visible = np.any(np.abs(lattice_pixels-.97) > 1e-7, axis=2)
    overlay[visible] = lattice_pixels[visible]
    axes[0, 0].imshow(overlay)
    axes[0, 0].set_title("Accepted M1 actual STL (23% gray) + M2 lattice · same coordinates")
    axes[0, 0].set_axis_off()
    for axis, elevation, azimuth, title in [
            (axes[0, 1], 28, -55, "Actual lattice STL · perspective"),
            (axes[1, 0], 0, -90, "Actual lattice STL · side"),
            (axes[1, 1], 90, -90, "Actual lattice STL · top")]:
        axis.imshow(rasterize(lattice, elevation, azimuth, width=1000, height=520))
        axis.set_title(title)
        axis.set_axis_off()
    y = float(lattice.bounds[:, 1].mean())
    section_axis = axes[2, 0]
    for mesh, color, label in [(base, "#757c86", "Accepted M1 STL"),
                                (lattice, "#148eaa", "M2 lattice STL")]:
        # STL corner welding is only needed to join section line segments.
        section_mesh = mesh.copy()
        section_mesh.merge_vertices(digits_vertex=12)
        section = section_mesh.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
        if section is not None:
            for index, points in enumerate(section.discrete):
                section_axis.plot(points[:, 0], points[:, 2], color=color,
                                  linewidth=1., label=label if index == 0 else None)
    section_axis.set_aspect("equal")
    section_axis.set_xlabel("X (mm)")
    section_axis.set_ylabel("Z (mm)")
    section_axis.set_title(f"Actual longitudinal intersections · Y={y:.3f} mm")
    # Focus on sole so individual gyroid sections can be reviewed; base upper
    # remains visible in the full-model panel above.
    section_axis.set_ylim(lattice.bounds[0, 2]-3, lattice.bounds[1, 2]+6)
    section_axis.grid(alpha=.2)
    section_axis.legend(fontsize=9)
    detail = axes[2, 1]
    detail.set_axis_off()
    dimensions = " × ".join(f"{value:.3f}" for value in lattice.extents)
    detail.text(.03, .88, "M2 lattice · geometry review", fontsize=17, weight="bold")
    lines = [f"Lattice extents: {dimensions} mm",
             f"STL triangles: {len(lattice.faces):,}",
             f"Minimum XYZ (mm): {np.round(lattice.bounds[0], 3).tolist()}",
             f"Maximum XYZ (mm): {np.round(lattice.bounds[1], 3).tolist()}",
             "All views use the actual exported STL triangles.",
             "Flat face shading; no smoothing or synthetic shoe imagery.",
             "M1 accepted base source is read only.",
             "Lattice-to-shell fusion belongs to M3.",
             "Physical connectivity / wall thickness / printability: NOT VERIFIED."]
    detail.text(.03, .70, "\n\n".join(lines), fontsize=10, va="top")
    fig.suptitle("Milestone 2 · real-coordinate Gyroid STL", fontsize=20)
    fig.tight_layout(rect=(0, .01, 1, .96))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=135)
    plt.close(fig)


def render_parameter_sections(directory: Path, output: Path) -> None:
    """Shared-plane sections from the actual period/threshold variant STLs."""
    import json
    primary = trimesh.load_mesh(directory/'lattice.stl', process=False)
    primary.merge_vertices(digits_vertex=12)
    metadata = json.loads((directory/'lattice_metadata.json').read_text())
    entries = [(primary, metadata['actual_lattice_parameters'], metadata['meshing'].get('level', 0.))]
    controls = json.loads((directory/'parameter_change_report.json').read_text())['runs']
    threshold_name, = [name for name in controls if name.startswith('threshold_')]
    for name in ('period_16', threshold_name):
        path = directory/'variants'/name
        mesh = trimesh.load_mesh(path/'lattice.stl', process=False)
        mesh.merge_vertices(digits_vertex=12)
        report = json.loads((path/'verification_report.json').read_text())
        entries.append((mesh, report['settings'], report['meshing'].get('level', 0.)))
    z = float(primary.bounds[:, 2].mean())
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), facecolor='#f3f5f7', sharex=True, sharey=True)
    for axis, (mesh, settings, actual_level) in zip(axes, entries):
        section = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
        if section is None:
            raise ValueError('Actual lattice has no intersection at parameter review plane')
        for points in section.discrete:
            axis.plot(points[:, 0], points[:, 1], color='#148eaa', linewidth=.8)
        axis.set_aspect('equal')
        axis.set_xlim(primary.bounds[0, 0]-3, primary.bounds[1, 0]+3)
        axis.set_ylim(primary.bounds[0, 1]-3, primary.bounds[1, 1]+3)
        axis.set_xlabel('X (mm)'); axis.set_ylabel('Y (mm)')
        title = f"Period {settings['period_mm']} mm · requested threshold {settings['threshold']}"
        if actual_level:
            title += f"\nEffective threshold {settings['threshold']+actual_level:.8f} · level {actual_level:.2e}"
        axis.set_title(title)
        axis.grid(alpha=.2)
    fig.suptitle(f'Actual STL parameter sections · same plane Z={z:.3f} mm · threshold is dimensionless', fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, .94))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=140)
    plt.close(fig)
