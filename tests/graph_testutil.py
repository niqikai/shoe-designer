"""Shared helpers for the GeometryGraph tests: closed-form truths and synthetic fields."""

from __future__ import annotations

import numpy as np

from shoegen.graph import (
    Graph,
    HeightSurfaceField,
    MemoryArrayStore,
    SampledField,
    sample_grid2d,
    sample_two_level,
)

FAKE_SOURCE = "ab" * 32


def rng(seed: int = 0) -> np.random.Generator:
    return np.random.default_rng(seed)


def single(node) -> Graph:
    return Graph(root="n", nodes={"n": node})


# ---------------------------------------------------------------- closed-form truths
# Written independently of the kernels: projection onto the solid for the outside,
# distance to the nearest face for the inside.


def box_truth(p: np.ndarray, center, half) -> np.ndarray:
    c = np.asarray(center, dtype=np.float64)
    b = np.asarray(half, dtype=np.float64)
    lo, hi = c - b, c + b
    q = np.clip(p, lo, hi)
    outside = np.linalg.norm(p - q, axis=1)
    inside = -np.min(np.minimum(p - lo, hi - p), axis=1)
    is_in = np.all((p >= lo) & (p <= hi), axis=1)
    return np.where(is_in, inside, outside)


def rounded_box_truth(p, center, half, r) -> np.ndarray:
    # A rounded box is the inner box dilated by a ball of radius r.
    return box_truth(p, center, np.asarray(half) - r) - r


def capsule_truth(p, a, b, r) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    ab = b - a
    t = np.clip(((p - a) @ ab) / (ab @ ab), 0.0, 1.0)
    nearest = a[None, :] + t[:, None] * ab[None, :]
    return np.linalg.norm(p - nearest, axis=1) - r


def halfspace_truth(p, normal, offset) -> np.ndarray:
    n = np.asarray(normal, dtype=np.float64)
    n = n / np.linalg.norm(n)
    return p @ n - offset


def capped_cylinder_truth(p, center_xy, radius, z_min, z_max) -> np.ndarray:
    """Exact SDF of a vertical solid cylinder, by projection onto the solid."""
    cxy = np.asarray(center_xy, dtype=np.float64)
    rel = p[:, :2] - cxy
    rho = np.linalg.norm(rel, axis=1)
    scale = np.where(rho > radius, radius / np.maximum(rho, 1e-300), 1.0)
    qxy = cxy + rel * scale[:, None]
    qz = np.clip(p[:, 2], z_min, z_max)
    q = np.column_stack([qxy, qz])
    outside = np.linalg.norm(p - q, axis=1)
    inside = -np.minimum(radius - rho, np.minimum(p[:, 2] - z_min, z_max - p[:, 2]))
    is_in = (rho <= radius) & (p[:, 2] >= z_min) & (p[:, 2] <= z_max)
    return np.where(is_in, inside, outside)


def sphere_sdf(center, radius):
    c = np.asarray(center, dtype=np.float64)

    def fn(q: np.ndarray) -> np.ndarray:
        return np.linalg.norm(q - c, axis=1) - radius

    return fn


# ---------------------------------------------------------------- synthetic sampled fields

SPHERE_C = (1.0, -2.0, 3.0)
SPHERE_R = 20.0


def sphere_field(store: MemoryArrayStore, **kw) -> SampledField:
    """A synthetic 'last': sphere of radius 20 sampled with the two-level layout."""
    c = np.asarray(SPHERE_C)
    grid = sample_two_level(
        sphere_sdf(SPHERE_C, SPHERE_R),
        tuple(c - SPHERE_R),
        tuple(c + SPHERE_R),
        store,
        **kw,
    )
    return SampledField(
        source_sha256=FAKE_SOURCE, sampling={"kind": "synthetic-sphere", "r": SPHERE_R}, grid=grid
    )


PLANE_N = np.array([0.3, 0.1, -1.0]) / np.linalg.norm([0.3, 0.1, -1.0])


def plane_height_sdf(q: np.ndarray) -> np.ndarray:
    """Signed distance to z = 0.3x + 0.1y + 2 (negative below)."""
    return -(q @ PLANE_N) - 2.0 / np.linalg.norm([0.3, 0.1, -1.0])


def height_field(store: MemoryArrayStore) -> HeightSurfaceField:
    grid = sample_two_level(
        plane_height_sdf, (-25.0, -25.0, -8.0), (25.0, 25.0, 12.0), store, coarse_margin_mm=10.0
    )
    return HeightSurfaceField(
        source_sha256=FAKE_SOURCE, sampling={"kind": "synthetic-plane"}, grid=grid
    )


DISK_C = (2.0, 1.0)
DISK_R = 15.0


def disk_grid(store: MemoryArrayStore):
    c = np.asarray(DISK_C)

    def fn2(q):
        return np.linalg.norm(q - c, axis=1) - DISK_R

    return sample_grid2d(
        fn2,
        tuple(c - DISK_R),
        tuple(c + DISK_R),
        store,
        source_sha256=FAKE_SOURCE,
        sampling={"kind": "synthetic-disk"},
        margin_mm=10.0,
    )


def bits(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(a, dtype=np.float64).view(np.uint64)
