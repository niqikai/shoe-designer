"""Analytic geometry checks for bounded adaptive CAD certification logic."""
import math

import numpy as np
import pytest

from shoe_poc.lattice.domain import DomainSamplingError, plan_grid
from shoe_poc.lattice.refinement import complete_certified_grid


def sphere_case(max_queries=20000):
    bounds = np.array([[-6., -6., -6.], [6., 6., 6.]])
    plan = plan_grid(bounds, .4)
    field = np.full(plan.shape, .4, np.float32)
    policy = {'clearance_mm': .5, 'coverage_tolerance_mm': .1,
              'toe_start_fraction': .5, 'max_queries': max_queries}

    def query(point):
        sphere_distance = abs(np.linalg.norm(point)-5.)
        distance = min(sphere_distance, *(point-bounds[0]), *(bounds[1]-point))
        return distance, lambda: np.linalg.norm(point) < 5.

    return bounds, plan, field, policy, query


def test_completes_all_deep_eligible_nodes_without_relaxing_clearance():
    bounds, plan, field, policy, query = sphere_case()
    stats = complete_certified_grid(plan, field, bounds_mm=bounds, policy=policy, query=query)
    xyz = np.stack(np.meshgrid(*plan.axes, indexing='ij'), axis=-1)
    depth = 5.-np.linalg.norm(xyz, axis=-1)
    q = .5+math.sqrt(3)*.4+8e-6
    required = (xyz[..., 0] >= 0) & (depth > q+.1)
    assert required.any()
    assert np.all(field[required] < 0)
    assert np.all(depth[field < 0] >= q-1e-6)
    assert stats['status'] == 'COMPLETE'
    assert stats['query_count'] < 20000
    assert stats['distance_pruned_block_count'] > 0


def test_preserves_seed_and_refines_again_even_with_covered_center():
    bounds, plan, field, policy, query = sphere_case()
    index = tuple(int(np.argmin(abs(axis))) for axis in plan.axes)
    field[index] = -.01
    before = field.copy()
    stats = complete_certified_grid(plan, field, bounds_mm=bounds, policy=policy, query=query)
    assert np.all(field <= before)
    assert stats['added_negative_node_count'] > 100
    assert field[index] <= -.01


def test_query_budget_is_failure_with_diagnostics():
    bounds, plan, field, policy, query = sphere_case(max_queries=1)
    with pytest.raises(DomainSamplingError, match='budget exhausted') as caught:
        complete_certified_grid(plan, field, bounds_mm=bounds, policy=policy, query=query)
    assert caught.value.metadata['query_count'] == 1
    assert caught.value.metadata['status'] == 'INCOMPLETE_QUERY_BUDGET'
    assert caught.value.metadata['query_budget_reached'] is True


def test_refinement_cannot_be_attached_to_a_planar_domain():
    from shoe_poc.schemas.spec import LatticeDomainParameters
    with pytest.raises(ValueError, match='curved_lower_foot'):
        LatticeDomainParameters.model_validate({'mode': 'planar', 'refinement': {}})


def test_native_refinement_checkpoint_resumes_only_bound_seed_and_still_completes(tmp_path):
    import json
    from build123d import Box, Pos
    from shoe_poc.lattice.domain import SoleDomain
    from shoe_poc.lattice.envelope import SoleEnvelope
    from shoe_poc.lattice.refinement import refine_sole_domain
    from shoe_poc.schemas.spec import DomainRefinementParameters
    bounds = np.array([[-4., -4., -4.], [4., 4., 4.]])
    plan = plan_grid(bounds, .8)
    metadata = {'computation_bounds_mm': bounds.tolist(), 'sole_z_bounds_mm': [-4., 3.5],
                'clearance_mm': .1, 'runtime_seconds': 0.}
    roof = SoleEnvelope({'version': 'lower-foot-roof-polyline-v1',
                        'upper_points_xz_mm': [[-4., 3.5], [4., 3.5]], 'lower_datum_z_mm': -4.})
    def seed():
        return SoleDomain(plan, None, np.full(plan.shape, .8, np.float32), metadata.copy())
    shoe, cavity = Box(8, 8, 8), Pos(30, 0, 0)*Box(2, 2, 2)
    journal = tmp_path/'queries.json'
    settings = DomainRefinementParameters(toe_start_fraction=0., max_queries=1)
    with pytest.raises(DomainSamplingError, match='budget exhausted'):
        refine_sole_domain(seed(), shoe, cavity, settings=settings, envelope=roof,
                           checkpoint_path=journal, source_identity={'test': 'box'})
    record = json.loads(journal.read_text())
    assert record['role'] == 'INCOMPLETE_REFINEMENT_QUERY_SEED'
    assert record['coverage_status'] == 'NOT_ACCEPTED' and len(record['records']) == 1
    settings = settings.model_copy(update={'max_queries': 2000})
    result = refine_sole_domain(seed(), shoe, cavity, settings=settings, envelope=roof,
                               checkpoint_path=journal, source_identity={'test': 'box'})
    report = result.metadata['refinement']
    assert report['status'] == 'COMPLETE' and report['checkpoint_resumed']
    assert report['analytic_pruned_block_count'] > 0
    xyz = np.stack(np.meshgrid(*plan.axes, indexing='ij'), axis=-1)
    depth = np.minimum(np.min(4.-abs(xyz), axis=-1), 3.5-xyz[..., 2])
    eligible = depth > .1+np.sqrt(3)*.8+8e-6+.1
    assert eligible.any() and np.all(result.field_mm[eligible] < 0)
    assert np.all(depth[result.field_mm < 0] >= .1+np.sqrt(3)*.8-1e-6)
    fresh = refine_sole_domain(seed(), shoe, cavity, settings=settings, envelope=roof,
                               source_identity={'test': 'box'})
    replayed = refine_sole_domain(seed(), shoe, cavity, settings=settings, envelope=roof,
                                  checkpoint_path=journal, source_identity={'test': 'box'})
    assert np.array_equal(result.field_mm, fresh.field_mm)
    assert np.array_equal(result.field_mm, replayed.field_mm)
    assert replayed.metadata['refinement']['native_distance_queries'] == 0
    with pytest.raises(DomainSamplingError, match='binding'):
        refine_sole_domain(seed(), shoe, cavity, settings=settings, envelope=roof,
                           checkpoint_path=journal, source_identity={'test': 'changed'})
    changed = json.loads(journal.read_text())
    next(iter(changed['records'].values()))['distance_mm'] += 1
    journal.write_text(json.dumps(changed))
    with pytest.raises(DomainSamplingError, match='integrity'):
        refine_sole_domain(seed(), shoe, cavity, settings=settings, envelope=roof,
                           checkpoint_path=journal, source_identity={'test': 'box'})
