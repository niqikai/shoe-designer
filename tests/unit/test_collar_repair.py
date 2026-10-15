"""Collar acceptance probes use actual CAD curves and solids, not declared metadata.

These tests exercise the cutter against an analytic rounded foot envelope. The
licensed last still requires its separate artifact/integration acceptance.
"""
from pathlib import Path

import numpy as np
import pytest
from build123d import Compound, Edge, GeomType, Plane, Solid, Wire
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section

from shoe_poc.geometry.upper import _collar_tool
from shoe_poc.schemas import ShoeSpec


def _capsule(radius):
    body = Solid.make_cylinder(radius, 160, Plane(origin=(40, 0, 40), z_dir=(1, 0, 0)))
    return body.fuse(Solid.make_sphere(radius, Plane(origin=(40, 0, 40))),
                     Solid.make_sphere(radius, Plane(origin=(200, 0, 40)))).clean()


@pytest.fixture(scope="module")
def cad_foot():
    # Exact spherical/cylindrical surfaces joined tangentially. Known radii give
    # genuine cavity/outer normal offsets without exercising the envelope builder.
    return _capsule(40), _capsule(41), _capsule(43.5)


@pytest.fixture
def spec():
    result = ShoeSpec.load(Path(__file__).resolve().parents[2] / "input/specs/shoe_spec.json")
    result.upper.opening_parameters.collar_height_mm = 45
    result.upper.opening_parameters.transition_mm = 60
    return result


def _rim(outer, opening):
    section = BRepAlgoAPI_Section(outer.wrapped, opening.wrapped, False)
    section.Approximation(True)
    section.Build()
    assert section.IsDone(), "Actual exterior/cutter B-Rep section failed"
    shape = Compound(section.Shape())
    assert shape.edges(), "No actual entry rim was cut"
    return shape, Wire.combine(shape.edges(), tol=1e-5)


def _assert_one_smooth_entry_rim(outer, opening):
    shape, wires = _rim(outer, opening)
    assert len(wires) == 1, f"Expected exactly one entry loop, observed {len(wires)}"
    assert wires[0].is_closed, "Entry rim has an open boundary"
    edges = list(shape.edges())
    # CAD face seams subdivide a smooth spatial loop. Check actual tangent
    # agreement at every subdivision, rather than checking Bezier control data.
    for index, edge in enumerate(edges):
        for t in (0.0, 1.0):
            point = np.asarray(tuple(edge.position_at(t)))
            matches = [(other, endpoint) for j, other in enumerate(edges) if j != index
                       for endpoint in (0.0, 1.0)
                       if np.linalg.norm(point-np.asarray(tuple(other.position_at(endpoint)))) <= 1e-4]
            # A single periodic edge can itself close the entire rim.
            if len(edges) == 1:
                matches = [(edge, 1-t)]
            assert len(matches) == 1, "Rim branch or unmatched CAD endpoint"
            other, endpoint = matches[0]
            tangent = np.asarray(tuple(edge.tangent_at(t)))
            other_tangent = np.asarray(tuple(other.tangent_at(endpoint)))
            angle = np.degrees(np.arccos(np.clip(abs(np.dot(tangent, other_tangent)), 0, 1)))
            assert angle <= 0.5, f"Actual rim has a {angle:.3f} degree tangent break"
    return shape


def _roof_height(opening, x):
    # Independently evaluate the cutter boundary with a vertical CAD line.
    common = opening.intersect(Edge.make_line((x, 0, 0), (x, 0, 150)))
    assert common is not None and common.edges()
    return min(vertex.center().Z for vertex in common.vertices())


def _termination_contact_area(upper, opening, x_front):
    area = 0.0
    for face in opening.faces():
        if face.geom_type != GeomType.PLANE:
            continue
        if abs(face.center().X-x_front) > 1e-4 or abs(face.normal_at().X) < 0.999:
            continue
        common = upper.intersect(face)
        if common is not None:
            area += sum(part.area for part in common.faces())
    return area


