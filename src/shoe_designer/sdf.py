"""Signed distance fields on a regular, z-anisotropic voxel grid.

Convention: phi < 0 inside the solid. Arrays are indexed ``[ix, iy, iz]`` (float32); voxel
``(i, j, k)`` sits at ``origin + (i*h, j*h, k*dz)`` (millimetres, same frame as the mesh).

The z spacing is ``dz = h / tan(alpha_b)`` so that a one-voxel-per-layer erosion in z yields
a surface that is exactly ``alpha_b`` from vertical (design doc issue-10-cavity-rebuild §3).
Horizontal spacing is isotropic.

Mesh -> SDF uses libigl: nearest-triangle distance through an AABB tree, sign through the fast
winding number (robust to holes and non-manifold input). SDF -> mesh uses skimage marching cubes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import igl
import numpy as np
import trimesh
from skimage import measure

ALPHA_B_DEG = 43.0  # construction overhang angle (audit uses 45 deg)
EXPORT_PAD = 2  # empty voxels added on every side before marching cubes
MAX_DROPPED_MM3 = 1000.0  # > 1 cm^3 of discarded fragments is a construction error
CHUNK_POINTS = 2_000_000


class SDFError(Exception):
    pass


@dataclass(frozen=True)
class Grid:
    origin: tuple[float, float, float]
    h: float  # horizontal voxel size, mm
    dz: float  # vertical voxel size, mm
    shape: tuple[int, int, int]

    @staticmethod
    def dz_for(h: float, alpha_deg: float = ALPHA_B_DEG) -> float:
        if not 0.0 < alpha_deg < 90.0:
            raise SDFError(f"alpha_deg must be in (0, 90) degrees, got {alpha_deg!r}")
        return h / math.tan(math.radians(alpha_deg))

    @classmethod
    def from_bounds(cls, lo, hi, h: float, alpha_deg: float = ALPHA_B_DEG, margin: float = 0.0) -> Grid:
        """Grid covering [lo - margin, hi + margin]; the origin is lo - margin."""
        if h <= 0:
            raise SDFError("h must be positive")
        dz = cls.dz_for(h, alpha_deg)
        lo = np.asarray(lo, float) - margin
        hi = np.asarray(hi, float) + margin
        step = np.array([h, h, dz])
        shape = tuple(int(n) + 1 for n in np.ceil((hi - lo) / step - 1e-9))
        return cls(tuple(float(v) for v in lo), float(h), float(dz), shape)

    @classmethod
    def for_mesh(cls, mesh: trimesh.Trimesh, h: float, alpha_deg: float = ALPHA_B_DEG, margin_voxels: int = 3) -> Grid:
        b = mesh.bounds
        margin = margin_voxels * max(h, cls.dz_for(h, alpha_deg))
        return cls.from_bounds(b[0], b[1], h, alpha_deg, margin)

    @property
    def spacing(self) -> tuple[float, float, float]:
        return (self.h, self.h, self.dz)

    @property
    def n_voxels(self) -> int:
        return int(np.prod(self.shape, dtype=np.int64))

    def coords(self, ijk: np.ndarray) -> np.ndarray:
        """World positions (N, 3) of integer voxel indices (N, 3)."""
        return np.asarray(self.origin) + np.asarray(ijk, float) * np.asarray(self.spacing)

    def to_index(self, points: np.ndarray) -> np.ndarray:
        """Continuous voxel coordinates of world points (N, 3)."""
        return (np.asarray(points, float) - np.asarray(self.origin)) / np.asarray(self.spacing)


class MeshQuery:
    """Signed-distance queries against one mesh; the AABB tree and winding-number BVH are built once."""

    def __init__(self, mesh: trimesh.Trimesh):
        self.v = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
        self.f = np.ascontiguousarray(mesh.faces, dtype=np.int64)
        self._aabb = igl.AABB()
        self._aabb.init(self.v, self.f)
        self._bvh = igl.FastWindingNumberBVH()
        self._bvh.init(self.v, self.f)

    def winding(self, points: np.ndarray) -> np.ndarray:
        return self._bvh.winding_number(np.ascontiguousarray(points, dtype=np.float64))

    def signed_distance(self, points: np.ndarray, chunk: int = CHUNK_POINTS) -> np.ndarray:
        """Distance to the surface (AABB), negative where the fast winding number is > 0.5. float32."""
        points = np.asarray(points, float)
        out = np.empty(len(points), np.float32)
        for s in range(0, len(points), chunk):
            q = np.ascontiguousarray(points[s : s + chunk])
            sq = self._aabb.squared_distance(self.v, self.f, q)[0]
            out[s : s + chunk] = np.where(self.winding(q) > 0.5, -1.0, 1.0) * np.sqrt(sq)
        return out


def signed_distance(mesh: trimesh.Trimesh, points: np.ndarray, chunk: int = CHUNK_POINTS) -> np.ndarray:
    """Signed distance (negative inside) of ``points`` (N, 3) to ``mesh``. float32."""
    return MeshQuery(mesh).signed_distance(points, chunk)


def _axes(grid: Grid, idx: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    gx, gy, gz = np.meshgrid(*idx, indexing="ij")
    return grid.coords(np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1))


def sdf_from_mesh(
    mesh: trimesh.Trimesh,
    grid: Grid,
    band: float | None = None,
    coarse: int = 4,
    chunk: int = CHUNK_POINTS,
) -> np.ndarray:
    """Sample the signed distance of ``mesh`` on ``grid``.

    With ``band=None`` every voxel gets its exact distance (computed in chunks). With a band, a
    coarse lattice (every ``coarse`` voxels) is queried first; cells provably farther than
    ``band`` from the surface (1-Lipschitz bound) are filled with ``+-band`` (sign preserved)
    and only the remaining voxels are queried exactly. Values with ``|phi| < band`` are exact.
    """
    nx, ny, nz = grid.shape
    phi = np.empty(grid.shape, np.float32)
    query = MeshQuery(mesh).signed_distance

    if band is None:
        rows = max(1, chunk // (ny * nz))
        for x0 in range(0, nx, rows):
            xs = np.arange(x0, min(nx, x0 + rows))
            phi[xs[0] : xs[-1] + 1] = query(_axes(grid, (xs, np.arange(ny), np.arange(nz))), chunk).reshape(len(xs), ny, nz)
        return phi

    if band <= 0 or coarse < 1:
        raise SDFError("band must be positive and coarse >= 1")
    nodes = [np.unique(np.minimum(np.arange(0, n + coarse, coarse), n - 1)) for n in grid.shape]
    cd = query(_axes(grid, tuple(nodes)), chunk).reshape(tuple(len(a) for a in nodes))
    diag = math.sqrt(2 * (coarse * grid.h) ** 2 + (coarse * grid.dz) ** 2)
    if min(len(a) for a in nodes) < 2:
        far = np.zeros(tuple(max(len(a) - 1, 1) for a in nodes), bool)
        sign = np.ones(far.shape, np.float32)
    else:
        corners = [cd[a : cd.shape[0] - 1 + a, b : cd.shape[1] - 1 + b, c : cd.shape[2] - 1 + c]
                   for a in (0, 1) for b in (0, 1) for c in (0, 1)]
        near = np.min(np.abs(corners), axis=0)
        neg = np.stack(corners) < 0
        # a cell is "far" only if it is out of reach AND all eight corners agree on the sign
        far = (near >= band + diag) & (neg.all(axis=0) | ~neg.any(axis=0))
        sign = np.where(neg[0], -1.0, 1.0).astype(np.float32)
    cells = far.shape

    def cell_of(n, nc):
        return np.minimum(np.arange(n) // coarse, nc - 1)

    ci, cj, ck = (cell_of(n, c) for n, c in zip(grid.shape, cells))
    # Fill slab by slab to avoid a second full-size temporary.
    for x in range(nx):
        far_xy = far[ci[x]][np.ix_(cj, ck)]
        sl = phi[x]
        sl[...] = np.where(far_xy, band * sign[ci[x]][np.ix_(cj, ck)], np.nan)
    # Exact distances for the voxels still unset, in chunks of slabs.
    x0 = 0
    while x0 < nx:
        x1, count = x0, 0
        while x1 < nx and (count == 0 or count < chunk):
            count += int(np.isnan(phi[x1]).sum())
            x1 += 1
        sub = phi[x0:x1]
        ii = np.argwhere(np.isnan(sub))
        if len(ii):
            ii[:, 0] += x0
            sub[np.isnan(sub)] = query(grid.coords(ii), chunk)
        x0 = x1
    return phi


def sample(phi: np.ndarray, grid: Grid, points: np.ndarray) -> np.ndarray:
    """Trilinear interpolation of ``phi`` at world ``points`` (N, 3); clamped outside the grid."""
    u = grid.to_index(points)
    hi = np.array(grid.shape) - 1
    u = np.clip(u, 0, hi)
    i0 = np.minimum(np.floor(u).astype(np.int64), np.maximum(hi - 1, 0))
    t = u - i0
    i1 = np.minimum(i0 + 1, hi)
    out = np.zeros(len(u), np.float64)
    for dx in (0, 1):
        wx = t[:, 0] if dx else 1 - t[:, 0]
        ix = i1[:, 0] if dx else i0[:, 0]
        for dy in (0, 1):
            wy = t[:, 1] if dy else 1 - t[:, 1]
            iy = i1[:, 1] if dy else i0[:, 1]
            for dz in (0, 1):
                wz = t[:, 2] if dz else 1 - t[:, 2]
                iz = i1[:, 2] if dz else i0[:, 2]
                out += wx * wy * wz * phi[ix, iy, iz]
    return out


@dataclass(frozen=True)
class ExportReport:
    n_components: int
    kept_volume_mm3: float
    dropped_volume_mm3: float
    n_faces: int


def sdf_to_mesh(
    phi: np.ndarray,
    grid: Grid,
    max_dropped_mm3: float = MAX_DROPPED_MM3,
) -> tuple[trimesh.Trimesh, ExportReport]:
    """Marching cubes at phi = 0 with the anisotropic voxel size.

    The field is padded with ``EXPORT_PAD`` empty voxels on every side. Only the largest outward
    shell (plus the internal voids lying inside it) is kept; the volume of other solid shells is
    reported as dropped and, above ``max_dropped_mm3``, raises ``SDFError``. No decimation.
    """
    if not np.isfinite(phi).all():
        raise SDFError("phi contains NaN or inf")
    far = float(max(np.abs(phi).max(), 1.0))
    padded = np.pad(phi, EXPORT_PAD, mode="constant", constant_values=far)
    if not (padded < 0).any():
        raise SDFError("field has no interior (nothing below zero)")
    verts, faces, _, _ = measure.marching_cubes(padded, level=0.0, spacing=grid.spacing)
    verts = verts + np.asarray(grid.origin) - EXPORT_PAD * np.asarray(grid.spacing)
    mesh = trimesh.Trimesh(verts, faces, process=False)
    parts = mesh.split(only_watertight=False)  # faces grouped by shared vertices
    vols = np.array([p.volume for p in parts])
    if vols[np.argmax(np.abs(vols))] < 0:  # make the dominant shell outward-facing
        for p in parts:
            p.invert()
        vols = -vols
    # Outward shells (volume > 0) are solids; inward shells (volume < 0) are internal voids.
    solids = np.flatnonzero(vols > 0)
    keep = int(solids[np.argmax(vols[solids])])
    dropped = float(vols[solids].sum() - vols[keep])
    if dropped > max_dropped_mm3:
        raise SDFError(f"dropped {dropped:.1f} mm^3 of disconnected fragments (> {max_dropped_mm3:g})")
    # Voids belong to the kept solid if they lie inside it; a void inside a dropped fragment goes with it.
    kept = [parts[keep]]
    voids = [p for p in parts if p.volume < 0]
    if voids:
        probe = MeshQuery(parts[keep])
        kept += [p for p in voids if probe.winding(p.vertices[:1])[0] > 0.5]
    best = trimesh.util.concatenate(kept) if len(kept) > 1 else kept[0]
    return best, ExportReport(len(parts), float(best.volume), dropped, len(best.faces))
