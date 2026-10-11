import numpy as np
import trimesh

from shoe_poc.validation import validate_mesh


def test_closed_mesh():
    report = validate_mesh(trimesh.creation.box(extents=[10, 20, 30]))
    assert report["geometry_status"] == "PASS"
    assert report["manufacturing_status"] == "NOT_VERIFIED"


def test_open_mesh_is_detected():
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    report = validate_mesh(mesh)
    assert report["geometry_status"] == "FAIL"
    assert next(c for c in report["checks"] if c["name"] == "boundary_edges")["value"] > 0


def test_isolated_component_is_detected():
    first = trimesh.creation.box()
    second = first.copy(); second.apply_translation([4, 0, 0])
    report = validate_mesh(trimesh.util.concatenate([first, second]))
    assert next(c for c in report["checks"] if c["name"] == "connected_components")["value"] == 2
    assert report["geometry_status"] == "FAIL"


def test_oversize_mesh_is_detected():
    report = validate_mesh(trimesh.creation.box(extents=[501, 20, 30]))
    assert next(c for c in report["checks"] if c["name"] == "model_extents_mm")["status"] == "FAIL"


def test_nonmanifold_edge_is_detected():
    mesh = trimesh.creation.box()
    mesh.faces = np.vstack([mesh.faces, mesh.faces[0]])
    report = validate_mesh(mesh)
    assert next(c for c in report["checks"] if c["name"] == "nonmanifold_edges")["value"] > 0
    assert report["geometry_status"] == "FAIL"


def test_watertight_through_hole_is_not_a_continuous_base_upper():
    mesh = trimesh.creation.torus(major_radius=5, minor_radius=1)
    assert mesh.is_watertight
    report = validate_mesh(mesh)
    assert next(c for c in report["checks"] if c["name"] == "base_shell_euler_number")["status"] == "FAIL"
    assert report["geometry_status"] == "FAIL"
