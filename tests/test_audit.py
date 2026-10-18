import numpy as np
import pytest
import trimesh

from shoe_designer.assets import Asset, Loaded
from shoe_designer import audit


def hollow(outer, wall, name="shoe"):
    """Closed shell with an enclosed void: outward outer box + inward-facing inner box."""
    o = trimesh.creation.box(extents=outer)
    i = trimesh.creation.box(extents=[e - 2 * wall for e in outer])
    i.invert()
    m = trimesh.util.concatenate([o, i])
    m.apply_translation([outer[0] / 2, 0, outer[2] / 2])  # x from 0, z from 0
    return m


def loaded(mesh, role="shoe", asset_id="t"):
    a = Asset(id=asset_id, role=role, path="x", source="t", license="t")
    return Loaded(asset=a, mesh=mesh, transform=np.eye(4))


def test_wall_thickness_of_known_shell():
    t = audit.wall_thickness(hollow((100, 60, 40), 2.0), n=4000)
    assert t["hit_fraction"] > 0.99
    assert t["median_mm"] == pytest.approx(2.0, abs=0.1)
    assert t["frac_below_1.2mm"] == 0.0


def test_thin_wall_is_flagged():
    t = audit.wall_thickness(hollow((100, 60, 40), 0.8), n=4000)
    assert t["frac_below_1.2mm"] > 0.9


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
    assert f["seats"] and f["collision_frac"] == 0.0
    assert f["seat_dz_mm"] == 4.0  # 3 mm floor, 2 mm search step: first free height
    assert f["clearance_min_mm"] == pytest.approx(1.0, abs=0.3)  # last rests ~1 mm above the floor
    assert f["clearance_median_mm"] > 3.0  # sides have ~7 mm gap


def test_oversized_last_does_not_seat():
    shoe = hollow((140, 80, 60), 3.0)
    last = trimesh.creation.box(extents=(140, 80, 30))
    last.apply_translation([70, 0, 15])
    f = audit.fit_to_last(last, shoe, n=1500)
    assert not f["seats"]
    assert f["length_delta_mm"] == 0.0
    assert any(k.startswith("best_seat") for k in f)


def test_audit_asset_skips_shoe_metrics_for_lasts():
    r = audit.audit_asset(loaded(trimesh.creation.box(extents=(200, 80, 100)), role="last"))
    assert "thickness" not in r and "fit_to_last" not in r
