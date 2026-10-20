"""Conformal flow-aligned yarn pattern for the knit upper (#46 part A, checkpoint-1 prototype).

The yarn pattern of ``lattice_variants`` is evaluated in a chart ``(S = r0 * theta, X(x))`` (angle about the foot axis, a warp
of x): its cells have straight edges in that chart and no relation to the shape of the shoe (swirl at the toe, cell size
that follows the cross-section only through ``pitch_pts``). This module replaces the chart by a *flow chart*:

1. ``(p, t)``: ``p = r0 * theta`` (circumferential, period ``C = n * pitch``), ``t = r0 * integral dx / rho(x)`` with
   ``rho(x)`` the equivalent radius of the cavity cross-section. For a surface of revolution ``(theta, integral dx/rho)`` is
   conformal, so cells are about as wide as long and their size follows the cross-section (denser at the narrow heel).
2. A flow field in the chart: the yarn direction ``alpha(p, t)`` (angle from the foot axis) = mean sweep along the foot axis
   plus a first harmonic about the foot. Streamlines are integrated from a base line ``t = t0``; their label is ``psi``
   (``= p`` on the base line) and the arc length along them ``phi``. Both are stored as tables on the ``(p, t)`` grid.
3. Voronoi seeds on a jittered hex lattice in ``(phi / elongate, psi)``: cells are elongated along the flow, the rods (cell
   boundaries) are curves that follow the flow and bend with it.
4. The distance to a rod is taken in world units: the gradient of the cell-boundary distance is pushed through the chain
   ``(phi, psi) -> (p, t) -> world`` and the exact tube formula of ``lattice_upper`` (``d^2 = (a^2 + b^2 - 2abc)/(1 - c^2)``)
   makes it a round tube of the set diameter. The rod diameter is therefore a world measure, independent of the chart.

Reported: the chart distortion sigma (anisotropy and size change of a cell of the chart in world space) and the flow
compression (how much neighbouring streamlines converge). Both are limited by the config; a flow that converges too much is
refused. Not solved here: the pole at the toe tip and the heel (cross-section -> 0): rho is floored at ``rho_floor_frac * r0``.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from .lattice import LatticeError, u_of_x
from .lattice_variants import KnitLayer2, Prep, VariantSpec
from .process import CONFIG_DIR


@dataclass(frozen=True)
class ConformalSpec(VariantSpec):
    conformal: bool = True
    flow_sweep: tuple = ((0.0, 0.0), (1.0, 0.0))  # mean yarn angle from the foot axis [deg] along u (0 heel .. 1 toe)
    flow_amp_deg: float = 0.0  # first harmonic about the foot axis [deg]
    flow_amp_pts: tuple = ((0.0, 1.0), (1.0, 1.0))
    flow_theta0_deg: float = 90.0  # angle (about the foot axis, 0 = +y, 90 = up) where the harmonic is largest
    flow_base_u: float = 0.6
    rho_floor_frac: float = 0.55
    chart_step_mm: float = 0.5
    flow_max_deg: float = 70.0
    chart: str = "lscm"  # "lscm" (mid-surface conformal chart, falls back to the analytic chart if the mesh is not one disk) or "analytic"
    flow_cross_deg: float = 0.0  # lscm chart: lean of the yarns away from the top line, alpha = sweep - cross * v_n (v_n = -1 .. 1 across the foot)
    lscm_target_faces: int = 40000
    lloyd_iters: int = 6  # voronoi in the lscm chart: seeds are placed for equal WORLD cell area (the conformal factor varies 0.8-3x) and relaxed this often
    lscm_pin_fracs: tuple = (0.2, 0.8)
    seam_solid_mm: float = 8.0  # half width of the solid strip over the heel seam
    seam_x_frac: float = 0.30
    reach_mm: float = 3.0  # shell points farther than this from the chart mesh stay solid
    sole_pad_mm: float = 2.5  # sole_mode = "net": solid pad plate under the foot
    sole_pad_hole_mm: float = 6.0
    sole_pad_hole_pitch_mm: float = 16.0
    sole_wall_mm: float = 3.4  # outer wall net thickness
    sole_wall_rod_mm: float = 3.0
    sole_pitch_mm: float = 16.0  # outer wall net cell size
    sole_el: float = 1.4
    sole_ring_mm: float = 6.0  # bottom edge bead diameter (0 = none)
    sole_wall_calib: float = 0.7  # measured wall volume of a jittered Voronoi with fillets is ~1.4x the thin-wall estimate 2.1 t / pitch
    outline: tuple = ()  # outline functions (shoe_designer.outline), each a dict: {fn = "toe", kind = "high", ...}
    sole_bulge_mm: float = 0.0  # net sole: inset of the bottom edge (curved, bulging side wall)
    sole_bulge_power: float = 1.5
    sole_rocker2_mm: float = 0.0  # net sole: cradle (extra rise of the bottom towards the toe and the sides)
    sole_rocker2_start: float = 0.55
    sole_cradle_mm: float = 0.0
    tab_w_mm: float = 0.0  # flat pull tab (0 = none)
    tab_t_mm: float = 2.6
    tab_d_mm: float = 26.0
    flow_compress: tuple = (0.55, 1.8)  # allowed range of d psi / d p (width of a flow tube relative to the base line)
    # width of a cell (across the flow) relative to the along-flow length: elongate = length / width

    @classmethod
    def from_dict(cls, raw: dict, defaults: dict | None = None):
        d = {**(defaults or {}), **raw}
        names = {f.name for f in fields(cls)}
        bad = set(d) - names
        if bad:
            raise LatticeError(f"unknown conformal keys {sorted(bad)}")
        for k in ("raise_pts", "pitch_pts", "yarn_pts_u", "yarn_pts_z", "image_u", "sole_density", "flow_sweep", "flow_amp_pts", "flow_compress", "lscm_pin_fracs"):
            if k in d:
                d[k] = tuple(tuple(float(v) for v in p) if isinstance(p, (list, tuple)) else float(p) for p in d[k])
        if "bumps" in d:
            d["bumps"] = tuple(tuple(sorted(b.items())) for b in d["bumps"])
        if "outline" in d:
            d["outline"] = tuple(tuple(sorted(o.items())) for o in d["outline"])
        return cls(**d)


def load_specs(path: str | Path | None = None) -> list[ConformalSpec]:
    p = Path(path) if path else CONFIG_DIR / "lattice_conformal.toml"
    if not p.exists() and (CONFIG_DIR / p).exists():
        p = CONFIG_DIR / p
    with open(p, "rb") as f:
        raw = tomllib.load(f)
    return [ConformalSpec.from_dict(v, raw.get("defaults", {})) for v in raw["variant"]]


# ----------------------------------------------------------------------------------------------- chart (p, t)
def equivalent_radius(p: Prep, x: np.ndarray, smooth_mm: float = 5.0) -> np.ndarray:
    """rho(x) = sqrt(area / pi) of the cavity cross-section below the collar, at the x of the array (smoothed)."""
    g = p.grid
    z = g.origin[2] + np.arange(g.shape[2]) * g.dz
    inside = (p.c0 < 0) & (z[None, None, :] < p.zt[:, :, None] - 5.0)
    area = inside.sum(axis=(1, 2)).astype(float) * g.h * g.dz
    xs = g.origin[0] + np.arange(g.shape[0]) * g.h
    ok = area > 50.0
    rho = np.sqrt(area / math.pi)
    rho = ndimage.gaussian_filter1d(np.where(ok, rho, 0.0), smooth_mm / g.h, mode="nearest")
    return np.interp(x, xs[ok], rho[ok])


class FlowChart:
    """Tables of the flow chart on a regular (t, p) grid (p periodic with period C)."""

    def __init__(self, p: Prep, v: ConformalSpec, frame: dict):
        self.v, self.p = v, p
        self.r0, self.C = float(frame["r0"]), 2.0 * math.pi * float(frame["r0"])
        self.n = int(frame["n_around"])
        self.axis = p.axis
        self.sgn = 1.0 if p.axis["toe"] == "+x" else -1.0
        self.alpha_ref = math.radians(float(np.interp(v.flow_base_u, [q[0] for q in v.flow_sweep], [q[1] for q in v.flow_sweep])))
        self._contours(p, frame, v)
        # mean t(x) (u of a chart row, base line): the chart t itself depends on (x, theta), see _contours
        self.xn = self.cx
        self.t_nodes = self.ttab.mean(axis=1)
        self._build()

    def _contours(self, p: Prep, frame: dict, v):
        """Arc-length circumferential coordinate: p(x, theta) = C * (arc length from theta = 0) / perimeter(x), on 1 mm rows in x."""
        step = 1.0
        xs = np.arange(self.axis["x_min"] - 6.0, self.axis["x_max"] + 6.0 + 0.5, step)
        nth = 256
        th = np.linspace(-math.pi, math.pi, nth, endpoint=False)
        P = midsurface_points(p, frame, xs, th, 0.5 * v.thickness_mm)
        valid = np.isfinite(P).all(axis=(1, 2))
        seg = np.linalg.norm(np.roll(P, -1, axis=1) - P, axis=-1)
        per = np.where(valid, seg.sum(axis=1), np.nan)
        # a closed contour must also be a single sweep: reject rows whose perimeter jumps (ray escapes through the collar opening)
        area_rho = equivalent_radius(p, xs)
        rho_c = np.where(valid, per / (2 * math.pi), area_rho)
        bad = valid & (np.abs(rho_c / np.maximum(area_rho, 1.0) - 1.0) > 0.6)
        valid &= ~bad
        rho_c = np.where(valid, per / (2 * math.pi), area_rho)
        rho_c = ndimage.gaussian_filter1d(rho_c, 3.0, mode="nearest")
        cum = np.cumsum(seg, axis=1) - seg  # arc length from theta = -pi
        s0 = np.array([np.interp(0.0, th, cum[i]) if valid[i] else 0.0 for i in range(len(xs))])
        ptab = np.where(valid[:, None], self.C * (cum - s0[:, None]) / np.where(valid, per, 1.0)[:, None], self.r0 * th[None, :])
        # meridian stretch: |dP/dx| normal to the circumferential tangent (the surface is not a cylinder: the vamp is steep)
        Px = np.gradient(np.nan_to_num(P, nan=0.0), step, axis=0)
        T = np.roll(P, -1, axis=1) - np.roll(P, 1, axis=1)
        T = np.nan_to_num(T / np.maximum(np.linalg.norm(T, axis=-1, keepdims=True), 1e-9), nan=0.0)
        perp = Px - np.sum(Px * T, axis=-1, keepdims=True) * T
        m_c = np.where(valid, np.linalg.norm(perp, axis=-1).mean(axis=1), 1.0)
        m_c = ndimage.gaussian_filter1d(np.clip(m_c, 1.0, 2.5), 3.0, mode="nearest")
        # t(x) = r0 * integral of (mean meridian stretch) / rho dx. A per-theta stretch (isometric along every meridian) was tried and
        # is not usable: the table is noisy and its Jacobian degenerates (sigma p95 > 10).
        ttab = np.repeat((np.cumsum(self.r0 * m_c / np.maximum(rho_c, self.v.rho_floor_frac * self.r0)) * step)[:, None], len(th), axis=1)
        self.cx, self.rho_c, self.m_c, self.cth, self.ptab, self.ttab, self.cvalid, self.cstep = xs, rho_c, m_c, th, ptab, ttab, valid, step

    def _tab(self, tab, x, theta):
        """(value, d/dtheta, d/dx) of a (row, angle) table at plane x for angles ``theta`` in (-pi, pi]; bilinear."""
        fx = float(np.clip((x - self.cx[0]) / self.cstep, 0, len(self.cx) - 1.001))
        i = int(fx)
        w = fx - i
        row = (1 - w) * tab[i] + w * tab[i + 1]
        i0, i1 = max(i - 1, 0), min(i + 2, len(self.cx) - 1)
        dx = (tab[i1] - tab[i0]) / ((i1 - i0) * self.cstep)
        thx = np.concatenate([self.cth[-1:] - 2 * math.pi, self.cth, self.cth[:2] + 2 * math.pi])
        shift = self.C if tab is self.ptab else 0.0
        pr = np.concatenate([row[-1:] - shift, row, row[:2] + shift])
        dr = np.concatenate([dx[-1:], dx, dx[:2]])
        dth = np.gradient(pr, thx)
        return np.interp(theta, thx, pr), np.interp(theta, thx, dth), np.interp(theta, thx, dr)

    def pt_of(self, x: float, theta):
        """(p, p_theta, p_x, t, t_theta, t_x) at plane x."""
        return self._tab(self.ptab, x, theta) + self._tab(self.ttab, x, theta)

    def t_of_x(self, x):
        return np.interp(x, self.xn, self.t_nodes)

    def x_of_t(self, t):
        return np.interp(t, self.t_nodes, self.xn)

    # flow angle (rad) from the foot axis at chart point (p, t)
    def alpha(self, pp, tt):
        v = self.v
        u = u_of_x(self.x_of_t(tt), self.axis)
        sw = np.interp(u, [q[0] for q in v.flow_sweep], [q[1] for q in v.flow_sweep])
        am = np.interp(u, [q[0] for q in v.flow_amp_pts], [q[1] for q in v.flow_amp_pts])
        a = sw + v.flow_amp_deg * am * np.cos(pp / self.r0 - math.radians(v.flow_theta0_deg))
        return np.radians(np.clip(a, -v.flow_max_deg, v.flow_max_deg))

    def _build(self):
        v, C = self.v, self.C
        h = v.chart_step_mm
        nt = len(self.t_nodes)
        tg = np.arange(float(self.ttab.min()), float(self.ttab.max()) + h, h)
        self.tg = tg
        nT = len(tg)
        k0 = int(np.argmin(np.abs(tg - self.t_of_x(self.axis["x_min"] + v.flow_base_u * (self.axis["x_max"] - self.axis["x_min"]) if self.sgn > 0
                                                  else self.axis["x_max"] - v.flow_base_u * (self.axis["x_max"] - self.axis["x_min"])))))
        ns = int(round(3 * C / h)) + 1
        psi = np.linspace(-C, 2 * C, ns)
        P = np.empty((nT, ns))
        F = np.empty((nT, ns))
        P[k0], F[k0] = psi, 0.0
        for direction in (1, -1):
            pc, fc = psi.copy(), np.zeros(ns)
            k = k0
            while 0 <= k + direction < nT:
                dt = direction * h
                a1 = self.alpha(pc, tg[k])
                pm = pc + 0.5 * dt * np.tan(a1)
                a2 = self.alpha(pm, tg[k] + 0.5 * dt)
                pc = pc + dt * np.tan(a2)
                fc = fc + abs(dt) / np.cos(a2) * direction
                k += direction
                P[k], F[k] = pc, fc
        # on the grid p in [0, C): psi(p, t), phi(p, t) by inverting the (monotone) map psi -> p at every t
        npg = int(round(C / h))
        self.pg = np.arange(npg) * (C / npg)
        pad = 4
        DEL = np.empty((nT, npg))
        PHI = np.empty((nT, npg))
        comp_lo, comp_hi = np.inf, 0.0
        for k in range(nT):
            dp = np.diff(P[k])
            if (dp <= 0).any():
                raise LatticeError("flow streamlines cross: reduce flow_amp_deg / flow_sweep")
            if P[k][0] > self.pg[0] - 1e-6 or P[k][-1] < self.pg[-1] + 1e-6:
                raise LatticeError("flow shifts the pattern by more than a period: reduce the sweep")
            DEL[k] = np.interp(self.pg, P[k], psi) - self.pg
            PHI[k] = np.interp(self.pg, P[k], F[k])
            w = (dp / np.diff(psi))
            m = (P[k][:-1] > 0) & (P[k][:-1] < C)
            comp_lo, comp_hi = min(comp_lo, 1.0 / w[m].max()), max(comp_hi, 1.0 / w[m].min())  # d psi / d p
        self.psi_p_range = (float(comp_lo), float(comp_hi))
        lo, hi = v.flow_compress
        if comp_lo < lo or comp_hi > hi:
            raise LatticeError(f"flow compression d(psi)/d(p) in [{comp_lo:.2f}, {comp_hi:.2f}] outside the allowed [{lo}, {hi}]")
        self.phi_range = (float(PHI.min()), float(PHI.max()))
        # gradients (central differences; p periodic)
        dDp = (np.roll(DEL, -1, axis=1) - np.roll(DEL, 1, axis=1)) / (2 * C / npg)
        dPp = (np.roll(PHI, -1, axis=1) - np.roll(PHI, 1, axis=1)) / (2 * C / npg)
        dDt = np.gradient(DEL, h, axis=0)
        dPt = np.gradient(PHI, h, axis=0)
        tab = np.stack([DEL, PHI, dDp, dPp, dDt, dPt])  # delta = psi - p
        self.tab = np.concatenate([tab[:, :, -pad:], tab, tab[:, :, :pad]], axis=2)  # wrap padding
        self.pad = pad
        self.h = h
        self.npg = npg

    def eval(self, pp, tt):
        """(psi, phi, psi_p, psi_t, phi_p, phi_t) at chart points (p wrapped into [0, C))."""
        pw = np.mod(pp, self.C)
        ci = np.clip((tt - self.tg[0]) / self.h, 0, len(self.tg) - 1)
        cj = pw / (self.C / self.npg) + self.pad
        out = [ndimage.map_coordinates(self.tab[i], [ci, cj], order=1, mode="nearest") for i in range(6)]
        dl, ph, ddp, dpp, ddt, dpt = out
        return pp + dl, ph, 1.0 + ddp, ddt, dpp, dpt


# ----------------------------------------------------------------------------------------------- the layer
class ConformalKnit(KnitLayer2):
    """KnitLayer2 whose Voronoi chart is the flow chart (``style = 'voronoi'`` in the spec)."""

    def __init__(self, p, v, aux, rs, frame, image, chart: FlowChart):
        v0 = v
        super().__init__(p, v0, aux, rs, frame, image)
        self.chart = chart
        el = v.elongate
        C = chart.C
        n = chart.n
        pitch = v.pitch_mm
        # seeds: jittered hex lattice in the *true* chart geometry (c1, c2) = (S / elongate, N) with S = phi + psi sin(a), N = psi cos(a)
        # (a = alpha_ref: the orthogonalised flow coordinates), n columns; periodic under psi -> psi + C, which is (C sin a / el, C cos a)
        sa, ca = math.sin(chart.alpha_ref), math.cos(chart.alpha_ref)
        self.sa, self.ca = sa, ca
        f0, f1 = chart.phi_range
        pw = C * ca / n
        dc1 = C * sa / (el * n)
        jmax = int(math.ceil((f1 - f0 + C * abs(sa)) / el / (pitch * 0.87))) + 6
        rng = np.random.default_rng(v.seed)
        jit = rng.uniform(-v.jitter, v.jitter, (n, jmax, 2)) * pitch
        i, j = np.meshgrid(np.arange(n), np.arange(jmax), indexing="ij")
        c2 = (i + 0.5) * pw + 0.5 * (j % 2) * pw + jit[:, :, 0]
        c1 = (f0 - 0.0) / el - 3 * pitch - C * abs(sa) / el + j * pitch * 0.87 + i * dc1 + jit[:, :, 1]
        pts = np.stack([c1.ravel(), c2.ravel()], axis=1)
        sh = np.array([C * sa / el, C * ca])
        self.seed_pts = np.concatenate([pts - sh, pts, pts + sh])
        self.seed_tree = cKDTree(self.seed_pts)

    def _chart(self, ix, x, theta, th, ns, a_mid):
        v, ch = self.v, self.chart
        el = v.elongate
        pp, p_th, p_x, tt, t_th, t_x = ch.pt_of(x, theta)
        psi, phi, psi_p, psi_t, phi_p, phi_t = ch.eval(pp, tt)
        psi_w = np.mod(psi, ch.C)
        q = np.stack([(phi + psi_w * self.sa) / el, psi_w * self.ca], axis=1)
        dist, idx = self.seed_tree.query(q, k=3)
        u = [(q - self.seed_pts[idx[:, i]]) / np.maximum(dist[:, i : i + 1], 1e-9) for i in range(3)]
        d01, d02 = 0.5 * (dist[:, 1] - dist[:, 0]), 0.5 * (dist[:, 2] - dist[:, 0])
        g01, g02 = 0.5 * (u[1] - u[0]), 0.5 * (u[2] - u[0])
        k = v.fillet_mm
        if k > 0:
            m = np.minimum(d01, d02)
            e1, e2 = np.exp(-(d01 - m) / k), np.exp(-(d02 - m) / k)
            d = m - k * np.log(e1 + e2)
            g = (e1[:, None] * g01 + e2[:, None] * g02) / (e1 + e2)[:, None]
        else:
            d, g = d01, g01
        # gradient in (p, t), then in the world: grad p = r0 grad theta, grad t = (dt/dx, 0, 0)
        # c1 = (phi + psi sa) / el, c2 = psi ca
        gp = g[:, 0] * (phi_p + self.sa * psi_p) / el + g[:, 1] * self.ca * psi_p
        gt = g[:, 0] * (phi_t + self.sa * psi_t) / el + g[:, 1] * self.ca * psi_t
        gw = (gp * p_th + gt * t_th) * th
        gw[0] = gw[0] + gp * p_x + gt * t_x
        gn = np.maximum(np.linalg.norm(gw, axis=0), 1e-6)
        b = d / gn
        c = np.sum(ns * gw / gn, axis=0)
        return self._tube(a_mid, b, c)


# ----------------------------------------------------------------------------------------------- distortion sigma
def midsurface_points(p: Prep, frame: dict, xs: np.ndarray, thetas: np.ndarray, level: float, rmax: float = 120.0, dr: float = 0.5):
    """World points P(x, theta) where phi_C = level, first crossing of a ray from the foot-axis centre line (nan if none)."""
    out = [midsurface_points_chunk(p, frame, xs[i : i + 40], thetas, level, rmax, dr) for i in range(0, len(xs), 40)]
    return np.concatenate(out, axis=0)


def midsurface_points_chunk(p, frame, xs, thetas, level, rmax, dr):
    g = p.grid
    yc = np.interp(xs, frame["x"], frame["yc"])
    zc = np.interp(xs, frame["x"], frame["zc"])
    r = np.arange(0.0, rmax, dr)
    X, TH, R = np.meshgrid(xs, thetas, r, indexing="ij")
    Y = yc[:, None, None] + R * np.cos(TH)
    Z = zc[:, None, None] + R * np.sin(TH)
    ci = [(X - g.origin[0]) / g.h, (Y - g.origin[1]) / g.h, (Z - g.origin[2]) / g.dz]
    val = ndimage.map_coordinates(p.c0, [a.ravel() for a in ci], order=1, mode="nearest").reshape(X.shape)
    above = val >= level
    first = above.argmax(axis=2)
    has = above.any(axis=2) & (first > 0)
    k = np.clip(first, 1, len(r) - 1)
    v1 = np.take_along_axis(val, k[..., None], 2)[..., 0]
    v0 = np.take_along_axis(val, (k - 1)[..., None], 2)[..., 0]
    rr = r[k - 1] + dr * (level - v0) / np.maximum(v1 - v0, 1e-6)
    P = np.stack([np.broadcast_to(xs[:, None], rr.shape), yc[:, None] + rr * np.cos(thetas)[None, :], zc[:, None] + rr * np.sin(thetas)[None, :]], axis=-1)
    P[~has] = np.nan
    return P


def chart_distortion(P: np.ndarray, xs: np.ndarray, thetas: np.ndarray, Jc: np.ndarray) -> dict:
    """Singular values of d(world) / d(c), c = (phi, psi) the unit-speed flow coordinates. ``P`` (nx, nth, 3) mid-surface points
    on the (x, theta) grid, ``Jc`` (nx, nth, 2, 2) = d(c) / d(x, theta). Ideal: both singular values equal (conformal), and their
    product (cell area per chart unit) constant. The elongation of the cells is a separate, intended anisotropy."""
    Px = np.gradient(P, float(xs[1] - xs[0]), axis=0)
    Pth = np.gradient(P, float(thetas[1] - thetas[0]), axis=1)
    A = np.stack([Px, Pth], axis=-1)  # (nx, nth, 3, 2)
    M = A @ np.linalg.inv(Jc)
    sv = np.linalg.svd(np.nan_to_num(M, nan=0.0), compute_uv=False)
    return {"s1": sv[..., 0], "s2": sv[..., 1], "P": P}


# ----------------------------------------------------------------------------------------------- independent measurements
def rod_diameters(solid: np.ndarray, h: float, region: np.ndarray | None = None, target: float | None = None) -> dict:
    """Rod diameter measured on the *binary* voxels, independent of the field that made them: twice the distance to the nearest
    air voxel centre at the skeleton (medial axis) of the solid (the centre of the first air voxel lies on average half a voxel outside
    the surface, which cancels the loss from the skeleton voxel not being on the axis; the tube test in tests/test_lattice_conformal.py calibrates it). ``region`` limits the skeleton voxels
    that are counted (the yarn zone; the rim, base and patch edge are excluded)."""
    from skimage.morphology import skeletonize

    edt = ndimage.distance_transform_edt(solid, return_distances=True).astype(np.float32)
    sk = skeletonize(solid)
    if region is not None:
        sk &= region
    d = 2.0 * ndimage.maximum_filter(edt, size=3)[sk] * h  # ridge value: the skeleton voxel is up to ~0.4 voxel off the axis
    if d.size == 0:
        return {"n": 0}
    thin = {} if target is None else {"frac_below_target_minus_1_voxel": float((d < target - h).mean()), "n_below_target_minus_1_voxel": int((d < target - h).sum())}
    return {**thin, "n": int(d.size), "min": float(d.min()), "p05": float(np.percentile(d, 5)), "p25": float(np.percentile(d, 25)), "median": float(np.median(d)),
            "p95": float(np.percentile(d, 95)), "max": float(d.max())}


def shell_pores(solid: np.ndarray, h: float, band: np.ndarray) -> dict:
    """Window sizes of the yarn shell: connected air regions of the mid-surface layer ``band`` (a one-voxel-thick shell inside the
    wall), each with the diameter of the largest ball that fits (distance to the solid, twice) -- inscribed window diameter."""
    air = ~solid
    dist = ndimage.distance_transform_edt(air).astype(np.float32) * h
    lab, n = ndimage.label(band & air)
    if n == 0:
        return {"n": 0}
    mx = ndimage.maximum(dist, lab, index=np.arange(1, n + 1))
    area = ndimage.sum(np.ones_like(dist), lab, index=np.arange(1, n + 1)) * h * h
    keep = area > 4.0 * h * h  # drop slivers at the patch edge
    d = 2.0 * np.asarray(mx)[keep]
    return {"n": int(keep.sum()), "min": float(d.min()), "p05": float(np.percentile(d, 5)), "median": float(np.median(d)), "p95": float(np.percentile(d, 95)),
            "max": float(d.max()), "n_below_5mm": int((d < 5.0).sum())}


# ----------------------------------------------------------------------------------------------- flow in the LSCM chart
class UVFlow:
    """Flow field on the uv plane of an :class:`~shoe_designer.lattice_lscm.LscmChart` (u along the foot, heel -> toe)."""

    def __init__(self, chart, v: ConformalSpec):
        self.v = v
        uv = chart.uv
        h = v.chart_step_mm
        self.h = h
        self.u0, self.u1 = float(uv[:, 0].min()), float(uv[:, 0].max())
        self.vlo, self.vhi = float(uv[:, 1].min()), float(uv[:, 1].max())
        # across-foot position v_n(u): centre line and half width from the mesh
        nb = 60
        edges = np.linspace(self.u0, self.u1, nb + 1)
        k = np.clip(np.digitize(uv[:, 0], edges) - 1, 0, nb - 1)
        vmid = np.array([0.5 * (uv[k == i, 1].min() + uv[k == i, 1].max()) if (k == i).any() else np.nan for i in range(nb)])
        vhalf = np.array([0.5 * (uv[k == i, 1].max() - uv[k == i, 1].min()) if (k == i).any() else np.nan for i in range(nb)])
        ok = np.isfinite(vmid)
        cen = 0.5 * (edges[1:] + edges[:-1])
        self.cen, self.vmid, self.vhalf = cen[ok], ndimage.gaussian_filter1d(vmid[ok], 2, mode="nearest"), np.maximum(ndimage.gaussian_filter1d(vhalf[ok], 2, mode="nearest"), 5.0)
        self.L = self.u1 - self.u0
        ext = 1.3 * self.L * math.tan(math.radians(v.flow_max_deg)) * 0.5 + 40.0
        self.vg = np.arange(self.vlo - ext, self.vhi + ext, h)
        self.ug = np.arange(self.u0 - 5.0, self.u1 + 5.0 + h, h)
        self._build()

    def alpha(self, uu, vv):
        v = self.v
        un = (uu - self.u0) / self.L
        vm = np.interp(uu, self.cen, self.vmid)
        vh = np.interp(uu, self.cen, self.vhalf)
        vn = np.clip((vv - vm) / vh, -1.0, 1.0)
        sw = np.interp(un, [q[0] for q in v.flow_sweep], [q[1] for q in v.flow_sweep])
        am = np.interp(un, [q[0] for q in v.flow_amp_pts], [q[1] for q in v.flow_amp_pts])
        return np.radians(np.clip(sw - v.flow_cross_deg * am * vn, -v.flow_max_deg, v.flow_max_deg))

    def _build(self):
        v, h = self.v, self.h
        ug, vg = self.ug, self.vg
        nu = len(ug)
        k0 = int(np.argmin(np.abs(ug - (self.u0 + v.flow_base_u * self.L))))
        P = np.empty((nu, len(vg)))
        F = np.empty((nu, len(vg)))
        P[k0], F[k0] = vg, 0.0
        for direction in (1, -1):
            pc, fc = vg.copy(), np.zeros(len(vg))
            k = k0
            while 0 <= k + direction < nu:
                du = direction * h
                a1 = self.alpha(ug[k], pc)
                pm = pc + 0.5 * du * np.tan(a1)
                a2 = self.alpha(ug[k] + 0.5 * du, pm)
                pc = pc + du * np.tan(a2)
                fc = fc + abs(du) / np.cos(a2) * direction
                k += direction
                P[k], F[k] = pc, fc
        PSI = np.empty((nu, len(vg)))
        PHI = np.empty((nu, len(vg)))
        lo, hi = np.inf, 0.0
        for k in range(nu):
            if (np.diff(P[k]) <= 0).any():
                raise LatticeError("flow streamlines cross: reduce flow_cross_deg / flow_sweep")
            PSI[k] = np.interp(vg, P[k], vg, left=np.nan, right=np.nan)
            PHI[k] = np.interp(vg, P[k], F[k], left=np.nan, right=np.nan)
            m = (vg > self.vlo) & (vg < self.vhi)
            w = np.gradient(PSI[k], h)
            if np.isfinite(w[m]).all():
                lo, hi = min(lo, float(w[m].min())), max(hi, float(w[m].max()))
        if not np.isfinite(PSI[:, (vg > self.vlo) & (vg < self.vhi)]).all():
            raise LatticeError("flow leaves the chart: reduce the sweep")
        self.psi_v_range = (lo, hi)
        a, b = v.flow_compress
        if lo < a or hi > b:
            raise LatticeError(f"flow compression d(psi)/d(v) in [{lo:.2f}, {hi:.2f}] outside the allowed [{a}, {b}]")
        PSI = np.where(np.isfinite(PSI), PSI, vg[None, :])
        PHI = np.where(np.isfinite(PHI), PHI, 0.0)
        self.tab = np.stack([PSI, PHI, np.gradient(PSI, h, axis=0), np.gradient(PSI, h, axis=1), np.gradient(PHI, h, axis=0), np.gradient(PHI, h, axis=1)])
        self.alpha_ref = math.radians(float(np.interp(v.flow_base_u, [q[0] for q in v.flow_sweep], [q[1] for q in v.flow_sweep])))

    def eval(self, uu, vv):
        """(psi, phi, psi_u, psi_v, phi_u, phi_v) at uv points."""
        ci = np.clip((uu - self.ug[0]) / self.h, 0, len(self.ug) - 1)
        cj = np.clip((vv - self.vg[0]) / self.h, 0, len(self.vg) - 1)
        return tuple(ndimage.map_coordinates(self.tab[i], [ci, cj], order=1, mode="nearest") for i in range(6))


class ConformalKnitUV(KnitLayer2):
    """KnitLayer2 whose Voronoi pattern lives in the flow-aligned LSCM chart (world-space tube distance)."""

    def __init__(self, p, v, aux, rs, frame, image, chart, flow: UVFlow):
        super().__init__(p, v, aux, rs, frame, image)
        self.lchart, self.flow = chart, flow
        el, pitch = v.elongate, v.pitch_mm
        sa, ca = math.sin(flow.alpha_ref), math.cos(flow.alpha_ref)
        self.sa, self.ca = sa, ca
        # seeds in the orthogonalised coordinates c = ((phi + psi sa) / el, psi ca): equal world cell area (density ~ lam^2 / det), Lloyd-relaxed
        psi, phi, *_ = flow.eval(chart.uv[:, 0], chart.uv[:, 1])
        step = 1.5
        gu = np.arange(chart.uv[:, 0].min(), chart.uv[:, 0].max() + step, step)
        gv = np.arange(chart.uv[:, 1].min(), chart.uv[:, 1].max() + step, step)
        G = np.stack(np.meshgrid(gu, gv, indexing="ij"), -1).reshape(-1, 2)
        dq, iq = cKDTree(chart.uv).query(G)
        ok = dq < 3.0
        G, lam = G[ok], chart.lam[iq[ok]]
        ps, ph, ps_u, ps_v, ph_u, ph_v = flow.eval(G[:, 0], G[:, 1])
        cs = np.stack([(ph + ps * sa) / el, ps * ca], axis=1)
        det = np.abs(((ph_u + sa * ps_u) / el) * (ca * ps_v) - ((ph_v + sa * ps_v) / el) * (ca * ps_u))
        wgt = lam**2 / np.maximum(det, 1e-3)
        world_area = float((lam**2).sum() * step * step)
        n_seed = max(int(round(world_area / (0.87 * pitch * pitch * el))), 8)
        rng = np.random.default_rng(v.seed)
        pr = lam**2
        pick = rng.choice(len(cs), size=min(n_seed, len(cs)), replace=False, p=pr / pr.sum())
        pts = cs[pick] + rng.uniform(-v.jitter, v.jitter, (len(pick), 2)) * pitch
        if v.style != "triangle":
            for _ in range(max(v.lloyd_iters, 0)):
                _, a = cKDTree(pts).query(cs)
                num = np.zeros_like(pts)
                den = np.zeros(len(pts))
                np.add.at(num, a, cs * wgt[:, None])
                np.add.at(den, a, wgt)
                m = den > 0
                pts[m] = num[m] / den[m, None]
        self.seed_pts = pts
        self.seed_tree = cKDTree(pts)
        self.n_seeds = len(pts)
        # solid heel seam strip and coverage bookkeeping
        ax = p.axis
        self.seam_x = ax["x_min"] + v.seam_x_frac * (ax["x_max"] - ax["x_min"]) if ax["toe"] == "+x" else ax["x_max"] - v.seam_x_frac * (ax["x_max"] - ax["x_min"])
        self.uncovered = 0
        self.total = 0
        self.reach = v.reach_mm

    def _theta(self, x, py, pz):
        self._last = (x, py, pz)
        return super()._theta(x, py, pz)

    def _chart(self, ix, x, theta, th, ns, a_mid):
        v, fl, lc = self.v, self.flow, self.lchart
        x_, py, pz = self._last
        pts = np.stack([np.full(len(py), x_), py, pz], axis=1).astype(np.float64)
        uv, J, dist_m = lc.eval(pts)
        psi, phi, psi_u, psi_v, phi_u, phi_v = fl.eval(uv[:, 0], uv[:, 1])
        el, sa, ca = v.elongate, self.sa, self.ca
        if v.style == "triangle":  # three families of straight lines (in the orthogonalised chart), 60 degrees apart
            c1, c2 = phi + psi * sa, psi * ca
            sp_ = v.pitch_mm * math.sqrt(3.0) / 2.0
            best_s, best_g = np.full(len(c1), np.inf), np.zeros((len(c1), 2))
            for ang in (0.0, math.pi / 3.0, 2.0 * math.pi / 3.0):
                nx_, ny_ = math.cos(ang), math.sin(ang)
                t = c1 * nx_ + c2 * ny_
                r = np.mod(t, sp_)
                sgn = np.where(r < 0.5 * sp_, r, r - sp_)  # signed distance to the nearest line
                m = np.abs(sgn) < np.abs(best_s)
                best_s = np.where(m, sgn, best_s)
                best_g[m] = (nx_, ny_)
            g_phi, g_psi = best_g[:, 0], best_g[:, 0] * sa + best_g[:, 1] * ca
            g_uv = np.stack([g_phi * phi_u + g_psi * psi_u, g_phi * phi_v + g_psi * psi_v], axis=1)
            gw = np.einsum("nij,ni->jn", J, g_uv)
            gn = np.maximum(np.linalg.norm(gw, axis=0), 1e-6)
            out = self._tube(a_mid, best_s / gn, np.sum(ns * gw / gn, axis=0))
            return self._finish(out, a_mid, dist_m, x_, py)
        q = np.stack([(phi + psi * sa) / el, psi * ca], axis=1)
        dist, idx = self.seed_tree.query(q, k=3)
        u = [(q - self.seed_pts[idx[:, i]]) / np.maximum(dist[:, i : i + 1], 1e-9) for i in range(3)]
        d01, d02 = 0.5 * (dist[:, 1] - dist[:, 0]), 0.5 * (dist[:, 2] - dist[:, 0])
        g01, g02 = 0.5 * (u[1] - u[0]), 0.5 * (u[2] - u[0])
        k = v.fillet_mm
        if k > 0:
            m = np.minimum(d01, d02)
            e1, e2 = np.exp(-(d01 - m) / k), np.exp(-(d02 - m) / k)
            d = m - k * np.log(e1 + e2)
            g = (e1[:, None] * g01 + e2[:, None] * g02) / (e1 + e2)[:, None]
        else:
            d, g = d01, g01
        gphi = g[:, 0] / el
        gpsi = g[:, 0] * sa / el + g[:, 1] * ca
        g_uv = np.stack([gphi * phi_u + gpsi * psi_u, gphi * phi_v + gpsi * psi_v], axis=1)
        gw = np.einsum("nij,ni->jn", J, g_uv)
        gn = np.maximum(np.linalg.norm(gw, axis=0), 1e-6)
        out = self._tube(a_mid, d / gn, np.sum(ns * gw / gn, axis=0))
        return self._finish(out, a_mid, dist_m, x_, py)

    def _finish(self, out, a_mid, dist_m, x_, py):
        """No pattern where the chart does not reach, and over the heel seam: the point stays solid (tube distance 0)."""
        v = self.v
        yc = np.interp(x_, self.frame["x"], self.frame["yc"])
        heel = (x_ < self.seam_x) if self.p.axis["toe"] == "+x" else (x_ > self.seam_x)
        near = np.abs(a_mid) < 0.5 * v.yarn_mm * 1.3 + 1.0  # only points near the rod mid-surface need the chart; the others are air anyway
        dist_t = np.sqrt(np.maximum(dist_m**2 - a_mid**2, 0.0))  # distance along the surface (the offset from the mid-surface is not a gap)
        solid = ((dist_t > self.reach) & near) | (heel & (np.abs(py - yc) < v.seam_solid_mm))
        self.uncovered += int(((dist_t > self.reach) & near).sum())
        self.total += int(near.sum())
        return np.where(solid, 0.0, out)


# ----------------------------------------------------------------------------------------------- whole-shoe build
def build_whole(prep: Prep, v: ConformalSpec, cfg, process, progress=None):
    """Whole upper with the flow-aligned pattern. Returns (fine phi, fine grid, report, aux). The chart is LSCM when the mesh is one clean
    disk, else the analytic chart (the reason is in the report)."""
    import dataclasses

    from . import lattice_lscm as LS
    from .lattice import LatticeConfig, SoleLayer, SoleZone, build_fine, check_sole_params
    from .lattice_upper import foot_frame
    from .lattice_variants import check_variant, effective_sole, shell_body_v

    from .lattice import strut_min_mm
    from .lattice_sole import SoleNetLayer, check_sole_net, footprint
    from .lattice_variants import sole_bottom

    from . import outline as O

    if v.outline:
        v = O.apply_outline(v, [dict(o) for o in v.outline])
    net = v.sole_mode == "net"
    chk = check_variant(v, cfg, process)
    cfg_v = dataclasses.replace(LatticeConfig(**{**cfg.__dict__, "voxel_mm": v.voxel_mm}), sole=effective_sole(v, cfg))
    body, aux = shell_body_v(prep, v)
    shell_sole = v.sole_mode == "shell"
    if v.tab_w_mm > 0:
        body = np.minimum(body, pull_tab_body(prep, v, body, aux["zt_var"])).astype(np.float32)
        body[body == 0] = 1e-3
    zone = SoleZone.build(body, prep.c0, prep.grid, cfg_v.sole) if not (shell_sole or net) else None
    pores = check_sole_params(cfg_v, process, zone) if (not v.preview and not (shell_sole or net)) else {}
    sole_feats = None
    if net:
        need = max(cfg.min_opening_mm if False else 0.0, process.min_channel_mm if process is not None else 0.0)
        sole_feats = check_sole_net(v, strut_min_mm(cfg, process), need, cfg.voxel_ratio)
        chk = {**chk, "printable": bool(v.voxel_mm <= min(chk["yarn_diameter_min_mm"], sole_feats["thinnest_mm"]) / cfg.voxel_ratio * (1 + 1e-9) and not v.preview)}
    frame = foot_frame(prep.c0, prep.zt, prep.grid, v.pitch_mm)
    route, why, lchart, flow, fchart = "analytic", "", None, None, None
    if v.conformal and v.chart == "lscm":
        try:
            lchart = LS.build_chart(prep, aux, v.rim_mm, 0.0, field=(prep.c0 - 0.5 * aux["T"]).astype(np.float32), floor_margin_mm=max(v.sole_margin_mm - 1.0, 0.5), target_faces=v.lscm_target_faces,
                                    seam_half_mm=3.0, seam_x_frac=v.seam_x_frac, frame=frame, pin_fracs=tuple(v.lscm_pin_fracs))
            route = "lscm"
        except LatticeError as e:  # mesh problems fall back to the analytic chart; the flow guard below is a config error and propagates
            why = str(e)
            lchart = None
        if lchart is not None:
            flow = UVFlow(lchart, v)
    if route == "analytic" and v.conformal:
        fchart = FlowChart(prep, v, frame)
    holder = {}

    def layers(rs, fg):
        holder["rs"] = rs
        if route == "lscm":
            holder["knit"] = ConformalKnitUV(prep, v, aux, rs, frame, None, lchart, flow)
        elif v.conformal:
            holder["knit"] = ConformalKnit(prep, v, aux, rs, frame, None, fchart)
        else:
            holder["knit"] = KnitLayer2(prep, v, aux, rs, frame, None)
        if net:
            foot = footprint(prep, aux, aux["T"], v.sole_flare_mm)
            holder["sole"] = SoleNetLayer(prep, aux, rs, v, foot, sole_bottom(prep, v))
            return [holder["sole"], holder["knit"]]
        return ([] if shell_sole else [SoleLayer(zone, rs)]) + [holder["knit"]]

    fine, fg, stats = build_fine(body, prep.grid, layers, v.voxel_mm, cfg.pad_mm, progress)
    k = holder["knit"]
    aux = {**aux, "rs": holder["rs"], "spec": v}
    rep = {"outline": [dict(o) for o in v.outline], "name": v.name, "route": route if v.conformal else "direct (#14)", "fallback_reason": why, "check": chk, "voxel_mm": v.voxel_mm, "printable": chk["printable"],
           "yarn_diameter_used_mm": [round(k.r_min, 3), round(k.r_max, 3)], "sole_pores": pores, "sole_net": sole_feats,
           "solid_body_cm3": round(stats.vol_base_mm3 / 1000, 2), "lattice_body_cm3": round(stats.vol_final_mm3 / 1000, 2)}
    if net:
        rep["sole_zone"] = {"slab_below_pad_cm3": round(stats.zone_base_mm3 / 1000, 2), "solid_after_cm3": round(stats.zone_final_mm3 / 1000, 2),
                            "fill_fraction": round(stats.zone_final_mm3 / max(stats.zone_base_mm3, 1e-9), 3), "outer_wall_cells": holder["sole"].n_cells,
                            "wall_length_mm": round(holder["sole"].L, 1), "inner_wall_mm": holder["sole"].wall_in.round(2).tolist()}
    if lchart is not None:
        rep["lscm"] = lchart.report
        rep["flow"] = {"psi_v_range": [round(x, 3) for x in flow.psi_v_range]}
        rep["coverage"] = chart_coverage(prep, aux, v, lchart)
    return fine, fg, rep, aux


def chart_coverage(prep: Prep, aux: dict, v: ConformalSpec, chart) -> dict:
    """Share of the rod zone (coarse voxels within the rod reach of the mid-surface phi_C = T/2, between the sole band and the rim) farther than ``reach_mm`` from the chart mesh."""
    g = prep.grid
    X, Y, Z = np.meshgrid(g.origin[0] + np.arange(g.shape[0]) * g.h, g.origin[1] + np.arange(g.shape[1]) * g.h, g.origin[2] + np.arange(g.shape[2]) * g.dz, indexing="ij")
    m = (np.abs(prep.c0 - 0.5 * aux["T"]) < 0.5 * v.yarn_mm * 1.3 + 1.0) & (Z > aux["floor"][:, :, None] + v.sole_margin_mm) & (Z < aux["zt_var"][:, :, None] - v.rim_mm)
    pts = np.stack([X[m], Y[m], Z[m]], 1)
    d, _ = chart.tree.query(pts)
    a = (prep.c0 - 0.5 * aux["T"])[m]
    dt = np.sqrt(np.maximum(d**2 - a**2, 0.0))
    return {"zone_points": int(len(pts)), "uncovered_share": round(float((dt > v.reach_mm).mean()), 4), "reach_mm": v.reach_mm}


def yarn_zone_masks(prep: Prep, aux: dict, v: ConformalSpec, fg, rs, u_range=None) -> tuple[np.ndarray, np.ndarray]:
    """(region, band) on the fine grid: the yarn zone (shell between sole band and rim, 3 mm margins; optional u range) and its one-voxel
    mid-surface layer."""
    g = prep.grid
    shape = (fg.shape[0], fg.shape[1], fg.shape[2])
    c0f = np.empty(shape, np.float32)
    for ix in range(shape[0]):
        c0f[ix] = rs.plane(prep.c0, ix)
    X2, Y2 = np.meshgrid(rs.x, rs.y, indexing="ij")
    ci = [(X2 - g.origin[0]) / g.h, (Y2 - g.origin[1]) / g.h]
    zf = ndimage.map_coordinates(aux["floor"], ci, order=1, mode="nearest")
    zt = ndimage.map_coordinates(aux["zt_var"], ci, order=1, mode="nearest")
    z = rs.z[None, None, :]
    region = ((z > (zf + v.sole_margin_mm + 3.0)[:, :, None]) & (z < (zt - v.rim_mm - 3.0)[:, :, None]) & (c0f > 0) & (c0f < v.thickness_mm))
    if u_range is not None:
        xu = u_of_x(rs.x, prep.axis)[:, None, None]
        region &= (xu > u_range[0]) & (xu < u_range[1])
    band = region & (np.abs(c0f - 0.5 * v.thickness_mm) < 0.75 * fg.h)
    return region, band


def mesh_thickness(mesh, n: int = 200000, seed: int = 0) -> dict:
    """Local thickness on the exported mesh (independent of the voxels and the field): from surface samples, a ray along the inward normal to
    the first hit. A round rod of diameter D gives D (to the voxel accuracy of the mesh). Needs embree for speed (``--extra fast``)."""
    import trimesh

    pts, fi = trimesh.sample.sample_surface(mesh, n, seed=seed)
    nrm = mesh.face_normals[fi]
    o = pts - nrm * 1e-3
    loc, ray_i, _ = mesh.ray.intersects_location(o, -nrm, multiple_hits=False)
    d = np.full(n, np.nan)
    d[ray_i] = np.linalg.norm(loc - o[ray_i], axis=1)
    return {"d": d, "points": pts, "normals": nrm}


def pull_tab_body(prep: Prep, v: ConformalSpec, body: np.ndarray, ztv: np.ndarray) -> np.ndarray:
    """Flat pull tab behind the collar (box-section loop in the y-z plane), clipped so it never enters the cavity."""
    from .outline import tab_sdf

    g = prep.grid
    ys = g.origin[1] + np.arange(g.shape[1]) * g.h
    z = g.origin[2] + np.arange(g.shape[2]) * g.dz
    xs = g.origin[0] + np.arange(g.shape[0]) * g.h
    sgn = -1.0 if prep.axis["toe"] == "+x" else 1.0  # the heel end
    jy = np.abs(ys) < 3.0
    cols = (body < 0)[:, jy, :].any(axis=1)
    zsel = (z > float(ztv[prep.has].min()) - 8) & (z < float(ztv[prep.has].min()) - 2)
    xi = np.flatnonzero(cols[:, zsel].any(axis=1))
    xb = xs[xi.min()] if sgn < 0 else xs[xi.max()]
    zt0 = float(np.median(ztv[np.argmin(np.abs(xs - xb)), jy])) if jy.any() else float(ztv[prep.has].min())
    R = 0.5 * v.tab_d_mm - 0.5 * v.tab_t_mm
    cz = zt0 + R - 3.0
    cx = xb + sgn * (0.5 * v.tab_w_mm - 4.0)
    X, Y, Z = np.meshgrid(xs.astype(np.float32), ys.astype(np.float32), z.astype(np.float32), indexing="ij")
    return np.maximum(tab_sdf(X, Y, Z, cx, cz, R, v.tab_w_mm, v.tab_t_mm), -prep.c0).astype(np.float32)


def sole_cell_openings(prep: Prep, aux: dict, v: ConformalSpec, fg, rs, solid: np.ndarray) -> dict:
    """Net sole: the open cells below the pad plate. Mid-height layer of the sole zone: connected air regions and the diameter of the largest ball in each
    (the cells are open downwards, so a ball that fits in the cell can leave through the bottom); plus the pad hole diameter (the way up to the shoe cavity)."""
    from .lattice_variants import sole_bottom

    g = prep.grid
    zb = sole_bottom(prep, v)
    X2, Y2 = np.meshgrid(rs.x, rs.y, indexing="ij")
    ci = [(X2 - g.origin[0]) / g.h, (Y2 - g.origin[1]) / g.h]
    zbf = ndimage.map_coordinates(zb, ci, order=1, mode="nearest")
    flf = ndimage.map_coordinates(aux["floor"], ci, order=1, mode="nearest")
    zmid = 0.5 * (zbf + flf - v.sole_pad_mm)
    z = rs.z[None, None, :]
    layer = np.abs(z - zmid[:, :, None]) < 0.75 * fg.h
    # inside the footprint: slab present around (solid somewhere in the column below the plate) and away from the outline
    colsolid = ndimage.binary_closing((solid & layer).any(axis=2), iterations=int(round(20 / fg.h)))
    inner = ndimage.binary_erosion(colsolid, iterations=int(round(0.5 * v.sole_wall_mm / fg.h + 4)))
    air = ~solid
    dist = ndimage.distance_transform_edt(air).astype(np.float32) * fg.h
    lab, n = ndimage.label(layer & air & inner[:, :, None])
    if n == 0:
        return {"n": 0}
    mx = ndimage.maximum(dist, lab, index=np.arange(1, n + 1))
    area = ndimage.sum(np.ones_like(dist), lab, index=np.arange(1, n + 1)) * fg.h**2
    keep = area > 30.0
    d = 2.0 * np.asarray(mx)[keep]
    return {"cells": int(keep.sum()), "cell_inscribed_min_mm": float(d.min()), "cell_inscribed_median_mm": float(np.median(d)), "cell_inscribed_max_mm": float(d.max()),
            "pad_hole_mm": v.sole_pad_hole_mm, "cells_below_5mm": int((d < 5.0).sum())}
