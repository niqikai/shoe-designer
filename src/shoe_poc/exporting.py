"""STL serialization without zero-area records from CAD pole degeneracies."""
from pathlib import Path
import struct

import numpy as np


def remove_exact_zero_area_records(path: Path) -> dict:
    """Preserve every positive-area triangle byte for byte, with no tolerance.

    OCCT's pole tessellation can emit duplicate-vertex or collinear triangles.
    Such records represent no surface area and interfere with mesh topology.
    This does not fill holes, relocate vertices, change normals or smooth CAD.
    """
    payload = path.read_bytes()
    if len(payload) < 84:
        raise ValueError("Truncated binary STL")
    count = struct.unpack_from("<I", payload, 80)[0]
    if len(payload) != 84 + 50 * count:
        raise ValueError("Expected complete binary STL records")
    dtype = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)),
                      ("attribute", "<u2")])
    records = np.frombuffer(payload, dtype=dtype, count=count, offset=84)
    vertices = records["vertices"].astype(np.float64)
    area_vectors = np.cross(vertices[:, 1] - vertices[:, 0],
                            vertices[:, 2] - vertices[:, 0])
    zero_area = np.all(area_vectors == 0., axis=1)
    discarded = int(np.count_nonzero(zero_area))
    if discarded:
        retained = records[~zero_area]
        path.write_bytes(payload[:80] + struct.pack("<I", len(retained)) + retained.tobytes())
    return {"native_triangle_count": count, "discarded_exact_zero_area_records": discarded,
            "retained_triangle_count": count - discarded,
            "positive_area_triangles_preserved_byte_for_byte": True,
            "area_tolerance": 0., "hole_filling": False, "vertex_or_normal_changes": False}


def export_mesh(shape, path: Path, tolerance=.15, angular_tolerance=.1) -> dict:
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.StlAPI import StlAPI_Writer
    # build123d 0.10 export_stl uses relative deflection. Explicitly use absolute
    # millimetres, and clear only cached triangulations before native remeshing.
    BRepTools.Clean_s(shape.wrapped)
    mesh = BRepMesh_IncrementalMesh(shape.wrapped, tolerance, False, angular_tolerance, True)
    mesh.Perform()
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(shape.wrapped, str(path)):
        raise ValueError(f"Native STL export failed: {path}")
    report = remove_exact_zero_area_records(path)
    report.update({"linear_deflection_mm": tolerance, "angular_deflection_radians": angular_tolerance,
                   "relative_deflection": False, "cached_triangulations_cleared": True})
    return report
