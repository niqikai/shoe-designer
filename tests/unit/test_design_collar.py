"""Independent native-CAD probes of the explicit sculpted collar branch."""
from pathlib import Path

import pytest
from build123d import Axis, Edge, GeomType, Plane, Solid

from shoe_poc.geometry.upper import _collar_tool, _collar_rim_metrics
from shoe_poc.schemas import ShoeSpec


def _capsule(radius):
    return Solid.make_cylinder(radius, 160, Plane(origin=(40, 0, 40), z_dir=(1, 0, 0))).fuse(
        Solid.make_sphere(radius, Plane(origin=(40, 0, 40))),
        Solid.make_sphere(radius, Plane(origin=(200, 0, 40)))).clean()


@pytest.fixture(scope='module')
def reference_foot():
    # A descending analytic vamp makes the intended front-slope case explicit.
    # Exact analytic offsets retain their identity through the same rigid pose.
    shapes = [_capsule(radius).rotate(Axis.Y, 8.) for radius in (40, 41, 43.5)]
    shift = -shapes[0].bounding_box().min.Z
    return tuple(shape.translate((0, 0, shift)) for shape in shapes)


@pytest.fixture
def sculpted_spec():
    spec = ShoeSpec.load(Path(__file__).resolve().parents[2]/'input/specs/shoe_spec.json')
    opening = spec.upper.opening_parameters
    opening.collar_style = 'sculpted_3d'
    opening.opening_end_x_fraction = .64
    opening.collar_height_mm = 43.
    opening.heel_lift_mm = 2.
    opening.side_dip_mm = 5.
    return spec


def _actual_roof_height(opening, x, y):
    crossing = opening.intersect(Edge.make_line((x, y, -50), (x, y, 150)))
    assert crossing is not None and crossing.edges()
    return min(vertex.center().Z for vertex in crossing.vertices())


def test_saddle_is_actual_transverse_cad_geometry(reference_foot, sculpted_spec):
    last, _, outer = reference_foot
    opening, _ = _collar_tool(last, outer, sculpted_spec)
    flat_spec = sculpted_spec.model_copy(deep=True)
    flat_spec.upper.opening_parameters.side_dip_mm = 0.
    flat, _ = _collar_tool(last, outer, flat_spec)
    for x in (50., 75.):
        assert _actual_roof_height(opening, x, 0) == pytest.approx(
            _actual_roof_height(flat, x, 0), abs=1e-6)
        for y in (-20., 20.):
            assert _actual_roof_height(opening, x, y) < _actual_roof_height(flat, x, y)-1.
    # The heel-to-waist slope is present in the real solid, rather than metadata.
    assert _actual_roof_height(opening, 20, 0) > _actual_roof_height(opening, 80, 0)+1.


def test_actual_sculpted_entry_one_loop_without_tangent_corners(reference_foot, sculpted_spec):
    last, cavity, outer = reference_foot
    opening, _ = _collar_tool(last, outer, sculpted_spec)
    assert opening.is_valid and len(opening.solids()) == 1
    rim = _collar_rim_metrics(outer, opening)
    assert rim['one_closed_entry_loop']
    assert not rim['unmeasurable_tangent_joints']
    assert rim['maximum_tangent_discontinuity_degrees'] < .5
    expected_front = last.bounding_box().min.X+.64*last.bounding_box().size.X
    assert rim['front_x_mm'] == pytest.approx(expected_front, abs=.25)
    entry = cavity.intersect(opening)
    assert entry.is_valid and len(entry.solids()) == 1 and entry.volume > 1


def test_sculpted_cutter_preserves_source_exclusion_and_hides_termination(reference_foot, sculpted_spec):
    last, cavity, outer = reference_foot
    opening, metadata = _collar_tool(last, outer, sculpted_spec)
    upper = outer.cut(cavity, opening).clean()
    assert upper.is_valid and len(upper.solids()) == 1
    common = upper.intersect(last)
    assert common is None or sum(s.volume for s in common.solids()) <= .01
    front = metadata['opening_x_range_mm'][1]
    contact_area = 0.
    for face in opening.faces():
        if face.geom_type != GeomType.PLANE or abs(face.center().X-front) > 1e-5:
            continue
        common = upper.intersect(face)
        if common is not None:
            contact_area += sum(f.area for f in common.faces())
    assert contact_area <= .01
