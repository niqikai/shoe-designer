"""M0.5 mesh health checks. Manufacturing checks are implemented in M3."""
import bmesh
import numpy as np
from mathutils.bvhtree import BVHTree


def coordinates(mesh):
    values = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", values)
    return values.reshape((-1, 3))


def triangles(mesh):
    mesh.calc_loop_triangles()
    values = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", values)
    return values.reshape((-1, 3))


def surface_tree(mesh):
    return BVHTree.FromPolygons(coordinates(mesh).tolist(), triangles(mesh).tolist(), all_triangles=True)


def boundary_loops(edges):
    """Order existing boundary edges without inventing or editing vertices."""
    remaining = set(edges)
    adjacency = {}
    for edge in remaining:
        for vert in edge.verts:
            adjacency.setdefault(vert, []).append(edge)
    if any(len(connected) != 2 for connected in adjacency.values()):
        raise ValueError("边界包含分叉或未闭合链，拒绝自动封口。")
    loops = []
    while remaining:
        first_edge = min(remaining, key=lambda edge: edge.index)
        start = first_edge.verts[0]
        current = start
        edge = first_edge
        loop = []
        while True:
            loop.append(current)
            remaining.remove(edge)
            current = edge.other_vert(current)
            if current == start:
                break
            options = [candidate for candidate in adjacency[current] if candidate in remaining]
            if len(options) != 1:
                raise ValueError("无法确定唯一闭合边界，拒绝自动封口。")
            edge = options[0]
        loops.append(loop)
    return loops


def mesh_health(mesh, *, intersections=True, near_duplicate_tolerance_mm=1e-5):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        points = coordinates(mesh)
        triangle_indices = triangles(mesh)
        triangle_points = points[triangle_indices]
        # BMesh's float polygon-area accumulator can return zero for valid
        # micron-scale cap triangles at a large coordinate offset. Measure
        # cross products of local edge vectors in float64 instead.
        triangle_areas = np.linalg.norm(np.cross(triangle_points[:, 1] - triangle_points[:, 0],
                                                triangle_points[:, 2] - triangle_points[:, 0]), axis=1) * 0.5
        polygon_indices = np.empty(len(mesh.loop_triangles), dtype=np.int32)
        mesh.loop_triangles.foreach_get("polygon_index", polygon_indices)
        finite = bool(np.isfinite(points).all())
        boundary = sum(edge.is_boundary for edge in bm.edges)
        nonmanifold = sum(not edge.is_manifold for edge in bm.edges)
        bad_vertices = sum(not vert.is_manifold for vert in bm.verts)
        bad_winding = sum(edge.is_manifold and not edge.is_contiguous for edge in bm.edges)
        degenerate = len(np.unique(polygon_indices[triangle_areas <= 1e-10]))
        duplicates = len(points) - len(np.unique(points, axis=0)) if len(points) else 0
        near = len(bmesh.ops.find_doubles(bm, verts=list(bm.verts), dist=near_duplicate_tolerance_mm)["targetmap"])
        remaining = set(bm.verts)
        components = 0
        while remaining:
            stack = [remaining.pop()]
            components += 1
            while stack:
                vert = stack.pop()
                for edge in vert.link_edges:
                    other = edge.other_vert(vert)
                    if other in remaining:
                        remaining.remove(other)
                        stack.append(other)
        volume = float(bm.calc_volume(signed=True)) if len(bm.faces) else 0.0
        intersection_pairs = None
        examples = []
        if intersections and finite and len(bm.faces):
            tree = surface_tree(mesh)
            pairs = sorted({tuple(sorted(pair)) for pair in tree.overlap(tree) if pair[0] != pair[1]})
            intersection_pairs = len(pairs)
            examples = [list(pair) for pair in pairs[:8]]
        closed = bool(len(bm.faces) and boundary == 0 and nonmanifold == 0 and bad_vertices == 0)
        passed = (closed and finite and components == 1 and bad_winding == 0 and degenerate == 0
                  and duplicates == 0 and near == 0 and volume > 0 and intersection_pairs == 0)
        return {
            "status": "pass" if passed else ("not_fully_checked" if not intersections and closed else "fail"),
            "vertices": len(bm.verts), "edges": len(bm.edges), "polygons": len(bm.faces),
            "triangles": len(triangle_indices), "finite_coordinates": finite,
            "bounds_mm": {"min": points.min(axis=0).tolist(), "max": points.max(axis=0).tolist()} if len(points) else None,
            "dimensions_mm_xyz": np.ptp(points, axis=0).tolist() if len(points) else [0, 0, 0],
            "closed": closed, "boundary_edges": boundary, "non_manifold_edges": nonmanifold,
            "non_manifold_vertices": bad_vertices, "connected_components": components,
            "inconsistent_winding_edges": bad_winding,
            "normal_orientation": "outward" if closed and bad_winding == 0 and volume > 0 else "unconfirmed",
            "globally_inverted": bool(closed and bad_winding == 0 and volume < 0),
            "degenerate_faces": degenerate, "duplicate_vertices": duplicates,
            "near_duplicate_vertices": near, "near_duplicate_tolerance_mm": near_duplicate_tolerance_mm,
            "signed_volume_mm3": volume, "surface_area_mm2": float(triangle_areas.sum()),
            "area_method": "float64 cross products of local triangle edges; degenerate threshold 1e-10 mm2",
            "self_intersection_pairs": intersection_pairs, "intersection_examples_triangle_indices": examples,
            "intersection_method": "Blender BVH triangle intersection; float arithmetic; not an exact proof for coplanar overlaps",
            "manufacturing_status": "not_checked_M3",
        }
    finally:
        bm.free()


def require_healthy(mesh, label):
    report = mesh_health(mesh)
    if report["status"] != "pass":
        raise ValueError(f"{label} 未通过网格健康检查，已停止保存和导出：{report}")
    return report
