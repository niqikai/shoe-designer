"""Sampled implicit surfaces, extracted by Lewiner Marching Cubes.

Every vertex is computed by the mesher from a scalar field. No shoe mesh or
vertex list is authored. Project-local binary dependencies match Blender Python.
"""
from pathlib import Path
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
    marching_cubes, _, _ = geometry_dependencies()
    if not np.isfinite(field).all():
        raise ValueError("隐式场包含非有限数值。")
    for axis in range(3):
        if np.any(np.take(field, [0, -1], axis=axis) <= 0):
            raise ValueError("模型接触体素边界，无法保证封闭。")
    verts, faces, _, _ = marching_cubes(field, level=0, spacing=(spacing,) * 3,
                                       gradient_direction="ascent", allow_degenerate=False)
    verts += np.asarray(origin)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts.tolist(), [], faces.tolist())
    mesh.update()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        # Collapse numerical coincidences far below the sampling precision.
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-5)
        bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=1e-5)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    return mesh


def sample_map(values, points_xy, origin_xy, spacing):
    _, map_coordinates, _ = geometry_dependencies()
    indices = ((np.asarray(points_xy) - np.asarray(origin_xy)) / spacing).T
    return map_coordinates(values, indices, order=1, mode="nearest")
