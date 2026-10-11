"""Sampled RSS guard for native CAD allocations, available on macOS and Linux."""
import os
from pathlib import Path
import threading

import psutil

from shoe_poc.reporting import utc_now, write_json


class MemoryBudget:
    """Stop the CLI with diagnostic exit 3 on an observed budget violation.

    RSS is sampled every 100 ms. This is not an OS hard memory limit: brief
    spikes between samples can exceed the budget. OS/container limits remain
    appropriate for production jobs.
    """
    def __init__(self, maximum_mb: float, output: Path, command: str):
        self.maximum_bytes = int(maximum_mb * 1024 * 1024)
        self.output = output
        self.command = command
        self.peak_bytes = 0
        self.process = psutil.Process()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, name="CAD-memory-budget", daemon=True)

    def _watch(self):
        while not self.stop.is_set():
            current = self.process.memory_info().rss
            self.peak_bytes = max(self.peak_bytes, current)
            if current > self.maximum_bytes:
                report = {"status": "FAIL", "type": "MemoryBudgetExceeded",
                          "error": "Observed process RSS exceeds configured budget",
                          "generated_at": utc_now(), "rss_bytes": current,
                          "maximum_bytes": self.maximum_bytes, "command": self.command,
                          "sampling_interval_seconds": 0.1}
                write_json(self.output / "failure_report.json", report)
                if self.command in {"run", "build-base"}:
                    write_json(self.output / "execution_report.json", report)
                # An OCCT operation cannot be safely interrupted by raising a
                # Python exception from a background thread. Exit the CLI only.
                os._exit(3)
            self.stop.wait(0.1)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.thread.join(timeout=1)
