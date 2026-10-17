"""Hard checks on the extracted mesh (ADR 0004): failing any of them gives exit code 5."""

from __future__ import annotations

import numpy as np

from shoegen.checks.records import FAIL, PASS, record, source
from shoegen.mesh import MeshResult
from shoegen.profile import ManufacturingProfile


def run_hard_checks(result: MeshResult, profile: ManufacturingProfile) -> list[dict]:
    """Watertight, manifold, one connected component, fits the build volume.

    The build-volume test uses the mesh axis-aligned bounding box as it is (no rotation
    search): ``dims <= profile.build_volume_mm`` per axis. The dimensions are reported.
    """
    val = result.validation
    generic = source(profile, None)
    mesh = result.mesh
    if mesh.n_vertices:
        lo = mesh.vertices.min(axis=0)
        hi = mesh.vertices.max(axis=0)
        dims = hi - lo
    else:
        lo = hi = dims = np.zeros(3)
    # A sealed cavity is a second surface shell with negative signed volume; it is not a
    # second solid. Solid bodies are the shells that enclose positive volume.
    solids = sum(1 for c in val.components if c.volume_mm3 > 0)
    cavity_shells = sum(1 for c in val.components if c.volume_mm3 < 0)
    if not val.components:
        solids = val.n_components
    bv = np.asarray(profile.build_volume_mm, dtype=np.float64)
    fits = bool(np.all(dims <= bv)) and mesh.n_vertices > 0
    return [
        record(
            "watertight",
            "hard",
            PASS if val.watertight else FAIL,
            measured={
                "watertight": val.watertight,
                "open_edges": val.open_edges,
                "nonmanifold_edges": val.nonmanifold_edges,
                "misoriented_edges": val.misoriented_edges,
                "degenerate_triangles": val.degenerate_triangles,
                "triangles": val.triangles,
            },
            threshold={"open_edges": 0, "nonmanifold_edges": 0},
            src=generic,
        ),
        record(
            "manifold",
            "hard",
            PASS if val.manifold else FAIL,
            measured={"manifold": val.manifold, "nonmanifold_vertices": val.nonmanifold_vertices},
            threshold={"nonmanifold_vertices": 0},
            src=generic,
        ),
        record(
            "connected_components",
            "hard",
            PASS if solids == 1 else FAIL,
            measured={
                "components": solids,
                "surface_shells": val.n_components,
                "cavity_shells": cavity_shells,
            },
            threshold={"components": 1},
            detail="solid bodies = surface shells with positive volume; cavity shells excluded",
            src=generic,
        ),
        record(
            "build_volume",
            "hard",
            PASS if fits else FAIL,
            measured={
                "dimensions_mm": dims,
                "aabb_min_mm": lo,
                "aabb_max_mm": hi,
                "orientation": "as modelled (axis-aligned, no rotation search)",
            },
            threshold={"build_volume_mm": list(profile.build_volume_mm)},
            src=source(profile, "build_volume_mm"),
        ),
    ]
