"""Milestone 1 mesh checks. Manufacturing qualification belongs to later stages."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from shoe_poc.reporting import utc_now


def singular_vertex_links(mesh):
    """A closed manifold vertex has one connected cyclic triangle link.

    Two sheets meeting only at a vertex can pass edge incidence and watertight
    checks. Retain their real locations instead of treating them as valid walls.
    """
    singular = []
    for index, face_indices in enumerate(mesh.vertex_faces):
        incident = mesh.faces[face_indices[face_indices >= 0]]
        others = incident[incident != index]
        if len(others) != 2 * len(incident):
            singular.append({"vertex_index": index, "point_mm": mesh.vertices[index].tolist(),
                             "reason": "Repeated vertex in incident triangle"})
            continue
        neighbors = {}
        for first, second in others.reshape(-1, 2):
            neighbors.setdefault(first, set()).add(second)
            neighbors.setdefault(second, set()).add(first)
        remaining = set(neighbors)
        components = 0
        while remaining:
            components += 1
            pending = [remaining.pop()]
            while pending:
                for other in neighbors[pending.pop()]:
                    if other in remaining:
                        remaining.remove(other)
                        pending.append(other)
        if components != 1 or any(len(link) != 2 for link in neighbors.values()):
            singular.append({"vertex_index": index, "point_mm": mesh.vertices[index].tolist(),
                             "link_components": components,
                             "cyclic_link": all(len(link) == 2 for link in neighbors.values())})
    return singular


def validate_mesh(model: Path | trimesh.Trimesh, max_extent_mm: float = 500.0,
                  expected_euler_number: int | None = 2) -> dict:
    mesh = trimesh.load_mesh(model, process=True) if isinstance(model, Path) else model
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise ValueError("Expected a nonempty triangle mesh")
    finite = bool(np.isfinite(mesh.vertices).all())
    edges = np.sort(mesh.edges, axis=1)
    _, incidence = np.unique(edges, axis=0, return_counts=True)
    boundary = int(np.count_nonzero(incidence == 1))
    nonmanifold = int(np.count_nonzero(incidence > 2))
    # Count adjacency components directly: split() may repair submeshes implicitly.
    components = len(trimesh.graph.connected_components(mesh.face_adjacency,
                     nodes=np.arange(len(mesh.faces)), min_len=1, engine="scipy"))
    extents = mesh.extents.tolist() if finite else None
    volume = float(mesh.volume) if finite else None
    degenerate = int(np.count_nonzero(mesh.area_faces <= 1e-10)) if finite else None
    singular = singular_vertex_links(mesh)
    checks = []

    def check(name, passed, value, threshold):
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "value": value,
                       "threshold": threshold, "source": "Milestone 1 geometry policy",
                       "checked_at": utc_now()})

    check("finite_vertices", finite, finite, True)
    check("watertight", bool(mesh.is_watertight), bool(mesh.is_watertight), True)
    check("boundary_edges", boundary == 0, boundary, 0)
    check("nonmanifold_edges", nonmanifold == 0, nonmanifold, 0)
    check("nonmanifold_vertex_links", not singular, len(singular), 0)
    check("connected_components", components == 1, components, 1)
    check("consistent_winding", bool(mesh.is_winding_consistent), bool(mesh.is_winding_consistent), True)
    if expected_euler_number is not None:
        # A continuous, unperforated base upper/sole with one entry cavity has
        # genus zero. Watertight meshes can still have unwanted through-holes.
        check("base_shell_euler_number", mesh.euler_number == expected_euler_number,
              int(mesh.euler_number), expected_euler_number)
    check("positive_volume_mm3", finite and volume > 0, volume, "> 0")
    check("model_extents_mm", finite and bool(np.all(mesh.extents > 0))
          and bool(np.all(mesh.extents <= max_extent_mm)), extents, [0, max_extent_mm])
    check("degenerate_triangles", finite and degenerate == 0, degenerate, 0)
    for name in ("global_self_intersections", "minimum_wall_thickness", "SLS_TPU_process_rules",
                 "wearability", "physical_print_validation"):
        checks.append({"name": name, "status": "NOT_VERIFIED", "value": None,
                       "threshold": None, "source": "Not implemented or not qualified in Milestone 1",
                       "checked_at": utc_now()})
    return {"scope": "Milestone 1 mesh integrity; not manufacturing acceptance",
            "geometry_status": "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS",
            "manufacturing_status": "NOT_VERIFIED", "overall_status": "PARTIAL",
            "bounds_mm": mesh.bounds.tolist() if finite else None, "extents_mm": extents,
            "volume_mm3": volume, "vertices": len(mesh.vertices), "triangles": len(mesh.faces),
            "euler_number": int(mesh.euler_number),
            "singular_vertices": singular,
            "checks": checks}
