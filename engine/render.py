"""Deterministic orthographic evidence views rendered by Blender, with a mm ruler."""
from pathlib import Path
import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector

VIEWS = ("side", "top", "front", "iso")


def material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    return mat


def _camera_text(camera, text, position, size, mat, temporary):
    data = bpy.data.curves.new("preview_label", "FONT")
    data.body = text
    data.size = size
    data.materials.append(mat)
    obj = bpy.data.objects.new("preview_label", data)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = camera
    obj.location = position
    temporary.append(obj)


def _ruler(camera, view_height, view_width, mat, temporary):
    x, y = -view_width * 0.43, -view_height * 0.40
    for offset, scale in ((25, (50, 0.45, 0.2)), (0, (0.45, 3, 0.2)), (50, (0.45, 3, 0.2))):
        bpy.ops.mesh.primitive_cube_add(size=1)
        obj = bpy.context.object
        obj.name = "ruler_50mm"
        obj.parent = camera
        obj.location = (x + offset, y, -200)
        obj.scale = scale
        obj.data.materials.append(mat)
        temporary.append(obj)
    _camera_text(camera, "50 mm", (x, y - view_height * 0.045, -200), view_height * 0.026, mat, temporary)


def render_views(objects, frame_object, output_dir, prefix, title, *, views=VIEWS, foot="unconfirmed", footer=None):
    scene = bpy.context.scene
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 700
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world = scene.world or bpy.data.worlds.new("preview_world")
    scene.world.color = (0.93, 0.95, 0.95)
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "MATERIAL"
    shading.background_type = "WORLD"
    shading.show_shadows = True
    shading.show_cavity = True
    shading.cavity_type = "BOTH"
    shading.curvature_ridge_factor = 1.3
    shading.curvature_valley_factor = 1.0
    scene.display.render_aa = "16"
    original_visibility = {obj: obj.hide_render for obj in scene.objects}
    original_camera = scene.camera
    for obj in scene.objects:
        obj.hide_render = obj not in objects
    corners = [frame_object.matrix_world @ Vector(corner) for corner in frame_object.bound_box]
    target = sum(corners, Vector()) / 8
    directions = {"side": Vector((1, 0, 0)), "top": Vector((0, 0, 1)),
                  "front": Vector((0, 1, 0)), "iso": Vector((1, 1, math.sqrt(2)))}
    dark = material("annotation", (0.055, 0.10, 0.13))
    temporary = []
    camera_data = bpy.data.cameras.new("inspection_camera")
    camera = bpy.data.objects.new("inspection_camera", camera_data)
    scene.collection.objects.link(camera)
    temporary.append(camera)
    scene.camera = camera
    camera_data.type = "ORTHO"
    camera_data.clip_start = 0.1
    camera_data.clip_end = 5000
    files = {}
    try:
        for view in views:
            per_view = []
            camera.location = target + directions[view].normalized() * 800
            camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
            bpy.context.view_layer.update()
            local = [camera.matrix_world.inverted() @ point for point in corners]
            width = max(p.x for p in local) - min(p.x for p in local)
            height = max(p.y for p in local) - min(p.y for p in local)
            camera_data.ortho_scale = 1
            unit_frame = camera_data.view_frame(scene=scene)
            unit_width = max(p.x for p in unit_frame) - min(p.x for p in unit_frame)
            unit_height = max(p.y for p in unit_frame) - min(p.y for p in unit_frame)
            camera_data.ortho_scale = max(height * 1.65 / unit_height, width * 1.38 / unit_width)
            view_width = camera_data.ortho_scale * unit_width
            view_height = camera_data.ortho_scale * unit_height
            _camera_text(camera, title, (-view_width * 0.43, view_height * 0.43, -200), view_height * 0.043, dark, per_view)
            label = {"side": "SIDE | toe ->", "top": "TOP | toe +Y up", "front": "FRONT | viewed from toe", "iso": "45 DEG | orthographic"}[view]
            _camera_text(camera, label, (-view_width * 0.43, view_height * 0.38, -200), view_height * 0.026, dark, per_view)
            _camera_text(camera, footer or f"inferred {foot.upper()} | LAST ONLY", (-view_width * 0.43, -view_height * 0.34, -200), view_height * 0.024, dark, per_view)
            _ruler(camera, view_height, view_width, dark, per_view)
            scene.render.filepath = str(output_dir / f"{prefix}_{view}.png")
            bpy.context.view_layer.update()
            bpy.ops.render.render(write_still=True)
            files[view] = scene.render.filepath
            for obj in per_view:
                bpy.data.objects.remove(obj, do_unlink=True)
        return files
    finally:
        for obj in temporary:
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        for obj, hidden in original_visibility.items():
            obj.hide_render = hidden
        scene.camera = original_camera


