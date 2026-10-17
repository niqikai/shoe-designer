"""Checks and report (plan M0.9): each check finds what it should and nothing else."""

from __future__ import annotations

import json
import math

import pytest

from shoegen.checks import (
    VERDICT,
    bcc_throat_diameter_mm,
    build_report,
    cavity_check,
    collect_block_masks,
    run_checks,
    run_hard_checks,
    run_prebuild_checks,
    thin_wall_check,
    write_report,
)
from shoegen.evaluate import plan_blocks
from shoegen.graph import (
    Box,
    Capsule,
    GraphBuilder,
    Intersect,
    RoundedBox,
    StrutLattice,
    Subtract,
    Union,
)
from shoegen.mesh import extract_mesh
from shoegen.profile import load_profile

PROFILE = load_profile("pbf-tpu-generic@0")  # min_wall 1.0, build volume 380x284x380


def box_graph(center, half):
    b = GraphBuilder()
    return b.build(b.add(Box(center=center, half_size=half), "box"))


def hollow_sphere_graph(c=(4.0, 4.0, 4.0), r_out=16.0, r_in=8.0, tunnel=False):
    b = GraphBuilder()
    outer = b.add(RoundedBox(center=c, half_size=(r_out,) * 3, radius=r_out), "outer")
    inner = b.add(RoundedBox(center=c, half_size=(r_in,) * 3, radius=r_in), "inner")
    shell = b.add(Subtract(base=outer, tool=inner), "shell")
    if tunnel:
        cap = b.add(Capsule(a=c, b=(c[0] + 24.0, c[1], c[2]), radius=2.0), "tunnel")
        shell = b.add(Subtract(base=shell, tool=cap), "holed")
    return b.build(shell)


def make(graph, rmin, rmax, h):
    plan = plan_blocks(graph, None, rmin, rmax, h)
    return plan, collect_block_masks(plan, workers=1)


# ---------------------------------------------------------------- thin walls


def test_plate_below_min_wall_is_thin_above_is_not():
    h = 0.25
    thin_t, thick_t = 0.6, 2.5
    g_thin = box_graph((0, 0, 0), (15, 15, thin_t / 2))
    g_thick = box_graph((0, 0, 0), (15, 15, thick_t / 2))
    _, m_thin = make(g_thin, (-20,) * 3, (20,) * 3, h)
    _, m_thick = make(g_thick, (-20,) * 3, (20,) * 3, h)
    thin = thin_wall_check(m_thin, PROFILE.min_wall_mm)
    thick = thin_wall_check(m_thick, PROFILE.min_wall_mm)
    assert thick["thin_samples"] == 0
    assert thick["thin_volume_mm3"] == 0.0
    # the report states the resolution band: min_wall -/+ 2h
    assert thin["flagged_reliably_below_mm"] == pytest.approx(0.5)
    assert thin["not_flagged_above_mm"] == pytest.approx(1.5)
    analytic = 30 * 30 * thin_t
    assert thin["thin_volume_mm3"] == pytest.approx(analytic, rel=0.35)
    assert thin["affected_blocks"] >= 1


def test_thick_solid_spanning_blocks_has_no_false_thin_at_block_faces():
    # 40 mm box over several 32 mm blocks (h = 0.5); block faces cut through the material.
    g = box_graph((4, 4, 4), (20, 20, 20))
    plan, masks = make(g, (-30,) * 3, (40,) * 3, 0.5)
    assert plan.n_crossing > 1
    res = thin_wall_check(masks, PROFILE.min_wall_mm)
    assert res["thin_samples"] == 0
    assert res["blocks_examined"] > 1


def test_thin_wall_across_block_boundary_counted_once():
    # A 0.6 mm plate straddling the block boundary at x = 2 is flagged on both sides.
    g = box_graph((2.0, 4.0, 0.0), (10, 8, 0.3))
    _, masks = make(g, (-30,) * 3, (30,) * 3, 0.5)
    res = thin_wall_check(masks, PROFILE.min_wall_mm)
    assert res["affected_blocks"] >= 2
    assert res["thin_volume_mm3"] == pytest.approx(20 * 16 * 0.6, rel=0.5)


# ---------------------------------------------------------------- cavities


