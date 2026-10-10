"""Sampled implicit surfaces, extracted by Lewiner Marching Cubes.

Every vertex is computed by the mesher from a scalar field. No shoe mesh or
vertex list is authored. Project-local binary dependencies match Blender Python.
"""
from pathlib import Path
import json
import sys

import bmesh
import bpy
import numpy as np


def geometry_dependencies():
    site = Path(__file__).resolve().parents[2] / ".venv/lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if not site.is_dir():
        raise RuntimeError("缺少项目几何依赖，请先运行 python3 tools/setup_geometry.py。")
    if str(site) not in sys.path:
        sys.path.append(str(site))
    from scipy.ndimage import map_coordinates, distance_transform_edt
    from skimage.measure import marching_cubes
    return marching_cubes, map_coordinates, distance_transform_edt


def mesh_from_field(field, origin, spacing, name):
    """Preserve healthy level-zero geometry; break failed numerical ties inward.

    The sole skin can coincide almost exactly with a sample plane. Welding
    its near-coincident triangles can pinch a valid implicit surface into a
    non-manifold mesh. Bounded negative scalar-level retries move that tie
    off the grid without adding field material. Both candidates retain the
    complete mesh-health gate; the engine checks the final shoe again.

    The level is measured in the implicit field's engineering millimetre
    scale. This field is not an exact signed distance, so the scalar shift is
    not a bound on physical surface displacement or wall thickness.
    """
    from engine.validate import mesh_health

    marching_cubes, _, _ = geometry_dependencies()
    if not np.isfinite(field).all():
        raise ValueError("隐式场包含非有限数值。")
    for axis in range(3):
        if np.any(np.take(field, [0, -1], axis=axis) <= 0):
            raise ValueError("模型接触体素边界，无法保证封闭。")
    attempts = []
    for fraction in (0.0, -1e-4, -2e-4, -5e-4, -1e-3):
        level = float(spacing) * fraction
        verts, faces, _, _ = marching_cubes(field, level=level, spacing=(spacing,) * 3,
                                           gradient_direction="ascent", allow_degenerate=False)
        verts += np.asarray(origin)
        mesh = bpy.data.meshes.new(name)
        accepted = False
        try:
            mesh.from_pydata(verts.tolist(), [], faces.tolist())
            mesh.update()
            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                # Keep the established cleanup and numerical tolerances.
                for _ in range(2):
                    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=2e-5)
                    bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=2e-5)
                    bmesh.ops.triangulate(bm, faces=list(bm.faces))
                bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                if bm.calc_volume(signed=True) < 0:
                    bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
                bm.to_mesh(mesh)
            finally:
                bm.free()
            mesh.update()
            health = mesh_health(mesh)
            attempts.append({"level_field_mm": level, "status": health["status"], "mesh_health": health})
            if health["status"] != "pass":
                continue
            mesh["numerical_merge_tolerance_mm"] = 2e-5
            mesh["extraction_requested_level_field_mm"] = 0.0
            mesh["extraction_level_field_mm"] = level
            mesh["extraction_attempts_json"] = json.dumps(attempts, ensure_ascii=False, allow_nan=False)
            mesh["extraction_method"] = "Lewiner Marching Cubes; level zero first; bounded inward numerical tie-breaks"
            mesh["extraction_level_units"] = "implicit field engineering mm scale; not an exact signed distance or physical displacement bound"
            accepted = True
            return mesh
        finally:
            if not accepted:
                bpy.data.meshes.remove(mesh)
    raise ValueError("Marching Cubes 原提取及有界数值回退均未通过完整网格健康检查：" + repr(attempts))


def sample_map(values, points_xy, origin_xy, spacing):
    _, map_coordinates, _ = geometry_dependencies()
    indices = ((np.asarray(points_xy) - np.asarray(origin_xy)) / spacing).T
    return map_coordinates(values, indices, order=1, mode="nearest")
