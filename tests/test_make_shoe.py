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


def test_variant_names_match_config_and_unknown_is_listed():
    names = ms.variant_names()
    assert names[0] == "v1_base" and "v10_structure" in names and len(names) == 10
    with pytest.raises(ms.MakeShoeError) as e:
        ms.load_variant("v99", None)
    assert all(n in str(e.value) for n in names)
    assert all(n in ms.parser().format_help().replace("\n", " ").replace("  ", " ") for n in ("v1_base",))


def test_variant_image_only_for_image_driven():
    v, note = ms.load_variant("v6_image", "pic.png")
    assert v.image == "pic.png" and "drives" in note
    v, note = ms.load_variant("v6_image", None)
    assert v.image == "test" and note is None
    v, note = ms.load_variant("v7_hex", "pic.png")
    assert not v.image and "not used" in note


def test_knit_arg_wiring_and_printable_check(tmp_path, monkeypatch):
    phi, g, core = cup()
    seen = {}
    monkeypatch.setattr(ms, "place_cavity", lambda *a, **k: types.SimpleNamespace(pose={}))

    def fake(pl, v, cfg, log):
        seen["variant"] = v.name
        return {"form": "B-knit", "variant": v.name, "printable": False, "_field": (phi, g), "_core": (core, g)}

    monkeypatch.setattr(ms, "build_form_knit", fake)
    a = ms.parser().parse_args(["--form", "B-knit", "--variant", "v7_hex", "--out-dir", str(tmp_path), "--samples", "500"])
    assert ms.run(a, lambda s: None) == 0 and seen["variant"] == "v7_hex"
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "B-knit 针织变体 v7_hex" in md and "预览(不可打印)" in md and "B-knit takes its look" in md
    assert json.loads((tmp_path / "params.json").read_text())["variant"] == "v7_hex"
    assert ms.main(["--form", "B-knit", "--variant", "nope", "--out-dir", str(tmp_path)]) == 2


# ---------------------------------------------------------------------------------------------- --set overrides
from shoe_designer import spec_overrides as so


def test_parse_set_numbers_bools_json_and_strings():
    d = so.parse_set(["pitch_mm=10", "preview=true", "style=hex", "raise_pts=[[0,45],[1,0]]", "pitch_mm=11"])
    assert d == {"pitch_mm": 11, "preview": True, "style": "hex", "raise_pts": [[0, 45], [1, 0]]}
    with pytest.raises(so.OverrideError):
        so.parse_set(["novalue"])


def test_apply_overrides_types_clamp_and_json_fields():
    v, _ = ms.load_variant("v7_hex", None)
    new, applied, clamps = so.apply_overrides(v, {"pitch_mm": 100, "yarn_mm": 2, "raise_pts": [[0, 45], [0.5, 45], [1, 0]], "bumps": [{"dT": 2.0, "u": [0, 0.3]}]})
    assert new.pitch_mm == 40.0 and applied["pitch_mm"] == 40.0 and clamps == ["pitch_mm: 100.0 -> 40.0 (range [4.0, 40.0])"]
    assert new.yarn_mm == 2.0 and new.raise_pts[1] == (0.5, 45.0) and new.bump_dicts() == [{"dT": 2.0, "u": [0, 0.3]}]
    assert new.name == v.name and v.pitch_mm != 40.0  # the preset is untouched


def test_apply_overrides_rejects_unknown_key_and_bad_types():
    v, _ = ms.load_variant("v1_base", None)
    with pytest.raises(so.OverrideError, match="valid keys: .*pitch_mm"):
        so.apply_overrides(v, {"nope": 1})
    for bad in ({"pitch_mm": "x"}, {"preview": 1}, {"raise_pts": 3}, {"seed": 1.5}, {"name": "x"}):
        with pytest.raises(so.OverrideError):
            so.apply_overrides(v, bad)


def test_conformal_spec_overrides_and_list_params(capsys):
    v, _ = ms.load_style("k1_honeycomb", None)
    new, _, _ = so.apply_overrides(v, {"flow_cross_deg": 99, "sole_mode": "net"})
    assert new.flow_cross_deg == 30.0 and new.outline == v.outline
    assert ms.main(["--form", "B", "--list-params"]) == 0
    out = capsys.readouterr().out
    assert "flow_cross_deg" in out and "range [4.0, 40.0]" in out
    assert ms.main(["--form", "A", "--list-params"]) == 2


def test_set_refusals_exit_2(tmp_path):
    base = ["--form", "B-knit", "--variant", "v7_hex", "--out-dir", str(tmp_path)]
    assert ms.main([*base, "--set", "bogus=1"]) == 2
    assert ms.main([*base, "--set", "yarn_mm=0.6"]) == 2  # below the 1.2 mm strut minimum: the engine refuses, before the template stage
    assert ms.main(["--out-dir", str(tmp_path), "--set", "pitch_mm=9"]) == 2  # form A


def test_set_is_recorded_in_report_and_params(tmp_path, monkeypatch):
    phi, g, core = cup()
    monkeypatch.setattr(ms, "place_cavity", lambda *a, **k: types.SimpleNamespace(pose={}))
    seen = {}

    def fake(pl, v, cfg, log):
        seen["pitch"], seen["yarn"] = v.pitch_mm, v.yarn_mm
        return {"form": "B-knit", "variant": v.name, "printable": True, "_field": (phi, g), "_core": (core, g)}

    monkeypatch.setattr(ms, "build_form_knit", fake)
    a = ms.parser().parse_args(["--form", "B-knit", "--variant", "v7_hex", "--set", "pitch_mm=10", "--set", "yarn_mm=2.0", "--set", "elongate=9",
                                "--out-dir", str(tmp_path), "--samples", "500"])
    assert ms.run(a, lambda s: None) == 0
    assert seen == {"pitch": 10.0, "yarn": 2.0}
    p = json.loads((tmp_path / "params.json").read_text())
    assert p["overrides"] == {"pitch_mm": 10.0, "yarn_mm": 2.0, "elongate": 3.0} and "elongate" in p["override_clamps"][0]
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "参数覆盖" in md and "`pitch_mm` = `10.0`" in md and "elongate: 9.0 -> 3.0" in md
