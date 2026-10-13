"""Negative probes use real OCCT solids; no generated metadata supplies results."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Plane, Solid, Wire

from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.surface import validate_surface_samples


@pytest.fixture
def spec():
    return ShoeSpec.load(Path(__file__).resolve().parents[2] / "input/specs/shoe_spec.json")


def box_at(x, y, z, dx, dy, dz):
    return Solid.make_box(dx, dy, dz, Plane(origin=(x, y, z)))


def actual_shell(clearance=1.0, thickness=2.5, blocked=False):
    last = Solid.make_box(30, 20, 30)
    cavity = box_at(-clearance, -clearance, -clearance,
                    30+2*clearance, 20+2*clearance, 30+2*clearance)
    offset = clearance+thickness
    outer = box_at(-offset, -offset, -offset, 30+2*offset, 20+2*offset, 30+2*offset)
    opening = box_at(-10, -10, 20, 50, 40, 30)
    shape = outer.cut(cavity) if blocked else outer.cut(cavity, opening)
    return last, SimpleNamespace(shape=shape, opening=opening, cavity=cavity,
                                      sole=box_at(-4, -4, -4, 38, 28, 4), metadata={})


def check_by_name(report, name):
    return next(check for check in report["checks"] if check["name"] == name)


def test_exact_brep_directional_measurements_and_explicit_global_unknown(spec):
    last, build = actual_shell()
    report = validate_surface_samples(last, build, spec)
    assert report["geometry_status"] == "PASS"
    assert report["overall_status"] == "PARTIAL"
    assert report["sampled_clearance_mm"]["minimum_mm"] == pytest.approx(1.0, abs=1e-6)
    assert report["sampled_thickness_mm"]["minimum_mm"] == pytest.approx(2.5, abs=1e-6)
    assert report["unmeasurable_regions"] == []
    assert {s["reason"] for s in report["excluded_samples"]} >= {"opening_removed_surface_or_rim", "sole_connection"}
    assert check_by_name(report, "global_minimum_wall_thickness")["status"] == "NOT_VERIFIED"
    assert check_by_name(report, "user_visual_acceptance")["status"] == "NOT_VERIFIED"
    json.dumps(report, allow_nan=False)


def test_real_offset_error_is_measured_not_read_from_metadata(spec):
    last, build = actual_shell(clearance=2.0)
    build.metadata = {"clearance_mm": 1.0, "nominal_thickness_mm": 2.5, "topology_valid": True}
    report = validate_surface_samples(last, build, spec)
    assert report["geometry_status"] == "FAIL"
    assert check_by_name(report, "sampled_normal_clearance")["status"] == "FAIL"
    assert report["sampled_clearance_mm"]["maximum_absolute_deviation_mm"] == pytest.approx(1)


def test_actual_thin_sidewall_is_detected(spec):
    last, build = actual_shell()
    build.shape = build.shape.cut(box_at(32.0, -10, 0, 10, 40, 40))
    assert build.shape.is_valid
    report = validate_surface_samples(last, build, spec)
    assert check_by_name(report, "sampled_directional_thickness")["status"] == "FAIL"
    assert report["sampled_thickness_mm"]["minimum_mm"] == pytest.approx(1.0)


def test_actual_capped_collar_is_detected(spec):
    last, build = actual_shell(blocked=True)
    report = validate_surface_samples(last, build, spec)
    assert report["geometry_status"] == "FAIL"
    assert check_by_name(report, "opening_cutter_unobstructed")["value"] > 1
    assert check_by_name(report, "opening_cutter_unobstructed")["status"] == "FAIL"


def test_self_intersecting_loft_fails_topology_before_sampling(spec):
    last, build = actual_shell()
    # Bow-tie profiles loft into crossing walls: an actual self-intersecting B-Rep.
    wires = [Wire.make_polygon([(0, 0, z), (10, 10, z), (0, 10, z), (10, 0, z)])
             for z in (0, 10)]
    build.shape = Solid.make_loft(wires)
    assert not build.shape.is_valid
    report = validate_surface_samples(last, build, spec)
    assert report["geometry_status"] == "FAIL"
    assert check_by_name(report, "valid_single_solid_breps")["status"] == "FAIL"
    assert not report["samples"]


def test_unmeasurable_material_gap_cannot_become_pass(spec):
    last, build = actual_shell()
    # Remove a complete sidewall patch at selected UV sites, away from the collar.
    build.shape = build.shape.cut(box_at(30, -5, 8, 10, 30, 8))
    report = validate_surface_samples(last, build, spec)
    assert report["unmeasurable_regions"]
    assert check_by_name(report, "planned_surface_probe_coverage")["status"] == "NOT_VERIFIED"
    assert report["geometry_status"] != "PASS"


def test_zero_clearance_contact_supported_without_claiming_minimum(spec):
    spec.upper.clearance_mm = 0
    last, build = actual_shell(clearance=0)
    report = validate_surface_samples(last, build, spec)
    assert report["sampled_clearance_mm"]["minimum_mm"] == pytest.approx(0, abs=1e-5)
    assert check_by_name(report, "sampled_normal_clearance")["status"] == "PASS"


def test_raised_actual_sole_junction_is_excluded_without_hiding_upper_thin_region(spec):
    last, build = actual_shell()
    raised_sole = box_at(31, -3.5, 0, 7, 27, 11)
    build.sole = build.sole.fuse(raised_sole)
    build.shape = build.shape.fuse(raised_sole)
    report = validate_surface_samples(last, build, spec)
    assert any(s["reason"] == "actual_sole_or_junction_material_interval"
               for s in report["excluded_samples"])
    assert check_by_name(report, "sampled_directional_thickness")["status"] == "PASS"
    # The independent thin region is on the other side and above the actual sole.
    build.shape = build.shape.cut(box_at(-10, -5, 12, 8, 30, 7))
    report = validate_surface_samples(last, build, spec)
    assert check_by_name(report, "sampled_directional_thickness")["status"] == "FAIL"


def test_distance_solid_membership_is_preserved_by_compound_wrapper():
    from build123d import Compound
    from shoe_poc.validation.surface import _distance_to_shape
    solid = Solid.make_box(10, 10, 10)
    compound = Compound([solid])
    assert _distance_to_shape(solid, (5, 5, 5)) == 0
    assert _distance_to_shape(compound, (5, 5, 5)) == 0
    assert _distance_to_shape(solid, (15, 5, 5)) == pytest.approx(5)
    assert _distance_to_shape(compound, (15, 5, 5)) == pytest.approx(5)
    members = Compound([solid, box_at(20, 0, 0, 10, 10, 10)])
    assert _distance_to_shape(members, (25, 5, 5)) == 0
    # Surface-only queries must still use geometric distance, not classify a
    # face as though it were a closed solid.
    top_face = next(face for face in solid.faces() if face.normal_at().Z > 0.99)
    assert _distance_to_shape(top_face, (5, 5, 15)) == pytest.approx(5)


def test_actual_raised_sole_compound_excludes_material_without_hiding_thin_upper(spec):
    from build123d import Compound
    last, build = actual_shell()
    raised = box_at(31, -3.5, 0, 7, 27, 11)
    build.sole = Compound([build.sole.fuse(raised)])
    build.shape = build.shape.fuse(raised)
    report = validate_surface_samples(last, build, spec)
    assert any(s['reason'] == 'actual_sole_or_junction_material_interval'
               for s in report['excluded_samples'])
    assert check_by_name(report, 'sampled_directional_thickness')['status'] == 'PASS'
    build.shape = build.shape.cut(box_at(-10, -5, 12, 8, 30, 7))
    report = validate_surface_samples(last, build, spec)
    assert check_by_name(report, 'sampled_directional_thickness')['status'] == 'FAIL'


def test_actual_opening_compound_wrapper_preserves_exclusions(spec):
    from build123d import Compound
    last, build = actual_shell()
    original = validate_surface_samples(last, build, spec)
    build.opening = Compound([build.opening])
    wrapped = validate_surface_samples(last, build, spec)
    assert wrapped['geometry_status'] == original['geometry_status'] == 'PASS'
    assert len(wrapped['samples']) == len(original['samples'])
    assert len(wrapped['excluded_samples']) == len(original['excluded_samples'])
    assert wrapped['unmeasurable_regions'] == []


def test_reused_native_classifier_refreshes_inside_on_and_cavity_states():
    from shoe_poc.validation.surface import _LoadedSolidClassifier, _inside
    # A genuine sealed void exercises OUT inside the outer bounding box, and
    # alternating queries ensure a previous IN/ON result cannot leak forward.
    solid = Solid.make_box(10, 10, 10).cut(box_at(3, 3, 3, 4, 4, 4))
    assert solid.is_valid and len(solid.solids()) == 1
    classifier = _LoadedSolidClassifier(solid)
    queries = [((1, 5, 5), True), ((5, 5, 5), False),
               ((0, 5, 5), True), ((15, 5, 5), False),
               ((3, 5, 5), True), ((1, 5, 5), True)]
    for _ in range(3):
        for point, expected in queries:
            assert classifier.inside(point) is expected
            assert classifier.inside(point) == _inside(solid, point)


def test_cached_solid_members_preserve_exact_compound_distance_queries():
    from build123d import Compound
    from shoe_poc.validation.surface import _LoadedSolidClassifier, _distance_to_shape
    hollow = Solid.make_box(10, 10, 10).cut(box_at(3, 3, 3, 4, 4, 4))
    shape = Compound([hollow, box_at(20, 0, 0, 10, 10, 10)])
    classifiers = tuple(_LoadedSolidClassifier(solid) for solid in shape.solids())
    for _ in range(2):
        for point, expected in [((25, 5, 5), 0), ((15, 5, 5), 5),
                                ((5, 5, 5), 2), ((3, 5, 5), 0), ((1, 5, 5), 0)]:
            assert _distance_to_shape(shape, point, classifiers) == pytest.approx(expected)
            assert _distance_to_shape(shape, point, classifiers) == pytest.approx(
                _distance_to_shape(shape, point))
