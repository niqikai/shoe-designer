"""Cost of feeding level_set from the real graph kernel (no block samples), lattice block.

    uv run python spikes/m0_6a/b_realkernel_sample.py

Times Program.eval on a single point per Python call (what a per-point callback would do)
at random points in the lattice box, and extrapolates to the number of level_set callbacks.
Light: 20000 calls.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import common

from shoegen.graph import compile_graph

sh = common.lattice_shape()
prog = compile_graph(sh.graph)
x0, x1, y0, y1, z0, z1 = common.LATTICE_BOX
rng = np.random.default_rng(0)
pts = rng.uniform([x0, y0, z0], [x1, y1, z1], size=(20000, 3))
buf = np.zeros((1, 3))
prog.eval(pts[:2])
t = time.perf_counter()
acc = 0.0
for p in pts:
    buf[0] = p
    acc += float(prog.eval(buf)[0])
dt = time.perf_counter() - t
t = time.perf_counter()
prog.eval(pts)
dtb = time.perf_counter() - t
vol = (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)
calls = int(vol * 2 / 0.25**3)
print(
    json.dumps(
        {
            "us_per_call_single_point": round(dt / len(pts) * 1e6, 2),
            "us_per_point_batched_eval": round(dtb / len(pts) * 1e6, 3),
            "est_level_set_callbacks_edge_0.25": calls,
            "extrapolated_minutes_single_point_callback": round(calls * dt / len(pts) / 60, 1),
            "extrapolated_seconds_batched": round(calls * dtb / len(pts), 1),
        }
    )
)
