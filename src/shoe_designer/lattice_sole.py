"""Integrated sole of the lattice shoe (#46 part B): pad plate + the same pattern family below it, no square BCC platform.

Inside the sole slab (footprint of the cavity grown by the shell thickness and the flare, from ``z_bottom(x, y)`` up to the cavity
floor) the layer builds, from the top down:

* **pad plate** - a solid plate ``pad_mm`` thick (2-3 mm) directly under the foot with round holes (``pad_hole_mm``, hex pitch
  ``pad_hole_pitch_mm``): the support surface, breathing and powder exit towards the shoe cavity;
* **outer wall net** - a vertical shell ``wall_mm`` thick along the outline of the footprint with the Voronoi rod pattern of the upper
  (cells elongated along the outline, ``wall_rod_mm`` rods with rounded junctions) in the developable chart (arc length ``s``, height
  ``z``): isometric, so the rods are true tubes (exact world distance, tube formula with c = 0); an outline period holds a whole
  number of cells;
* **inner walls** - the 2D Voronoi cell boundaries of the plan view (``cell_mm``, same jitter/fillet as the upper) extruded from the
  pad plate to the outsole: vertical walls whose thickness follows the density table (front sparse, heel dense, as in #14);
* **bottom edge bead** - a round tube ``ring_mm`` along the bottom outline (the rounded sole edge).

The underside is open (no outsole skin): every cell is open downwards, so there is no closed cavity and the powder leaves through
the bottom; the pad holes connect the cells to the shoe cavity. All sizes are configuration; the minimum of every printed
feature is checked (rods, walls, plate, bead >= the process minimum; the voxel <= the thinnest feature / 4).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage import measure

from .outline import cradle_raise, inset_profile
from .lattice import FineStats, LatticeError, Resampler, _occ, _signed_2d, u_of_x


def footprint(prep, aux: dict, t_floor_from_T: np.ndarray, flare_mm: float, smooth_mm: float = 3.0) -> np.ndarray:
    """2D signed distance (negative inside) of the sole slab footprint: the cavity footprint grown by the shell thickness at the floor + flare."""
    g = prep.grid
    k = np.clip(np.round((aux["floor"] - g.origin[2]) / g.dz).astype(int), 0, g.shape[2] - 1)
    t_floor = np.take_along_axis(t_floor_from_T, k[:, :, None], axis=2)[:, :, 0]
    f = ndimage.gaussian_filter(_signed_2d(prep.has, g.h), smooth_mm / g.h, mode="nearest")  # the footprint of the cavity columns is ragged at 1 mm
    return (f - t_floor - np.float32(flare_mm)).astype(np.float32)


def outline_polyline(foot: np.ndarray, grid, level: float, step: float = 0.5):
    """Longest closed contour of ``foot = level``: points (m, 2) at ``step`` spacing, tangents, cumulative arc length, total length.
    Starts at the heel centre (smallest x)."""
    cs = measure.find_contours(foot, level)
    c = max(cs, key=len)
    xy = np.stack([grid.origin[0] + c[:, 0] * grid.h, grid.origin[1] + c[:, 1] * grid.h], axis=1)
    if np.linalg.norm(xy[0] - xy[-1]) > 1e-6:
        xy = np.vstack([xy, xy[:1]])
    seg = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    L = float(s[-1])
    n = int(round(L / step))
    ss = np.linspace(0.0, L, n, endpoint=False)
    pts = np.stack([np.interp(ss, s, xy[:, 0]), np.interp(ss, s, xy[:, 1])], axis=1)
    i0 = int(np.argmin(pts[:, 0]))
    pts = np.roll(pts, -i0, axis=0)
    # smooth against the marching-squares staircase
    sm = ndimage.gaussian_filter1d(pts, 3.0 / step, axis=0, mode="wrap")
    sl = np.linalg.norm(np.roll(sm, -1, axis=0) - sm, axis=1)
    sc = np.concatenate([[0.0], np.cumsum(sl)[:-1]])
    tan = np.roll(sm, -1, axis=0) - np.roll(sm, 1, axis=0)
    tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-9)
    return sm, tan, sc, float(sl.sum())


def hex_hole_dist(x, y, pitch: float, r: float):
    """Signed distance to a hex array of round holes (radius r) at points (x, y): negative inside a hole."""
    a1, a2 = np.array([pitch, 0.0]), np.array([0.5 * pitch, 0.5 * pitch * math.sqrt(3)])
    P = np.stack([x, y], axis=-1)
    B = np.stack([a1, a2], axis=1)
    f = P @ np.linalg.inv(B).T
    best = np.full(P.shape[:-1], np.inf)
    for di in (0, 1):
        for dj in (0, 1):
            ij = np.floor(f) + np.array([di, dj])
            c = ij @ B.T
            best = np.minimum(best, np.linalg.norm(P - c, axis=-1))
    return best - r


class SoleNetLayer:
    """Applies the net sole on one fine x-plane (see the module docstring)."""

    name = "sole_net"
    reach = 2.0

    def __init__(self, prep, aux: dict, rs: Resampler, v, foot: np.ndarray, zb: np.ndarray):
        self.p, self.aux, self.rs, self.v = prep, aux, rs, v
        g = prep.grid
        self.foot, self.zb = foot, zb
        gx, gy = np.gradient(foot, g.h, g.h)
        n = np.maximum(np.sqrt(gx**2 + gy**2), 1e-6)
        self.nx, self.ny = (gx / n).astype(np.float32), (gy / n).astype(np.float32)
        # outer wall net: arc-length chart on the mid contour of the wall
        self.mid = -0.5 * v.sole_wall_mm
        pl, tan, sc, L = outline_polyline(foot, g, self.mid)
        self.pl, self.tan, self.sc, self.L = pl, tan, sc, L
        self.tree = cKDTree(pl)
        self.n_cells = max(int(round(L / v.sole_pitch_mm)), 6)
        self.k = self.n_cells * v.sole_pitch_mm / L  # chart units per mm of arc length
        el, pitch = v.sole_el, v.sole_pitch_mm
        rng = np.random.default_rng(v.seed + 1)
        zlo, zhi = float(np.nanmin(zb)) - 3 * pitch, float(np.nanmax(aux["floor"][prep.has])) + 3 * pitch
        nj = int(math.ceil((zhi - zlo) / (pitch * 0.87))) + 2
        n = self.n_cells
        jit = rng.uniform(-v.jitter, v.jitter, (n, nj, 2)) * pitch
        i, j = np.meshgrid(np.arange(n), np.arange(nj), indexing="ij")
        c1 = (i + 0.5 * (j % 2) + 0.5) * pitch + jit[:, :, 0]  # along s' = k s
        c2 = zlo + j * pitch * 0.87 + jit[:, :, 1]
        pts = np.stack([(c1 / el).ravel(), c2.ravel()], axis=1)
        per = n * pitch / el
        self.wall_pts = np.concatenate([pts - [per, 0], pts, pts + [per, 0]])
        self.wall_tree = cKDTree(self.wall_pts)
        self.per = per
        # inner honeycomb: plan Voronoi (cells elongated along x)
        cp = v.sole_cell_mm
        x0, x1 = prep.axis["x_min"] - 3 * cp, prep.axis["x_max"] + 3 * cp
        ys = prep.grid.origin[1] + np.array([0, prep.grid.shape[1] * prep.grid.h])
        ni, nk = int(math.ceil((ys[1] - ys[0]) / cp)) + 2, int(math.ceil((x1 - x0) / (cp * 0.87 * el))) + 2
        rng = np.random.default_rng(v.seed + 2)
        jit = rng.uniform(-v.jitter, v.jitter, (ni, nk, 2)) * cp
        i, j = np.meshgrid(np.arange(ni), np.arange(nk), indexing="ij")
        px = ((x0 + j * cp * 0.87 * el) / el + jit[:, :, 1]).ravel()
        py = (ys[0] + (i + 0.5 * (j % 2)) * cp + jit[:, :, 0]).ravel()
        self.plan_tree = cKDTree(np.stack([px, py], axis=1))
        self.plan_pts = np.stack([px, py], axis=1)
        # inner wall thickness along u from the density table: wall volume fraction ~ 2.1 t / pitch
        dens = np.asarray(v.sole_density if v.sole_density else ((0.0, 0.22), (1.0, 0.22)), float)
        self.wall_in = np.stack([dens[:, 0], dens[:, 1] * cp / 2.1 * v.sole_wall_calib], axis=1)
        self.min_features = self.features()

    def features(self) -> dict:
        v = self.v
        return {"wall_net_rod_mm": v.sole_wall_rod_mm, "inner_wall_mm_min": float(self.wall_in[:, 1].min()), "pad_plate_mm": v.sole_pad_mm,
                "bead_mm": v.sole_ring_mm if v.sole_ring_mm > 0 else None, "wall_shell_mm": v.sole_wall_mm}

    @staticmethod
    def _soft(d01, d02, g01, g02, k):
        if k > 0:
            m = np.minimum(d01, d02)
            e1, e2 = np.exp(-(d01 - m) / k), np.exp(-(d02 - m) / k)
            return m - k * np.log(e1 + e2), (e1[:, None] * g01 + e2[:, None] * g02) / (e1 + e2)[:, None]
        return d01, g01

    def apply(self, base: np.ndarray, ix: int, stats: FineStats) -> np.ndarray:
        rs, v = self.rs, self.v
        x = float(rs.x[ix])
        zz = rs.z[None, :]
        fo = rs.row(self.foot, ix)
        zb = rs.row(self.zb, ix)
        fl = rs.row(self.aux["floor"], ix)
        u_ = float(u_of_x(x, self.p.axis))
        if v.sole_rocker2_mm > 0 or v.sole_cradle_mm > 0:  # cradle: the bottom only rises
            half = float(np.abs(rs.y[fo < 0]).max()) if (fo < 0).any() else 1.0
            zb = zb + cradle_raise(u_, rs.y.astype(np.float64), half, v.sole_rocker2_mm, v.sole_rocker2_start, v.sole_cradle_mm).astype(np.float32)
        top_z = fl - np.float32(v.sole_pad_mm)
        t_rel = np.clip((zz - zb[:, None]) / np.maximum(top_z - zb, 1.0)[:, None], 0.0, 1.0)
        if v.sole_bulge_mm > 0:  # curved side wall: the bottom edge is inset, the wall bulges out above
            inset = inset_profile(t_rel, v.sole_bulge_mm, v.sole_bulge_power).astype(np.float32)
        else:
            inset = np.zeros_like(t_rel)
        Fe = fo[:, None] + inset  # footprint value at every (y, z) after the bulge
        pad_top = fl[:, None]
        pad_bot = (fl - np.float32(v.sole_pad_mm))[:, None]
        # inside the slab only: base < 1 (solid slab); zone below the pad plate
        phi_z = np.maximum(zz - pad_bot, zb[:, None] - zz)  # below the plate, above the outsole
        out = base.copy()
        if v.sole_bulge_mm > 0 or v.sole_rocker2_mm > 0 or v.sole_cradle_mm > 0:  # trim the slab to the bulged / raised bottom
            below_pad = zz < top_z[:, None] + np.float32(v.sole_pad_mm)
            np.maximum(out, np.where(below_pad, np.maximum(Fe, zb[:, None] - zz), -np.inf), out=out)
            base = out.copy()
        cand = (phi_z < self.reach) & (base < 1.0)
        if cand.any():
            iy, iz = np.nonzero(cand)
            px, py, pz = np.full(len(iy), x), rs.y[iy].astype(np.float64), rs.z[iz].astype(np.float64)
            F = Fe[iy, iz].astype(np.float64)
            d = np.full(len(iy), np.inf)
            # --- outer wall net (band |foot - mid| < wall/2 + reach)
            bandm = np.abs(F - self.mid) < 0.5 * v.sole_wall_mm + 3.0
            if bandm.any():
                q = np.stack([px[bandm], py[bandm]], axis=1)
                dd, ii = self.tree.query(q)
                sl = self.sc[ii] + np.einsum("ij,ij->i", q - self.pl[ii], self.tan[ii])
                s_ch = np.mod(sl * self.k, self.n_cells * v.sole_pitch_mm) / v.sole_el
                qq = np.stack([s_ch, pz[bandm]], axis=1)
                dist, idx = self.wall_tree.query(qq, k=3)
                u = [(qq - self.wall_pts[idx[:, i]]) / np.maximum(dist[:, i : i + 1], 1e-9) for i in range(3)]
                d01, d02 = 0.5 * (dist[:, 1] - dist[:, 0]), 0.5 * (dist[:, 2] - dist[:, 0])
                g01, g02 = 0.5 * (u[1] - u[0]), 0.5 * (u[2] - u[0])
                dsoft, g = self._soft(d01, d02, g01, g02, v.fillet_mm)
                # gradient in the world: d(s')/d(s) = k / el along the tangent, d/dz = 1
                gs = g[:, 0] * self.k / v.sole_el
                gn = np.maximum(np.sqrt(gs**2 + g[:, 1] ** 2), 1e-6)
                b = dsoft / gn
                a_mid = F[bandm] - self.mid
                dw = np.sqrt(np.maximum(a_mid**2 + b**2, 0.0)) - 0.5 * v.sole_wall_rod_mm  # cylinder: c = 0 exactly
                d[bandm] = np.minimum(d[bandm], dw)
            # --- inner walls: extruded plan Voronoi, thickness by u
            if (F < 3.0).any():
                u_ = float(u_of_x(x, self.p.axis))
                t_in = float(np.interp(u_, self.wall_in[:, 0], self.wall_in[:, 1]))
                q = np.stack([px / v.sole_el, py], axis=1)
                dist, idx = self.plan_tree.query(q, k=3)
                u = [(q - self.plan_pts[idx[:, i]]) / np.maximum(dist[:, i : i + 1], 1e-9) for i in range(3)]
                d01, d02 = 0.5 * (dist[:, 1] - dist[:, 0]), 0.5 * (dist[:, 2] - dist[:, 0])
                g01, g02 = 0.5 * (u[1] - u[0]), 0.5 * (u[2] - u[0])
                dsoft, g = self._soft(d01, d02, g01, g02, v.fillet_mm)
                gn = np.maximum(np.sqrt((g[:, 0] / v.sole_el) ** 2 + g[:, 1] ** 2), 1e-6)
                dh = dsoft / gn - 0.5 * t_in
                d = np.minimum(d, np.where(F < -0.5 * v.sole_wall_mm, dh, np.inf))
            # --- bottom edge bead: tube of diameter ring_mm along the bottom outline
            if v.sole_ring_mm > 0:
                r = 0.5 * v.sole_ring_mm
                zbp = zb[iy].astype(np.float64)
                dr = np.sqrt((F + r) ** 2 + (pz - zbp - r) ** 2) - r
                d = np.minimum(d, dr)
            pz_ = phi_z[iy, iz].astype(np.float64)
            new = np.maximum(base[iy, iz], np.minimum(-pz_, d).astype(np.float32))
            zm = pz_ < 0
            h = rs.fine.h
            stats.zone_base_mm3 += float(_occ(base[iy, iz][zm], h).sum()) * h**3
            stats.zone_final_mm3 += float(_occ(new[zm], h).sum()) * h**3
            out[iy, iz] = new
        # --- pad plate: solid, with round holes
        if v.sole_pad_hole_mm > 0:
            hd = hex_hole_dist(np.full(len(rs.y), x), rs.y.astype(np.float64), v.sole_pad_hole_pitch_mm, 0.5 * v.sole_pad_hole_mm).astype(np.float32)
            inplate = (zz >= pad_bot) & (zz <= pad_top) & (base < 1.0)
            np.maximum(out, np.where(inplate, -hd[:, None], -np.inf), out=out)
        return out


def check_sole_net(v, lo: float, need_opening: float = 0.0, voxel_ratio: float = 4.0) -> dict:
    """Config-level checks of the net sole: every printed feature >= ``lo`` (the process minimum), hole sizes, voxel. Raises LatticeError."""
    dens = np.asarray(v.sole_density if v.sole_density else ((0.0, 0.22), (1.0, 0.22)), float)
    t_in = float((dens[:, 1] * v.sole_cell_mm / 2.1 * v.sole_wall_calib).min())
    feats = {"wall_net_rod": v.sole_wall_rod_mm, "inner_wall_min": t_in, "pad_plate": v.sole_pad_mm, "wall_shell": v.sole_wall_mm}
    if v.sole_ring_mm > 0:
        feats["bead"] = v.sole_ring_mm
    for k, val in feats.items():
        if val < lo - 1e-9:
            raise LatticeError(f"sole feature {k} = {val:.2f} mm < minimum {lo:g} mm")
    if v.sole_wall_mm < v.sole_wall_rod_mm - 1e-9:
        raise LatticeError("sole wall shell thinner than its rod diameter")
    if 0 < v.sole_pad_hole_mm < need_opening:
        raise LatticeError(f"pad hole {v.sole_pad_hole_mm:g} mm < required opening {need_opening:g} mm")
    thinnest = min(feats.values())
    return {"features_mm": {k: round(x, 3) for k, x in feats.items()}, "thinnest_mm": round(thinnest, 3), "voxel_max_mm": round(thinnest / voxel_ratio, 3)}
