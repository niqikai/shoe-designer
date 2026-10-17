import copy

import numpy as np
import pytest
import trimesh

from shoe_poc.lattice.gyroid import GyroidParameters, SampledField, SamplingGrid, sample_gyroid
from shoe_poc.lattice.meshing import LatticeExtractionError, extract_lattice_mesh, export_lattice, mesh_integrity


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
    # A coarse closed block is representable in STL at these large origins;
    # submillimetre gyroid features may legitimately fail serialization there.
    values = np.ones((7, 7, 7), dtype=np.float32)
    values[2:5, 2:5, 2:5] = -1
    field = SampledField(values, (0., 0., 0.), .5, {})
    local = extract_lattice_mesh(field).mesh
    offset = np.array([1_000_000.0, 2_000_000.0, -3_000_000.0])
    translated = SampledField(field.values, tuple(np.array(field.origin_mm) + offset), field.spacing_mm, field.metadata)
    distant = extract_lattice_mesh(translated).mesh
    assert np.allclose(distant.vertices - offset, local.vertices, atol=1e-8, rtol=0)


def tiny_void_field(positive_value, origin=(0., 0., 0.)):
    values = np.ones((9, 9, 9), dtype=np.float32)
    values[2:7, 2:7, 2:7] = -1
    # A zero surface very close to a node produces an arbitrarily small closed
    # void; no face is deleted to make the numerical contour acceptable.
    values[4, 4, 4] = positive_value
    return SampledField(values, origin, 1., {"threshold": .45, "nested": {"original": True}})


@pytest.mark.parametrize(("positive_value", "origin", "first_native_pass"), [
    (5e-7, (0., 0., 0.), False),
    (3e-6, (100., 100., 100.), True),
])
def test_bounded_inward_contour_handles_native_or_stl_tiny_area_without_mutating_field(
        positive_value, origin, first_native_pass):
    field = tiny_void_field(positive_value, origin)
    original = field.values.copy()
    original_metadata = copy.deepcopy(field.metadata)
    values_object, metadata_object = field.values, field.metadata
    result = extract_lattice_mesh(field)
    attempts = result.metadata["numerical_attempts"]
    assert attempts[0]["level"] == 0 and attempts[0]["status"] == "FAIL"
    assert attempts[0]["integrity"]["checks"]["nondegenerate_triangles"] is first_native_pass
    if first_native_pass:
        assert attempts[0]["serialized_degenerate_triangle_count"] > 0
    else:
        assert attempts[0]["degenerate_triangle_count"] > 0
    assert 2 <= len(attempts) <= 3
    assert attempts[-1]["status"] == "PASS"
    assert attempts[-1]["degenerate_triangle_count"] == attempts[-1]["serialized_degenerate_triangle_count"] == 0
    assert attempts[-1]["minimum_area_mm2"] > 1e-12
    assert attempts[-1]["serialized_minimum_area_mm2"] > 1e-12
    inset = result.metadata["numerical_level_inset_dimensionless"]
    assert inset in (16*np.finfo(np.float32).eps, 128*np.finfo(np.float32).eps)
    assert result.metadata["target_level"] == 0
    assert result.metadata["level"] == -inset
    assert result.metadata["effective_gyroid_threshold"] == .45-inset
    assert result.metadata["numerical_domain_inset_mm"] == field.spacing_mm*inset
    assert result.metadata["components_removed"] == 0
    assert not result.metadata["mesh_repair_applied"]
    assert result.mesh.is_watertight and result.mesh.is_winding_consistent
    assert result.metadata["integrity"]["connected_components"] == 2
    assert field.values is values_object and field.metadata is metadata_object
    assert np.array_equal(field.values, original)
    assert field.metadata == original_metadata


def test_numerical_ladder_stops_after_three_strict_serialization_failures():
    field = tiny_void_field(3e-6, (1_000_000., 1_000_000., 1_000_000.))
    original = field.values.copy()
    with pytest.raises(LatticeExtractionError, match="all bounded numerical contours") as caught:
        extract_lattice_mesh(field)
    attempts = caught.value.metadata["numerical_attempts"]
    assert len(attempts) == 3
    assert [attempt["float32_eps_multiplier"] for attempt in attempts] == [0, 16, 128]
    assert all(attempt["status"] == "FAIL" for attempt in attempts)
    assert all(attempt["serialized_degenerate_triangle_count"] > 0 for attempt in attempts)
    assert np.array_equal(field.values, original)


