"""Deterministic transverse support-envelope reconstruction from the actual last.

OCC direct offsets of the supplied 235-face asset fail. This fallback samples
its tessellation, takes convex support polygons of overlapping transverse slabs and builds nested ruled lofts. No reference dimensions or
replacement primitive geometry are used.
"""
import numpy as np


def section_envelopes(shape, clearance, thickness, section_count=36, radial_count=48):
    from build123d import Wire, Solid
    verts, faces = shape.tessellate(0.25, 0.1)
    vertices = np.asarray([tuple(v) for v in verts])
    triangles = vertices[np.asarray(faces)]
    box = shape.bounding_box()
    xs = np.linspace(box.min.X + 0.15, box.max.X - 0.15, section_count)
    rings_inner, rings_outer = [], []
    angles = np.linspace(0, 2*np.pi, radial_count, endpoint=False)
    directions = np.stack((np.cos(angles), np.sin(angles)), axis=1)
    # Sub-mm extra envelope margin covers tessellation error; exact last is
    # also subtracted by the assembler as a final hard cavity constraint.
    approximation_margin = 0.6
    for x in xs:
        # Both adjacent profiles cover the complete interval between them.
        # This support slab prevents the interpolated envelope cutting across
        # the highly curved toe/heel between sparse transverse planes.
        half_slab = (xs[1] - xs[0]) * 1.05
        candidates = triangles[(triangles[:, :, 0].min(axis=1) <= x+half_slab) & (triangles[:, :, 0].max(axis=1) >= x-half_slab)]
        points = candidates[:, :, 1:].reshape(-1, 2)
        if len(points) < 3:
            raise ValueError(f'Unable to reconstruct transverse last slab at X={x:.3f}')
        # A circumscribed fixed-normal polygon contains every support point.
        # Ray samples joined by chords would be inscribed and could slice off
        # corners on tall narrow sections, producing unintended perforations.
        support = (points @ directions.T).max(axis=0)
        for distance, rings in [(clearance + approximation_margin, rings_inner), (clearance+thickness+approximation_margin, rings_outer)]:
            bounds = support + distance
            yz = np.asarray([np.linalg.solve(np.stack((directions[i-1], directions[i])), [bounds[i-1], bounds[i]]) for i in range(radial_count)])
            rings.append(np.column_stack((np.full(radial_count,x),yz)))
    def loft(rings, margin):
        first, last = rings[0].copy(), rings[-1].copy()
        first[:,0] = box.min.X-margin
        last[:,0] = box.max.X+margin
        profiles = [first, *rings, last]
        wires = [Wire.make_polygon(r.tolist()) for r in profiles]
        result = Solid.make_loft(wires, ruled=True)
        if not result.is_valid or result.volume <= 0:
            raise ValueError('Transverse envelope loft is invalid')
        return result
    inner = loft(rings_inner, clearance+approximation_margin)
    outer = loft(rings_outer, clearance+thickness+approximation_margin)
    return inner, outer, {
        'method': 'Nested ruled lofts of overlapping convex transverse support slabs sampled from the real last',
        'section_count': section_count, 'support_slab_half_width_mm': float(half_slab), 'support_directions_per_section': radial_count,
        'tessellation_tolerance_mm': 0.25, 'additional_envelope_margin_mm': approximation_margin,
        'nominal_section_offset_difference_mm': thickness,
        'limitations': ['Section convexification bridges local concavities.', 'Ruled loft has faceted exterior.', 'Offsets are transverse-plane distances, not a certified global normal wall thickness.'],
    }
