"""S7 end-to-end driver: synthetic fields stand in for the heavy stages; the full run is opt-in (SHOE_E2E=1, needs the assets)."""
import json
import os
import types

import numpy as np
import pytest

from shoe_designer import intent as it
from shoe_designer import make_shoe as ms
from shoe_designer.lattice import LatticeConfig
from shoe_designer.process import ProcessConfig
from shoe_designer.sdf import Grid


def proc():
    return ProcessConfig.from_toml("config/process_sls_tpu.toml")


def box_sdf(X, Y, Z, lo, hi):
    c, h = (np.array(lo) + hi) / 2, (np.array(hi) - lo) / 2
    q = np.stack([np.abs(X - c[0]) - h[0], np.abs(Y - c[1]) - h[1], np.abs(Z - c[2]) - h[2]])
    return np.linalg.norm(np.maximum(q, 0), axis=0) + np.minimum(q.max(axis=0), 0)


def cup(closed=False):
    """A 40x30x20 mm cup (4 mm walls) open at the top, in a grid with an air margin; ``closed`` seals the top (a closed cavity)."""
    g = Grid((0.0, 0.0, 0.0), 1.0, 1.0, (50, 40, 30))
    X, Y, Z = np.meshgrid(*[np.arange(n, dtype=float) for n in g.shape], indexing="ij")
    outer = box_sdf(X, Y, Z, (5, 5, 3), (45, 35, 23))
    inner = box_sdf(X, Y, Z, (9, 9, 7), (41, 31, 30 if not closed else 19))
    return np.maximum(outer, -inner).astype(np.float32), g, (inner < 0)


def rep_of(phi, g, core):
    return {"form": "A", "_field": (phi, g), "_core": (core, g)}


def test_audit_passes_a_clean_cup():
    phi, g, core = cup()
    res = ms.check_and_audit(rep_of(phi, g, core), proc(), LatticeConfig(), False, samples=500)
    st = {c.name: c.status for c in res.checks}
    assert st["watertight"] == st["single_solid"] == st["no_closed_cavity"] == "pass"
    assert st["gate_d8"] == "na"
    assert st["build_volume"] == "pass"
    assert res.audit["weight_g"] == pytest.approx(res.mesh.volume / 1000 * 1.2, abs=0.06)
    assert not res.failed


def test_audit_flags_a_closed_cavity():
    phi, g, core = cup(closed=True)
    res = ms.check_and_audit(rep_of(phi, g, core), proc(), LatticeConfig(), False, samples=500)
    c = {c.name: c for c in res.checks}["no_closed_cavity"]
    assert c.status == "fail" and "已由保险填充补实" in c.detail
    assert res.failed


def test_tiny_sealed_pocket_is_a_warning_when_filled():
    phi, g, core = cup()
    # a 2-voxel air pocket inside the 4 mm wall
    phi[7, 20, 12:14] = 1.0
    res = ms.check_and_audit(rep_of(phi, g, core), proc(), LatticeConfig(), False, samples=500)
    c = {c.name: c for c in res.checks}["no_closed_cavity"]
    assert c.status == "warn" and "0 个" in c.value


def test_strided_keeps_origin_and_scales_spacing():
    phi, g, _ = cup()
    s, gs = ms.strided(phi, g, 2.0)
    assert s.shape == gs.shape and gs.h == 2.0 and gs.origin == g.origin


def test_intent_pattern_prefers_intent_then_style_then_none():
    base = {"pattern": None, "style_tags": []}
    assert ms.intent_pattern(base, None, None).kind == "none"
    assert ms.intent_pattern({**base, "style_tags": ["sporty"]}, None, None).kind == "hex_perforation"
    p = ms.intent_pattern({"pattern": {"kind": "wave_displacement", "scale_mm": None, "depth_mm": 99}, "style_tags": ["sporty"]}, None, None)
    assert p.kind == "wave_displacement" and p.depth_mm == 2.0 and "depth_mm" in p.clamped
    assert ms.intent_pattern(base, "voronoi_perforation", None).kind == "voronoi_perforation"


def test_resolve_intent_rules_and_empty():
    res = ms.resolve_intent("波浪纹的镂空运动鞋", [], it.RuleBackend())
    assert res.ok and res.intent["template"] == "dlp_hollow"
    empty = ms.resolve_intent("  ", [], None)
    assert empty.ok and empty.intent["pattern"] is None


def test_missing_key_is_reported_without_leaking(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ms.MakeShoeError, match="DEEPSEEK_API_KEY"):
        ms.make_backend("deepseek")


def test_load_style_and_outline_clamp():
    v, cl = ms.load_style("k1_honeycomb", None)
    assert v.name == "k1_honeycomb" and cl == []
    v, cl = ms.load_style("k2_triangle", json.dumps([{"fn": "sole_wall", "sole_mm": 99.0}]))
    assert v.outline[0]["sole_mm"] == 32.0 and "sole_wall.sole_mm" in cl[0]
    with pytest.raises(ms.MakeShoeError, match="unknown style"):
        ms.load_style("nope", None)


def test_run_writes_outputs_with_stubbed_stages(tmp_path, monkeypatch):
    phi, g, core = cup()
    monkeypatch.setattr(ms, "place_cavity", lambda *a, **k: types.SimpleNamespace(pose={"stub": True}))
    monkeypatch.setattr(ms, "build_form_a", lambda pl, spec, sole, cfg, log: {**rep_of(phi, g, core), "pattern_kind": spec.kind})
    a = ms.parser().parse_args(["波浪纹的镂空运动鞋", "--out-dir", str(tmp_path), "--samples", "500"])
    logs = []
    assert ms.run(a, logs.append) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"shoe.stl", "report.md", "params.json", "audit.json"}
    params = json.loads((tmp_path / "params.json").read_text())
    assert params["template_id"] == "dlp_hollow_44_right" and params["pattern"]["kind"] == "wave_displacement"
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "审计结论" in md and "水密" in md and "意图与限幅" in md
    assert any("clamp report" in s for s in logs)


def test_bad_size_is_a_user_error(tmp_path):
    assert ms.main(["--size", "40", "--out-dir", str(tmp_path)]) == 2


@pytest.mark.skipif(not os.environ.get("SHOE_E2E"), reason="set SHOE_E2E=1 and SHOE_ASSETS_ROOT: ~1 min, needs the template meshes")
def test_full_form_a(tmp_path):
    assert ms.main(["波浪纹的镂空运动鞋", "--out-dir", str(tmp_path)]) in (0, 1)
    assert (tmp_path / "shoe.stl").stat().st_size > 1e6
