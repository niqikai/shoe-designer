"""Vertical projection of a last: footprint outline and lowest-z height (plan M0.3).

Everything is computed from the triangle mesh with exact geometry, no rasterisation.

The *lower surface* is the set of downward-facing triangles (normal z component below
``-1e-3``). Near-vertical triangles are left out on purpose: where tessellations of the
top and bottom rims of a vertical wall differ by the chord tolerance, they form slivers
that would otherwise make the lowest z jump by the wall height inside a 0.01 mm strip.
For a closed solid, the projection of the lower surface is the projection of the solid
(a vertical line enters the solid through a downward-facing triangle).

* ``zmin(xy)``: the lowest z of the lower surface above each point, by testing the point
  against the XY-projected triangles (numba loop over a bucket grid) and interpolating z.
  ``+inf`` where no triangle covers the point. Tolerances are closed.
* The *outline* is the boundary of the lower surface, minus boundary edges that lie
  strictly inside the projection (folds of the lower surface; found by probing both sides
  of the edge with ``zmin``). Every outline piece keeps the z of its end points.
* ``footprint_sdf(xy)``: exact 2D signed distance to the outline (negative where covered).
  Nearest segment search: k nearest midpoints of pieces cut to <= 0.5 mm, then the exact
  point-segment distance on the candidates.
* ``height(xy)``: ``zmin`` inside the footprint; outside, the z interpolated on the nearest
  outline piece (edge value, so the height map is continuous across the outline).
"""

from __future__ import annotations

import numpy as np
from numba import njit
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from shoegen.last.errors import LastMeshError
from shoegen.last.mesh import LastMesh

_BUCKET_MM = 2.0
_PIECE_MM = 0.5
_DOWN_COS = 1e-3
_PROBE_MM = 1e-5
_K_NEAREST = 16
_QUERY_CHUNK = 200_000
_MIN_AREA_MM2 = 0.25


# --------------------------------------------------------------------------- numba kernels


@njit(cache=True)
def _bucket_counts(bx0, by0, bx1, by1, nx, ny, counts):
    for t in range(bx0.shape[0]):
        for ix in range(bx0[t], bx1[t] + 1):
            for iy in range(by0[t], by1[t] + 1):
                counts[ix * ny + iy] += 1


@njit(cache=True)
def _bucket_fill(bx0, by0, bx1, by1, ny, start, cursor, items):
    for t in range(bx0.shape[0]):
        for ix in range(bx0[t], bx1[t] + 1):
            for iy in range(by0[t], by1[t] + 1):
                c = ix * ny + iy
                items[start[c] + cursor[c]] = t
                cursor[c] += 1


@njit(cache=True)
def _zmin_kernel(px, py, tx, ty, tz, tinv, start, items, ox, oy, inv_cell, nx, ny, tol, out):
    for q in range(px.shape[0]):
        fx = (px[q] - ox) * inv_cell
        fy = (py[q] - oy) * inv_cell
        best = np.inf
        if fx >= 0.0 and fy >= 0.0 and fx < nx and fy < ny:
            c = int(fx) * ny + int(fy)
            for k in range(start[c], start[c + 1]):
                t = items[k]
                x0 = tx[t, 0]
                y0 = ty[t, 0]
                dx = px[q] - x0
                dy = py[q] - y0
                l1 = (dx * (ty[t, 2] - y0) - (tx[t, 2] - x0) * dy) * tinv[t]
                l2 = ((tx[t, 1] - x0) * dy - dx * (ty[t, 1] - y0)) * tinv[t]
                l0 = 1.0 - l1 - l2
                if l0 >= -tol and l1 >= -tol and l2 >= -tol:
                    z = l0 * tz[t, 0] + l1 * tz[t, 1] + l2 * tz[t, 2]
                    best = min(best, z)
        out[q] = best


# --------------------------------------------------------------------------- projection


def _drop_small_components(faces: np.ndarray, mask: np.ndarray, proj_area: np.ndarray):
    """Keep only edge-connected groups of ``mask`` faces with projected area >= _MIN_AREA_MM2.

    Sliver triangles at the equator of a rounded rim have meaningless normals; the ones
    that come out downward-facing form tiny islands far from the real lower surface.
    """
    idx = np.flatnonzero(mask)
    sub = faces[idx]
    ea = sub.reshape(-1)
    eb = np.roll(sub, -1, axis=1).reshape(-1)
    nv = int(faces.max()) + 1
    key = np.minimum(ea, eb) * nv + np.maximum(ea, eb)
    order = np.argsort(key, kind="stable")
    ks = key[order]
    same = np.flatnonzero(ks[1:] == ks[:-1])
    owner = np.repeat(np.arange(len(idx)), 3)
    f0 = owner[order[same]]
    f1 = owner[order[same + 1]]
    graph = coo_matrix((np.ones(len(f0)), (f0, f1)), shape=(len(idx), len(idx)))
    _, label = connected_components(graph, directed=False)
    area = np.bincount(label, weights=proj_area[idx])
    keep_idx = idx[area[label] >= _MIN_AREA_MM2]
    out = np.zeros_like(mask)
    out[keep_idx] = True
    return out


