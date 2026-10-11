"""Measured B-Rep geometry; no size, side or orientation inferred from extents."""
def analyze_shape(shape) -> dict:
    from OCP.BRepCheck import BRepCheck_Shell
    from OCP.BRepCheck import BRepCheck_Status
    box = shape.bounding_box()
    shells = shape.shells()
    open_shells = sum(BRepCheck_Shell(s.wrapped).Closed() != BRepCheck_Status.BRepCheck_NoError for s in shells)
    return {
        'geometry_type': shape.wrapped.ShapeType().name,
        'solid_count': len(shape.solids()), 'shell_count': len(shells),
        'face_count': len(shape.faces()), 'edge_count': len(shape.edges()),
        'vertex_count': len(shape.vertices()),
        'bbox_mm': {'min': list(box.min), 'max': list(box.max), 'size': list(box.size)},
        'volume_mm3': shape.volume, 'surface_area_mm2': shape.area,
        'topology_valid': bool(shape.is_valid), 'open_shell_count': open_shells,
        'abnormal_geometry': [] if shape.is_valid and open_shells == 0 and shape.volume > 0 else ['Invalid topology, open shell, or non-positive volume'],
        'shoe_size': 'NOT_INFERRED', 'side': 'NOT_INFERRED', 'toe_direction': 'CONFIGURATION_REQUIRED',
    }
