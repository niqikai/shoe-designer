"""Reference volume of the 80 x 80 x 40 mm lattice block by midpoint quadrature.

    uv run python spikes/m0_6a/lattice_truth.py

The BCC lattice is periodic with the 10 mm cell and the clip box is a whole number of
cells (8 x 8 x 4) shifted by 0.07 mm, so volume = 256 x (volume of one periodic cell).
One cell is integrated with the real graph kernel on a midpoint grid (fine and coarse, to
show convergence).
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from shoegen.graph import GraphBuilder, StrutLattice, compile_graph

b = GraphBuilder()
g = b.build(b.add(StrutLattice(cell_mm=10.0, radius_mm=1.0, origin=(0.0, 0.0, 0.0)), "lat"))
prog = compile_graph(g)
res = {}
for h in (0.1, 0.05, 0.025):
    n = round(10.0 / h)
    ax = (np.arange(n) + 0.5) * h
    inside = 0
    for i in range(n):  # one x-slab at a time
        yy, zz = np.meshgrid(ax, ax, indexing="ij")
        pts = np.column_stack([np.full(yy.size, ax[i]), yy.ravel(), zz.ravel()])
        inside += int(np.count_nonzero(prog.eval(pts) < 0.0))
    v_cell = inside * h**3
    res[str(h)] = {
        "cell_volume_mm3": v_cell,
        "block_volume_mm3": 256 * v_cell,
        "relative_density": v_cell / 1000.0,
    }
print(json.dumps(res))
