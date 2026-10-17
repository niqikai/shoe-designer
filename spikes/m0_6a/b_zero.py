"""manifold3d level_set when many samples are exactly 0 (grid-aligned box faces).

    uv run python spikes/m0_6a/b_zero.py

Box [10, 20]^3 (+inside convention: min over faces of the signed distance), level_set grid
points land exactly on the faces, so many callback values are exactly 0.0.
"""

import json
import sys
from pathlib import Path

import numba
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import common
import manifold3d as m3d


@numba.njit
def f(x, y, z):
    return min(min(x - 10.0, 20.0 - x), min(min(y - 10.0, 20.0 - y), min(z - 10.0, 20.0 - z)))


f(0.0, 0.0, 0.0)
cnt = {"zero": 0, "total": 0}


def counting(x, y, z):
    v = f(x, y, z)
    cnt["total"] += 1
    cnt["zero"] += v == 0.0
    return v


for e in (1.0, 0.8):
    cnt.update(zero=0, total=0)
    m3d.Manifold.level_set(counting, [5.0] * 3 + [25.0] * 3, e).num_tri()
    M = m3d.Manifold.level_set(f, [5.0] * 3 + [25.0] * 3, e)
    mesh = M.to_mesh64()
    v = np.asarray(mesh.vert_properties, np.float64)[:, :3]
    t = np.asarray(mesh.tri_verts, np.int64)
    r = common.mesh_report(v, t)
    print(
        json.dumps(
            {
                "edge": e,
                "calls": cnt["total"],
                "calls_with_value_exactly_0": cnt["zero"],
                "status": str(M.status()),
                "triangles": r["triangles"],
                "closed_oriented_manifold": r["closed_oriented_manifold"],
                "degenerate_zero_area": r["degenerate_tris_zero_area"],
                "volume": r["volume_mm3"],
                "expected": 1000.0,
                "euler": r["euler_characteristic"],
                "bbox": [r["bbox_min"], r["bbox_max"]],
            }
        )
    )
