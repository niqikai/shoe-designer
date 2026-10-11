"""Deterministic headless raster preview of the actual exported STL."""
from pathlib import Path
import os

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/shoe-poc-matplotlib" if os.path.exists("/private/tmp") else "/tmp/shoe-poc-matplotlib")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
import trimesh


def render_preview(model: Path, output: Path) -> None:
    mesh = trimesh.load_mesh(model)
    fig = plt.figure(figsize=(16, 10), facecolor="#f3f5f7")
    angles = [(28, -55, "Actual base shoe · perspective"),
              (90, -90, "Top view · entry opening"), (0, -90, "Side view · solid sole")]
    for i, (elev, azim, title) in enumerate(angles, 1):
        ax = fig.add_subplot(2, 2, i, projection="3d")
        surface = Poly3DCollection(mesh.triangles, linewidths=0, alpha=1)
        normals = mesh.face_normals
        lighting = np.clip(0.5 + 0.5 * (normals @ np.array([0.3, -0.5, 0.8])), 0.25, 1)
        surface.set_facecolor(np.column_stack([0.12 * lighting, 0.56 * lighting,
                                              0.66 * lighting, np.ones(len(lighting))]))
        ax.add_collection3d(surface)
        for setter, bounds in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), mesh.bounds.T):
            setter(bounds[0] - 3, bounds[1] + 3)
        ax.set_box_aspect(mesh.extents)
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(title, fontsize=12)
        ax.set_xlabel("X (mm)"); ax.set_ylabel("Y (mm)"); ax.set_zlabel("Z (mm)")
    ax = fig.add_subplot(2, 2, 4)
    y = float(mesh.bounds[:, 1].mean())
    section = mesh.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    if section is not None:
        for points in section.discrete:
            ax.plot(points[:, 0], points[:, 2], color="#148eaa", linewidth=1.4)
    ax.set_aspect("equal"); ax.set_xlabel("X (mm)"); ax.set_ylabel("Z (mm)")
    ax.set_title("Longitudinal section · cavity and wall", fontsize=12)
    ax.grid(alpha=0.2)
    fig.suptitle("Milestone 1 · real STEP last → parametric CAD → STL", fontsize=18)
    fig.text(0.5, 0.015, "Engineering preview only. Orientation and fit require review; no print qualification.", ha="center", fontsize=11)
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    fig.savefig(output, dpi=140)
    plt.close(fig)
