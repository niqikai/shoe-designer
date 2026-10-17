"""manifold3d level_set: cost of the per-point Python callback (candidate B, criterion 2).

    uv run python spikes/m0_6a/b_callback_overhead.py

All variants run the same level_set (sphere bounds 44 mm, edge 0.5 mm) and differ only in
the callback. ``calls`` is counted once with a counting wrapper; the timed runs are
uncounted. ``us_per_call`` is wall time of the whole level_set divided by calls (it
includes the mesh construction, so it is an upper bound of the callback cost).
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numba
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import common
import manifold3d as m3d

from shoegen.evaluate import plan_blocks
from shoegen.graph import compile_graph

c = np.asarray(common.SPHERE_CENTRE)
R = common.SPHERE_R
BOUNDS = [*(c - 22), *(c + 22)]
EDGE = 0.5


def run(name, f, n_calls=None, reps=3):
    if n_calls is None:
        cnt = [0]

        def counting(x, y, z):
            cnt[0] += 1
            return f(x, y, z)

        m3d.Manifold.level_set(counting, BOUNDS, EDGE).num_tri()
        n_calls = cnt[0]
    best = None
    for _ in range(reps):
        t = time.perf_counter()
        M = m3d.Manifold.level_set(f, BOUNDS, EDGE)
        nt = M.num_tri()
        dt = time.perf_counter() - t
        best = dt if best is None else min(best, dt)
    print(
        json.dumps(
            {
                "callback": name,
                "calls": n_calls,
                "triangles": nt,
                "seconds_best_of_3": round(best, 4),
                "us_per_call": round(best / n_calls * 1e6, 3),
                "Mcalls_per_s": round(n_calls / best / 1e6, 2),
            }
        )
    )
    return n_calls


def main():
    # 1. constant callback (everything outside): pure grid-visit + pybind11 call overhead
    run("python lambda, constant -1.0", lambda x, y, z: -1.0)
    # 2. pure-Python analytic sphere
    run(
        "python analytic sphere (math.sqrt)",
        lambda x, y, z: R - math.sqrt((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2),
    )

    # 3. numba @njit scalar analytic sphere
    @numba.njit
    def nsph(x, y, z):
        return R - math.sqrt((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2)

    nsph(0.0, 0.0, 0.0)
    run("numba njit analytic sphere", nsph)

    # 4. numba trilinear lookup into the M0.5 block samples
    sh = common.sphere_shape(1.0)
    plan = plan_blocks(sh.graph, None, sh.region_min, sh.region_max, sh.voxel)
    store, _ = common.build_store(plan, workers=1)
    look = common.make_lookup(store)
    run("numba njit trilinear lookup (M0.5 blocks)", look)

    # 5. the real graph kernel, one point per Python->numba call
    prog = compile_graph(sh.graph)
    buf = np.zeros((1, 3))

    def real(x, y, z):
        buf[0, 0], buf[0, 1], buf[0, 2] = x, y, z
        return -float(prog.eval(buf)[0])

    real(30.0, 30.0, 30.0)
    run("Program.eval on 1 point per call (graph kernel)", real, reps=1)


if __name__ == "__main__":
    main()
