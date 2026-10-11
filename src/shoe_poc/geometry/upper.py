"""Upper offset and collar opening, using the imported last as design surface."""


class UpperGenerator:
    def generate(self, last_shape, spec):
        from build123d import Solid, Plane
        clearance = spec.upper.clearance_mm
        thickness = spec.upper.thickness_mm
        source = last_shape.solids()[0]
        from .envelope import section_envelopes
        cavity, outer, envelope_metadata = section_envelopes(source, clearance, thickness)
        last_outside_cavity = sum(s.volume for s in source.cut(cavity).solids())
        cavity_outside_outer = sum(s.volume for s in cavity.cut(outer).solids())
        last_outside_outer = sum(s.volume for s in source.cut(outer).solids())
        if max(last_outside_cavity, cavity_outside_outer, last_outside_outer) > 0.01:
            raise ValueError(f'Envelope containment failed: last outside cavity={last_outside_cavity}, cavity outside outer={cavity_outside_outer}, last outside outer={last_outside_outer} mm3')
        if not cavity.is_valid or not outer.is_valid:
            raise ValueError('Real-last offset produced invalid geometry')
        box = last_shape.bounding_box()
        opening_spec = spec.upper.opening_parameters
        x0 = box.min.X + box.size.X * opening_spec.opening_start_x_fraction
        x1 = box.min.X + box.size.X * opening_spec.opening_end_x_fraction
        pad = clearance + thickness + 10
        if opening_spec.opening_start_x_fraction == 0:
            x0 -= pad
        opening = Solid.make_box(x1-x0, box.size.Y+2*pad, box.size.Z+pad,
                                 Plane(origin=(x0, box.min.Y-pad, box.min.Z+opening_spec.collar_height_mm)))
        upper = outer.cut(cavity, opening)
        return upper, cavity, opening, outer, {
            'method': 'Real-last sampled section lofts and Boolean cavity/collar subtraction',
            'envelope': envelope_metadata,
            'last_outside_cavity_volume_mm3': last_outside_cavity,
            'cavity_outside_outer_volume_mm3': cavity_outside_outer,
            'last_outside_outer_volume_mm3': last_outside_outer,
            'nominal_thickness_mm': thickness, 'clearance_mm': clearance,
            'collar_height_above_last_base_mm': opening_spec.collar_height_mm,
            'opening_x_range_mm': [x0,x1],
            'minimum_wall_thickness_status': 'NOT_VERIFIED',
        }
