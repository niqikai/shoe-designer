"""Cost of validating a welded mesh with manifold3d (what M0.6 plans to do after welding).

    python spikes/m0_6a/b_validate.py .scratch/m0_6a/lattice_e030.npz

Loads a canonical mesh (v, t) in a fresh process, builds ``Manifold(Mesh64)``, reads status,
volume and genus, and counts ticks of a background thread (same probe as b_gil_probe.py) to
show whether the call lets other Python threads (the RSS watchdog) run.
"""

import json
import sys
import threading
import time

import manifold3d as m3d
import numpy as np

sys.path.insert(0, __import__("pathlib").Path(__file__).parent.as_posix())
import common

z = np.load(sys.argv[1])
v, t = z["v"], z["t"].astype(np.uint32)
ticks, stop = [], threading.Event()


def run():
    while not stop.is_set():
        ticks.append(time.perf_counter())
        time.sleep(0.05)


th = threading.Thread(target=run, daemon=True)
th.start()
time.sleep(0.2)
rss0 = common.current_rss_mb()
t0 = time.perf_counter()
mesh = m3d.Mesh64(vert_properties=v, tri_verts=t)
M = m3d.Manifold(mesh)
status = str(M.status())
nt = M.num_tri()
vol = M.volume()
genus = M.genus()
t1 = time.perf_counter()
stop.set()
th.join()
inside = [x for x in ticks if t0 <= x <= t1]
gaps = [b - a for a, b in zip([t0] + inside, inside + [t1])]
print(
    json.dumps(
        {
            "mesh": sys.argv[1],
            "triangles_in": len(t),
            "status": status,
            "triangles_out": nt,
            "volume": vol,
            "genus": genus,
            "seconds": round(t1 - t0, 2),
            "rss_before_mb": round(rss0),
            "maxrss_after_mb": round(common.peak_rss_mb()),
            "ticks_during_call": len(inside),
            "ticks_expected": int((t1 - t0) / 0.05),
            "longest_gap_s": round(max(gaps), 2),
        }
    )
)
