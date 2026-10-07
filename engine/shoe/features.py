"""Measure features from imported, normalized geometry; never author its vertices."""
import numpy as np
from mathutils import Vector

from engine.validate import coordinates, surface_tree, triangles


def plantar_contour(mesh, threshold=-0.35):
    """Trace a smooth vertex-normal level set on the existing closed surface.

    This measures a curve, not a new mesh. Triangle-edge crossings remain degree
    two even where a binary face selection would touch itself at a vertex.
    """
    points = coordinates(mesh)
    normals = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("normal", normals)
    values = normals.reshape((-1, 3))[:, 2].copy()
    values[np.abs(values - threshold) < 1e-10] += 1e-9
    positions, adjacency = {}, {}
    for face in triangles(mesh):
        crossings = []
        for i in range(3):
            a, b = int(face[i]), int(face[(i + 1) % 3])
            if (values[a] < threshold) == (values[b] < threshold):
                continue
            key = tuple(sorted((a, b)))
            if key not in positions:
                first, second = key
                ratio = (threshold - values[first]) / (values[second] - values[first])
                positions[key] = points[first] + ratio * (points[second] - points[first])
            crossings.append(key)
        if len(crossings) == 2:
            a, b = crossings
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)
        elif crossings:
            raise ValueError("楦底曲面等值线出现异常交点。")
    if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise ValueError("楦底曲面等值线不闭合。")
    remaining = set(adjacency)
    loops = []
    while remaining:
        start = min(remaining)
        current, previous, loop = start, None, []
        while True:
            remaining.remove(current)
            loop.append(positions[current])
            next_key = next(key for key in adjacency[current] if key != previous)
            previous, current = current, next_key
            if current == start:
                break
        loops.append(np.array(loop))
    def area(loop):
        return float(np.sum(loop[:, 0] * np.roll(loop[:, 1], -1) - np.roll(loop[:, 0], -1) * loop[:, 1]) * 0.5)
    loops.sort(key=lambda loop: abs(area(loop)), reverse=True)
    main = loops[0]
    if area(main) < 0:
        main = main[::-1]
    main = np.roll(main, -int(main[:, 1].argmin()), axis=0)
    outline = main.tolist()
    outline.append(outline[0])
    return outline, {"normal_z_threshold": threshold, "boundary_loops": len(loops),
                     "projected_area_mm2": abs(area(main)),
                     "method": "largest closed level set of interpolated vertex normal Z on existing surface, counterclockwise from heel",
                     "limitation": "curved sole/sidewall transition is defined by normal threshold; not an authored anatomical seam"}


