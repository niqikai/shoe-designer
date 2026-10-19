"""Closure off vs on, side by side (outputs of build_real.py).

    uv run --with matplotlib python scripts/plot_compare.py OFF_DIR ON_DIR OUT.png

Rows: off, on. Columns: midline xz, side xz (y = centroid + 15), cross sections at x/L = 0.55, 0.70, 0.85.
Template envelope in grey, shoe in red (off) / blue (on). Plots only; derived from non-redistributable meshes.
"""
import sys
from pathlib import Path

import matplotlib
import trimesh

matplotlib.use("Agg")
import matplotlib.pyplot as plt

dirs = [Path(sys.argv[1]), Path(sys.argv[2])]
names, colors = ["closure off", "closure on (43 deg)"], ["tab:red", "tab:blue"]
meshes = [{n: trimesh.load(d / f"{n}.stl") for n in ("envelope", "shoe")} for d in dirs]
b = meshes[0]["shoe"].bounds
L = b[1, 0] - b[0, 0]
yc = meshes[0]["shoe"].centroid[1]
cols = [("midline xz (y=0)", [0, 0, 0], [0, 1, 0], (0, 2)), (f"side xz (y={yc + 15:.0f})", [0, yc + 15, 0], [0, 1, 0], (0, 2))]
cols += [(f"x/L = {f}", [b[0, 0] + f * L, 0, 0], [1, 0, 0], (1, 2)) for f in (0.55, 0.70, 0.85)]
fig, axs = plt.subplots(2, len(cols), figsize=(5 * len(cols), 9))
for r in range(2):
    for c, (title, o, n, ax_) in enumerate(cols):
        ax = axs[r, c]
        for key, col, lw in (("envelope", "0.55", 0.9), ("shoe", colors[r], 1.3)):
            s = meshes[r][key].section(plane_origin=o, plane_normal=n)
            if s is None:
                continue
            for e in s.discrete:
                ax.plot(e[:, ax_[0]], e[:, ax_[1]], color=col, lw=lw)
        ax.set_aspect("equal")
        ax.set_title(f"{names[r]}: {title}", fontsize=9)
fig.tight_layout()
fig.savefig(sys.argv[3], dpi=90)
