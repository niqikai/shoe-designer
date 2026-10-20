"""LSCM chart of the knit upper (#46 part A): a conformal flattening of the mid-surface of the shell.

The mid-surface ``phi_C = T/2`` between the sole band and the collar rim is meshed (marching cubes on the cavity field, cut by the
band, decimated to ~``target_faces`` triangles: the decimation removes the sliver triangles marching cubes leaves, which break
LSCM), cut open along a seam strip at the heel centre (the strip stays solid in the build: it is the "solid strip" that covers the
seam), and flattened with a least-squares conformal map (Levy et al.; two pins at the heel-most and toe-most boundary vertices).
Each mesh vertex carries ``uv`` (mm, scaled so the median conformal factor is 1) and the 2x3 Jacobian ``d(uv)/d(world)``; a world
point near the surface gets ``uv`` and ``J`` from the weighted affine predictions of its nearest vertices. The pattern (flow field,
Voronoi cells) lives in uv; the rod distance is converted to world units by ``J`` and the exact tube formula of ``lattice_upper``.

Reported: conformal distortion (anisotropy s1/s2 per face, area factor sqrt(s1 s2) spread), mesh topology, coverage (points of the
shell zone farther than ``reach_mm`` from the mesh get no pattern: they stay solid and are counted).
"""

from __future__ import annotations

import math
import time

import numpy as np
import scipy.sparse as sp
from scipy import ndimage
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import spsolve
from scipy.spatial import cKDTree

from .lattice import LatticeError, u_of_x


def _compact(v, f):
    u = np.unique(f)
    r = -np.ones(len(v), int)
    r[u] = np.arange(len(u))
    return v[u], r[f]


def _largest(v, f):
    adj = sp.coo_matrix((np.ones(f.size), (f.ravel(), np.roll(f, -1, 1).ravel())), shape=(len(v), len(v)))
    _, lab = connected_components(adj, directed=False)
    b = np.argmax(np.bincount(lab[f[:, 0]]))
    return _compact(v, f[lab[f[:, 0]] == b])


def _euler(v, f):
    e = np.unique(np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), 1), axis=0)
    return len(v) - len(e) + len(f)


def _manifold_cleanup(v, f, max_iter: int = 10):
    """Drop the faces at non-manifold (bow-tie) vertices of the masked mesh: they make igl.decimate return an empty mesh."""
    for _ in range(max_iter):
        e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), 1)
        key = e[:, 0].astype(np.int64) * len(v) + e[:, 1]
        uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        bnd_edge = cnt[inv] == 1
        bcount = np.zeros(len(v), int)
        np.add.at(bcount, e[bnd_edge, 0], 1)
        np.add.at(bcount, e[bnd_edge, 1], 1)
        nonman_edge = cnt[inv] > 2
        bad = (bcount > 2)
        bad[e[nonman_edge].ravel()] = True
        if not bad.any():
            break
        f = f[~bad[f].any(1)]
        v, f = _compact(v, f)
        v, f = _largest(v, f)
    return v, f


def lscm(v, f, pins, pin_uv):
    """Least-squares conformal map: minimise the Cauchy-Riemann residual of every triangle, two vertices pinned."""
    e1, e2 = v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]
    ar = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)
    f = f[ar > 1e-3]  # sliver triangles carry no information (and a zero edge makes the local frame singular)
    e1, e2 = v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]
    l1 = np.linalg.norm(e1, axis=1)
    x3 = np.sum(e2 * e1, axis=1) / l1
    y3 = np.linalg.norm(np.cross(e1, e2), axis=1) / l1
    X, Y = np.stack([np.zeros(len(f)), l1, x3], 1), np.stack([np.zeros(len(f)), np.zeros(len(f)), y3], 1)
    A = 0.5 * l1 * y3
    n = len(v)
    rows, cols, vals = [], [], []
    t = np.arange(len(f))
    for j in range(3):
        j1, j2 = (j + 1) % 3, (j + 2) % 3
        cr = (Y[:, j1] - Y[:, j2]) / (2 * A) * np.sqrt(A)
        ci = (X[:, j2] - X[:, j1]) / (2 * A) * np.sqrt(A)
        rows += [t, t, len(f) + t, len(f) + t]
        cols += [f[:, j], n + f[:, j], f[:, j], n + f[:, j]]
        vals += [cr, -ci, ci, cr]
    M = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(2 * len(f), 2 * n))
    fixed = np.concatenate([pins, n + pins])
    used = np.zeros(n, bool)
    used[f.ravel()] = True
    unused = np.flatnonzero(~used)
    free = np.setdiff1d(np.arange(2 * n), np.concatenate([fixed, unused, n + unused]))
    xf = np.concatenate([pin_uv[:, 0], pin_uv[:, 1]])
    Mf = M[:, free]
    N = (Mf.T @ Mf).tocsc()
    sol = spsolve(N, Mf.T @ (-(M[:, fixed] @ xf)))
    out = np.zeros(2 * n)
    out[fixed], out[free] = xf, sol
    uv = np.stack([out[:n], out[n:]], axis=1)
    if len(unused):  # vertices that only touch sliver triangles: copy the nearest vertex
        _, j = cKDTree(v[used]).query(v[unused])
        uv[unused] = uv[np.flatnonzero(used)[j]]
    return uv


