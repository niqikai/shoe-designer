"""Solid outsole from the real last's sampled plan-view support envelope."""
import numpy as np
from scipy.spatial import ConvexHull


class SoleGenerator:
    def generate(self, last_shape, spec):
        from build123d import Face, Wire, Solid
        vertices, _ = last_shape.tessellate(0.4, 0.15)
        points = np.asarray([tuple(v)[:2] for v in vertices])
        hull = ConvexHull(points)
        polygon = points[hull.vertices]
        # Intersect consecutive outward-shifted supporting lines. The outline is
        # derived from the real last, while the deliberate convex support hull
        # bridges the medial arch and is an explicit template approximation.
        vectors = np.roll(polygon, -1, axis=0) - polygon
        normals = np.stack((vectors[:, 1], -vectors[:, 0]), axis=1)
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        margin = spec.sole.profile_parameters.margin_mm
        distances = np.sum(normals * polygon, axis=1) + margin
        outline = np.asarray([np.linalg.solve(np.stack((normals[i-1], normals[i])), [distances[i-1], distances[i]]) for i in range(len(polygon))])
        base_z = last_shape.bounding_box().min.Z
        bottom = base_z - spec.sole.height_mm
        # The stock reaches the underside of the rising heel/toe. The real
        # cavity is cut out after fusion, giving a contoured internal footbed.
        connection_height = min(18.0, last_shape.bounding_box().size.Z * 0.2)
        wire = Wire.make_polygon([(x, y, bottom) for x, y in outline])
        solid = Solid.extrude(Face(wire), (0, 0, spec.sole.height_mm + connection_height))
        return solid, {
            'method': 'Convex hull of tessellated real-last XY projection, outward miter offset, extrusion and real-cavity subtraction',
            'footprint_vertex_count': len(outline), 'sampling_tolerance_mm': 0.4,
            'margin_mm': margin, 'nominal_underfoot_height_mm': spec.sole.height_mm,
            'bottom_z_mm': bottom, 'stock_overlap_above_last_base_mm': connection_height,
        }
