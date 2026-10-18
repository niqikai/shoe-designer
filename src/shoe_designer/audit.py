"""Data audit for catalogued meshes (issue #7).

Everything runs on meshes already in the canonical frame (see ``assets``): mm, +X heel->toe,
+Z up, printed sole-down. Sampling is seeded so reports are reproducible.

Known limits (v0): thickness and overhang are sampled, not exhaustive; "enclosed" overhang only
means "other model material lies below the face", not "inside a closed cavity"; fit is a rigid
heel/seat search, not a registration.
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


def _pct(a: np.ndarray, qs=(1, 5, 50, 95)) -> dict:
    return {f"p{q}": round(float(np.percentile(a, q)), 3) for q in qs}


def sample_surface(mesh: trimesh.Trimesh, n: int):
    pts, face = trimesh.sample.sample_surface(mesh, n, seed=SEED)
    return np.asarray(pts), mesh.face_normals[face]


def geometry(mesh: trimesh.Trimesh) -> dict:
    comps = trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)))
    ext = mesh.extents
    return {
        "faces": int(len(mesh.faces)),
        "extents_mm": [round(float(x), 2) for x in ext],
        "bounds_min": [round(float(x), 2) for x in mesh.bounds[0]],
        "watertight": bool(mesh.is_watertight),
        "bodies": int(len(comps)),
        "volume_cm3": round(float(mesh.volume) / 1000.0, 2) if mesh.is_watertight else None,
    }


def wall_thickness(mesh: trimesh.Trimesh, n: int = 20000) -> dict:
    """Distance along the inward normal to the first opposite surface, at sampled points."""
    pts, nrm = sample_surface(mesh, n)
    origins = pts - nrm * 0.05
    loc, ray, _ = mesh.ray.intersects_location(origins, -nrm, multiple_hits=False)
    d = np.linalg.norm(loc - origins[ray], axis=1)
    return {
        "samples": int(n),
        "hit_fraction": round(len(d) / n, 4),
        "min_mm": round(float(d.min()), 3),
        "median_mm": round(float(np.median(d)), 3),
        **_pct(d),
        f"frac_below_{THIN_MM}mm": round(float((d < THIN_MM).mean()), 4),
    }


def overhangs(mesh: trimesh.Trimesh) -> dict:
    """Area of steep downward faces, split by whether model material lies below them."""
    fn, area, ctr = mesh.face_normals, mesh.area_faces, mesh.triangles_center
    steep = (fn[:, 2] < OVERHANG_NZ) & (ctr[:, 2] > BED_TOL_MM)
    idx = np.flatnonzero(steep)
    total = float(area.sum())
    if len(idx) == 0:
        return {"overhang_area_frac": 0.0, "open_frac": 0.0, "enclosed_frac": 0.0, "by_third": {}}
    o = ctr[idx] + fn[idx] * 0.05
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


def fit_to_last(last: trimesh.Trimesh, shoe: trimesh.Trimesh, n: int = 4000, tol: float = 0.005) -> dict:
    """Seat the last in the shoe's cavity.

    For each lengthwise offset dx, find the lowest height dz at which at most ``tol`` of the
    last's sampled surface points lie inside the shoe's solid. The lowest seat wins; ties go to the position closest to centred.
    Clearance is the distance from last points to the shoe surface, measured only below 60% of
    the shoe height so the open collar does not count.
    """
    pts, _ = sample_surface(last, n)
    slack = float(shoe.extents[0] - last.extents[0])
    half = abs(slack) / 2.0 + 2.0
    dxs = slack / 2.0 + np.linspace(-half, half, 9)  # includes the centred position
    dzs = np.arange(0.0, float(shoe.extents[2]) * 0.6, 2.0)
    seats, low = [], None  # low: least-colliding position with the last seated near the floor
    for dx in dxs:
        for dz in dzs:
            moved = pts + [dx, 0.0, dz]
            frac = float(shoe.contains(moved).mean())
            if dz <= LOW_SEAT_MM and (low is None or frac < low[2]):
                low = (float(dx), float(dz), frac)
            if frac <= tol:
                seats.append((float(dz), abs(float(dx) - slack / 2.0), float(dx), frac))
                break
    out = {"length_delta_mm": round(slack, 2), "length_ratio": round(float(shoe.extents[0] / last.extents[0]), 4)}
    if not seats:
        dx, dz, frac = low
        return {
            **out,
            "seats": False,
            "reason": "last collides with the shoe solid at every tested position",
            f"best_seat_dz_le_{LOW_SEAT_MM:g}mm": {"dx_mm": round(dx, 2), "dz_mm": round(dz, 2), "collision_frac": round(frac, 4)},
        }
    dz, _, dx, frac = min(seats)  # lowest seat; ties go to the most centred position
    moved = pts + [dx, 0.0, dz]
    spts, _ = sample_surface(shoe, 200000)
    dist, _ = cKDTree(spts).query(moved)
    zone = moved[:, 2] < shoe.extents[2] * 0.6
    d = dist[zone]
    return {
        **out,
        "seats": True,
        "dx_mm": round(dx, 2),
        "seat_dz_mm": round(dz, 2),
        "collision_frac": round(frac, 4),
        "clearance_min_mm": round(float(d.min()), 3),
        "clearance_median_mm": round(float(np.median(d)), 3),
        **{f"clearance_{k}": v for k, v in _pct(d, (5, 95)).items()},
    }


def audit_asset(loaded, last_mesh: trimesh.Trimesh | None = None) -> dict:
    a, m = loaded.asset, loaded.mesh
    out = {"id": a.id, "role": a.role, "license": a.license, "geometry": geometry(m)}
    if a.role == "shoe":
        out["thickness"] = wall_thickness(m)
        out["overhangs"] = overhangs(m)
        if last_mesh is not None:
            out["fit_to_last"] = fit_to_last(last_mesh, m)
    return out
