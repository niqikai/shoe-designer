"""Zoned ease field e(x), collar truncation and the cavity field phi_C (#10 S4).

Design: docs/design/issue-10-cavity-rebuild.md §4.1 (ease), §4.3 (collar), §1.2 (phi_C).

* :class:`EaseField` - the continuous ease e as a function of the last's normalised coordinates
  (length fraction u, normal class up/side/down). Zones and numbers come from a TOML file
  (``config/ease_zones.toml``). It is callable as ``f(points_last, normals_last)`` so it can be
  passed straight to :func:`shoe_designer.pose.fit_pose` / ``gap_map`` as ``ease``.
* :func:`build_cavity` - ``phi_C = phi_L - e(x)`` on a grid (``phi_L`` = signed distance of the
  posed last), then :func:`open_collar` makes the cavity continue upward through the collar so no
  thin film is left at the mouth. Returns a :class:`Cavity` (``phi`` array + ``Grid``).

Conventions as in :mod:`shoe_designer.sdf`: ``phi < 0`` inside, arrays ``[ix, iy, iz]``, mm.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import igl
import numpy as np
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree

from .sdf import Grid, sdf_from_mesh

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "ease_zones.toml"
FACE_CLASSES = ("up", "side", "down")
T_FILL_MM = 1.5  # design doc §1.5: hard-fill wall, also the film threshold of the collar rule
COLLAR_REACH_MM = 10.0  # §4.3-2: truncation only inside the collar ring dilated by this much


class EaseError(Exception):
    pass


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


@dataclass(frozen=True)
class Zone:
    name: str
    u: tuple[float, float]  # length-fraction interval (0 heel, 1 toe)
    faces: tuple[str, ...]  # subset of FACE_CLASSES
    value: float  # mm


@dataclass(frozen=True)
class EaseConfig:
    base: float
    zones: tuple[Zone, ...]
    fdm_shrink: float
    transition_mm: float
    smooth_mm: float
    up_ramp: tuple[float, float]
    down_ramp: tuple[float, float]
    insole_mm: float

    @classmethod
    def from_toml(cls, path: str | Path | None = None) -> EaseConfig:
        path = Path(path) if path else DEFAULT_CONFIG
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except FileNotFoundError:
            raise EaseError(f"ease config not found: {path}") from None
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> EaseConfig:
        g, n = raw.get("global", {}), raw.get("normals", {})
        insole = float(raw.get("floor", {}).get("insole_mm", 0.0))
        zones = []
        for z in raw.get("zone", []):
            faces = tuple(z.get("faces", FACE_CLASSES))
            bad = set(faces) - set(FACE_CLASSES)
            u = tuple(float(v) for v in z["u"])
            if bad or len(u) != 2 or not 0.0 <= u[0] < u[1] <= 1.0:
                raise EaseError(f"bad zone {z.get('name')!r}: faces {sorted(bad)} / u {u}")
            zones.append(Zone(z["name"], u, faces, float(z["value"]) + (insole if z.get("insole") else 0.0)))
        if "base" not in raw:
            raise EaseError("config needs a [base] value")
        up, down = (tuple(float(v) for v in n.get(k, (0.5, 0.8))) for k in ("up", "down"))
        if len(up) != 2 or len(down) != 2:
            raise EaseError("[normals] up/down must be [lo, hi]")
        if not (0.0 <= up[0] < up[1] <= 1.0 and 0.0 <= down[0] < down[1] <= 1.0) or up[0] < 0.5 or down[0] < 0.5:
            raise EaseError("normal ramps need 0.5 <= lo < hi <= 1 so that up and down never overlap")
        return cls(float(raw["base"]["value"]), tuple(zones), float(g.get("fdm_shrink_mm", 0.0)),
                   float(g.get("transition_mm", 10.0)), float(g.get("smooth_mm", 0.0)), up, down, insole)

    @property
    def e_max(self) -> float:
        return max([self.base] + [z.value for z in self.zones]) + self.fdm_shrink


class EaseField:
    """e(u, normal) = FDM shrink + layered zone blend. ``x_range`` is the last's x extent (last frame)."""

    def __init__(self, config: EaseConfig, x_range: tuple[float, float]):
        if not x_range[1] > x_range[0]:
            raise EaseError("x_range must have positive length")
        self.config = config
        self.x0 = float(x_range[0])
        self.length = float(x_range[1] - x_range[0])

    @classmethod
    def for_last(cls, last: trimesh.Trimesh, config: EaseConfig | str | Path | None = None) -> EaseField:
        if not isinstance(config, EaseConfig):
            config = EaseConfig.from_toml(config)
        b = last.bounds
        return cls(config, (float(b[0, 0]), float(b[1, 0])))

    def face_weights(self, nz: np.ndarray) -> dict[str, np.ndarray]:
        c = self.config
        up = smoothstep((nz - c.up_ramp[0]) / (c.up_ramp[1] - c.up_ramp[0]))
        down = smoothstep((-nz - c.down_ramp[0]) / (c.down_ramp[1] - c.down_ramp[0]))
        return {"up": up, "down": down, "side": np.clip(1.0 - up - down, 0.0, 1.0)}

    def zone_weight(self, zone: Zone, u: np.ndarray, fw: dict[str, np.ndarray]) -> np.ndarray:
        """Membership of ``zone`` in [0, 1]: smoothstep ramps centred on interior edges, none at u = 0 or 1."""
        tw = self.config.transition_mm / self.length
        w = np.ones_like(u)
        if zone.u[0] > 0.0:
            w = w * smoothstep((u - zone.u[0]) / tw + 0.5)
        if zone.u[1] < 1.0:
            w = w * smoothstep((zone.u[1] - u) / tw + 0.5)
        return w * np.clip(sum(fw[f] for f in zone.faces), 0.0, 1.0)

    def __call__(self, points: np.ndarray, normals: np.ndarray) -> np.ndarray:
        """e (mm, shrink included) at last-frame ``points`` (N, 3) with unit ``normals`` (N, 3)."""
        points, normals = np.asarray(points, float), np.asarray(normals, float)
        u = (points[:, 0] - self.x0) / self.length
        fw = self.face_weights(normals[:, 2])
        e = np.full(len(points), self.config.base)
        for z in self.config.zones:
            w = self.zone_weight(z, u, fw)
            e = e + w * (z.value - e)
        return e + self.config.fdm_shrink


