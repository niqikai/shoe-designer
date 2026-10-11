import json
from pathlib import Path
import subprocess
import sys
import trimesh


def cli(*arguments):
    return subprocess.run([sys.executable, "-m", "shoe_poc", *map(str, arguments)],
                          capture_output=True, text=True, timeout=45)


def test_cli_help():
    result = cli("--help")
    assert result.returncode == 0
    assert "Milestone 1" in result.stdout
    assert "build-lattice" not in result.stdout


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
