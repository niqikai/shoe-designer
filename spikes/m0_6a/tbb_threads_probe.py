"""How many threads does manifold3d's TBB use, and do TBB_NUM_THREADS / the DYLD shim change it?

    python spikes/m0_6a/tbb_threads_probe.py
    TBB_NUM_THREADS=1 python spikes/m0_6a/tbb_threads_probe.py
    DYLD_INSERT_LIBRARIES=.scratch/interpose/libonecpu.dylib ONECPU_N=1 python spikes/m0_6a/tbb_threads_probe.py

Prints CPU seconds / wall seconds of a parallel boolean workload: ratio ~ threads in use.
"""

import resource
import time

import manifold3d as m

a = m.Manifold.sphere(10, 1500)
b = m.Manifold.sphere(10, 1500).translate((3, 0, 0))
a.num_tri()
b.num_tri()
t = time.perf_counter()
r0 = resource.getrusage(resource.RUSAGE_SELF)
for _ in range(3):
    n = (a + b).num_tri()
    n = (a - b).num_tri()
r1 = resource.getrusage(resource.RUSAGE_SELF)
w = time.perf_counter() - t
cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
print(f"wall {w:.2f} s  cpu {cpu:.2f} s  cpu/wall {cpu / w:.2f}  triangles {n}")
