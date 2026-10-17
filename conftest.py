"""Optional machine-readable pytest evidence, including every failed/skipped test."""
from pathlib import Path
import pytest

from shoe_poc.reporting import environment, utc_now, write_json


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
    # The artifact fixture records the actual tested directory and hashes.
    # Report placement alone cannot prove which generated shoe was tested.
    provenance = getattr(session.config, "_shoe_repair_provenance", {})
    write_json(Path(target), {"generated_at": utc_now(), "pytest_exit_code": int(exitstatus),
                             "invocation_args": list(session.config.invocation_params.args),
                             "collected_nodeids": [item.nodeid for item in session.items],
                             "collected": session.testscollected, "counts": counts,
                             "tests": results, "environment": environment(),
                             "build_provenance": provenance,
                             "design_build_provenance": getattr(session.config, "_shoe_design_provenance", {}),
                             "lattice_build_provenance": getattr(session.config, "_shoe_lattice_provenance", {}),
                             "toe_repair_provenance": getattr(session.config, "_shoe_toe_repair_provenance", {}),
                             "terminal_build_provenance": getattr(session.config, "_shoe_terminal_provenance", {}),
                             "m2_closeout_provenance": getattr(session.config, "_shoe_m2_closeout_provenance", {})})
