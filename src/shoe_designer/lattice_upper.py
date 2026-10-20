"""Lattice shell for the upper (#14 part B, exploration prototype: not in the main line).

The shell hangs on the cavity only: the outer skin is ``phi_C`` (S4) offset outward by T, the template envelope is not used
(this is the "rebuild the skin from the last" route of the design doc). Body = shell (0 <= phi_C < T) below the collar line,
plus a sole slab under the cavity floor, which gets the A lattice. Two forms replace the solid shell above the sole:

* ``knit``  - thin shell (T = 1.2-2 mm) of two crossing yarn families: tubes of diameter ``yarn_diameter_mm`` that follow the
  mid-surface (phi_C = T/2) along helices about the foot axis (``f = R0 * theta +- s * x`` constant). The distance to a yarn is
  evaluated in world coordinates as the distance to the *curve* where the mid-surface meets a level surface of ``f``:
  ``d^2 = (a^2 + b^2 - 2 a b c) / (1 - c^2)`` with a, b the signed distances to the two surfaces and c the cosine of their normals,
  so a yarn is a real 3D tube (round section) wherever the two surfaces are not tangent (1 - c^2 is floored at ``MIN_SIN2``).
* ``volume`` - thick shell (6-8 mm) filled with a world-aligned lattice: gyroid sheet (thickness ``wall_mm``) or BCC struts.

Solid collar band (``rim_mm``) and a solid band above the sole slab (``sole_margin_mm``) stay. No conformal coordinate system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .lattice import (LatticeConfig, LatticeError, Resampler, SoleLayer, SoleZone, _fill_nearest, _occ, _signed_2d, build_fine, cavity_floor,
                      check_radii, check_voxel, strut_distance, strut_min_mm)
from .process import ProcessConfig
from .sdf import Grid

MIN_SIN2 = 0.12  # floor of 1 - c^2: where the yarn direction runs along the surface the tube is cut to a ribbon no wider than ~3x
FORMS = ("knit", "volume")


@dataclass(frozen=True)
class KnitConfig:
    thickness_mm: float = 1.8
    yarn_diameter_mm: float = 1.6
    pitch_mm: float = 7.0
    helix_slope: float = 0.9


@dataclass(frozen=True)
class VolumeConfig:
    thickness_mm: float = 7.0
    kind: str = "gyroid"
    cell_mm: float = 8.0
    wall_mm: float = 1.4
    radius_mm: float = 0.7
    skin_mm: float = 0.0


@dataclass(frozen=True)
class UpperConfig:
    rim_mm: float = 8.0
    sole_thickness_mm: float = 12.0
    sole_margin_mm: float = 2.0
    knit: KnitConfig = KnitConfig()
    volume: VolumeConfig = VolumeConfig()

    @classmethod
    def from_lattice(cls, cfg: LatticeConfig) -> UpperConfig:
        u = cfg.upper
        try:
            return cls(rim_mm=float(u["rim_mm"]), sole_thickness_mm=float(u["sole_thickness_mm"]), sole_margin_mm=float(u["sole_margin_mm"]),
                       knit=KnitConfig(float(u["thickness_mm"]), float(u["knit"]["yarn_diameter_mm"]), float(u["knit"]["pitch_mm"]),
                                       float(u["knit"]["helix_slope"])),
                       volume=VolumeConfig(float(u["volume"]["thickness_mm"]), str(u["volume"]["kind"]), float(u["volume"]["cell_mm"]),
                                           float(u["volume"]["wall_mm"]), float(u["volume"]["radius_mm"]), float(u["volume"]["skin_mm"])))
        except KeyError as e:
            raise LatticeError(f"[upper] missing key {e}") from None


def check_upper(form: str, u: UpperConfig, cfg: LatticeConfig, process: ProcessConfig | None) -> None:
    lo = strut_min_mm(cfg, process)
    if form == "knit":
        k = u.knit
        check_radii([0.5 * k.yarn_diameter_mm], lo, cfg.voxel_mm, cfg.voxel_ratio, what="yarn")
        if k.thickness_mm < k.yarn_diameter_mm - 1e-9:
            raise LatticeError(f"shell thickness {k.thickness_mm:g} mm < yarn diameter {k.yarn_diameter_mm:g} mm (the yarn would be cut)")
        if k.pitch_mm <= 2 * k.yarn_diameter_mm:
            raise LatticeError("pitch must exceed two yarn diameters or the diamonds close")
    elif form == "volume":
        v = u.volume
        if v.kind not in ("gyroid", "bcc"):
            raise LatticeError(f"volume kind must be 'gyroid' or 'bcc', got {v.kind!r}")
        thinnest = v.wall_mm if v.kind == "gyroid" else 2 * v.radius_mm
        check_radii([0.5 * thinnest], lo, cfg.voxel_mm, cfg.voxel_ratio, what=f"{v.kind} " + ("wall" if v.kind == "gyroid" else "strut"))
        if v.skin_mm and v.skin_mm < lo:
            raise LatticeError(f"skin {v.skin_mm:g} mm < minimum {lo:g} mm")
        if v.thickness_mm < v.cell_mm * 0.75:
            raise LatticeError(f"shell {v.thickness_mm:g} mm cannot hold a cell of {v.cell_mm:g} mm")
    else:
        raise LatticeError(f"unknown form {form!r} (use one of {FORMS})")
    check_voxel(cfg.voxel_mm, lo, cfg.voxel_ratio)


# ----------------------------------------------------------------------------------------------- body from the cavity
def offset_field(cav: np.ndarray, grid: Grid, trust_mm: float = 2.5) -> np.ndarray:
    """phi_C made usable for offsets of several mm: S4 keeps phi_C exact only a few mm outside the cavity (beyond that it is
    clamped); outside ``trust_mm`` the value is the voxel distance to the cavity, shifted to join phi_C where the two overlap."""
    inside = cav < 0
    d = ndimage.distance_transform_edt(~inside, sampling=grid.spacing).astype(np.float32)
    band = (cav > trust_mm - 0.5) & (cav < trust_mm + 0.5)
    shift = float(np.median(cav[band] - d[band])) if band.any() else -0.5 * grid.h
    out = np.where(cav > trust_mm, np.maximum(d + shift, np.float32(trust_mm)), cav)
    return out.astype(np.float32)


def shell_body(cav: np.ndarray, zt: np.ndarray, grid: Grid, thickness: float, sole_thickness: float) -> tuple[np.ndarray, dict]:
    """phi of cavity-only body: shell 0 <= phi_C < T below the collar line plus a sole slab; ``zt`` is the collar height per column."""
    z = (grid.origin[2] + np.arange(grid.shape[2]) * grid.dz).astype(np.float32)
    shell = np.maximum(cav - np.float32(thickness), -cav)
    shell = np.maximum(shell, z[None, None, :] - zt[:, :, None].astype(np.float32))
    zf, has = cavity_floor(cav, grid)
    zf = _fill_nearest(zf, has).astype(np.float32)
    foot = _signed_2d(has, grid.h) - np.float32(thickness)  # footprint of the cavity dilated by T (negative inside)
    slab = np.maximum(foot[:, :, None], np.maximum((zf - np.float32(sole_thickness))[:, :, None] - z[None, None, :], z[None, None, :] - zf[:, :, None]))
    body = np.maximum(np.minimum(shell, slab), -cav).astype(np.float32)
    body[body == 0] = 1e-3
    return body, {"floor_z": zf}


def foot_frame(cav: np.ndarray, zt: np.ndarray, grid: Grid, pitch: float) -> dict:
    """Centre line (y_c(x), z_c(x)) of the cavity and the number of yarns around it (so the helices close)."""
    z = grid.origin[2] + np.arange(grid.shape[2]) * grid.dz
    inside = (cav < 0) & (z[None, None, :] < zt[:, :, None] - 5.0)
    xs = grid.origin[0] + np.arange(grid.shape[0]) * grid.h
    cnt = inside.sum(axis=(1, 2)).astype(float)
    ok = cnt > 50
    if not ok.any():
        raise LatticeError("cavity below the collar is empty")
    yy = grid.origin[1] + np.arange(grid.shape[1]) * grid.h
    yc = np.where(ok, (inside.sum(axis=2) * yy[None, :]).sum(axis=1) / np.maximum(cnt, 1), 0.0)
    zc = np.where(ok, (inside.sum(axis=1) * z[None, :]).sum(axis=1) / np.maximum(cnt, 1), 0.0)
    xo = xs[ok]
    sm = lambda v: ndimage.gaussian_filter1d(v[ok], 4.0 / grid.h, mode="nearest")  # noqa: E731
    area = cnt[ok] * grid.h * grid.h * grid.dz / grid.h  # cross-section area per x slab (mm^2)
    circ = 2.0 * math.pi * np.sqrt(np.median(area) / math.pi)
    n = max(int(round(circ / pitch)), 6)
    return {"x": xo, "yc": sm(yc), "zc": sm(zc), "n_around": n, "r0": n * pitch / (2.0 * math.pi), "circumference_mm": circ}


# ----------------------------------------------------------------------------------------------- the upper layer
class UpperLayer:
    """Replaces the solid shell above the sole by a yarn mesh (``knit``) or a volumetric lattice (``volume``) on one x-plane."""

    def __init__(self, form: str, u: UpperConfig, cav: np.ndarray, zt: np.ndarray, floor_z: np.ndarray, grid: Grid, rs: Resampler, frame: dict | None):
        self.form, self.u, self.rs, self.frame = form, u, rs, frame
        self.cav = cav
        self.zt, self.floor = zt.astype(np.float32), floor_z.astype(np.float32)
        self.grad = [g.astype(np.float32) for g in np.gradient(cav, grid.h, grid.h, grid.dz)]
        self.thick = u.knit.thickness_mm if form == "knit" else u.volume.thickness_mm
        self.reach = 2.0
        self.stat_zone = [0.0, 0.0]

    def apply(self, base: np.ndarray, ix: int, stats) -> np.ndarray:
        rs, u = self.rs, self.u
        zz = rs.z[None, :]
        zt, zf = rs.row(self.zt, ix), rs.row(self.floor, ix)
        phi_z = np.maximum((zf + np.float32(u.sole_margin_mm))[:, None] - zz, zz - (zt - np.float32(u.rim_mm))[:, None])
        if (phi_z >= self.reach).all():
            return base
        cav = rs.plane(self.cav, ix)
        skin = u.volume.skin_mm if self.form == "volume" else 0.0
        if skin:
            phi_z = np.maximum(phi_z, np.maximum(np.float32(skin) - cav, cav - np.float32(self.thick - skin)))
        cand = (phi_z < self.reach) & (base < 1.0)
        if not cand.any():
            return base
        iy, iz = np.nonzero(cand)
        px, py, pz = np.full(len(iy), rs.x[ix], np.float32), rs.y[iy], rs.z[iz]
        a = cav[iy, iz]
        d = self._knit(ix, px, py, pz, a, [rs.plane(g, ix)[iy, iz] for g in self.grad]) if self.form == "knit" else self._volume(px, py, pz)
        pz_ = phi_z[iy, iz]
        new = np.maximum(base[iy, iz], np.minimum(-pz_, d))
        zm = pz_ < 0
        h = rs.fine.h
        stats.zone_base_mm3 += float(_occ(base[iy, iz][zm], h).sum()) * h**3
        stats.zone_final_mm3 += float(_occ(new[zm], h).sum()) * h**3
        out = base.copy()
        out[iy, iz] = new
        return out

    def _knit(self, ix, px, py, pz, a, grad):
        k, f = self.u.knit, self.frame
        x = float(px[0])
        yc, zc = np.interp(x, f["x"], f["yc"]), np.interp(x, f["x"], f["zc"])
        dyc = np.interp(x + 1.0, f["x"], f["yc"]) - np.interp(x - 1.0, f["x"], f["yc"])
        dzc = np.interp(x + 1.0, f["x"], f["zc"]) - np.interp(x - 1.0, f["x"], f["zc"])
        yc_, zc_, dyc, dzc = yc, zc, dyc / 2.0, dzc / 2.0
        Y, Z = (py - np.float32(yc_)).astype(np.float64), (pz - np.float32(zc_)).astype(np.float64)
        rho2 = np.maximum(Y * Y + Z * Z, 4.0)
        theta = np.arctan2(Z, Y)
        r0, s, p = f["r0"], k.helix_slope, k.pitch_mm
        # d theta / d(x, y, z)
        th = np.stack([(-Y * dzc + Z * dyc) / rho2, -Z / rho2, Y / rho2])
        gs = np.stack(grad).astype(np.float64)
        ns = gs / np.maximum(np.linalg.norm(gs, axis=0), 1e-6)
        a_mid = a.astype(np.float64) - 0.5 * self.thick
        best = np.full(len(py), np.inf)
        for sign in (1.0, -1.0):
            fval = r0 * theta + sign * s * x
            gf = r0 * th
            gf[0] += sign * s
            gn = np.maximum(np.linalg.norm(gf, axis=0), 1e-6)
            b = ((fval + 0.5 * p) % p - 0.5 * p) / gn
            c = np.sum(ns * gf / gn, axis=0)
            den = np.maximum(1.0 - c * c, MIN_SIN2)
            d2 = np.maximum(a_mid * a_mid + b * b - 2.0 * a_mid * b * c, 0.0) / den
            best = np.minimum(best, np.sqrt(d2))
        return (best - 0.5 * k.yarn_diameter_mm).astype(np.float32)

    def _volume(self, px, py, pz):
        v = self.u.volume
        if v.kind == "bcc":
            return strut_distance("bcc", px, py, pz, v.cell_mm) - np.float32(v.radius_mm)
        w = 2.0 * math.pi / v.cell_mm
        sx, cx = np.sin(w * px), np.cos(w * px)
        sy, cy = np.sin(w * py), np.cos(w * py)
        sz, cz = np.sin(w * pz), np.cos(w * pz)
        g = sx * cy + sy * cz + sz * cx
        gx, gy, gz = w * (cx * cy - sz * sx), w * (-sx * sy + cy * cz), w * (-sy * sz + cz * cx)
        gn = np.maximum(np.sqrt(gx * gx + gy * gy + gz * gz), 1e-3)
        return (np.abs(g) / gn - 0.5 * v.wall_mm).astype(np.float32)


def shell_openings(phi: np.ndarray, fg: Grid, cav: np.ndarray, coarse: Grid, zt: np.ndarray, floor_z: np.ndarray, u: UpperConfig, thick: float,
                   chunk: int = 96, halo_mm: float = 12.0, min_open_mm: float = 5.0) -> dict:
    """Hole sizes of the shell. The air voxels on the mid-surface (phi_C = T / 2) in the upper zone (above the sole margin, below
    the rim) fall into connected pieces, one per hole of the mesh; each hole's size is 2 * the largest distance to the solid inside
    it (its inscribed ball). Chunked in x; a hole belongs to the chunk of its first plane."""
    rs = Resampler(coarse, fg)
    halo = int(math.ceil(halo_mm / fg.h))
    holes = []
    n = fg.shape[0]
    for s0 in range(0, n, chunk):
        e0 = min(s0 + chunk, n)
        a, b = max(s0 - halo, 0), min(e0 + halo, n)
        air = phi[a:b] >= 0
        band = np.zeros(air.shape, bool)
        for ix in range(a, b):
            zz = rs.z[None, :]
            zone = (zz > (rs.row(floor_z, ix) + u.sole_margin_mm)[:, None]) & (zz < (rs.row(zt, ix) - u.rim_mm)[:, None])
            band[ix - a] = zone & (np.abs(rs.plane(cav, ix) - 0.5 * thick) < 0.35)
        band &= air
        if not band.any():
            continue
        dist = ndimage.distance_transform_edt(air, sampling=fg.h).astype(np.float32)
        lab, k = ndimage.label(band)
        mx = ndimage.maximum(dist, lab, index=np.arange(1, k + 1))
        for i, sl in enumerate(ndimage.find_objects(lab)):
            if s0 <= sl[0].start + a < e0:
                holes.append(2.0 * float(mx[i]))
    v = np.asarray(holes) if holes else np.zeros(1)
    big = v[v >= 3.0]
    major = {"major_holes_ge3mm": int(len(big)), "major_p50_mm": round(float(np.median(big)), 2) if len(big) else None,
             "major_fraction_ge_min": round(float((big >= min_open_mm).mean()), 3) if len(big) else None}
    return {**major, "holes": int(len(holes)), "p10_mm": round(float(np.percentile(v, 10)), 2), "p50_mm": round(float(np.percentile(v, 50)), 2),
            "p90_mm": round(float(np.percentile(v, 90)), 2), "max_mm": round(float(v.max()), 2),
            "fraction_ge_min": round(float((v >= min_open_mm).mean()), 3), "min_open_mm": min_open_mm}


def upper_shell(form: str, phi: np.ndarray, cav: np.ndarray, zt: np.ndarray, grid: Grid, cfg: LatticeConfig, process: ProcessConfig | None = None,
                progress=None) -> tuple[np.ndarray, Grid, dict]:
    """Prototype of form ``knit`` or ``volume`` on the cavity ``cav`` (``phi`` is only used for its grid and is not part of the body)."""
    u = UpperConfig.from_lattice(cfg)
    check_upper(form, u, cfg, process)
    cav = offset_field(cav, grid)
    thick = u.knit.thickness_mm if form == "knit" else u.volume.thickness_mm
    body, aux = shell_body(cav, zt, grid, thick, u.sole_thickness_mm)
    zone = SoleZone.build(body, cav, grid, cfg.sole)
    from .lattice import check_sole_params

    pores = check_sole_params(cfg, process, zone)
    frame = foot_frame(cav, zt, grid, u.knit.pitch_mm) if form == "knit" else None

    def layers(rs, fg):
        return [SoleLayer(zone, rs), UpperLayer(form, u, cav, zt, aux["floor_z"], grid, rs, frame)]

    fine, fg, stats = build_fine(body, grid, layers, cfg.voxel_mm, cfg.pad_mm, progress)
    g = cfg.tpu_density_g_cm3
    openings = shell_openings(fine, fg, cav, grid, zt.astype(np.float32), aux["floor_z"], u, thick, min_open_mm=cfg.min_opening_mm)
    rep = {"form": form, "openings": openings, "thickness_mm": thick, "upper": u.knit.__dict__ if form == "knit" else u.volume.__dict__,
           "rim_mm": u.rim_mm, "sole_thickness_mm": u.sole_thickness_mm, "sole_pores": pores,
           "solid_body_cm3": round(stats.vol_base_mm3 / 1000, 2), "lattice_body_cm3": round(stats.vol_final_mm3 / 1000, 2),
           "saved_vs_solid_body_cm3": round((stats.vol_base_mm3 - stats.vol_final_mm3) / 1000, 2),
           "tpu_density_g_cm3": g, "frame": None if frame is None else {k: v for k, v in frame.items() if k in ("n_around", "r0", "circumference_mm")},
           "sole_zone": {k: zone.report[k] for k in ("columns_with_core", "islands_dropped", "core_thickness_mm")}}
    return fine, fg, rep
