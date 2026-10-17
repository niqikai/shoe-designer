"""Optional cross-check of a mesh with manifold3d, in a separate process.

``Manifold(Mesh64(...))`` holds the GIL for the whole call and needs about 440 bytes per
triangle on top of the mesh (M0.6a: 5.3 M triangles, 1.03 s, +2.3 GB). In the parent that
would starve the RSS watchdog thread, so the check runs in a one-worker ``spawn`` pool:
the parent only waits on a future (its watchdog keeps sampling, and counts the child's
RSS, which is the point), and the child exits with its parent (``worker_parent_guard``).
Before starting, the cost is estimated and the check is skipped if it would exceed the
memory budget. The project's own checks (:func:`shoegen.mesh.validate_mesh`) are the
authoritative ones; this is an independent second opinion.
"""

from __future__ import annotations

import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import psutil

from shoegen.mesh.model import Mesh
from shoegen.resources import worker_parent_guard

MANIFOLD3D_BYTES_PER_TRIANGLE = 440
MANIFOLD3D_SAFETY = 1.5


def estimate_manifold3d_bytes(mesh: Mesh) -> int:
    copy = mesh.vertices.nbytes + 8 * 3 * mesh.n_faces  # float64 vertices, uint64 faces
    return int(MANIFOLD3D_SAFETY * MANIFOLD3D_BYTES_PER_TRIANGLE * mesh.n_faces + 2 * copy)


def _check(vertices: np.ndarray, faces: np.ndarray) -> dict:
    import manifold3d as m3d

    t0 = time.perf_counter()
    m = m3d.Manifold(
        m3d.Mesh64(
            vert_properties=np.ascontiguousarray(vertices, dtype=np.float64),
            tri_verts=np.ascontiguousarray(faces, dtype=np.uint64),
        )
    )
    status = m.status()
    out = {
        "status": status.name,
        "ok": status == m3d.Error.NoError,
        "triangles": int(m.num_tri()),
        "vertices": int(m.num_vert()),
        "genus": int(m.genus()),
        "volume_mm3": float(m.volume()),
        "seconds": time.perf_counter() - t0,
    }
    return out


def manifold3d_check(
    mesh: Mesh,
    *,
    memory_limit_bytes: int | None = None,
    in_subprocess: bool = True,
) -> dict:
    """Run manifold3d on ``mesh``; returns a dict (``skipped`` with a reason if the
    estimated memory would exceed ``memory_limit_bytes``)."""
    est = estimate_manifold3d_bytes(mesh)
    base = {"estimate_bytes": est, "in_subprocess": bool(in_subprocess)}
    if memory_limit_bytes is not None:
        rss = psutil.Process().memory_info().rss
        if rss + est > memory_limit_bytes:
            return base | {
                "skipped": f"estimated {est / 2**30:.2f} GiB + {rss / 2**30:.2f} GiB in use "
                f"exceeds the {memory_limit_bytes / 2**30:.2f} GiB budget"
            }
    if mesh.n_faces == 0:
        return base | {"skipped": "empty mesh"}
    if not in_subprocess:
        return base | _check(mesh.vertices, mesh.faces)
    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=worker_parent_guard,
    ) as ex:
        return base | ex.submit(_check, mesh.vertices, mesh.faces).result()
