"""Parameterized geometry entry point.

At M0.5 this builds the closed, cropped last used to establish the design frame.
Soles, upper shells and print exports are added in M1, after visual approval.
"""
import bpy

from engine.params import normalize_params
from engine.shoe.last import cut_last, load_last
from engine.validate import require_healthy


def build_shoe(params, *, last_mesh=None):
    effective, warnings = normalize_params(params)
    source = last_mesh if last_mesh is not None else load_last()
    mesh = cut_last(source, effective["collar_height_mm"])
    label = effective["collar_style"] + "_last_candidate"
    require_healthy(mesh, label)
    obj = bpy.data.objects.new(label, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["stage"] = "M0.5_last_only"
    obj["collar_height_mm"] = effective["collar_height_mm"]
    obj["clamp_messages"] = "\n".join(warnings)
    return obj
