"""Adaptive completion of a certified CAD domain, without relaxing clearance.

A grid node is required when it is inside allowed material and its exact
boundary distance exceeds clearance + full cell diagonal + numerical reserve
+ coverage tolerance. Blocks are skipped only after a Lipschitz upper-bound
proof, an outside-material proof, or because all their nodes are certified.
The tolerance describes omitted boundary depth, not CAD distance error.
"""
from __future__ import annotations

import copy
import logging
import math
import time
import hashlib
import json
from pathlib import Path

import numpy as np

from .domain import CAD_TOLERANCE_MM, DomainSamplingError, NativeDomainProbe, SoleDomain

REFINEMENT_VERSION = 'adaptive-certified-toe-grid-v1'


def complete_certified_grid(plan, field, *, bounds_mm, policy, query, block_upper_bound=None):
    """Complete a corridor using ``query(point) -> (unsigned distance, inside)``.

    The callback's distance must be the exact minimum distance to ALL allowed
    domain boundaries, including computation bounds and the envelope. Its
    inside result may be lazy: a callable is only evaluated if classification
    is needed. This core is separately testable with analytic solids.
    """
    started = time.perf_counter()
    spacing = plan.spacing_mm
    bounds = np.asarray(bounds_mm, float)
    diagonal = math.sqrt(3)*spacing
    q = float(policy['clearance_mm'])+diagonal+8*CAD_TOLERANCE_MM
    tolerance = float(policy['coverage_tolerance_mm'])
    threshold = q+tolerance
    start_x = bounds[0, 0]+float(policy['toe_start_fraction'])*np.ptp(bounds[:, 0])
    axes = plan.axes
    lows = np.array([np.searchsorted(axis, bounds[0, i], side='right') for i, axis in enumerate(axes)])
    highs = np.array([np.searchsorted(axis, bounds[1, i], side='left')-1 for i, axis in enumerate(axes)])
    lows[0] = max(lows[0], int(np.searchsorted(axes[0], start_x)))
    stats = {'version': REFINEMENT_VERSION, 'policy': {k: v for k, v in policy.items() if k != 'clearance_mm'},
             'status': 'RUNNING', 'corridor_start_x_mm': float(start_x),
             'required_node_boundary_distance_mm': threshold,
             'clearance_and_numerical_reserve_mm': q,
             'coverage_tolerance_mm': tolerance, 'query_count': 0,
             'classified_query_count': 0, 'block_count': 0,
             'covered_block_count': 0, 'distance_pruned_block_count': 0,
             'outside_block_count': 0, 'leaf_count': 0,
             'analytic_pruned_block_count': 0,
             'seed_negative_node_count': int(np.count_nonzero(field < 0)),
             'query_budget_reached': False,
             'completeness_scope': 'Every allowed grid node in the corridor whose exact minimum domain-boundary distance exceeds clearance + full cell diagonal + 8 CAD tolerances + coverage tolerance is certified negative. This is a grid completeness claim, not continuous geometry or gyroid connectivity.'}
    if np.any(highs < lows):
        raise DomainSamplingError('Refinement corridor contains no interior grid nodes', stats)
    cache = {}

    def add_ball(index, radius):
        # Include the positive ring so caps interpolate the same signed sphere.
        cells = int(math.ceil((radius+spacing)/spacing))
        lo = np.maximum(0, index-cells)
        hi = np.minimum(plan.shape, index+cells+1)
        sl = tuple(slice(int(a), int(b)) for a, b in zip(lo, hi))
        ds = [(np.arange(lo[i], hi[i])-index[i])*spacing for i in range(3)]
        signed = np.sqrt(ds[0][:, None, None]**2+ds[1][None, :, None]**2+ds[2][None, None, :]**2)-radius
        np.minimum(field[sl], signed, out=field[sl])

    # A parent sample supplies reusable upper bounds and outside proofs before
    # another native distance query. The stack stores only O(tree depth) blocks.
    stack = [(lows, highs, None)]
    while stack:
        lo, hi, parent = stack.pop()
        stats['block_count'] += 1
        sl = tuple(slice(int(a), int(b)+1) for a, b in zip(lo, hi))
        if np.all(field[sl] < 0):
            stats['covered_block_count'] += 1
            continue
        if block_upper_bound is not None:
            corner_lo = np.array([axes[i][lo[i]] for i in range(3)])
            corner_hi = np.array([axes[i][hi[i]] for i in range(3)])
            if block_upper_bound(corner_lo, corner_hi) <= threshold:
                stats['analytic_pruned_block_count'] += 1
                continue
        center = (lo+hi)//2
        point = np.array([axes[i][center[i]] for i in range(3)])
        radius = float(np.linalg.norm(np.maximum(center-lo, hi-center)*spacing))
        if parent is not None:
            parent_point, parent_distance, parent_inside = parent
            reach = float(np.linalg.norm(point-parent_point))+radius
            if parent_distance+reach <= threshold:
                stats['distance_pruned_block_count'] += 1
                continue
            if parent_inside is False and parent_distance > reach+8*CAD_TOLERANCE_MM:
                stats['outside_block_count'] += 1
                continue
        key = tuple(map(int, center))
        if key not in cache:
            if stats['query_count'] >= int(policy['max_queries']):
                stats.update(status='INCOMPLETE_QUERY_BUDGET', query_budget_reached=True,
                             pending_block_count=len(stack)+1,
                             runtime_seconds=time.perf_counter()-started)
                raise DomainSamplingError('Adaptive toe certification query budget exhausted; coverage is incomplete', stats)
            distance, inside = query(point)
            if not math.isfinite(distance) or distance < 0:
                raise DomainSamplingError('Refinement received invalid boundary distance', stats)
            cache[key] = [float(distance), inside]
            stats['query_count'] += 1
            if stats['query_count'] % 500 == 0:
                logging.getLogger('shoe_poc').info('Adaptive toe certificates: %d queries, %d pending blocks', stats['query_count'], len(stack))
        record = cache[key]
        distance = record[0]
        if distance+radius <= threshold:
            stats['distance_pruned_block_count'] += 1
            continue
        if callable(record[1]):
            record[1] = bool(record[1]())
            stats['classified_query_count'] += 1
        inside = bool(record[1])
        if not inside and distance > radius+8*CAD_TOLERANCE_MM:
            stats['outside_block_count'] += 1
            continue
        if inside and distance > q+CAD_TOLERANCE_MM:
            add_ball(center, distance-q)
        if np.all(lo == hi):
            stats['leaf_count'] += 1
            if inside and distance > threshold and not field[key] < 0:
                raise DomainSamplingError('Required eligible refinement leaf was not certified', stats)
            continue
        if np.all(field[sl] < 0):
            stats['covered_block_count'] += 1
            continue
        # Split the longest dimension, visiting the half nearest the interior
        # first. Binary blocks avoid eight tiny child queries at thin surfaces.
        dimension = int(np.argmax(hi-lo))
        midpoint = int(center[dimension])
        first_hi, second_lo = hi.copy(), lo.copy()
        first_hi[dimension], second_lo[dimension] = midpoint, midpoint+1
        evidence = (point, distance, inside)
        stack.append((second_lo, hi, evidence))
        stack.append((lo, first_hi, evidence))
    stats.update(status='COMPLETE', runtime_seconds=time.perf_counter()-started,
                 certified_negative_node_count=int(np.count_nonzero(field < 0)))
    stats['added_negative_node_count'] = stats['certified_negative_node_count']-stats['seed_negative_node_count']
    return stats


