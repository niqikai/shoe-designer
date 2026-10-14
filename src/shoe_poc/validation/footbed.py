"""Independent underfoot material probes on the actual final CAD solid.

This is a bounded directional lower-bound check, not a global wall-thickness or
manufacturing certificate. The footbed can intentionally exceed upper thickness.
"""
from __future__ import annotations

import math

import numpy as np

from shoe_poc.reporting import utc_now
from .surface import (CAD_TOLERANCE_MM, FIT_ERROR_MM, THICKNESS_ERROR_MM,
                      _LoadedSolidClassifier, _candidate_sites, _ray_hits)


def validate_underfoot_material(last_shape, build, spec):
    """Probe genuine downward source normals against cavity and final material.

    Every selected downward site within 25 mm of the last base is retained as a
    measurement or an explicit error. This deliberately includes sole material,
    unlike the uniform upper-offset check. Normals are verified by source-solid
    membership. Material intervals are verified against the actual final solid.
    """
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    lower_bound = spec.upper.thickness_mm-THICKNESS_ERROR_MM
    report = {
        'geometry_status': 'NOT_VERIFIED', 'overall_status': 'PARTIAL',
        'method': 'Trim-classified source CAD points and verified downward outward normals; exact cavity/final-shoe ray hits and midpoint material classification',
        'checked_at': utc_now(),
        'nominal_upper_thickness_mm': spec.upper.thickness_mm,
        'experimental_thickness_allowance_mm': THICKNESS_ERROR_MM,
        'minimum_directional_material_target_mm': lower_bound,
        'clearance_target_mm': spec.upper.clearance_mm,
        'clearance_absolute_error_mm': FIT_ERROR_MM,
        'selection': {'maximum_source_height_above_base_mm': 25.,
                      'maximum_downward_normal_z': -.55,
                      'heel_end_x_fraction': .25, 'toe_start_x_fraction': .75,
                      'sole_exclusion_applied': False},
        'samples': [], 'measurement_errors': [], 'coverage': {}, 'regions': {},
        'minimum_measured_directional_material_mm': None,
        'maximum_measured_directional_material_mm': None,
        'minimum_measured_clearance_mm': None, 'maximum_measured_clearance_mm': None,
        'limitations': ['The minimum is the minimum of finite outward-normal samples, not a global shortest-distance material minimum.',
                        'The configured nominal upper thickness and 0.35 mm allowance belong to the experimental CAD policy, not manufacturing or comfort qualification.',
                        'Source normals with Z above -0.55 and source sites higher than 25 mm are outside this bounded underfoot scope.'],
    }
    shapes = (last_shape, build.shape, build.cavity)
    if any(not shape.is_valid or len(shape.solids()) != 1 or shape.volume <= 0 for shape in shapes):
        report['geometry_status'] = 'FAIL'
        report['measurement_errors'].append({'reason': 'Source, cavity or final CAD is not a valid positive single solid'})
        return report
    box = last_shape.bounding_box()
    candidates, coverage = _candidate_sites(last_shape)
    selected = [(face, point, normal) for face, point, normal in candidates
                if point[2] <= box.min.Z+25. and normal[2] <= -.55]
    report['coverage'] = {'source_site_selection': coverage, 'selected_underfoot_site_count': len(selected),
                          'selected_source_face_count': len({site[0] for site in selected})}
    source_classifier = _LoadedSolidClassifier(last_shape)
    final_classifier = _LoadedSolidClassifier(build.shape)
    cavity_classifier = _LoadedSolidClassifier(build.cavity)
    final_ray, cavity_ray = IntCurvesFace_ShapeIntersector(), IntCurvesFace_ShapeIntersector()
    final_ray.Load(build.shape.wrapped, CAD_TOLERANCE_MM)
    cavity_ray.Load(build.cavity.wrapped, CAD_TOLERANCE_MM)
    ray_length = math.sqrt(sum(value*value for value in build.shape.bounding_box().size))+10.

    def region_of(x):
        fraction = (x-box.min.X)/box.size.X
        return 'heel' if fraction <= .25 else 'toe' if fraction >= .75 else 'middle'

    for face_index, point, normal in selected:
        record = {'source_face_index': face_index, 'point_mm': list(point),
                  'outward_normal': list(normal), 'region': region_of(point[0])}
        try:
            p, n = np.asarray(point), np.asarray(normal)
            if not source_classifier.inside(p-n*.001) or source_classifier.inside(p+n*.001):
                raise ValueError('Source normal is ambiguous or not outward')
            if not cavity_classifier.inside(p):
                raise ValueError('Source probe is outside the actual cavity')
            hits = _ray_hits(final_ray, point, normal, ray_length)
            boundaries = sorted(set([0., *hits, ray_length]))
            intervals = [(a, b) for a, b in zip(boundaries, boundaries[1:])
                         if b-a > 1e-5 and final_classifier.inside(p+n*((a+b)/2))]
            if not intervals or intervals[0][1] >= ray_length-1e-5:
                raise ValueError('Underfoot ray has no closed material interval')
            a, b = intervals[0]
            cavity_hits = _ray_hits(cavity_ray, point, normal, ray_length)
            if not cavity_hits:
                raise ValueError('Underfoot ray has no measured cavity exit')
            cavity_exit = cavity_hits[0]
            clearance, material = float(a), float(b-a)
            valid = (material >= lower_bound
                     and abs(clearance-spec.upper.clearance_mm) <= FIT_ERROR_MM
                     and abs(clearance-cavity_exit) <= FIT_ERROR_MM)
            record.update({'clearance_mm': clearance, 'cavity_exit_mm': cavity_exit,
                           'directional_material_mm': material,
                           'final_material_intervals_mm': [list(interval) for interval in intervals],
                           'status': 'PASS' if valid else 'FAIL'})
            report['samples'].append(record)
        except (ValueError, RuntimeError) as error:
            record['reason'] = str(error)
            report['measurement_errors'].append(record)
    for region in ('heel', 'middle', 'toe'):
        samples = [record for record in report['samples'] if record['region'] == region]
        errors = [record for record in report['measurement_errors'] if record.get('region') == region]
        status = ('FAIL' if any(record['status'] == 'FAIL' for record in samples)
                  else 'NOT_VERIFIED' if errors or not samples else 'PASS')
        report['regions'][region] = {'geometry_status': status, 'sample_count': len(samples),
                                     'measurement_error_count': len(errors),
                                     'minimum_directional_material_mm': min(
                                         (record['directional_material_mm'] for record in samples), default=None)}
    material = [record['directional_material_mm'] for record in report['samples']]
    clearance = [record['clearance_mm'] for record in report['samples']]
    report.update({'minimum_measured_directional_material_mm': min(material, default=None),
                   'maximum_measured_directional_material_mm': max(material, default=None),
                   'minimum_measured_clearance_mm': min(clearance, default=None),
                   'maximum_measured_clearance_mm': max(clearance, default=None)})
    statuses = [region['geometry_status'] for region in report['regions'].values()]
    report['geometry_status'] = ('FAIL' if 'FAIL' in statuses else 'NOT_VERIFIED'
                                if report['measurement_errors'] or 'NOT_VERIFIED' in statuses else 'PASS')
    if report['geometry_status'] == 'FAIL':
        report['overall_status'] = 'FAIL'
    return report
