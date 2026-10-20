"""Shape-freedom demo for the knit upper (#14 B-knit): a parameter space on top of the cavity of the last.

Everything the user sees is outside the cavity ``phi_C`` (the last + ease, fixed): the shell is ``0 <= phi_C < T(p)``
below a collar line ``z_t'(x, y)`` plus a sole slab; the yarn pattern replaces the solid shell above the sole.
The parameters (all in ``config/lattice_variants.toml``, one ``[[variant]]`` per look):

* outline    - ``bumps``: extra thickness dT as a product of windows along the foot axis (u), height above the floor and a
               normal weight (all / side / top / front / back); a bump can only thicken, the cavity is never entered.
* collar     - ``raise``: collar height offset along u (clamped under the top of the last), ``rim_mm`` solid band,
               ``rim_thick_mm`` thicker rim.
* sole       - thickness, flare, forefoot rocker, heel bevel, tread (chevron grooves); the A lattice fills it.
* mesh style - ``diamond`` / ``triangle`` (families of helices about the foot axis, exact 3D tube distance),
               ``hex`` / ``voronoi`` (pattern on the unrolled mid-surface chart, distance converted to world units).
* density    - ``pitch_pts`` (yarn spacing along the foot axis through a warp of x), ``yarn_pts_u`` / ``yarn_pts_z``
               (yarn diameter factor along u / along the height), ``image`` (yarn diameter from a picture).
* structure  - ``heel_counter_*`` and ``toe_cap_*`` solid patches (with extra thickness).

Hard limits: yarn diameter >= the process minimum everywhere (checked on the config before building, and measured);
no conformal coordinate system (the pattern is a function of x and the angle about the foot axis, so the spacing around
the foot follows the cross-section size); no laces, tongue or eyelets; the last fixes the cavity.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from .lattice import (LatticeConfig, LatticeError, Resampler, SoleLayer, SoleZone, _fill_nearest, _occ, _signed_2d, build_fine, cavity_floor,
                      check_radii, check_sole_params, knot_radii, strut_min_mm, u_of_x)
from .lattice_upper import MIN_SIN2, foot_frame, offset_field
from .process import CONFIG_DIR, ProcessConfig
from .sdf import Grid

STYLES = ("diamond", "triangle", "hex", "voronoi")
NORMALS = ("all", "side", "top", "front", "back")


@dataclass(frozen=True)
class VariantSpec:
    name: str = "base"
    description: str = ""
    style: str = "diamond"
    pitch_mm: float = 7.0
    helix_slope: float = 0.9
    ring_factor: float = 1.0
    jitter: float = 0.3
    thickness_mm: float = 2.4
    yarn_mm: float = 1.6
    rim_mm: float = 8.0
    rim_thick_mm: float = 0.0
    sole_mm: float = 12.0
    sole_margin_mm: float = 2.0
    sole_flare_mm: float = 0.0
    rocker_mm: float = 0.0
    rocker_start: float = 0.62
    heel_bevel_mm: float = 0.0
    tread_depth_mm: float = 0.0
    tread_pitch_mm: float = 9.0
    tread_width_mm: float = 3.5
    raise_pts: tuple = ((0.0, 0.0), (1.0, 0.0))
    pitch_pts: tuple = ()
    yarn_pts_u: tuple = ()
    yarn_pts_z: tuple = ()
    bumps: tuple = ()
    heel_counter_h_mm: float = 0.0
    heel_counter_u: float = 0.2
    toe_cap_h_mm: float = 0.0
    toe_cap_u: float = 0.85
    reinforce_dT_mm: float = 1.6
    image: str = ""
    image_dense_mm: float = 2.4
    image_sparse_mm: float = 1.2
    image_u: tuple = (0.15, 0.85)
    seed: int = 7
    voxel_mm: float = 0.3
    preview: bool = False

    @classmethod
    def from_dict(cls, raw: dict, defaults: dict | None = None) -> VariantSpec:
        d = {**(defaults or {}), **raw}
        names = {f.name for f in fields(cls)}
        bad = set(d) - names
        if bad:
            raise LatticeError(f"unknown variant keys {sorted(bad)}")
        for k in ("raise_pts", "pitch_pts", "yarn_pts_u", "yarn_pts_z", "image_u"):
            if k in d:
                d[k] = tuple(tuple(float(v) for v in p) if isinstance(p, (list, tuple)) else float(p) for p in d[k])
        if "bumps" in d:
            d["bumps"] = tuple(tuple(sorted(b.items())) for b in d["bumps"])
        return cls(**d)

    def bump_dicts(self):
        return [dict(b) for b in self.bumps]


def load_variants(path: str | Path | None = None) -> list[VariantSpec]:
    p = Path(path) if path else CONFIG_DIR / "lattice_variants.toml"
    if not p.exists() and (CONFIG_DIR / p).exists():
        p = CONFIG_DIR / p
    with open(p, "rb") as f:
        raw = tomllib.load(f)
    return [VariantSpec.from_dict(v, raw.get("defaults", {})) for v in raw["variant"]]


def check_variant(v: VariantSpec, cfg: LatticeConfig, process: ProcessConfig | None) -> dict:
    """Config-level checks (raise LatticeError): style, yarn diameters over every knot and the image range, thickness, voxel."""
    lo = strut_min_mm(cfg, process)
    if v.style not in STYLES:
        raise LatticeError(f"style must be one of {STYLES}, got {v.style!r}")
    d_all = [v.yarn_mm * f for _, f in v.yarn_pts_u] + [v.yarn_mm * f for _, f in v.yarn_pts_z] + [v.yarn_mm]
    if v.image:
        d_all += [v.image_sparse_mm, v.image_dense_mm]
    d_min, d_max = min(d_all), max(d_all)
    if d_min < lo * (1 - 1e-9):
        raise LatticeError(f"yarn diameter {d_min:.3f} mm < minimum {lo:g} mm")
    if v.thickness_mm < d_max - 1e-9:
        raise LatticeError(f"thickness {v.thickness_mm:g} mm < largest yarn diameter {d_max:g} mm")
    if v.pitch_mm <= 2 * d_max:
        raise LatticeError("pitch must exceed two yarn diameters")
    for b in v.bump_dicts():
        if b.get("normal", "all") not in NORMALS:
            raise LatticeError(f"bump normal must be one of {NORMALS}")
    sole_d = 2 * min(r for _, r in knot_radii(cfg.sole))
    printable = v.voxel_mm <= min(d_min, sole_d) / cfg.voxel_ratio * (1 + 1e-9)
    if not v.preview:
        check_radii([0.5 * d_min], lo, v.voxel_mm, cfg.voxel_ratio, what="yarn")
    return {"yarn_diameter_min_mm": round(d_min, 3), "yarn_diameter_max_mm": round(d_max, 3), "printable": bool(printable and not v.preview)}


# ----------------------------------------------------------------------------------------------- coarse preparation
@dataclass
class Prep:
    grid: Grid
    c0: np.ndarray  # offset-capable cavity field (unopened)
    zt: np.ndarray  # collar height per column
    normals: list  # unit gradient of c0
    floor: np.ndarray  # cavity floor height per column (nearest-extended)
    has: np.ndarray  # columns with cavity
    top: np.ndarray  # highest cavity height per column (0 where none)
    axis: dict


def prepare(c0_raw: np.ndarray, zt: np.ndarray, grid: Grid) -> Prep:
    c0 = offset_field(c0_raw, grid)
    zf, has = cavity_floor(c0_raw, grid)
    zf_ext = _fill_nearest(zf, has).astype(np.float32)
    z = grid.origin[2] + np.arange(grid.shape[2]) * grid.dz
    inside = c0_raw < 0
    top = np.where(inside.any(axis=2), z[grid.shape[2] - 1 - inside[:, :, ::-1].argmax(axis=2)], 0.0).astype(np.float32)
    g = np.gradient(c0, grid.h, grid.h, grid.dz)
    n = np.maximum(np.sqrt(g[0] ** 2 + g[1] ** 2 + g[2] ** 2), 1e-6)
    low = inside & (z[None, None, :] < (zf_ext + 25.0)[:, :, None])  # foot part of the cavity defines the axis
    xs = np.flatnonzero(low.any(axis=(1, 2)))
    width = low.any(axis=2).sum(axis=1)[xs[0] : xs[-1] + 1]
    q = max(len(width) // 4, 1)
    toe = "+x" if width[-q:].mean() >= width[:q].mean() else "-x"
    axis = {"x_min": float(grid.origin[0] + xs[0] * grid.h), "x_max": float(grid.origin[0] + xs[-1] * grid.h), "toe": toe}
    return Prep(grid, c0, zt.astype(np.float32), [(a / n).astype(np.float32) for a in g], zf_ext, has, top, axis)


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _interp_pts(pts, x, default):
    if not pts:
        return np.full(np.shape(x), default, np.float32)
    a = np.asarray(pts, float)
    return np.interp(x, a[:, 0], a[:, 1]).astype(np.float32)


def _window(t, lo, hi, ramp):
    return _smooth((t - lo) / max(ramp, 1e-6)) * _smooth((hi - t) / max(ramp, 1e-6))


def thickness_field(p: Prep, v: VariantSpec) -> np.ndarray:
    """T(x, y, z): base thickness plus the bumps and the reinforcement patches (3D coarse array)."""
    g = p.grid
    xs = g.origin[0] + np.arange(g.shape[0]) * g.h
    z = (g.origin[2] + np.arange(g.shape[2]) * g.dz).astype(np.float32)
    u = u_of_x(xs, p.axis).astype(np.float32)[:, None, None]
    hrel = z[None, None, :] - p.floor[:, :, None]
    sgn = 1.0 if p.axis["toe"] == "+x" else -1.0
    wn = {"all": np.float32(1.0), "side": p.normals[1] ** 2, "top": np.clip(p.normals[2], 0, 1) ** 2,
          "front": np.clip(sgn * p.normals[0], 0, 1) ** 2, "back": np.clip(-sgn * p.normals[0], 0, 1) ** 2}
    t = np.full(g.shape, v.thickness_mm, np.float32)
    bumps = v.bump_dicts()
    if v.heel_counter_h_mm > 0:
        bumps.append({"dT": v.reinforce_dT_mm, "u": [-0.2, v.heel_counter_u], "z": [-10.0, v.heel_counter_h_mm], "normal": "all", "ramp": 0.04, "ramp_z": 4.0})
    if v.toe_cap_h_mm > 0:
        bumps.append({"dT": v.reinforce_dT_mm, "u": [v.toe_cap_u, 1.2], "z": [-10.0, v.toe_cap_h_mm], "normal": "all", "ramp": 0.04, "ramp_z": 4.0})
    for b in bumps:
        ulo, uhi = b.get("u", [-1.0, 2.0])
        zlo, zhi = b.get("z", [-1e3, 1e3])
        t += np.float32(b["dT"]) * (_window(u, ulo, uhi, b.get("ramp", 0.12)) * _window(hrel, zlo, zhi, b.get("ramp_z", 6.0)) * wn[b.get("normal", "all")]).astype(np.float32)
    if v.rim_thick_mm:
        ztv = collar_line(p, v)
        t += np.float32(v.rim_thick_mm) * _smooth((z[None, None, :] - (ztv[:, :, None] - v.rim_mm - 4.0)) / 4.0)
    return np.maximum(t, np.float32(0.5))


def collar_line(p: Prep, v: VariantSpec) -> np.ndarray:
    """Collar height z_t' = z_t + raise(u), never closer than 8 mm to the top of the last."""
    g = p.grid
    xs = g.origin[0] + np.arange(g.shape[0]) * g.h
    r = _interp_pts(v.raise_pts, u_of_x(xs, p.axis), 0.0)[:, None]
    room = np.where(p.top > p.zt + 8.0, p.top - 8.0 - p.zt, 0.0)  # the last continues above the collar here: how far it may be raised
    return (p.zt + np.where(room > 0, np.clip(r, 0.0, room), np.maximum(r, 0.0))).astype(np.float32)


