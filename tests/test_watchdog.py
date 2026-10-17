import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil

HELPERS = Path(__file__).parent / "helpers"
EXIT_RESOURCE_LIMIT = 6


def _is_gone(pid: int) -> bool:
    """True if the process no longer runs (absent, or exited and already reaped by init)."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.NoSuchProcess:
        return
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=5)


def test_watchdog_trips_and_kills_worker_tree(tmp_path):
    report_path = tmp_path / "failure.json"
    proc = subprocess.Popen(
        [sys.executable, str(HELPERS / "watchdog_trip.py"), str(report_path), "300"],
    )
    try:
        assert proc.wait(timeout=30) == EXIT_RESOURCE_LIMIT

        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["status"] == "resource_limit"
        assert report["exit_code"] == EXIT_RESOURCE_LIMIT
        assert report["rss_bytes"] > report["limit_bytes"]
        child_pids = report["child_pids"]
        assert child_pids, "report should list the worker PIDs"

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not all(_is_gone(p) for p in child_pids):
            time.sleep(0.1)
        still_alive = [p for p in child_pids if not _is_gone(p)]
        assert not still_alive, f"worker processes still running: {still_alive}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if report_path.exists():
            for pid in json.loads(report_path.read_text(encoding="utf-8")).get("child_pids", []):
                _kill_tree(pid)


def test_worker_exits_when_parent_is_sigkilled(tmp_path):
    pid_path = tmp_path / "worker.pid"
    proc = subprocess.Popen(
        [sys.executable, str(HELPERS / "parent_kill_target.py"), str(pid_path)],
    )
    worker_pid = None
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip():
                worker_pid = int(pid_path.read_text(encoding="utf-8"))
                break
            time.sleep(0.05)
        assert worker_pid is not None, "worker never started its task"
        assert psutil.pid_exists(worker_pid)

        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not _is_gone(worker_pid):
            time.sleep(0.1)
        assert _is_gone(worker_pid), "worker survived parent SIGKILL"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if worker_pid is not None:
            _kill_tree(worker_pid)
