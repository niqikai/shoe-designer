"""Sections of the stand-in cavity before/after the closure (output of closure_real.py).

    uv run --with matplotlib python scripts/plot_closure.py OUT_DIR OUT.png
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import trimesh

matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = Path(sys.argv[1])
meshes = {n: trimesh.load(d / f"{n}.stl") for n in ("envelope", "cavity", "cavity_closed")}
style = {"envelope": ("0.6", 1.0, "envelope"), "cavity": ("tab:blue", 1.2, "phi_C"), "cavity_closed": ("tab:red", 1.2, "phi_C*")}


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


b = meshes["cavity_closed"].bounds
L = b[1, 0] - b[0, 0]
fig, axs = plt.subplots(2, 3, figsize=(18, 9))
section(axs[0, 0], [0, np.mean(b[:, 1]), 0], [0, 1, 0], (0, 2))
axs[0, 0].set_title("midline xz")
for ax, f in zip(axs.ravel()[1:], (0.55, 0.70, 0.80, 0.90, 0.95)):
    x = b[0, 0] + f * L
    section(ax, [x, 0, 0], [1, 0, 0], (1, 2))
    ax.set_title(f"x/L = {f}")
fig.tight_layout()
fig.savefig(sys.argv[2], dpi=110)
