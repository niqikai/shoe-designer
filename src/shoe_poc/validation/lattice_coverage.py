"""Finite CAD/STL coverage probes for the rising forefoot and toe.

Containment can pass for a lattice whose toe is absent. These probes start from
accepted CAD, rather than mesh bounds, and aggregate a full gyroid period before
requiring material. Reported line-length sums are sampled support measures, not
volume, complete offsets, manufacturing checks, or a global geometric proof.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

from .lattice import _mesh


def _intervals(value):
    array = np.asarray(value, dtype=float)
    if array.shape == (0,):
        return []
    if array.ndim != 2 or array.shape[1] != 2 or not np.isfinite(array).all():
        raise ValueError('Intervals must be finite N x 2 pairs')
    if np.any(array[:, 1] <= array[:, 0]) or np.any(array[1:, 0] < array[:-1, 1]):
        raise ValueError('Intervals must be increasing, nonempty and disjoint')
    return array.tolist()


def _overlap(first, second):
    return sum(max(0., min(b, d)-max(a, c)) for a, b in first for c, d in second)


def _subtract(first, second):
    result = []
    for a, b in first:
        cursor = a
        for c, d in second:
            if d <= cursor or c >= b:
                continue
            if c > cursor:
                result.append([cursor, min(c, b)])
            cursor = max(cursor, d)
        if cursor < b:
            result.append([cursor, b])
    return result


class NativeVerticalSoleSections:
    """Actual B-Rep Edge/solid Booleans, independent of the generation raster.

    The roof callable expresses the intended design boundary. Shoe and cavity
    material below it are measured from native CAD at each requested site.
    """
    def __init__(self, shoe, cavity, *, upper_z: Callable, z_min_mm: float):
        self.shoe, self.cavity, self.upper_z = shoe, cavity, upper_z
        self.z_min_mm = float(z_min_mm)
        box = shoe.bounding_box()
        self.bounds_mm = np.array([tuple(box.min), tuple(box.max)], dtype=float)

    def _section(self, shape, x, y):
        from build123d import Edge
        box = shape.bounding_box()
        line = Edge.make_line((x, y, float(box.min.Z)-1), (x, y, float(box.max.Z)+1))
        common = shape.intersect(line)
        intervals = []
        for edge in common.edges() if common is not None else []:
            limits = sorted(float(vertex.center().Z) for vertex in edge.vertices())
            if len(limits) != 2:
                raise ValueError('Unexpected native vertical section topology')
            if limits[1]-limits[0] > 1e-6:
                intervals.append(limits)
        return _intervals(sorted(intervals))

    def __call__(self, x, y):
        material = self._section(self.shoe, x, y)
        cavity = self._section(self.cavity, x, y)
        top = float(np.asarray(self.upper_z(float(x))))
        if not math.isfinite(top):
            raise ValueError('Nonfinite intended sole ceiling')
        sole = [[max(a, self.z_min_mm), min(b, top)] for a, b in _subtract(material, cavity)
                if min(b, top) > max(a, self.z_min_mm)]
        return {'sole_intervals_mm': sole, 'shoe_intervals_mm': material,
                'cavity_intervals_mm': cavity, 'upper_z_mm': top}


class _VerticalMeshSections:
    def __init__(self, mesh):
        self.triangles = mesh.triangles
        self.low = self.triangles[:, :, :2].min(axis=1)
        self.high = self.triangles[:, :, :2].max(axis=1)

    def __call__(self, x, y, tolerance):
        site = np.array([x, y])
        mask = np.all(self.low <= site+tolerance, axis=1) & np.all(self.high >= site-tolerance, axis=1)
        triangles = self.triangles[mask]
        if not len(triangles):
            return []
        a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        v, w = b-a, c-a
        d = v[:, 0]*w[:, 1]-v[:, 1]*w[:, 0]
        valid = np.abs(d) > 1e-12
        a, v, w, d = a[valid], v[valid], w[valid], d[valid]
        dx, dy = x-a[:, 0], y-a[:, 1]
        beta, gamma = (dx*w[:, 1]-dy*w[:, 0])/d, (v[:, 0]*dy-v[:, 1]*dx)/d
        hit = (beta >= -1e-10) & (gamma >= -1e-10) & (beta+gamma <= 1+1e-10)
        z, signs = (a[:, 2]+beta*v[:, 2]+gamma*w[:, 2])[hit], np.sign(d[hit])
        groups = []
        for value, sign in sorted(zip(z, signs)):
            if groups and abs(value-groups[-1][0]) <= tolerance:
                groups[-1][1].add(int(sign))
            else:
                groups.append([float(value), {int(sign)}])
        depth, start, result = 0, None, []
        for value, directions in groups:
            following = depth+(1 if -1 in directions else 0)-(1 if 1 in directions else 0)
            if following < 0:
                raise ValueError('Vertical mesh line exits before entering material')
            if depth == 0 and following > 0:
                start = value
            if depth > 0 and following == 0 and value-start > tolerance:
                result.append([start, value])
            depth = following
        if depth:
            raise ValueError('Vertical mesh line has unmatched material entry')
        return result


def validate_lattice_coverage(model, *, native_sections: Callable, bounds_mm,
                              period_mm: float, resolution_mm: float,
                              clearance_mm: float, domain_field: Callable | None = None,
                              start_fraction: float = .55, lanes: int = 5,
                              reference_datum_mm: float | None = None,
                              tolerance_mm: float = 2e-5) -> dict:
    """Measure CAD support, certified-domain support and actual STL per X bin.

    Each bin spans one period; three off-grid X stations and multiple Y lanes
    avoid requiring a gyroid sheet on every line. Usable intervals exclude the
    experimental reserve and a full voxel diagonal at both vertical ends.
    Near-tip intervals too thin for those reserves are retained in the report
    without a lattice requirement. This vertical test is not an exact 3-D
    eroded-domain calculation. Missing support in substantial measurable bins
    fails; empty/thin terminal bins never turn an unmeasured toe into a PASS.
    """
    bounds = np.asarray(bounds_mm, dtype=float)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or np.any(bounds[1] <= bounds[0]):
        raise ValueError('Coverage bounds must be finite increasing XYZ pairs')
    if not all(math.isfinite(v) and v > 0 for v in (period_mm, resolution_mm, tolerance_mm)):
        raise ValueError('Period, resolution and tolerance must be positive finite values')
    if not math.isfinite(clearance_mm) or clearance_mm < 0 or not 0 <= start_fraction < 1:
        raise ValueError('Invalid clearance or longitudinal start fraction')
    if isinstance(lanes, bool) or not isinstance(lanes, int) or not 3 <= lanes <= 11:
        raise ValueError('Coverage lanes must be an integer from 3 to 11')
    if reference_datum_mm is not None and not math.isfinite(reference_datum_mm):
        raise ValueError('Reference datum must be finite')
    mesh = _mesh(model)
    report = {'geometry_status': 'NOT_VERIFIED', 'global_coverage_status': 'NOT_VERIFIED',
        'manufacturing_status': 'NOT_VERIFIED', 'scope': 'Finite native vertical CAD sections and actual reimported STL intervals in forefoot/toe period bins',
        'bounds_basis': 'Accepted CAD, independent of exported lattice bounds',
        'bounds_mm': bounds.tolist(), 'period_mm': period_mm, 'resolution_mm': resolution_mm,
        'clearance_mm': clearance_mm, 'cell_diagonal_guard_mm': math.sqrt(3)*resolution_mm,
        'vertical_end_reserve_mm': clearance_mm+math.sqrt(3)*resolution_mm,
        'minimum_required_domain_fraction': .01, 'minimum_required_lattice_fraction': .005,
        'reference_datum_mm': reference_datum_mm,
        'measurement_errors': [], 'bins': [], 'samples': [],
        'limitations': ['Finite line-length sums are not volume or global coverage proof.',
            'Vertical reserve is not an exact three-dimensional CAD offset; near-sidewall support is descriptive.',
            'Period-bin occupancy allows gyroid phase gaps on individual lines.',
            'Thin terminal CAD material is measured separately and is not required to contain lattice.']}
    if (not len(mesh.faces) or not np.isfinite(mesh.vertices).all() or not mesh.is_watertight
            or not mesh.is_winding_consistent):
        report['measurement_errors'].append('Coverage requires a finite closed consistently oriented mesh')
        return report
    mesh_section = _VerticalMeshSections(mesh)
    reserve = report['vertical_end_reserve_mm']
    start, end = bounds[0, 0]+start_fraction*(bounds[1, 0]-bounds[0, 0]), bounds[1, 0]
    edges = np.arange(start, end, period_mm).tolist()+[float(end)]
    ys = bounds[0, 1]+np.linspace(.30, .70, lanes)*(bounds[1, 1]-bounds[0, 1])
    failures = []
    for index, (left, right) in enumerate(zip(edges, edges[1:])):
        row = {'index': index, 'x_bounds_mm': [float(left), float(right)], 'samples': [],
               'cad_material_length_sum_mm': 0., 'usable_length_sum_mm': 0.,
               'domain_negative_length_sum_mm': 0., 'lattice_usable_length_sum_mm': 0.,
               'usable_column_count': 0, 'thin_reserve_column_count': 0,
               'empty_cad_column_count': 0, 'raised_usable_column_count': 0,
               'raised_usable_length_sum_mm': 0., 'raised_lattice_length_sum_mm': 0.,
               'raised_domain_negative_length_sum_mm': 0.,
               'lattice_outside_intended_sole_length_sum_mm': 0.}
        for x in left+(right-left)*np.array([.1732050808, .5031056200, .8273268354]):
            for y in ys:
                record = {'bin_index': index, 'x_mm': float(x), 'y_mm': float(y)}
                try:
                    native = native_sections(float(x), float(y))
                    sole = _intervals(native['sole_intervals_mm'])
                    usable = [[a+reserve, b-reserve] for a, b in sole if b-a > 2*reserve+2*resolution_mm]
                    actual = mesh_section(float(x), float(y), tolerance_mm)
                    raw_length = sum(b-a for a, b in sole)
                    usable_length = sum(b-a for a, b in usable)
                    actual_length = _overlap(actual, usable)
                    # Numerical interpolation near a cap has a tiny tolerance;
                    # intrusion checks use all actual intervals, including upper material.
                    allowed = [[a-tolerance_mm, b+tolerance_mm] for a, b in sole]
                    escaped = max(0., sum(b-a for a, b in actual)-_overlap(actual, allowed))
                    domain_length = 0.
                    raised = ([[max(a, reference_datum_mm+tolerance_mm), b] for a, b in usable
                              if b > max(a, reference_datum_mm+tolerance_mm)]
                              if reference_datum_mm is not None else [])
                    raised_domain_length = 0.
                    if domain_field is not None:
                        lengths = []
                        for intervals in (usable, raised):
                            length = 0.
                            for a, b in intervals:
                                count = max(2, math.ceil((b-a)/resolution_mm))
                                zs = a+(np.arange(count)+.38196601125)/count*(b-a)
                                points = np.column_stack([np.full(count, x), np.full(count, y), zs])
                                values = np.asarray(domain_field(points), dtype=float)
                                if values.shape != (count,) or not np.isfinite(values).all():
                                    raise ValueError('Domain sampler returned invalid field evidence')
                                length += float(np.mean(values < 0))*(b-a)
                            lengths.append(length)
                        domain_length, raised_domain_length = lengths
                    record.update(native)
                    record.update({'usable_intervals_mm': usable, 'lattice_intervals_mm': actual,
                        'usable_length_mm': usable_length, 'lattice_usable_length_mm': actual_length,
                        'domain_negative_length_mm': domain_length if domain_field is not None else None,
                        'raised_usable_intervals_mm': raised,
                        'raised_lattice_length_mm': _overlap(actual, raised),
                        'lattice_outside_intended_sole_length_mm': escaped,
                        'thin_solid_reserve_only': bool(sole and not usable)})
                    for key, value in [('cad_material_length_sum_mm', raw_length), ('usable_length_sum_mm', usable_length),
                            ('domain_negative_length_sum_mm', domain_length), ('lattice_usable_length_sum_mm', actual_length),
                            ('raised_usable_length_sum_mm', sum(b-a for a, b in raised)),
                            ('raised_lattice_length_sum_mm', _overlap(actual, raised)),
                            ('raised_domain_negative_length_sum_mm', raised_domain_length),
                            ('lattice_outside_intended_sole_length_sum_mm', escaped)]:
                        row[key] += value
                    row['usable_column_count'] += int(bool(usable))
                    row['thin_reserve_column_count'] += int(bool(sole and not usable))
                    row['empty_cad_column_count'] += int(not sole)
                    row['raised_usable_column_count'] += int(bool(raised))
                    row['samples'].append(len(report['samples']))
                    report['samples'].append(record)
                except Exception as error:
                    report['measurement_errors'].append({**record, 'reason': str(error)})
        size = row['usable_length_sum_mm']
        # A full-period bin with several measurable interior columns is enough
        # evidence to demand some material. Partial terminal bins remain measured.
        required = row['usable_column_count'] >= lanes and size >= 2*period_mm
        row.update({'coverage_required': required,
                    'domain_support_fraction': row['domain_negative_length_sum_mm']/size if size else None,
                    'lattice_support_fraction': row['lattice_usable_length_sum_mm']/size if size else None})
        raised_size = row['raised_usable_length_sum_mm']
        raised_required = row['raised_usable_column_count'] >= 3 and raised_size >= period_mm
        row['raised_coverage_required'] = raised_required
        row['raised_lattice_support_fraction'] = row['raised_lattice_length_sum_mm']/raised_size if raised_size else None
        row['raised_domain_support_fraction'] = row['raised_domain_negative_length_sum_mm']/raised_size if raised_size else None
        reasons = []
        if row['lattice_outside_intended_sole_length_sum_mm'] > tolerance_mm:
            reasons.append('Actual STL material intrudes above/outside the intended CAD sole domain')
        if required:
            if domain_field is not None and row['domain_support_fraction'] < report['minimum_required_domain_fraction']:
                reasons.append('Substantial native CAD sole bin has no adequate certified-domain support')
            if row['lattice_support_fraction'] < report['minimum_required_lattice_fraction']:
                reasons.append('Substantial native CAD sole bin has no adequate exported lattice support')
        if raised_required:
            if domain_field is not None and row['raised_domain_support_fraction'] < report['minimum_required_domain_fraction']:
                reasons.append('Risen native CAD sole above the reference datum has no adequate certified-domain support')
            if row['raised_lattice_support_fraction'] < report['minimum_required_lattice_fraction']:
                reasons.append('Risen native CAD sole above the reference datum has no adequate exported lattice support')
        row['status'] = 'FAIL' if reasons else 'PASS' if required else 'MEASURED_RESERVE_OR_EDGE'
        row['failure_reasons'] = reasons
        if reasons:
            failures.append(index)
        report['bins'].append(row)
    report['failed_bin_indices'] = failures
    report['coverage_required_bin_count'] = sum(row['coverage_required'] for row in report['bins'])
    report['raised_coverage_required_bin_count'] = sum(row['raised_coverage_required'] for row in report['bins'])
    for name, predicate in [('native_material', lambda sample: bool(sample['sole_intervals_mm'])),
                            ('usable', lambda sample: bool(sample['usable_intervals_mm'])),
                            ('lattice_in_usable', lambda sample: sample['lattice_usable_length_mm'] > tolerance_mm)]:
        positions = [sample['x_mm'] for sample in report['samples'] if predicate(sample)]
        report[name+'_sampled_x_bounds_mm'] = [min(positions), max(positions)] if positions else None
    if reference_datum_mm is not None and not report['raised_coverage_required_bin_count']:
        report['measurement_errors'].append('No substantial raised sole bin was measured above the reference datum; risen-toe coverage remains unverified')
    report['geometry_status'] = ('FAIL' if failures else 'NOT_VERIFIED' if report['measurement_errors']
                                or not report['coverage_required_bin_count'] else 'PASS')
    report['domain_support_status'] = 'CHECKED' if domain_field is not None else 'NOT_VERIFIED'
    return report
