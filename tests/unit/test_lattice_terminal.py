"""Detached tip fragments must not qualify as connected toe coverage."""
import numpy as np
import pytest
import trimesh

from shoe_poc.validation.lattice_terminal import principal_mesh_component, validate_terminal_coverage


def _box(low, high):
    low, high = np.asarray(low), np.asarray(high)
    mesh = trimesh.creation.box(extents=high-low)
    mesh.apply_translation((low+high)/2)
    return mesh


def _expected(x, distance=3.):
    return {'grid_index': [x, 4, 4], 'point_mm': [float(x), 4., 4.],
            'inside_intended_material': True, 'minimum_boundary_distance_mm': distance}


def _check(mesh, field, expected):
    return validate_terminal_coverage(mesh, field_mm=field, gyroid_values=field,
        origin_mm=[0., 0., 0.], spacing_mm=1., expected_nodes=expected,
        clearance_mm=.5, approximation_tolerance_mm=.1)


def _field():
    field = np.ones((14, 10, 10), np.float32)
    field[1:13, 1:8, 1:8] = -1
    return field


def test_complete_connected_material_passes_and_true_reserved_tip_is_excluded():
    report = _check(_box([1, 1, 1], [12, 7, 7]), _field(),
                    [_expected(3), _expected(9), _expected(11, distance=1.)])
    assert report['geometry_status'] == 'PASS'
    assert report['eligible_sample_count'] == 2
    assert report['mesh']['components_removed'] == 0
    assert report['global_coverage_status'] == 'NOT_VERIFIED'


def test_far_tip_fragment_cannot_rescue_short_principal_mesh():
    mesh = trimesh.util.concatenate([_box([1, 1, 1], [7, 7, 7]), _box([10, 3, 3], [12, 5, 5])])
    report = _check(mesh, _field(), [_expected(3), _expected(9)])
    assert report['geometry_status'] == 'FAIL'
    assert report['mesh']['all_component_bounds_mm'][1][0] == 12
    assert report['mesh']['principal_bounds_mm'][1][0] == 7
    assert report['failed_terminal_planes']
    assert report['mesh']['components_removed'] == 0


def test_empty_domain_plane_and_detached_eligible_domain_cannot_pass():
    field = _field()
    field[8] = 1
    report = _check(_box([1, 1, 1], [12, 7, 7]), field, [_expected(3), _expected(9)])
    assert report['geometry_status'] == 'FAIL'
    assert report['domain_component_count_6_neighbor'] == 2
    assert report['detached_domain_samples']
    assert any(row['x_mm'] == 8 for row in report['failed_terminal_planes'])


def test_missing_deep_eligible_node_is_failure_even_when_the_mesh_reaches_toe():
    field = _field()
    field[9, 4, 4] = 1
    report = _check(_box([1, 1, 1], [12, 7, 7]), field, [_expected(3), _expected(9)])
    assert report['geometry_status'] == 'FAIL'
    assert len(report['missing_domain_samples']) == 1


def test_cap_merely_touching_terminal_plane_cannot_establish_material_coverage():
    report = _check(_box([1, 1, 1], [9, 7, 7]), _field(), [_expected(3), _expected(9)])
    assert report['geometry_status'] == 'FAIL'
    assert report['failed_terminal_planes'][-1]['x_mm'] == 9
    assert report['failed_terminal_planes'][-1]['principal_stl_crossing_triangle_count'] == 0


@pytest.mark.parametrize('x_origin', [0., 259.1459322558137])
def test_real_marching_cubes_faces_meet_at_grid_planes_without_false_gaps(x_origin):
    from skimage.measure import marching_cubes
    field = _field()
    vertices, faces, _, _ = marching_cubes(field, level=0, spacing=(1., 1., 1.))
    vertices[:, 0] += x_origin
    # Reproduce the actual float32 serialization coordinates, including
    # world-space rounding at the toe, rather than testing an ideal box only.
    mesh = trimesh.Trimesh(vertices=vertices.astype(np.float32), faces=faces, process=False)
    if mesh.volume < 0:
        mesh.invert()
    expected = [_expected(3), _expected(9)]
    for row in expected:
        row['point_mm'][0] += x_origin
    report = validate_terminal_coverage(mesh, field_mm=field, gyroid_values=field,
        origin_mm=[x_origin, 0., 0.], spacing_mm=1., expected_nodes=expected,
        clearance_mm=.5, approximation_tolerance_mm=.1)
    assert report['geometry_status'] == 'PASS'
    assert all(row['principal_stl_left_crossing_triangle_count'] > 0
               and row['principal_stl_right_crossing_triangle_count'] > 0
               for row in report['terminal_planes'])


