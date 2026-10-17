"""Resource guards: RSS watchdog for the CLI and a parent-death guard for pool workers.

See ADR 0005 (RSS limit) and implementation plan M0.1.

Why ``os._exit``: a ``sys.exit`` from the watchdog thread would only end that thread.
Why kill children first: ``ProcessPoolExecutor`` workers outlive a parent that exits
without cleanup and keep their memory.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import psutil

EXIT_RESOURCE_LIMIT = 6
_CHILD_TERM_TIMEOUT_S = 2.0
_CHILD_KILL_TIMEOUT_S = 5.0
_PARENT_POLL_INTERVAL_S = 0.5

_GONE_ERRORS = (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess)


def _sample(proc: psutil.Process) -> tuple[int, list[psutil.Process], list[int]]:
    """Return (total RSS of proc and all descendants, descendant processes, their pids)."""
    try:
        children = proc.children(recursive=True)
    except _GONE_ERRORS:
        children = []
    total = 0
    try:
        total += proc.memory_info().rss
    except _GONE_ERRORS:
        pass
    alive: list[psutil.Process] = []
    for child in children:
        try:
            total += child.memory_info().rss
            alive.append(child)
        except _GONE_ERRORS:
            continue
    return total, alive, [c.pid for c in alive]


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


class RssWatchdog:
    """Context manager that enforces an RSS limit on this process and its descendants.

    On trip: write the failure report, SIGTERM all descendants, wait 2 s, SIGKILL the
    survivors, reap them, then ``os._exit(6)``.

    ``on_trip`` is called with the report dict after the report is written and before any
    process is signalled. ``exit_fn`` replaces ``os._exit`` (tests only).
    """

    def __init__(
        self,
        limit_bytes: int,
        interval_s: float = 0.5,
        report_path: str | os.PathLike[str] | None = None,
        on_trip: Callable[[dict[str, Any]], None] | None = None,
        exit_fn: Callable[[int], None] = os._exit,
    ) -> None:
        if limit_bytes <= 0:
            raise ValueError("limit_bytes must be positive")
        self.limit_bytes = int(limit_bytes)
        self.interval_s = float(interval_s)
        self.report_path = Path(report_path) if report_path is not None else None
        self.on_trip = on_trip
        self._exit_fn = exit_fn
        self._proc = psutil.Process()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak_bytes = 0
        self._lock = threading.Lock()

    @property
    def peak_bytes(self) -> int:
        with self._lock:
            return self._peak_bytes

    def __enter__(self) -> Self:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="shoegen-rss-watchdog", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, 2 * self.interval_s))
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            total, _, _ = _sample(self._proc)
            with self._lock:
                self._peak_bytes = max(self._peak_bytes, total)
            if total > self.limit_bytes:
                self._trip(total)
                return
            self._stop.wait(self.interval_s)

    def _trip(self, rss_bytes: int) -> None:
        _, children, child_pids = _sample(self._proc)
        report: dict[str, Any] = {
            "status": "resource_limit",
            "exit_code": EXIT_RESOURCE_LIMIT,
            "rss_bytes": rss_bytes,
            "limit_bytes": self.limit_bytes,
            "peak_bytes": self.peak_bytes,
            "parent_pid": self._proc.pid,
            "child_pids": child_pids,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if self.report_path is not None:
            _write_report(self.report_path, report)
        if self.on_trip is not None:
            self.on_trip(report)

        for child in children:
            try:
                child.terminate()
            except _GONE_ERRORS:
                pass
        _, alive = psutil.wait_procs(children, timeout=_CHILD_TERM_TIMEOUT_S)
        for child in alive:
            try:
                child.kill()
            except _GONE_ERRORS:
                pass
        psutil.wait_procs(alive, timeout=_CHILD_KILL_TIMEOUT_S)
        self._exit_fn(EXIT_RESOURCE_LIMIT)


def worker_parent_guard(interval_s: float = _PARENT_POLL_INTERVAL_S) -> None:
    """Pool initializer: exit this worker if its parent process goes away.

    Records ``os.getppid()`` at start-up and polls it from a daemon thread. Covers the case
    where the parent is SIGKILLed and cannot clean up its children.
    """
    parent_pid = os.getppid()

    def _watch() -> None:
        while True:
            time.sleep(interval_s)
            if os.getppid() != parent_pid:
                os._exit(1)

    threading.Thread(target=_watch, name="shoegen-parent-guard", daemon=True).start()