class LscmChart:
    """uv(world) and d(uv)/d(world) on the shell zone of the upper."""

    def __init__(self, v, f, uv, report):
        self.v, self.f, self.uv, self.report = v, f, uv, report
        e1, e2 = v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]
        n = np.cross(e1, e2)
        area = 0.5 * np.linalg.norm(n, axis=1)
        nrm = n / np.maximum(2 * area, 1e-12)[:, None]
        # J = d(uv)/d(world) per face: J [e1 e2 n] = [d1 d2 0]
        d1, d2 = uv[f[:, 1]] - uv[f[:, 0]], uv[f[:, 2]] - uv[f[:, 0]]
        Ec = np.stack([e1, e2, nrm], axis=-1)  # (nf, 3, 3), columns
        Dc = np.stack([d1, d2, np.zeros_like(d1)], axis=-1)  # (nf, 2, 3), columns
        Jf = Dc @ np.linalg.inv(Ec)  # (nf, 2, 3)
        # per-vertex average (area weighted)
        w = np.zeros(len(v))
        Jv = np.zeros((len(v), 2, 3))
        for k in range(3):
            np.add.at(w, f[:, k], area)
            np.add.at(Jv, f[:, k], Jf * area[:, None, None])
        self.J = Jv / np.maximum(w, 1e-12)[:, None, None]
        self.tree = cKDTree(v)
        # conformal factor (world mm per uv unit) at the vertices, area weighted
        sv = face_singular_values(self)
        sz = np.sqrt(sv[:, 0] * sv[:, 1])
        lam = np.zeros(len(v))
        for k in range(3):
            np.add.at(lam, f[:, k], sz * area)
        self.lam = lam / np.maximum(w, 1e-12)

    def eval(self, pts: np.ndarray, k: int = 4):
        """(uv (N, 2), J (N, 2, 3), distance to the nearest vertex (N,)): inverse-distance blend of the affine predictions."""
        dist, idx = self.tree.query(pts, k=k)
        w = 1.0 / np.maximum(dist, 0.05) ** 2
        w /= w.sum(axis=1, keepdims=True)
        uvs = np.zeros((len(pts), 2))
        Js = np.zeros((len(pts), 2, 3))
        for j in range(k):
            dv = pts - self.v[idx[:, j]]
            uvs += w[:, j, None] * (self.uv[idx[:, j]] + np.einsum("nij,nj->ni", self.J[idx[:, j]], dv))
            Js += w[:, j, None, None] * self.J[idx[:, j]]
        return uvs, Js, dist[:, 0]


