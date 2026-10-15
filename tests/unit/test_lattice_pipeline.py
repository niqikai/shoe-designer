"""M2 provenance and preflight checks without importing or rebuilding native CAD."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from shoe_poc.lattice.pipeline import GENERATED, accepted_baseline
from shoe_poc.schemas import ShoeSpec


ROOT = Path(__file__).resolve().parents[2]
M2_SPEC = ROOT / "input/specs/shoe_spec_m2.json"
APPROVAL_FILES = ("base_shoe.step", "base_shoe.stl", "shoe_spec.json", "preview.png", "toe_comparison.png")
SUPPORT_FILES = ("cavity.step", "last_normalized.step", "last_metadata.json", "geometry_report.json")


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def record(path):
    content = path.read_bytes()
    return {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


@pytest.fixture
def frozen_fixture(tmp_path):
    """Small bound bytes stand in for CAD; the gate must never import them."""
    baseline = tmp_path / "frozen"
    baseline.mkdir()
    source = (tmp_path / "purchased_source.step").resolve()
    source.write_bytes(b"test source bytes, deliberately not a native STEP model")
    data = ShoeSpec.load(M2_SPEC).model_dump(mode="json")
    data["last"]["file"] = str(source)
    for name in (*APPROVAL_FILES, *SUPPORT_FILES):
        (baseline / name).write_bytes(f"bound fixture bytes: {name}".encode())
    write_json(baseline / "shoe_spec.json", data)
    execution = {"status": "PASS", "run_id": "frozen-test-run", "input_sha256": record(source)["sha256"],
                 "outputs": {name: record(baseline / name) for name in SUPPORT_FILES}}
    write_json(baseline / "execution_report.json", execution)
    acceptance = tmp_path / "approval.json"
    approval = {"status": "ACCEPTED", "scope": "M1_base_shape", "run_id": execution["run_id"],
                "source_sha256": record(source)["sha256"],
                "files": {name: record(baseline / name) for name in APPROVAL_FILES}}
    write_json(acceptance, approval)
    write_json(acceptance.with_suffix(".m2_inputs.json"), {
        "scope": "M2_immutable_CAD_inputs", "accepted_run_id": execution["run_id"],
        "acceptance_sha256": record(acceptance)["sha256"],
        "files": {name: record(baseline / name) for name in ("execution_report.json", *SUPPORT_FILES)},
    })
    return baseline, ShoeSpec.model_validate(data), acceptance


@pytest.mark.real_last
def test_actual_frozen_m1_baseline_passes_without_cad_rebuild():
    baseline = ROOT / "output/m1_toe_refine"
    if not (baseline / "base_shoe.step").is_file() or not (ROOT / "input/lasts/sneaker_last.stp").is_file():
        pytest.skip("Requires locally licensed, accepted M1 artifacts")
    approval_path = ROOT / "docs/milestone1_shape_acceptance.json"
    approval = json.loads(approval_path.read_text())
    before = {name: record(baseline / name) for name in (*APPROVAL_FILES, *SUPPORT_FILES, "execution_report.json")}
    result = accepted_baseline(baseline, ShoeSpec.load(M2_SPEC), approval_path)
    assert result["accepted_run_id"] == approval["run_id"]
    assert result["source_sha256"] == approval["source_sha256"]
    assert result["files"]["base_shoe.step"] == approval["files"]["base_shoe.step"]
    assert before == {name: record(baseline / name) for name in before}


def test_lightweight_bound_fixture_is_accepted(frozen_fixture):
    baseline, spec, acceptance = frozen_fixture
    result = accepted_baseline(baseline, spec, acceptance)
    assert result["accepted_run_id"] == "frozen-test-run"
    assert result["files"]["base_shoe.step"] == record(baseline / "base_shoe.step")


@pytest.mark.parametrize("name", (*APPROVAL_FILES, *SUPPORT_FILES))
@pytest.mark.parametrize("change_size", [False, True])
def test_changed_bound_artifact_is_rejected(frozen_fixture, name, change_size):
    baseline, spec, acceptance = frozen_fixture
    path = baseline / name
    content = path.read_bytes()
    path.write_bytes(content + b" extra" if change_size else bytes([content[0] ^ 1]) + content[1:])
    with pytest.raises(ValueError, match="changed"):
        accepted_baseline(baseline, spec, acceptance)


@pytest.mark.parametrize("name", ("preview.png", "cavity.step"))
def test_missing_bound_artifact_is_rejected(frozen_fixture, name):
    baseline, spec, acceptance = frozen_fixture
    (baseline / name).unlink()
    with pytest.raises(ValueError, match="changed"):
        accepted_baseline(baseline, spec, acceptance)


@pytest.mark.parametrize("change", [
    {"status": "PENDING"}, {"status": None}, {"scope": "manufacturing"}, {"scope": None},
])
def test_missing_explicit_shape_acceptance_is_rejected(frozen_fixture, change):
    baseline, spec, acceptance = frozen_fixture
    approval = json.loads(acceptance.read_text())
    approval.update(change)
    write_json(acceptance, approval)
    with pytest.raises(ValueError, match="explicit M1 base-shape acceptance"):
        accepted_baseline(baseline, spec, acceptance)


def test_missing_acceptance_file_is_rejected(frozen_fixture):
    baseline, spec, acceptance = frozen_fixture
    acceptance.unlink()
    with pytest.raises(FileNotFoundError):
        accepted_baseline(baseline, spec, acceptance)


def test_missing_immutable_input_lock_is_rejected(frozen_fixture):
    baseline, spec, acceptance = frozen_fixture
    acceptance.with_suffix(".m2_inputs.json").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        accepted_baseline(baseline, spec, acceptance)


@pytest.mark.parametrize("name", SUPPORT_FILES)
def test_support_artifact_cannot_be_rebound_by_editing_execution(frozen_fixture, name):
    baseline, spec, acceptance = frozen_fixture
    (baseline / name).write_bytes(b"different support data with freshly rebound execution hash")
    path = baseline / "execution_report.json"
    execution = json.loads(path.read_text())
    execution["outputs"][name] = record(baseline / name)
    write_json(path, execution)
    with pytest.raises(ValueError):
        accepted_baseline(baseline, spec, acceptance)


def test_approval_must_bind_every_required_artifact(frozen_fixture):
    baseline, spec, acceptance = frozen_fixture
    approval = json.loads(acceptance.read_text())
    approval["files"].pop("base_shoe.stl")
    write_json(acceptance, approval)
    with pytest.raises(ValueError, match="bind all required"):
        accepted_baseline(baseline, spec, acceptance)


@pytest.mark.parametrize("change", [{"status": "FAIL"}, {"run_id": "another-run"}])
def test_baseline_execution_must_identify_accepted_passing_run(frozen_fixture, change):
    baseline, spec, acceptance = frozen_fixture
    path = baseline / "execution_report.json"
    execution = json.loads(path.read_text())
    execution.update(change)
    write_json(path, execution)
    with pytest.raises(ValueError):
        accepted_baseline(baseline, spec, acceptance)


@pytest.mark.parametrize(("section", "field", "value"), [
    ("last", "side", "left"), ("shoe", "size", "42"),
    ("upper", "clearance_mm", 1.2), ("sole", "height_mm", 17.),
    ("manufacturing", "material_profile", {"id": "another-material", "material": "TPU", "status": "EXPERIMENTAL"}),
])
def test_geometry_and_frozen_design_section_changes_are_rejected(frozen_fixture, section, field, value):
    baseline, spec, acceptance = frozen_fixture
    data = spec.model_dump(mode="json")
    data[section][field] = value
    changed = ShoeSpec.model_validate(data)
    with pytest.raises(ValueError, match=f"frozen M1 {section}"):
        accepted_baseline(baseline, changed, acceptance)


def test_lattice_only_changes_are_permitted(frozen_fixture):
    baseline, spec, acceptance = frozen_fixture
    data = spec.model_dump(mode="json")
    data["lattice"].update(period_mm=16., threshold=.5, resolution_mm=.5, phase_origin_mm=[1., 2., 3.],
                           domain={"max_z_offset_mm": -1., "clearance_mm": 3., "bounds_mm": [[-40., -30., -15.], [40., 30., -1.]]})
    changed = ShoeSpec.model_validate(data)
    assert changed.lattice != spec.lattice
    assert accepted_baseline(baseline, changed, acceptance)["accepted_run_id"] == "frozen-test-run"


@pytest.mark.parametrize("binding", ["source_bytes", "approval", "execution"])
def test_wrong_source_geometry_is_rejected(frozen_fixture, binding):
    baseline, spec, acceptance = frozen_fixture
    if binding == "source_bytes":
        Path(spec.last.file).write_bytes(b"different purchased last bytes")
    elif binding == "approval":
        approval = json.loads(acceptance.read_text())
        approval["source_sha256"] = "0" * 64
        write_json(acceptance, approval)
    else:
        path = baseline / "execution_report.json"
        execution = json.loads(path.read_text())
        execution["input_sha256"] = "0" * 64
        write_json(path, execution)
    with pytest.raises(ValueError):
        accepted_baseline(baseline, spec, acceptance)


def test_legacy_lattice_loads_optional_domain_and_phase_defaults():
    data = json.loads(M2_SPEC.read_text())
    data["lattice"].pop("domain")
    data["lattice"].pop("phase_origin_mm")
    spec = ShoeSpec.model_validate(data)
    assert spec.lattice.phase_origin_mm == [0., 0., 0.]
    assert spec.lattice.domain.model_dump() == {"max_z_offset_mm": 0., "clearance_mm": 2., "bounds_mm": None}


@pytest.mark.parametrize(("path", "value"), [
    (("phase_origin_mm",), [0., 0.]), (("phase_origin_mm",), [0., 0., 0., 0.]),
    (("phase_origin_mm",), [10001., 0., 0.]), (("phase_origin_mm",), [0., float("nan"), 0.]),
    (("phase_origin_mm",), [0., float("inf"), 0.]), (("phase_origin_mm",), [True, 0., 0.]),
    (("domain", "bounds_mm"), [[0., 0., 0.], [0., 1., 1.]]),
    (("domain", "bounds_mm"), [[1., 0., 0.], [0., 1., 1.]]),
    (("domain", "bounds_mm"), [[0., 0.], [1., 1.]]),
    (("domain", "bounds_mm"), [[0., 0., 0.], [10001., 1., 1.]]),
    (("domain", "bounds_mm"), [[0., 0., 0.], [float("inf"), 1., 1.]]),
    (("domain", "max_z_offset_mm"), .1), (("domain", "max_z_offset_mm"), -20.1),
    (("domain", "clearance_mm"), .49), (("domain", "clearance_mm"), 10.1),
    (("domain", "clearance_mm"), True), (("resolution_mm",), 0.),
    (("resolution_mm",), float("nan")), (("resolution_mm",), float("inf")),
    (("resolution_mm",), True), (("resolution_mm",), "0.4"),
])
def test_invalid_lattice_domain_phase_and_sampling_resources_rejected(path, value):
    data = json.loads(M2_SPEC.read_text())
    node = data["lattice"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        ShoeSpec.model_validate(data)


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "shoe_poc", "build-lattice", *map(str, args)],
                          capture_output=True, text=True, timeout=30, cwd=ROOT)


@pytest.mark.parametrize("relationship", ["same", "descendant", "ancestor"])
def test_cli_forbidden_output_preserves_frozen_artifacts_before_rss_guard(tmp_path, relationship):
    ancestor = tmp_path / "protected"
    baseline = ancestor / "frozen"
    baseline.mkdir(parents=True)
    output = {"same": baseline, "descendant": baseline / "m2", "ancestor": ancestor}[relationship]
    sentinel = b'{"status":"PASS","run_id":"do-not-overwrite"}\n'
    (baseline / "execution_report.json").write_bytes(sentinel)
    (baseline / "base_shoe.step").write_bytes(b"frozen native geometry sentinel")
    if output != baseline and output.exists():
        (output / "execution_report.json").write_bytes(sentinel)
    before = {path: path.read_bytes() for path in ancestor.rglob("*") if path.is_file()}
    result = run_cli("--spec", tmp_path / "missing.json", "--base-dir", baseline,
                     "--output", output, "--max-rss-mb", 1)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "non-nested" in result.stderr
    assert before == {path: path.read_bytes() for path in ancestor.rglob("*") if path.is_file()}
    assert not (output / "failure_report.json").exists()


@pytest.mark.parametrize("name", ["execution_report.json", "failure_report.json", "shoe_spec.json", "lattice.stl"])
def test_cli_generated_output_symlink_cannot_overwrite_frozen_baseline(tmp_path, name):
    baseline, output = tmp_path / "frozen", tmp_path / "m2"
    baseline.mkdir()
    output.mkdir()
    sentinel = baseline / "execution_report.json"
    content = b'{"status":"PASS","run_id":"frozen-sentinel"}\n'
    sentinel.write_bytes(content)
    link = output / name
    link.symlink_to(sentinel)
    result = run_cli("--spec", tmp_path / "missing.json", "--base-dir", baseline,
                     "--output", output, "--max-rss-mb", 1)
    assert result.returncode == 2, result.stdout + result.stderr
    assert sentinel.read_bytes() == content
    assert link.is_symlink()
    assert set(output.iterdir()) == {link}


@pytest.mark.parametrize("name", GENERATED)
def test_direct_output_preflight_rejects_each_generated_symlink(tmp_path, name):
    from shoe_poc.lattice.pipeline import validate_output_paths
    baseline, output = tmp_path / "frozen", tmp_path / "m2"
    baseline.mkdir()
    output.mkdir()
    sentinel = baseline / "execution_report.json"
    sentinel.write_bytes(b"frozen report bytes")
    link = output / name
    link.symlink_to(sentinel)
    with pytest.raises(ValueError):
        validate_output_paths(output, baseline)
    assert sentinel.read_bytes() == b"frozen report bytes"
    assert link.is_symlink()
    assert set(output.iterdir()) == {link}


@pytest.mark.parametrize(("flag", "value"), [
    ("--max-voxels", "0"), ("--max-voxels", "-1"), ("--max-voxels", "1.5"),
    ("--max-field-memory-mb", "0"), ("--max-field-memory-mb", "-1"),
    ("--max-field-memory-mb", "nan"), ("--max-field-memory-mb", "inf"),
])
def test_cli_invalid_lattice_resource_budget_fails_before_outputs(tmp_path, flag, value):
    output = tmp_path / "new-output"
    result = run_cli("--spec", tmp_path / "missing.json", "--base-dir", tmp_path / "frozen",
                     "--output", output, flag, value, "--max-rss-mb", 1)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not output.exists()
