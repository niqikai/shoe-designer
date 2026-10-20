"""Strut-lattice weight reduction on phi_shoe (#14, part A: sole lattice; shared machinery for part B).

Input is the composed ``phi_shoe`` (S6) and the cavity field; nothing of S1-S6 is changed. The lattice lives on a
*fine* grid (``voxel_mm`` <= thinnest strut / 4, 0.3 mm for a 1.2 mm strut) covering only the bounding box of the
shoe; the coarse fields are resampled trilinearly one x-plane at a time, so no full-size temporary other than the
final fine field is allocated. Everything is a pure function of the inputs and the config: no random numbers.

Sole zone ``Z`` (``phi_Z < 0`` inside), built from the coarse fields::

    phi_Z = max( lateral(x, y),                      columns that can hold a lattice core (open to the outside at the sides)
                 z_bottom + skin_outsole - z,        solid skin above the outsole
                 z - (z_top - skin_cavity),          z_top = min(cavity floor, top of the solid run) of the column
                 skin_cavity - phi_C )               solid skin around the cavity (normal distance)

    phi   = max( phi_shoe, min(-phi_Z, phi_lattice) )      lattice inside Z, solid outside; sides of the sole stay open

The strut distance of a BCC / simple-cubic lattice is evaluated in closed form (BCC: one segment after folding by
the cell's reflection group). The radius follows the foot axis through the density knots; the cell is constant.

Checks (``audit.wall_thickness`` is a ray method and flags every strut as a thin wall: not used here):
build-time parameter checks (:func:`check_params`), the thin-material share on the voxels (:func:`thin_material`),
sealed cavities and through-opening (:func:`pore_report`, ``process.sealed_cavities``).
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import ndimage

from .process import CONFIG_DIR, ProcessConfig, mesh_inner_shells, sealed_cavities
from .sdf import Grid, SDFError, sdf_to_mesh

KINDS = ("bcc", "cubic")
TABLE_N = 64  # samples per cell edge for the density <-> radius table
RMAX_MARGIN_MM = 0.6  # extra reach of the lattice evaluation beyond the largest radius


class LatticeError(ValueError):
    """A lattice parameter violates the process limits (refused at build time)."""


# ----------------------------------------------------------------------------------------------- config
@dataclass(frozen=True)
class SoleConfig:
    kind: str = "bcc"
    cell_mm: float = 6.0
    skin_cavity_mm: float = 2.0
    skin_outsole_mm: float = 2.0
    core_min_mm: float = 3.0
    open_mm: float = 3.0
    edge_extend_mm: float = 4.0
    outsole_hole_mm: float = 6.0
    hole_pitch_mm: float = 24.0
    density: tuple = ((0.0, 0.40), (0.35, 0.36), (0.70, 0.26), (1.0, 0.22))
    pad_layer_mm: float = 2.0
    pad_radius_factor: float = 1.15
    toe: str = "auto"


@dataclass(frozen=True)
class LatticeConfig:
    min_diameter_mm: float = 1.2
    voxel_mm: float = 0.3
    voxel_ratio: float = 4.0
    pad_mm: float = 2.0
    tpu_density_g_cm3: float = 1.2
    min_opening_mm: float = 5.0
    enforce_opening: bool = False
    sole: SoleConfig = field(default_factory=SoleConfig)
    upper: dict = field(default_factory=dict)  # raw [upper] tables, read by lattice_upper

    @classmethod
    def from_toml(cls, path: str | Path | None = None) -> LatticeConfig:
        p = Path(path) if path else CONFIG_DIR / "lattice.toml"
        if not p.exists() and (CONFIG_DIR / p).exists():
            p = CONFIG_DIR / p
        with open(p, "rb") as f:
            raw = tomllib.load(f)
        try:
            s = dict(raw["sole"])
            s["density"] = tuple((float(u), float(r)) for u, r in s["density"])
            sole = SoleConfig(**s)
            return cls(
                min_diameter_mm=float(raw["strut"]["min_diameter_mm"]), voxel_mm=float(raw["grid"]["voxel_mm"]),
                voxel_ratio=float(raw["grid"]["voxel_ratio"]), pad_mm=float(raw["grid"]["pad_mm"]),
                tpu_density_g_cm3=float(raw["report"]["tpu_density_g_cm3"]),
                min_opening_mm=float(raw["powder"]["min_opening_mm"]), enforce_opening=bool(raw["powder"]["enforce_opening"]),
                sole=sole, upper=raw.get("upper", {}))
        except (KeyError, TypeError) as e:
            raise LatticeError(f"{p}: bad or missing key {e}") from None


def strut_min_mm(cfg: LatticeConfig, process: ProcessConfig | None) -> float:
    """Smallest allowed strut diameter: the config value, raised by the process file's wall minimum."""
    return max(cfg.min_diameter_mm, process.wall_min if process is not None else 0.0)


