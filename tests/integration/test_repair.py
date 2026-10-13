"""Verify saved real M1 repair artifacts without silently rebuilding the shoe.

Set SHOE_POC_REPAIR_OUTPUT to select an actual run. Missing artifacts are skipped
as NOT_TESTED; the legacy output/m1 run is never treated as a repair result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from shoe_poc.schemas import ShoeSpec
from shoe_poc.reporting import sha256

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope="module")
def repair_output(request):
    folder = Path(os.environ.get("SHOE_POC_REPAIR_OUTPUT", ROOT / "output/m1_repair"))
    required = ("shoe_spec.json", "last_normalized.step", "base_shoe.step", "base_shoe.stl",
                "surface_report.json", "execution_report.json", "geometry_report.json",
                "validation_report.json", "roundtrip_report.json")
    if not all((folder / name).is_file() for name in required):
        pytest.skip("Actual real-last M1 repair artifacts unavailable; repair integration NOT_TESTED")
    execution = json.loads((folder / "execution_report.json").read_text())
    # Freeze the actual artifact fixture's identity at test time. The JSON
    # report destination alone does not identify the model that was tested.
    request.config._shoe_repair_provenance = {
        "directory": str(folder.resolve()), "run_id": execution.get("run_id"),
        "base_shoe_stl_sha256": sha256(folder / "base_shoe.stl"),
    }
    return folder


@pytest.fixture(scope="module")
def repaired_shapes(repair_output):
    from build123d import import_step
    return (import_step(repair_output / "last_normalized.step"),
            import_step(repair_output / "base_shoe.step"))


@pytest.fixture(scope="module")
def surface_report(repair_output):
    return json.loads((repair_output / "surface_report.json").read_text())


def test_repair_v01_defaults_are_saved_with_actual_cad(repair_output, repaired_shapes):
    spec = ShoeSpec.load(repair_output / "shoe_spec.json")
    assert spec.schema_version == "0.1"
    assert spec.upper.opening_parameters.transition_mm > 0
    assert spec.sole.profile_parameters.edge_radius_mm >= 0
    last, shoe = repaired_shapes
    assert last.is_valid and shoe.is_valid
    assert len(last.solids()) == len(shoe.solids()) == 1
    # Independent final-solid Boolean: a watertight shell alone does not prove
    # that the actual licensed last remains excluded from the exported shoe.
    overlap = shoe.intersect(last)
    overlap_volume = sum(s.volume for s in overlap.solids()) if overlap is not None else 0.0
    assert overlap_volume <= 0.01


def test_saved_real_positive_pipeline_contract_and_current_artifacts(repair_output, surface_report):
    """A truthful FAIL report alone cannot qualify the positive repair fixture."""
    read = lambda name: json.loads((repair_output / f"{name}.json").read_text())
    execution, geometry, mesh, roundtrip = [read(name) for name in (
        "execution_report", "geometry_report", "validation_report", "roundtrip_report")]
    spec = ShoeSpec.load(repair_output / "shoe_spec.json")
    assert execution["run_id"]
    assert execution["status"] == "PASS"
    assert execution["milestone_status"] == "PARTIAL"
    assert execution["appearance_acceptance"] == "PENDING"
    assert mesh["geometry_status"] == roundtrip["status"] == surface_report["geometry_status"] == "PASS"
    assert mesh["manufacturing_status"] == surface_report["manufacturing_status"] == "NOT_VERIFIED"
    # A previous model/report pair must not pass by occupying the same paths.
    outputs = execution["outputs"]
    assert {"base_shoe.step", "base_shoe.stl", "last_normalized.step", "shoe_spec.json",
            "geometry_report.json", "surface_report.json", "validation_report.json",
            "roundtrip_report.json"} <= outputs.keys()
    for name, recorded in outputs.items():
        artifact = repair_output / name
        assert artifact.is_file(), name
        assert artifact.stat().st_size == recorded["bytes"], name
        assert sha256(artifact) == recorded["sha256"], name
    assert execution["input_sha256"] == sha256(Path(spec.last.file))
    assert not (repair_output / "failure_report.json").exists()

    targets = surface_report["targets_mm"]
    assert targets == {"clearance": spec.upper.clearance_mm, "thickness": spec.upper.thickness_mm}
    tolerances = surface_report["tolerances_mm"]
    assert tolerances["clearance_absolute_error"] == .25
    assert tolerances["thickness_absolute_error"] == .35
    assert tolerances["cad_intersection"] == 1e-6
    assert not surface_report["unmeasurable_regions"]
    coverage = surface_report["coverage"]
    assert coverage["planned_site_count"] == min(512, coverage["candidate_count"])
    assert coverage["sampled_face_count"] == coverage["last_face_count"]
    assert coverage["faces_without_valid_uv_site"] == coverage["faces_omitted_by_budget"] == []
    samples = surface_report["samples"]
    assert samples
    for sample in samples:
        assert sample["status"] == "PASS"
        assert abs(sample["clearance_mm"]-targets["clearance"]) <= .25
        assert abs(sample["thickness_mm"]-targets["thickness"]) <= .35
        assert sample["clearance_deviation_mm"] == pytest.approx(sample["clearance_mm"]-targets["clearance"], abs=1e-12)
        assert sample["thickness_deviation_mm"] == pytest.approx(sample["thickness_mm"]-targets["thickness"], abs=1e-12)
    for field, statistics in (("clearance", "sampled_clearance_mm"), ("thickness", "sampled_thickness_mm")):
        values = [sample[f"{field}_mm"] for sample in samples]
        stats = surface_report[statistics]
        assert stats["count"] == len(samples)
        assert stats["minimum_mm"] == min(values)
        assert stats["maximum_mm"] == max(values)
        assert stats["mean_mm"] == pytest.approx(float(np.mean(values)), abs=1e-12)
    junction = geometry["junction"]
    assert junction["geometry_status"] == junction["validation"]["geometry_status"] == "PASS"
    validation = junction["validation"]
    assert validation["measurement_error_count"] == 0
    assert validation["records"]
    assert all(r["status"] == "PASS" and r["normal_angle_degrees"] <= validation["angle_limit_degrees"]
               for r in validation["records"])
    assert all(validation["regions"][region]["geometry_status"] == "PASS"
               and validation["regions"][region]["sample_count"] > 0 for region in ("heel", "middle", "toe"))
    rounding = geometry["sole"]["bottom_round_validation"]
    assert rounding["geometry_status"] == ("PASS" if spec.sole.profile_parameters.edge_radius_mm else "NOT_REQUIRED")


def test_saved_real_sampling_reports_errors_exclusions_and_unknowns(surface_report):
    report = surface_report
    assert report["samples"]
    assert report["coverage"]["planned_site_count"] <= 512
    assert len(report["samples"]) + len(report["excluded_samples"]) + len(report["unmeasurable_regions"]) == report["coverage"]["planned_site_count"]
    assert all(s["status"] in {"PASS", "FAIL"} for s in report["samples"])
    checks = {c["name"]: c for c in report["checks"]}
    for name in ("global_minimum_wall_thickness", "global_minimum_clearance",
                 "global_surface_self_intersections", "user_visual_acceptance"):
        assert checks[name]["status"] == "NOT_VERIFIED"
    if any(c["status"] == "FAIL" for c in report["checks"]):
        assert report["geometry_status"] == "FAIL"
    assert report["overall_status"] in {"PARTIAL", "FAIL"}


def test_saved_real_samples_replayed_by_independent_boolean_lines(repaired_shapes, surface_report):
    """CAD common with a finite edge independently checks intersector readings."""
    from build123d import Edge, Vertex
    last, shoe = repaired_shapes
    samples = surface_report["samples"]
    for index in np.linspace(0, len(samples)-1, min(5, len(samples)), dtype=int):
        sample = samples[index]
        p, n = np.asarray(sample["point_mm"]), np.asarray(sample["outward_normal"])
        assert last.distance_to(Vertex(*p)) <= 1e-4
        travel = max(25.0, sample["clearance_mm"] + sample["thickness_mm"] + 5.0)
        common = shoe.intersect(Edge.make_line(p.tolist(), (p+n*travel).tolist()))
        edges = list(common.edges()) if common is not None else []
        assert edges
        projected = [(sorted(float(np.dot(np.asarray(tuple(v.center()))-p, n))
                             for v in edge.vertices()), edge.length) for edge in edges]
        projected.sort(key=lambda pair: pair[0][0])
        endpoints, length = projected[0]
        assert endpoints[0] == pytest.approx(sample["clearance_mm"], abs=1e-4)
        assert length == pytest.approx(sample["thickness_mm"], abs=1e-4)
