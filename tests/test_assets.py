import os
from pathlib import Path

import numpy as np
import pytest
import trimesh

from shoe_designer import assets
from shoe_designer.assets import AssetError, load_asset, load_catalog, rotation


def write_manifest(tmp_path, body):
    p = tmp_path / "manifest.toml"
    p.write_text(body, encoding="utf-8")
    return p


def row(**kw):
    base = dict(id="a", role="shoe", path="a.stl", source="t", license="t")
    base.update(kw)
    lines = ["[[asset]]"]
    for k, v in base.items():
        lines.append(f"{k} = {str(v).lower() if isinstance(v, bool) else repr(v).replace(chr(39), chr(34))}")
    return "\n".join(lines) + "\n"


def box(extents, offset):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(offset)
    return m


def test_rotation_is_proper_for_every_axis_pair():
    names = list(assets._AXES)
    for toe in names:
        for up in names:
            if toe[1] == up[1]:
                continue
            r = rotation(toe, up)
            assert np.allclose(r @ r.T, np.eye(3))
            assert np.isclose(np.linalg.det(r), 1.0), (toe, up)  # never mirrors the shoe


def test_rotation_rejects_same_axis():
    with pytest.raises(AssetError):
        rotation("+X", "-X")


def test_normalizes_toe_axis_and_origin(tmp_path):
    # length along source Y, toe at -Y; 200 long (y), 80 wide (x), 100 tall (z)
    m = box((80, 200, 100), (500, -300, 40))
    m.export(tmp_path / "a.stl")
    cat = load_catalog(write_manifest(tmp_path, row(toe="-Y", up="+Z")))
    loaded = load_asset("a", cat, tmp_path)
    assert np.allclose(loaded.mesh.extents, (200, 80, 100), atol=1e-3)
    lo, hi = loaded.mesh.bounds
    assert np.allclose([lo[0], lo[2]], [0, 0], atol=1e-3)
    assert np.isclose(lo[1], -hi[1], atol=1e-3)
    # the source point at the toe end (min y) must land at max canonical x
    toe_end = np.array([500, -400, 40, 1.0]) @ loaded.transform.T
    assert np.isclose(toe_end[0], 200, atol=1e-3)


def test_frame_of_keeps_region_registered_with_last(tmp_path):
    box((100, 280, 90), (0, 0, 50)).export(tmp_path / "last.stl")
    box((10, 10, 10), (0, 100, 50)).export(tmp_path / "region.stl")
    body = row(id="last", role="last", path="last.stl", toe="+Y") + row(id="reg", role="region", path="region.stl", frame_of="last")
    cat = load_catalog(write_manifest(tmp_path, body))
    last, region = load_asset("last", cat, tmp_path), load_asset("reg", cat, tmp_path)
    assert np.allclose(last.transform, region.transform)
    # region sits 100 mm toward the toe from the last's centre, so x = 140 + 100 -/+ 5
    assert np.isclose(region.mesh.centroid[0], 140 + 100, atol=1e-3)


def test_wrong_units_are_rejected(tmp_path):
    box((8, 20, 10), (0, 0, 0)).export(tmp_path / "a.stl")  # looks like cm
    cat = load_catalog(write_manifest(tmp_path, row(toe="+Y")))
    with pytest.raises(AssetError, match="units"):
        load_asset("a", cat, tmp_path)


def test_missing_file_and_unknown_id(tmp_path):
    cat = load_catalog(write_manifest(tmp_path, row()))
    with pytest.raises(AssetError, match="not found"):
        load_asset("a", cat, tmp_path)
    with pytest.raises(AssetError, match="unknown asset"):
        load_asset("zzz", cat, tmp_path)


def test_manifest_validation(tmp_path):
    with pytest.raises(AssetError, match="duplicate"):
        load_catalog(write_manifest(tmp_path, row() + row()))
    with pytest.raises(AssetError, match="role"):
        load_catalog(write_manifest(tmp_path, row(role="boot")))
    with pytest.raises(AssetError, match="toe"):
        load_catalog(write_manifest(tmp_path, row(toe="up")))
    with pytest.raises(AssetError, match="frame_of"):
        load_catalog(write_manifest(tmp_path, row(frame_of="nope")))


def test_missing_root_env(monkeypatch):
    monkeypatch.delenv(assets.ROOT_ENV, raising=False)
    with pytest.raises(AssetError, match=assets.ROOT_ENV):
        assets.assets_root()


def test_shipped_manifest_is_valid():
    cat = load_catalog()
    assert "sneaker_last" in cat and "slide_last" not in cat
    assert cat["sneaker_last"].commercial_use is False


REAL = os.environ.get(assets.ROOT_ENV)


@pytest.mark.skipif(not REAL or not (Path(REAL).expanduser() / "Sneaker - Zellerfeld - Template Files").is_dir(),
                    reason="real assets not available")
def test_real_assets_load_in_canonical_frame():
    cat = load_catalog()
    last = load_asset("sneaker_last", cat)
    assert last.mesh.is_watertight
    assert np.allclose(last.mesh.bounds[0][[0, 2]], 0, atol=1e-3)
    thickness = load_asset("sneaker_reference_thickness", cat)
    # region data must overlap the last it was authored for
    assert np.all(thickness.mesh.bounds[0] < last.mesh.bounds[1])
    assert np.all(thickness.mesh.bounds[1] > last.mesh.bounds[0])
