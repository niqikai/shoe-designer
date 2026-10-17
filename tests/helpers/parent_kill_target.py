"""Helper for test_watchdog: a parent that runs a busy worker and waits to be killed.

Usage: python parent_kill_target.py <worker_pid_path>
The busy task writes its own PID to <worker_pid_path> so the test can find it.
"""

import sys
import time
from concurrent.futures import ProcessPoolExecutor


def busy(pid_path: str, seconds: float) -> None:
    import os

    with open(pid_path, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        sum(range(10_000))  # pure Python loop: holds the GIL in short slices


def main() -> None:
    from shoegen.resources import worker_parent_guard

    pid_path = sys.argv[1]
    with ProcessPoolExecutor(max_workers=1, initializer=worker_parent_guard) as pool:
        pool.submit(busy, pid_path, 120.0).result()


if __name__ == "__main__":
    main()