# --------------------------------------------------------------------------- grids


def extend_grid(grid: Grid, lo, hi) -> Grid:
    """``grid`` grown (voxel-aligned, same spacing) to also cover [lo, hi]. The old grid sits at index
    offset ``round((old.origin - new.origin) / spacing)`` inside the new one."""
    sp = np.asarray(grid.spacing)
    o = np.asarray(grid.origin)
    far = o + (np.asarray(grid.shape) - 1) * sp
    n_lo = np.maximum(0, np.ceil((o - np.asarray(lo, float)) / sp - 1e-9)).astype(int)
    n_hi = np.maximum(0, np.ceil((np.asarray(hi, float) - far) / sp - 1e-9)).astype(int)
    shape = tuple(int(s + a + b) for s, a, b in zip(grid.shape, n_lo, n_hi))
    return Grid(tuple(float(v) for v in o - n_lo * sp), grid.h, grid.dz, shape)


# --------------------------------------------------------------------------- collar truncation


@dataclass
class CollarReport:
    ring_columns: int
    extruded_columns: int
    film_columns: int  # extruded columns where a solid sliver (0 < gap < t_fill) sat under the cap
    through_columns: int  # extruded columns whose cavity already reached z_T (last stands in the mouth)
    z_top_min: float | None  # lowest height from which a column was opened upward
    max_gap_extruded: float  # largest (z_T - z_c) among extruded columns, < t_fill by construction
    min_gap_kept: float | None  # smallest gap among ring columns that keep a roof (>= t_fill)
    kept_gap_hist: dict = field(default_factory=dict)  # roof thickness bins (mm) of the kept ring columns

    def to_dict(self) -> dict:
        return {k: (None if v is None else (round(v, 3) if isinstance(v, float) else v))
                for k, v in self.__dict__.items()}