class LastProjection:
    """Footprint and lowest-z queries for a closed :class:`LastMesh`."""

    def __init__(self, mesh: LastMesh) -> None:
        v = mesh.vertices
        f = mesh.faces
        a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        n = np.cross(b - a, c - a)
        nlen = np.maximum(np.linalg.norm(n, axis=1), 1e-300)
        down = (n[:, 2] / nlen < -_DOWN_COS) & (n[:, 2] < -1e-12)
        down = _drop_small_components(f, down, 0.5 * np.abs(n[:, 2]))
        if not down.any():
            raise LastMeshError("last has no downward-facing triangles")
        self._down_faces = f[down]
        self._tx = np.ascontiguousarray(np.stack([a[down, 0], b[down, 0], c[down, 0]], axis=1))
        self._ty = np.ascontiguousarray(np.stack([a[down, 1], b[down, 1], c[down, 1]], axis=1))
        self._tz = np.ascontiguousarray(np.stack([a[down, 2], b[down, 2], c[down, 2]], axis=1))
        self._tinv = np.ascontiguousarray(1.0 / n[down, 2])
        self._build_buckets()
        self._build_outline(v)

    # ---------------------------------------------------------------- buckets

    def _build_buckets(self) -> None:
        pad = 1e-6
        x0 = self._tx.min(axis=1) - pad
        x1 = self._tx.max(axis=1) + pad
        y0 = self._ty.min(axis=1) - pad
        y1 = self._ty.max(axis=1) + pad
        self._ox = float(x0.min())
        self._oy = float(y0.min())
        self._inv_cell = 1.0 / _BUCKET_MM
        self._nx = int((x1.max() - self._ox) * self._inv_cell) + 1
        self._ny = int((y1.max() - self._oy) * self._inv_cell) + 1
        bx0 = ((x0 - self._ox) * self._inv_cell).astype(np.int64)
        bx1 = ((x1 - self._ox) * self._inv_cell).astype(np.int64)
        by0 = ((y0 - self._oy) * self._inv_cell).astype(np.int64)
        by1 = ((y1 - self._oy) * self._inv_cell).astype(np.int64)
        counts = np.zeros(self._nx * self._ny, dtype=np.int64)
        _bucket_counts(bx0, by0, bx1, by1, self._nx, self._ny, counts)
        self._start = np.zeros(counts.size + 1, dtype=np.int64)
        np.cumsum(counts, out=self._start[1:])
        self._items = np.empty(int(self._start[-1]), dtype=np.int64)
        _bucket_fill(
            bx0, by0, bx1, by1, self._ny, self._start, np.zeros(counts.size, np.int64), self._items
        )

    # ---------------------------------------------------------------- zmin

    def zmin(self, xy: np.ndarray, tol: float = 1e-9) -> np.ndarray:
        """Lowest z of the lower surface above each ``xy`` row; ``+inf`` where uncovered."""
        xy = np.asarray(xy, dtype=np.float64)
        px = np.ascontiguousarray(xy[:, 0])
        py = np.ascontiguousarray(xy[:, 1])
        out = np.empty(len(xy), dtype=np.float64)
        _zmin_kernel(
            px,
            py,
            self._tx,
            self._ty,
            self._tz,
            self._tinv,
            self._start,
            self._items,
            self._ox,
            self._oy,
            self._inv_cell,
            self._nx,
            self._ny,
            tol,
            out,
        )
        return out

    # ---------------------------------------------------------------- outline

    def _build_outline(self, v: np.ndarray) -> None:
        f = self._down_faces
        ea = f.reshape(-1)
        eb = np.roll(f, -1, axis=1).reshape(-1)
        nv = len(v)
        key = np.minimum(ea, eb) * nv + np.maximum(ea, eb)
        _, first, counts = np.unique(key, return_index=True, return_counts=True)
        edge = first[counts == 1]  # edges owned by exactly one lower triangle
        p3 = v[ea[edge]]
        q3 = v[eb[edge]]
        seg = q3[:, :2] - p3[:, :2]
        length = np.linalg.norm(seg, axis=1)
        ok = length > 1e-9
        p3, q3, seg, length = p3[ok], q3[ok], seg[ok], length[ok]
        # Probe both sides: boundary iff exactly one side is covered by the lower surface.
        mid = 0.5 * (p3[:, :2] + q3[:, :2])
        normal = np.column_stack([-seg[:, 1], seg[:, 0]]) / length[:, None]
        left = np.isfinite(self.zmin(mid + _PROBE_MM * normal))
        right = np.isfinite(self.zmin(mid - _PROBE_MM * normal))
        boundary = left != right
        p3, q3, length = p3[boundary], q3[boundary], length[boundary]
        if len(p3) == 0:
            raise LastMeshError("could not find a footprint outline for the last")
        # Cut into pieces of at most _PIECE_MM, interpolating z.
        n_piece = np.maximum(np.ceil(length / _PIECE_MM).astype(np.int64), 1)
        seg_id = np.repeat(np.arange(len(p3)), n_piece)
        first_piece = np.cumsum(n_piece) - n_piece
        k = np.arange(n_piece.sum()) - np.repeat(first_piece, n_piece)
        t0 = (k / n_piece[seg_id])[:, None]
        t1 = ((k + 1) / n_piece[seg_id])[:, None]
        d3 = (q3 - p3)[seg_id]
        pa = p3[seg_id] + d3 * t0
        pb = p3[seg_id] + d3 * t1
        self._pa = np.ascontiguousarray(pa[:, :2])
        self._pb = np.ascontiguousarray(pb[:, :2])
        self._za = np.ascontiguousarray(pa[:, 2])
        self._zb = np.ascontiguousarray(pb[:, 2])
        self._tree = cKDTree(0.5 * (self._pa + self._pb))
        pts = np.vstack([self._pa, self._pb])
        self.bbox_min = (float(pts[:, 0].min()), float(pts[:, 1].min()))
        self.bbox_max = (float(pts[:, 0].max()), float(pts[:, 1].max()))

    @property
    def outline_segments(self) -> tuple[np.ndarray, np.ndarray]:
        """Endpoints ``(A, B)`` of the outline pieces, each ``(S, 2)``."""
        return self._pa, self._pb

    def _nearest_outline(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Distance to the outline polylines and the z interpolated at the closest point."""
        k = min(_K_NEAREST, len(self._pa))
        _, idx = self._tree.query(xy, k=k)
        idx = idx.reshape(len(xy), k)
        a = self._pa[idx]  # (n, k, 2)
        ab = self._pb[idx] - a
        ap = xy[:, None, :] - a
        denom = np.maximum(np.einsum("nkc,nkc->nk", ab, ab), 1e-300)
        t = np.clip(np.einsum("nkc,nkc->nk", ap, ab) / denom, 0.0, 1.0)
        diff = xy[:, None, :] - (a + ab * t[..., None])
        d2 = np.einsum("nkc,nkc->nk", diff, diff)
        best = np.argmin(d2, axis=1)
        rows = np.arange(len(xy))
        tb = t[rows, best]
        ib = idx[rows, best]
        z = self._za[ib] + (self._zb[ib] - self._za[ib]) * tb
        return np.sqrt(d2[rows, best]), z

    # ---------------------------------------------------------------- public queries

    def footprint_sdf(self, xy: np.ndarray) -> np.ndarray:
        """Exact signed distance to the footprint outline (negative inside)."""
        xy = np.asarray(xy, dtype=np.float64)
        out = np.empty(len(xy))
        for s in range(0, len(xy), _QUERY_CHUNK):
            c = xy[s : s + _QUERY_CHUNK]
            dist, _ = self._nearest_outline(c)
            covered = np.isfinite(self.zmin(c))
            out[s : s + _QUERY_CHUNK] = np.where(covered, -dist, dist)
        return out

    def height(self, xy: np.ndarray) -> np.ndarray:
        """Lowest z above each point; outside the footprint the value at the nearest outline."""
        xy = np.asarray(xy, dtype=np.float64)
        out = np.empty(len(xy))
        for s in range(0, len(xy), _QUERY_CHUNK):
            c = xy[s : s + _QUERY_CHUNK]
            z = self.zmin(c)
            miss = ~np.isfinite(z)
            if miss.any():
                _, z_edge = self._nearest_outline(c[miss])
                z[miss] = z_edge
            out[s : s + _QUERY_CHUNK] = z
        return out