def test_sealed_cavity_detected_across_block_boundaries():
    plan, masks = make(hollow_sphere_graph(), (-30,) * 3, (30,) * 3, 0.5)
    assert plan.grid_shape == (2, 2, 2)
    res = cavity_check(masks)
    assert res["cavity_count"] == 1
    assert res["unreachable_void_volume_mm3"] == pytest.approx(4 / 3 * math.pi * 8**3, rel=0.03)


def test_cavity_with_hole_is_reachable():
    _, masks = make(hollow_sphere_graph(tunnel=True), (-30,) * 3, (30,) * 3, 0.5)
    res = cavity_check(masks)
    assert res["cavity_count"] == 0
    assert res["unreachable_void_volume_mm3"] == 0.0


def test_solid_box_has_no_cavity():
    _, masks = make(box_graph((4, 4, 4), (20, 20, 20)), (-30,) * 3, (40,) * 3, 0.5)
    assert cavity_check(masks)["cavity_count"] == 0


# ---------------------------------------------------------------- hard + report


def build(graph, rmin, rmax, h, profile=PROFILE):
    plan = plan_blocks(graph, None, rmin, rmax, h)
    res = extract_mesh(plan, workers=1, min_fragment_volume_mm3=profile.min_fragment_volume_mm3)
    return plan, res


def by_id(records):
    return {r["id"]: r for r in records}


def test_hard_checks_pass_for_clean_solid():
    _, res = build(box_graph((0, 0, 0), (10, 8, 6)), (-14,) * 3, (14,) * 3, 1.0)
    hard = by_id(run_hard_checks(res, PROFILE))
    assert {r["result"] for r in hard.values()} == {"pass"}
    assert hard["build_volume"]["measured"]["dimensions_mm"] == pytest.approx([20, 16, 12])
    assert hard["build_volume"]["source"]["profile_id"] == PROFILE.id


def test_multi_component_fails():
    b = GraphBuilder()
    a = b.add(Box(center=(-12, 0, 0), half_size=(5, 5, 5)), "a")
    c = b.add(Box(center=(12, 0, 0), half_size=(5, 5, 5)), "c")
    g = b.build(b.add(Union(children=[a, c]), "u"))
    _, res = build(g, (-20,) * 3, (20,) * 3, 1.0)
    hard = by_id(run_hard_checks(res, PROFILE))
    assert hard["connected_components"]["result"] == "fail"
    assert hard["connected_components"]["measured"]["components"] == 2
    assert hard["watertight"]["result"] == "pass"


def test_oversized_box_fails_build_volume_only():
    small = PROFILE.model_copy(update={"build_volume_mm": (30.0, 30.0, 30.0)})
    _, res = build(box_graph((0, 0, 0), (20, 5, 5)), (-26, -10, -10), (26, 10, 10), 1.0, small)
    hard = by_id(run_hard_checks(res, small))
    assert hard["build_volume"]["result"] == "fail"
    assert hard["build_volume"]["measured"]["dimensions_mm"][0] == pytest.approx(40.0)
    assert hard["watertight"]["result"] == "pass"
    assert hard["connected_components"]["result"] == "pass"


def test_region_clipping_the_solid_fails_watertight():
    _, res = build(box_graph((0, 0, 0), (10, 10, 10)), (-6,) * 3, (6,) * 3, 1.0)
    hard = by_id(run_hard_checks(res, PROFILE))
    assert hard["watertight"]["result"] == "fail"


def test_sealed_cavity_does_not_fail_hard_and_is_reported_soft():
    g = hollow_sphere_graph()
    plan, res = build(g, (-30,) * 3, (30,) * 3, 0.5)
    report, code = run_checks(g, PROFILE, 0.5, plan, res, workers=1)
    hard = by_id(report["checks"]["hard"])
    assert hard["connected_components"]["measured"]["components"] == 1
    assert hard["connected_components"]["measured"]["cavity_shells"] == 1
    assert code == 0
    soft = by_id(report["checks"]["soft"])
    assert soft["enclosed_cavities"]["finding"] is True
    assert soft["enclosed_cavities"]["result"] == "report_only"
    assert soft["thin_walls"]["finding"] is False


def test_soft_findings_never_change_exit_code():
    g = box_graph((0, 0, 0), (15, 15, 0.3))  # 0.6 mm plate: thin
    plan, res = build(g, (-20,) * 3, (20,) * 3, 0.25)
    report, code = run_checks(g, PROFILE, 0.25, plan, res, workers=1)
    assert by_id(report["checks"]["soft"])["thin_walls"]["finding"] is True
    assert code == 0
    assert report["exit_code"] == 0