def test_missing_saved_lattice_plane_cannot_change_analytic_expectation_to_void():
    from shoe_poc.lattice.gyroid import GyroidParameters, SamplingGrid, sample_gyroid
    field = _field()
    sampled = sample_gyroid(GyroidParameters(12., .6, 1.), SamplingGrid((0., 0., 0.), field.shape, 1.),
                           domain_signed_field=field)
    sampled.values[8] = 1
    report = validate_terminal_coverage(_box([1, 1, 1], [12, 7, 7]), field_mm=field,
        gyroid_values=sampled.values, origin_mm=[0., 0., 0.], spacing_mm=1.,
        expected_nodes=[_expected(3), _expected(9)], clearance_mm=.5, approximation_tolerance_mm=.1,
        analytic_parameters={'period_mm': 12., 'threshold': .6, 'phase_origin_mm': [0., 0., 0.],
                             'cap_inset_mm': sampled.metadata['cap_inset_mm']})
    assert report['geometry_status'] == 'FAIL'
    assert any(row['x_mm'] == 8 for row in report['lattice_field_mismatch_planes'])
    plane = next(row for row in report['terminal_planes'] if row['x_mm'] == 8)
    assert plane['expected_lattice_negative_node_count'] > 0


def test_principal_material_is_selected_by_volume_not_triangle_count():
    large = _box([0, 0, 0], [10, 10, 10])
    small = trimesh.creation.icosphere(subdivisions=3, radius=.5)
    small.apply_translation([20., 0., 0.])
    _, _, report = principal_mesh_component(trimesh.util.concatenate([large, small]))
    assert report['principal_volume_mm3'] == pytest.approx(1000.)
    assert report['principal_bounds_mm'][1][0] == 10
    assert report['all_component_bounds_mm'][1][0] > 20


def test_native_terminal_sampler_matches_known_cad_boundary_distances():
    from build123d import Box, Pos
    from shoe_poc.lattice.domain import plan_grid
    from shoe_poc.lattice.envelope import SoleEnvelope
    from shoe_poc.validation.lattice_terminal import sample_native_terminal_nodes
    roof = SoleEnvelope({'version': 'lower-foot-roof-polyline-v1',
        'lower_datum_z_mm': -5., 'upper_points_xz_mm': [[0., 4.], [20., 4.]]})
    bounds = [[0., -5., -5.], [20., 5., 4.]]
    plan = plan_grid(bounds, .5)
    rows = sample_native_terminal_nodes(Pos(10, 0, 0)*Box(20, 10, 10), Pos(50, 0, 0)*Box(1, 1, 1),
        envelope=roof, axes=plan.axes,
        guide=[{'x_mm': x, 'left_y_mm': -4., 'right_y_mm': 4.} for x in (0., 20.)],
        period_mm=12., computation_bounds_mm=bounds)
    assert rows and any(row['inside_intended_material'] for row in rows)
    for row in rows:
        if row['inside_intended_material']:
            x, y, z = row['point_mm']
            exact = min(x, 20-x, y+5, 5-y, z+5, 4-z)
            assert row['minimum_boundary_distance_mm'] == pytest.approx(exact, abs=1e-6)


@pytest.mark.parametrize('distance', [None, float('nan'), -1.])
def test_unresolved_native_measurements_do_not_pass(distance):
    report = _check(_box([1, 1, 1], [12, 7, 7]), _field(), [_expected(3), _expected(9, distance)])
    assert report['geometry_status'] == 'NOT_VERIFIED'
    assert report['measurement_errors']
