"""Small synthetic checks for the M3 native geometry and assembly rules."""
import numpy as np
import pytest
import trimesh

from shoe_poc.assembly.core import assemble_core_shell
from shoe_poc.assembly.geometry import (cancel_opposite_duplicate_faces, exact_index_triangles,
                                        validate_native_mesh)


def _joined(*meshes):
    """Concatenate closed shells while preserving disjoint vertex identities."""
    vertices = []
    faces = []
    offset = 0
    for mesh in meshes:
        vertices.append(np.asarray(mesh.vertices))
        faces.append(np.asarray(mesh.faces) + offset)
        offset += len(mesh.vertices)
    return trimesh.Trimesh(vertices=np.vstack(vertices), faces=np.vstack(faces), process=False)


def _box(extents, center=(0, 0, 0), *, inward=False):
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(center)
    if inward:
        mesh.invert()
    return mesh


def test_point_touching_closed_shells_fail_vertex_link_topology():
    first = _box((2, 2, 2))
    second = _box((2, 2, 2), center=(2, 2, 2))
    triangles = np.concatenate((first.triangles, second.triangles))
    mesh = exact_index_triangles(triangles)

    report = validate_native_mesh(mesh)

    assert report['checks']['manifold_edges']
    assert report['checks']['cyclic_vertex_links'] is False
    assert report['singular_vertex_count'] == 1
    assert report['status'] == 'FAIL'


def test_duplicate_face_is_rejected_without_removing_triangles():
    box = _box((2, 2, 2))
    mesh = trimesh.Trimesh(vertices=box.vertices,
                           faces=np.vstack((box.faces, box.faces[0])), process=False)

    report = validate_native_mesh(mesh)

    assert report['triangles'] == len(box.faces) + 1
    assert report['checks']['no_duplicate_faces'] is False
    assert report['repair_applied'] is False
    assert report['status'] == 'FAIL'


def test_exact_opposite_boundary_faces_cancel_without_moving_vertices():
    box = _box((2, 2, 2))
    triangles = np.concatenate((box.triangles, box.triangles[0, [0, 2, 1]][None, ...]))
    mesh = exact_index_triangles(triangles)

    cleaned, details = cancel_opposite_duplicate_faces(mesh)

    assert details['opposite_duplicate_pairs_cancelled'] == 1
    assert details['triangles_removed'] == 2
    assert len(cleaned.faces) == len(box.faces) - 1
    assert set(map(tuple, cleaned.vertices)) == set(map(tuple, box.vertices))
    assert cleaned.metadata['opposite_duplicate_face_cancellation']['coordinates_changed'] is False


def test_reversed_outer_shell_fails_positive_material_orientation():
    reversed_box = _box((2, 2, 2), inward=True)

    report = validate_native_mesh(reversed_box)

    assert report['checks']['consistent_directed_edges']
    assert report['checks']['valid_shell_nesting_and_orientation'] is False
    assert report['physical_body_count'] == 0
    assert report['status'] == 'FAIL'


def test_incomplete_self_intersection_coverage_keeps_topology_available_but_partial():
    report = validate_native_mesh(_box((2, 2, 2)), self_intersection_pair_budget=0)

    assert report['self_intersections']['status'] == 'NOT_VERIFIED'
    assert report['checks']['no_detected_self_intersections'] is None
    assert report['topology_status'] == 'PASS'
    assert report['status'] == 'PARTIAL'


def test_nested_void_shell_has_alternating_orientation_and_one_body():
    mesh = _joined(_box((10, 10, 10)), _box((4, 4, 4), inward=True))

    report = validate_native_mesh(mesh)

    assert report['status'] == 'PASS'
    assert report['physical_body_count'] == 1
    assert [row['role'] for row in report['shells']] == ['material_boundary', 'void_boundary']
    assert [row['nesting_depth'] for row in report['shells']] == [0, 1]
    assert report['volume_mm3'] == pytest.approx(1000 - 64)


def test_positive_island_below_total_volume_floor_is_still_retained():
    small = _box((.001, .001, .001), center=(5, 5, 5))
    report = validate_native_mesh(small, require_single_body=False)
    assert 0 < report['volume_mm3'] < 1e-7
    assert report['physical_body_count'] == 1
    assert report['checks']['valid_shell_nesting_and_orientation'] is True


def test_island_inside_void_is_a_second_physical_body():
    mesh = _joined(_box((10, 10, 10)), _box((6, 6, 6), inward=True), _box((2, 2, 2)))

    report = validate_native_mesh(mesh, require_single_body=False)

    assert report['status'] == 'PASS'
    assert report['physical_body_count'] == 2
    assert [row['nesting_depth'] for row in report['shells']] == [0, 1, 2]
    assert [row['role'] for row in report['shells']] == [
        'material_boundary', 'void_boundary', 'material_boundary']
    strict_report = validate_native_mesh(mesh, require_single_body=True)
    assert strict_report['checks']['physical_body_count'] is False
    assert strict_report['status'] == 'FAIL'


def test_core_shell_volumes_follow_intersection_subtraction_and_exclude_shoe_cavity():
    # B is the 10 mm cube, C the 6 mm core-region cube, and L crosses C's
    # +X boundary. The upper/wearer cavity outside C must not enter V.
    base = _box((10, 10, 10))
    core = _box((6, 6, 6))
    lattice = _box((2, 2, 2), center=(2.6, 0, 0))

    result = assemble_core_shell(base, lattice, core, regional_x_bins={'all': (-5, 5)})

    assert result.report['status'] == 'PASS'
    assert result.report['volumes']['core_inside_base_volume_mm3'] == pytest.approx(216)
    assert result.report['volumes']['remaining_void_volume_mm3'] == pytest.approx(216 - 1.4 * 2 * 2)
    assert result.report['volumes']['remaining_void_volume_mm3'] < 1000 - result.report['volumes']['assembled_volume_mm3']
    assert result.remaining_void.volume() == pytest.approx(216 - 1.4 * 2 * 2)
