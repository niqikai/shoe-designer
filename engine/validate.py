"""Mesh health and M3 manufacturing export decisions."""
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


def _coplanar_overlap_area(first, second, normal):
    """Double-precision 2D triangle clipping for adjacent-face contact tests."""
    axis = int(np.abs(normal).argmax())
    a, b = np.delete(first, axis, axis=1), np.delete(second, axis, axis=1)
    cross = lambda u, v: u[0] * v[1] - u[1] * v[0]
    sign = 1 if cross(b[1] - b[0], b[2] - b[0]) >= 0 else -1
    polygon = list(a)
    for p, q in zip(b, np.roll(b, -1, axis=0)):
        clipped = []
        for s, t in zip(polygon, polygon[1:] + polygon[:1]):
            ds, dt = sign * cross(q - p, s - p), sign * cross(q - p, t - p)
            if ds >= 0:
                clipped.append(s)
            if (ds >= 0) != (dt >= 0):
                clipped.append(s + (t - s) * ds / (ds - dt))
        polygon = clipped
        if not polygon:
            return 0.0
    return abs(sum(cross(p, q) for p, q in zip(polygon, polygon[1:] + polygon[:1]))) * .5


def _only_shared_contact(points, first_ids, second_ids):
    """Exclude legal shared vertices/edges, while retaining folded overlaps.

    BVH's float overlap can report coplanar neighbour triangles touching only
    at their shared vertex. A double-precision plane/cone check distinguishes
    that contact from intersection beyond the mesh's shared feature.
    """
    shared = set(first_ids) & set(second_ids)
    if not shared or len(shared) == 3:
        return False
    a, b = points[first_ids], points[second_ids]
    na, nb = np.cross(a[1] - a[0], a[2] - a[0]), np.cross(b[1] - b[0], b[2] - b[0])
    lengths = np.linalg.norm(na), np.linalg.norm(nb)
    if min(lengths) < 1e-12:
        return False
    na, nb = na / lengths[0], nb / lengths[1]
    direction = np.cross(na, nb)
    scale = max(np.ptp(np.vstack((a, b)), axis=0).max(), 1.0)
    if np.linalg.norm(direction) < 1e-7:
        return _coplanar_overlap_area(a, b, na) < 1e-12 * scale ** 2
    if len(shared) == 2:
        return True  # Distinct planes intersect only along their shared edge.
    origin = points[next(iter(shared))]
    direction /= np.linalg.norm(direction)
    def in_cone(ids, ray):
        u, v = [points[i] - origin for i in ids if i not in shared]
        uu, uv, vv = u @ u, u @ v, v @ v
        determinant = uu * vv - uv * uv
        if determinant < 1e-20:
            return False
        ru, rv = ray @ u, ray @ v
        alpha = (ru * vv - rv * uv) / determinant
        beta = (rv * uu - ru * uv) / determinant
        return alpha >= -1e-10 and beta >= -1e-10
    return not any(in_cone(first_ids, sign * direction) and in_cone(second_ids, sign * direction) for sign in (-1, 1))


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
            raw_pairs = sorted({tuple(sorted(pair)) for pair in tree.overlap(tree) if pair[0] != pair[1]})
            pairs = [pair for pair in raw_pairs if not _only_shared_contact(points, triangle_indices[pair[0]], triangle_indices[pair[1]])]
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
            "intersection_method": "Blender float BVH candidates; float64 shared-feature contact filtering; not an exact proof for coplanar overlaps",
            "manufacturing_status": "not_checked_M3",
        }
    finally:
        bm.free()


def require_healthy(mesh, label):
    report = mesh_health(mesh)
    if report["status"] != "pass":
        raise ValueError(f"{label} 未通过网格健康检查，已停止保存和导出：{report}")
    return report


def validate_manufacturing(mesh, params, health, measured):
    """Combine scoped checks; a failed or missing required check blocks export."""
    from engine.manufacturing_pose import check_fdm_overhang, plan_build_pose
    pose = plan_build_pose(coordinates(mesh), params)
    overhang = (check_fdm_overhang(coordinates(mesh), triangles(mesh), pose)
                if params["print_process"] == "FDM" else
                {"status": "not_applicable", "message": "粉末工艺不使用 FDM 悬垂角规则；热变形与工艺朝向需打印方确认。"})
    checks = {"mesh": health, "thickness": measured.get("thickness", {"status": "fail"}),
              "powder_removal": measured.get("powder_removal", {"status": "fail"}),
              "build_volume": pose, "overhang": overhang}
    blockers = [key for key, check in checks.items() if check.get("status") not in ("pass", "warning", "not_applicable")]
    warnings = [check["message"] for check in checks.values() if check.get("status") == "warning" and "message" in check]
    warnings.append("尚未指定实际 TPU 牌号、设备与打印配置；本报告只按项目经验规则筛查，制造前须由打印方确认并试印局部样件。")
    return {**measured, "status": "fail" if blockers else "warning", "export_allowed": not blockers,
            "blockers": blockers, "warnings": warnings, "build_volume": pose, "overhang": overhang,
            "summary": ("必要制造检查未通过，本轮不导出打印网格。" if blockers else
                        "项目自动几何筛查通过，可导出带制造警告的候选；尚未获得具体工艺认证。"),
            "export_policy": "no STL/GLB on any required fail/missing check; scoped warnings accompany candidates"}
