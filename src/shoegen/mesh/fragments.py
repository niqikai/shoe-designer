"""Removal of small disconnected pieces (``profile.min_fragment_volume_mm3``, ADR 0004).

A *shell* is a connected component of the surface. Its signed volume is positive for the
outer boundary of a solid piece and negative for the boundary of a cavity inside one.
A fragment is a shell with ``0 <= volume < threshold``; cavity shells inside a removed
fragment go with it (winding-number test). The shell with the largest volume is always
kept, so a part that is itself below the threshold is not deleted outright (the hard
checks of M0.9 report it instead). Volumes are summed in canonical triangle order, so the
decisions are deterministic.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from numba import njit

from shoegen.mesh import topology as topo
from shoegen.mesh.model import Mesh, index_dtype

_JIT = {"cache": True, "nogil": True}


@dataclass
class FragmentStats:
    threshold_mm3: float
    shells_before: int
    removed: int = 0
    """Positive shells (solid pieces) removed."""
    removed_volume_mm3: float = 0.0
    removed_triangles: int = 0
    cavities_removed: int = 0
    """Negative shells removed because they lay inside a removed fragment."""

    def to_dict(self) -> dict:
        return asdict(self)


@njit(**_JIT)
def _shell_measures(verts, tri, tlabel, vlabel, nc):
    vol = np.zeros(nc, np.float64)
    ntri = np.zeros(nc, np.int64)
    lo = np.full((nc, 3), np.inf)
    hi = np.full((nc, 3), -np.inf)
    first_vertex = np.full(nc, -1, np.int64)
    for t in range(tri.shape[0]):
        c = tlabel[t]
        a = verts[tri[t, 0]]
        b = verts[tri[t, 1]]
        d = verts[tri[t, 2]]
        vol[c] += (
            a[0] * (b[1] * d[2] - b[2] * d[1])
            + a[1] * (b[2] * d[0] - b[0] * d[2])
            + a[2] * (b[0] * d[1] - b[1] * d[0])
        ) / 6.0
        ntri[c] += 1
    for v in range(verts.shape[0]):
        c = vlabel[v]
        if c < 0:
            continue
        if first_vertex[c] < 0:
            first_vertex[c] = v
        for k in range(3):
            lo[c, k] = min(lo[c, k], verts[v, k])
            hi[c, k] = max(hi[c, k], verts[v, k])
    return vol, ntri, lo, hi, first_vertex


@njit(**_JIT)
def _winding_number(verts, tri, tlabel, shell, q):
    """Generalized winding number of shell ``shell`` at point ``q`` (van Oosterom &
    Strackee solid angles, summed in triangle order)."""
    total = 0.0
    for t in range(tri.shape[0]):
        if tlabel[t] != shell:
            continue
        a = verts[tri[t, 0]] - q
        b = verts[tri[t, 1]] - q
        c = verts[tri[t, 2]] - q
        la = math.sqrt(a @ a)
        lb = math.sqrt(b @ b)
        lc = math.sqrt(c @ c)
        det = (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            + a[1] * (b[2] * c[0] - b[0] * c[2])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
        den = la * lb * lc + (a @ b) * lc + (b @ c) * la + (c @ a) * lb
        total += 2.0 * math.atan2(det, den)
    return total / (4.0 * math.pi)


@njit(**_JIT)
def _keep_triangles(tri, tlabel, remove):
    nt = tri.shape[0]
    keep = np.empty(nt, np.bool_)
    for t in range(nt):
        keep[t] = not remove[tlabel[t]]
    return keep


def remove_fragments(mesh: Mesh, min_volume_mm3: float) -> tuple[Mesh, FragmentStats]:
    """Drop shells with ``0 <= volume < min_volume_mm3`` (and cavities inside them)."""
    thr = float(min_volume_mm3)
    if not (math.isfinite(thr) and thr >= 0):
        raise ValueError(f"min_volume_mm3 must be finite and >= 0, got {min_volume_mm3!r}")
    if mesh.n_faces == 0:
        return mesh, FragmentStats(threshold_mm3=thr, shells_before=0)
    v, f = mesh.vertices, mesh.faces
    vlabel, tlabel, nc = topo.components(f, mesh.n_vertices)
    vol, ntri, lo, hi, first_vertex = _shell_measures(v, f, tlabel, vlabel, nc)
    stats = FragmentStats(threshold_mm3=thr, shells_before=int(nc))
    largest = int(np.argmax(vol))
    remove = np.zeros(nc, dtype=bool)
    for c in range(nc):
        if c != largest and 0.0 <= vol[c] < thr:
            remove[c] = True
    if remove.any():
        for p in np.flatnonzero(remove):
            for n in np.flatnonzero(vol < 0):
                if remove[n] or -vol[n] > vol[p]:
                    continue
                if np.any(lo[n] < lo[p]) or np.any(hi[n] > hi[p]):
                    continue
                q = v[first_vertex[n]]
                if _winding_number(v, f, tlabel, int(p), q) > 0.5:
                    remove[n] = True
                    stats.cavities_removed += 1
    if not remove.any():
        return mesh, stats
    pos = remove & (vol >= 0)
    stats.removed = int(np.count_nonzero(pos))
    stats.removed_volume_mm3 = float(vol[pos].sum())
    stats.removed_triangles = int(ntri[remove].sum())
    keep = _keep_triangles(f, tlabel, remove)
    faces = f[keep]
    used = np.zeros(mesh.n_vertices, dtype=bool)
    used[faces.ravel()] = True
    new = np.cumsum(used) - 1  # monotonic: canonical order is kept
    nv = int(used.sum())
    out = Mesh(v[used], new[faces].astype(index_dtype(nv)))
    return out, stats
