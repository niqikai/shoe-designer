import dataclasses

import numpy as np
import pytest
from test_lattice import capsule_scene

from shoe_designer import lattice as L
from shoe_designer import lattice_variants as V
from shoe_designer.process import ProcessConfig, mesh_inner_shells

CFG = L.LatticeConfig.from_toml()
PROC = ProcessConfig.from_toml("process_sls_tpu.toml")


@pytest.fixture(scope="module")
def prep():
    grid, cav, zt = capsule_scene()
    return V.prepare(cav, zt, grid)


def spec(**kw):
    return V.VariantSpec.from_dict(kw, {"thickness_mm": 2.4, "sole_mm": 10.0})


def test_all_configured_variants_pass_the_checks():
    specs = V.load_variants()
    assert len(specs) >= 8 and len({s.name for s in specs}) == len(specs)
    for s in specs:
        assert V.check_variant(s, CFG, PROC)["yarn_diameter_min_mm"] >= 1.2


def test_yarn_below_minimum_or_thicker_than_the_shell_is_refused():
    with pytest.raises(L.LatticeError, match="diameter"):
        V.check_variant(spec(yarn_mm=1.0), CFG, PROC)
    with pytest.raises(L.LatticeError, match="diameter"):
        V.check_variant(spec(yarn_pts_u=[[0.0, 0.5], [1.0, 1.0]]), CFG, PROC)  # 0.8 mm at the heel
    with pytest.raises(L.LatticeError, match="thickness"):
        V.check_variant(spec(thickness_mm=1.5), CFG, PROC)
    with pytest.raises(L.LatticeError, match="style"):
        V.check_variant(spec(style="weave"), CFG, PROC)
    with pytest.raises(L.LatticeError, match="unknown"):
        spec(colour="red")


def test_preview_is_not_printable_and_a_coarse_voxel_is_refused_otherwise():
    assert V.check_variant(spec(), CFG, PROC)["printable"]
    with pytest.raises(L.LatticeError, match="voxel"):
        V.check_variant(spec(voxel_mm=0.8), CFG, PROC)
    chk = V.check_variant(spec(voxel_mm=0.8, preview=True), CFG, PROC)
    assert not chk["printable"]


def test_bumps_only_thicken_and_never_enter_the_cavity(prep):
    v = spec(bumps=[{"dT": 5.0, "u": [0.5, 1.2], "normal": "top"}], toe_cap_h_mm=20.0)
    t = V.thickness_field(prep, v)
    assert t.min() >= 2.4 - 1e-6 and t.max() > 6.0
    body, _ = V.shell_body_v(prep, v)
    assert (body[prep.c0 < 0] >= 0).all()  # no solid inside the cavity


def test_collar_raise_is_clamped_under_the_top_of_the_last(prep):
    raised = V.collar_line(prep, spec(raise_pts=[[0.0, 200.0], [1.0, 200.0]]))
    assert (raised >= prep.zt).all()
    cols = prep.top > prep.zt + 8
    assert cols.any() and (raised[cols] <= prep.top[cols] - 8 + 1e-3).all()


@pytest.mark.parametrize("kw", [
    {"style": "diamond"},
    {"style": "triangle", "pitch_mm": 9.0},
    {"style": "hex", "pitch_mm": 12.0},
    {"style": "voronoi", "pitch_mm": 12.0},
    {"style": "diamond", "image": "test", "pitch_mm": 8.0, "thickness_mm": 2.6},
    {"style": "diamond", "pitch_pts": [[0.0, 0.8], [1.0, 1.4]], "yarn_pts_u": [[0.0, 1.2], [1.0, 0.9]], "raise_pts": [[0, 10], [1, 10]]},
], ids=["diamond", "triangle", "hex", "voronoi", "image", "gradient+raise"])
def test_variants_build_one_watertight_body_without_sealed_cavities(prep, kw):
    v = spec(**kw)
    fine, fg, rep, aux = V.build_variant(prep, v, CFG, PROC)
    assert rep["printable"]
    L.fill_sealed(fine, fg)
    assert L.sealed(fine, fg)["count"] == 0
    mesh, er = L.to_mesh(fine, fg, 200.0)
    assert mesh.is_watertight and mesh.body_count == 1 and mesh_inner_shells(mesh)["count"] == 0
    assert rep["lattice_body_cm3"] < 0.8 * rep["solid_body_cm3"]
    m = rep["measured"]
    assert m["yarn_diameter_used_min_mm"] >= 1.2 - 1e-6