def sole_bottom(p: Prep, v: VariantSpec) -> np.ndarray:
    g = p.grid
    xs = g.origin[0] + np.arange(g.shape[0]) * g.h
    ys = g.origin[1] + np.arange(g.shape[1]) * g.h
    u = u_of_x(xs, p.axis)[:, None]
    zb = p.floor - v.sole_mm
    if v.rocker_mm:
        zb = zb + v.rocker_mm * _smooth((u - v.rocker_start) / (1.0 - v.rocker_start)) ** 1.5
    if v.heel_bevel_mm:
        zb = zb + v.heel_bevel_mm * _smooth((0.12 - u) / 0.12)
    if v.tread_depth_mm:
        f = (xs[:, None] + 0.6 * np.abs(ys)[None, :]) % v.tread_pitch_mm
        groove = _smooth((v.tread_width_mm * 0.5 - np.abs(f - v.tread_pitch_mm / 2) + 0.5) / 1.0)
        zb = zb + v.tread_depth_mm * groove
    return zb.astype(np.float32)


def shell_body_v(p: Prep, v: VariantSpec) -> tuple[np.ndarray, dict]:
    g = p.grid
    z = (g.origin[2] + np.arange(g.shape[2]) * g.dz).astype(np.float32)
    t3 = thickness_field(p, v)
    ztv = collar_line(p, v)
    shell = np.maximum(p.c0 - t3, -p.c0)
    shell = np.maximum(shell, z[None, None, :] - ztv[:, :, None])
    k = np.clip(np.round((p.floor - g.origin[2]) / g.dz).astype(int), 0, g.shape[2] - 1)
    t_floor = np.take_along_axis(t3, k[:, :, None], axis=2)[:, :, 0]
    foot = _signed_2d(p.has, g.h) - t_floor - np.float32(v.sole_flare_mm)
    zb = sole_bottom(p, v)
    slab = np.maximum(foot[:, :, None], np.maximum(zb[:, :, None] - z[None, None, :], z[None, None, :] - p.floor[:, :, None]))
    body = np.maximum(np.minimum(shell, slab), -p.c0).astype(np.float32)
    body[body == 0] = 1e-3
    return body, {"T": t3, "zt_var": ztv, "floor": p.floor}


