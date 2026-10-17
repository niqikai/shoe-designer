"""Does a Python background thread (like RssWatchdog) get to run during level_set?

    uv run python spikes/m0_6a/b_gil_probe.py

A thread ticks every 50 ms. We count ticks during a ~5 s level_set with (a) a pure-Python
callback and (b) a numba njit callback, and report the longest gap between ticks.
"""

import json
import math
import threading
import time

import manifold3d as m3d
import numba


def probe(f, half, e):
    ticks = []
    stop = threading.Event()

    def run():
        while not stop.is_set():
            ticks.append(time.perf_counter())
            time.sleep(0.05)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.2)
    t0 = time.perf_counter()
    m3d.Manifold.level_set(f, [-half] * 3 + [half] * 3, e).num_tri()
    t1 = time.perf_counter()
    stop.set()
    th.join()
    inside = [x for x in ticks if t0 <= x <= t1]
    gaps = [b - a for a, b in zip([t0] + inside, inside + [t1])]
    return {
        "call_seconds": round(t1 - t0, 2),
        "ticks_during_call": len(inside),
        "ticks_expected_at_50ms": int((t1 - t0) / 0.05),
        "longest_gap_s": round(max(gaps), 2),
    }


@numba.njit
def nf(x, y, z):
    return 20.0 - math.sqrt(x * x + y * y + z * z)


nf(0.0, 0.0, 0.0)
print(
    json.dumps(
        {
            "python_callback": probe(
                lambda x, y, z: 20.0 - math.sqrt(x * x + y * y + z * z), 100, 1.0
            )
        }
    )
)
print(json.dumps({"numba_callback": probe(nf, 80, 0.5)}))
