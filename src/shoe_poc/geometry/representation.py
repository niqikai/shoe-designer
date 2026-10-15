"""Bounded native B-Rep repair before qualification of the rolled-toe solid.

An offset face can remain valid yet have a wire parameterization that OCCT
cannot tessellate. Repair native CAD before export; never patch the mesh.
"""
from __future__ import annotations

import numpy as np
from pathlib import Path
from tempfile import TemporaryDirectory


def repair_parameterization(shape):
    from build123d import Compound, export_step, import_step
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
    # Canonicalize native curve representations and isolate the input. Fixing
    # the fresh Boolean result directly left a three-edge native mesh gap on
    # an outer offset face; a STEP roundtrip before the same repair removes
    # that discrepancy. All geometry bounds below include this roundtrip.
    with TemporaryDirectory(prefix="shoe-native-representation-") as folder:
        path = Path(folder)/"canonical.step"
        if not export_step(shape, path):
            raise ValueError("Native representation canonical STEP export failed")
        canonical = import_step(path)
    fixer = ShapeFix_Shape(canonical.wrapped)
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
    return result, {"method": "Temporary STEP canonicalization then OCCT ShapeFix_Shape on isolated native CAD before export and final validation",
                    "canonicalization": "STEP roundtrip; its geometric effect is included in the bounds below",
                    "performed": bool(performed), "geometry_status": "PASS",
                    "precision_mm": 1e-7, "maximum_repair_tolerance_mm": 1e-5,
                    "bbox_max_delta_mm": delta, "bbox_delta_limit_mm": 1e-5,
                    "symmetric_difference_volume_mm3": difference,
                    "symmetric_difference_limit_mm3": .01,
                    "before_face_count": len(shape.faces()), "after_face_count": len(result.faces()),
                    "mesh_hole_filling": False, "mesh_vertex_or_normal_changes": False,
                    "scope": "Bounded native CAD representation repair; downstream containment, wall, junction and complete tessellation checks remain required; no global exact-equivalence proof"}