# ----------------------------------------------------------------------------------------------- yarn layer
class KnitLayer2:
    """Yarn mesh on one fine x-plane, with every shape parameter of the spec."""

    reach = 2.0

    def __init__(self, p: Prep, v: VariantSpec, aux: dict, rs: Resampler, frame: dict, image: np.ndarray | None):
        self.p, self.v, self.aux, self.rs, self.frame, self.image = p, v, aux, rs, frame, image
        self.frame2, self.blend = leg_frame(p, v) if max(q[1] for q in v.raise_pts) > 0 and v.style in ("diamond", "triangle") else None, 6.0
        fg = rs.fine
        self.L = p.axis["x_max"] - p.axis["x_min"]
        u = u_of_x(rs.x, p.axis)
        m = _interp_pts(v.pitch_pts, u, 1.0).astype(np.float64)
        self.m = m
        self.X = np.cumsum(1.0 / m) * fg.h  # warp of x: local spacing along the foot axis scales with m
        self.g_u = _interp_pts(v.yarn_pts_u, u, 1.0)
        self.zc = (rs.z.min(), rs.z.max())
        self.r_min = np.inf
        self.r_max = 0.0
        self.sgn = 1.0 if p.axis["toe"] == "+x" else -1.0
        g = p.grid
        self.z_lo = float(p.floor[p.has].min() + v.sole_margin_mm)
        self.z_hi = float(aux["zt_var"][p.has].max())
        if v.style == "voronoi":
            n = frame["n_around"]
            jmax = int(math.ceil((self.X[-1] - self.X[0]) / (v.pitch_mm * 0.87))) + 4
            rng = np.random.default_rng(v.seed)
            jit = rng.uniform(-v.jitter, v.jitter, (n, jmax, 2)) * v.pitch_mm
            i, j = np.meshgrid(np.arange(n), np.arange(jmax), indexing="ij")
            s = (i + 0.5) * v.pitch_mm + jit[:, :, 0] + 0.5 * (j % 2) * v.pitch_mm
            x = self.X[0] - 2 * v.pitch_mm + j * v.pitch_mm * 0.87 + jit[:, :, 1]
            circ = n * v.pitch_mm
            pts = np.stack([s.ravel() - circ / 2, x.ravel()], axis=1)
            self.vor_circ = circ
            self.vor_tree = cKDTree(np.concatenate([pts - [circ, 0], pts, pts + [circ, 0]]))
            self.vor_pts = np.concatenate([pts - [circ, 0], pts, pts + [circ, 0]])

    # --- helpers
    def _theta(self, x, py, pz):
        f = self.frame
        yc, zc = np.interp(x, f["x"], f["yc"]), np.interp(x, f["x"], f["zc"])
        dyc = (np.interp(x + 1.0, f["x"], f["yc"]) - np.interp(x - 1.0, f["x"], f["yc"])) / 2.0
        dzc = (np.interp(x + 1.0, f["x"], f["zc"]) - np.interp(x - 1.0, f["x"], f["zc"])) / 2.0
        Y, Z = (py - yc).astype(np.float64), (pz - zc).astype(np.float64)
        rho2 = np.maximum(Y * Y + Z * Z, 4.0)
        th = np.stack([(-Y * dzc + Z * dyc) / rho2, -Z / rho2, Y / rho2])
        return np.arctan2(Z, Y), th

    def _tube(self, a_mid, b, c):
        den = np.maximum(1.0 - c * c, MIN_SIN2)
        return np.sqrt(np.maximum(a_mid * a_mid + b * b - 2.0 * a_mid * b * c, 0.0) / den)

    def _families(self, theta, th, ns, a_mid, r0, along, galong):
        """Distance to the yarn curves f = r0 * theta + b * along = const (families (1, +-s) and, for triangles, rings)."""
        v = self.v
        s, p = v.helix_slope, v.pitch_mm
        fam = [(1.0, s), (1.0, -s)]
        if v.style == "triangle":
            fam.append((0.0, v.ring_factor))
        best = np.full(len(theta), np.inf)
        for aa, bb in fam:
            fval = aa * r0 * theta + bb * along
            gf = aa * r0 * th + bb * galong
            gn = np.maximum(np.linalg.norm(gf, axis=0), 1e-6)
            b = ((fval + 0.5 * p) % p - 0.5 * p) / gn
            c = np.sum(ns * gf / gn, axis=0)
            best = np.minimum(best, self._tube(a_mid, b, c))
        return best

    def _theta_z(self, px, py, pz):
        """Angle about a vertical axis through the leg's centre line (ankle part of a raised collar)."""
        f = self.frame2
        xc, yc = np.interp(pz, f["z"], f["xc"]), np.interp(pz, f["z"], f["yc"])
        dxc = np.interp(pz + 1.0, f["z"], f["xc"]) - np.interp(pz - 1.0, f["z"], f["xc"])
        dyc = np.interp(pz + 1.0, f["z"], f["yc"]) - np.interp(pz - 1.0, f["z"], f["yc"])
        X, Y = (px - xc).astype(np.float64), (py - yc).astype(np.float64)
        rho2 = np.maximum(X * X + Y * Y, 4.0)
        th = np.stack([-Y / rho2, X / rho2, (Y * dxc / 2.0 - X * dyc / 2.0) / rho2])
        return np.arctan2(Y, X), th

    def _chart(self, ix, x, theta, th, ns, a_mid):
        v, f = self.v, self.frame
        r0, p = f["r0"], v.pitch_mm
        S, Xc = r0 * theta, self.X[ix] + np.zeros_like(theta)
        # chart gradient of the pattern distance d(S, X) -> world gradient (S = r0 theta, X' = 1/m)
        if v.style == "hex":
            py = p * math.sqrt(3.0)
            best = None
            for ox, oy in ((0.0, 0.0), (0.5 * p, 0.5 * py)):
                qs = (S - ox + 0.5 * p) % p - 0.5 * p
                qx = (Xc - oy + 0.5 * py) % py - 0.5 * py
                d2 = qs * qs + qx * qx
                if best is None:
                    best = (d2, qs, qx)
                else:
                    m = d2 < best[0]
                    best = (np.where(m, d2, best[0]), np.where(m, qs, best[1]), np.where(m, qx, best[2]))
            _, qs, qx = best
            nrm = np.array([[1.0, 0.0], [0.5, math.sqrt(3) / 2], [-0.5, math.sqrt(3) / 2]])
            proj = np.stack([qs * n[0] + qx * n[1] for n in nrm])
            k = np.argmax(np.abs(proj), axis=0)
            pk = np.take_along_axis(proj, k[None], axis=0)[0]
            d = 0.5 * p - np.abs(pk)
            gs = -np.sign(pk) * nrm[k, 0]
            gx = -np.sign(pk) * nrm[k, 1]
        else:  # voronoi
            Sw = np.mod(S + self.vor_circ / 2, self.vor_circ) - self.vor_circ / 2
            dist, idx = self.vor_tree.query(np.stack([Sw, Xc], axis=1), k=2)
            q = np.stack([Sw, Xc], axis=1)
            u1 = (q - self.vor_pts[idx[:, 0]]) / np.maximum(dist[:, :1], 1e-9)
            u2 = (q - self.vor_pts[idx[:, 1]]) / np.maximum(dist[:, 1:2], 1e-9)
            d = 0.5 * (dist[:, 1] - dist[:, 0])
            g = 0.5 * (u2 - u1)
            gs, gx = g[:, 0], g[:, 1]
        gw = gs * r0 * th
        gw[0] = gw[0] + gx / self.m[ix]
        gn = np.maximum(np.linalg.norm(gw, axis=0), 1e-6)
        b = d / gn
        c = np.sum(ns * gw / gn, axis=0)
        return self._tube(a_mid, b, c)

    def _diameter(self, ix, px, pz, zt_pt, zf_pt):
        v = self.v
        d = np.full(len(px), v.yarn_mm * float(self.g_u[ix]), np.float64)
        if v.yarn_pts_z:
            t = (pz - zf_pt) / np.maximum(zt_pt - zf_pt, 1.0)
            d = d * np.interp(t, [q[0] for q in v.yarn_pts_z], [q[1] for q in v.yarn_pts_z])
        if self.image is not None:
            u = float(u_of_x(px[0], self.p.axis))
            lo, hi = v.image_u
            H, W = self.image.shape
            cx = (u - lo) / (hi - lo) * (W - 1)
            cy = (self.z_hi - pz) / max(self.z_hi - self.z_lo, 1.0) * (H - 1)
            val = ndimage.map_coordinates(self.image, [cy, np.full(len(pz), cx)], order=1, mode="nearest")
            inside = (u >= lo) & (u <= hi)
            d = np.where(inside, v.image_sparse_mm + (v.image_dense_mm - v.image_sparse_mm) * val, d)
        return d

    def apply(self, base, ix, stats):
        rs, v, p, aux = self.rs, self.v, self.p, self.aux
        zz = rs.z[None, :]
        x = float(rs.x[ix])
        u = float(u_of_x(x, p.axis))
        zf, zt = rs.row(aux["floor"], ix), rs.row(aux["zt_var"], ix)
        phi_z = np.maximum((zf + np.float32(v.sole_margin_mm))[:, None] - zz, zz - (zt - np.float32(v.rim_mm))[:, None])
        for hh, uu, sign in ((v.heel_counter_h_mm, v.heel_counter_u, 1.0), (v.toe_cap_h_mm, v.toe_cap_u, -1.0)):
            if hh > 0:
                xterm = (u - uu) * self.L if sign > 0 else (uu - u) * self.L  # negative inside the patch (u range)
                zterm = zz - (zf[:, None] + np.float32(hh))
                phi_z = np.maximum(phi_z, -np.maximum(np.float32(xterm), zterm))
        if (phi_z >= self.reach).all():
            return base
        cand = (phi_z < self.reach) & (base < 1.0)
        if not cand.any():
            return base
        iy, iz = np.nonzero(cand)
        px, py, pz = np.full(len(iy), x, np.float32), rs.y[iy], rs.z[iz]
        c0 = rs.plane(p.c0, ix)[iy, iz].astype(np.float64)
        T = rs.plane(aux["T"], ix)[iy, iz].astype(np.float64)
        ns = np.stack([rs.plane(g, ix)[iy, iz] for g in p.normals]).astype(np.float64)
        ns = ns / np.maximum(np.linalg.norm(ns, axis=0), 1e-6)
        theta, th = self._theta(x, py, pz)
        a_mid = c0 - 0.5 * T
        dist = (self._families(theta, th, ns, a_mid, self.frame["r0"], self.X[ix], np.array([[1.0 / self.m[ix]], [0.0], [0.0]]))
                if v.style in ("diamond", "triangle") else self._chart(ix, x, theta, th, ns, a_mid))
        if self.frame2 is not None and v.style in ("diamond", "triangle"):
            # raised collar: above the old collar line the leg is a vertical tube, so the yarns wind about a vertical axis
            s_ = pz - rs.row(p.zt, ix)[iy]
            up = s_ > -self.blend
            both = up & (s_ < self.blend)
            if up.any():
                th2, g2 = self._theta_z(px[up], py[up], pz[up])
                d2 = self._families(th2, g2, ns[:, up], a_mid[up], self.frame2["r0"], pz[up].astype(np.float64), np.array([[0.0], [0.0], [1.0]]))
                dist = np.where(s_ > self.blend, np.inf, dist)
                dist[up] = np.minimum(dist[up], d2)
        dia = self._diameter(ix, px, pz, zt[iy], zf[iy])
        pz_ = phi_z[iy, iz]
        zm = pz_ < 0
        if zm.any():
            self.r_min = min(self.r_min, float(dia[zm].min()))
            self.r_max = max(self.r_max, float(dia[zm].max()))
        d = (dist - 0.5 * dia).astype(np.float32)
        new = np.maximum(base[iy, iz], np.minimum(-pz_, d))
        h = rs.fine.h
        stats.zone_base_mm3 += float(_occ(base[iy, iz][zm], h).sum()) * h**3
        stats.zone_final_mm3 += float(_occ(new[zm], h).sum()) * h**3
        out = base.copy()
        out[iy, iz] = new
        return out