def check_voxel(voxel_mm: float, min_diameter_mm: float, ratio: float = 4.0) -> None:
    """The grid must resolve the thinnest strut with at least ``ratio`` voxels across (voxel <= diameter / ratio)."""
    if not (math.isfinite(voxel_mm) and voxel_mm > 0):
        raise LatticeError(f"voxel_mm must be finite and > 0, got {voxel_mm!r}")
    if voxel_mm > min_diameter_mm / ratio * (1 + 1e-9):
        raise LatticeError(f"voxel {voxel_mm:g} mm > thinnest strut {min_diameter_mm:g} mm / {ratio:g} = {min_diameter_mm / ratio:g} mm")


def check_radii(radii, min_diameter_mm: float, voxel_mm: float, ratio: float = 4.0, what: str = "strut") -> None:
    """Build-time parameter check: 2 * r >= minimum everywhere, and the voxel resolves the thinnest strut actually used."""
    r = np.asarray(list(radii), float)
    if not np.isfinite(r).all():
        raise LatticeError(f"{what} radius is not finite")
    bad = 2.0 * r.min() < min_diameter_mm * (1 - 1e-9)
    if bad:
        raise LatticeError(f"{what} diameter {2 * r.min():.3f} mm < minimum {min_diameter_mm:g} mm")
    check_voxel(voxel_mm, 2.0 * float(r.min()), ratio)


# ----------------------------------------------------------------------------------------------- strut field
def strut_distance(kind: str, x, y, z, cell: float, phase=(0.0, 0.0, 0.0)) -> np.ndarray:
    """Distance (mm) from world points to the centre lines of a periodic strut lattice, float32.

    ``bcc``: nodes at the cell corners and centres, 8 struts from each centre to its corners. By the reflection symmetry of
    the lattice only the segment from the cell centre to the (+,+,+) corner is needed for the folded point.
    ``cubic``: nodes at the corners, struts along the 12 edges. ``phase`` shifts the cell origin.
    """
    a = float(cell)
    u = [np.mod(np.asarray(c, np.float32) - np.float32(p), np.float32(a)) for c, p in zip((x, y, z), phase)]
    c = [np.abs(v - np.float32(a / 2)) for v in u]  # offsets from the cell centre, [0, a/2]
    if kind == "bcc":
        t = np.clip(np.float32(2.0 / (3.0 * a)) * (c[0] + c[1] + c[2]), 0.0, 1.0) * np.float32(a / 2)
        return np.sqrt((c[0] - t) ** 2 + (c[1] - t) ** 2 + (c[2] - t) ** 2).astype(np.float32)
    if kind == "cubic":
        e = [np.float32(a / 2) - v for v in c]  # offsets from the nearest edge line along each axis
        return np.sqrt(np.minimum(np.minimum(e[0] ** 2 + e[1] ** 2, e[0] ** 2 + e[2] ** 2), e[1] ** 2 + e[2] ** 2)).astype(np.float32)
    raise LatticeError(f"unknown lattice kind {kind!r} (use one of {KINDS})")


@lru_cache(maxsize=32)
def _distance_sample(kind: str, cell: float) -> np.ndarray:
    g = (np.arange(TABLE_N, dtype=np.float32) + 0.5) / TABLE_N * np.float32(cell)
    X, Y, Z = np.meshgrid(g, g, g, indexing="ij")
    return np.sort(strut_distance(kind, X, Y, Z, cell).ravel())


def radius_for_density(kind: str, cell: float, rho: float) -> float:
    """Strut radius whose solid volume fraction in a periodic lattice is ``rho`` (quantile of the strut-distance field)."""
    if not 0.0 < rho < 1.0:
        raise LatticeError(f"density must be in (0, 1), got {rho!r}")
    d = _distance_sample(kind, float(cell))
    return float(np.interp(rho * (len(d) - 1), np.arange(len(d)), d))


def density_for_radius(kind: str, cell: float, r: float) -> float:
    """Solid volume fraction of a periodic lattice with strut radius ``r`` (inverse of :func:`radius_for_density`)."""
    d = _distance_sample(kind, float(cell))
    return float(np.searchsorted(d, r) / len(d))


def knot_radii(cfg: SoleConfig) -> list[tuple[float, float]]:
    return [(u, radius_for_density(cfg.kind, cfg.cell_mm, rho)) for u, rho in cfg.density]


# ----------------------------------------------------------------------------------------------- resampling
def fine_grid(grid: Grid, phi: np.ndarray, voxel: float, pad_mm: float) -> Grid:
    """Isotropic fine grid around the solid of ``phi`` (coarse grid ``grid``)."""
    solid = phi < 0
    ijk = [np.flatnonzero(solid.any(axis=tuple(a for a in range(3) if a != k))) for k in range(3)]
    lo = np.array([grid.origin[k] + ijk[k].min() * grid.spacing[k] for k in range(3)]) - pad_mm
    hi = np.array([grid.origin[k] + ijk[k].max() * grid.spacing[k] for k in range(3)]) + pad_mm
    shape = tuple(int(n) + 1 for n in np.ceil((hi - lo) / voxel - 1e-9))
    return Grid(tuple(float(v) for v in lo), float(voxel), float(voxel), shape)