def extract_features(mesh, normalization):
    points = coordinates(mesh)
    minimum, maximum = points.min(axis=0), points.max(axis=0)
    length = float(maximum[1] - minimum[1])
    edge_indices = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edge_indices)
    edges = points[edge_indices.reshape((-1, 2))]
    dy = edges[:, 1, 1] - edges[:, 0, 1]
    tree = surface_tree(mesh)

    def section(y):
        selected = ((edges[:, :, 1].min(axis=1) <= y) & (edges[:, :, 1].max(axis=1) >= y) & (np.abs(dy) > 1e-9))
        crossed = edges[selected]
        if not len(crossed):
            return None
        t = (y - crossed[:, 0, 1]) / dy[selected]
        x = crossed[:, 0, 0] + t * (crossed[:, 1, 0] - crossed[:, 0, 0])
        return float(x.min()), float(x.max())

    def cast(x, y, *, upper=False):
        z = float(maximum[2] + length) if upper else float(minimum[2] - length)
        direction = Vector((0, 0, -1 if upper else 1))
        position, normal, _, _ = tree.ray_cast(Vector((x, y, z)), direction, float(3 * length))
        return (list(position), list(normal)) if position is not None else (None, None)

    outline, bottom_info = plantar_contour(mesh)

    widths = []
    centerline = []
    for y in np.arange(float(minimum[1]) + 0.5, float(maximum[1]), 1.0):
        bounds = section(float(y))
        if bounds is None:
            continue
        left, right = bounds
        widths.append({"y_mm": float(y), "negative_x_mm": left, "positive_x_mm": right, "width_mm": right - left})
        x = (left + right) * 0.5
        bottom, _ = cast(x, float(y))
        top, _ = cast(x, float(y), upper=True)
        if bottom and top:
            centerline.append({"x_mm": x, "y_mm": float(y), "bottom_z_mm": bottom[2], "top_z_mm": top[2]})
    widest = max(widths, key=lambda row: row["width_mm"])
    heel = min(widths, key=lambda row: abs(row["y_mm"] - length * 0.18))
    arch_section = min(widths, key=lambda row: abs(row["y_mm"] - length * 0.40))
    relative = (arch_section["y_mm"] - heel["y_mm"]) / (widest["y_mm"] - heel["y_mm"])
    negative_baseline = heel["negative_x_mm"] + relative * (widest["negative_x_mm"] - heel["negative_x_mm"])
    positive_baseline = heel["positive_x_mm"] + relative * (widest["positive_x_mm"] - heel["positive_x_mm"])
    negative_inset = arch_section["negative_x_mm"] - negative_baseline
    positive_inset = positive_baseline - arch_section["positive_x_mm"]
    toe_band_center_x = float(points[points[:, 1] > minimum[1] + 0.97 * length, 0].mean())
    if toe_band_center_x < -2 and negative_inset > positive_inset + 1:
        side = "right"
    elif toe_band_center_x > 2 and positive_inset > negative_inset + 1:
        side = "left"
    else:
        side = "unconfirmed"

    arch_samples = []
    for row in widths[::3]:
        if length * 0.30 <= row["y_mm"] <= length * 0.60:
            for fraction in np.linspace(0.15, 0.85, 11):
                x = row["negative_x_mm"] + fraction * row["width_mm"]
                position, normal = cast(x, row["y_mm"])
                if position and normal[2] < -0.35:
                    arch_samples.append(position)
    instep_samples = []
    for row in centerline:
        if 0.45 * length <= row["y_mm"] <= 0.65 * length:
            position, normal = cast(row["x_mm"], row["y_mm"], upper=True)
            if position and normal[2] > 0.3:
                instep_samples.append(position)
    if not arch_samples or not instep_samples:
        raise ValueError("未能在指定分区中识别足弓或脚背特征。")
    top_heel_bottom = min(centerline, key=lambda row: abs(row["y_mm"] - 0.15 * length))
    heel_tip_band = points[points[:, 1] < minimum[1] + 0.1]
    toe = points[int(points[:, 1].argmax())].tolist()
    return {
        "schema_version": 1, "unit": "mm", "normalization": normalization,
        "dimensions_mm": {"length": length, "bounding_width": float(maximum[0] - minimum[0]), "height": float(maximum[2] - minimum[2])},
        "heel_point_mm": [0.0, 0.0, 0.0], "heel_surface_rear_point_mm": heel_tip_band.mean(axis=0).tolist(),
        "toe_point_mm": toe, "widest_cross_section": widest,
        "heel_width_cross_section": heel, "heel_plantar_elevation_mm": top_heel_bottom["bottom_z_mm"],
        "plantar_outline_mm": outline, "plantar_outline_definition": bottom_info,
        "longitudinal_centerline": centerline, "width_profile": widths,
        "cross_section_sampling_mm": 1.0,
        "arch_highest_point_mm": max(arch_samples, key=lambda p: p[2]),
        "arch_definition": "highest downward ray hit in Y=30%-60% of length, lateral 15%-85%, normal Z < -0.35; grid 3 mm by 11 lateral samples",
        "instep_highest_point_mm": max(instep_samples, key=lambda p: p[2]),
        "instep_definition": "highest upper surface on cross-section midline at Y=45%-65% of length, excluding ankle cylinder; 1 mm sampling",
        "zones": {"heel": {"y_min_mm": 0.0, "y_max_mm": 0.30 * length},
                  "arch": {"y_min_mm": 0.30 * length, "y_max_mm": 0.60 * length},
                  "forefoot": {"y_min_mm": 0.60 * length, "y_max_mm": length},
                  "definition": "initial engineering partition at 30% / 60% length; not a pressure map"},
        "laterality": {"inferred": side, "confidence": "medium" if side != "unconfirmed" else "low",
                       "toe_band_center_x_mm": toe_band_center_x, "negative_x_arch_inset_mm": negative_inset,
                       "positive_x_arch_inset_mm": positive_inset,
                       "basis": "toe bias and greater medial arch inset; with toe +Y and up +Z, medial -X suggests right foot",
                       "requires_visual_confirmation": True},
        "size_estimate": {"label": "approximately EU 42-43; unconfirmed", "confidence": "low",
                          "assumed_toe_allowance_mm": [10, 15], "implied_foot_length_mm": [length - 15, length - 10],
                          "basis": "engineering allowance assumption plus adidas heel-toe size chart; last length is not foot length",
                          "reference_url": "https://www.adidas.com/us/help/size_charts/men-shoes",
                          "requires_fit_validation": True},
    }
