"""Synthetic algorithm tests; boxes/surfaces are not real shoe acceptance evidence."""

import hashlib
import re
from types import SimpleNamespace

from build123d import Box, Compound, Shell, export_step
import numpy as np
import pytest

from shoe_poc.last.analyzer import analyze_shape
from shoe_poc.last.loader import LastLoader
from shoe_poc.last.normalizer import normalize_last


@pytest.fixture
def box_step(tmp_path):
    path = tmp_path / "synthetic_box.step"
    export_step(Box(10, 20, 30), path)
    return path


def test_valid_closed_step_import_and_measured_analysis(box_step):
    loaded = LastLoader().load(box_step)
    measured = analyze_shape(loaded.shape)
    assert measured["solid_count"] == 1
    assert measured["shell_count"] == 1
    assert measured["face_count"] == 6
    assert measured["edge_count"] == 12
    assert measured["vertex_count"] == 8
    assert measured["volume_mm3"] == pytest.approx(6000)
    assert measured["surface_area_mm2"] == pytest.approx(2200)
    assert measured["bbox_mm"]["size"] == pytest.approx([10, 20, 30])
    assert measured["bbox_mm"]["min"] == pytest.approx([-5, -10, -15])
    assert measured["bbox_mm"]["max"] == pytest.approx([5, 10, 15])
    assert measured["topology_valid"] is True
    assert measured["open_shell_count"] == 0
    assert measured["abnormal_geometry"] == []
    assert measured["shoe_size"] == "NOT_INFERRED"
    assert measured["side"] == "NOT_INFERRED"
    assert measured["toe_direction"] == "CONFIGURATION_REQUIRED"
    raw = box_step.read_bytes()
    assert loaded.source["sha256"] == hashlib.sha256(raw).hexdigest()
    assert loaded.source["size_bytes"] == len(raw)
    assert loaded.source["working_unit"] == "mm"
    assert loaded.source["source_length_unit_prefixes"] == [".MILLI."]
    assert loaded.source["header_repaired_in_temporary_copy"] is False
    assert loaded.source["local_only"] is True


def test_nonstep_input_is_rejected(tmp_path):
    path = tmp_path / "invalid.stp"
    path.write_text("This is not a STEP file.", encoding="utf-8")
    with pytest.raises(ValueError, match="STEP DATA section is missing"):
        LastLoader().load(path)


def test_missing_asset_is_not_masked(tmp_path):
    with pytest.raises(FileNotFoundError):
        LastLoader().load(tmp_path / "missing.step")


def test_absent_length_unit_is_rejected(box_step):
    # Corrupt only length-unit metadata, keeping the generated B-Rep data real.
    raw = box_step.read_text(encoding="utf-8")
    changed = raw.replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT(.MILLI.,.RADIAN.)")
    assert changed != raw
    box_step.write_text(changed, encoding="utf-8")
    with pytest.raises(ValueError, match="STEP length units could not be established"):
        LastLoader().load(box_step)


def test_multiple_solids_are_rejected(tmp_path):
    path = tmp_path / "two_solids.step"
    export_step(Compound([Box(10, 10, 10), Box(5, 5, 5).translate((20, 0, 0))]), path)
    with pytest.raises(ValueError, match="Expected one shoe-last solid, found 2"):
        LastLoader().load(path)


def test_open_shell_is_detected_and_rejected(tmp_path):
    # Five box faces form an open shell, unlike a closed last solid.
    open_shell = Shell(Box(10, 20, 30).faces()[:-1])
    measured = analyze_shape(open_shell)
    assert measured["solid_count"] == 0
    assert measured["open_shell_count"] == 1
    assert measured["abnormal_geometry"]
    path = tmp_path / "open_shell.step"
    export_step(open_shell, path)
    with pytest.raises(ValueError, match="Expected one shoe-last solid, found 0"):
        LastLoader().load(path)


def test_header_repair_is_local_and_preserves_source_license(box_step):
    source = box_step.read_text(encoding="utf-8")
    license_record = "FILE_NAME(License: paid synthetic test asset; redistribution prohibited);"
    malformed = re.sub(r"FILE_NAME\s*\((.*?)\);", license_record, source, count=1, flags=re.S)
    assert malformed != source
    box_step.write_text(malformed, encoding="utf-8")
    original = box_step.read_bytes()
    loaded = LastLoader().load(box_step)
    assert loaded.shape.volume == pytest.approx(6000)
    assert box_step.read_bytes() == original
    assert loaded.source["header_repaired_in_temporary_copy"] is True
    assert loaded.source["original_file_name_record"] == license_record
    assert loaded.source["license_notice"] == license_record
    assert loaded.source["license_status"] == "REQUIRES_PURCHASE_AUTHORIZATION_REVIEW"
    assert loaded.source["sha256"] == hashlib.sha256(original).hexdigest()
    assert loaded.source["path"] == str(box_step.resolve())
    assert loaded.source["local_only"] is True


@pytest.mark.parametrize("confirmed", [False, True])
def test_rigid_normalization_transforms_geometry_and_records_uncertainty(confirmed):
    original = Box(2, 4, 6)
    rotation = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    translation = [10, 20, 30]
    coordinates = SimpleNamespace(rotation=rotation, translation_mm=translation, orientation_confirmed=confirmed)
    transformed, metadata = normalize_last(original, coordinates)
    measured = analyze_shape(transformed)
    assert measured["bbox_mm"]["min"] == pytest.approx([8, 19, 27])
    assert measured["bbox_mm"]["max"] == pytest.approx([12, 21, 33])
    assert measured["bbox_mm"]["size"] == pytest.approx([4, 2, 6])
    assert measured["volume_mm3"] == pytest.approx(original.volume)
    assert measured["topology_valid"] is True
    expected_points = {tuple(np.round(np.asarray(rotation) @ np.asarray(tuple(v)) + translation, 7)) for v in original.vertices()}
    actual_points = {tuple(np.round(tuple(v), 7)) for v in transformed.vertices()}
    assert actual_points == expected_points
    assert list(original.bounding_box().size) == pytest.approx([2, 4, 6])
    assert metadata["matrix_4x4"] == [[0, -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 30], [0, 0, 0, 1]]
    assert metadata["orientation_confirmed"] is confirmed
    assert metadata["orientation_status"] == (
        "CONFIGURATION_CONFIRMED" if confirmed else "PROVISIONAL_REQUIRES_USER_CONFIRMATION"
    )


@pytest.mark.parametrize(
    "rotation",
    [
        [[1, 0], [0, 1]],
        [[2, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[1, 0.1, 0], [0, 1, 0], [0, 0, 1]],
        # This small scale exceeds the schema's absolute 1e-6 tolerance.
        [[1.000003, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[float("nan"), 0, 0], [0, 1, 0], [0, 0, 1]],
    ],
)
def test_invalid_normalization_rotation_is_rejected(rotation):
    coordinates = SimpleNamespace(rotation=rotation, translation_mm=[0, 0, 0], orientation_confirmed=False)
    with pytest.raises(ValueError, match="proper orthonormal 3x3"):
        normalize_last(Box(2, 4, 6), coordinates)


@pytest.mark.parametrize("translation", [[0, 0], [0, 0, 0, 0], [float("nan"), 0, 0], [0, float("inf"), 0]])
def test_invalid_normalization_translation_is_rejected(translation):
    coordinates = SimpleNamespace(rotation=np.eye(3), translation_mm=translation, orientation_confirmed=False)
    with pytest.raises(ValueError, match="finite 3-vector"):
        normalize_last(Box(2, 4, 6), coordinates)
