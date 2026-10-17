"""Last mesh -> sampled fields for the GeometryGraph (plan M0.3, ADR 0003).

Public API
----------
``last_sdf_field(mesh, store, params)``
    :class:`~shoegen.graph.SampledField` of the last: libigl signed distance (pseudo-normal
    sign), two-level sampling (fine 0.5 mm in a 6 mm narrow band, coarse 2 mm over
    bbox + 30 mm) through :func:`shoegen.graph.sample_two_level`.
``footprint_field(proj, store, params)``
    :class:`~shoegen.graph.Grid2D`: exact 2D signed distance to the XY outline of the last
    (negative inside), 0.5 mm grid, >= 30 mm past the outline. Input of ``Extrude2D``.
``height_map(proj, store, params)``
    :class:`HeightMap`: the lowest z of the last at each XY on a 0.5 mm grid (nearest-outline
    edge value outside the footprint, hence continuous).
``height_surface_field(h, offset, store, params)``
    :class:`~shoegen.graph.HeightSurfaceField`: signed distance to ``z = h(x, y) + offset``,
    two-level sampled. Exact (a real millimetre distance), so it may be offset.
``build_last_fields(mesh, cache_dir, params)``
    All of the above, cached under ``output/cache/fields/`` keyed by the last sha256, the
    mesh content and the sampling parameters; a hit skips every computation.

Only the narrow band is sampled finely: the producers in :mod:`shoegen.graph.sampled`
evaluate the distance function on brick centres and then on candidate bricks, in chunks, so
memory stays proportional to the band (plan 1.1).

Height surface sheet: the sheet is triangulated over the footprint bbox plus
``sheet_margin_mm`` (default 30 mm) and sampled with ``bbox = sheet bbox``. The field is the
distance to this finite sheet: inside the sheet's XY rectangle it is exact; beyond it, it
grows with the distance to the sheet edge (rule 3 of :mod:`shoegen.graph.sampled` then
holds), so consumers must stay within the footprint plus the sheet margin.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from shoegen.graph.arrays import NpyDirectoryStore
from shoegen.graph.nodes import Grid2D, HeightSurfaceField, SampledField
from shoegen.graph.sampled import sample_grid2d, sample_two_level
from shoegen.last.heightsheet import build_height_sheet
from shoegen.last.mesh import LastMesh
from shoegen.last.projection import LastProjection
from shoegen.last.sdf import MeshSDF

DEFAULT_CACHE_DIR = Path("output/cache/fields")
CACHE_VERSION = 1
_SDF_CHUNK = 262_144


@dataclass(frozen=True)
class FieldParams:
    """Sampling parameters (all lengths in mm). Part of the cache key."""

    fine_h: float = 0.5
    brick_size: int = 8
    narrow_band_mm: float = 6.0
    coarse_h: float = 2.0
    coarse_margin_mm: float = 30.0
    grid2d_h: float = 0.5
    grid2d_margin_mm: float = 30.0
    sheet_margin_mm: float = 30.0
    sheet_base_h: float = 1.0
    sheet_tolerance_mm: float = 0.02
    sheet_min_edge_mm: float = 0.03
    sign: str = "pseudonormal"

    def sampling(self, mesh: LastMesh, kind: str, **extra) -> dict:
        """Scalar dict stored in the node's ``sampling`` (and so hashed into the graph)."""
        d: dict = {
            "kind": kind,
            "mesh_sha256": mesh.mesh_sha256,
            "tessellation_tol_mm": mesh.tessellation[0],
            "tessellation_angular_rad": mesh.tessellation[1],
            "fine_h": self.fine_h,
            "brick_size": self.brick_size,
            "narrow_band_mm": self.narrow_band_mm,
            "coarse_h": self.coarse_h,
            "coarse_margin_mm": self.coarse_margin_mm,
            "sign": self.sign,
        }
        d.update(extra)
        return d


# ---------------------------------------------------------------------- signed distance


