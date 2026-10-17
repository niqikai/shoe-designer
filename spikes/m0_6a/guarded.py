"""Run a command under an out-of-process RSS guard (parent + all descendants).

    python spikes/m0_6a/guarded.py --limit-gb 6 --out result.json -- <command ...>

Why: manifold3d's level_set keeps the GIL for the whole call when the callback is a numba
function, so the in-process RssWatchdog thread cannot run during it (see b_gil_probe.py).
This guard polls from a separate process, records the peak of the whole process tree and
SIGKILLs the tree above the limit (exit code 6, like the watchdog). stdout/stderr of the
command pass through unchanged.
"""

import argparse
import json
import subprocess
import sys
import time

import psutil

ap = argparse.ArgumentParser()
ap.add_argument("--limit-gb", type=float, default=6.0)
ap.add_argument("--out", default=None)
ap.add_argument("--interval", type=float, default=0.05)
ap.add_argument("cmd", nargs=argparse.REMAINDER)
a = ap.parse_args()
cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
limit = int(a.limit_gb * 2**30)
t0 = time.perf_counter()
p = subprocess.Popen(cmd)
root = psutil.Process(p.pid)
peak = 0
tripped = False
while p.poll() is None:
    try:
        procs = [root] + root.children(recursive=True)
        rss = 0
        for q in procs:
            try:
                rss += q.memory_info().rss
            except psutil.Error:
                pass
        peak = max(peak, rss)
        if rss > limit:
            tripped = True
            for q in procs:
                try:
                    q.kill()
                except psutil.Error:
                    pass
            break
    except psutil.Error:
        pass
    time.sleep(a.interval)
rc = p.wait()
res = {
    "cmd": " ".join(cmd[-6:]),
    "limit_gb": a.limit_gb,
    "peak_tree_rss_mb": round(peak / 2**20, 1),
    "tripped": tripped,
    "returncode": rc,
    "wall_seconds": round(time.perf_counter() - t0, 2),
}
if a.out:
    with open(a.out, "a") as fh:
        fh.write(json.dumps(res) + "\n")
print("GUARD " + json.dumps(res), file=sys.stderr)
sys.exit(6 if tripped else rc)