class Resampler:
    """Trilinear resampling of coarse arrays onto a fine grid, one fine x-plane at a time (clamped at the border)."""

    def __init__(self, coarse: Grid, fine: Grid):
        self.coarse, self.fine = coarse, fine
        self.ax = []
        for k in range(3):
            u = np.clip((fine.origin[k] + np.arange(fine.shape[k]) * fine.spacing[k] - coarse.origin[k]) / coarse.spacing[k], 0, coarse.shape[k] - 1)
            i0 = np.minimum(np.floor(u).astype(np.int64), max(coarse.shape[k] - 2, 0))
            self.ax.append((i0, (u - i0).astype(np.float32)))
        self.x = fine.origin[0] + np.arange(fine.shape[0]) * fine.h
        self.y = (fine.origin[1] + np.arange(fine.shape[1]) * fine.h).astype(np.float32)
        self.z = (fine.origin[2] + np.arange(fine.shape[2]) * fine.dz).astype(np.float32)

    def _yz(self, p, with_z=True):
        y0, wy = self.ax[1]
        p = p[y0] * (1 - wy)[:, None] + p[y0 + 1] * wy[:, None] if p.ndim == 2 else p[y0] * (1 - wy) + p[y0 + 1] * wy
        if with_z:
            z0, wz = self.ax[2]
            p = p[:, z0] * (1 - wz) + p[:, z0 + 1] * wz
        return p.astype(np.float32)

    def plane(self, arr: np.ndarray, ix: int) -> np.ndarray:
        """(ny_f, nz_f) values of the 3D coarse ``arr`` on fine plane ``ix``."""
        i0, w = self.ax[0]
        return self._yz(arr[i0[ix]] * (1 - w[ix]) + arr[i0[ix] + 1] * w[ix])

    def row(self, arr2: np.ndarray, ix: int) -> np.ndarray:
        """(ny_f,) values of the 2D coarse ``arr2`` (axes x, y) along fine plane ``ix``."""
        i0, w = self.ax[0]
        return self._yz(arr2[i0[ix]] * (1 - w[ix]) + arr2[i0[ix] + 1] * w[ix], with_z=False)


# ----------------------------------------------------------------------------------------------- sole zone
def _crossing(phi_a, phi_b):
    """Fraction of the step from a (>= 0) to b (< 0) where phi crosses 0."""
    return phi_a / np.maximum(phi_a - phi_b, 1e-6)


def column_heights(phi: np.ndarray, grid: Grid) -> dict:
    """Per (x, y) column of the first solid run: ``has``, bottom ``zb`` and top ``zr`` of the run (sub-voxel, mm)."""
    nz = grid.shape[2]
    solid = phi < 0
    has = solid.any(axis=2)
    k0 = solid.argmax(axis=2)
    after = ~solid & (np.arange(nz)[None, None, :] > k0[:, :, None])
    k1 = np.where(after.any(axis=2), after.argmax(axis=2), nz)
    take = lambda k: np.take_along_axis(phi, np.clip(k, 0, nz - 1)[:, :, None], axis=2)[:, :, 0]  # noqa: E731
    z0, dz = grid.origin[2], grid.dz
    zb = z0 + (k0 - 1 + _crossing(take(k0 - 1), take(k0))) * dz
    zb = np.where(k0 == 0, z0, zb)
    ps, pa = take(k1 - 1), take(k1)
    t = -ps / np.maximum(pa - ps, 1e-6)
    zr = z0 + (k1 - 1 + t) * dz
    zr = np.where(k1 >= nz, z0 + (nz - 1) * dz, zr)
    return {"has": has, "zb": np.where(has, zb, 0.0), "zr": np.where(has, zr, 0.0)}


