"""Data audit for catalogued meshes (issue #7).

Everything runs on meshes already in the canonical frame (see ``assets``): mm, +X heel->toe,
+Z up, printed sole-down. Sampling is seeded so reports are reproducible.

Known limits (v0):
- thickness and overhang are sampled, not exhaustive;
- "enclosed" overhang means "other model material lies below the face" (first-hit ray test, so it
  does not need a watertight mesh), not "inside a closed cavity";
- fit is a rigid search over lengthwise, lateral and vertical offsets (no rotation, no scaling) and
  needs watertight meshes, because it relies on inside/outside tests;
- clearance is a nearest-neighbour distance to a dense point cloud of the shoe, so it is
  over-estimated by up to ``clearance_resolution_mm`` (reported).
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

SEED = 7
THIN_MM = 1.2  # below this TPU walls are unreliable
OVERHANG_NZ = -np.sin(np.radians(45.0))  # normals pointing down more steeply than 45 deg
BED_TOL_MM = 0.2
LOW_SEAT_MM = 30.0  # deepest the last can plausibly sit above the shoe's z=0
RAY_OFFSET_MM = 0.05  # rays start this far inside the surface so they do not re-hit it
FIT_DY_MM = (-6.0, -3.0, 0.0, 3.0, 6.0)
FIT_DZ_STEP_MM = 3.0


def _pct(a: np.ndarray, qs=(1, 5, 50, 95)) -> dict:
    return {f"p{q}": round(float(np.percentile(a, q)), 3) for q in qs}


def sample_surface(mesh: trimesh.Trimesh, n: int):
    pts, face = trimesh.sample.sample_surface(mesh, n, seed=SEED)
    return np.asarray(pts), mesh.face_normals[face]


def geometry(mesh: trimesh.Trimesh) -> dict:
    ext = mesh.extents
    return {
        "faces": int(len(mesh.faces)),
        "extents_mm": [round(float(x), 2) for x in ext],
        "bounds_min": [round(float(x), 2) for x in mesh.bounds[0]],
        "watertight": bool(mesh.is_watertight),
        "bodies": int(mesh.body_count),
        "volume_cm3": round(float(mesh.volume) / 1000.0, 2) if mesh.is_watertight else None,
    }


def wall_thickness(mesh: trimesh.Trimesh, n: int = 20000) -> dict:
    """Distance along the inward normal to the first opposite surface, at sampled points."""
    pts, nrm = sample_surface(mesh, n)
    origins = pts - nrm * RAY_OFFSET_MM
    loc, ray, _ = mesh.ray.intersects_location(origins, -nrm, multiple_hits=False)
    out = {"samples": int(n), "hit_fraction": round(len(ray) / n, 4), "thin_threshold_mm": THIN_MM}
    if len(ray) == 0:  # open or inconsistently oriented surface: nothing to measure
        return {**out, "min_mm": None, "median_mm": None, "frac_thin": None}
    d = np.linalg.norm(loc - origins[ray], axis=1) + RAY_OFFSET_MM  # distance from the surface itself
    return {
        **out,
        "min_mm": round(float(d.min()), 3),
        "median_mm": round(float(np.median(d)), 3),
        **_pct(d),
        "frac_thin": round(float((d < THIN_MM).mean()), 4),
    }


def overhangs(mesh: trimesh.Trimesh) -> dict:
    """Area of steep downward faces, split by whether model material lies below them."""
    fn, area, ctr = mesh.face_normals, mesh.area_faces, mesh.triangles_center
    steep = (fn[:, 2] < OVERHANG_NZ) & (ctr[:, 2] > BED_TOL_MM)
    idx = np.flatnonzero(steep)
    total = float(area.sum())
    if len(idx) == 0:
        return {"overhang_area_frac": 0.0, "open_frac": 0.0, "enclosed_frac": 0.0, "by_third": {}}
    o = ctr[idx] + fn[idx] * RAY_OFFSET_MM
    below = np.zeros(len(idx), bool)
    _, ray, _ = mesh.ray.intersects_location(o, fn[idx], multiple_hits=False)
    below[ray] = True
    L = mesh.bounds[1][0] - mesh.bounds[0][0]
    third = np.minimum((ctr[idx, 0] - mesh.bounds[0][0]) / L * 3, 2.999).astype(int)
    names = ["heel", "mid", "toe"]
    return {
        "overhang_area_frac": round(float(area[idx].sum()) / total, 4),
        "open_frac": round(float(area[idx][~below].sum()) / total, 4),
        "enclosed_frac": round(float(area[idx][below].sum()) / total, 4),
        "by_third": {names[t]: round(float(area[idx][third == t].sum()) / total, 4) for t in range(3)},
    }


def _ratios(last: trimesh.Trimesh, shoe: trimesh.Trimesh) -> dict:
    r = shoe.extents / last.extents
    return {
        "length_delta_mm": round(float(shoe.extents[0] - last.extents[0]), 2),
        "length_ratio": round(float(r[0]), 4),
        "width_ratio": round(float(r[1]), 4),
        "height_ratio": round(float(r[2]), 4),  # shoes are shorter than lasts: lasts include the shaft
    }


def _thirds_and_sides(last_pts: np.ndarray, hit: np.ndarray, x0: float, length: float, yc: float) -> dict:
    t = np.minimum((last_pts[:, 0] - x0) / length * 3, 2.999).astype(int)
    by_third = {n: round(float(hit[t == i].mean()), 4) for i, n in enumerate(["heel", "mid", "toe"]) if (t == i).any()}
    sides = {"y_pos": last_pts[:, 1] > yc, "y_neg": last_pts[:, 1] <= yc}
    return {"by_third": by_third, "by_side": {k: round(float(hit[m].mean()), 4) for k, m in sides.items() if m.any()}}


def _volume_points(mesh: trimesh.Trimesh, count: int, rng) -> np.ndarray:
    """Uniform random points inside a watertight mesh (rejection sampling in its bbox)."""
    lo, hi = mesh.bounds
    got: list[np.ndarray] = []
    total = 0
    while total < count:
        cand = rng.uniform(lo, hi, size=(4 * count, 3))
        keep = cand[mesh.contains(cand)]
        got.append(keep)
        total += len(keep)
    return np.vstack(got)[:count]


def fit_to_last(last: trimesh.Trimesh, shoe: trimesh.Trimesh, n: int = 4000, tol: float = 0.005) -> dict:
    """Seat the last in the shoe's cavity.

    Collision is checked two ways: the share of the last's surface points inside the shoe solid,
    and the share of the last's *volume* overlapping the shoe solid (random points inside the
    last), which also catches thin walls and internal bumps that slip between surface samples.
    Surface area of the shoe is deliberately not used: a lattice sole has huge area per mm3 and
    would swamp the measure. For each lengthwise/lateral offset, find the lowest height at which
    both stay under ``tol``; the lowest seat wins, ties go to the most centred.
    Clearance is signed (negative = penetration) and measured only below 60% of the shoe height
    so the open collar does not count.
    """
    out = _ratios(last, shoe)
    if not (last.is_watertight and shoe.is_watertight):
        return {**out, "seats": None, "reason": "mesh not watertight; inside/outside test unreliable"}
    pts, _ = sample_surface(last, n)
    spts, _ = sample_surface(shoe, 5 * n)
    rng = np.random.default_rng(SEED)
    vol = _volume_points(last, n // 2, rng)

    def collide(dx, dy, dz):
        off = np.array([dx, dy, dz])
        return float(shoe.contains(pts + off).mean()), float(shoe.contains(vol + off).mean())

    slack = float(shoe.extents[0] - last.extents[0])
    half = abs(slack) / 2.0 + 2.0
    dxs = slack / 2.0 + np.linspace(-half, half, 5)  # includes the centred position
    dzs = np.arange(0.0, float(shoe.extents[2]) * 0.6, FIT_DZ_STEP_MM)
    seats, low = [], None  # low: least-colliding position with the last seated near the floor
    for dx in dxs:
        for dy in FIT_DY_MM:
            for dz in dzs:
                a, b = collide(dx, dy, dz)
                frac = max(a, b)
                if dz <= LOW_SEAT_MM and (low is None or frac < low[0]):
                    low = (frac, float(dx), float(dy), float(dz), a, b)
                if frac <= tol:
                    seats.append((float(dz), abs(float(dx) - slack / 2.0) + abs(float(dy)), float(dx), float(dy), a, b))
                    break
    if seats:
        dz, _, dx, dy, a, b = min(seats)
        placement = {"dx_mm": round(dx, 2), "dy_mm": round(dy, 2), "dz_mm": round(dz, 2)}
    else:
        _, dx, dy, dz, a, b = low
        placement = {"dx_mm": round(dx, 2), "dy_mm": round(dy, 2), "dz_mm": round(dz, 2)}
    off = np.array([dx, dy, dz])
    moved = pts + off
    inside = shoe.contains(moved)
    ycentre = float(last.bounds.mean(axis=0)[1] + dy)
    detail = {
        **placement,
        "last_in_shoe_frac": round(a, 4),
        "volume_overlap_frac": round(b, 4),
        "collision_breakdown": _thirds_and_sides(moved, inside, float(last.bounds[0][0] + dx), float(last.extents[0]), ycentre),
    }
    if not seats:
        return {
            **out,
            "seats": False,
            "reason": "last collides with the shoe solid at every tested position",
            "best_low_seat": detail,
        }
    cloud = np.vstack([shoe.vertices, spts])  # dense cloud: mesh vertices plus surface samples
    tree = cKDTree(cloud)
    dist, _ = tree.query(moved)
    signed = np.where(inside, -dist, dist)
    zone = moved[:, 2] < shoe.extents[2] * 0.6
    d = signed[zone]
    resolution = float(np.median(tree.query(cloud[:2000], k=2)[0][:, 1])) / 2.0
    return {
        **out,
        "seats": True,
        **detail,
        "clearance_min_mm": round(float(d.min()), 3),
        "clearance_median_mm": round(float(np.median(d)), 3),
        **{f"clearance_{k}": v for k, v in _pct(d, (5, 95)).items()},
        "clearance_resolution_mm": round(resolution, 3),
    }


def infer_last_size(last: trimesh.Trimesh, shoe_rows: list[dict]) -> dict:
    """Estimate which EU size the last corresponds to, from lengths only.

    For each series of shoes with known sizes, interior length = outer length - 2 x median wall
    (the end walls), fitted linearly against size, read off at the last's length. Fit-based
    evidence is unusable when the shoe cavity has a different shape, so this stays a length
    argument and is never marked confirmed.
    """
    series: dict[str, list[tuple[float, float]]] = {}
    for r in shoe_rows:
        wall = (r.get("thickness") or {}).get("median_mm")
        if r.get("size_eu") is None or r.get("series") is None or wall is None:
            continue
        series.setdefault(r["series"], []).append((r["size_eu"], r["geometry"]["extents_mm"][0] - 2 * wall))
    target = float(last.extents[0])
    rows, estimates = [], []
    for name, pts in sorted(series.items()):
        if len(pts) < 2:
            continue
        sizes, inner = zip(*sorted(pts))
        slope, icpt = np.polyfit(inner, sizes, 1)
        est = float(slope * target + icpt)
        extrapolated = not (min(sizes) <= est <= max(sizes))
        rows.append({
            "series": name,
            "sizes": list(sizes),
            "interior_lengths_mm": [round(x, 1) for x in inner],
            "estimate_eu": round(est, 2),
            "extrapolated": extrapolated,
        })
        estimates.append(est)
    return {
        "last_length_mm": round(target, 1),
        "series": rows,
        "range_eu": [round(min(estimates), 2), round(max(estimates), 2)] if estimates else None,
        "assumptions": [
            "the shoe series and the last share one size grading",
            "end wall thickness = median sampled wall thickness of each shoe",
            "length only: the cavity shape does not match the last, so fit cannot confirm it",
        ],
        "confirmed": False,
    }


def audit_asset(loaded, last_mesh: trimesh.Trimesh | None = None) -> dict:
    a, m = loaded.asset, loaded.mesh
    out = {"id": a.id, "role": a.role, "license": a.license, "size_eu": a.size_eu, "series": a.series,
           "geometry": geometry(m)}
    if a.role == "shoe":
        out["thickness"] = wall_thickness(m)
        out["overhangs"] = overhangs(m)
        if last_mesh is not None:
            out["fit_to_last"] = fit_to_last(last_mesh, m)
    return out
