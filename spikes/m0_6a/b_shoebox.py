"""manifold3d level_set over a shoe-sized bounding box with a small surface inside.

    bash-lock;  uv run python spikes/m0_6a/b_shoebox.py --edge 0.25 --watchdog-gb 6

Box 300 x 120 x 150 mm (order of a shoe plus margin), analytic numba sphere R = 20 mm as
the only surface. Shows that level_set time/memory follow the box volume, not the surface.
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numba

sys.path.insert(0, str(Path(__file__).parent))
import common
import manifold3d as m3d

from shoegen.resources import RssWatchdog

ap = argparse.ArgumentParser()
ap.add_argument("--edge", type=float, required=True)
ap.add_argument("--watchdog-gb", type=float, default=6.0)
a = ap.parse_args()


@numba.njit
def f(x, y, z):
    return 20.0 - math.sqrt(x * x + y * y + z * z)


f(0.0, 0.0, 0.0)
size = (300.0, 120.0, 150.0)
bounds = [-size[0] / 2, -size[1] / 2, -size[2] / 2, size[0] / 2, size[1] / 2, size[2] / 2]
with RssWatchdog(int(a.watchdog_gb * 2**30), interval_s=0.1) as wd:
    base = common.current_rss_mb()
    t = time.perf_counter()
    M = m3d.Manifold.level_set(f, bounds, a.edge)
    nt = M.num_tri()
    dt = time.perf_counter() - t
    peak = wd.peak_bytes / 2**20
    maxrss = common.peak_rss_mb()
print(
    json.dumps(
        {
            "box_mm": size,
            "edge_mm": a.edge,
            "seconds": round(dt, 2),
            "triangles": nt,
            "grid_points_est_M": round(2 * math.prod(s / a.edge for s in size) / 1e6, 1),
            "rss_before_mb": round(base, 1),
            "watchdog_peak_mb(starved)": round(peak, 1),
            "maxrss_after_mb": round(maxrss, 1),
            "dense_float32_voxels_at_this_edge_GB": round(
                math.prod(s / a.edge for s in size) * 4 / 2**30, 2
            ),
        }
    )
)