def test_resource_budget_cannot_be_evaded_by_numerical_retries(monkeypatch):
    import skimage.measure
    field = tiny_void_field(5e-7)
    monkeypatch.setattr(skimage.measure, "marching_cubes", lambda *a, **k: pytest.fail("Allocation preflight must run first"))
    with pytest.raises(LatticeExtractionError, match="meshing peak") as caught:
        extract_lattice_mesh(field, max_memory_bytes=100)
    assert len(caught.value.metadata["numerical_attempts"]) == 1
    assert caught.value.metadata["numerical_attempts"][0]["level"] == 0


def test_valid_target_zero_contour_is_retained_without_retry():
    result = extract_lattice_mesh(make_field())
    assert result.metadata["level"] == result.metadata["target_level"] == 0
    assert result.metadata["numerical_level_inset_dimensionless"] == 0
    assert result.metadata["effective_gyroid_threshold"] == .6
    assert len(result.metadata["numerical_attempts"]) == 1


def test_non_area_integrity_failure_is_never_retried(monkeypatch):
    import shoe_poc.lattice.meshing as meshing
    original = meshing.mesh_integrity
    calls = []

    def reject_winding(mesh):
        report = original(mesh)
        calls.append(report)
        report["checks"]["consistent_winding"] = False
        report["status"] = "FAIL"
        return report

    monkeypatch.setattr(meshing, "mesh_integrity", reject_winding)
    with pytest.raises(LatticeExtractionError, match="non-area integrity") as caught:
        extract_lattice_mesh(tiny_void_field(5e-7))
    assert len(calls) == len(caught.value.metadata["numerical_attempts"]) == 1


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


def test_extraction_failure_roundtrips_between_spawned_workers():
    import pickle
    error = LatticeExtractionError('non-area integrity', [{'level': 0., 'status': 'FAIL'}])
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is type(error)
    assert str(restored) == str(error)
    assert restored.metadata == error.metadata


def test_raw_lewiner_connectivity_survives_float32_grid_node_collision(tmp_path):
    # Minimal 4x4x2 scalar patch of the actual threshold=.45 failure. Positive
    # padding preserves its original grid indices: the x~606 float32 spacing
    # is what makes the old degenerate-face remover merge distinct MC nodes.
    # This fixture needs no CAD input or saved M2 artifacts.
    from skimage.measure import marching_cubes

    values = np.ones((610, 84, 42), dtype=np.float32)
    values[605:609, 79:83, 37:41] = np.float32(1.001)
    values[605:609, 79:83, 37:39] = [
        [[.3710446656, 1.001], [.1526014656, 1.001],
         [-.07674152404, 1.001], [-.1404212713, 1.001]],
        [[.3947588503, 1.001], [.1900104731, -3.330430218e-6],
         [-.02166867442, 1.001], [-.2310271859, 1.001]],
        [[.3679560125, .1798629463], [.1807650328, -.03165596724],
         [-.008268276230, -.2411113977], [-.1908822209, -.1972177476]],
        [[.2918075621, .08668532968], [.1252691895, -.1041809767],
         [-.03712599352, -.2869983017], [-.1882805377, -.4462233782]],
    ]
    field = SampledField(values, (0., 0., 0.), .4, {"threshold": .45})
    old_vertices, old_faces, _, _ = marching_cubes(
        values, level=0., spacing=(.4,) * 3, allow_degenerate=False)
    old_mesh = trimesh.Trimesh(old_vertices, old_faces, process=False)
    if old_mesh.volume < 0:
        old_mesh.invert()
    assert mesh_integrity(old_mesh)["nonmanifold_edges"] == 1

    result = extract_lattice_mesh(field)
    attempts = result.metadata["numerical_attempts"]
    assert attempts[0]["status"] == "FAIL"
    assert attempts[0]["integrity"]["checks"]["manifold_edges"]
    assert attempts[0]["integrity"]["checks"]["manifold_vertex_links"]
    assert attempts[0]["degenerate_triangle_count"] > 0
    assert attempts[-1]["status"] == "PASS"
    assert result.metadata["allow_degenerate"] is True
    assert result.metadata["components_removed"] == 0
    assert result.metadata["mesh_repair_applied"] is False
    assert result.metadata["integrity"]["connected_components"] == 1
    assert result.metadata["effective_gyroid_threshold"] < .45
    assert result.metadata["field"]["threshold"] == .45
    exported = export_lattice(result, tmp_path / "collision.stl")
    assert mesh_integrity(trimesh.load_mesh(exported, process=True))["status"] == "PASS"