def leg_frame(p: Prep, v: VariantSpec) -> dict | None:
    """Centre line (x_c(z), y_c(z)) of the cavity above the old collar line and the number of yarns around it."""
    g = p.grid
    z = g.origin[2] + np.arange(g.shape[2]) * g.dz
    k0 = int(np.searchsorted(z, float(p.zt[p.has].min()) - 10.0))
    sub = p.c0[:, :, k0:] < 0
    cnt = sub.sum(axis=(0, 1)).astype(float)
    ok = cnt > 30
    if ok.sum() < 5:
        return None
    xs = g.origin[0] + np.arange(g.shape[0]) * g.h
    ys = g.origin[1] + np.arange(g.shape[1]) * g.h
    xc = (sub.sum(axis=1) * xs[:, None]).sum(axis=0) / np.maximum(cnt, 1)
    yc = (sub.sum(axis=0) * ys[:, None]).sum(axis=0) / np.maximum(cnt, 1)
    zz = z[k0:]
    sm = lambda a: ndimage.gaussian_filter1d(a[ok], 3.0, mode="nearest")  # noqa: E731
    circ = 2.0 * math.pi * math.sqrt(float(np.median(cnt[ok])) * g.h * g.h / math.pi)
    n = max(int(round(circ / v.pitch_mm)), 6)
    return {"z": zz[ok], "xc": sm(xc), "yc": sm(yc), "n_around": n, "r0": n * v.pitch_mm / (2.0 * math.pi)}