def test_actual_rim_closed_smooth_and_only_one_entry(cad_foot, spec):
    last, cavity, outer = cad_foot
    opening, _ = _collar_tool(last, outer, spec)
    assert opening.is_valid and len(opening.solids()) == 1
    _assert_one_smooth_entry_rim(outer, opening)
    entry = cavity.intersect(opening)
    assert entry.is_valid and len(entry.solids()) == 1 and entry.volume > 1
    upper = outer.cut(cavity, opening)
    assert upper.is_valid and len(upper.solids()) == 1
    common = upper.intersect(last)
    assert common is None or sum(s.volume for s in common.solids()) <= 0.01


def test_actual_cad_roof_joins_rear_height_and_reduces_steep_lip(cad_foot, spec):
    last, _, outer = cad_foot
    opening, _ = _collar_tool(last, outer, spec)
    front = last.bounding_box().min.X + last.bounding_box().size.X*spec.upper.opening_parameters.opening_end_x_fraction
    start = front-spec.upper.opening_parameters.transition_mm
    step = 0.01
    rear = _roof_height(opening, start-1)
    assert rear == pytest.approx(45, abs=1e-5)
    assert abs((_roof_height(opening, start+step)-_roof_height(opening, start))/step) < 1e-3
    assert abs((_roof_height(opening, start+2*step)-2*_roof_height(opening, start+step)
                +_roof_height(opening, start))/step**2) < 1e-3
    xs = np.linspace(start+0.1, front-0.1, 41)
    heights = np.asarray([_roof_height(opening, x) for x in xs])
    assert np.all(np.diff(heights) >= -1e-5)
    # Experimental shape regression guard: this analytic foot previously rose
    # almost 40 mm in 15 mm with slope >4. No manufacturing/fit claim follows.
    assert np.max(np.abs(np.diff(heights)/np.diff(xs))) < 2.0


def test_front_entry_position_matches_config_and_heel_stays_fixed(cad_foot, spec):
    last, _, outer = cad_foot
    long_opening, _ = _collar_tool(last, outer, spec)
    long_rim = _assert_one_smooth_entry_rim(outer, long_opening)
    short_spec = spec.model_copy(deep=True)
    short_spec.upper.opening_parameters.transition_mm = 15
    short_opening, _ = _collar_tool(last, outer, short_spec)
    short_rim, _ = _rim(outer, short_opening)
    front = last.bounding_box().min.X + last.bounding_box().size.X*spec.upper.opening_parameters.opening_end_x_fraction
    # Actual geometric front, not metadata's requested front, must remain fixed.
    assert long_rim.bounding_box().max.X == pytest.approx(front, abs=0.25)
    assert short_rim.bounding_box().max.X == pytest.approx(front, abs=0.25)
    assert long_rim.bounding_box().min.X == pytest.approx(short_rim.bounding_box().min.X, abs=1e-4)
    for x in (10, 30, 60):
        assert _roof_height(long_opening, x) == pytest.approx(_roof_height(short_opening, x), abs=1e-4)


def test_no_vertical_front_termination_intersects_actual_upper(cad_foot, spec):
    last, cavity, outer = cad_foot
    opening, _ = _collar_tool(last, outer, spec)
    upper = outer.cut(cavity, opening)
    front = last.bounding_box().min.X + last.bounding_box().size.X*spec.upper.opening_parameters.opening_end_x_fraction
    assert _termination_contact_area(upper, opening, front) <= 0.01


def test_negative_two_actual_entry_windows_are_rejected(cad_foot):
    _, _, outer = cad_foot
    windows = [Solid.make_box(20, 120, 70, Plane(origin=(x, -60, 40))) for x in (65, 135)]
    opening = Compound(windows)
    _, wires = _rim(outer, opening)
    assert len(wires) == 2
    with pytest.raises(AssertionError, match="exactly one"):
        _assert_one_smooth_entry_rim(outer, opening)


def test_negative_vertical_cut_has_real_corner_and_exposed_front_wall(cad_foot):
    _, cavity, outer = cad_foot
    opening = Solid.make_box(146, 120, 70, Plane(origin=(-14, -60, 45)))
    upper = outer.cut(cavity, opening)
    assert _termination_contact_area(upper, opening, 132) > 1
    with pytest.raises(AssertionError, match="tangent break"):
        _assert_one_smooth_entry_rim(outer, opening)


