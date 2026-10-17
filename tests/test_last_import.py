"""Synthetic-only tests for last import, normalization and reporting (no real last files)."""

from __future__ import annotations

import hashlib
import json
import re

import numpy as np
import pytest
from build123d import Box, export_step, fillet
from scipy.spatial import cKDTree

from shoegen.last import CoordinateSystem, LastLoader, normalize_last
from shoegen.reporting import artifact_manifest, environment, sha256, write_json


def _rounded_box():
    box = Box(60, 30, 20)
    return fillet(box.edges(), 4)


@pytest.fixture
def box_step(tmp_path):
    path = tmp_path / "synthetic_rounded_box.step"
    export_step(_rounded_box(), path)
    return path


def _vertices(shape) -> np.ndarray:
    verts, _ = shape.tessellate(0.01, 0.05)
    return np.array([[v.X, v.Y, v.Z] for v in verts], dtype=float)


def test_import_rounded_box(box_step):
    loaded = LastLoader().load(box_step)
    expected = _rounded_box().volume
    assert loaded.shape.volume == pytest.approx(expected, rel=1e-6)
    src = loaded.source
    assert src["sha256"] == hashlib.sha256(box_step.read_bytes()).hexdigest()
    assert src["header_repaired_in_temporary_copy"] is False
    assert src["working_unit"] == "mm"
    assert src["license_status"] == "REQUIRES_PURCHASE_AUTHORIZATION_REVIEW"
    assert src["local_only"] is True


def test_broken_file_name_header_is_repaired_in_temp_copy(box_step):
    text = box_step.read_text(encoding="utf-8")
    record = "FILE_NAME(License: synthetic test asset; redistribution prohibited);"
    broken = re.sub(r"FILE_NAME\s*\((.*?)\);", record, text, count=1, flags=re.DOTALL)
    assert broken != text
    box_step.write_text(broken, encoding="utf-8")
    original = box_step.read_bytes()
    loaded = LastLoader().load(box_step)
    assert loaded.shape.volume == pytest.approx(_rounded_box().volume, rel=1e-6)
    assert box_step.read_bytes() == original  # source file untouched
    assert loaded.source["header_repaired_in_temporary_copy"] is True
    assert loaded.source["original_file_name_record"] == record
    assert loaded.source["license_notice"] == record
    assert loaded.source["license_status"] == "REQUIRES_PURCHASE_AUTHORIZATION_REVIEW"


def test_nonstep_input_is_rejected(tmp_path):
    path = tmp_path / "invalid.stp"
    path.write_text("not a STEP file", encoding="utf-8")
    with pytest.raises(ValueError, match="STEP DATA section is missing"):
        LastLoader().load(path)


def test_normalization_matches_direct_vertex_transform(box_step):
    loaded = LastLoader().load(box_step)
    # Non-trivial proper rotation: Rz(35 deg) @ Ry(-20 deg) @ Rx(50 deg).
    a, b, c = np.radians([50.0, -20.0, 35.0])
    rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    rz = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    rotation = rz @ ry @ rx
    translation = np.array([12.5, -33.0, 41.25])
    coords = CoordinateSystem(
        rotation=tuple(map(tuple, rotation)),
        translation_mm=tuple(translation),
        orientation_confirmed=True,
    )
    normalized, meta = normalize_last(loaded.shape, coords)

    assert np.allclose(np.array(meta["matrix_4x4"])[:3, :3], rotation)
    assert np.allclose(np.array(meta["matrix_4x4"])[:3, 3], translation)
    assert meta["orientation_status"] == "CONFIGURATION_CONFIRMED"
    assert normalized.volume == pytest.approx(loaded.shape.volume, rel=1e-6)
    assert normalized.is_valid

    expected = _vertices(loaded.shape) @ rotation.T + translation
    actual = _vertices(normalized)
    # Nearest-neighbour matching in both directions (tessellation may differ in ordering).
    d_fwd, _ = cKDTree(actual).query(expected)
    d_bwd, _ = cKDTree(expected).query(actual)
    assert max(d_fwd.max(), d_bwd.max()) <= 1e-4


def test_normalization_rejects_non_rigid_rotation():
    coords = CoordinateSystem(
        rotation=((2, 0, 0), (0, 1, 0), (0, 0, 1)), translation_mm=(0, 0, 0)
    )
    with pytest.raises(ValueError, match="orthonormal"):
        normalize_last(Box(1, 1, 1), coords)
    mirror = CoordinateSystem(rotation=((-1, 0, 0), (0, 1, 0), (0, 0, 1)), translation_mm=(0, 0, 0))
    with pytest.raises(ValueError, match="orthonormal"):
        normalize_last(Box(1, 1, 1), mirror)


def test_reporting_deterministic_and_measured(tmp_path):
    out = tmp_path / "sub" / "report.json"
    write_json(out, {"b": 2, "a": {"z": 1, "y": [3, 2]}})
    text = out.read_text()
    assert text.endswith("\n")
    assert list(json.loads(text)) == ["a", "b"]
    assert text.index('"y"') < text.index('"z"')  # sorted keys
    assert sha256(out) == hashlib.sha256(out.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        write_json(tmp_path / "nan.json", {"x": float("nan")})

    manifest = artifact_manifest(out.parent)
    assert manifest == {"report.json": {"bytes": out.stat().st_size, "sha256": sha256(out)}}

    env = environment()
    assert env["packages"]["numpy"] != "NOT_INSTALLED"
    assert env["process_peak_rss_bytes"] > 0
