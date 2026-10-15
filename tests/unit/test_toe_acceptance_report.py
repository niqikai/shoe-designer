"""Synthetic report fixtures test provenance gates, never actual CAD quality."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shoe_poc.reporting import sha256

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT/"scripts/report_toe.py"
SPEC = importlib.util.spec_from_file_location("toe_acceptance_under_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path, value):
    path.write_text(json.dumps(value))


def _read(path):
    return json.loads(path.read_text())


def _records(directory, names):
    return {name: {"sha256": sha256(directory/name), "bytes": (directory/name).stat().st_size} for name in names}


@pytest.fixture
def evidence(tmp_path):
    output, variant, old = [tmp_path/name for name in ("current", "variant", "old")]
    for directory in (output, variant, old):
        directory.mkdir()
    (old/"base_shoe.step").write_bytes(b"SYNTHETIC baseline provenance sentinel; not geometry")
    run, source = "synthetic-current-run", "synthetic-source-hash"
    reports = {
        "toe_report": {"run_id": run, "source_sha256": source, "geometry_status": "PASS",
                       "baseline_directory": str(old), "baseline_step_sha256": sha256(old/"base_shoe.step")},
        "design_report": {"run_id": run, "source_sha256": source, "geometry_status": "PASS"},
        "geometry_report": {"algorithm_version": "synthetic-report-guard-fixture"},
        "last_metadata": {"source": {"sha256": source}},
        "shoe_spec": {"sole": {"height_mm": 16, "profile_parameters": {"toe_roll_radius_mm": 15}}},
        "roundtrip_report": {"status": "PASS"},
        "validation_report": {"geometry_status": "PASS"},
        "surface_report": {"geometry_status": "PASS"},
        "mesh_export_report": {"base_shoe": {"native_face_coverage_status": "PASS"}},
    }
    for name, value in reports.items():
        _write(output/(name+".json"), value)
    for name in ("base_shoe.step", "base_shoe.stl", "last_normalized.step"):
        (output/name).write_bytes(b"SYNTHETIC current provenance sentinel; not geometry "+name.encode())
    execution = {"status": "PASS", "run_id": run, "input_sha256": source,
                 "outputs": _records(output, MODULE.REQUIRED_BUILD_OUTPUTS)}
    _write(output/"execution_report.json", execution)
    for name in MODULE.PARAMETER_FILES:
        (variant/name).write_bytes(b"SYNTHETIC variant provenance sentinel; not geometry "+name.encode())
    _write(variant/"execution_report.json", {"status": "PASS", "run_id": "synthetic-variant-run",
           "input_sha256": source, "outputs": _records(variant, MODULE.PARAMETER_FILES)})
    parameters = {"status": "PASS", "checks": {"synthetic_actual_check": True}}
    for role, directory, identifier in (("baseline", output, run), ("variant", variant, "synthetic-variant-run")):
        parameters[role] = {"directory": str(directory), "run_id": identifier, "execution_status": "PASS",
                            "input_sha256": source, "files": _records(directory, MODULE.PARAMETER_FILES)}
    _write(output/"parameter_change_report.json", parameters)
    rows = {f"tests/integration/test_real_last.py::test_T{i:02d}_synthetic": {"status": "PASS"}
            for i in (1, 2, 3, 5, 6, 7, 16, 19)}
    tests = {"build_provenance": {"directory": str(output.resolve()), "run_id": run,
                                 "base_shoe_stl_sha256": sha256(output/"base_shoe.stl")},
             "pytest_exit_code": 0, "collected": 8, "counts": {"PASS": 8, "FAIL": 0, "NOT_TESTED": 0}, "tests": rows}
    _write(output/"test_report.json", tests)
    return SimpleNamespace(output=output, variant=variant, old=old, document=tmp_path/"report.md")


def _report(evidence):
    return MODULE.report(evidence.output, evidence.document)


def _update_bound_report(evidence, name, mutate):
    path = evidence.output/(name+".json")
    value = _read(path)
    mutate(value)
    _write(path, value)
    execution = _read(evidence.output/"execution_report.json")
    execution["outputs"].update(_records(evidence.output, {name+".json"}))
    _write(evidence.output/"execution_report.json", execution)


def test_complete_bound_synthetic_evidence_passes_only_technical_levels(evidence):
    result = _report(evidence)
    assert result["technical_status"] == "PASS"
    assert result["overall_status"] == "PARTIAL"
    assert result["appearance_acceptance"] == "PENDING"
    assert result["supplemental_acceptance_guard"]["status"] == "NOT_TESTED"


def test_explicit_noncore_failure_cannot_hide_in_zero_failure_summary(evidence):
    path = evidence.output/"test_report.json"
    value = _read(path)
    value["tests"]["tests/unit/test_extra.py::test_failure"] = {"status": "FAIL"}
    _write(path, value)
    result = _report(evidence)
    assert result["acceptance_levels"]["tests"] == result["technical_status"] == "FAIL"


@pytest.mark.parametrize("change", ("counts", "collected", "skipped"))
def test_incomplete_or_inconsistent_test_evidence_cannot_pass(evidence, change):
    path = evidence.output/"test_report.json"
    value = _read(path)
    if change == "counts":
        value["counts"]["PASS"] += 1
    elif change == "collected":
        value["collected"] += 1
    else:
        value["tests"]["tests/unit/test_extra.py::test_skipped"] = {"status": "NOT_TESTED"}
        value["counts"]["NOT_TESTED"] = 1
        value["collected"] += 1
    _write(path, value)
    result = _report(evidence)
    assert result["acceptance_levels"]["tests"] == "NOT_VERIFIED"
    assert result["technical_status"] == "PARTIAL"


@pytest.mark.parametrize("name", ("toe_report.json", "geometry_report.json", "base_shoe.step", "mesh_export_report.json"))
def test_missing_mandatory_execution_hash_record_is_rejected(evidence, name):
    path = evidence.output/"execution_report.json"
    value = _read(path)
    del value["outputs"][name]
    _write(path, value)
    with pytest.raises(ValueError, match="Missing artifact hash records"):
        _report(evidence)


def test_current_record_byte_size_mismatch_is_rejected(evidence):
    path = evidence.output/"execution_report.json"
    value = _read(path)
    value["outputs"]["base_shoe.stl"]["bytes"] += 1
    _write(path, value)
    with pytest.raises(ValueError, match="hash/size mismatch"):
        _report(evidence)


@pytest.mark.parametrize("name,key", (("toe_report", "source_sha256"), ("design_report", "source_sha256"),
                                     ("design_report", "run_id")))
def test_current_hashes_cannot_promote_wrong_run_or_source(evidence, name, key):
    _update_bound_report(evidence, name, lambda value: value.update({key: "stale-evidence"}))
    with pytest.raises(ValueError, match="current run and source"):
        _report(evidence)


def test_replaced_toe_baseline_is_rejected(evidence):
    (evidence.old/"base_shoe.step").write_bytes(b"replaced baseline")
    with pytest.raises(ValueError, match="baseline CAD"):
        _report(evidence)


def test_stale_main_pytest_provenance_does_not_qualify_current_build(evidence):
    path = evidence.output/"test_report.json"
    value = _read(path)
    value["build_provenance"]["run_id"] = "old-run"
    _write(path, value)
    result = _report(evidence)
    assert result["tests_current"] is False
    assert result["technical_status"] == "PARTIAL"


@pytest.mark.parametrize("change", ("variant_file", "variant_run", "missing_record", "baseline_hash", "false_check"))
def test_parameter_summary_cannot_promote_stale_or_failed_actual_evidence(evidence, change):
    path = evidence.output/"parameter_change_report.json"
    value = _read(path)
    if change == "variant_file":
        (evidence.variant/"base_shoe.stl").write_bytes(b"overwritten variant")
    elif change == "variant_run":
        live_path = evidence.variant/"execution_report.json"
        live = _read(live_path); live["run_id"] = "new-variant-run"; _write(live_path, live)
    elif change == "missing_record":
        del value["variant"]["files"]["base_shoe.step"]
    elif change == "baseline_hash":
        value["baseline"]["files"]["base_shoe.step"]["sha256"] = "old-hash"
    else:
        value["checks"]["synthetic_actual_check"] = False
    _write(path, value)
    result = _report(evidence)
    assert result["acceptance_levels"]["sole_height_variant"] == ("FAIL" if change == "false_check" else "NOT_VERIFIED")
    assert result["technical_status"] != "PASS"


def test_separate_guard_run_is_bound_to_current_report_source(evidence):
    debug = evidence.output/"debug"; debug.mkdir()
    tests = {"pytest_exit_code": 0, "collected": 1, "counts": {"PASS": 1, "FAIL": 0, "NOT_TESTED": 0},
             "tests": {"synthetic_guard_fixture": {"status": "PASS"}}}
    _write(debug/"toe_report_guard_tests.json", tests)
    metadata = {"report_script_sha256": sha256(SCRIPT), "test_source_sha256": sha256(Path(__file__)),
                "pytest_report_sha256": sha256(debug/"toe_report_guard_tests.json")}
    _write(debug/"toe_report_guard_provenance.json", metadata)
    result = _report(evidence)
    assert result["acceptance_levels"]["acceptance_report_guard_tests"] == "PASS"
    assert result["test_counts"]["PASS"] == 8
    assert result["supplemental_acceptance_guard"]["counts"]["PASS"] == 1
    metadata["report_script_sha256"] = "old-report-script"
    _write(debug/"toe_report_guard_provenance.json", metadata)
    result = _report(evidence)
    assert result["acceptance_levels"]["acceptance_report_guard_tests"] == "NOT_VERIFIED"
    assert result["technical_status"] == "PARTIAL"
