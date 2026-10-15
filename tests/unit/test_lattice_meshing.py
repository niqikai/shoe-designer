import numpy as np
import pytest
import trimesh

from shoe_poc.lattice.gyroid import GyroidParameters, SampledField, SamplingGrid, sample_gyroid
from shoe_poc.lattice.meshing import extract_lattice_mesh, export_lattice, mesh_integrity


def make_field(*, period=12, threshold=.6, spacing=.4, phase=(0, 0, 0)):
    low, high = np.array([21, -7, 4]), np.array([33, 5, 16])
    grid = SamplingGrid.from_bounds([low, high], spacing)
    axes = grid.axes
    d = [np.maximum(low[i] - axes[i], axes[i] - high[i]) for i in range(3)]
    domain = np.maximum(np.maximum(d[0][:, None, None], d[1][None, :, None]), d[2][None, None, :]).astype(np.float32)
    return sample_gyroid(GyroidParameters(period, threshold, spacing, phase), grid, domain_signed_field=domain)


def test_closed_clipped_gyroid_real_mm_xyz_and_stl_roundtrip(tmp_path):
    result = extract_lattice_mesh(make_field())
    assert result.metadata["integrity"]["status"] == "PASS"
    assert result.mesh.is_watertight and result.mesh.is_winding_consistent
    assert np.all(result.mesh.bounds[0] >= np.array([21, -7, 4]) - 1e-5)
    assert np.all(result.mesh.bounds[1] <= np.array([33, 5, 16]) + 1e-5)
    assert result.mesh.volume > 0
    path = export_lattice(result, tmp_path / "lattice.stl")
    reloaded = trimesh.load_mesh(path, process=True)
    assert mesh_integrity(reloaded)["status"] == "PASS"
    assert np.allclose(reloaded.bounds, result.mesh.bounds, atol=1e-5)
    assert reloaded.volume == pytest.approx(result.mesh.volume, rel=1e-5)


def test_period_threshold_phase_and_spacing_change_extracted_geometry():
    reference = extract_lattice_mesh(make_field()).mesh
    variants = [extract_lattice_mesh(field).mesh for field in (
        make_field(period=14), make_field(threshold=.4), make_field(spacing=.3), make_field(phase=(1, 2, 3)))]
    for other in variants:
        assert reference.volume != pytest.approx(other.volume, rel=1e-5)
        assert len(reference.faces) != len(other.faces) or not np.allclose(reference.vertices, other.vertices)


def test_disconnected_implicit_material_components_are_retained():
    grid = SamplingGrid.from_bounds([[-5, -5, -5], [5, 5, 5]], .25)
    x, y, z = (axis.reshape(shape) for axis, shape in zip(grid.axes, ((-1, 1, 1), (1, -1, 1), (1, 1, -1))))
    values = np.minimum((x - 2) ** 2 + y ** 2 + z ** 2 - 1,
                        (x + 2) ** 2 + y ** 2 + z ** 2 - 1).astype(np.float32)
    result = extract_lattice_mesh(SampledField(values, grid.origin_mm, .25, {}))
    assert result.metadata["integrity"]["connected_components"] == 2
    assert result.metadata["components_removed"] == 0
    assert result.metadata["integrity"]["status"] == "PASS"


def test_large_world_origin_does_not_quantize_native_vertices():
    field = make_field()
    local = extract_lattice_mesh(field).mesh
    offset = np.array([1_000_000.0, 2_000_000.0, -3_000_000.0])
    translated = SampledField(field.values, tuple(np.array(field.origin_mm) + offset), field.spacing_mm, field.metadata)
    distant = extract_lattice_mesh(translated).mesh
    assert np.allclose(distant.vertices - offset, local.vertices, atol=1e-8, rtol=0)


def test_open_boundary_and_memory_budget_fail_before_native_extraction():
    field = make_field()
    with pytest.raises(ValueError, match="meshing peak"):
        extract_lattice_mesh(field, max_memory_bytes=100)
    field.values[0] = -1
    with pytest.raises(ValueError, match="Open scalar boundary"):
        extract_lattice_mesh(field)


def test_open_and_point_contact_meshes_fail_integrity():
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    assert mesh_integrity(mesh)["status"] == "FAIL"
    contact = trimesh.creation.icosphere(subdivisions=1)
    second = int(np.argmax(np.linalg.norm(contact.vertices - contact.vertices[0], axis=1)))
    contact.faces[contact.faces == second] = 0
    contact.remove_unreferenced_vertices()
    assert mesh_integrity(contact)["singular_vertex_count"] == 1