def mesh_sdf_fn(
    mesh: LastMesh, chunk: int = _SDF_CHUNK, sign: str = "pseudonormal"
) -> Callable[[np.ndarray], np.ndarray]:
    """Vectorised signed distance to the last (libigl AABB), chunked.

    ``last_to_mesh`` has verified that the mesh is closed, manifold and consistently
    oriented, so the angle-weighted pseudo-normal sign is exact; it is ~20x faster than the
    exact generalised winding number (``sign="winding_number"``, ~4 000 points/s on a
    10k-triangle mesh), which stays available for meshes not produced by ``last_to_mesh``.
    See :class:`shoegen.last.sdf.MeshSDF`.
    """
    return MeshSDF(mesh.vertices, mesh.faces, sign=sign, chunk=chunk)


def last_sdf_field(
    mesh: LastMesh, store: NpyDirectoryStore, params: FieldParams | None = None
) -> SampledField:
    """Sample the signed distance of ``mesh`` into a :class:`SampledField` node."""
    p = params or FieldParams()
    grid = sample_two_level(
        mesh_sdf_fn(mesh, sign=p.sign),
        mesh.bbox_min,
        mesh.bbox_max,
        store,
        fine_h=p.fine_h,
        brick_size=p.brick_size,
        narrow_band_mm=p.narrow_band_mm,
        coarse_h=p.coarse_h,
        coarse_margin_mm=p.coarse_margin_mm,
    )
    return SampledField(
        source_sha256=mesh.source_sha256,
        sampling=p.sampling(mesh, "last-sdf"),
        grid=grid,
    )


# ---------------------------------------------------------------------- 2D products


def _grid_axis(lo: float, hi: float, h: float, pad: float) -> tuple[float, int]:
    """Origin (a multiple of h) and sample count covering [lo - pad, hi + pad]."""
    import math

    origin = h * math.floor((lo - pad) / h)
    return origin, max(math.ceil((hi + pad - origin) / h) + 1, 2)


def footprint_field(
    proj: LastProjection,
    store: NpyDirectoryStore,
    params: FieldParams | None = None,
    *,
    mesh: LastMesh,
) -> Grid2D:
    """Exact 2D signed distance of the last's XY outline as a :class:`Grid2D` (<0 inside)."""
    p = params or FieldParams()
    return sample_grid2d(
        proj.footprint_sdf,
        proj.bbox_min,
        proj.bbox_max,
        store,
        source_sha256=mesh.source_sha256,
        sampling=p.sampling(mesh, "last-footprint", h=p.grid2d_h, margin_mm=p.grid2d_margin_mm),
        h=p.grid2d_h,
        margin_mm=p.grid2d_margin_mm,
    )


@dataclass(frozen=True)
class HeightMap:
    """Bottom height ``h(x, y)`` of the last on a regular grid (plan M0.3).

    ``values[i, j] = h(origin + (i, j) * h)`` as float32, stored in the array store under
    ``values_sha256``. ``exact`` (not serialised) evaluates the unsampled height from the
    mesh; :func:`height_surface_field` uses it to refine the sheet where the 0.5 mm grid
    is too coarse, and falls back to bilinear interpolation of ``values`` without it.
    """

    source_sha256: str
    sampling: dict
    origin: tuple[float, float]
    h: float
    bbox_min: tuple[float, float]
    bbox_max: tuple[float, float]
    values_sha256: str
    values: np.ndarray = field(compare=False, repr=False)
    exact: Callable[[np.ndarray], np.ndarray] | None = field(
        default=None, compare=False, repr=False
    )

    def sample(self, xy: np.ndarray) -> np.ndarray:
        """Bilinear interpolation of the grid, clamped to its extent."""
        nx, ny = self.values.shape
        u = (np.asarray(xy, dtype=np.float64) - np.asarray(self.origin)) / self.h
        u[:, 0] = np.clip(u[:, 0], 0.0, nx - 1.0)
        u[:, 1] = np.clip(u[:, 1], 0.0, ny - 1.0)
        i = np.minimum(np.floor(u[:, 0]).astype(np.int64), nx - 2)
        j = np.minimum(np.floor(u[:, 1]).astype(np.int64), ny - 2)
        fx, fy = u[:, 0] - i, u[:, 1] - j
        v = self.values
        a = v[i, j] * (1 - fx) + v[i + 1, j] * fx
        b = v[i, j + 1] * (1 - fx) + v[i + 1, j + 1] * fx
        return (a * (1 - fy) + b * fy).astype(np.float64)

    def to_json(self) -> dict:
        return {
            "source_sha256": self.source_sha256,
            "sampling": self.sampling,
            "origin": list(self.origin),
            "h": self.h,
            "bbox_min": list(self.bbox_min),
            "bbox_max": list(self.bbox_max),
            "values_sha256": self.values_sha256,
        }

    @staticmethod
    def from_json(obj: dict, store: NpyDirectoryStore, exact=None) -> HeightMap:
        return HeightMap(
            source_sha256=obj["source_sha256"],
            sampling=dict(obj["sampling"]),
            origin=tuple(obj["origin"]),  # type: ignore[arg-type]
            h=float(obj["h"]),
            bbox_min=tuple(obj["bbox_min"]),  # type: ignore[arg-type]
            bbox_max=tuple(obj["bbox_max"]),  # type: ignore[arg-type]
            values_sha256=obj["values_sha256"],
            values=np.asarray(store.get(obj["values_sha256"])),
            exact=exact,
        )


