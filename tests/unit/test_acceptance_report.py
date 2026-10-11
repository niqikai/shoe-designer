"""Acceptance must not promote missing/failed evidence or pending appearance."""
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/report_m1.py"
spec = importlib.util.spec_from_file_location("m1_acceptance", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_empty_evidence_cannot_pass(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    result = module.report(output, tmp_path / "report.md")
    assert result["technical_status"] == "PARTIAL"
    assert result["overall_status"] == "PARTIAL"
    assert all(status == "NOT_TESTED" for status in result["test_requirements"].values())


def test_failed_surface_measurement_blocks_acceptance(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    evidence = {
        "execution_report": {"status": "PASS"},
        "validation_report": {"geometry_status": "PASS"},
        "roundtrip_report": {"status": "PASS"},
        "surface_report": {"geometry_status": "FAIL"},
    }
    for name, value in evidence.items():
        (output / f"{name}.json").write_text(json.dumps(value))
    result = module.report(output, tmp_path / "report.md")
    assert result["technical_status"] == result["overall_status"] == "FAIL"
    assert result["appearance_acceptance"] == "PENDING"
    assert result["acceptance_levels"]["global_minimum_wall_thickness"] == "NOT_VERIFIED"


def test_all_technical_checks_do_not_accept_appearance(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    tests = {f"tests/integration/test_real_last.py::test_T{i:02d}_evidence": {"status": "PASS"}
             for i in (1, 2, 3, 5, 6, 7, 16, 19)}
    evidence = {
        "execution_report": {"status": "PASS"},
        "validation_report": {"geometry_status": "PASS"},
        "roundtrip_report": {"status": "PASS"},
        "surface_report": {"geometry_status": "PASS"},
        "parameter_change_report": {"status": "PASS"},
        "test_report": {"pytest_exit_code": 0, "counts": {"FAIL": 0}, "tests": tests},
    }
    for name, value in evidence.items():
        (output / f"{name}.json").write_text(json.dumps(value))
    result = module.report(output, tmp_path / "report.md")
    assert result["technical_status"] == "PASS"
    assert result["overall_status"] == "PARTIAL"
    assert result["user_acceptance"] == result["appearance_acceptance"] == "PENDING"
