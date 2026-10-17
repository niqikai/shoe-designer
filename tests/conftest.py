"""Shared pytest hooks.

``eval_report`` lets tests publish measured numbers (block counts, timings, RSS) that are
printed in the terminal summary, so acceptance evidence shows up in a normal run.
"""

from __future__ import annotations

import pytest

_REPORT_KEY = pytest.StashKey[list[str]]()


@pytest.fixture
def eval_report(request):
    lines = request.config.stash.setdefault(_REPORT_KEY, [])

    def add(line: str) -> None:
        lines.append(f"{request.node.name}: {line}")

    return add


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    lines = config.stash.get(_REPORT_KEY, [])
    if lines:
        terminalreporter.write_sep("-", "measured values")
        for line in lines:
            terminalreporter.write_line(line)
