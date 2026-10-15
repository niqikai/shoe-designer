import json
from pathlib import Path
import subprocess
import sys
import trimesh
import pytest

from shoe_poc.cli import InputError, _prepare_build_output, _record_build_failure


def cli(*arguments):
    return subprocess.run([sys.executable, "-m", "shoe_poc", *map(str, arguments)],
                          capture_output=True, text=True, timeout=45)


def test_cli_help():
    result = cli("--help")
    assert result.returncode == 0
    assert "Milestone 1" in result.stdout
    assert "build-lattice" in result.stdout


def test_missing_last_has_input_error(tmp_path):
    result = cli("inspect-last", "--input", tmp_path / "missing.step", "--output", tmp_path / "report")
    assert result.returncode == 2
    report = json.loads((tmp_path / "report/failure_report.json").read_text())
    assert report["status"] == "FAIL"
    assert report["type"] == "FileNotFoundError"


def test_mesh_failure_propagates_exit_code(tmp_path):
    mesh = trimesh.creation.box()
    mesh.update_faces(range(len(mesh.faces) - 1))
    model = tmp_path / "open.stl"
    mesh.export(model)
    result = cli("validate", "--model", model, "--output", tmp_path / "report")
    assert result.returncode == 3
    report = json.loads((tmp_path / "report/validation_report.json").read_text())
    assert report["geometry_status"] == "FAIL"
    assert report["manufacturing_status"] == "NOT_VERIFIED"


def test_bad_spec_has_input_error(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text('{"schema_version":"0.1","unit":"inch"}')
    result = cli("build-base", "--spec", path, "--output", tmp_path / "report")
    assert result.returncode == 2
    report = json.loads((tmp_path / "report/failure_report.json").read_text())
    assert report["status"] == "FAIL"


def test_memory_budget_stops_native_import_with_diagnostic(tmp_path):
    result = cli("inspect-last", "--input", tmp_path / "absent.step", "--output", tmp_path / "report", "--max-rss-mb", 1)
    assert result.returncode == 3
    report = json.loads((tmp_path / "report/failure_report.json").read_text())
    assert report["type"] == "MemoryBudgetExceeded"
    assert report["rss_bytes"] > report["maximum_bytes"]


def test_new_build_clears_old_evidence_and_preserves_inputs_and_diagnostics(tmp_path):
    for name in ("shoe_spec.json", "test_report.json", "parameter_change_report.json",
                 "base_shoe.stl", "comparison.png", "user_notes.txt"):
        (tmp_path / name).write_text(name)
    debug = tmp_path / "debug"
    debug.mkdir()
    (debug / "failed_trial.step").write_text("evidence")
    spec_path = tmp_path / "shoe_spec.json"
    cleared = _prepare_build_output(tmp_path, spec_path, tmp_path / "source.step", None)
    assert {"test_report.json", "parameter_change_report.json", "base_shoe.stl", "comparison.png"} <= set(cleared)
    assert spec_path.read_text() == "shoe_spec.json"
    assert (tmp_path / "user_notes.txt").is_file()
    assert (debug / "failed_trial.step").is_file()


@pytest.mark.parametrize("name", ["base_shoe.stl", "upper.stl", "cavity.stl", "last_normalized.stl"])
def test_comparison_never_overwrites_generated_input(tmp_path, name):
    source = tmp_path / name
    source.write_bytes(b"previous model")
    with pytest.raises(InputError, match="Comparison input"):
        _prepare_build_output(tmp_path, tmp_path / "spec.json", tmp_path / "source.step", source)
    assert source.read_bytes() == b"previous model"


def test_cad_source_cannot_be_replaced_by_generated_output(tmp_path):
    source = tmp_path / "last_normalized.step"
    source.write_text("purchased source")
    with pytest.raises(InputError, match="Source last"):
        _prepare_build_output(tmp_path, tmp_path / "spec.json", source, None)
    assert source.read_text() == "purchased source"


def test_failure_retains_completed_current_run_evidence(tmp_path):
    path = tmp_path / "execution_report.json"
    evidence = {"run_id": "current", "status": "FAIL", "timings_seconds": {"build": 9},
                "outputs": {"base_shoe.stl": {"sha256": "actual"}}, "elapsed_seconds": 10}
    path.write_text(json.dumps(evidence))
    _record_build_failure(tmp_path, "current", RuntimeError("mesh failed"), 11)
    recorded = json.loads(path.read_text())
    assert recorded["outputs"] == evidence["outputs"]
    assert recorded["timings_seconds"] == evidence["timings_seconds"]
    assert recorded["error"] == "mesh failed"


def test_input_failure_discards_previous_run_evidence(tmp_path):
    (tmp_path / "execution_report.json").write_text(json.dumps({"run_id": "previous", "status": "PASS"}))
    (tmp_path / "test_report.json").write_text('{"pytest_exit_code":0}')
    _record_build_failure(tmp_path, "current", InputError("bad spec"), 0.1)
    assert not (tmp_path / "test_report.json").exists()
    recorded = json.loads((tmp_path / "execution_report.json").read_text())
    assert recorded["status"] == "FAIL" and recorded["run_id"] == "current"


def test_direct_build_caller_preserves_failure_evidence(tmp_path):
    from shoe_poc.cli import build_base
    spec = tmp_path / "bad_spec.json"
    spec.write_text('{"unit":"inch"}')
    output = tmp_path / "reports"
    with pytest.raises(ValueError):
        build_base(spec, output, run_id="variant-attempt")
    execution = json.loads((output / "execution_report.json").read_text())
    failure = json.loads((output / "failure_report.json").read_text())
    assert execution["status"] == failure["status"] == "FAIL"
    assert execution["run_id"] == failure["run_id"] == "variant-attempt"
    assert execution["milestone_status"] == "PARTIAL"


def test_import_failure_keeps_elapsed_stage_record(tmp_path):
    from shoe_poc.cli import build_base
    data = json.loads((Path(__file__).resolve().parents[2] / "input/specs/shoe_spec.json").read_text())
    data["last"]["file"] = str(tmp_path / "missing.step")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(data))
    output = tmp_path / "report"
    with pytest.raises(FileNotFoundError):
        build_base(spec, output)
    report = json.loads((output / "execution_report.json").read_text())
    assert report["status"] == "FAIL"
    assert report["last_completed_or_failed_stage"] == "import_last"
    assert report["last_stage_status"] == "FAILED"
    assert report["timings_seconds"]["import_last"] > 0


