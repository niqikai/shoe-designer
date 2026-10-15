"""Replay saved toe CAD/STL evidence, preserving the actual prior artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from shoe_poc.reporting import sha256
from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.design import _volume

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope="module")
def toe_output():
    directory = Path(os.environ.get("SHOE_POC_TOE_OUTPUT", ROOT/"output/m1_toe_refine"))
    if not (directory/"toe_report.json").is_file():
        pytest.skip("Saved toe-refinement output unavailable; actual toe replay NOT_TESTED")
    required = ("base_shoe.step", "base_shoe.stl", "last_normalized.step", "cavity.step",
                "shoe_spec.json", "last_metadata.json", "execution_report.json", "design_report.json",
                "surface_report.json", "validation_report.json", "roundtrip_report.json", "geometry_report.json")
    for name in required:
        assert (directory/name).is_file(), f"Incomplete saved toe evidence: {name}"
    reports = {name: json.loads((directory/(name+".json")).read_text()) for name in
               ("toe_report", "last_metadata", "execution_report", "design_report", "surface_report",
                "validation_report", "roundtrip_report", "geometry_report")}
    baseline = Path(reports["toe_report"]["baseline_directory"])
    assert baseline.resolve() != directory.resolve()
    for name in ("base_shoe.step", "base_shoe.stl", "last_normalized.step", "cavity.step", "shoe_spec.json", "last_metadata.json"):
        assert (baseline/name).is_file(), f"Missing actual preserved baseline evidence: {name}"
    return directory, baseline, reports


@pytest.fixture(scope="module")
def toe_shapes(toe_output):
    from build123d import import_step
    directory, baseline, _ = toe_output
    return {"shoe": import_step(directory/"base_shoe.step"),
            "last": import_step(directory/"last_normalized.step"),
            "cavity": import_step(directory/"cavity.step"),
            "baseline_shoe": import_step(baseline/"base_shoe.step"),
            "baseline_last": import_step(baseline/"last_normalized.step"),
            "baseline_cavity": import_step(baseline/"cavity.step")}


def test_saved_toe_reports_are_current_and_do_not_approve_appearance(toe_output):
    directory, baseline, reports = toe_output
    execution, toe = reports["execution_report"], reports["toe_report"]
    assert execution["status"] == toe["geometry_status"] == "PASS"
    assert execution["toe_profile_status"] == "PASS"
    assert execution["run_id"] == toe["run_id"] == reports["design_report"]["run_id"]
    assert toe["source_sha256"] == execution["input_sha256"] == reports["last_metadata"]["source"]["sha256"]
    assert toe["baseline_step_sha256"] == sha256(baseline/"base_shoe.step")
    assert execution["milestone_status"] == "PARTIAL"
    assert execution["appearance_acceptance"] == toe["appearance_acceptance"] == "PENDING"
    assert toe["baseline_comparison"]["appearance_acceptance"] == "PENDING"
    assert reports["design_report"]["geometry_status"] == "PASS"
    assert reports["surface_report"]["geometry_status"] == reports["validation_report"]["geometry_status"] == "PASS"
    assert reports["roundtrip_report"]["status"] == "PASS"
    assert reports["geometry_report"]["junction"]["geometry_status"] == "PASS"
    repair = reports["geometry_report"]["representation_repair"]
    assert repair["geometry_status"] == "PASS"
    assert repair["symmetric_difference_volume_mm3"] <= .01
    assert repair["bbox_max_delta_mm"] <= 1e-5
    export = json.loads((directory/"mesh_export_report.json").read_text())["base_shoe"]
    assert export["native_face_coverage_status"] == "PASS"
    assert export["native_faces_without_triangulation"] == []
    assert export["hole_filling"] is False
    assert export["vertex_or_normal_changes"] is False
    assert {"toe_report.json", "base_shoe.step", "base_shoe.stl"} <= execution["outputs"].keys()
    for name, recorded in execution["outputs"].items():
        assert sha256(directory/name) == recorded["sha256"], name
        assert (directory/name).stat().st_size == recorded["bytes"], name
    assert not (directory/"failure_report.json").exists()


def test_actual_toe_solid_and_native_mesh_remain_connected_and_closed(toe_output, toe_shapes):
    from shoe_poc.validation import validate_mesh
    directory, _, _ = toe_output
    shoe = toe_shapes["shoe"]
    assert shoe.is_valid and len(shoe.solids()) == 1 and shoe.volume > 0
    mesh = validate_mesh(directory/"base_shoe.stl")
    assert mesh["geometry_status"] == "PASS"
    assert mesh["euler_number"] == 2
    assert _volume(shoe.intersect(toe_shapes["last"])) <= .01
    assert _volume(shoe.intersect(toe_shapes["cavity"])) <= .01


def test_actual_source_fit_and_upper_parameters_are_preserved(toe_output, toe_shapes):
    directory, baseline, reports = toe_output
    current, old = ShoeSpec.load(directory/"shoe_spec.json"), ShoeSpec.load(baseline/"shoe_spec.json")
    assert current.last.coordinate_system == old.last.coordinate_system
    assert current.upper == old.upper
    assert current.sole.height_mm == old.sole.height_mm
    assert current.sole.profile_parameters.toe_roll_radius_mm > 0
    assert _volume(toe_shapes["last"].cut(toe_shapes["baseline_last"]))+_volume(
        toe_shapes["baseline_last"].cut(toe_shapes["last"])) <= .01
    assert _volume(toe_shapes["cavity"].cut(toe_shapes["baseline_cavity"]))+_volume(
        toe_shapes["baseline_cavity"].cut(toe_shapes["cavity"])) <= .01
    baseline_metadata = json.loads((baseline/"last_metadata.json").read_text())
    assert reports["toe_report"]["source_sha256"] == baseline_metadata["source"]["sha256"]


def test_saved_toe_floor_and_front_points_replay_on_actual_cad_boolean_lines(toe_output, toe_shapes):
    """Independent Boolean lines check saved ray results without reusing the ray helper."""
    from build123d import Edge
    _, _, reports = toe_output
    toe = reports["toe_report"]
    for key, shape in (("candidate", toe_shapes["shoe"]), ("baseline", toe_shapes["baseline_shoe"])):
        assert toe[key]["measurement_error_count"] == 0
        assert len(toe[key]["lanes"]) == 3
        box = shape.bounding_box()
        for lane in toe[key]["lanes"]:
            assert lane["geometry_status"] == "PASS"
            y = lane["y_mm"]
            # Endpoints and interior probes cover the roll and its floor/front
            # transitions. Lines span the entire solid so intervals are real.
            for kind in ("floor", "front"):
                points = lane[kind+"_points_xz_mm"]
                assert len(points) >= 3
                for index in sorted({0, len(points)//3, len(points)//2, 2*len(points)//3, len(points)-1}):
                    x, z = points[index]
                    ends = ((x, y, box.min.Z-2), (x, y, box.max.Z+2)) if kind == "floor" else (
                        (box.min.X-2, y, z), (box.max.X+2, y, z))
                    common = shape.intersect(Edge.make_line(*ends))
                    assert common is not None and common.edges()
                    coordinates = [tuple(vertex.center())[2 if kind == "floor" else 0]
                                   for edge in common.edges() for vertex in edge.vertices()]
                    measured = min(coordinates) if kind == "floor" else max(coordinates)
                    assert measured == pytest.approx(z if kind == "floor" else x, abs=1e-4)


def test_toe_shape_change_is_observed_in_all_three_actual_profiles(toe_output):
    _, _, reports = toe_output
    toe = reports["toe_report"]
    assert toe["baseline_comparison"]["status"] == "OBSERVED"
    for row in toe["baseline_comparison"]["lane_changes"]:
        # These are regression expectations for this actual selected candidate,
        # not universal dimensions for conventional shoes or manufacturing.
        assert row["maximum_terminal_floor_low_slope_run_mm"]["change_mm"] < -.25
        assert row["maximum_front_near_vertical_run_mm"]["change_mm"] < -.25
