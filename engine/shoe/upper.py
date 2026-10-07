"""Outward offset shell with a real open collar and a sealed rim."""
import numpy as np
from mathutils import Vector


def upper_field(tree, x, y, z, thickness, height):
    spacing = float(z[1] - z[0])
    band = thickness + spacing * 2
    field = np.full((len(x), len(y), len(z)), band, dtype=np.float32)
    query_count = 0
    active_z = np.flatnonzero((z >= -band) & (z <= height + spacing * 2))
    for i, vx in enumerate(x):
        for j, vy in enumerate(y):
            for k in active_z:
                point = Vector((float(vx), float(vy), float(z[k])))
                nearest, normal, _, distance = tree.find_nearest(point, band)
                if nearest is None:
                    continue
                signed = distance if (point - nearest).dot(normal) >= 0 else -distance
                field[i, j, k] = max(signed - thickness, -signed, float(z[k]) - height)
                query_count += 1
    return field, {"target_thickness_mm": thickness, "collar_height_mm": height,
                   "near_surface_queries": query_count,
                   "method": "outward nearest-surface offset; hollow interior; horizontal collar clip"}