def contact_sheet(files, destination, *, columns=2):
    """Compose Blender evidence images without another Python package."""
    loaded = [bpy.data.images.load(str(path), check_existing=False) for path in files]
    try:
        width, height = loaded[0].size
        if any(tuple(image.size) != (width, height) for image in loaded):
            raise ValueError("拼图输入尺寸不一致。")
        rows = math.ceil(len(loaded) / columns)
        pixels = np.ones((rows * height, columns * width, 4), dtype=np.float32)
        for i, source in enumerate(loaded):
            buffer = np.empty(width * height * 4, dtype=np.float32)
            source.pixels.foreach_get(buffer)
            row, col = divmod(i, columns)
            start_y = (rows - 1 - row) * height
            pixels[start_y:start_y + height, col * width:(col + 1) * width] = buffer.reshape((height, width, 4))
        # Loaded PNG byte pixels are already in display space. Save a byte
        # image directly, rather than applying the render view transform twice.
        result = bpy.data.images.new("evidence_contact_sheet", width=columns * width, height=rows * height, alpha=False, float_buffer=False)
        result.colorspace_settings.name = "sRGB"
        result.pixels.foreach_set(pixels.reshape(-1))
        result.file_format = "PNG"
        result.filepath_raw = str(destination)
        result.save()
        bpy.data.images.remove(result)
    finally:
        for image in loaded:
            bpy.data.images.remove(image)


def render_cutaway(obj, output_dir, lattice_name):
    """Temporary open half-model for inspection only; never export this mesh."""
    mesh = obj.data.copy()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
                               plane_co=(0, 0, 0), plane_no=(1, 0, 0), dist=1e-5,
                               clear_outer=True, clear_inner=False)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    cut = bpy.data.objects.new("inspection_cutaway_not_for_export", mesh)
    bpy.context.scene.collection.objects.link(cut)
    try:
        return render_views([cut], obj, output_dir, "cutaway", lattice_name.upper() + " | INTERNAL SECTION",
                            views=("side", "iso"), foot=obj["foot_side"], footer="VISUAL CUTAWAY ONLY | Not a print mesh")
    finally:
        bpy.data.objects.remove(cut, do_unlink=True)
        bpy.data.meshes.remove(mesh)


def render_thin_locations(obj, thickness, output_dir):
    """Overlay diagnostic markers on a temporary render, never on exports."""
    markers = []
    red = material("thin_location_marker", (.9, .08, .025))
    try:
        for item in thickness.get("thin_examples", []):
            bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=2,
                                                location=item["position_mm"])
            marker = bpy.context.object
            marker.data.materials.append(red)
            markers.append(marker)
        return render_views([obj, *markers], obj, output_dir, "thin", "M3 | THIN EDGE LOCATIONS",
                            views=("side", "iso"), footer=f"RED = {len(markers)} OF {thickness['thin_sample_count']} THIN SAMPLES | MARKERS ONLY")
    finally:
        for marker in markers:
            mesh = marker.data
            bpy.data.objects.remove(marker, do_unlink=True)
            bpy.data.meshes.remove(mesh)