def _column_xy(grid: Grid) -> np.ndarray:
    nx, ny, _ = grid.shape
    gx, gy = np.meshgrid(grid.origin[0] + np.arange(nx) * grid.h, grid.origin[1] + np.arange(ny) * grid.h, indexing="ij")
    return np.stack([gx.ravel(), gy.ravel()], axis=1)


def collar_region(grid: Grid, collar, reach: float = COLLAR_REACH_MM) -> np.ndarray:
    """(nx, ny) bool: columns inside the collar ring or within ``reach`` mm (xy) of the rim."""
    xy = _column_xy(grid)
    near = cKDTree(collar.rim[:, :2]).query(xy)[0] <= reach
    return (collar.inside(xy) | near).reshape(grid.shape[:2])


def open_collar(phi_c: np.ndarray, grid: Grid, collar, t_fill: float = T_FILL_MM,
                reach: float = COLLAR_REACH_MM) -> tuple[np.ndarray, CollarReport]:
    """Make the cavity continue upward through the collar (§4.3-2).

    For every column (x, y) in the collar region: ``z_c`` = highest zero crossing of ``phi_c`` that lies
    below ``z_T(x, y)``. If ``z_T - z_c < t_fill`` the column is opened from ``z_c`` to the top of the
    grid. Columns with a thicker roof keep it (a real wall, e.g. the heel counter, or the vamp). The
    opened region at each height is the footprint of the opened columns, merged into the field as
    a true 2-D signed distance (vertical walls at the footprint edge), so the result is still a
    distance-like field, and the column rule introduces no thin sheet at the collar.
    """
    nx, ny, nz = grid.shape
    zt = collar.z_t(_column_xy(grid)).reshape(nx, ny)
    region = collar_region(grid, collar, reach)
    if not region.any():
        raise EaseError("collar region is empty: the grid does not cover the collar rim")
    zs = grid.origin[2] + np.arange(nz) * grid.dz
    if zt[region].max() + 3 * grid.dz > zs[-1]:
        raise EaseError("grid does not extend above the collar; use a taller grid (extend_grid)")

    has, gap, kc, through = _roof_gap(phi_c, grid, zt)
    ring = region & has
    opened = ring & (gap < t_fill)

    out = phi_c.copy()
    if opened.any():
        def footprint_sdf(foot):
            if foot.all():  # no background voxel for the EDT: a constant field
                return np.full(foot.shape, -0.5 * grid.h * foot.shape[0], np.float32)
            d = np.where(foot, -(ndimage.distance_transform_edt(foot, sampling=grid.h) - 0.5 * grid.h),
                         ndimage.distance_transform_edt(~foot, sampling=grid.h) - 0.5 * grid.h)
            return d.astype(np.float32)

        k_lo, k_hi = int(kc[opened].min()), int(kc[opened].max())
        for k in range(k_lo, k_hi + 1):
            out[:, :, k] = np.minimum(out[:, :, k], footprint_sdf(opened & (kc <= k)))
        if k_hi + 1 < nz:  # above the last opened column the footprint no longer changes
            out[:, :, k_hi + 1:] = np.minimum(out[:, :, k_hi + 1:], footprint_sdf(opened)[:, :, None])
    kept = ring & ~opened
    through = opened & through
    rep = CollarReport(
        ring_columns=int(ring.sum()), extruded_columns=int(opened.sum()),
        # film: gap > 1 voxel = a real sliver, not just the voxel step
        film_columns=int((opened & ~through & (gap > grid.dz)).sum()),
        through_columns=int(through.sum()),
        z_top_min=float(zs[kc[opened]].min()) if opened.any() else None,
        max_gap_extruded=float(gap[opened].max()) if opened.any() else 0.0,
        min_gap_kept=float(gap[kept].min()) if kept.any() else None,
        kept_gap_hist={f"{a}-{b}": int(((gap[kept] >= a) & (gap[kept] < b)).sum())
                       for a, b in ((t_fill, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 10.0), (10.0, 1e9))},
    )
    return out, rep


