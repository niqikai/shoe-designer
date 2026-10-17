import json
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil

HELPERS = Path(__file__).parent / "helpers"
EXIT_RESOURCE_LIMIT = 6
TOKEN_VAR = "SHOEGEN_WATCHDOG_TEST_TOKEN"


def _env_with_token() -> tuple[dict, str]:
    """Environment for the helper with a unique token; every descendant inherits it."""
    token = uuid.uuid4().hex
    return {**os.environ, TOKEN_VAR: token}, token


def _orphans_with_token(token: str) -> list[int]:
    """Live processes re-parented to init (PPID 1) that carry ``token`` in their environment,
    i.e. leftovers of the helper's process tree (plan M0.1: no PPID-1 leftovers)."""
    found = []
    for p in psutil.process_iter(["pid", "ppid"]):
        if p.info["ppid"] != 1:
            continue
        try:
            if p.environ().get(TOKEN_VAR) == token and p.status() != psutil.STATUS_ZOMBIE:
                found.append(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return found


def _wait_no_orphans(token: str, seconds: float) -> list[int]:
    deadline = time.monotonic() + seconds
    left = _orphans_with_token(token)
    while left and time.monotonic() < deadline:
        time.sleep(0.1)
        left = _orphans_with_token(token)
    return left


def test_orphan_scan_sees_a_tagged_orphan():
    """The PPID-1 scan below is only evidence if it can see an orphan at all: create one
    on purpose (a shell that backgrounds a Python sleeper and exits) and find it by its
    token. (A Python process, like the pool workers: macOS hides the environment of
    platform binaries such as /bin/sleep.)"""
    env, token = _env_with_token()
    sleeper = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(30)'"
    out = subprocess.run(
        ["/bin/sh", "-c", f"{sleeper} >/dev/null 2>&1 & echo $!"],
        env=env, capture_output=True, text=True, check=True,
    )  # fmt: skip
    pid = int(out.stdout.strip())
    try:
        deadline = time.monotonic() + 5
        while pid not in _orphans_with_token(token) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pid in _orphans_with_token(token)
    finally:
        _kill_tree(pid)


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
    env, token = _env_with_token()
    proc = subprocess.Popen(
        [sys.executable, str(HELPERS / "watchdog_trip.py"), str(report_path), "300"], env=env
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
        # no process of the helper's tree (listed or not, e.g. a resource tracker) was
        # left behind re-parented to init
        assert _wait_no_orphans(token, 5.0) == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if report_path.exists():
            for pid in json.loads(report_path.read_text(encoding="utf-8")).get("child_pids", []):
                _kill_tree(pid)


def test_worker_exits_when_parent_is_sigkilled(tmp_path):
    pid_path = tmp_path / "worker.pid"
    env, token = _env_with_token()
    proc = subprocess.Popen(
        [sys.executable, str(HELPERS / "parent_kill_target.py"), str(pid_path)], env=env
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

        t_kill = time.monotonic()
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)

        # plan M0.1: the worker exits on its own within 2 s of the parent's SIGKILL
        deadline = t_kill + 2.0
        while time.monotonic() < deadline and not _is_gone(worker_pid):
            time.sleep(0.05)
        elapsed = time.monotonic() - t_kill
        assert _is_gone(worker_pid), f"worker survived parent SIGKILL for {elapsed:.2f} s"
        print(f"worker gone {elapsed:.2f} s after the parent's SIGKILL")
        # the worker (and anything else of the tree) is gone, not just re-parented
        assert _wait_no_orphans(token, 2.0) == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if worker_pid is not None:
            _kill_tree(worker_pid)
