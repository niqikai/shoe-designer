"""Real asset acceptance. Primitive tests never stand in for these checks."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from shoe_poc.last import LastLoader, analyze_shape, normalize_last
from shoe_poc.schemas import ShoeSpec

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope="module")
def spec():
    result = ShoeSpec.load(ROOT / "input/specs/shoe_spec.json")
    if not Path(result.last.file).exists():
        pytest.skip("Real licensed STEP asset unavailable; real geometry NOT_TESTED")
    return result


@pytest.fixture(scope="module")
def loaded(spec):
    return LastLoader().load(spec.last.file)


@pytest.fixture(scope="module")
def normalized(loaded, spec):
    return normalize_last(loaded.shape, spec.last.coordinate_system)[0]


@pytest.fixture(scope="module")
def built(normalized, spec):
    from shoe_poc.geometry.assembler import ShoeAssembler
    return ShoeAssembler().build(normalized, spec)


def test_T01_real_step_import(loaded):
    assert len(loaded.shape.solids()) == 1
    assert loaded.shape.is_valid
    assert loaded.source["working_unit"] == "mm"


def test_T02_real_geometric_analysis(loaded):
    metadata = analyze_shape(loaded.shape)
    assert metadata["face_count"] > 0
    assert metadata["volume_mm3"] > 0
    assert metadata["open_shell_count"] == 0
    assert all(size > 0 for size in metadata["bbox_mm"]["size"])
    assert metadata["side"] == "NOT_INFERRED"


def test_T03_real_normalization_preserves_geometry(loaded, normalized, spec):
    assert normalized.volume == pytest.approx(loaded.shape.volume, rel=1e-8)
    rotation = np.asarray(spec.last.coordinate_system.rotation)
    translation = np.asarray(spec.last.coordinate_system.translation_mm)
    source_points = np.array([tuple(vertex.center()) for vertex in loaded.shape.vertices()])
    expected = source_points @ rotation.T + translation
    actual = np.array([tuple(vertex.center()) for vertex in normalized.vertices()])
    # Independent distances avoid relying on vertex enumeration order after OCCT transform.
    from scipy.spatial import cKDTree
    distances, _ = cKDTree(actual).query(expected)
    assert np.max(distances) < 1e-6
    assert normalized.is_valid


def test_T05_cavity_excludes_real_last(built, normalized):
    from shoe_poc.geometry.assembler import volume_of
    assert volume_of(built.shape.intersect(normalized)) <= 0.01
    assert volume_of(built.shape.intersect(built.cavity)) <= 0.01
    assert built.cavity.volume > 0
    assert built.metadata["cavity_entry_intersection_volume_mm3"] > 1


def test_T06_upper_is_solid_and_entry_open(built):
    from shoe_poc.geometry.assembler import volume_of
    assert built.upper.is_valid
    assert volume_of(built.upper) > 100
    assert volume_of(built.shape.intersect(built.opening)) <= 0.01
    assert built.metadata["opening_removed_envelope_volume_mm3"] > 1
    assert built.shape.is_valid
    assert len(built.shape.solids()) == 1


def test_T07_sole_height_changes_actual_solid(normalized, spec):
    from shoe_poc.geometry.sole import SoleGenerator
    alternate = spec.model_copy(deep=True)
    alternate.sole.height_mm = spec.sole.height_mm + 5
    first, _ = SoleGenerator().generate(normalized, spec)
    second, _ = SoleGenerator().generate(normalized, alternate)
    assert second.volume > first.volume
    assert second.bounding_box().size.Z - first.bounding_box().size.Z == pytest.approx(5, abs=1e-6)
    assert first.bounding_box().min.Z - second.bounding_box().min.Z == pytest.approx(5, abs=1e-6)


def test_T16_step_stl_roundtrip_real_geometry(built, tmp_path):
    from build123d import export_step, import_step
    from shoe_poc.exporting import export_mesh
    from shoe_poc.validation import validate_mesh
    from shoe_poc.validation.volume import measure_cad_volume
    export_step(built.shape, tmp_path / "shoe.step")
    serialization = export_mesh(built.shape, tmp_path / "shoe.stl", tolerance=0.15, angular_tolerance=0.1)
    assert serialization["discarded_exact_zero_area_records"] >= 0
    assert serialization["retained_triangle_count"] == (serialization["native_triangle_count"]
                                                       - serialization["discarded_exact_zero_area_records"])
    assert serialization["positive_area_triangles_preserved_byte_for_byte"] is True
    assert serialization["area_tolerance"] == 0
    step = import_step(tmp_path / "shoe.step")
    assert step.is_valid and len(step.solids()) == 1
    source_volume = measure_cad_volume(built.shape)
    step_volume = measure_cad_volume(step)
    (tmp_path / "volume_roundtrip_measurements.json").write_text(json.dumps(
        {"source": source_volume, "step": step_volume, "relative_tolerance": 1e-5}, indent=2))
    assert step_volume["volume_mm3"] == pytest.approx(source_volume["volume_mm3"], rel=1e-5)
    mesh = validate_mesh(tmp_path / "shoe.stl")
    assert mesh["geometry_status"] == "PASS"
    assert mesh["euler_number"] == 2
    assert next(c for c in mesh["checks"] if c["name"] == "minimum_wall_thickness")["status"] == "NOT_VERIFIED"
    assert np.allclose(mesh["extents_mm"], list(built.shape.bounding_box().size), atol=0.3, rtol=0)


def test_T19_real_headless_cli_M1_end_to_end(spec, built, tmp_path):
    # Measured native CAD generation (~65 s), the independent 512-site B-Rep
    # probe (~224 s), STEP/STL roundtrips and rendering exceed the old 300 s
    # allowance. Keep the full validation contract and allow a bounded 600 s.
    result = subprocess.run([sys.executable, "-m", "shoe_poc", "run", "--spec",
                             str(ROOT / "input/specs/shoe_spec.json"), "--output", str(tmp_path)],
                            capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for filename in ("last_metadata.json", "last_normalized.step", "shoe_spec.json", "base_shoe.step",
                     "base_shoe.stl", "preview.png", "execution_report.json", "roundtrip_report.json"):
        assert (tmp_path / filename).stat().st_size > 0
    report = json.loads((tmp_path / "execution_report.json").read_text())
    assert report["status"] == "PASS"
    assert report["milestone_status"] == "PARTIAL"
    surface = json.loads((tmp_path / "surface_report.json").read_text())
    assert surface["geometry_status"] == "PASS"
    geometry = json.loads((tmp_path / "geometry_report.json").read_text())
    assert geometry["junction"]["geometry_status"] == "PASS"
    roundtrip = json.loads((tmp_path / "roundtrip_report.json").read_text())
    assert roundtrip["status"] == "PASS"
    validation = json.loads((tmp_path / "validation_report.json").read_text())
    assert validation["geometry_status"] == "PASS"
    assert validation["manufacturing_status"] == "NOT_VERIFIED"
    saved = ShoeSpec.load(tmp_path / "shoe_spec.json")
    assert saved.last.file == spec.last.file
    # Compare two independent builds after the same STEP export/reimport
    # protocol so serialization error is not mistaken for build variation.
    # T16 separately preserves the source-to-STEP relative tolerance of 1e-5.
    from build123d import export_step, import_step
    export_step(built.shape, tmp_path / "fixture_repeat.step")
    first_reloaded = import_step(tmp_path / "fixture_repeat.step")
    repeated = import_step(tmp_path / "base_shoe.step")
    from shoe_poc.validation.volume import measure_cad_volume
    measurements = {}
    for reference in ("point", "xy_plane"):
        first = measure_cad_volume(first_reloaded, reference=reference, refine=True)
        second = measure_cad_volume(repeated, reference=reference, refine=True)
        measurements[reference] = {"first": first, "second": second,
                                   "relative_tolerance": 1e-8}
    measurements["absolute_protocol_difference_mm3"] = {
        stage: abs(measurements["point"][stage]["volume_mm3"]
                   - measurements["xy_plane"][stage]["volume_mm3"])
        for stage in ("first", "second")}
    (tmp_path / "volume_repeatability_measurements.json").write_text(json.dumps(measurements, indent=2))
    for reference in ("point", "xy_plane"):
        pair = measurements[reference]
        assert pair["second"]["volume_mm3"] == pytest.approx(pair["first"]["volume_mm3"], rel=1e-8)
    assert np.allclose(list(repeated.bounding_box().min), list(built.shape.bounding_box().min), atol=1e-6, rtol=0)
    assert np.allclose(list(repeated.bounding_box().max), list(built.shape.bounding_box().max), atol=1e-6, rtol=0)
