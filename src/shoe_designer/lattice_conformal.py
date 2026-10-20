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
    flow_compress: tuple = (0.55, 1.8)  # allowed range of d psi / d p (width of a flow tube relative to the base line)
    # width of a cell (across the flow) relative to the along-flow length: elongate = length / width

    @classmethod
    def from_dict(cls, raw: dict, defaults: dict | None = None):
        d = {**(defaults or {}), **raw}
        names = {f.name for f in fields(cls)}
        bad = set(d) - names
        if bad:
            raise LatticeError(f"unknown conformal keys {sorted(bad)}")
        for k in ("raise_pts", "pitch_pts", "yarn_pts_u", "yarn_pts_z", "image_u", "sole_density", "flow_sweep", "flow_amp_pts", "flow_compress"):
            if k in d:
                d[k] = tuple(tuple(float(v) for v in p) if isinstance(p, (list, tuple)) else float(p) for p in d[k])
        if "bumps" in d:
            d["bumps"] = tuple(tuple(sorted(b.items())) for b in d["bumps"])
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