def make_test_image(shape=(160, 400)) -> np.ndarray:
    """Plain test picture for the image-driven density (own drawing: ring, star and a bar; 1 = dense, 0 = sparse)."""
    from PIL import Image, ImageDraw

    h, w = shape
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    d.ellipse((20, 20, 140, 140), fill=0)
    d.ellipse((50, 50, 110, 110), fill=255)
    cx, cy, r1, r2 = 240, 80, 70, 30
    d.polygon([(cx + (r1 if i % 2 == 0 else r2) * math.sin(i * math.pi / 5), cy - (r1 if i % 2 == 0 else r2) * math.cos(i * math.pi / 5)) for i in range(10)], fill=0)
    d.rectangle((330, 30, 370, 130), fill=0)
    return 1.0 - np.asarray(im, np.float32) / 255.0


def build_variant(prep: Prep, v: VariantSpec, cfg: LatticeConfig, process: ProcessConfig | None, progress=None):
    """Build one variant. Returns (fine phi, fine grid, report dict)."""
    chk = check_variant(v, cfg, process)
    cfg_v = LatticeConfig(**{**cfg.__dict__, "voxel_mm": v.voxel_mm})
    body, aux = shell_body_v(prep, v)
    zone = SoleZone.build(body, prep.c0, prep.grid, cfg.sole)
    pores = check_sole_params(cfg_v, process, zone) if not v.preview else {}
    frame = foot_frame(prep.c0, prep.zt, prep.grid, v.pitch_mm)
    img = None
    if v.image:
        if v.image == "test":
            img = make_test_image()
        else:
            from PIL import Image

            img = 1.0 - np.asarray(Image.open(v.image).convert("L"), np.float32) / 255.0
    holder = {}

    def layers(rs, fg):
        holder["knit"] = KnitLayer2(prep, v, aux, rs, frame, img)
        return [SoleLayer(zone, rs), holder["knit"]]

    fine, fg, stats = build_fine(body, prep.grid, layers, v.voxel_mm, cfg.pad_mm, progress)
    k = holder["knit"]
    meas = {"yarn_diameter_used_min_mm": round(k.r_min, 3) if np.isfinite(k.r_min) else None, "yarn_diameter_used_max_mm": round(k.r_max, 3)}
    rep = {"name": v.name, "style": v.style, "check": chk, "measured": meas, "voxel_mm": v.voxel_mm, "printable": chk["printable"],
           "solid_body_cm3": round(stats.vol_base_mm3 / 1000, 2), "lattice_body_cm3": round(stats.vol_final_mm3 / 1000, 2),
           "sole_pores": pores, "frame": {kk: frame[kk] for kk in ("n_around", "r0", "circumference_mm")},
           "collar_z_max_mm": round(float(aux["zt_var"][prep.has].max()), 1), "thickness_max_mm": round(float(aux["T"].max()), 2),
           "sole_zone": {kk: zone.report[kk] for kk in ("columns_with_core", "islands_dropped", "core_thickness_mm")}}
    return fine, fg, rep, aux
