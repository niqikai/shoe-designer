import numpy as np
import pytest
import trimesh

from shoe_designer.assets import Asset, Loaded
from helpers import hollow
from shoe_designer import audit


def loaded(mesh, role="shoe", asset_id="t"):
    a = Asset(id=asset_id, role=role, path="x", source="t", license="t")
    return Loaded(asset=a, mesh=mesh, transform=np.eye(4))


def test_wall_thickness_of_known_shell():
    t = audit.wall_thickness(hollow((100, 60, 40), 2.0), n=4000)
    assert t["hit_fraction"] > 0.99
    assert t["median_mm"] == pytest.approx(2.0, abs=0.1)
    assert t["frac_thin"] == 0.0


def test_thin_wall_is_flagged():
    t = audit.wall_thickness(hollow((100, 60, 40), 0.8), n=4000)
    assert t["frac_thin"] > 0.9


def test_overhang_counts_void_ceiling_as_enclosed_and_ignores_bed_face():
    o = audit.overhangs(hollow((100, 60, 40), 2.0))
    # the only steep down-facing face besides the bed contact is the void's ceiling
    assert o["enclosed_frac"] > 0.05
    assert o["open_frac"] == 0.0


def test_open_overhang_is_open():
    plate = trimesh.creation.box(extents=(100, 60, 2))
    plate.apply_translation([50, 0, 21])  # floating plate: nothing below it
    o = audit.overhangs(plate)
    assert o["open_frac"] > 0.3 and o["enclosed_frac"] == 0.0


def test_geometry_counts_bodies():
    a = trimesh.creation.box(extents=(10, 10, 10))
    b = trimesh.creation.box(extents=(10, 10, 10))
    b.apply_translation([30, 0, 0])
    g = audit.geometry(trimesh.util.concatenate([a, b]))
    assert g["bodies"] == 2 and g["watertight"]


def test_last_seats_in_roomy_cavity_with_clearance():
    shoe = hollow((140, 80, 60), 3.0)
    last = trimesh.creation.box(extents=(120, 60, 30))
    last.apply_translation([60, 0, 15])
    f = audit.fit_to_last(last, shoe, n=1500)
    assert f["seats"] and f["last_in_shoe_frac"] == 0.0 and f["volume_overlap_frac"] == 0.0
    assert f["dz_mm"] == 3.0  # last rests on the 3 mm floor
    assert abs(f["clearance_min_mm"]) <= 0.5  # touching the floor
    assert f["clearance_median_mm"] > 3.0  # sides have ~7 mm gap


def test_oversized_last_does_not_seat():
    shoe = hollow((140, 80, 60), 3.0)
    last = trimesh.creation.box(extents=(140, 80, 30))
    last.apply_translation([70, 0, 15])
    f = audit.fit_to_last(last, shoe, n=1500)
    assert not f["seats"]
    assert f["length_delta_mm"] == 0.0
    assert f["best_low_seat"]["last_in_shoe_frac"] > 0
    assert set(f["best_low_seat"]["collision_breakdown"]["by_third"]) <= {"heel", "mid", "toe"}


def test_audit_asset_skips_shoe_metrics_for_lasts():
    r = audit.audit_asset(loaded(trimesh.creation.box(extents=(200, 80, 100)), role="last"))
    assert "thickness" not in r and "fit_to_last" not in r


def test_wall_thickness_with_no_ray_hits_does_not_crash():
    plane = trimesh.Trimesh(vertices=[[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], faces=[[0, 1, 2], [0, 2, 3]])
    t = audit.wall_thickness(plane, n=200)
    assert t["hit_fraction"] == 0.0 and t["median_mm"] is None and t["frac_thin"] is None


def test_wall_thickness_is_measured_from_the_surface_not_the_ray_origin():
    t = audit.wall_thickness(hollow((100, 60, 40), 1.22), n=3000)
    assert t["min_mm"] >= 1.2 and t["frac_thin"] == 0.0  # was recorded as 1.17 mm before the offset fix


def test_non_watertight_shoe_is_not_fitted():
    shoe = hollow((140, 80, 60), 3.0)
    shoe.update_faces(np.arange(len(shoe.faces)) != 0)  # punch a hole
    last = trimesh.creation.box(extents=(120, 60, 30))
    f = audit.fit_to_last(last, shoe, n=500)
    assert f["seats"] is None and "watertight" in f["reason"]
    assert f["width_ratio"] == pytest.approx(80 / 60, abs=1e-3)


def test_thin_internal_plate_between_samples_blocks_seating():
    shoe = trimesh.util.concatenate([hollow((140, 80, 60), 3.0)])
    plate = trimesh.creation.box(extents=(1.0, 74, 40))  # 1 mm wall across the whole cavity
    plate.apply_translation([70, 0, 3 + 20])
    shoe = trimesh.util.concatenate([shoe, plate])
    last = trimesh.creation.box(extents=(120, 60, 30))
    last.apply_translation([60, 0, 15])
    f = audit.fit_to_last(last, shoe, n=1500)
    assert f["seats"] is False
    assert f["best_low_seat"]["volume_overlap_frac"] > 0 or f["best_low_seat"]["last_in_shoe_frac"] > 0


def test_lateral_offset_cavity_is_found_by_dy_search():
    shoe = hollow((140, 70, 60), 3.0)
    shoe.apply_translation([0, 4.0, 0])  # cavity centre 4 mm off the last's
    last = trimesh.creation.box(extents=(120, 60, 30))
    last.apply_translation([60, 0, 15])
    f = audit.fit_to_last(last, shoe, n=1500)
    assert f["seats"] is True and f["dy_mm"] > 0


def test_clearance_is_signed():
    shoe = hollow((140, 80, 60), 3.0)
    last = trimesh.creation.box(extents=(120, 60, 30))
    last.apply_translation([60, 0, 15])
    f = audit.fit_to_last(last, shoe, n=1500, tol=0.2)  # permissive: allow some penetration
    assert f["clearance_min_mm"] <= f["clearance_median_mm"]
    assert f["clearance_resolution_mm"] > 0


def row(series, size, length, wall):
    return {"series": series, "size_eu": size, "thickness": {"median_mm": wall},
            "geometry": {"extents_mm": [length, 100, 100]}}


def test_size_inference_interpolates_between_known_sizes():
    last = trimesh.creation.box(extents=(276.7, 100, 130))
    inf = audit.infer_last_size(last, [row("a", 43, 281.0, 2.6), row("a", 44, 287.0, 2.6)])
    (s,) = inf["series"]
    assert s["estimate_eu"] == pytest.approx(43 + (276.7 - 275.8) / 6.0, abs=0.01)
    assert not s["extrapolated"] and inf["confirmed"] is False
    assert inf["range_eu"][0] == inf["range_eu"][1]


def test_size_inference_needs_two_sizes_and_flags_extrapolation():
    last = trimesh.creation.box(extents=(276.7, 100, 130))
    assert audit.infer_last_size(last, [row("a", 43, 281.0, 2.6)])["range_eu"] is None
    far = audit.infer_last_size(last, [row("a", 36, 245.0, 2.6), row("a", 37, 251.0, 2.6)])
    assert far["series"][0]["extrapolated"]
