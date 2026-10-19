"""Shoe body composition (design doc issue-10-cavity-rebuild §1.2, §2, §4.3-4, #10 S6).

Chain, all fields on one shared grid (phi < 0 inside, arrays ``[ix, iy, iz]``, mm)::

    phi_1 = phi_E - P_fit(cp(x))             level 2: smooth push of the outer skin (only fit gaps)
    phi_2 = min(phi_1, phi_C - t_fill)       level 3: hard fill, only inside M_below
    phi_3 = min(phi_2, phi_A - t)            closure shell (not part of the Gate)
    phi_W = max(phi_3, -phi_C*)              subtract the closed cavity
    phi_W = max(phi_W, ...)                  sole guard: envelope below ``sole_min`` stays solid
    phi   = roundI(phi_W, b, r)              rounded intersection with the collar surface (collar region only)
    shoe  = max(phi, bed_z - z)              cut at the print bed

The gap that decides whether the template fits is measured on the *raw* skin
(``need_fit = max(0, t - phi_C)`` on the zero level of phi_E, inside M_below), before any smoothing;
:func:`gate_verdict` applies the §2 table to it. Nothing here modifies S1-S5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import trimesh
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from skimage import measure

from .ease import collar_region
from .sdf import ExportReport, Grid, SDFError, sample, sdf_to_mesh

WALL_MM = 2.0  # t
T_FILL_MM = 1.5  # hard fill thickness
T_MIN_MM = 1.2  # audit threshold
SIGMA_MM = 6.0  # push smoothing sigma (design doc: 6-10); real 44 sweep 4/6/8/10 in the S6 report
ROUND_R_MM = 1.0
SOLE_MIN_MM = 6.0  # D4
BED_Z_MM = 0.0
CAP_MARGIN_MM = 1.5  # skin within this of the collar cap (z_T) is the cap itself or the crest, not wall
BED_FIX_Z_MM = 0.5  # bed-contact surface: z < this and nz < BED_FIX_NZ
BED_FIX_NZ = -0.9
POOL_MM = 2.0  # surface samples are pooled to this spacing before smoothing (keeps pair counts bounded)
PHI_E_BAND_MM = 8.0  # envelope() default band: phi_E is exact within this distance of the skin


@dataclass(frozen=True)
class GateThresholds:
    """§2 table. ``area_*`` are provisional (D8) and re-fixed after the MVE."""

    p95_ok: float = 2.0
    max_bad: float = 4.0
    area_over_0p5_ok: float = 0.15
    area_over_0p5_bad: float = 0.30
    fill_area_ok: float = 0.03
    fill_area_bad: float = 0.10


@dataclass
class SkinSamples:
    """Zero level of phi_E sampled as marching-cubes vertices."""

    pts: np.ndarray  # (N, 3)
    normals: np.ndarray  # (N, 3) unit, outward
    area: np.ndarray  # (N,) mm^2 per vertex (1/3 of adjacent faces)
    below: np.ndarray  # (N,) bool: inside M_below
    bed: np.ndarray  # (N,) bool: bed-contact surface
    phi_c: np.ndarray  # (N,) phi_C at the vertex (distance to the cavity, negative if inside it)
    need: np.ndarray  # (N,) max(0, wall - phi_c), raw fit gap; 0 outside M_below and on the bed


def skin_samples(phi_e: np.ndarray, phi_c: np.ndarray, grid: Grid, collar, wall: float = WALL_MM,
                 bed_fix: bool = True) -> SkinSamples:
    """Raw fit gap on the outer skin (design §2: ``need_fit(q) = max(0, t - phi_C(q))``, q in M_below)."""
    padded = np.pad(phi_e, 1, mode="constant", constant_values=float(np.abs(phi_e).max()))
    verts, faces, _, _ = measure.marching_cubes(padded, level=0.0, spacing=grid.spacing)
    verts = verts + np.asarray(grid.origin) - np.asarray(grid.spacing)
    mesh = trimesh.Trimesh(verts, faces, process=False)
    area = np.bincount(mesh.faces.ravel(), weights=np.repeat(mesh.area_faces / 3.0, 3), minlength=len(verts))
    normals = np.asarray(mesh.vertex_normals)
    probe = sample(phi_e, grid, verts + 0.5 * normals)
    if probe.mean() < sample(phi_e, grid, verts - 0.5 * normals).mean():  # marching cubes winding: make them outward
        normals = -normals
    below = verts[:, 2] < collar.z_t(verts[:, :2]) - CAP_MARGIN_MM  # the cap closes the old cavity and lies inside phi_C
    bed = (verts[:, 2] < BED_Z_MM + BED_FIX_Z_MM) & (normals[:, 2] < BED_FIX_NZ) & bed_fix  # no bed, nothing to keep flat
    pc = sample(phi_c, grid, verts)
    need = np.where(below & ~bed, np.maximum(0.0, wall - pc), 0.0)
    return SkinSamples(verts, normals, area, below, bed, pc, need)


def pool(s: SkinSamples, cell_mm: float = POOL_MM) -> SkinSamples:
    """Bin the samples into ``cell_mm`` cells: mean position / normal, summed area, max need (so a peak survives)."""
    key = np.floor((s.pts - s.pts.min(axis=0)) / cell_mm).astype(np.int64)
    key = (key[:, 0] * (key[:, 1].max() + 1) + key[:, 1]) * (key[:, 2].max() + 1) + key[:, 2]
    _, inv = np.unique(key, return_inverse=True)
    n = inv.max() + 1
    cnt = np.bincount(inv, minlength=n).astype(float)

    def mean(a):
        return np.stack([np.bincount(inv, weights=a[:, k], minlength=n) for k in range(3)], axis=1) / cnt[:, None]

    nrm = mean(s.normals)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    need = np.zeros(n)
    np.maximum.at(need, inv, s.need)
    frac = lambda m: np.bincount(inv, weights=m.astype(float), minlength=n) / cnt  # noqa: E731
    return SkinSamples(mean(s.pts), nrm, np.bincount(inv, weights=s.area, minlength=n), frac(s.below) >= 0.5,
                       frac(s.bed) >= 0.5, np.bincount(inv, weights=s.phi_c, minlength=n) / cnt, need)


def smooth_push(pooled: SkinSamples, sigma: float = SIGMA_MM) -> np.ndarray:
    """P_fit on the pooled samples: max filter (radius 2 sigma), then normal-weighted normalised Gaussian.

    Both steps only look at pairs with ``n_i . n_j > 0`` within R = 2 sigma, and the Gaussian is truncated at R,
    so for every sample ``P >= need`` holds strictly (every contributor's filtered value is >= need there).
    P is zero outside M_below and on the bed-contact surface (fixed after smoothing, not an input average).
    """
    r = 2.0 * sigma
    tree = cKDTree(pooled.pts)
    coo = tree.sparse_distance_matrix(tree, r, output_type="coo_matrix")
    off = coo.row != coo.col  # the diagonal may or may not be stored (zero distance): drop and re-add it
    i, j, d = coo.row[off], coo.col[off], coo.data[off]
    k = np.arange(len(pooled.pts))
    i, j, d = np.concatenate([i, k]), np.concatenate([j, k]), np.concatenate([d, np.zeros(len(k))])
    dot = np.einsum("ij,ij->i", pooled.normals[i], pooled.normals[j])
    keep = dot > 0.0
    i, j, d, dot = i[keep], j[keep], d[keep], dot[keep]
    n = len(pooled.pts)
    m = np.zeros(n)
    np.maximum.at(m, i, pooled.need[j])
    w = np.exp(-0.5 * (d / sigma) ** 2) * dot
    num = np.bincount(i, weights=w * m[j], minlength=n)
    den = np.bincount(i, weights=w, minlength=n)
    p = num / np.maximum(den, 1e-12)
    p[~pooled.below | pooled.bed] = 0.0
    return p


def push_field(phi_e: np.ndarray, grid: Grid, pooled: SkinSamples, p: np.ndarray, band_mm: float = PHI_E_BAND_MM, k: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """Extend P from the skin to the band around it (inverse-distance mix of the ``k`` nearest samples).

    Returns ``(P, idx)`` with P a float32 field that is zero beyond ``max(p) + 1.5`` mm of the skin; ``idx`` are
    the flat indices of the band voxels (for reporting). Needs ``max(p) + 1.5 <= band_mm`` (the band in which phi_E is an exact distance).
    """
    out = np.zeros(phi_e.shape, np.float32)
    if p.max() <= 0:
        return out, np.empty(0, np.int64)
    band = float(p.max()) + 1.5
    if band > band_mm:
        raise SDFError(f"push {p.max():.1f} mm exceeds what the banded phi_E can carry ({band_mm - 1.5:g} mm): build envelope(band=...) wider")
    idx = np.flatnonzero(np.abs(phi_e.ravel()) < band)
    ijk = np.stack(np.unravel_index(idx, phi_e.shape), axis=1)
    pts = grid.coords(ijk)
    dist, nn = cKDTree(pooled.pts).query(pts, k=k)
    wgt = 1.0 / (dist**2 + 1.0)
    out.ravel()[idx] = (wgt * p[nn]).sum(axis=1) / wgt.sum(axis=1)
    return out, idx


def round_intersection(a: np.ndarray, b: np.ndarray, r: float) -> np.ndarray:
    """hg_sdf ``fOpIntersectionRound``: min(-r, max(a, b)) + |(max(r + a, 0), max(r + b, 0))|."""
    return np.minimum(-r, np.maximum(a, b)) + np.hypot(np.maximum(r + a, 0.0), np.maximum(r + b, 0.0))


def collar_columns(grid: Grid, collar, reach: float):
    """(region (nx, ny) bool, z_T (nx, ny), 1 / sqrt(1 + |grad z_T|^2) (nx, ny)) for the collar rounding."""
    from .ease import _column_xy

    zt = collar.z_t(_column_xy(grid)).reshape(grid.shape[:2])
    gx, gy = np.gradient(zt, grid.h)
    return collar_region(grid, collar, reach), zt, 1.0 / np.sqrt(1.0 + gx * gx + gy * gy)


@dataclass
class Composed:
    phi: np.ndarray  # float32 phi_shoe
    grid: Grid
    phi_1: np.ndarray  # fields kept for labelling the exported faces
    below: np.ndarray  # M_below (bool)
    report: dict
    gate: dict
    p_fit: np.ndarray | None = field(default=None, repr=False)


def _wmax(a, w, q):
    """q-th percentile of ``a`` with area weights ``w``."""
    o = np.argsort(a)
    c = np.cumsum(w[o])
    return float(a[o][np.searchsorted(c, q / 100.0 * c[-1])])


def hotspots(s: SkinSamples, threshold: float, link_mm: float = 3.0, top: int = 8) -> list[dict]:
    """Connected clusters of raw need above ``threshold`` (skin samples linked within ``link_mm``), largest first."""
    sel = np.flatnonzero(s.below & ~s.bed & (s.need > threshold))
    if not len(sel):
        return []
    pts = s.pts[sel]
    pairs = cKDTree(pts).query_pairs(link_mm, output_type="ndarray")
    n = len(sel)
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    out = []
    for c in np.unique(lab):
        m = lab == c
        w = s.need[sel][m]
        k = int(np.argmax(w))
        out.append({"area_mm2": round(float(s.area[sel][m].sum())), "max_mm": round(float(w.max()), 2),
                    "max_at": [round(float(v), 1) for v in pts[m][k]],
                    "centre": [round(float(v), 1) for v in pts[m].mean(axis=0)]})
    return sorted(out, key=lambda d: -d["area_mm2"])[:top]


def push_slope(pooled: SkinSamples, p: np.ndarray, max_d: float = 3.5) -> float:
    """Largest |dP / d| between neighbouring skin samples with similar normals (how steep the bulge is)."""
    pairs = cKDTree(pooled.pts).query_pairs(max_d, output_type="ndarray")
    i, j = pairs[:, 0], pairs[:, 1]
    ok = (np.einsum("ij,ij->i", pooled.normals[i], pooled.normals[j]) > 0.9) & pooled.below[i] & pooled.below[j] & ~pooled.bed[i] & ~pooled.bed[j]
    d = np.linalg.norm(pooled.pts[i] - pooled.pts[j], axis=1)
    ok &= d > 0.5
    return float(np.max(np.abs(p[i] - p[j])[ok] / d[ok])) if ok.any() else 0.0


def gate_metrics(s: SkinSamples, wall: float, t_fill: float) -> dict:
    """§2 Gate metrics from the raw skin samples (area weighted, M_below, bed-contact excluded)."""
    sel = s.below & ~s.bed
    a, need = s.area[sel], s.need[sel]
    total = float(a.sum())
    fill_need = np.maximum(0.0, t_fill - s.phi_c[sel])  # what a hard fill alone (no push) would have to add
    return {
        "outer_area_below_collar_mm2": round(total),
        "need_fit_p50_mm": round(_wmax(need, a, 50), 3),
        "need_fit_p95_mm": round(_wmax(need, a, 95), 3),
        "need_fit_p99_mm": round(_wmax(need, a, 99), 3),
        "need_fit_max_mm": round(float(need.max()), 3),
        "need_fit_max_at": [round(float(v), 1) for v in s.pts[sel][np.argmax(need)]],
        "need_fit_area_over_0p5_frac": round(float(a[need > 0.5].sum() / total), 4),
        "need_fit_area_over_2_frac": round(float(a[need > 2.0].sum() / total), 4),
        "need_fit_area_over_4_frac": round(float(a[need > 4.0].sum() / total), 4),
        "hard_fill_only_area_frac": round(float(a[fill_need > 0].sum() / total), 4),
        "hotspots_over_4mm": hotspots(s, 4.0),
        "hotspots_over_2mm": hotspots(s, 2.0),
    }


def gate_verdict(m: dict, residual_fill_frac: float | None = None, th: GateThresholds = GateThresholds()) -> dict:
    """Apply the §2 table: per metric 'ok' / 'reshape' / 'not_applicable', and the overall verdict.

    The hard-fill row is judged on the larger of the no-push fill area (``hard_fill_only_area_frac``, the raw
    gap) and the residual after the level-2 push (``residual_fill_frac``): the push must not hide a bad fit.
    """
    def tri(v, ok, bad):
        return "ok" if v <= ok else ("not_applicable" if v > bad else "reshape")

    fill = m["hard_fill_only_area_frac"] if residual_fill_frac is None else max(m["hard_fill_only_area_frac"], residual_fill_frac)
    rows = {
        "need_fit_p95": (m["need_fit_p95_mm"], "ok" if m["need_fit_p95_mm"] <= th.p95_ok else "reshape"),
        "need_fit_max": (m["need_fit_max_mm"], "not_applicable" if m["need_fit_max_mm"] > th.max_bad else "ok"),
        "need_fit_area_over_0p5": (m["need_fit_area_over_0p5_frac"],
                                   tri(m["need_fit_area_over_0p5_frac"], th.area_over_0p5_ok, th.area_over_0p5_bad)),
        "hard_fill_area": (fill, tri(fill, th.fill_area_ok, th.fill_area_bad)),
    }
    status = [v[1] for v in rows.values()]
    overall = "not_applicable" if "not_applicable" in status else ("reshape" if "reshape" in status else "usable")
    return {"verdict": overall, "rows": {k: {"value": v[0], "status": v[1]} for k, v in rows.items()},
            "thresholds": th.__dict__, "note": "area thresholds are provisional (D8)"}


def _push_by_x(pooled: SkinSamples, p: np.ndarray, step: float = 20.0) -> list[dict]:
    keep = pooled.below & ~pooled.bed
    b = np.floor(pooled.pts[:, 0] / step).astype(int)
    out = []
    for k in np.unique(b[keep]):
        m = keep & (b == k)
        out.append({"x_mm": float(k * step), "max_mm": round(float(p[m].max()), 2),
                    "area_over_0p5_frac": round(float(pooled.area[m & (p > 0.5)].sum() / pooled.area[m].sum()), 3)})
    return out


def compose(
    phi_e: np.ndarray,
    phi_c: np.ndarray,
    phi_a: np.ndarray | None,
    phi_c_star: np.ndarray | None,
    grid: Grid,
    collar,
    *,
    wall: float = WALL_MM,
    t_fill: float = T_FILL_MM,
    sigma: float = SIGMA_MM,
    round_r: float = ROUND_R_MM,
    sole_min: float | None = SOLE_MIN_MM,
    bed_z: float = BED_Z_MM,
    band_mm: float = PHI_E_BAND_MM,
    bed_cut: bool = True,
    reach: float = 10.0,
    push: bool = True,
    keep_p: bool = False,
) -> Composed:
    """phi_shoe from phi_E (S2), phi_C (S4), phi_A / phi_C* (S5), all on ``grid`` (see the module docstring).

    ``band_mm``: half width in which ``phi_e`` is exact (``envelope(band=...)``); a push needs max P + 1.5 <= band.
    ``phi_a`` / ``phi_c_star`` None = closure off (no closure shell, cavity is ``phi_c``): processes that need no
    support (SLS/MJF). ``bed_cut=False``: no cut at ``bed_z`` and no bed-contact surface held flat.
    ``push=False`` skips level 2 (only hard fill), used to measure what the push buys.
    ``sole_min``: the envelope below ``bed_z + sole_min`` stays solid under the cavity (None = off); the report
    gives the volume it had to add, which is 0 when the pose already honours the sole thickness.
    """
    if (phi_a is None) != (phi_c_star is None):
        raise SDFError("phi_a and phi_c_star are given together (closure on) or both None (closure off)")
    for name, f in (("phi_e", phi_e), ("phi_c", phi_c), ("phi_a", phi_a), ("phi_c_star", phi_c_star)):
        if f is not None and f.shape != tuple(grid.shape):
            raise SDFError(f"{name} shape {f.shape} does not match grid {grid.shape}")
    voxel = grid.h * grid.h * grid.dz
    z = (grid.origin[2] + np.arange(grid.shape[2]) * grid.dz).astype(np.float32)
    below = collar.mask_below(grid)

    skin = skin_samples(phi_e, phi_c, grid, collar, wall, bed_fix=bed_cut)
    gate = gate_metrics(skin, wall, t_fill)
    pooled = pool(skin)
    p = smooth_push(pooled, sigma) if push else np.zeros(len(pooled.pts))
    p_max = float(p.max())
    i_max = int(np.argmax(p))

    # level 2
    pf, band_idx = push_field(phi_e, grid, pooled, p, band_mm)
    phi_1 = phi_e - pf
    # level 3 (only inside M_below): min(phi_1, phi_c - t_fill)
    fill = phi_c - np.float32(t_fill)
    fill[~below] = np.float32(band_mm + 8.0)
    phi_2 = np.minimum(phi_1, fill)
    # closure shell
    phi_3 = phi_2 if phi_a is None else np.minimum(phi_2, phi_a - np.float32(wall))
    phi = np.maximum(phi_3, -(phi_c if phi_c_star is None else phi_c_star))
    del phi_3

    solid_before = int((phi < 0).sum())
    sole_added = 0
    if sole_min:
        under = np.maximum(phi_e, (z - np.float32(bed_z + sole_min))[None, None, :])  # envelope below sole_min
        sole_added = int(((under < 0) & (phi >= 0)).sum())
        phi = np.minimum(phi, under)
        del under

    # collar rounding, only in the collar columns
    region, zt, inv = collar_columns(grid, collar, reach)
    cols = np.flatnonzero(region.ravel())
    sub = phi.reshape(-1, grid.shape[2])[cols]
    b = (z[None, :] - zt.ravel()[cols, None].astype(np.float32)) * inv.ravel()[cols, None].astype(np.float32)
    before = int((sub < 0).sum())
    sub = round_intersection(sub, b, round_r).astype(np.float32)
    phi.reshape(-1, grid.shape[2])[cols] = sub
    cut_vox = before - int((sub < 0).sum())
    # print bed
    if bed_cut:
        np.maximum(phi, (bed_z - z)[None, None, :], out=phi)
    # Nodes exactly on the cut plane (value 0) make marching cubes emit degenerate, non-manifold faces: nudge them out.
    phi[phi == 0] = np.float32(1e-3)

    # sealed pockets (outside air not connected to the grid border) would be inner shells in the STL: fill them
    air, _ = ndimage.label(phi >= 0)
    pockets = (air != air[0, 0, 0]) & (phi >= 0)
    del air
    n_pocket = int(pockets.sum())
    phi[pockets] = np.float32(-1e-3)
    del pockets

    inside = phi < 0
    in1 = phi_1 < 0
    fill_in = (fill < 0) & ~in1
    report = {
        "params": {"wall": wall, "t_fill": t_fill, "sigma": sigma, "round_r": round_r, "sole_min": sole_min,
                   "bed_z": bed_z, "closure": phi_a is not None, "bed_cut": bed_cut, "alpha_b_deg": round(math.degrees(math.atan2(grid.h, grid.dz)), 3)},
        "volume_mm3": {
            "envelope": round(float((phi_e < 0).sum() * voxel)),
            "pushed": round(float(((phi_1 < 0) & (phi_e >= 0)).sum() * voxel)),
            "hard_fill": round(float((fill_in & inside).sum() * voxel)),
            "closure_shell": round(float((~in1 & ~fill_in & inside).sum() * voxel)),
            "sole_guard": round(sole_added * voxel),
            "collar_cut": round(cut_vox * voxel),
            "sealed_pockets_filled": round(n_pocket * voxel, 1),
            "shoe": round(float(inside.sum() * voxel)),
            "shoe_before_sole_and_collar": round(solid_before * voxel),
        },
        "push": {
            "sigma_mm": sigma,
            "max_mm": round(p_max, 3),
            "max_at": [round(float(v), 1) for v in pooled.pts[i_max]],
            "area_over_0p5_mm2": round(float(pooled.area[p > 0.5].sum())),
            "area_over_2_mm2": round(float(pooled.area[p > 2.0].sum())),
            "area_over_0p5_frac": round(float(pooled.area[p > 0.5].sum() / pooled.area[pooled.below & ~pooled.bed].sum()), 4),
            "max_slope": round(push_slope(pooled, p), 3),
            "by_x": _push_by_x(pooled, p),
            "min_margin_p_minus_need_mm": round(float((p - pooled.need)[pooled.below & ~pooled.bed].min()), 4),
        },
    }
    out = Composed(phi, grid, phi_1, below, report, gate, pf if keep_p else None)
    return out


LABELS = ("cavity", "skin", "hard_fill", "closure_shell", "collar", "bed", "mixed")


def label_faces(mesh: trimesh.Trimesh, comp: Composed, phi_c: np.ndarray, phi_a: np.ndarray | None, phi_c_star: np.ndarray | None,
                collar, *, wall: float = WALL_MM, t_fill: float = T_FILL_MM, mix_vox: float = 0.5) -> np.ndarray:
    """Source of each exported face (design §5.1): sample the contributing fields at the face centroid and pick
    the one with the smallest |value|; 'mixed' when a second one is within ``mix_vox`` voxels as well. Object array of str."""
    g = comp.grid
    c = mesh.triangles_center
    zt = collar.z_t(c[:, :2])
    vals = np.stack([
        -sample(phi_c if phi_c_star is None else phi_c_star, g, c),
        sample(comp.phi_1, g, c),
        np.where(c[:, 2] < zt, sample(phi_c, g, c) - t_fill, 1e3),
        np.full(len(c), 1e3) if phi_a is None else sample(phi_a, g, c) - wall,
        np.abs(c[:, 2] - zt),
        c[:, 2] - comp.report["params"]["bed_z"] if comp.report["params"]["bed_cut"] else np.full(len(c), 1e3),
    ], axis=1)
    a = np.abs(vals)
    order = np.argsort(a, axis=1)
    first = order[:, 0]
    second_close = np.take_along_axis(a, order[:, 1:2], axis=1)[:, 0] < mix_vox * g.h
    names = np.array(LABELS[:-1], dtype=object)
    lab = names[first]
    lab[second_close] = "mixed"
    bed = (c[:, 2] - comp.report["params"]["bed_z"] < 0.5 * g.dz) & (mesh.face_normals[:, 2] < BED_FIX_NZ)
    bed &= comp.report["params"]["bed_cut"]
    lab[bed] = "bed"  # the cut plane coincides with the skin's own sole there; the plane wins (design §5.1)
    return lab


def export(comp: Composed, max_dropped_mm3: float = 1000.0, min_shell_mm3: float = 1.0):
    """Marching cubes, largest component (``sdf_to_mesh``), then drop zero-volume slivers.

    Nodes with |phi| ~ 0 make marching cubes emit 2- to 8-face shells of ~0 volume inside the solid; they count
    as extra bodies in the STL. Inner shells smaller than ``min_shell_mm3`` are removed; ``comp`` has no real
    pockets left (``compose`` fills them), so anything larger would be a construction error and raises.
    Returns (mesh, ExportReport).
    """
    mesh, rep = sdf_to_mesh(comp.phi, comp.grid, max_dropped_mm3)
    parts = mesh.split(only_watertight=False)
    if len(parts) == 1:
        return mesh, rep
    main = max(parts, key=lambda p: len(p.faces))
    vols = [abs(p.volume) for p in parts if p is not main]
    if vols and max(vols) >= min_shell_mm3:
        raise SDFError(f"exported mesh holds an extra shell of {max(vols):.1f} mm^3 (sealed pocket or fragment)")
    return main, ExportReport(rep.n_components, float(main.volume), rep.dropped_volume_mm3, len(main.faces))
