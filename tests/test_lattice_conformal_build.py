"""#46 parts A and B on a synthetic last: LSCM chart, flow field, tube diameter on oblique / curved / concave regions, integrated sole."""
import dataclasses

import numpy as np
import pytest
from scipy import ndimage

from shoe_designer import lattice as L
from shoe_designer import lattice_conformal as C
from shoe_designer import lattice_lscm as LS
from shoe_designer import outline as O
from shoe_designer.lattice_upper import foot_frame
from shoe_designer.lattice_variants import shell_body_v
from shoe_designer.process import ProcessConfig
from test_outline import synthetic_prep

CFG = L.LatticeConfig.from_toml()
PROC = ProcessConfig.from_toml("config/process_sls_tpu.toml")


def spec(**kw):
    base = dict(name="t", style="voronoi", pitch_mm=14.0, elongate=1.4, fillet_mm=2.0, yarn_mm=2.4, thickness_mm=3.4, rim_mm=6.0, sole_mm=12.0, voxel_mm=0.8,
                preview=True, sole_mode="net", sole_cell_mm=24.0, sole_density=((0.0, 0.31), (1.0, 0.27)), sole_pitch_mm=14.0, sole_el=1.0, sole_pad_hole_pitch_mm=14.0,
                flow_cross_deg=10.0, flow_compress=(0.4, 2.5), seam_x_frac=0.3, lscm_target_faces=12000)
    base.update(kw)
    return C.ConformalSpec(**base)


@pytest.fixture(scope="module")
def built():
    prep = synthetic_prep()
    v = spec(outline=(tuple(sorted({"fn": "sole_wall", "sole_mm": 14.0, "flare_mm": 2.0, "bulge_mm": 2.0}.items())),
                      tuple(sorted({"fn": "cradle", "rocker_mm": 5.0, "lateral_mm": 2.0}.items()))))
    fine, fg, rep, aux = C.build_whole(prep, v, CFG, PROC)
    return prep, v, fine, fg, rep, aux


def test_lscm_chart_is_one_conformal_disk(built):
    prep, v, fine, fg, rep, aux = built
    assert rep["route"] == "lscm", rep["fallback_reason"]
    r = rep["lscm"]
    assert r["euler"] == 1
    assert r["anisotropy_median"] < 1.05 and r["anisotropy_p95"] < 1.1  # conformal; the few slivers of the marching-cubes mesh may spike
    assert 0.5 < r["area_factor_p05"] <= r["area_factor_p95"] < 4.0
    assert rep["coverage"]["uncovered_share"] < 0.08


def test_flow_compression_guard():
    prep = synthetic_prep()
    v = spec(flow_cross_deg=25.0, flow_compress=(0.999, 1.001))
    with pytest.raises(L.LatticeError):
        C.build_whole(prep, v, CFG, PROC)


def test_never_enters_the_cavity_and_single_watertight_body(built):
    prep, v, fine, fg, rep, aux = built
    rs = aux["rs"]
    c0f = np.empty(fine.shape, np.float32)
    for ix in range(fine.shape[0]):
        c0f[ix] = rs.plane(prep.c0, ix)
    assert ((fine < 0) & (c0f < -0.5)).sum() == 0
    safety = L.fill_sealed(fine, fg) if L.sealed(fine, fg)["count"] else {"filled_mm3": 0.0}
    assert safety["filled_mm3"] < 100.0  # only voxel-level bubbles at the zone edges, filled by the safety net as in #14
    assert L.sealed(fine, fg)["count"] == 0
    mesh, _ = L.to_mesh(fine, fg, 400.0)
    chk = L.export_checks(mesh)
    assert chk["watertight"] and chk["bodies"] == 1 and chk["inner_shells"]["count"] == 0


def test_rod_diameter_on_curved_and_concave_regions(built):
    """The measured skeleton diameter stays >= set - 1 voxel on the whole yarn zone of a last with a waist (concave) and a rounded toe (curved)."""
    prep, v, fine, fg, rep, aux = built
    veff = aux["spec"]
    region, band = C.yarn_zone_masks(prep, aux, veff, fg, aux["rs"])
    r = C.rod_diameters(fine < 0, fg.h, region, target=veff.yarn_mm)
    assert r["n"] > 100
    assert r["median"] >= veff.yarn_mm - fg.h
    assert r["frac_below_target_minus_1_voxel"] < 0.08  # the measurement has skeleton spurs; the bulk must be there
    # oblique rods: the same rods measured on the flow variant with a strong lean
    assert rep["yarn_diameter_used_mm"][0] >= veff.yarn_mm - 1e-6


def test_sole_has_pad_plate_with_holes_open_cells_and_no_platform(built):
    prep, v, fine, fg, rep, aux = built
    veff, rs = aux["spec"], aux["rs"]
    z = rs.z
    g = prep.grid
    i = int(np.argmin(np.abs(rs.x - (prep.axis["x_min"] + 0.5 * (prep.axis["x_max"] - prep.axis["x_min"])))))
    X2, Y2 = np.meshgrid(rs.x[i : i + 1], rs.y, indexing="ij")
    fl = ndimage.map_coordinates(aux["floor"], [(X2 - g.origin[0]) / g.h, (Y2 - g.origin[1]) / g.h], order=1, mode="nearest")[0]
    jc = int(np.argmin(np.abs(rs.y)))
    zpad = fl[jc] - 0.5 * veff.sole_pad_mm
    k = int(np.argmin(np.abs(z - zpad)))
    plate = fine[i, :, k] < 0
    assert plate.sum() > 0.5 * (fine[i, :, k] < 1).sum()  # mostly solid plate under the foot at the middle of the plate
    assert rep["sole_net"]["thinnest_mm"] >= 1.2 and rep["sole_zone"]["fill_fraction"] < 0.6  # not a solid platform


def test_sole_features_below_minimum_are_refused():
    prep = synthetic_prep()
    with pytest.raises(L.LatticeError):
        C.build_whole(prep, spec(sole_density=((0.0, 0.05), (1.0, 0.05))), CFG, PROC)