def _roof_gap(phi: np.ndarray, grid: Grid, zt: np.ndarray):
    """Per column: (has cavity below z_T, roof gap z_T - z_c, k of the highest cavity voxel below z_T, open flag).

    z_c is the sub-voxel zero crossing above that voxel. ``open`` = the voxel just above z_T is cavity as
    well, i.e. the cavity already passes through z_T (gap <= 0).
    """
    nx, ny, nz = grid.shape
    zs = grid.origin[2] + np.arange(nz) * grid.dz
    cav = (phi < 0) & (zs[None, None, :] < zt[:, :, None])
    has = cav.any(axis=2)
    kc = nz - 1 - np.argmax(cav[:, :, ::-1], axis=2)
    ix, iy = np.indices((nx, ny))
    k1 = np.minimum(kc + 1, nz - 1)
    p0, p1 = phi[ix, iy, kc].astype(np.float64), phi[ix, iy, k1].astype(np.float64)
    top = np.where(p1 < 0, zs[k1], zs[kc] + grid.dz * p0 / np.where(p0 - p1 == 0, 1.0, p0 - p1))
    gap = np.where(has, zt - top, np.inf)
    return has, gap, kc, has & (p1 < 0)


def find_films(phi: np.ndarray, grid: Grid, collar, t_fill: float = T_FILL_MM,
               reach: float = COLLAR_REACH_MM) -> np.ndarray:
    """(nx, ny) bool: collar-region columns where a sliver of material thinner than ``t_fill`` would
    remain between the cavity top and the cap z_T (0 < gap < t_fill). Must be empty after
    :func:`open_collar`."""
    zt = collar.z_t(_column_xy(grid)).reshape(grid.shape[:2])
    has, gap, _, through = _roof_gap(phi, grid, zt)
    return collar_region(grid, collar, reach) & has & ~through & (gap < t_fill)


# --------------------------------------------------------------------------- phi_C


@dataclass
class Cavity:
    phi: np.ndarray  # float32 phi_C (< 0 inside the cavity), cavity open upward through the collar
    grid: Grid
    ease: EaseField
    ease_grid: np.ndarray  # float32, e(x) on the band around the last (e_far elsewhere)
    phi_last: np.ndarray  # float32 phi_L of the posed last (clamped to +-band beyond the band)
    collar: CollarReport | None
    report: dict = field(default_factory=dict)


