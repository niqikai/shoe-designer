"""One-piece upper and sole assembly; every build enforces cavity exclusion."""
from dataclasses import dataclass
import time
from .upper import UpperGenerator
from .sole import SoleGenerator


def volume_of(shape):
    if shape is None:
        return 0.0
    if isinstance(shape, (list, tuple)):
        return sum(volume_of(s) for s in shape)
    if shape.wrapped is None or shape.wrapped.IsNull():
        return 0.0
    return sum(s.volume for s in shape.solids())


@dataclass
class ShoeBuild:
    shape: object
    metadata: dict
    upper: object
    sole: object
    cavity: object
    opening: object


class ShoeAssembler:
    def build(self, last_shape, spec) -> ShoeBuild:
        start = time.monotonic()
        upper, cavity, opening, outer, upper_metadata = UpperGenerator().generate(last_shape, spec)
        sole_stock, sole_metadata = SoleGenerator().generate(last_shape, spec, outer=outer)
        sole = sole_stock.cut(cavity, last_shape, opening)
        if not sole.is_valid or len(sole.solids()) != 1 or sole.volume <= 0:
            raise ValueError('Cavity-trimmed sole is not a valid positive-volume single solid')
        connection_overlap = volume_of(upper.intersect(sole))
        if connection_overlap <= .01:
            raise ValueError('Upper and sole have no positive-volume material connection outside the cavity')
        # Fuse enclosing bodies before cavity subtraction to avoid coincident
        # seams between separately trimmed upper and sole.
        shoe = outer.fuse(sole_stock).cut(cavity, last_shape, opening).clean()
        if not shoe.is_valid or len(shoe.solids()) != 1 or shoe.volume <= 0:
            raise ValueError(f'Assembly is not a valid single solid (valid={shoe.is_valid}, solids={len(shoe.solids())})')
        last_overlap = volume_of(shoe.intersect(last_shape))
        cavity_overlap = volume_of(shoe.intersect(cavity))
        opening_overlap = volume_of(shoe.intersect(opening))
        tolerance_mm3 = 0.01
        if max(last_overlap, cavity_overlap, opening_overlap) > tolerance_mm3:
            raise ValueError(f'Cavity/opening exclusion failed: {last_overlap}, {cavity_overlap}, {opening_overlap} mm3')
        omitted_upper = volume_of(upper.cut(shoe))
        if omitted_upper > tolerance_mm3:
            raise ValueError(f'Assembly omitted original upper material: {omitted_upper} mm3')
        removed_at_opening = volume_of(outer.intersect(opening))
        cavity_entry_volume = volume_of(cavity.intersect(opening))
        if removed_at_opening <= 1 or cavity_entry_volume <= 1:
            raise ValueError('Configured collar does not intersect the upper; no entry opening was created')
        # A transverse CAD line through the forefoot must cross two nonzero
        # material intervals. This is local evidence, not a minimum-wall test.
        from build123d import Edge
        last_box = last_shape.bounding_box()
        probe_x = last_box.min.X + 0.70 * last_box.size.X
        probe_z = last_box.min.Z + 0.60 * spec.upper.opening_parameters.collar_height_mm
        probe_line = Edge.make_line((probe_x, last_box.min.Y-20, probe_z), (probe_x, last_box.max.Y+20, probe_z))
        probe_result = upper.intersect(probe_line)
        wall_intervals = [edge.length for edge in probe_result.edges()] if probe_result is not None else []
        if len(wall_intervals) < 2 or min(wall_intervals) <= 1e-5:
            raise ValueError('Upper sidewall probe did not find two nonzero material intervals; this last/template requires adaptation')
        from shoe_poc.validation.junction import validate_junction
        junction_validation = validate_junction(shoe, cavity, opening, last_box,
                                               maximum_z_mm=sole_stock.bounding_box().max.Z+1.)
        refined = (spec.upper.opening_parameters.collar_style == 'sculpted_3d'
                   or spec.sole.profile_parameters.sidewall_bulge_mm > 0)
        build = ShoeBuild(shoe, {
            'template': spec.shoe.template, 'upper': upper_metadata, 'sole': sole_metadata,
            'algorithm_version': ('exact-upper-sculpted-collar-periodic-rocker-sole-assembly-v8'
                                  if refined else 'exact-upper-periodic-rocker-sole-assembly-v7'),
            'junction': {
                'geometry_status': junction_validation['geometry_status'],
                'validation': junction_validation,
                'method': 'Closed circumferential band from actual exterior CAD hits and oriented normals; fuse stock/exterior and re-cut cavity/source/opening',
                'band_half_height_mm': sole_metadata['transition']['band_half_height_mm'],
                'join_inset_from_actual_outer_mm': sole_metadata['transition']['join_inset_mm'],
                'upper_guide_inset_from_actual_outer_mm': sole_metadata['transition']['upper_guide_inset_mm'],
                'maximum_stock_z_mm': sole_stock.bounding_box().max.Z,
                'native_subtractive_fillet_used': False,
                'upper_sole_material_overlap_volume_mm3': connection_overlap,
                'connection_scope': 'Positive material overlap and independent final CAD exterior normal samples; global tangent continuity is not certified',
            },
            'bbox_mm': {'min': list(shoe.bounding_box().min), 'max': list(shoe.bounding_box().max)},
            'face_count': len(shoe.faces()),
            'elapsed_seconds': time.monotonic()-start,
            'solid_count': len(shoe.solids()), 'topology_valid': shoe.is_valid,
            'last_intersection_volume_mm3': last_overlap,
            'cavity_intersection_volume_mm3': cavity_overlap,
            'opening_intersection_volume_mm3': opening_overlap,
            'original_upper_omitted_volume_mm3': omitted_upper,
            'opening_removed_envelope_volume_mm3': removed_at_opening,
            'cavity_entry_intersection_volume_mm3': cavity_entry_volume,
            'intersection_tolerance_mm3': tolerance_mm3,
            'upper_wall_probe': {'method': 'CAD common of upper and transverse Y line', 'x_mm': probe_x, 'z_mm': probe_z, 'material_interval_lengths_mm': wall_intervals, 'scope': 'Local directional thickness only; global minimum NOT_VERIFIED'},
            'upper_volume_mm3': volume_of(upper), 'sole_volume_mm3': volume_of(sole),
            'limitations': ['Uniform minimum wall thickness not measured.', 'Physical insertion, fit, comfort and material performance are not verified.', 'Finite exterior-junction normal samples cannot certify global tangent continuity; failed samples remain explicit.'],
        }, upper, sole, cavity, opening)
        # Private native guide for independent design validation. Keep the six
        # public fields and JSON metadata free of a unserializable CAD object.
        build._outer = outer
        return build
