"""Outsole footprint and curved solid midsole derived from the deformed last."""
import numpy as np
from mathutils import Vector

def outline_distance(outline, x, y):
    """Signed planar distance to the measured outline; negative inside."""
    points = np.asarray(outline, dtype=np.float64)
    # Resample the measured curve at about 1 mm, without fitting a new shape.
    arc = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points[:, :2], axis=0), axis=1))]
    stations = np.linspace(0, arc[-1], max(256, int(arc[-1]) + 1))
    curve = np.stack([np.interp(stations, arc, points[:, axis]) for axis in range(3)], axis=1)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    best = np.full(xx.shape, np.inf)
    edge_height = np.zeros(xx.shape, dtype=np.float64)
    inside = np.zeros(xx.shape, dtype=bool)
    for a, b in zip(curve[:-1], curve[1:]):
        delta = b - a
        length2 = float(delta[:2] @ delta[:2])
        if length2 < 1e-12:
            continue
        t = np.clip(((xx - a[0]) * delta[0] + (yy - a[1]) * delta[1]) / length2, 0, 1)
        dist2 = (xx - a[0] - t * delta[0]) ** 2 + (yy - a[1] - t * delta[1]) ** 2
        closer = dist2 < best
        edge_height[closer] = (a[2] + t * delta[2])[closer]
        best = np.minimum(best, dist2)
        if abs(delta[1]) > 1e-12:
            inside ^= ((a[1] > yy) != (b[1] > yy)) & (xx < a[0] + delta[0] * (yy - a[1]) / delta[1])
    return np.sqrt(best) * np.where(inside, -1, 1), edge_height


def sole_maps(tree, features, params, x, y):
    footprint, shoulder_height = outline_distance(features["plantar_outline_mm"], x, y)
    top = np.full(footprint.shape, np.nan, dtype=np.float32)
    # First upward hit is the actual plantar surface, including arch relief.
    for i, vx in enumerate(x):
        for j, vy in enumerate(y):
            if footprint[i, j] > params["outsole_flare_mm"] + 3:
                continue
            location, _, _, _ = tree.ray_cast(Vector((float(vx), float(vy), -200)), Vector((0, 0, 1)), 500)
            if location is not None:
                top[i, j] = location.z
    valid = np.isfinite(top)
    if not valid.any():
        raise ValueError("未能采样楦底曲面。")
    # Extend the measured sole/sidewall transition, rather than the unstable
    # grazing-ray silhouette. This keeps the flared shoulder smooth.
    use_plantar = valid & (footprint <= 0)
    top[~use_plantar] = (shoulder_height - .10 * np.maximum(footprint, 0))[~use_plantar]
    centerline = features["longitudinal_centerline"]
    length = features["dimensions_mm"]["length"]
    heel = min(centerline, key=lambda p: abs(p["y_mm"] - .15 * length))
    front = min(centerline, key=lambda p: abs(p["y_mm"] - .72 * length))
    bottom_stations = [heel["bottom_z_mm"] - params["heel_sole_mm"],
                       front["bottom_z_mm"] - params["forefoot_sole_mm"]]
    bottom_y = np.interp(y, [heel["y_mm"], front["y_mm"]], bottom_stations)
    bottom = np.broadcast_to(bottom_y, top.shape).copy()
    # Preserve at least the requested forefoot/heel minima under any local dip.
    if float((top - bottom)[footprint < 0].min()) < 4:
        raise ValueError("局部鞋底厚度不足，请增加底厚。")
    return {"outline_distance": footprint, "top": top, "bottom": bottom,
            "ray_hit": valid, "anchors": {"heel": heel, "forefoot": front},
            "bottom_anchor_z_mm": bottom_stations}


def solid_sole_field(maps, z, params):
    radius = np.minimum(params["edge_radius_mm"], (maps["top"] - maps["bottom"]) * .45)
    side = maps["outline_distance"] - params["outsole_flare_mm"] + radius
    mid = (maps["top"] + maps["bottom"]) * .5
    half = (maps["top"] - maps["bottom"]) * .5
    vertical = np.abs(z[None, None, :] - mid[:, :, None]) - half[:, :, None] + radius[:, :, None]
    lateral = side[:, :, None]
    return (np.sqrt(np.maximum(lateral, 0) ** 2 + np.maximum(vertical, 0) ** 2)
            + np.minimum(np.maximum(lateral, vertical), 0) - radius[:, :, None]).astype(np.float32)