def build_chart(prep, aux: dict, rim_mm: float, level: float, floor_margin_mm: float = 1.0, target_faces: int = 40000,
                seam_half_mm: float = 3.0, seam_x_frac: float = 0.30, frame: dict | None = None, pin_fracs=(0.2, 0.8), field: np.ndarray | None = None) -> LscmChart:
    """Mesh the mid-surface of the shell zone, cut the heel seam, decimate, flatten. Raises LatticeError if the mesh is not one disk."""
    import igl
    from skimage import measure

    t0 = time.perf_counter()
    g = prep.grid
    verts, faces, _, _ = measure.marching_cubes(prep.c0 if field is None else field, level=level, spacing=(g.h, g.h, g.dz))
    verts = verts + np.asarray(g.origin)
    ci = [(verts[:, 0] - g.origin[0]) / g.h, (verts[:, 1] - g.origin[1]) / g.h]
    zf = ndimage.map_coordinates(aux["floor"], ci, order=1, mode="nearest")
    zt = ndimage.map_coordinates(aux["zt_var"], ci, order=1, mode="nearest")
    keep = (verts[:, 2] > zf + floor_margin_mm) & (verts[:, 2] < zt - rim_mm + 1.0)
    v, f = _compact(verts, faces[keep[faces].all(1)])
    v, f = _largest(v, f)
    v, f = _manifold_cleanup(v, f)
    # always decimate (by at least 30 %): the cheapest edges to collapse are the sliver triangles of marching cubes, which break LSCM
    out = igl.decimate(v.astype(np.float64), f.astype(np.int64), int(min(target_faces, 0.7 * len(f))))
    v, f = np.ascontiguousarray(out[0]), np.ascontiguousarray(out[1], dtype=np.int64)
    v, f = _manifold_cleanup(*_compact(v, f))
    if v.ndim != 2 or len(f) == 0:
        raise LatticeError("LSCM mesh is empty after decimation")
    if frame is None:
        raise LatticeError("frame required for the seam")
    ax = prep.axis
    heel_sgn = -1.0 if ax["toe"] == "+x" else 1.0
    xs = ax["x_min"] + seam_x_frac * (ax["x_max"] - ax["x_min"]) if ax["toe"] == "+x" else ax["x_max"] - seam_x_frac * (ax["x_max"] - ax["x_min"])
    yc = np.interp(v[:, 0], frame["x"], frame["yc"])
    heel_side = (v[:, 0] < xs) if ax["toe"] == "+x" else (v[:, 0] > xs)
    cut = (np.abs(v[:, 1] - yc) < seam_half_mm) & heel_side
    v, f = _largest(*_compact(v, f[~cut[f].any(1)]))
    v, f = _manifold_cleanup(v, f)  # bow-tie vertices (two fans sharing one vertex) leave the LSCM system singular
    chi = _euler(v, f)
    if chi != 1:
        raise LatticeError(f"LSCM mesh is not a single disk (Euler characteristic {chi}): fall back to the analytic chart")
    # pins: two boundary vertices on opposite sides at 20 % and 80 % of the length (pins at the toe tip, where the strip is a cusp,
    # collapse the solution: the cusp is a pole of the conformal map)
    bnd = np.asarray(igl.boundary_loop(f))
    x0, x1 = float(v[:, 0].min()), float(v[:, 0].max())

    def pick(xf, side):
        sel = bnd[v[bnd, 1] * side > 0]
        return sel[np.argmin(np.abs(v[sel, 0] - (x0 + xf * (x1 - x0))))]

    i0, i1 = pick(pin_fracs[0], -1.0), pick(pin_fracs[1], 1.0)
    uv = lscm(v, f, np.array([i0, i1]), np.array([[0.0, 0.0], [float(np.linalg.norm(v[i1] - v[i0])), 0.0]]))
    # orient: heel -> toe is +u
    A = np.c_[uv - uv.mean(0), np.ones(len(uv))]
    grad = np.linalg.lstsq(A, v[:, 0], rcond=None)[0][:2]
    ang = math.atan2(grad[1], grad[0])
    if ax["toe"] != "+x":
        ang += math.pi
    c, s_ = math.cos(-ang), math.sin(-ang)
    uv = uv @ np.array([[c, s_], [-s_, c]])
    # mm scale: median sqrt(s1 s2) of the faces
    ch = LscmChart(v, f, uv, {})
    uv_s = _scale_uv(ch)
    ch = LscmChart(v, f, uv_s, {})
    sv = face_singular_values(ch)
    an = sv[:, 0] / sv[:, 1]
    sz = np.sqrt(sv[:, 0] * sv[:, 1])  # world mm per uv unit
    ch.report = {"faces": int(len(f)), "vertices": int(len(v)), "euler": int(chi), "anisotropy_median": float(np.median(an)), "anisotropy_p95": float(np.percentile(an, 95)),
                 "anisotropy_max": float(an.max()), "area_factor_p05": float(np.percentile(sz, 5)), "area_factor_median": float(np.median(sz)),
                 "area_factor_p95": float(np.percentile(sz, 95)), "area_factor_min": float(sz.min()), "area_factor_max": float(sz.max()),
                 "uv_extent_mm": (uv_s.max(0) - uv_s.min(0)).round(1).tolist(), "world_extent_mm": (v.max(0) - v.min(0)).round(1).tolist(),
                 "seconds": round(time.perf_counter() - t0, 2)}
    return ch


def face_singular_values(ch: LscmChart) -> np.ndarray:
    v, f, uv = ch.v, ch.f, ch.uv
    e1, e2 = v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]
    D = np.stack([uv[f[:, 1]] - uv[f[:, 0]], uv[f[:, 2]] - uv[f[:, 0]]], axis=-1)
    E = np.stack([e1, e2], axis=-1)
    ok = np.abs(np.linalg.det(D)) > 1e-12
    M = np.zeros((len(f), 3, 2))
    M[ok] = E[ok] @ np.linalg.inv(D[ok])
    return np.linalg.svd(M, compute_uv=False)


def _scale_uv(ch: LscmChart) -> np.ndarray:
    sv = face_singular_values(ch)
    s = float(np.median(np.sqrt(sv[:, 0] * sv[:, 1])))  # world per uv
    return ch.uv * s