def lattice_graph(radius, cell=12.0):
    b = GraphBuilder()
    lat = b.add(StrutLattice(cell_mm=cell, radius_mm=radius), "lattice")
    box = b.add(Box(center=(0, 0, 0), half_size=(18, 18, 18)), "box")
    return b.build(b.add(Intersect(children=[lat, box]), "body"))


def test_prebuild_failure_gives_exit_3_and_no_hard_results():
    g = lattice_graph(radius=0.5)  # diameter 1.0 < 1.5
    pre = run_prebuild_checks(g, PROFILE, 0.25)
    assert [r["result"] for r in pre] == ["fail", "pass"]
    report, code = run_checks(g, PROFILE, 0.25, include_environment=False)
    assert code == 3 == report["exit_code"]
    assert report["summary"]["hard"] == "not_run"
    assert report["checks"]["hard"] == []
    assert report["manufacturing_verdict"] == VERDICT


def test_voxel_rule_failure_reported_with_values():
    pre = by_id(run_prebuild_checks(lattice_graph(radius=2.0), PROFILE, 2.0))
    r = pre["voxel_vs_strut[lattice]"]
    assert r["result"] == "fail"
    assert r["measured"]["voxels_across_strut"] == pytest.approx(2.0)
    assert r["threshold"]["max_voxel_mm"] == pytest.approx(1.0)


def test_pore_estimate():
    assert bcc_throat_diameter_mm(12.0, 2.0) == pytest.approx(2 * (12 / math.sqrt(6) - 2))
    assert bcc_throat_diameter_mm(4.0, 2.0) == 0.0
    g = lattice_graph(radius=2.0, cell=12.0)
    plan, res = build(g, (-24,) * 3, (24,) * 3, 1.0)
    report, _ = run_checks(g, PROFILE, 1.0, plan, res, workers=1, include_environment=False)
    pore = by_id(report["checks"]["soft"])["min_pore_size"]
    assert pore["measured"]["min_throat_diameter_mm"] == pytest.approx(5.798, abs=0.01)
    assert pore["threshold"]["min_pore_mm"] == PROFILE.min_pore_mm
    assert pore["finding"] is False
    assert pore["source"]["profile_status"] == "placeholder"
    # ADR 0004 soft checks that M0 does not implement are listed as not_run, never pass
    soft = by_id(report["checks"]["soft"])
    assert soft["drainage"]["result"] == soft["supports"]["result"] == "not_run"
    assert report["summary"]["soft_not_run"] == ["drainage", "supports"]


def test_report_json_is_deterministic_sorted_and_not_verified(tmp_path):
    g = box_graph((0, 0, 0), (10, 8, 6))
    plan, res = build(g, (-14,) * 3, (14,) * 3, 1.0)
    paths = []
    for i in range(2):
        report, _ = run_checks(g, PROFILE, 1.0, plan, res, workers=1, include_environment=False)
        # timings differ between runs by design
        report["timings_s"] = {}
        report["mesh"]["timings_s"] = {}
        report["eval"] = {}
        for rec in report["checks"]["soft"]:
            rec.pop("seconds", None)
            rec["measured"].pop("seconds", None)
            rec.pop("mask_pass_seconds", None)
        p = tmp_path / f"r{i}.json"
        write_report(p, report)
        paths.append(p)
    a, b = (p.read_text() for p in paths)
    assert a == b
    data = json.loads(a)
    assert data["manufacturing_verdict"] == "NOT_VERIFIED"
    assert a == json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    assert data["profile"]["status"] == "placeholder"
    for group in ("prebuild", "hard", "soft"):
        for rec in data["checks"][group]:
            assert rec["source"]["profile_id"] == PROFILE.id
            assert rec["source"]["profile_status"] == "placeholder"
            assert "measured" in rec and "threshold" in rec


def test_report_never_passes_unrun_checks():
    report, code = build_report(PROFILE, [], None, None, include_environment=False)
    assert code == 0
    # nothing measured: neither group may be summarised as "pass" (M0 review)
    assert report["summary"]["prebuild"] == "not_applicable"
    assert report["summary"]["hard"] == "not_run"
    assert report["checks"]["hard"] == []