def refine_sole_domain(domain, shape, cavity, *, settings, envelope=None,
                       checkpoint_path=None, source_identity=None):
    """Add exact CAD certificates to a verified seed domain; never remove nodes."""
    from build123d import Compound, Vertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from scipy.spatial import cKDTree
    metadata = copy.deepcopy(domain.metadata)
    bounds = np.asarray(metadata['computation_bounds_mm'], float)
    z_min, z_max = metadata['sole_z_bounds_mm']
    probe = NativeDomainProbe(shape, cavity, z_min, z_max, envelope=envelope)
    boundary = Compound(children=[*shape.faces(), *cavity.faces()])

    # A journal is only an exact-query seed for another mandatory traversal.
    # It cannot be loaded through load_certified_domain as completed coverage.
    seed_digest = hashlib.sha256(memoryview(np.ascontiguousarray(domain.field_mm))).hexdigest()
    binding = {'version': REFINEMENT_VERSION, 'source': source_identity,
               'seed_field_sha256': seed_digest, 'seed_metadata': metadata,
               'grid': {'origin': list(domain.plan.origin_mm), 'shape': list(domain.plan.shape),
                        'spacing': domain.plan.spacing_mm},
               'envelope': None if envelope is None else envelope.metadata,
               'policy': {k: v for k, v in settings.model_dump(mode='json').items() if k != 'max_queries'}}
    identity = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    records, boundary_points = {}, []
    checkpoint_path = None if checkpoint_path is None else Path(checkpoint_path)
    resumed = False
    if checkpoint_path is not None and checkpoint_path.exists():
        saved = json.loads(checkpoint_path.read_text())
        rows = saved.get('records', {})
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        if (saved.get('role') != 'INCOMPLETE_REFINEMENT_QUERY_SEED'
                or saved.get('binding_sha256') != identity
                or saved.get('records_sha256') != digest):
            raise DomainSamplingError('Refinement checkpoint binding or integrity mismatch')
        for key, row in rows.items():
            p = np.asarray(row['point_mm'], float)
            b = np.asarray(row['boundary_point_mm'], float)
            if (p.shape != (3,) or b.shape != (3,) or not np.isfinite(p).all()
                    or not np.isfinite(b).all() or not math.isfinite(row['distance_mm'])
                    or row['distance_mm'] < 0 or type(row['inside']) is not bool
                    or key != ','.join(map(str, np.rint((p-np.asarray(domain.plan.origin_mm))/domain.plan.spacing_mm).astype(int)))):
                raise DomainSamplingError('Refinement checkpoint contains invalid query records')
            records[key] = row
        resumed = True
    tree, tree_size = None, 0
    counters = {'native_distance_queries': 0, 'reused_distance_queries': 0}

    def save_checkpoint():
        if checkpoint_path is None:
            return
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'role': 'INCOMPLETE_REFINEMENT_QUERY_SEED',
            'coverage_status': 'NOT_ACCEPTED', 'binding_sha256': identity,
            'records_sha256': hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
            'records': records}))
        temporary.replace(checkpoint_path)

    def block_bound(lo, hi):
        nonlocal tree, tree_size
        # Nearest actual box face, datum and vertical reach to the real roof
        # give upper bounds for any allowed point's boundary distance.
        limit = float(min(np.min(hi-bounds[0]), np.min(bounds[1]-lo)))
        if envelope is not None:
            roof = envelope.points
            vertices = roof[(roof[:, 0] >= lo[0]) & (roof[:, 0] <= hi[0]), 1]
            roof_max = max(float(envelope.upper_z(lo[0])), float(envelope.upper_z(hi[0])),
                           float(vertices.max()) if len(vertices) else -math.inf)
            limit = min(limit, roof_max-lo[2], hi[2]-envelope.z_bounds[0])
        if boundary_points:
            if tree is None or len(boundary_points)-tree_size >= 128:
                tree = cKDTree(np.asarray(boundary_points)); tree_size = len(boundary_points)
            center, half = (lo+hi)/2, (hi-lo)/2
            indices = np.atleast_1d(tree.query(center, k=min(8, tree_size))[1])
            candidates = [boundary_points[int(i)] for i in indices]
            candidates.extend(boundary_points[tree_size:])
            # Distance to one point on an actual trimmed CAD face is an upper
            # bound to nearest boundary distance; its maximum is at a corner.
            distances = np.linalg.norm(abs(center-np.asarray(candidates))+half, axis=1)
            limit = min(limit, float(distances.min()))
        return limit

    def query(point):
        key = ','.join(map(str, np.rint((point-np.asarray(domain.plan.origin_mm))/domain.plan.spacing_mm).astype(int)))
        if key in records:
            counters['reused_distance_queries'] += 1
            row = records[key]
            # Introduce supporting points in the original traversal order.
            # Preloading future points could prune earlier blocks differently
            # and change the certificate field on an otherwise identical run.
            boundary_points.append(np.asarray(row['boundary_point_mm']))
            return row['distance_mm'], row['inside']
        measure = BRepExtrema_DistShapeShape(Vertex(*point).wrapped, boundary.wrapped)
        if not measure.IsDone() or not math.isfinite(measure.Value()):
            raise DomainSamplingError('Exact CAD distance failed during refinement')
        distance = min(float(measure.Value()), float(np.min(point-bounds[0])),
                       float(np.min(bounds[1]-point)))
        if envelope is not None:
            distance = min(distance, envelope.boundary_distance(point))
        nearest = measure.PointOnShape2(1)
        boundary_point = [nearest.X(), nearest.Y(), nearest.Z()]
        inside = bool(probe.classify_points(point[None, :])['allowed'][0])
        records[key] = {'point_mm': point.tolist(), 'distance_mm': distance,
                        'inside': inside, 'boundary_point_mm': boundary_point}
        boundary_points.append(np.asarray(boundary_point))
        counters['native_distance_queries'] += 1
        if counters['native_distance_queries'] % 250 == 0:
            save_checkpoint()
        return distance, inside

    policy = settings.model_dump(mode='json')
    policy['clearance_mm'] = metadata['clearance_mm']
    try:
        stats = complete_certified_grid(domain.plan, domain.field_mm, bounds_mm=bounds,
                                        policy=policy, query=query, block_upper_bound=block_bound)
    finally:
        save_checkpoint()
    stats.update(counters)
    stats['checkpoint_resumed'] = resumed
    stats['checkpoint_scope'] = 'Exact queries only; mandatory complete traversal. Not a completed-domain cache.'
    stats['pruning_version'] = 'roof-box-and-actual-boundary-points-v1'
    stats['native_probe_diagnostics'] = probe.diagnostics
    metadata['refinement'] = stats
    metadata['certified_negative_node_count'] = stats['certified_negative_node_count']
    metadata['seed_candidate_statistics_scope'] = 'candidate_count, candidate_coverage_fraction and conservatively_omitted_candidate_count describe the original coarse seed only; adaptive completion has its own exact node contract.'
    metadata['runtime_seconds'] += stats['runtime_seconds']
    return SoleDomain(domain.plan, domain.occupancy, domain.field_mm, metadata)