def test_runtime_metrics_measure_real_single_loop_and_keep_unknowns(cad_foot, spec):
    from shoe_poc.geometry.upper import _collar_rim_metrics
    last, _, outer = cad_foot
    opening, _ = _collar_tool(last, outer, spec)
    metrics = _collar_rim_metrics(outer, opening)
    assert metrics["one_closed_entry_loop"] is True
    assert metrics["entry_loop_count"] == metrics["closed_loop_count"] == 1
    assert metrics["front_x_mm"] == pytest.approx(132, abs=0.25)
    assert metrics["maximum_tangent_discontinuity_degrees"] <= 0.5
    assert metrics["measured_tangent_joints"] > 0
    assert metrics["unmeasurable_tangent_joints"] == []
    assert metrics["global_curvature_continuity_status"] == "NOT_VERIFIED"
    assert metrics["visual_acceptance_status"] == "NOT_VERIFIED"


def test_runtime_metrics_detect_real_multiple_windows_and_corners(cad_foot):
    from shoe_poc.geometry.upper import _collar_rim_metrics
    _, _, outer = cad_foot
    windows = [Solid.make_box(20, 120, 70, Plane(origin=(x, -60, 40))) for x in (65, 135)]
    metrics = _collar_rim_metrics(outer, Compound(windows))
    assert metrics["entry_loop_count"] == 2
    assert metrics["one_closed_entry_loop"] is False
    assert metrics["maximum_tangent_discontinuity_degrees"] > 45


def _provide_analytic_cad_envelope(monkeypatch, cad_foot):
    """Supply known exact offset bodies; leave all runtime measurements real."""
    from shoe_poc.geometry import envelope
    _, cavity, outer = cad_foot
    # The synthetic capsule has no ankle partition. Place the guard above it so
    # this fixture isolates actual collar checks, rather than last-adapter logic.
    monkeypatch.setattr(envelope, 'section_envelopes',
                        lambda source, clearance, thickness: (cavity, outer, {'patch_split_z_mm': 95}))


def test_upper_runtime_accepts_actual_smooth_single_entry(monkeypatch, cad_foot, spec):
    from shoe_poc.geometry.upper import UpperGenerator
    _provide_analytic_cad_envelope(monkeypatch, cad_foot)
    last, _, _ = cad_foot
    upper, cavity, opening, outer, _ = UpperGenerator().generate(last, spec)
    assert upper.is_valid and len(upper.solids()) == 1
    _assert_one_smooth_entry_rim(outer, opening)
    assert len(cavity.intersect(opening).solids()) == 1


def test_upper_runtime_stops_on_actual_rectangle_corner(monkeypatch, cad_foot, spec):
    from shoe_poc.geometry import upper as upper_module
    _provide_analytic_cad_envelope(monkeypatch, cad_foot)
    rectangle = Solid.make_box(146, 120, 70, Plane(origin=(-14, -60, 45)))
    monkeypatch.setattr(upper_module, '_collar_tool',
                        lambda last, outer, parameters: (rectangle, {'opening_x_range_mm': [-14, 132]}))
    with pytest.raises(ValueError, match='corner|tangent'):
        upper_module.UpperGenerator().generate(cad_foot[0], spec)


def test_upper_runtime_stops_on_actual_two_entry_windows(monkeypatch, cad_foot, spec):
    from shoe_poc.geometry import upper as upper_module
    _provide_analytic_cad_envelope(monkeypatch, cad_foot)
    windows = Compound([Solid.make_box(20, 120, 70, Plane(origin=(x, -60, 40)))
                        for x in (65, 135)])
    monkeypatch.setattr(upper_module, '_collar_tool',
                        lambda last, outer, parameters: (windows, {'opening_x_range_mm': [65, 155]}))
    with pytest.raises(ValueError, match='exactly one closed'):
        upper_module.UpperGenerator().generate(cad_foot[0], spec)
