"""Optional machine-readable pytest evidence, including every failed/skipped test."""
from pathlib import Path
import pytest

import json

from shoe_poc.reporting import environment, sha256, utc_now, write_json


def pytest_addoption(parser):
    parser.addoption("--report-json", default=None, help="Write actual pytest outcomes as JSON")


def pytest_configure(config):
    config._shoe_results = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    report = result.get_result()
    results = item.config._shoe_results
    if report.when == "call" or report.failed or report.skipped:
        entry = results.setdefault(item.nodeid, {"duration_seconds": 0.0})
        entry["duration_seconds"] += report.duration
        if entry.get("status") != "FAIL":
            entry["status"] = {"passed": "PASS", "failed": "FAIL", "skipped": "NOT_TESTED"}[report.outcome]
            entry["phase"] = report.when
            entry["reason"] = str(report.longrepr) if report.failed or report.skipped else None


def pytest_sessionfinish(session, exitstatus):
    target = session.config.getoption("--report-json")
    if not target:
        return
    results = session.config._shoe_results
    counts = {status: sum(entry["status"] == status for entry in results.values())
              for status in ("PASS", "FAIL", "NOT_TESTED")}
    output = Path(target).parent
    execution_path = output / "execution_report.json"
    execution = json.loads(execution_path.read_text()) if execution_path.is_file() else {}
    provenance = {"run_id": execution.get("run_id"),
                  "base_shoe_stl_sha256": sha256(output / "base_shoe.stl")
                  if (output / "base_shoe.stl").is_file() else None}
    write_json(Path(target), {"generated_at": utc_now(), "pytest_exit_code": int(exitstatus),
                             "collected": session.testscollected, "counts": counts,
                             "tests": results, "environment": environment(),
                             "build_provenance": provenance})