def build_cavity(
    last: trimesh.Trimesh,
    transform: np.ndarray,
    collar,
    grid: Grid,
    config: EaseConfig | str | Path | None = None,
    *,
    t_fill: float = T_FILL_MM,
    reach: float = COLLAR_REACH_MM,
    band_margin: float = 4.0,
) -> Cavity:
    """Cavity field phi_C = phi_L - e(x), open upward through the collar. Input of S5 (closure) and S6.

    ``last``: last mesh in its own (canonical) frame. ``transform``: 4x4 rigid last -> template frame
    (``PoseResult.transform`` of S3). ``collar``: S2's ``Envelope.collar`` (anything with ``rim``,
    ``z_t(xy)`` and ``inside(xy)``). ``grid``: shared grid (e.g. ``Envelope.grid`` extended with
    :func:`extend_grid` so it covers the posed last and rises >= 3 voxels above the highest collar
    point). ``config``: :class:`EaseConfig`, a TOML path, or None for ``config/ease_zones.toml``.

    ``phi_L`` is an exact distance within ``e_max + band_margin`` of the last and +-band beyond it; there
    ``e`` is set to its largest value, which cannot move the zero level. Inside the band, ``e`` is the
    zone ease at the closest point of the last, lightly smoothed (``smooth_mm``). ``phi_C`` is exactly
    ``phi_L - e`` only up to the Lipschitz factor of ``e``: a smoothstep of height D over the 10 mm
    transition has peak slope 1.5*D/10 (0.3 for the 2 mm instep step, a bit less after the gaussian
    smoothing), so the wall's normal distance differs from ``e`` by about 1 - 1/sqrt(1 + slope^2),
    i.e. up to ~4% (~0.1 mm) at the steepest point and nothing elsewhere.

    Returns a :class:`Cavity` with ``phi`` (float32, shape ``grid.shape``), ``grid`` and a report.
    """
    cfg = config if isinstance(config, EaseConfig) else EaseConfig.from_toml(config)
    transform = np.asarray(transform, float)
    ease = EaseField.for_last(last, cfg)
    band = cfg.e_max + band_margin
    posed = last.copy()
    posed.apply_transform(transform)

    phi_l = sdf_from_mesh(posed, grid, band=band + 2.0)
    inner = np.abs(phi_l) < band
    ijk = np.argwhere(inner)
    pts = grid.coords(ijk)

    aabb = igl.AABB()
    v = np.ascontiguousarray(posed.vertices, np.float64)
    f = np.ascontiguousarray(posed.faces, np.int64)
    aabb.init(v, f)
    _, tri, cp = aabb.squared_distance(v, f, np.ascontiguousarray(pts, np.float64))
    r = transform[:3, :3]
    cp_last = (cp - transform[:3, 3]) @ r  # inverse rigid map (R orthogonal)
    n_last = last.face_normals[tri]
    e_vals = ease(cp_last, n_last)

    e = np.full(grid.shape, cfg.e_max, np.float32)
    if cfg.smooth_mm > 0:
        sig = (cfg.smooth_mm / grid.h, cfg.smooth_mm / grid.h, cfg.smooth_mm / grid.dz)
        num = np.zeros(grid.shape, np.float32)
        num[tuple(ijk.T)] = e_vals
        den = inner.astype(np.float32)
        num = ndimage.gaussian_filter(num, sig, truncate=3.0)
        den = ndimage.gaussian_filter(den, sig, truncate=3.0)
        sm = num[tuple(ijk.T)] / np.maximum(den[tuple(ijk.T)], 1e-6)
        e[tuple(ijk.T)] = sm
    else:
        e[tuple(ijk.T)] = e_vals
    # phi_L is +-(band+2) beyond the band; e is at most e_max there, so the sign survives.
    phi_c = (phi_l - e).astype(np.float32)
    phi_open, crep = open_collar(phi_c, grid, collar, t_fill, reach)
    zs = grid.origin[2] + np.arange(grid.shape[2]) * grid.dz
    zt = collar.z_t(_column_xy(grid)).reshape(grid.shape[:2])

    inside_e = e[tuple(ijk[np.abs(phi_l[tuple(ijk.T)]) < cfg.e_max + 1.0].T)]
    report = {
        "grid": {"shape": list(grid.shape), "h": grid.h, "dz": grid.dz},
        "e_range_mm": [round(float(inside_e.min()), 3), round(float(inside_e.max()), 3)] if inside_e.size else None,
        "e_max_config_mm": cfg.e_max,
        "fdm_shrink_mm": cfg.fdm_shrink,
        "insole_mm": cfg.insole_mm,
        "zones": {z.name: {"u": list(z.u), "faces": list(z.faces), "value": z.value} for z in cfg.zones} | {
            "base": {"value": cfg.base}},
        "collar": crep.to_dict(),
        # below z_T only: the opened chimneys run to the top of the grid, so the full count would depend on grid height
        "cavity_volume_below_collar_mm3": float(
            ((phi_open < 0) & (zs[None, None, :] < zt[:, :, None])).sum() * grid.h * grid.h * grid.dz),
    }
    return Cavity(phi_open, grid, ease, e, phi_l, crep, report)