def cavity_floor(cav: np.ndarray, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Lowest cavity height per column (sub-voxel) and the mask of columns with a cavity."""
    inside = cav < 0
    has = inside.any(axis=2)
    k = inside.argmax(axis=2)
    nz = grid.shape[2]
    take = lambda kk: np.take_along_axis(cav, np.clip(kk, 0, nz - 1)[:, :, None], axis=2)[:, :, 0]  # noqa: E731
    z = grid.origin[2] + (k - 1 + _crossing(take(k - 1), take(k))) * grid.dz
    return np.where(has, z, 0.0), has


def _fill_nearest(a: np.ndarray, valid: np.ndarray) -> np.ndarray:
    if valid.all() or not valid.any():
        return a
    idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    return a[tuple(idx)]


def _disk(r_px: int):
    r = max(int(r_px), 0)
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    return (xx * xx + yy * yy) <= r * r + 1e-9


def _signed_2d(free: np.ndarray, h: float) -> np.ndarray:
    """Signed distance (mm) to the boundary of ``free`` (negative inside), pixel centres half a pixel from the boundary."""
    d_in = ndimage.distance_transform_edt(free) if free.any() else np.zeros(free.shape)
    d_out = ndimage.distance_transform_edt(~free) if (~free).any() else np.full(free.shape, 1e3)
    return (np.where(free, -(d_in - 0.5), d_out - 0.5) * h).astype(np.float32)


def foot_axis(cav: np.ndarray, grid: Grid, toe: str = "auto") -> dict:
    """x extent of the cavity, which end is the toe, and u(x) in [0, 1] from heel (0) to toe (1)."""
    cols = (cav < 0).any(axis=2)
    xs = np.flatnonzero(cols.any(axis=1))
    if len(xs) < 4:
        raise LatticeError("cavity too short to define a foot axis")
    x0, x1 = grid.origin[0] + xs[0] * grid.h, grid.origin[0] + xs[-1] * grid.h
    if toe == "auto":
        width = cols.sum(axis=1)[xs[0] : xs[-1] + 1]
        q = max(len(width) // 4, 1)
        toe = "+x" if width[-q:].mean() >= width[:q].mean() else "-x"
    if toe not in ("+x", "-x"):
        raise LatticeError(f"toe must be 'auto', '+x' or '-x', got {toe!r}")
    return {"x_min": float(x0), "x_max": float(x1), "toe": toe}


def u_of_x(x, axis: dict):
    u = (np.asarray(x, float) - axis["x_min"]) / max(axis["x_max"] - axis["x_min"], 1e-9)
    return np.clip(u if axis["toe"] == "+x" else 1.0 - u, 0.0, 1.0)


@dataclass
class SoleZone:
    """Coarse 2D / 3D fields that define the sole zone (see the module docstring)."""

    cfg: SoleConfig
    grid: Grid
    cav: np.ndarray
    zb: np.ndarray  # bottom of the lattice core (outsole + skin)
    zt: np.ndarray  # top of the lattice core (floor / run top - skin)
    lateral: np.ndarray  # signed distance to the columns that hold a core (negative inside)
    columns: np.ndarray  # bool, columns that hold a core
    axis: dict
    knots: list
    report: dict

    @classmethod
    def build(cls, phi: np.ndarray, cav: np.ndarray, grid: Grid, cfg: SoleConfig) -> SoleZone:
        runs = column_heights(phi, grid)
        zf, cav_cols = cavity_floor(cav, grid)
        zf_ext = _fill_nearest(zf, cav_cols) if cav_cols.any() else np.full(zf.shape, 1e3)
        top = np.minimum(runs["zr"], zf_ext) - cfg.skin_cavity_mm
        bot = runs["zb"] + cfg.skin_outsole_mm
        thick = top - bot
        columns = runs["has"] & (thick >= cfg.core_min_mm)
        if cfg.open_mm > 0:
            columns = ndimage.binary_opening(columns, structure=_disk(int(round(0.5 * cfg.open_mm / grid.h)))) & runs["has"]
        # A lattice island enclosed by solid columns on every side could never be emptied of powder: keep only the
        # components that touch the outside of the shoe (columns without any solid).
        lab, n_lab = ndimage.label(columns, structure=np.ones((3, 3)))
        touch = np.unique(lab[ndimage.binary_dilation(~runs["has"], structure=np.ones((3, 3))) & (lab > 0)])
        islands = int(n_lab - len(touch))
        columns = np.isin(lab, touch) & columns
        free = columns | ~runs["has"]  # outside the shoe the zone continues, so the sole sides are open, not a thin film
        lateral = _signed_2d(free, grid.h) - np.float32(cfg.edge_extend_mm)
        zb_f, zt_f = _fill_nearest(bot, runs["has"]).astype(np.float32), _fill_nearest(top, runs["has"]).astype(np.float32)
        axis = foot_axis(cav, grid, cfg.toe)
        knots = knot_radii(cfg)
        area = float(columns.sum() * grid.h * grid.h)
        rep = {"islands_dropped": islands, "columns_with_core": int(columns.sum()), "core_area_mm2": round(area),
               "solid_columns": int(runs["has"].sum()), "core_thickness_mm": {
                   "median": round(float(np.median(thick[columns])), 2) if columns.any() else None,
                   "max": round(float(thick[columns].max()), 2) if columns.any() else None},
               "foot_axis": axis, "knots_u_rho_r": [(u, rho, round(r, 3)) for (u, rho), (_, r) in zip(cfg.density, knots)]}
        return cls(cfg, grid, cav, zb_f, zt_f, lateral, columns, axis, knots, rep)

    @property
    def r_max(self) -> float:
        return max(r for _, r in self.knots) * max(1.0, self.cfg.pad_radius_factor)

    @property
    def r_min(self) -> float:
        return min(r for _, r in self.knots)

    def radius_at(self, u: float) -> float:
        us, rs = zip(*self.knots)
        return float(np.interp(u, us, rs))

    def phase(self) -> tuple[float, float, float]:
        g = self.grid
        return (g.origin[0], g.origin[1], float(self.zb[self.columns].min()) if self.columns.any() else 0.0)


def hex_hole_distance(x: float, y: np.ndarray, pitch: float, diameter: float, offset: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Signed distance (mm, negative inside a hole) to a hex grid of round holes of ``diameter``, along the line ``x`` = const."""
    px, py = pitch, pitch * math.sqrt(3.0)
    u, v = x - offset[0], np.asarray(y, np.float64) - offset[1]
    d2 = np.full(v.shape, np.inf)
    for ox, oy in ((0.0, 0.0), (0.5 * px, 0.5 * py)):
        dx = (u - ox + 0.5 * px) % px - 0.5 * px
        dy = (v - oy + 0.5 * py) % py - 0.5 * py
        d2 = np.minimum(d2, dx * dx + dy * dy)
    return (np.sqrt(d2) - 0.5 * diameter).astype(np.float32)


# ----------------------------------------------------------------------------------------------- fine builder
@dataclass
class FineStats:
    vol_base_mm3: float = 0.0
    vol_final_mm3: float = 0.0
    zone_base_mm3: float = 0.0
    zone_final_mm3: float = 0.0
    zone_voxels: int = 0
    layers: dict = field(default_factory=dict)


def _occ(phi, h):
    return np.clip(0.5 - phi / h, 0.0, 1.0)


class SoleLayer:
    """Applies the sole lattice on one fine x-plane."""

    name = "sole"

    def __init__(self, zone: SoleZone, rs: Resampler):
        self.zone, self.rs = zone, rs
        self.reach = zone.r_max + RMAX_MARGIN_MM
        self.phase = zone.phase()
        self.hole_off = (self.phase[0] + 0.5 * zone.cfg.cell_mm, self.phase[1] + 0.5 * zone.cfg.cell_mm)

    def apply(self, base: np.ndarray, ix: int, stats: FineStats) -> np.ndarray:
        z, rs, c = self.zone, self.rs, self.zone.cfg
        zz = rs.z[None, :]
        lat = rs.row(z.lateral, ix)
        zt = rs.row(z.zt, ix)
        zb = rs.row(z.zb, ix)
        thin = np.float32(0.5 * c.core_min_mm) - (zt - zb)  # the extended zone never holds a core thinner than half of core_min
        phi_z = np.maximum(np.maximum(lat, thin)[:, None], np.maximum(zb[:, None] - zz, zz - zt[:, None]))
        if (phi_z >= self.reach).all():
            return base
        phi_z = np.maximum(phi_z, np.float32(c.skin_cavity_mm) - rs.plane(z.cav, ix))
        cand = (phi_z < self.reach) & (base < 1.0)
        if not cand.any():
            return base
        iy, iz = np.nonzero(cand)
        x = np.float32(rs.x[ix])
        px, py, pz = np.full(len(iy), x, np.float32), rs.y[iy], rs.z[iz]
        r = np.float32(z.radius_at(float(u_of_x(x, z.axis))))
        r_pt = np.full(len(iy), r, np.float32)
        if c.pad_radius_factor != 1.0 and c.pad_layer_mm > 0:
            s = np.clip((pz - (zt[iy] - np.float32(c.pad_layer_mm))) / np.float32(c.pad_layer_mm), 0.0, 1.0)
            r_pt = r_pt * (1.0 + np.float32(c.pad_radius_factor - 1.0) * s)
        d = strut_distance(c.kind, px, py, pz, c.cell_mm, self.phase) - r_pt
        pz_ = phi_z[iy, iz]
        new = np.maximum(base[iy, iz], np.minimum(-pz_, d))
        zone_mask = pz_ < 0
        h = rs.fine.h
        stats.zone_base_mm3 += float(_occ(base[iy, iz][zone_mask], h).sum()) * h**3
        stats.zone_final_mm3 += float(_occ(new[zone_mask], h).sum()) * h**3
        stats.zone_voxels += int(zone_mask.sum())
        out = base.copy()
        out[iy, iz] = new
        # powder windows: round holes through the outsole skin down to the lattice, only inside the sole zone columns
        if c.outsole_hole_mm > 0:
            hd = hex_hole_distance(rs.x[ix], rs.y, c.hole_pitch_mm, c.outsole_hole_mm, self.hole_off)
            np.maximum(out, np.minimum(np.minimum(-hd[:, None], -lat[:, None]), zb[:, None] - zz), out=out)
        return out


def build_fine(phi: np.ndarray, grid: Grid, layers_for, voxel: float, pad_mm: float, progress=None) -> tuple[np.ndarray, Grid, FineStats]:
    """Run the layers over every fine x-plane. ``layers_for(resampler, fine_grid)`` returns the list of layers."""
    fg = fine_grid(grid, phi, voxel, pad_mm)
    rs = Resampler(grid, fg)
    layers = layers_for(rs, fg)
    out = np.empty(fg.shape, np.float32)
    stats = FineStats()
    h = fg.h
    for ix in range(fg.shape[0]):
        base = rs.plane(phi, ix)
        b0 = float(_occ(base, h).sum()) * h**3
        cur = base
        for layer in layers:
            cur = layer.apply(cur, ix, stats)
        out[ix] = cur
        stats.vol_base_mm3 += b0
        stats.vol_final_mm3 += float(_occ(cur, h).sum()) * h**3
        if progress and ix % 200 == 0:
            progress(ix, fg.shape[0])
    return out, fg, stats


def sole_lattice(phi: np.ndarray, cav: np.ndarray, grid: Grid, cfg: LatticeConfig, process: ProcessConfig | None = None,
                 progress=None) -> tuple[np.ndarray, Grid, dict]:
    """Sole lattice on ``phi`` (coarse grid ``grid``, cavity ``cav``). Returns (fine phi, fine grid, report).

    Raises :class:`LatticeError` before any geometry is built when the strut / voxel / opening parameters violate the limits.
    """
    zone = SoleZone.build(phi, cav, grid, cfg.sole)
    check_sole_params(cfg, process, zone)
    fine, fg, stats = build_fine(phi, grid, lambda rs, fg: [SoleLayer(zone, rs)], cfg.voxel_mm, cfg.pad_mm, progress)
    rep = sole_report(zone, stats, cfg, process)
    return fine, fg, rep


def check_sole_params(cfg: LatticeConfig, process: ProcessConfig | None, zone: SoleZone | None = None) -> dict:
    """Parameter checks of the sole lattice; raises :class:`LatticeError`. Returns the pore estimate of the densest radius."""
    s = cfg.sole
    if s.kind not in KINDS:
        raise LatticeError(f"unknown lattice kind {s.kind!r}")
    if not (s.cell_mm > 0 and math.isfinite(s.cell_mm)):
        raise LatticeError("cell_mm must be > 0")
    for k in (s.skin_cavity_mm, s.skin_outsole_mm):
        if k < strut_min_mm(cfg, process):
            raise LatticeError(f"skin {k:g} mm < minimum wall {strut_min_mm(cfg, process):g} mm")
    if s.pad_radius_factor < 1.0:
        raise LatticeError("pad_radius_factor must be >= 1 (the pad layer only thickens)")
    knots = zone.knots if zone is not None else knot_radii(s)
    radii = [r for _, r in knots]
    check_radii(radii, strut_min_mm(cfg, process), cfg.voxel_mm, cfg.voxel_ratio, what="sole strut")
    need = max(cfg.min_opening_mm, process.min_channel_mm if process is not None else 0.0)
    if 0 < s.outsole_hole_mm < need:
        raise LatticeError(f"outsole powder window {s.outsole_hole_mm:g} mm < required opening {need:g} mm")
    pores = pore_report(s.kind, s.cell_mm, max(radii) * max(s.pad_radius_factor, 1.0), min(cfg.voxel_mm, 0.25))
    pores["required_mm"] = need
    pores["ok"] = bool(pores["through_mm"] >= need)
    if cfg.enforce_opening and not pores["ok"]:
        raise LatticeError(f"through opening {pores['through_mm']:.2f} mm < required {need:g} mm (cell {s.cell_mm:g}, r {max(radii):.2f})")
    return pores


def sole_report(zone: SoleZone, stats: FineStats, cfg: LatticeConfig, process) -> dict:
    g = cfg.tpu_density_g_cm3
    rep = dict(zone.report)
    rep["cell_mm"], rep["kind"], rep["voxel_mm"] = zone.cfg.cell_mm, zone.cfg.kind, cfg.voxel_mm
    rep["zone_volume_cm3"] = round(stats.zone_base_mm3 / 1000, 2)
    rep["zone_solid_after_cm3"] = round(stats.zone_final_mm3 / 1000, 2)
    rep["zone_fill_fraction"] = round(stats.zone_final_mm3 / max(stats.zone_base_mm3, 1e-9), 4)
    rep["saved_cm3"] = round((stats.zone_base_mm3 - stats.zone_final_mm3) / 1000, 2)
    rep["saved_g_at_density"] = round(rep["saved_cm3"] * g, 1)
    rep["tpu_density_g_cm3"] = g
    rep["volume_fine_before_cm3"] = round(stats.vol_base_mm3 / 1000, 2)
    rep["volume_fine_after_cm3"] = round(stats.vol_final_mm3 / 1000, 2)
    return rep


# ----------------------------------------------------------------------------------------------- pores
def pore_report(kind: str, cell: float, r: float, voxel: float = 0.2, tiles: int = 3) -> dict:
    """Free-ball sizes of a periodic lattice of strut radius ``r``.

    ``inscribed_mm``: the largest ball that fits in a pore. ``through_mm``: the largest ball that travels from one face of a
    ``tiles``^3 block to the opposite one along x (the opening powder / nozzle has to pass; symmetric lattices: same for y, z).
    """
    n = int(round(cell * tiles / voxel))
    g = (np.arange(n, dtype=np.float32) + 0.5) * np.float32(cell * tiles / n)
    X, Y, Z = np.meshgrid(g, g, g, indexing="ij")
    air = strut_distance(kind, X, Y, Z, cell) > r
    dist = ndimage.distance_transform_edt(air, sampling=cell * tiles / n).astype(np.float32)
    inscribed = float(dist.max()) * 2.0

    def passes(rad):
        lab, _ = ndimage.label(dist >= rad)
        a, b = set(np.unique(lab[0])) - {0}, set(np.unique(lab[-1])) - {0}
        return bool(a & b)

    lo, hi = 0.0, float(dist.max())
    for _ in range(14):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if passes(mid) else (lo, mid)
    return {"kind": kind, "cell_mm": cell, "radius_mm": round(float(r), 3), "inscribed_mm": round(inscribed, 2),
            "through_mm": round(2.0 * lo, 2), "density": round(density_for_radius(kind, cell, r), 4)}


# ----------------------------------------------------------------------------------------------- checks on the fine field
def thin_material(phi: np.ndarray, grid: Grid, min_diameter_mm: float, x_slices: tuple[int, int] | None = None, chunk: int = 96) -> dict:
    """Share of solid voxels that a ball of the minimum strut radius cannot reach (morphological opening, in x-chunks).

    This replaces the ray method for lattices: material thinner than the minimum strut shows up as lost volume under the
    opening. The test radius is ``min_diameter / 2 - h / 2`` because a voxel field resolves a radius only to half a voxel.
    """
    r = max(0.5 * min_diameter_mm - 0.5 * grid.h, 0.5 * grid.h)
    halo = int(math.ceil((r + 2 * grid.h) / grid.h)) + 1
    n = phi.shape[0]
    lo_all, hi_all = x_slices if x_slices else (0, n)
    solid_n = thin_n = 0
    for s in range(lo_all, hi_all, chunk):
        e = min(s + chunk, hi_all)
        a, b = max(s - halo, 0), min(e + halo, n)
        solid = phi[a:b] < 0
        if not solid.any():
            continue
        d_in = ndimage.distance_transform_edt(np.pad(solid, 1), sampling=grid.h)[1:-1, 1:-1, 1:-1]
        core = d_in >= r - 1e-6
        reach = ndimage.distance_transform_edt(np.pad(~core, 1, constant_values=True), sampling=grid.h)[1:-1, 1:-1, 1:-1] <= r + 1e-6
        thin = solid & ~reach
        sl = slice(s - a, e - a)
        solid_n += int(solid[sl].sum())
        thin_n += int(thin[sl].sum())
    return {"test_radius_mm": round(r, 3), "solid_voxels": solid_n, "thin_voxels": thin_n,
            "thin_fraction": round(thin_n / max(solid_n, 1), 5), "thin_volume_mm3": round(thin_n * grid.h**3, 1)}


def zone_box(zone: SoleZone) -> tuple[np.ndarray, np.ndarray]:
    """World bounding box (lo, hi) of the sole core columns and their heights."""
    g = zone.grid
    ij = np.argwhere(zone.columns)
    lo = np.array([g.origin[0] + ij[:, 0].min() * g.h, g.origin[1] + ij[:, 1].min() * g.h, float(zone.zb[zone.columns].min())])
    hi = np.array([g.origin[0] + ij[:, 0].max() * g.h, g.origin[1] + ij[:, 1].max() * g.h, float(zone.zt[zone.columns].max())])
    return lo - zone.cfg.edge_extend_mm, hi + zone.cfg.edge_extend_mm


def powder_access(phi: np.ndarray, fg: Grid, phi_coarse: np.ndarray, coarse: Grid, box, balls=(1.0, 2.0, 3.0, 5.0)) -> dict:
    """How much of the air inside the shoe (``phi_coarse < 0``) in ``box`` a ball of each diameter can reach from outside.

    A ball of diameter D moves through voxels whose distance to the solid is >= D/2; ``unreachable_mm3`` is the part of the
    enclosed air that stays farther than D/2 from every position the ball can reach from the border. ``0`` for the diameters the
    powder path allows; the through opening of the lattice (``pore_report``) says which diameter that is.
    """
    lo, hi = np.asarray(box[0], float), np.asarray(box[1], float)
    pad = int(math.ceil((max(balls) / 2 + 1.5) / fg.h))
    a = np.maximum(np.floor((lo - np.asarray(fg.origin)) / fg.h).astype(int), 0)
    b = np.minimum(np.ceil((hi - np.asarray(fg.origin)) / fg.h).astype(int) + 1, fg.shape)
    sub = np.pad(phi[a[0] : b[0], a[1] : b[1], a[2] : b[2]], pad, constant_values=1.0)
    inside = np.zeros(sub.shape, bool)
    rs = Resampler(coarse, fg)
    for k, ix in enumerate(range(a[0], b[0])):
        inside[pad + k, pad : pad + b[1] - a[1], pad : pad + b[2] - a[2]] = rs.plane(phi_coarse, ix)[a[1] : b[1], a[2] : b[2]] < 0
    air = sub >= 0
    dist = ndimage.distance_transform_edt(air, sampling=fg.h).astype(np.float32)
    core = air & inside
    out = {"box_mm": [[round(float(v), 1) for v in lo], [round(float(v), 1) for v in hi]], "enclosed_air_mm3": round(float(core.sum() * fg.h**3), 1), "balls": {}}
    for d in balls:
        lab, _ = ndimage.label(dist >= d / 2)
        reach = lab == lab[0, 0, 0] if lab[0, 0, 0] else np.zeros(air.shape, bool)
        far = ndimage.distance_transform_edt(~reach, sampling=fg.h) > d / 2 if reach.any() else np.ones(air.shape, bool)
        un = core & far
        out["balls"][f"{d:g}"] = {"unreachable_mm3": round(float(un.sum() * fg.h**3), 1), "unreachable_fraction": round(float(un.sum() / max(core.sum(), 1)), 4)}
        del lab, reach, far, un
    return out


def export_checks(mesh) -> dict:
    return {"watertight": bool(mesh.is_watertight), "bodies": int(mesh.body_count), "inner_shells": mesh_inner_shells(mesh)}


def to_mesh(phi: np.ndarray, grid: Grid, max_dropped_mm3: float = 50.0):
    """Marching cubes of a fine field; lattice fragments that are not part of the main body are dropped and reported."""
    phi = np.where(phi == 0, np.float32(1e-3), phi).astype(np.float32, copy=False)
    mesh, rep = sdf_to_mesh(phi, grid, max_dropped_mm3)
    return mesh, rep


def sealed(phi: np.ndarray, grid: Grid) -> dict:
    return sealed_cavities(phi, grid)


def fill_sealed(phi: np.ndarray, grid: Grid) -> dict:
    """Safety net: fill every air pocket that does not reach the grid border (they would trap powder). In place; reports it."""
    air, n = ndimage.label(phi >= 0)
    pockets = (air != air[0, 0, 0]) & (air > 0)
    voxel = grid.h * grid.h * grid.dz
    sizes = np.bincount(air[pockets]) if pockets.any() else np.zeros(1, int)
    del air
    phi[pockets] = np.float32(-1e-3)
    return {"filled_mm3": round(float(pockets.sum() * voxel), 2), "components": int(max(n - 1, 0)), "largest_voxels": int(sizes.max())}



# ----------------------------------------------------------------------------------------------- coupons (#11)
COUPON_CELLS = (4.0, 6.0, 8.0)
COUPON_DIAMETERS = (1.2, 1.6, 2.0)


def coupon(cell: float, diameter: float, cfg: LatticeConfig, process: ProcessConfig | None = None, kind: str | None = None,
           size=(48.0, 48.0, 20.0), skin_mm: float | None = None, hole_mm: float | None = None) -> tuple[np.ndarray, Grid, dict]:
    """Test block for the factory: lattice core between a bottom and a top skin plate, open lattice on the four sides, round powder
    holes in both plates (4 in the bottom plate at the quarter points, 1 in the top plate at the centre). One cell size, one strut
    diameter, no density gradient. Returns (phi, fine grid, report). Parameter checks as for the sole (diameter >= minimum, voxel).
    """
    kind = kind or cfg.sole.kind
    skin = cfg.sole.skin_outsole_mm if skin_mm is None else skin_mm
    hole = cfg.sole.outsole_hole_mm if hole_mm is None else hole_mm
    lo = strut_min_mm(cfg, process)
    check_radii([0.5 * diameter], lo, cfg.voxel_mm, cfg.voxel_ratio, what="coupon strut")
    if skin < lo:
        raise LatticeError(f"skin {skin:g} mm < minimum {lo:g} mm")
    need = max(cfg.min_opening_mm, process.min_channel_mm if process is not None else 0.0)
    if hole < need:
        raise LatticeError(f"coupon powder hole {hole:g} mm < required opening {need:g} mm")
    h = cfg.voxel_mm
    sx, sy, sz = size
    pad = 3
    shape = tuple(int(math.ceil(v / h)) + 1 + 2 * pad for v in size)
    grid = Grid((-pad * h,) * 3, h, h, shape)
    g = [-pad * h + np.arange(n, dtype=np.float32) * np.float32(h) for n in shape]
    X, Y, Z = np.meshgrid(*g, indexing="ij")
    box = np.maximum(np.maximum(-X, X - sx), np.maximum(np.maximum(-Y, Y - sy), np.maximum(-Z, Z - sz)))
    # outside the block the max-box distance is not Euclidean, fine for a sign + a few voxels of gradient
    phi_z = np.maximum(skin - Z, Z - (sz - skin))
    lat = strut_distance(kind, X, Y, Z, cell, (0.0, 0.0, skin)) - np.float32(0.5 * diameter)
    phi = np.maximum(box, np.minimum(-phi_z, lat))
    holes = [(sx * 0.25, sy * 0.25), (sx * 0.75, sy * 0.25), (sx * 0.25, sy * 0.75), (sx * 0.75, sy * 0.75)]
    for hx, hy in holes:
        carve = np.minimum(np.float32(0.5 * hole) - np.hypot(X - hx, Y - hy), skin + 0.5 - Z)  # bottom plate
        phi = np.maximum(phi, carve)
    carve = np.minimum(np.float32(0.5 * hole) - np.hypot(X - sx / 2, Y - sy / 2), Z - (sz - skin - 0.5))  # top plate
    phi = np.maximum(phi, carve).astype(np.float32)
    phi[phi == 0] = 1e-3
    vol = float(_occ(phi, h).sum()) * h**3
    rep = {"kind": kind, "cell_mm": cell, "strut_diameter_mm": diameter, "size_mm": list(size), "skin_mm": skin, "hole_mm": hole,
           "voxel_mm": h, "nominal_density": round(density_for_radius(kind, cell, 0.5 * diameter), 4),
           "volume_cm3": round(vol / 1000, 3), "solid_block_cm3": round(sx * sy * sz / 1000, 3),
           "fill_vs_solid": round(vol / (sx * sy * sz), 4), "grams_at_placeholder_density": round(vol / 1000 * cfg.tpu_density_g_cm3, 1),
           "pores": pore_report(kind, cell, 0.5 * diameter, 0.2)}
    return phi, grid, rep


__all__ = ["LatticeConfig", "SoleConfig", "LatticeError", "SoleZone", "strut_distance", "radius_for_density", "density_for_radius",
           "sole_lattice", "coupon", "check_sole_params", "check_radii", "check_voxel", "pore_report", "powder_access", "zone_box", "fill_sealed", "thin_material", "to_mesh", "export_checks",
           "SDFError"]
