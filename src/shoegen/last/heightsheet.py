"""The open surface ``z = h(x, y) + offset`` as a triangle mesh with a signed distance (M0.3).

The sheet is a Delaunay triangulation (in XY) of points lifted to ``z = h + offset``. It
starts from a regular lattice and is refined adaptively, using the exact height function,
wherever a triangle deviates from the surface: a uniform 0.5 mm grid cannot follow the
bottom of a rounded last near the outline, where the slope tends to infinity (a 0.5 mm cell
of an R = 50 mm hemisphere at its edge is off by 0.125 mm), but a handful of extra points
there can.

Signed distance: the unsigned distance comes from a libigl AABB tree on the triangles; the
sign is the sign of ``z - z_sheet(x, y)`` where ``z_sheet`` interpolates the very same
triangles (point location in the Delaunay triangulation), so that it is consistent with the
distance even within a few micrometres of the surface. Points outside the XY rectangle are
located at their clamped position.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import igl
import numpy as np
from scipy.spatial import Delaunay

HeightFn = Callable[[np.ndarray], np.ndarray]

_QUERY_CHUNK = 262_144
_MAX_POINTS = 3_000_000


@dataclass
class HeightSheet:
    vertices: np.ndarray  # (n, 3) float64
    faces: np.ndarray  # (m, 3) int64
    xy_min: np.ndarray
    xy_max: np.ndarray
    _tri: Delaunay
    _aabb: object

    @property
    def bbox_min(self) -> tuple[float, float, float]:
        return tuple(float(v) for v in self.vertices.min(axis=0))  # type: ignore[return-value]

    @property
    def bbox_max(self) -> tuple[float, float, float]:
        return tuple(float(v) for v in self.vertices.max(axis=0))  # type: ignore[return-value]

    def z_at(self, xy: np.ndarray) -> np.ndarray:
        """Interpolated sheet height at (clamped) ``xy``."""
        q = np.clip(xy, self.xy_min, self.xy_max)
        simplex = self._tri.find_simplex(q)
        miss = simplex < 0
        if miss.any():  # numerical misses on the hull: pull towards the centre
            centre = 0.5 * (self.xy_min + self.xy_max)
            q2 = q[miss] + (centre - q[miss]) * 1e-9
            simplex[miss] = self._tri.find_simplex(q2)
            q = q.copy()
            q[miss] = q2
        simplex = np.maximum(simplex, 0)
        t = self._tri.transform[simplex]
        b2 = np.einsum("nij,nj->ni", t[:, :2, :], q - t[:, 2, :])
        bary = np.column_stack([b2, 1.0 - b2.sum(axis=1)])
        corner_z = self.vertices[self._tri.simplices[simplex], 2]
        return np.einsum("ni,ni->n", bary, corner_z)

    def signed_distance(self, points: np.ndarray) -> np.ndarray:
        """Signed distance to the sheet, negative below it; chunked to bound memory."""
        points = np.asarray(points, dtype=np.float64)
        out = np.empty(len(points))
        for s in range(0, len(points), _QUERY_CHUNK):
            p = np.ascontiguousarray(points[s : s + _QUERY_CHUNK])
            sq, _, _ = self._aabb.squared_distance(self.vertices, self.faces, p)
            below = p[:, 2] < self.z_at(p[:, :2])
            out[s : s + _QUERY_CHUNK] = np.where(below, -np.sqrt(sq), np.sqrt(sq))
        return out


def _lattice(xy_min: np.ndarray, xy_max: np.ndarray, base_h: float) -> np.ndarray:
    nx = max(math.ceil((xy_max[0] - xy_min[0]) / base_h), 1) + 1
    ny = max(math.ceil((xy_max[1] - xy_min[1]) / base_h), 1) + 1
    gx, gy = np.meshgrid(
        np.linspace(xy_min[0], xy_max[0], nx), np.linspace(xy_min[1], xy_max[1], ny), indexing="ij"
    )
    return np.column_stack([gx.ravel(), gy.ravel()])


def _unique_points(pts: np.ndarray) -> np.ndarray:
    key = np.round(pts * 1e6).astype(np.int64)
    _, idx = np.unique(key, axis=0, return_index=True)
    return pts[np.sort(idx)]


def build_height_sheet(
    height: HeightFn,
    xy_min: tuple[float, float],
    xy_max: tuple[float, float],
    offset: float,
    *,
    base_h: float = 1.0,
    tolerance_mm: float = 0.015,
    min_edge_mm: float = 0.03,
    max_iterations: int = 14,
) -> HeightSheet:
    """Triangulate ``z = height(x, y) + offset`` over the rectangle ``[xy_min, xy_max]``.

    Triangles are refined until the surface point at the centroid and edge midpoints is
    within ``tolerance_mm`` (measured normal to the triangle) of the triangle, or the
    longest edge falls below ``min_edge_mm``.
    """
    lo = np.asarray(xy_min, dtype=np.float64)
    hi = np.asarray(xy_max, dtype=np.float64)
    pts = _lattice(lo, hi, base_h)
    for _ in range(max_iterations + 1):
        tri = Delaunay(pts)
        simp = tri.simplices
        z = height(pts)
        a, b, c = pts[simp[:, 0]], pts[simp[:, 1]], pts[simp[:, 2]]
        za, zb, zc = z[simp[:, 0]], z[simp[:, 1]], z[simp[:, 2]]
        tests = np.concatenate(
            [(a + b + c) / 3.0, 0.5 * (a + b), 0.5 * (b + c), 0.5 * (c + a)], axis=0
        )
        m = len(simp)
        interp = np.concatenate(
            [(za + zb + zc) / 3.0, 0.5 * (za + zb), 0.5 * (zb + zc), 0.5 * (zc + za)]
        )
        err = np.abs(height(tests) - interp).reshape(4, m)
        # plane gradient of each triangle
        e1, e2 = b - a, c - a
        det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
        det = np.where(np.abs(det) < 1e-300, 1e-300, det)
        gx = ((zb - za) * e2[:, 1] - (zc - za) * e1[:, 1]) / det
        gy = ((zc - za) * e1[:, 0] - (zb - za) * e2[:, 0]) / det
        err_n = err.max(axis=0) / np.sqrt(1.0 + gx * gx + gy * gy)
        longest = np.sqrt(
            np.maximum.reduce(
                [
                    np.einsum("ij,ij->i", e1, e1),
                    np.einsum("ij,ij->i", e2, e2),
                    np.einsum("ij,ij->i", c - b, c - b),
                ]
            )
        )
        flagged = (err_n > tolerance_mm) & (longest > min_edge_mm)
        if not flagged.any() or len(pts) > _MAX_POINTS:
            break
        sel = np.flatnonzero(flagged)
        new = np.concatenate([tests[k * m + sel] for k in range(4)], axis=0)
        pts = _unique_points(np.vstack([pts, new]))
    if len(tri.points) != len(pts):  # the last round added points
        tri = Delaunay(pts)
    z = height(pts) + offset
    vertices = np.column_stack([pts, z])
    faces = np.ascontiguousarray(tri.simplices, dtype=np.int64)
    aabb = igl.AABB()
    aabb.init(vertices, faces)
    return HeightSheet(vertices, faces, lo, hi, tri, aabb)
