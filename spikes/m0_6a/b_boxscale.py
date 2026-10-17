"""manifold3d level_set: does cost/memory scale with the surface or with the bounding box?

    uv run python spikes/m0_6a/b_boxscale.py

Analytic numba callback (sphere R=20, edge 0.5 mm) on boxes of growing size; the surface
is the same every time. Each size runs in a fresh subprocess so ru_maxrss is clean.
"""

import subprocess
import sys

CODE = r"""
import json, math, resource, sys, time
import numba, manifold3d as m3d
half = float(sys.argv[1]); e = float(sys.argv[2])
@numba.njit
def f(x, y, z):
    return 20.0 - math.sqrt(x*x + y*y + z*z)
f(0.0, 0.0, 0.0)
t = time.perf_counter()
M = m3d.Manifold.level_set(f, [-half]*3 + [half]*3, e)
nt = M.num_tri()
dt = time.perf_counter() - t
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
print(json.dumps({"half_box_mm": half, "edge_mm": e, "seconds": round(dt, 3),
                  "triangles": nt, "peak_rss_mb": round(rss, 1),
                  "grid_points_est_M": round(2 * (2*half/e)**3 / 1e6, 1)}))
"""
for half in (22, 40, 80, 160):
    out = subprocess.run(
        [sys.executable, "-c", CODE, str(half), "0.5"], capture_output=True, text=True, check=False
    )
    print(out.stdout.strip() or out.stderr.strip()[-300:])
