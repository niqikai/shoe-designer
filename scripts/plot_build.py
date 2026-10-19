"""Side view / sections of the composed shoe against the template envelope and the closed cavity.

    uv run --with matplotlib python scripts/plot_build.py OUT_DIR OUT.png

OUT_DIR holds shoe.stl, envelope.stl and cavity_closed.stl from build_real.py. Plots only: no
asset data is stored, but the picture is derived from non-redistributable meshes; keep it in reports/.
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import trimesh

matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = Path(sys.argv[1])
meshes = {n: trimesh.load(d / f"{n}.stl") for n in ("envelope", "shoe", "cavity_closed")}
style = {"envelope": ("0.55", 0.9, "template envelope"), "shoe": ("tab:red", 1.4, "shoe"), "cavity_closed": ("tab:blue", 0.9, "cavity phi_C*")}


def section(ax, origin, normal, axes):
    for n, m in meshes.items():
        s = m.section(plane_origin=origin, plane_normal=normal)
        if s is None:
            continue
        c, w, lab = style[n]
        for i, e in enumerate(s.discrete):
            ax.plot(e[:, axes[0]], e[:, axes[1]], color=c, lw=w, label=lab if i == 0 else None)
    ax.set_aspect("equal")
    ax.legend(loc="upper left", fontsize=7)


b = meshes["shoe"].bounds
L = b[1, 0] - b[0, 0]
fig, axs = plt.subplots(3, 3, figsize=(20, 14))
section(axs[0, 0], [0, 0, 0], [0, 1, 0], (0, 2))
axs[0, 0].set_title("midline xz (y = 0)")
y_side = meshes["shoe"].centroid[1] + 15
section(axs[0, 1], [0, y_side, 0], [0, 1, 0], (0, 2))
axs[0, 1].set_title(f"side xz (y = {y_side:.0f})")
# shaded side view
ax = axs[0, 2]
m = meshes["shoe"]
tri = m.triangles_center
vis = m.face_normals[:, 1] < -0.0  # faces looking toward -y (camera at -y)
shade = np.clip(-m.face_normals[:, 1], 0, 1) * 0.7 + 0.3
ax.tripcolor(m.vertices[:, 0], m.vertices[:, 2], m.faces[vis], facecolors=shade[vis], cmap="gray", vmin=0, vmax=1)
ax.set_aspect("equal")
ax.set_title("side view (from -y)")
for ax, f in zip(axs.ravel()[3:], (0.05, 0.30, 0.55, 0.70, 0.85, 0.95)):
    x = b[0, 0] + f * L
    section(ax, [x, 0, 0], [1, 0, 0], (1, 2))
    ax.set_title(f"x/L = {f}")
fig.tight_layout()
fig.savefig(sys.argv[2], dpi=100)
