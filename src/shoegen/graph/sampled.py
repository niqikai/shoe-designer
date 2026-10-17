"""Data layout of sampled fields, and reference producers.

This module is the contract between the samplers (plan M0.3) and the evaluator (M0.4).
Everything is evaluated by :mod:`shoegen.graph.kernels`; the producers below build the
arrays from any vectorised signed-distance callable, so M0.3 can call them with a libigl
``signed_distance`` wrapper and tests call them with analytic SDFs.

Common rules
------------
* Arrays are C-contiguous, little-endian, exactly ``float32`` (values) or ``int32``
  (index). They are named in the graph by :func:`shoegen.graph.arrays.array_sha256` and
  stored as ``<sha256>.npy`` (:class:`shoegen.graph.arrays.NpyDirectoryStore`).
* Every sample is the signed distance at a point computed from a global integer index as
  ``origin + i * h`` in float64 (ADR 0005), never by accumulating offsets. Samples shared
  by neighbouring bricks are therefore bitwise equal.
* Sign: negative inside the last / below the height surface.

Two-level 3D field (``SampledField``, ``HeightSurfaceField``; ADR 0003)
-------------------------------------------------------------------------
Parameters live in :class:`shoegen.graph.nodes.TwoLevelGrid`; three arrays:

``fine_index`` : int32, shape ``(NBx, NBy, NBz)``
    Brick table of the fine grid (spacing ``fine_h``, default 0.5 mm, origin
    ``fine_origin``). Brick ``(bx, by, bz)`` covers fine cells ``[b*S, (b+1)*S)`` per axis
    with ``S = brick_size`` (default 8). Entry = row in ``fine_bricks``, or ``-1`` if the
    brick is not stored.
``fine_bricks`` : float32, shape ``(N, S+1, S+1, S+1)``
    ``fine_bricks[r, i, j, k] = d(fine_origin + ((bx*S+i), (by*S+j), (bz*S+k)) * fine_h)``
    for ``i, j, k`` in ``0..S``. Each brick carries its own upper faces, so interpolation
    never reads a neighbouring brick (duplicated faces cost 9^3/8^3 = 1.42x).
``coarse`` : float32, shape ``(Nx, Ny, Nz)``, each >= 2
    ``coarse[i, j, k] = d(coarse_origin + (i, j, k) * coarse_h)``, ``coarse_h`` default
    2 mm, covering the source bbox expanded by >= 30 mm.

Producer obligations:

* Store every brick whose region contains a point with ``|d| <= narrow_band_mm``. A
  sufficient test (d is 1-Lipschitz) is ``min |d(sample)| <= narrow_band_mm +
  sqrt(3)/2 * fine_h`` over the brick's samples; the brick table must extend past the bbox
  by at least ``narrow_band_mm`` plus one brick.
* ``bbox_min``/``bbox_max`` are the bbox of the source surface (last mesh, or the
  triangulated height surface). The height surface must be triangulated over an XY range
  that covers the coarse domain, so its sign is consistent wherever the grids are used.

Evaluation at ``p`` (first rule that applies):

1. ``u = (p - fine_origin) / fine_h``. If ``0 <= u < NB*S`` on every axis and the brick of
   ``c = floor(u)`` is stored: trilinear interpolation in that brick at local cell
   ``c - b*S``, weights ``u - c``. This is the ``exact`` narrow band.
2. ``v = (p - coarse_origin) / coarse_h``. If ``0 <= v <= N-1`` on every axis: trilinear
   in ``coarse`` (cell ``min(floor(v), N-2)``). Error up to the coarse sampling error
   (about 1 mm); the jump at the fine/coarse switch is absorbed by the skip safety factor
   (ADR 0005).
3. Otherwise ``s * dist(p, bbox)``, where ``s`` is the sign of rule 2 at ``p`` clamped to
   the coarse domain. ``dist(p, bbox)`` never exceeds the true distance, so block skipping
   stays safe far away and the field is never truncated to a constant.

Interpolation is in float64 with ``a*(1-t) + b*t`` per axis (x, then y, then z), which
is exact at both cell ends, so the field is continuous across cells and bricks.

2D grid (``Extrude2D.field``)
-----------------------------
``values`` : float32, shape ``(Nx, Ny)``, each >= 2, ``values[i, j] = d2(origin + (i, j)*h)``
(the signed 2D distance to the outline, e.g. an exact distance transform of the foot
outline). Inside the grid: bilinear; outside: ``s * dist(p_xy, bbox2d)`` with ``s`` the
sign at the clamped point. The grid should extend >= 30 mm past the outline bbox, and
by more than any ``Extrude2D.offset``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol

import numpy as np

from shoegen.graph.nodes import Grid2D, TwoLevelGrid

SdfFn = Callable[[np.ndarray], np.ndarray]
"""Vectorised signed distance: ``(M, 3)`` (or ``(M, 2)``) float64 -> ``(M,)``."""

DEFAULT_FINE_H = 0.5
DEFAULT_BRICK = 8
DEFAULT_BAND = 6.0
DEFAULT_COARSE_H = 2.0
DEFAULT_COARSE_MARGIN = 30.0
_CHUNK_BRICKS = 256


class _Store(Protocol):
    def put(self, arr: np.ndarray) -> str:  # pragma: no cover - protocol
        ...


def _grid_axis(lo: float, hi: float, h: float, pad: float) -> tuple[float, int]:
    """Origin (a multiple of h) and sample count covering [lo - pad, hi + pad]."""
    origin = h * math.floor((lo - pad) / h)
    n = math.ceil((hi + pad - origin) / h) + 1
    return origin, max(n, 2)


def _points(origin: np.ndarray, h: float, idx: np.ndarray) -> np.ndarray:
    return origin[None, :] + idx.astype(np.float64) * h


def sample_two_level(
    fn: SdfFn,
    bbox_min: tuple[float, float, float],
    bbox_max: tuple[float, float, float],
    store: _Store,
    *,
    fine_h: float = DEFAULT_FINE_H,
    brick_size: int = DEFAULT_BRICK,
    narrow_band_mm: float = DEFAULT_BAND,
    coarse_h: float = DEFAULT_COARSE_H,
    coarse_margin_mm: float = DEFAULT_COARSE_MARGIN,
) -> TwoLevelGrid:
    """Sample ``fn`` into the two-level layout, put the arrays in ``store``.

    Memory stays proportional to the narrow band: ``fn`` is called on brick centres first,
    then brick by brick (in chunks) on candidate bricks only.
    """
    bmin = np.asarray(bbox_min, dtype=np.float64)
    bmax = np.asarray(bbox_max, dtype=np.float64)
    s = int(brick_size)

    # Coarse grid over bbox + margin.
    c_axes = [_grid_axis(bmin[a], bmax[a], coarse_h, coarse_margin_mm) for a in range(3)]
    c_origin = np.array([o for o, _ in c_axes])
    c_shape = tuple(n for _, n in c_axes)
    ci = np.indices(c_shape, dtype=np.int64).reshape(3, -1).T
    coarse = np.asarray(fn(_points(c_origin, coarse_h, ci)), dtype=np.float64)
    coarse = coarse.astype(np.float32).reshape(c_shape)

    # Fine brick table over bbox + band + one brick.
    pad = narrow_band_mm + s * fine_h
    f_origin = np.empty(3)
    nb = [0, 0, 0]
    for a in range(3):
        o, n = _grid_axis(bmin[a], bmax[a], fine_h, pad)
        f_origin[a] = o
        nb[a] = max(1, math.ceil((n - 1) / s))
    bidx = np.indices(nb, dtype=np.int64).reshape(3, -1).T
    centers = f_origin[None, :] + (bidx.astype(np.float64) * s + s / 2.0) * fine_h
    dc = np.abs(np.asarray(fn(centers), dtype=np.float64))
    half_diag = math.sqrt(3.0) * s * fine_h / 2.0
    candidates = bidx[dc <= narrow_band_mm + half_diag + 1e-9]

    keep_tol = narrow_band_mm + math.sqrt(3.0) / 2.0 * fine_h
    local = np.indices((s + 1,) * 3, dtype=np.int64).reshape(3, -1).T  # (S+1)^3 x 3
    index = np.full(nb, -1, dtype=np.int32)
    bricks: list[np.ndarray] = []
    for start in range(0, len(candidates), _CHUNK_BRICKS):
        chunk = candidates[start : start + _CHUNK_BRICKS]
        gi = (chunk[:, None, :] * s + local[None, :, :]).reshape(-1, 3)
        vals = np.asarray(fn(_points(f_origin, fine_h, gi)), dtype=np.float64)
        vals = vals.reshape(len(chunk), -1)
        for b, row in zip(chunk, vals, strict=True):
            if np.min(np.abs(row)) <= keep_tol:
                index[b[0], b[1], b[2]] = len(bricks)
                bricks.append(row.astype(np.float32).reshape((s + 1,) * 3))
    fine = np.stack(bricks) if bricks else np.zeros((0, s + 1, s + 1, s + 1), dtype=np.float32)

    return TwoLevelGrid(
        fine_origin=tuple(float(v) for v in f_origin),
        fine_h=float(fine_h),
        brick_size=s,
        narrow_band_mm=float(narrow_band_mm),
        coarse_origin=tuple(float(v) for v in c_origin),
        coarse_h=float(coarse_h),
        bbox_min=tuple(float(v) for v in bmin),
        bbox_max=tuple(float(v) for v in bmax),
        fine_bricks_sha256=store.put(np.ascontiguousarray(fine, dtype=np.float32)),
        fine_index_sha256=store.put(index),
        coarse_sha256=store.put(np.ascontiguousarray(coarse, dtype=np.float32)),
    )


def sample_grid2d(
    fn2: SdfFn,
    bbox_min: tuple[float, float],
    bbox_max: tuple[float, float],
    store: _Store,
    *,
    source_sha256: str,
    sampling: dict | None = None,
    h: float = DEFAULT_FINE_H,
    margin_mm: float = DEFAULT_COARSE_MARGIN,
) -> Grid2D:
    """Sample a 2D signed distance ``fn2`` on a dense grid over bbox + margin."""
    axes = [_grid_axis(bbox_min[a], bbox_max[a], h, margin_mm) for a in range(2)]
    origin = np.array([o for o, _ in axes])
    shape = tuple(n for _, n in axes)
    gi = np.indices(shape, dtype=np.int64).reshape(2, -1).T
    vals = np.asarray(fn2(_points(origin, h, gi)), dtype=np.float64)
    vals = vals.astype(np.float32).reshape(shape)
    return Grid2D(
        source_sha256=source_sha256,
        sampling=dict(sampling or {}),
        origin=(float(origin[0]), float(origin[1])),
        h=float(h),
        bbox_min=(float(bbox_min[0]), float(bbox_min[1])),
        bbox_max=(float(bbox_max[0]), float(bbox_max[1])),
        values_sha256=store.put(vals),
    )