def height_map(
    proj: LastProjection,
    store: NpyDirectoryStore,
    params: FieldParams | None = None,
    *,
    mesh: LastMesh,
) -> HeightMap:
    """Lowest z of the last at every node of a 0.5 mm grid over the footprint + margin."""
    p = params or FieldParams()
    axes = [
        _grid_axis(proj.bbox_min[a], proj.bbox_max[a], p.grid2d_h, p.grid2d_margin_mm)
        for a in range(2)
    ]
    origin = np.array([o for o, _ in axes])
    shape = tuple(n for _, n in axes)
    gi = np.indices(shape, dtype=np.int64).reshape(2, -1).T
    pts = origin[None, :] + gi.astype(np.float64) * p.grid2d_h
    values = proj.height(pts).astype(np.float32).reshape(shape)
    sha = store.put(values)
    return HeightMap(
        source_sha256=mesh.source_sha256,
        sampling=p.sampling(mesh, "last-height", h=p.grid2d_h, margin_mm=p.grid2d_margin_mm),
        origin=(float(origin[0]), float(origin[1])),
        h=float(p.grid2d_h),
        bbox_min=proj.bbox_min,
        bbox_max=proj.bbox_max,
        values_sha256=sha,
        values=values,
        exact=proj.height,
    )


def height_surface_field(
    h: HeightMap,
    offset: float,
    store: NpyDirectoryStore,
    params: FieldParams | None = None,
) -> HeightSurfaceField:
    """Signed distance to ``z = h(x, y) + offset`` as a two-level :class:`HeightSurfaceField`."""
    p = params or FieldParams()
    height_fn = h.exact if h.exact is not None else h.sample
    m = p.sheet_margin_mm
    sheet = build_height_sheet(
        height_fn,
        (h.bbox_min[0] - m, h.bbox_min[1] - m),
        (h.bbox_max[0] + m, h.bbox_max[1] + m),
        float(offset),
        base_h=p.sheet_base_h,
        tolerance_mm=p.sheet_tolerance_mm,
        min_edge_mm=p.sheet_min_edge_mm,
    )
    grid = sample_two_level(
        sheet.signed_distance,
        sheet.bbox_min,
        sheet.bbox_max,
        store,
        fine_h=p.fine_h,
        brick_size=p.brick_size,
        narrow_band_mm=p.narrow_band_mm,
        coarse_h=p.coarse_h,
        coarse_margin_mm=p.coarse_margin_mm,
    )
    sampling = dict(h.sampling)
    sampling.update(
        {
            "kind": "last-height-surface",
            "offset_mm": float(offset),
            "sheet_margin_mm": m,
            "sheet_tolerance_mm": p.sheet_tolerance_mm,
            "height_values_sha256": h.values_sha256,
            "sheet_height": "exact" if h.exact is not None else "grid-bilinear",
        }
    )
    return HeightSurfaceField(source_sha256=h.source_sha256, sampling=sampling, grid=grid)


# ---------------------------------------------------------------------- cached bundle


