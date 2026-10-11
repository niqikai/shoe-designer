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

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope="module")
def repair_output():
    folder = Path(os.environ.get("SHOE_POC_REPAIR_OUTPUT", ROOT / "output/m1_repair"))
    required = ("shoe_spec.json", "last_normalized.step", "base_shoe.step", "surface_report.json")
    if not all((folder / name).is_file() for name in required):
        pytest.skip("Actual real-last M1 repair artifacts unavailable; repair integration NOT_TESTED")
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
    assert sum(s.volume for s in overlap.solids()) <= 0.01


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
