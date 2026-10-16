"""Actual CAD sole-domain sampling with conservative cell containment certificates.

The raster is a sampled design subset, never a replacement XY footprint. Exact
CAD boundary distances certify a ball about each chosen interior anchor; the
1-Lipschitz distance bound and one full voxel diagonal reserve ensure every
cell incident to a negative field node is contained in allowed CAD material.
Assertions are relative to OCCT's numerical tolerance, not manufacturing proof.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import logging
import time
from typing import Any

import numpy as np

CAD_TOLERANCE_MM = 1e-6


@dataclass(frozen=True)
class GridPlan:
    axes: tuple[np.ndarray, np.ndarray, np.ndarray]
    spacing_mm: float
    estimated_peak_bytes: int

    @property
    def origin_mm(self):
        return tuple(float(axis[0]) for axis in self.axes)

    @property
    def shape(self):
        return tuple(len(axis) for axis in self.axes)

    @property
    def shape_xyz(self):
        return self.shape

    @property
    def voxel_count(self):
        return math.prod(self.shape)


@dataclass
class SoleDomain:
    plan: GridPlan
    occupancy: np.ndarray | None  # Cached certified fields do not retain raw scanline occupancy.
    field_mm: np.ndarray
    metadata: dict[str, Any]


class DomainSamplingError(ValueError):
    """Failure retaining machine-readable diagnostic evidence."""
    def __init__(self, message, metadata=None):
        super().__init__(message)
        self.metadata = metadata or {}


def plan_grid(bounds_mm, spacing_mm, *, max_voxels=12_000_000,
              max_memory_mb=1024, padding_cells=1):
    """Reject oversized arrays before creating even the coordinate vectors."""
    bounds = np.asarray(bounds_mm, dtype=float)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise ValueError('bounds_mm must contain finite min/max XYZ triples')
    if not math.isfinite(spacing_mm) or spacing_mm <= 0:
        raise ValueError('spacing_mm must be positive and finite')
    if np.any(bounds[1] <= bounds[0]):
        raise ValueError('Every grid maximum must exceed its minimum')
    if not isinstance(padding_cells, int) or padding_cells < 1:
        raise ValueError('At least one padding cell is required')
    if max_voxels <= 0 or max_memory_mb <= 0 or not math.isfinite(max_memory_mb):
        raise ValueError('Grid resource limits must be positive and finite')
    counts = tuple(int(math.ceil(float(v)/spacing_mm))+1+2*padding_cells
                   for v in bounds[1]-bounds[0])
    n = math.prod(counts)
    # EDT (float64 + int32 scratch), boolean masks, float32 output and temporary
    # certificate working arrays. MC has a separate active-cell preflight.
    estimate = n*64 + sum(counts)*8 + 32*1024**2
    if n > max_voxels or estimate > max_memory_mb*1024**2:
        raise DomainSamplingError('Domain grid exceeds configured resource limits', {
            'shape_xyz': counts, 'voxel_count': n,
            'estimated_peak_bytes': estimate, 'max_voxels': max_voxels,
            'max_memory_mb': max_memory_mb})
    axes = tuple(bounds[0, i]-padding_cells*spacing_mm + np.arange(counts[i])*spacing_mm
                 for i in range(3))
    return GridPlan(axes, float(spacing_mm), estimate)


def _validate_solid(shape, name):
    if not shape.is_valid or len(shape.solids()) != 1 or shape.volume <= 0:
        raise DomainSamplingError(f'{name} must be a valid positive single CAD solid')
    from OCP.BRepCheck import BRepCheck_Shell, BRepCheck_Status
    for shell in shape.shells():
        if BRepCheck_Shell(shell.wrapped).Closed() != BRepCheck_Status.BRepCheck_NoError:
            raise DomainSamplingError(f'{name} has an open CAD shell')


class NativeDomainProbe:
    """Reusable exact classifiers/scanlines independent of any lattice raster."""
    def __init__(self, shape, cavity, z_min_mm, z_max_mm):
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
        _validate_solid(shape, 'Shoe')
        _validate_solid(cavity, 'Cavity')
        if not (math.isfinite(z_min_mm) and math.isfinite(z_max_mm) and z_min_mm < z_max_mm):
            raise ValueError('Sole Z bounds must be finite and increasing')
        self.shape, self.cavity = shape, cavity
        self.z_min_mm, self.z_max_mm = float(z_min_mm), float(z_max_mm)
        self.classifiers = [BRepClass3d_SolidClassifier(s.solids()[0].wrapped) for s in (shape, cavity)]
        self.rays = []
        self._line_cache = {}
        self._boolean_cache = {}
        self.brackets = []
        self.diagnostics = {'line_count': 0, 'fallback_line_count': 0,
                            'tangent_hit_count': 0, 'duplicate_hit_count': 0,
                            'measurement_errors': [], 'classification_disagreements': [],
                            'boolean_line_count': 0}
        for solid in (shape, cavity):
            ray = IntCurvesFace_ShapeIntersector()
            ray.Load(solid.wrapped, CAD_TOLERANCE_MM)
            self.rays.append(ray)
            box = solid.bounding_box()
            pad = max(1., float(box.size.X)*.01)
            self.brackets.append((float(box.min.X)-pad, float(box.max.X)+pad))

    def _state(self, index, point):
        from OCP.gp import gp_Pnt
        self.classifiers[index].Perform(gp_Pnt(*map(float, point)), CAD_TOLERANCE_MM)
        return self.classifiers[index].State()

    def _boolean_intervals(self, index, point, axis):
        key = (index, axis, *(round(float(point[i]), 12) for i in range(3) if i != axis))
        if key not in self._boolean_cache:
            self._boolean_cache[key] = self._uncached_boolean_intervals(index, point, axis)
        return self._boolean_cache[key]

    def _uncached_boolean_intervals(self, index, point, axis):
        """Independent full-bbox CAD Edge/solid Common, including multiple spans."""
        from build123d import Edge
        solid = (self.shape, self.cavity)[index]
        bbox = solid.bounding_box()
        low, high = np.asarray(tuple(bbox.min)), np.asarray(tuple(bbox.max))
        pad = max(1., float(high[axis]-low[axis])*.01)
        begin, end = np.array(point, float), np.array(point, float)
        begin[axis], end[axis] = low[axis]-pad, high[axis]+pad
        common = solid.intersect(Edge.make_line(begin.tolist(), end.tolist()))
        self.diagnostics['boolean_line_count'] += 1
        intervals = []
        for edge in common.edges() if common is not None else []:
            limits = sorted(float(tuple(vertex.center())[axis]) for vertex in edge.vertices())
            if len(limits) != 2:
                raise DomainSamplingError('Unexpected Boolean line intersection topology')
            if limits[1]-limits[0] > 4*CAD_TOLERANCE_MM:
                if limits[0] <= begin[axis]+CAD_TOLERANCE_MM or limits[1] >= end[axis]-CAD_TOLERANCE_MM:
                    raise DomainSamplingError('Boolean intersection reaches outside its CAD bbox bracket')
                intervals.append(limits)
        merged = []
        for a, b in sorted(intervals):
            if merged and a <= merged[-1][1]+4*CAD_TOLERANCE_MM:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return merged

    @staticmethod
    def _contains(intervals, coordinate):
        return any(a+4*CAD_TOLERANCE_MM < coordinate < b-4*CAD_TOLERANCE_MM
                   for a, b in intervals)

    def _clean_axis_evidence(self, index, point, axis):
        from OCP.gp import gp_Pnt, gp_Dir, gp_Lin
        from OCP.IntCurveSurface import IntCurveSurface_In, IntCurveSurface_Out
        from OCP.TopAbs import TopAbs_IN
        bbox = (self.shape, self.cavity)[index].bounding_box()
        low, high = tuple(bbox.min)[axis], tuple(bbox.max)[axis]
        pad = max(1., (high-low)*.01)
        begin = np.array(point, float)
        begin[axis] = low-pad
        direction = np.zeros(3)
        direction[axis] = 1.
        ray = self.rays[index]
        ray.Perform(gp_Lin(gp_Pnt(*begin), gp_Dir(*direction)), 0., high-low+2*pad)
        if not ray.IsDone():
            return {'clean': False, 'reason': 'native intersector incomplete'}
        hits = sorted((begin[axis]+float(ray.WParameter(i)), ray.Transition(i), ray.State(i))
                      for i in range(1, ray.NbPnt()+1))
        clean = (len(hits)%2 == 0 and
                 all(h[2] == TopAbs_IN and h[1] == (IntCurveSurface_In if i%2 == 0 else IntCurveSurface_Out)
                     for i, h in enumerate(hits)) and
                 all(b[0]-a[0] > 4*CAD_TOLERANCE_MM for a, b in zip(hits, hits[1:])))
        intervals = [[hits[i][0], hits[i+1][0]] for i in range(0, len(hits)-1, 2)]
        return {'clean': bool(clean), 'intervals_mm': intervals,
                'inside': self._contains(intervals, point[axis]) if clean else None,
                'hit_count': len(hits), 'full_bbox_bracket_mm': [low-pad, high+pad]}

    def _resolve_membership(self, index, point, state, ray_inside):
        from OCP.TopAbs import TopAbs_IN
        classified = state == TopAbs_IN
        if classified == ray_inside:
            return classified
        evidence = [self._clean_axis_evidence(index, point, axis) for axis in range(3)]
        native_agreement = all(item['clean'] for item in evidence) and len({item['inside'] for item in evidence}) == 1
        record = {'solid': 'shoe' if index == 0 else 'cavity', 'point_mm': list(map(float, point)),
                  'original_classifier_state': str(state), 'scanline_inside': bool(ray_inside),
                  'oriented_native_axes': evidence, 'classifier_reliability': 'NOT_VERIFIED'}
        if native_agreement:
            record['resolution'] = 'THREE_AXIS_ORIENTED_RAY_AGREEMENT'
            self.diagnostics['classification_disagreements'].append(record)
            return evidence[0]['inside']
        intervals = [self._boolean_intervals(index, point, axis) for axis in range(3)]
        decisions = [self._contains(intervals[axis], point[axis]) for axis in range(3)]
        record.update({'boolean_intervals_xyz_mm': intervals, 'boolean_inside_xyz': decisions,
                       'resolution': 'THREE_AXIS_BOOLEAN_AGREEMENT' if len(set(decisions)) == 1 else 'UNRESOLVED'})
        self.diagnostics['classification_disagreements'].append(record)
        if len(set(decisions)) != 1:
            raise DomainSamplingError('Independent three-axis Boolean membership is inconsistent', self.diagnostics)
        return decisions[0]

    def classify_points(self, points):
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT
        points = np.asarray(points, dtype=float)
        if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
            raise ValueError('Native probe points must have finite shape (N,3)')
        sole, cavity = [], []
        for point in points:
            states = [self._state(i, point) for i in (0, 1)]
            if any(state not in (TopAbs_IN, TopAbs_ON, TopAbs_OUT) for state in states):
                raise DomainSamplingError('Unknown native CAD point classification')
            memberships = []
            for index, state in enumerate(states):
                # ON remains conservatively outside material / inside cavity.
                if state == TopAbs_ON:
                    memberships.append(index == 1)
                    continue
                intervals = self._intervals(index, point[1], point[2])
                memberships.append(self._resolve_membership(index, point, state,
                                                            self._contains(intervals, point[0])))
            sole.append(memberships[0])
            cavity.append(memberships[1])
        sole, cavity = np.asarray(sole, bool), np.asarray(cavity, bool)
        z_ok = (points[:, 2] > self.z_min_mm) & (points[:, 2] < self.z_max_mm)
        return {'inside_sole': sole, 'inside_cavity': cavity,
                'within_z_bounds': z_ok, 'allowed': sole & ~cavity & z_ok}

    def _intervals(self, index, y, z):
        key = (index, round(float(y), 12), round(float(z), 12))
        if key not in self._line_cache:
            self._line_cache[key] = self._uncached_intervals(index, y, z)
        return self._line_cache[key]

    def _uncached_intervals(self, index, y, z):
        from OCP.gp import gp_Pnt, gp_Dir, gp_Lin
        from OCP.IntCurveSurface import IntCurveSurface_In, IntCurveSurface_Out, IntCurveSurface_Tangent
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT
        ray = self.rays[index]
        x0, x1 = self.brackets[index]
        self.diagnostics['line_count'] += 1
        ray.Perform(gp_Lin(gp_Pnt(x0, float(y), float(z)), gp_Dir(1, 0, 0)), 0., x1-x0)
        if not ray.IsDone():
            raise DomainSamplingError('Native CAD scanline did not complete')
        hits = sorted((x0+float(ray.WParameter(i)), ray.Transition(i), ray.State(i))
                      for i in range(1, ray.NbPnt()+1))
        groups = []
        for hit in hits:
            if groups and abs(hit[0]-groups[-1][0][0]) <= 4*CAD_TOLERANCE_MM:
                groups[-1].append(hit)
                self.diagnostics['duplicate_hit_count'] += 1
            else:
                groups.append([hit])
        self.diagnostics['tangent_hit_count'] += sum(h[1] == IntCurveSurface_Tangent for h in hits)
        clean = all(len(g) == 1 and g[0][2] == TopAbs_IN and
                    g[0][1] in (IntCurveSurface_In, IntCurveSurface_Out) for g in groups)
        transitions = [g[0][1] for g in groups]
        clean = clean and len(groups)%2 == 0 and all(
            t == (IntCurveSurface_In if i%2 == 0 else IntCurveSurface_Out)
            for i, t in enumerate(transitions))
        if clean:
            return [(groups[i][0][0], groups[i+1][0][0]) for i in range(0, len(groups), 2)]
        # OCCT point classifiers can disagree with known true material on
        # trimmed periodic faces. Resolve ambiguous rays with an independent
        # CAD Boolean operation, not a midpoint vote from that classifier.
        self.diagnostics['fallback_line_count'] += 1
        return self._boolean_intervals(index, (0., y, z), 0)

    def scanline(self, y, z):
        try:
            return {'sole_intervals_mm': self._intervals(0, y, z),
                    'cavity_intervals_mm': self._intervals(1, y, z),
                    'within_z_bounds': self.z_min_mm < z < self.z_max_mm}
        except Exception as exc:
            self.diagnostics['measurement_errors'].append({'y_mm': float(y), 'z_mm': float(z), 'error': str(exc)})
            raise DomainSamplingError('Failed actual CAD scanline', self.diagnostics) from exc


def _mask_intervals(axis, intervals):
    result = np.zeros(len(axis), dtype=bool)
    for a, b in intervals:
        result |= (axis > a+CAD_TOLERANCE_MM) & (axis < b-CAD_TOLERANCE_MM)
    return result


def sample_sole_domain(shape, cavity, *, z_min_mm, z_max_mm, spacing_mm=1.,
                       clearance_mm=2., bounds_mm=None, max_voxels=12_000_000,
                       max_memory_mb=1024, max_certificate_queries=4_000):
    """Sample exact CAD occupancy and conservatively certify the retained subset.

    ``bounds_mm`` only reduces the computation window; it cannot enlarge the
    permitted CAD material or sole-only Z slab. ``clearance_mm`` reserves material
    around *all* exact material/cavity boundaries and the selected Z bounds.
    """
    from scipy.ndimage import distance_transform_edt
    from build123d import Compound, Vertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    started = time.perf_counter()
    if not isinstance(max_certificate_queries, int) or max_certificate_queries < 1:
        raise ValueError('max_certificate_queries must be a positive integer')
    if not math.isfinite(clearance_mm) or clearance_mm < 0:
        raise ValueError('clearance_mm must be finite and nonnegative')
    probe = NativeDomainProbe(shape, cavity, z_min_mm, z_max_mm)
    box = shape.bounding_box()
    actual_bounds = np.array([[box.min.X, box.min.Y, z_min_mm],
                              [box.max.X, box.max.Y, z_max_mm]], dtype=float)
    if bounds_mm is not None:
        requested = np.asarray(bounds_mm, dtype=float)
        if requested.shape != (2, 3) or not np.isfinite(requested).all() or np.any(requested[1] <= requested[0]):
            raise ValueError('Invalid computation bounds_mm')
        actual_bounds[0] = np.maximum(actual_bounds[0], requested[0])
        actual_bounds[1] = np.minimum(actual_bounds[1], requested[1])
    plan = plan_grid(actual_bounds, spacing_mm, max_voxels=max_voxels, max_memory_mb=max_memory_mb)
    xs, ys, zs = plan.axes
    occupancy = np.zeros(plan.shape, dtype=bool)
    for j, y in enumerate(ys):
        if j % 50 == 0:
            logging.getLogger('shoe_poc').info('CAD domain scanlines: Y row %d/%d', j, len(ys))
        for k, z in enumerate(zs):
            if not actual_bounds[0, 2] < z < actual_bounds[1, 2]:
                continue
            line = probe.scanline(y, z)
            occupancy[:, j, k] = (_mask_intervals(xs, line['sole_intervals_mm']) &
                                   ~_mask_intervals(xs, line['cavity_intervals_mm']))
    scanline_seconds = time.perf_counter()-started
    # Computation-boundary padding remains outside even if CAD spans beyond it.
    for dimension in range(3):
        select = [slice(None)]*3
        for end in (0, -1):
            select[dimension] = end
            occupancy[tuple(select)] = False
    diagonal = math.sqrt(3)*spacing_mm
    edt = distance_transform_edt(occupancy, sampling=spacing_mm)
    # This is only a candidate selector, never the containment proof. The exact
    # certificate below catches cavities/slivers smaller than a grid interval.
    candidate = edt > clearance_mm+2*diagonal
    remaining = candidate.copy()
    field = np.full(plan.shape, spacing_mm, dtype=np.float32)
    boundary = Compound(children=[*shape.faces(), *cavity.faces()])
    stats = {'query_count': 0, 'anchor_classification_errors': [],
             'minimum_anchor_boundary_distance_mm': None,
             'certificate_rule': 'Exact CAD boundary distance minus anchor-to-node distance minus clearance minus full cell diagonal minus 8 CAD tolerances',
             'cell_diagonal_reserve_mm': diagonal,
             'numeric_reserve_mm': 8*CAD_TOLERANCE_MM}
    # Candidate anchors are selected on a physical grid, prioritizing larger
    # approximate interior distances. Exact spheres cover the WHOLE nearby grid,
    # including across tiles. There is no per-node recursion near the boundary.
    anchor_stride = max(1, int(round(2.0/spacing_mm)))
    coarse = edt[::anchor_stride, ::anchor_stride, ::anchor_stride]
    indices = np.argwhere(candidate[::anchor_stride, ::anchor_stride, ::anchor_stride])
    priorities = coarse[tuple(indices.T)] if len(indices) else np.empty(0)
    indices = indices[np.argsort(-priorities, kind='stable')]*anchor_stride
    del edt, coarse, priorities
    stats['anchor_stride_mm'] = anchor_stride*spacing_mm
    stats['candidate_anchor_count'] = len(indices)
    stats['query_budget_reached'] = False
    for anchor_idx in indices:
        key = tuple(int(v) for v in anchor_idx)
        if not remaining[key]:
            continue
        if stats['query_count'] >= max_certificate_queries:
            stats['query_budget_reached'] = True
            break
        stats['query_count'] += 1
        if stats['query_count'] % 500 == 0:
            logging.getLogger('shoe_poc').info('CAD domain certificates: %d exact distance queries', stats['query_count'])
        anchor = np.array([plan.axes[i][key[i]] for i in range(3)])
        if not probe.classify_points(anchor[None, :])['allowed'][0]:
            stats['anchor_classification_errors'].append(anchor.tolist())
            raise DomainSamplingError('Unresolved scanline/native point disagreement', stats)
        distance = BRepExtrema_DistShapeShape(Vertex(*anchor).wrapped, boundary.wrapped)
        if not distance.IsDone() or not math.isfinite(distance.Value()):
            raise DomainSamplingError('Exact CAD boundary distance failed', stats)
        native_distance = float(distance.Value())
        minimum = stats['minimum_anchor_boundary_distance_mm']
        stats['minimum_anchor_boundary_distance_mm'] = native_distance if minimum is None else min(native_distance, minimum)
        distance_to_bounds = min(*(anchor-actual_bounds[0]), *(actual_bounds[1]-anchor))
        radius = min(native_distance, distance_to_bounds)-clearance_mm-diagonal-8*CAD_TOLERANCE_MM
        if radius <= CAD_TOLERANCE_MM:
            continue
        cells = int(math.ceil(radius/spacing_mm))
        lo, hi = np.maximum(0, anchor_idx-cells), np.minimum(plan.shape, anchor_idx+cells+1)
        sl = tuple(slice(int(a), int(b)) for a, b in zip(lo, hi))
        deltas = [(np.arange(lo[i], hi[i])-anchor_idx[i])*spacing_mm for i in range(3)]
        signed = np.sqrt(deltas[0][:, None, None]**2+deltas[1][None, :, None]**2+deltas[2][None, None, :]**2)-radius
        accepted = (signed < -CAD_TOLERANCE_MM) & occupancy[sl]
        local_field = field[sl]
        local_field[accepted] = np.minimum(local_field[accepted], signed[accepted])
        remaining[sl][accepted] = False
    stats['coverage_policy'] = 'Union of exact interior balls; unproved sampled regions are explicitly omitted, never certified by EDT'
    retained_count = int(np.count_nonzero(field < 0))
    if not retained_count:
        raise DomainSamplingError('No certifiable sole interior remains at this spacing and clearance', {
            'candidate_count': int(candidate.sum()), 'certificate': stats})
    # Independent, finite native checks: spread over deterministic retained-node
    # ranks, rather than reusing scanline classifications or certificate anchors.
    flat = np.flatnonzero(field < 0)
    ranks = np.unique(np.linspace(0, len(flat)-1, min(256, len(flat)), dtype=int))
    idx = np.array(np.unravel_index(flat[ranks], plan.shape)).T
    points = np.column_stack([plan.axes[i][idx[:, i]] for i in range(3)])
    checks = probe.classify_points(points)
    bad = np.flatnonzero(~checks['allowed'])
    if len(bad):
        raise DomainSamplingError('Independent native domain sample failed', {'failed_points_mm': points[bad].tolist()})
    metadata = {
        'method': 'Actual closed CAD material/cavity X scanlines plus exact CAD boundary-distance cell certificates',
        'axis_order': 'XYZ', 'shape_xyz': list(plan.shape), 'origin_mm': list(plan.origin_mm),
        'spacing_mm': spacing_mm, 'voxel_count': plan.voxel_count,
        'estimated_peak_bytes': plan.estimated_peak_bytes,
        'computation_bounds_mm': actual_bounds.tolist(),
        'sole_z_bounds_mm': [z_min_mm, z_max_mm], 'clearance_mm': clearance_mm,
        'occupancy_count': int(occupancy.sum()), 'candidate_count': int(candidate.sum()),
        'certified_negative_node_count': retained_count,
        'conservatively_omitted_candidate_count': int(remaining.sum()),
        'candidate_coverage_fraction': float(1-remaining.sum()/max(1, candidate.sum())),
        'native_scanlines': probe.diagnostics, 'certificate': stats,
        'independent_native_samples': {'count': len(points), 'failed_count': len(bad),
                                       'points_mm': points.tolist(), 'measurement_errors': []},
        'runtime_seconds': time.perf_counter()-started,
        'scanline_runtime_seconds': scanline_seconds,
        'containment_scope': 'Every cell incident to a negative node is within the actual CAD shoe, outside the cavity, and inside the bounded sole window, with reserved clearance, conditional on OCCT distance/classifier tolerance.',
        'limitations': ['This deliberately bounded sole subset excludes upper material and toe/rocker material above the chosen maximum sole Z.',
                        'Sampling and conservative certificates can omit narrow material regions; sampled geometry is not an exact CAD offset.',
                        'The 2 mm default reserve is an experimental shell allowance, not manufacturing qualification.',
                        'CAD containment does not establish printable wall thickness, fusion quality, or comfort.'],
    }
    return SoleDomain(plan, occupancy, field, metadata)