def test_image_controls_the_yarn_diameter(prep):
    v = spec(image="test", pitch_mm=8.0, thickness_mm=2.6)
    _, _, rep, _ = V.build_variant(prep, v, CFG, PROC)
    assert rep["measured"]["yarn_diameter_used_min_mm"] == pytest.approx(1.2, abs=0.05)
    assert rep["measured"]["yarn_diameter_used_max_mm"] == pytest.approx(2.4, abs=0.05)


def test_deterministic(prep):
    v = spec(style="voronoi", pitch_mm=12.0)
    a = V.build_variant(prep, v, CFG, PROC)[0]
    b = V.build_variant(prep, v, CFG, PROC)[0]
    assert np.array_equal(a, b)
    assert dataclasses.replace(v, seed=8) != v


# ------------------------------------------------------------------------------------------------ concept-board styles
def test_styles_config_loads_and_checks():
    specs = V.load_variants("lattice_styles.toml")
    assert [s.name for s in specs] == ["s1_honeycomb", "s2_triangle", "s3_skeleton"]
    for s in specs:
        chk = V.check_variant(s, CFG, PROC)
        assert chk["printable"] and chk["yarn_diameter_min_mm"] >= 2.0
    assert specs[2].voxel_mm == 1.0 and specs[2].yarn_mm >= 5.0 and specs[2].pitch_mm >= 30.0


def test_coarse_voxel_is_judged_by_the_thinnest_feature():
    s = V.load_variants("lattice_styles.toml")[0]
    with pytest.raises(L.LatticeError, match="voxel"):
        V.check_variant(dataclasses.replace(s, voxel_mm=1.0), CFG, PROC)  # 2.4 mm rods need <= 0.6 mm
    assert not V.check_variant(dataclasses.replace(s, voxel_mm=1.0, preview=True), CFG, PROC)["printable"]
    thin_skin = dataclasses.replace(s, sole_skin_cavity_mm=1.5, voxel_mm=0.5)
    assert not V.check_variant(thin_skin, CFG, PROC)["printable"]  # a 1.5 mm skin needs <= 0.375 mm


def test_skeleton_style_with_loop_and_no_sole_builds_and_the_loop_is_attached(prep):
    s = dataclasses.replace(V.load_variants("lattice_styles.toml")[2], pitch_mm=20.0, yarn_mm=4.0, thickness_mm=5.0, fillet_mm=3.0, heel_loop_mm=22.0,
                            heel_loop_tube_mm=4.0, voxel_mm=1.0)
    fine, fg, rep, aux = V.build_variant(prep, s, CFG, PROC)
    L.fill_sealed(fine, fg)
    mesh, er = L.to_mesh(fine, fg, 200.0)
    assert mesh.is_watertight and mesh.body_count == 1 and er.dropped_volume_mm3 < 50
    assert rep["sole_zone"].startswith("none")


def test_voronoi_elongation_and_fillet_change_the_field(prep):
    base = spec(style="voronoi", pitch_mm=12.0, voxel_mm=0.3)
    a = V.build_variant(prep, base, CFG, PROC)[0]
    b = V.build_variant(prep, dataclasses.replace(base, elongate=1.6, fillet_mm=2.0), CFG, PROC)[0]
    assert not np.array_equal(a, b)
    assert (b < 0).sum() > (a < 0).sum() * 0.95  # a fillet only adds material at the junctions
