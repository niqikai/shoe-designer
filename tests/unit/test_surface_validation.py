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