def test_design_baseline_never_overwrites_itself(tmp_path):
    from shoe_poc.cli import build_base
    saved = tmp_path / "execution_report.json"
    saved.write_text('{"run_id":"previous","status":"PASS"}')
    with pytest.raises(InputError, match="baseline and output must differ"):
        build_base(tmp_path / "spec.json", tmp_path, design_baseline=tmp_path)
    assert json.loads(saved.read_text())["run_id"] == "previous"
    assert not (tmp_path / "failure_report.json").exists()


@pytest.mark.parametrize("changed_field", ["clearance_mm", "thickness_mm"])
def test_design_comparison_rejects_changed_fit_before_build(tmp_path, changed_field):
    from shoe_poc.cli import build_base
    from shoe_poc.reporting import sha256
    root = Path(__file__).resolve().parents[2]
    data = json.loads((root / "input/specs/shoe_spec.json").read_text())
    # Only input validation runs: these placeholders must never be imported.
    source = tmp_path / "source.step"
    source.write_text("immutable source")
    data["last"]["file"] = str(source)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    for name in ("base_shoe.step", "last_normalized.step", "cavity.step"):
        (baseline / name).write_text("preserved original")
    (baseline / "shoe_spec.json").write_text(json.dumps(data))
    (baseline / "last_metadata.json").write_text(json.dumps({"source": {"sha256": sha256(source)}}))
    data["upper"][changed_field] += .2
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(data))
    with pytest.raises(InputError, match="retain the baseline clearance and upper thickness"):
        build_base(spec, tmp_path / "new", design_baseline=baseline)
    assert (baseline / "base_shoe.step").read_text() == "preserved original"
    failure = json.loads((tmp_path / "new/failure_report.json").read_text())
    assert failure["status"] == "FAIL"
