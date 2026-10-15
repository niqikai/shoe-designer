"""Exact-domain sampling regressions: holes, multiple hits and missed subvoxels."""
import math

import numpy as np
import pytest
from build123d import Align, Box, Location, Pos, Rot

from shoe_poc.lattice.domain import (
    DomainSamplingError, NativeDomainProbe, plan_grid, sample_sole_domain,
)


def box(x, y, z, at=(0, 0, 0)):
    return Box(x, y, z, align=(Align.MIN, Align.MIN, Align.MIN)).moved(Location(at))


def points_for_mask(domain, mask):
    indices = np.array(np.where(mask)).T
    return np.column_stack([domain.plan.axes[i][indices[:, i]] for i in range(3)])


def test_plan_rejects_memory_before_axis_allocation(monkeypatch):
    def allocation(*args, **kwargs):
        raise AssertionError('allocated before preflight')
    monkeypatch.setattr(np, 'arange', allocation)
    with pytest.raises(DomainSamplingError, match='resource limits') as error:
        plan_grid(((0, 0, 0), (300, 110, 20)), .01, max_voxels=10_000_000)
    assert error.value.metadata['voxel_count'] > 10_000_000


@pytest.mark.parametrize('spacing', [.5, 1.])
def test_native_domain_excludes_cavity_and_certifies_voxel_cells(spacing):
    material = box(24, 18, 12)
    cavity = box(4, 4, 4, (10, 7, 4))
    domain = sample_sole_domain(material, cavity, z_min_mm=0, z_max_mm=12,
                                spacing_mm=spacing, clearance_mm=.5)
    native = NativeDomainProbe(material, cavity, 0, 12)
    negative = domain.field_mm < 0
    nodes = points_for_mask(domain, negative)
    assert len(nodes) > 0
    assert native.classify_points(nodes)['allowed'].all()
    # Every neighbor corner of a negative node belongs to some incident MC cell.
    # The certificate accounts for its whole diagonal, not just sample nodes.
    for delta in [(a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)]:
        assert native.classify_points(nodes+spacing*np.asarray(delta))['allowed'].all()
    assert domain.metadata['independent_native_samples']['failed_count'] == 0
    assert domain.metadata['certificate']['query_count'] > 0
    for axis in range(3):
        assert np.all(np.take(domain.field_mm, (0, -1), axis=axis) > 0)


def test_native_scanline_multiple_material_hits_and_cavity():
    # Connected U shape has four X crossings through its arms.
    shape = box(20, 12, 8)-box(8, 10, 10, (6, 3, -1))
    cavity = box(2, 2, 2, (1, 5, 3))
    native = NativeDomainProbe(shape, cavity, 0, 8)
    line = native.scanline(6, 4)
    np.testing.assert_allclose(line['sole_intervals_mm'], [[0, 6], [14, 20]], atol=1e-5)
    np.testing.assert_allclose(line['cavity_intervals_mm'], [[1, 3]], atol=1e-5)
    flags = native.classify_points(np.array([[2, 6, 4], [4, 6, 4], [10, 6, 4], [16, 6, 4]]))
    assert flags['allowed'].tolist() == [False, True, False, True]


def test_edge_scanline_uses_explicit_native_fallback():
    native = NativeDomainProbe(box(10, 10, 10), box(1, 1, 1, (20, 20, 20)), 0, 10)
    line = native.scanline(0, 5)
    assert line['sole_intervals_mm'] == [[0., 10.]]  # Boolean retains the boundary line
    assert native.diagnostics['fallback_line_count'] >= 1
    assert native.diagnostics['measurement_errors'] == []


def test_subvoxel_cavity_is_excluded_by_exact_distance_not_sampled_edt():
    shape = box(20, 20, 14)
    cavity = box(.12, .12, .12, (10.2, 10.2, 7.2))
    domain = sample_sole_domain(shape, cavity, z_min_mm=0, z_max_mm=14,
                                spacing_mm=1, clearance_mm=.25)
    points = points_for_mask(domain, domain.field_mm < 0)
    # No integer Y/Z scanline intersects this tiny cavity. Nevertheless the exact
    # boundary-distance certificates reserve the cavity and a full cell diagonal.
    offsets = np.maximum(np.maximum(np.array([10.2]*2+[7.2])-points,
                                    points-np.array([10.32]*2+[7.32])), 0)
    distance = np.linalg.norm(offsets, axis=1)
    assert np.min(distance) >= .25+math.sqrt(3)-1e-5
    assert domain.metadata['conservatively_omitted_candidate_count'] > 0


def test_sloping_native_material_not_bounding_box():
    shape = Pos(0, 0, 2)*Rot(12, 0, 0)*box(20, 20, 12)
    cavity = box(1, 1, 1, (50, 50, 50))
    domain = sample_sole_domain(shape, cavity, z_min_mm=0, z_max_mm=16,
                                spacing_mm=1, clearance_mm=.5)
    native = NativeDomainProbe(shape, cavity, 0, 16)
    points = points_for_mask(domain, domain.occupancy)
    assert native.classify_points(points)['allowed'].all()
    assert domain.metadata['occupancy_count'] < math.prod(domain.plan.shape)*.8


