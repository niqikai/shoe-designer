"""Helper for test_watchdog: a parent with an RSS watchdog and a memory-hungry pool worker.

Usage: python watchdog_trip.py <report_path> <limit_mb>
"""

import sys
import time
from concurrent.futures import ProcessPoolExecutor

MB = 1024 * 1024


def hog(seconds: float) -> int:
    """Allocate and touch 20 MB every 0.1 s until `seconds` have passed."""
    import numpy as np

    blocks = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        block = np.ones(20 * MB // 8, dtype=np.float64)  # np.ones writes every element
        blocks.append(block)
        time.sleep(0.1)
    return len(blocks)


def main() -> None:
    from shoegen.resources import RssWatchdog, worker_parent_guard

    report_path = sys.argv[1]
    limit_mb = int(sys.argv[2])
    with (
        RssWatchdog(limit_bytes=limit_mb * MB, report_path=report_path),
        ProcessPoolExecutor(max_workers=1, initializer=worker_parent_guard) as pool,
    ):
        pool.submit(hog, 60.0).result()
    sys.exit(0)


if __name__ == "__main__":
    main()
