"""Bounded native B-Rep repair before qualification of the rolled-toe solid.

An offset face can remain valid yet have a wire parameterization that OCCT
cannot tessellate. Repair native CAD before export; never patch the mesh.
"""
from __future__ import annotations

import numpy as np


def repair_parameterization(shape):
    from build123d import Compound
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.ShapeFix import ShapeFix_Shape
    from shoe_poc.validation.volume import measure_cad_volume

    def material_volume(member):
        if isinstance(member, (tuple, list)):
            return sum(material_volume(s) for s in member)
        if member is None or member.wrapped is None or member.wrapped.IsNull() or not member.solids():
            return 0.
        return measure_cad_volume(member)["volume_mm3"]

    if not shape.is_valid or len(shape.solids()) != 1:
        raise ValueError("Representation repair requires one valid input solid")
    # Isolate any native edge/surface changes from the preserved input.
    copied = BRepBuilderAPI_Copy(shape.wrapped, True, False).Shape()
    fixer = ShapeFix_Shape(copied)
    fixer.SetPrecision(1e-7)
    fixer.SetMaxTolerance(1e-5)
    performed = fixer.Perform()
    result = Compound.cast(fixer.Shape())
    if not result.is_valid or len(result.solids()) != 1:
        raise ValueError("Native representation repair did not retain one valid solid")
    bounds = lambda s: np.array([tuple(s.bounding_box().min), tuple(s.bounding_box().max)])
    delta = float(np.max(np.abs(bounds(result)-bounds(shape))))
    difference = material_volume(shape.cut(result))+material_volume(result.cut(shape))
    if delta > 1e-5 or difference > .01:
        raise ValueError(f"Native representation repair changed bounded geometry: bbox {delta} mm, symmetric difference {difference} mm3")
    return result, {"method": "OCCT ShapeFix_Shape on an isolated native CAD copy before export and final validation",
                    "performed": bool(performed), "geometry_status": "PASS",
                    "precision_mm": 1e-7, "maximum_repair_tolerance_mm": 1e-5,
                    "bbox_max_delta_mm": delta, "bbox_delta_limit_mm": 1e-5,
                    "symmetric_difference_volume_mm3": difference,
                    "symmetric_difference_limit_mm3": .01,
                    "before_face_count": len(shape.faces()), "after_face_count": len(result.faces()),
                    "mesh_hole_filling": False, "mesh_vertex_or_normal_changes": False,
                    "scope": "Bounded native CAD representation repair; downstream containment, wall, junction and complete tessellation checks remain required; no global exact-equivalence proof"}