@pytest.mark.parametrize('spacing', [0, -1, float('nan')])
def test_invalid_spacing(spacing):
    with pytest.raises(ValueError):
        plan_grid(((0, 0, 0), (1, 1, 1)), spacing)


def test_empty_certified_interior_is_explicit_failure():
    with pytest.raises(DomainSamplingError, match='No certifiable sole interior'):
        sample_sole_domain(box(10, 10, 2), box(1, 1, 1, (30, 30, 30)),
                           z_min_mm=0, z_max_mm=2, spacing_mm=1, clearance_mm=2)


def test_computation_bounds_only_reduce_domain():
    domain = sample_sole_domain(box(30, 20, 12), box(1, 1, 1, (40, 40, 40)),
                                z_min_mm=0, z_max_mm=12, spacing_mm=.5,
                                clearance_mm=.5, bounds_mm=((5, 5, -10), (25, 15, 50)))
    assert domain.metadata['computation_bounds_mm'] == [[5, 5, 0], [25, 15, 12]]
    points = points_for_mask(domain, domain.field_mm < 0)
    assert np.all(points[:, 0] > 5) and np.all(points[:, 0] < 25)


def test_classifier_disagreement_requires_three_independent_boolean_axes(monkeypatch):
    from OCP.TopAbs import TopAbs_OUT
    native = NativeDomainProbe(box(10, 10, 10), box(1, 1, 1, (20, 20, 20)), 0, 10)
    original = native._state
    monkeypatch.setattr(native, '_clean_axis_evidence', lambda *args: {'clean': False})
    monkeypatch.setattr(native, '_state', lambda index, point: TopAbs_OUT if index == 0 else original(index, point))
    assert native.classify_points(np.array([[5., 5., 5.]]))['allowed'].tolist() == [True]
    record = native.diagnostics['classification_disagreements'][0]
    assert record['boolean_inside_xyz'] == [True, True, True]
    assert record['resolution'] == 'THREE_AXIS_BOOLEAN_AGREEMENT'
    assert native.diagnostics['boolean_line_count'] == 3


def test_inconsistent_boolean_axes_are_explicit_failure(monkeypatch):
    from OCP.TopAbs import TopAbs_OUT
    native = NativeDomainProbe(box(10, 10, 10), box(1, 1, 1, (20, 20, 20)), 0, 10)
    monkeypatch.setattr(native, '_state', lambda index, point: TopAbs_OUT)
    monkeypatch.setattr(native, '_clean_axis_evidence', lambda *args: {'clean': False})
    monkeypatch.setattr(native, '_boolean_intervals', lambda index, point, axis: [] if axis == 1 else [[0., 10.]])
    with pytest.raises(DomainSamplingError, match='three-axis Boolean'):
        native.classify_points(np.array([[5., 5., 5.]]))
    assert native.diagnostics['classification_disagreements'][0]['resolution'] == 'UNRESOLVED'


def test_certificate_budget_produces_explicit_conservative_subset():
    domain = sample_sole_domain(box(30, 20, 12), box(1, 1, 1, (40, 40, 40)),
                                z_min_mm=0, z_max_mm=12, spacing_mm=.5,
                                clearance_mm=.5, max_certificate_queries=1)
    assert domain.metadata['certificate']['query_budget_reached']
    assert domain.metadata['conservatively_omitted_candidate_count'] > 0
    assert domain.metadata['certificate']['query_count'] == 1
    assert domain.metadata['certified_negative_node_count'] > 0


def test_clean_three_axis_oriented_crossings_resolve_classifier_disagreement(monkeypatch):
    from OCP.TopAbs import TopAbs_OUT
    native = NativeDomainProbe(box(10, 10, 10), box(1, 1, 1, (20, 20, 20)), 0, 10)
    monkeypatch.setattr(native, '_state', lambda index, point: TopAbs_OUT)
    assert native.classify_points(np.array([[5., 5., 5.]]))['allowed'].tolist() == [True]
    record = native.diagnostics['classification_disagreements'][0]
    assert record['resolution'] == 'THREE_AXIS_ORIENTED_RAY_AGREEMENT'
    assert all(item['clean'] and item['inside'] for item in record['oriented_native_axes'])
    assert native.diagnostics['boolean_line_count'] == 0


def test_three_axis_native_disagreement_cannot_vote_past_boolean(monkeypatch):
    from OCP.TopAbs import TopAbs_OUT
    native = NativeDomainProbe(box(10, 10, 10), box(1, 1, 1, (20, 20, 20)), 0, 10)
    monkeypatch.setattr(native, '_state', lambda index, point: TopAbs_OUT)
    monkeypatch.setattr(native, '_clean_axis_evidence', lambda index, point, axis: {'clean': True, 'inside': axis != 2})
    assert native.classify_points(np.array([[5., 5., 5.]]))['allowed'].tolist() == [True]
    assert native.diagnostics['classification_disagreements'][0]['resolution'] == 'THREE_AXIS_BOOLEAN_AGREEMENT'
    assert native.diagnostics['boolean_line_count'] == 3
