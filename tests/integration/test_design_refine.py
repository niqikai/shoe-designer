"""Replay saved design-refinement CAD evidence; do not rebuild or claim repeatability.

SHOE_POC_DESIGN_OUTPUT selects the actual artifact directory. Missing output is
honestly NOT_TESTED, independently of the previous M1 repair fixture.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from shoe_poc.reporting import sha256
from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.design import _volume

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope="module")
def design_output(request):
    directory = Path(os.environ.get("SHOE_POC_DESIGN_OUTPUT", ROOT / "output/m1_design_refine"))
    required = ("shoe_spec.json", "last_normalized.step", "base_shoe.step", "base_shoe.stl",
                "design_report.json", "last_metadata.json", "geometry_report.json",
                "surface_report.json", "execution_report.json", "validation_report.json",
                "roundtrip_report.json")
    if not all((directory / name).is_file() for name in required):
        pytest.skip("Actual design-refinement artifacts unavailable; integration NOT_TESTED")
    reports = {name: json.loads((directory / (name+".json")).read_text()) for name in
               ("design_report", "last_metadata", "execution_report", "geometry_report",
                "surface_report", "validation_report", "roundtrip_report")}
    provenance = {"directory": str(directory.resolve()), "run_id": reports["execution_report"].get("run_id"),
                  "base_shoe_stl_sha256": sha256(directory / "base_shoe.stl")}
    request.config._shoe_design_provenance = dict(provenance)
    return directory, reports, provenance


@pytest.fixture(scope="module")
def design_shapes(design_output):
    from build123d import import_step
    directory, reports, _ = design_output
    baseline = Path(reports["design_report"]["baseline_directory"])
    # A found positive artifact must include its real comparison evidence;
    # absent baseline evidence is a test failure, not another skip.
    assert baseline.resolve() != directory.resolve()
    for name in ("base_shoe.step", "last_normalized.step", "shoe_spec.json", "last_metadata.json", "cavity.step"):
        assert (baseline / name).is_file(), f"Missing actual baseline evidence: {name}"
    return {"last": import_step(directory / "last_normalized.step"),
            "shoe": import_step(directory / "base_shoe.step"),
            "baseline_last": import_step(baseline / "last_normalized.step"),
            "baseline_cavity": import_step(baseline / "cavity.step"), "baseline_directory": baseline}


def test_design_positive_reports_are_current_and_do_not_approve_appearance(design_output):
    directory, reports, provenance = design_output
    execution, design = reports["execution_report"], reports["design_report"]
    assert execution["run_id"] == design["run_id"] == provenance["run_id"]
    assert execution["status"] == "PASS"
    assert execution["milestone_status"] == design["overall_status"] == "PARTIAL"
    assert execution["appearance_acceptance"] == design["appearance_acceptance"] == design["user_acceptance"] == "PENDING"
    assert design["geometry_status"] == "PASS"
    assert design["metrics_status"] == "OBSERVED"
    assert reports["surface_report"]["geometry_status"] == reports["validation_report"]["geometry_status"] == "PASS"
    assert reports["roundtrip_report"]["status"] == "PASS"
    assert reports["geometry_report"]["junction"]["geometry_status"] == "PASS"
    for name, recorded in execution["outputs"].items():
        assert (directory / name).is_file(), name
        assert sha256(directory / name) == recorded["sha256"], name
        assert (directory / name).stat().st_size == recorded["bytes"], name
    assert {"design_report.json", "base_shoe.step", "base_shoe.stl"} <= execution["outputs"].keys()
    assert sha256(directory / "base_shoe.stl") == provenance["base_shoe_stl_sha256"]
    assert not (directory / "failure_report.json").exists()
    for key in ("global_wall_minimum", "global_self_intersection", "global_curvature_continuity", "manufacturing_qualification"):
        assert design[key] == "NOT_VERIFIED"


def test_design_source_frame_and_fit_are_fixed_with_real_cad_evidence(design_output, design_shapes):
    directory, reports, _ = design_output
    baseline_directory = design_shapes["baseline_directory"]
    current, baseline = ShoeSpec.load(directory / "shoe_spec.json"), ShoeSpec.load(baseline_directory / "shoe_spec.json")
    baseline_metadata = json.loads((baseline_directory / "last_metadata.json").read_text())
    source_sha = reports["last_metadata"]["source"]["sha256"]
    assert source_sha == baseline_metadata["source"]["sha256"] == reports["design_report"]["source_sha256"]
    assert source_sha == reports["execution_report"]["input_sha256"]
    assert current.last.coordinate_system == baseline.last.coordinate_system
    assert current.upper.clearance_mm == baseline.upper.clearance_mm == 1.
    assert current.upper.thickness_mm == baseline.upper.thickness_mm == 2.5
    assert current.sole.height_mm == 16.
    assert _volume(design_shapes["last"].cut(design_shapes["baseline_last"])) + _volume(
        design_shapes["baseline_last"].cut(design_shapes["last"])) <= .01
    assert _volume(design_shapes["shoe"].intersect(design_shapes["last"])) <= .01
    checks = {c["name"]: c for c in reports["design_report"]["checks"]}
    assert checks["unchanged_source_last"]["status"] == checks["unchanged_clearance_cavity"]["status"] == "PASS"
    assert checks["unchanged_clearance_cavity"]["value"] <= .01
    # Replay both imported actual cavities only if the current artifact exports
    # its cavity. Otherwise preserve honest recorded
    # symmetric-difference evidence; never substitute a regenerated offset.
    if (directory / "cavity.step").is_file():
        from build123d import import_step
        cavity = import_step(directory / "cavity.step")
        old = design_shapes["baseline_cavity"]
        assert _volume(cavity.cut(old))+_volume(old.cut(cavity)) <= .01


def test_design_underfoot_actual_samples_replayed_with_independent_boolean_lines(design_output, design_shapes):
    from build123d import Edge, Vertex
    _, reports, _ = design_output
    footbed = reports["design_report"]["underfoot"]
    assert footbed["geometry_status"] == "PASS"
    assert footbed["selection"]["sole_exclusion_applied"] is False
    assert footbed["measurement_errors"] == []
    assert footbed["minimum_directional_material_target_mm"] == pytest.approx(2.15)
    assert footbed["minimum_measured_directional_material_mm"] >= 2.15
    for region in ("heel", "middle", "toe"):
        assert footbed["regions"][region]["geometry_status"] == "PASS"
        assert footbed["regions"][region]["sample_count"] > 0
        sample = next(s for s in footbed["samples"] if s["region"] == region)
        point, normal = np.asarray(sample["point_mm"]), np.asarray(sample["outward_normal"])
        assert design_shapes["last"].distance_to(Vertex(*point)) <= 1e-4
        length = sample["clearance_mm"]+sample["directional_material_mm"]+5.
        common = design_shapes["shoe"].intersect(Edge.make_line(point.tolist(), (point+normal*length).tolist()))
        assert common is not None and common.edges()
        intervals = []
        for edge in common.edges():
            limits = sorted(float(np.dot(np.asarray(tuple(v.center()))-point, normal)) for v in edge.vertices())
            intervals.append((limits, edge.length))
        limits, material = min(intervals, key=lambda item: item[0][0])
        assert limits[0] == pytest.approx(sample["clearance_mm"], abs=1e-4)
        assert material == pytest.approx(sample["directional_material_mm"], abs=1e-4)


def test_design_width_and_actual_floor_metrics_remain_observations(design_output):
    _, reports, _ = design_output
    design = reports["design_report"]
    widths = design["forefoot_fit_constraint"]
    assert widths["source_width_mm"] < widths["cavity_width_mm"] < widths["final_shoe_width_mm"]
    assert widths["nominal_cavity_plus_two_walls_reference_mm"] == pytest.approx(widths["cavity_width_mm"]+5.)
    assert design["baseline_comparison"]["appearance_acceptance"] == "PENDING"
    assert design["rim"]["one_closed_entry_loop"]
    assert design["rim"]["unmeasurable_tangent_joints"] == []
    assert design["surface_conditioning"]["sample_count"] <= 512
    assert design["surface_conditioning"]["global_self_intersection"] == "NOT_VERIFIED"
    floors = {r["source_length_fraction"]: r["floor_z_mm"] for r in design["actual_rocker"]["floor_samples"]}
    assert floors[.95] > floors[.4]