def _key(*parts) -> str:
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class LastFields:
    """All fields of one last, backed by an :class:`NpyDirectoryStore`."""

    store: NpyDirectoryStore
    params: FieldParams
    mesh: LastMesh
    sampled_field: SampledField
    footprint: Grid2D
    height_map: HeightMap
    cache_hit: bool
    _surfaces: dict = field(default_factory=dict, repr=False)

    def height_surface(self, offset: float) -> HeightSurfaceField:
        """Height surface field ``z = h + offset`` (cached like the other fields)."""
        key = _key(
            "height-surface",
            CACHE_VERSION,
            self.mesh.source_sha256,
            self.mesh.mesh_sha256,
            self.mesh.tessellation,
            self.params,
            float(offset),
        )
        if key in self._surfaces:
            return self._surfaces[key]
        node = _read_node(self.store, key, HeightSurfaceField)
        if node is None:
            node = height_surface_field(self.height_map, offset, self.store, self.params)
            _write_json(self.store, key, node.model_dump(mode="json"))
        self._surfaces[key] = node
        return node


def _json_path(store: NpyDirectoryStore, key: str) -> Path:
    return store.root / f"{key}.json"


def _write_json(store: NpyDirectoryStore, key: str, obj: dict) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    path = _json_path(store, key)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True))
    tmp.replace(path)


def _arrays_present(store: NpyDirectoryStore, shas: tuple[str, ...]) -> bool:
    return all(store.path_for(s).is_file() for s in shas)


def _read_node(store: NpyDirectoryStore, key: str, cls):
    path = _json_path(store, key)
    if not path.is_file():
        return None
    try:
        node = cls.model_validate(json.loads(path.read_text()))
    except (ValueError, OSError):
        return None
    shas = node.grid.array_shas() if hasattr(node, "grid") else ()
    return node if _arrays_present(store, shas) else None


def fields_cache_key(mesh: LastMesh, params: FieldParams) -> str:
    """Cache key: source sha256, mesh content, tessellation and every sampling parameter."""
    return _key(
        "fields", CACHE_VERSION, mesh.source_sha256, mesh.mesh_sha256, mesh.tessellation, params
    )


def build_last_fields(
    mesh: LastMesh,
    cache_dir: str | Path | None = None,
    params: FieldParams | None = None,
    *,
    use_cache: bool = True,
) -> LastFields:
    """SDF field, footprint grid and height map of ``mesh``, cached on disk.

    The key covers the source sha256, the mesh content (so a different normalisation
    misses), the tessellation and every sampling parameter. A hit returns the stored
    nodes without touching the mesh; the projection needed for exact height-surface
    refinement is built lazily.
    """
    p = params or FieldParams()
    store = NpyDirectoryStore(cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR)
    key = fields_cache_key(mesh, p)
    state: dict = {}

    def lazy_height(xy: np.ndarray) -> np.ndarray:
        if "proj" not in state:
            state["proj"] = LastProjection(mesh)
        return state["proj"].height(xy)

    if use_cache:
        path = _json_path(store, key)
        if path.is_file():
            try:
                obj = json.loads(path.read_text())
                sampled = SampledField.model_validate(obj["sampled_field"])
                footprint = Grid2D.model_validate(obj["footprint"])
                shas = (*sampled.grid.array_shas(), footprint.values_sha256)
                shas += (obj["height_map"]["values_sha256"],)
                if _arrays_present(store, shas):
                    hm = HeightMap.from_json(obj["height_map"], store, exact=lazy_height)
                    return LastFields(store, p, mesh, sampled, footprint, hm, True)
            except (ValueError, KeyError, OSError):
                pass  # unreadable cache entry: rebuild
    sampled = last_sdf_field(mesh, store, p)
    proj = LastProjection(mesh)
    state["proj"] = proj
    footprint = footprint_field(proj, store, p, mesh=mesh)
    hm = height_map(proj, store, p, mesh=mesh)
    _write_json(
        store,
        key,
        {
            "sampled_field": sampled.model_dump(mode="json"),
            "footprint": footprint.model_dump(mode="json"),
            "height_map": hm.to_json(),
        },
    )
    return LastFields(store, p, mesh, sampled, footprint, replace(hm, exact=lazy_height), False)
