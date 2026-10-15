"""Design descriptors use genuine solids, not declared generator results."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Compound, Plane, Solid, Wire

from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.design import _sections, validate_design_shape


@pytest.fixture
def spec():
    return ShoeSpec.load(Path(__file__).resolve().parents[2] / "input/specs/shoe_spec.json")


def shell(*, floor=-3.5, blocked=False, source_radius=20):
    last = Solid.make_cylinder(source_radius, 40)
    cavity = Solid.make_cylinder(source_radius+1, 42, Plane(origin=(0, 0, -1)))
    outer = Solid.make_cylinder(source_radius+3.5, 47, Plane(origin=(0, 0, floor)))
    opening = Solid.make_box(80, 80, 40, Plane(origin=(-40, -40, 30)))
    shape = outer.cut(cavity) if blocked else outer.cut(cavity, opening)
    sole = Solid.make_cylinder(source_radius+3.5, -floor, Plane(origin=(0, 0, floor))).cut(cavity)
    return last, SimpleNamespace(shape=shape, upper=shape, sole=sole, cavity=cavity,
                                 opening=opening, metadata={"visual_acceptance": "PASS"}), outer


def checked(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def test_actual_cad_underfoot_sections_and_no_visual_acceptance(spec):
    last, build, outer = shell()
    report = validate_design_shape(last, build, spec, outer_shape=outer)
    assert report["geometry_status"] == "PASS"
    assert len(report["underfoot"]["samples"]) > 0
    assert report["underfoot"]["measurement_errors"] == []
    assert report["underfoot"]["minimum_measured_directional_material_mm"] == pytest.approx(2.5, abs=1e-6)
    assert report["global_wall_minimum"] == "NOT_VERIFIED"
    assert report["rim"]["one_closed_entry_loop"]
    assert report["appearance_acceptance"] == report["user_acceptance"] == "PENDING"
    assert report["overall_status"] == "PARTIAL"
    assert report["source_proportions"]["status"] == "OBSERVED"
    json.dumps(report, allow_nan=False)


def test_actual_thin_footbed_fails_even_with_valid_solid(spec):
    last, build, outer = shell(floor=-2.)
    assert build.shape.is_valid
    report = validate_design_shape(last, build, spec, outer_shape=outer)
    assert report["geometry_status"] == "FAIL"
    thickness = checked(report, "sampled_underfoot_material_thickness")
    assert thickness["status"] == "FAIL"
    assert thickness["value"] == pytest.approx(1.)
    assert thickness["threshold"]["minimum_mm"] == pytest.approx(2.15)


def test_real_entry_cap_cannot_pass_from_water_tightness(spec):
    last, build, outer = shell(blocked=True)
    assert build.shape.is_valid and len(build.shape.solids()) == 1
    report = validate_design_shape(last, build, spec, outer_shape=outer)
    assert checked(report, "source_cavity_opening_exclusion")["status"] == "FAIL"
    assert report["geometry_status"] == "FAIL"


def test_one_connected_cutter_can_still_make_two_actual_entry_windows(spec):
    last, build, outer = shell()
    left = Solid.make_box(14, 60, 35, Plane(origin=(-20, -30, 20)))
    right = Solid.make_box(14, 60, 35, Plane(origin=(6, -30, 20)))
    bridge = Solid.make_box(40, 60, 5, Plane(origin=(-20, -30, 50)))
    build.opening = left.fuse(right, bridge)
    assert build.opening.is_valid and len(build.opening.solids()) == 1
    build.shape = outer.cut(build.cavity, build.opening)
    build.upper = build.shape
    assert build.shape.is_valid and len(build.shape.solids()) == 1
    report = validate_design_shape(last, build, spec, outer_shape=outer)
    entry = checked(report, "one_connected_cavity_entry")
    assert entry["value"]["solid_count"] == 2
    assert entry["status"] == "FAIL"
    assert report["geometry_status"] == "FAIL"


def test_source_shrinking_cannot_be_accepted_as_forefoot_refinement(spec):
    original, baseline, _ = shell()
    smaller, build, outer = shell(source_radius=18)
    report = validate_design_shape(smaller, build, spec, outer_shape=outer,
                                   baseline={"last_shape": original, "shape": baseline.shape})
    assert checked(report, "unchanged_source_last")["status"] == "FAIL"
    assert report["geometry_status"] == "FAIL"
    assert report["baseline_comparison"]["appearance_acceptance"] == "PENDING"


def test_true_self_crossing_fold_rejected_before_sampling(spec):
    last, build, outer = shell()
    wires = [Wire.make_polygon([(0, 0, z), (10, 10, z), (0, 10, z), (10, 0, z)]) for z in (0, 10)]
    build.shape = Solid.make_loft(wires)
    assert not build.shape.is_valid
    report = validate_design_shape(last, build, spec, outer_shape=outer)
    assert checked(report, "valid_single_solid_breps")["status"] == "FAIL"
    assert report["geometry_status"] == "FAIL"
    assert "underfoot" not in report


def test_small_real_cavity_shrink_is_not_hidden_inside_fit_tolerance(spec):
    last, build, outer = shell()
    old_cavity = build.cavity
    build.cavity = Solid.make_cylinder(20.8, 42, Plane(origin=(0, 0, -1)))
    build.shape = outer.cut(build.cavity, build.opening)
    build.upper = build.shape
    report = validate_design_shape(last, build, spec, outer_shape=outer,
                                   baseline={"last_shape": last, "cavity": old_cavity})
    assert checked(report, "unchanged_source_last")["status"] == "PASS"
    assert checked(report, "unchanged_clearance_cavity")["status"] == "FAIL"
    assert report["geometry_status"] == "FAIL"


def test_missing_actual_rim_evidence_is_explicit_partial(spec):
    last, build, _ = shell()
    report = validate_design_shape(last, build, spec)
    assert report["geometry_status"] == "PARTIAL"
    assert report["rim"]["status"] == "NOT_VERIFIED"
    assert any(r["region"] == "actual exterior rim" for r in report["unmeasurable_regions"])


def test_actual_floor_lift_and_width_changes_are_observations_not_approval(spec):
    last, original, _ = shell()
    _, raised, outer = shell(floor=-3.)
    report = validate_design_shape(last, raised, spec, outer_shape=outer,
                                   baseline={"shape": original.shape, "last_shape": last})
    floors = report["actual_rocker"]["floor_samples"]
    assert all(row["floor_z_mm"] == pytest.approx(-3.) for row in floors)
    assert checked(report, "unchanged_source_last")["status"] == "PASS"
    assert report["underfoot"]["minimum_measured_directional_material_mm"] == pytest.approx(2.)
    assert report["appearance_acceptance"] == "PENDING"


def test_actual_sharp_peak_is_reported_without_invented_aesthetic_pass(spec):
    last, build, outer = shell()
    # A native cone joins the circular rim. It is a real sharp protrusion, not a
    # metadata claim; its geometry also intrudes into the intended cutter space.
    spike = Solid.make_cone(4, 0, 20, Plane(origin=(22, 0, 29)))
    build.shape = build.shape.fuse(spike)
    build.upper = build.shape
    assert build.shape.is_valid
    report = validate_design_shape(last, build, spec, outer_shape=outer)
    assert report["shoe_peak_z_mm"] == pytest.approx(49., abs=1e-6)
    assert checked(report, "source_cavity_opening_exclusion")["status"] == "FAIL"
    assert report["surface_conditioning"]["global_self_intersection"] == "NOT_VERIFIED"
    assert report["appearance_acceptance"] == "PENDING"


def test_real_adjacent_section_seam_and_real_material_gap_are_distinguished():
    first = Solid.make_box(10, 10, 10)
    adjacent = Solid.make_box(10, 10, 10, Plane(origin=(0, 10, 0)))
    separated = Solid.make_box(10, 10, 10, Plane(origin=(0, 11, 0)))
    touching_row = _sections(Compound(children=[first, adjacent]), first.bounding_box(), (.5,))[0]
    gap_row = _sections(Compound(children=[first, separated]), first.bounding_box(), (.5,))[0]
    assert touching_row["material_component_count"] == 1
    assert gap_row["material_face_count"] == 2
    assert gap_row["material_component_count"] == 2
