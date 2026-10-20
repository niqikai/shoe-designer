"""#46 checkpoint 1: how far from conformal is a LSCM (libigl) chart of the same forefoot patch? Probe only, not used by the pipeline.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast python scripts/lattice_lscm_probe.py [--u0 0.45 --u1 0.75]

Mid-surface (phi_C = T/2) of the patch above the sole band, flattened with igl.lscm (two pinned vertices at the far ends of the
patch); per-face singular values of the uv -> world map: anisotropy s1/s2 (1 = conformal) and area scale sqrt(s1 s2).
"""
import argparse
import sys
import time
from pathlib import Path

import igl
import numpy as np
from scipy import ndimage
from skimage import measure

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lattice_variants as LV  # noqa: E402

from shoe_designer import lattice as L  # noqa: E402
from shoe_designer import lattice_conformal as C  # noqa: E402
from shoe_designer import lattice_variants as V  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--u0", type=float, default=0.45)
ap.add_argument("--u1", type=float, default=0.75)
ap.add_argument("--T", type=float, default=3.4)
a = ap.parse_args()
t0 = time.perf_counter()
prep = LV.load_prep(a)
g = prep.grid
sub = prep.c0
verts, faces, _, _ = measure.marching_cubes(sub, level=0.5 * a.T, spacing=(g.h, g.h, g.dz))
verts = verts + np.asarray(g.origin)
ax = prep.axis
u = L.u_of_x(verts[:, 0], ax)
zfl = ndimage.map_coordinates(prep.floor, [(verts[:, 0] - g.origin[0]) / g.h, (verts[:, 1] - g.origin[1]) / g.h], order=1, mode="nearest")
keep_v = (u > a.u0) & (u < a.u1) & (verts[:, 2] > zfl + 10.0)
fk = keep_v[faces].all(axis=1)
f = faces[fk]
used = np.unique(f)
remap = -np.ones(len(verts), int)
remap[used] = np.arange(len(used))
v, f = verts[used], remap[f]
# largest connected component
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

adj = sp.coo_matrix((np.ones(len(f) * 3), (f.ravel(), np.roll(f, -1, axis=1).ravel())), shape=(len(v), len(v)))
nc, lab = connected_components(adj, directed=False)
big = np.argmax(np.bincount(lab))
vk = lab == big
fk = vk[f].all(axis=1)
f = f[fk]
used = np.unique(f)
remap = -np.ones(len(v), int)
remap[used] = np.arange(len(used))
v, f = v[used], remap[f]
import trimesh

m = trimesh.Trimesh(v, f, process=False)
m.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=1)  # weld slivers of the marching-cubes surface (0.1 mm)
m.update_faces(m.nondegenerate_faces(height=0.05))
m.remove_unreferenced_vertices()
v, f = np.asarray(m.vertices), np.asarray(m.faces)
bnd = igl.boundary_loop(f)
print("faces", len(f), "verts", len(v), "boundary loop", len(bnd), "euler", len(v) - len(set(map(tuple, np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)))) + len(f))
# pins: the two boundary vertices farthest apart in x
i0, i1 = bnd[np.argmin(v[bnd, 0])], bnd[np.argmax(v[bnd, 0])]
ar = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
print("degenerate faces (area < 1e-2 mm2)", int((ar < 1e-2).sum()), "min area", float(ar.min()))


def lscm(v, f, pins, pin_uv):
    """Least-squares conformal map (Levy et al.): minimise the Cauchy-Riemann residual of every triangle, two vertices pinned."""
    import scipy.sparse as sp
    from scipy.sparse.linalg import spsolve

    e1, e2 = v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]
    l1 = np.linalg.norm(e1, axis=1)
    x2, y2 = l1, np.zeros(len(f))
    x3 = np.sum(e2 * e1, axis=1) / np.maximum(l1, 1e-12)
    y3 = np.linalg.norm(np.cross(e1, e2), axis=1) / np.maximum(l1, 1e-12)
    X, Y = np.stack([np.zeros(len(f)), x2, x3], 1), np.stack([np.zeros(len(f)), y2, y3], 1)
    A = 0.5 * np.abs(x2 * y3)
    n = len(v)
    rows, cols, vals = [], [], []
    for j in range(3):
        j1, j2 = (j + 1) % 3, (j + 2) % 3
        cr = (Y[:, j1] - Y[:, j2]) / (2 * A) * np.sqrt(A)  # Re c_j (weighted)
        ci = (X[:, j2] - X[:, j1]) / (2 * A) * np.sqrt(A)  # Im c_j
        t = np.arange(len(f))
        # real residual: cr u - ci v ; imaginary: ci u + cr v ; unknown vector [u (n), v (n)]
        rows += [t, t, len(f) + t, len(f) + t]
        cols += [f[:, j], n + f[:, j], f[:, j], n + f[:, j]]
        vals += [cr, -ci, ci, cr]
    M = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(2 * len(f), 2 * n))
    fixed = np.concatenate([pins, n + pins])
    free = np.setdiff1d(np.arange(2 * n), fixed)
    xf = np.concatenate([pin_uv[:, 0], pin_uv[:, 1]])
    rhs = -(M[:, fixed] @ xf)
    Mf = M[:, free]
    sol = spsolve((Mf.T @ Mf).tocsc(), Mf.T @ rhs)
    out = np.zeros(2 * n)
    out[fixed], out[free] = xf, sol
    return np.stack([out[:n], out[n:]], axis=1)


uv = lscm(v, f, np.array([i0, i1]), np.array([[0.0, 0.0], [np.linalg.norm(v[i1] - v[i0]), 0.0]]))
# per-face Jacobian uv -> 3D
e1, e2 = v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]
d1, d2 = uv[f[:, 1]] - uv[f[:, 0]], uv[f[:, 2]] - uv[f[:, 0]]
D = np.stack([d1, d2], axis=-1)  # (nf, 2, 2): columns = uv edges
E = np.stack([e1, e2], axis=-1)  # (nf, 3, 2)
ok = np.abs(np.linalg.det(D)) > 1e-9
M = E[ok] @ np.linalg.inv(D[ok])
sv = np.linalg.svd(M, compute_uv=False)
an = sv[:, 0] / sv[:, 1]
sz = np.sqrt(sv[:, 0] * sv[:, 1])
area = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)[ok]
w = area / area.sum()
print({"anisotropy_median": round(float(np.median(an)), 3), "anisotropy_p95": round(float(np.percentile(an, 95)), 3), "anisotropy_max": round(float(an.max()), 3),
       "size_p05_over_median": round(float(np.percentile(sz, 5) / np.median(sz)), 3), "size_p95_over_median": round(float(np.percentile(sz, 95) / np.median(sz)), 3),
       "size_min_over_median": round(float(sz.min() / np.median(sz)), 3), "uv_extent": (uv.max(0) - uv.min(0)).round(1).tolist(),
       "world_extent": (v.max(0) - v.min(0)).round(1).tolist(), "time_s": round(time.perf_counter() - t0, 1), "rss_mb": round(LV.rss_mb())})
