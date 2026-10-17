"""Explicitly scoped manufacturing checks; missing rules never imply a pass."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from shoe_poc.reporting import sha256


def manufacturing_report(mesh, rules_path: Path, *, measurements: dict | None = None) -> dict:
    rules_path = Path(rules_path)
    config = json.loads(rules_path.read_text())
    if config.get('units') != 'mm' or not isinstance(config.get('rules'), dict):
        raise ValueError('Manufacturing rules require millimetres and an explicit rules object')
    measurements = measurements or {}
    checks = {}
    for name in ('minimum_wall_thickness_mm', 'minimum_feature_size_mm', 'minimum_clearance_mm'):
        entry = config['rules'].get(name, {})
        threshold = entry.get('value')
        if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                                      or not np.isfinite(threshold) or threshold <= 0):
            raise ValueError(f'Invalid manufacturing threshold: {name}')
        measured = measurements.get(name, {})
        # Sample minima are useful evidence but do not certify a global lower bound.
        certified = measured.get('global_lower_bound_mm')
        if threshold is None:
            status, reason = 'NOT_VERIFIED', 'Factory/material threshold is not configured'
        elif certified is None or measured.get('global_bound_proved') is not True:
            status, reason = 'NOT_VERIFIED', 'No independently proved global geometric lower bound'
        elif not np.isfinite(certified) or certified < 0:
            raise ValueError(f'Invalid global measurement: {name}')
        else:
            status = 'PASS' if certified >= threshold else 'FAIL'
            reason = 'Compared configured threshold with independently proved global bound'
        checks[name] = {'status': status, 'threshold_mm': threshold, 'measurement': measured, 'reason': reason}
    envelope = config.get('build_volume_mm')
    extents = np.asarray(mesh.extents, dtype=float)
    if extents.shape != (3,) or not np.isfinite(extents).all() or np.any(extents <= 0):
        raise ValueError('Manufacturing report requires a finite nonempty mesh')
    if envelope is None:
        checks['build_volume'] = {'status': 'NOT_VERIFIED', 'reason': 'Printer envelope and build orientation are not configured',
                                  'model_extents_mm': extents.tolist()}
    else:
        limits = np.asarray(envelope, dtype=float)
        if limits.shape != (3,) or not np.isfinite(limits).all() or np.any(limits <= 0):
            raise ValueError('build_volume_mm must contain three positive finite lengths')
        checks['build_volume'] = {'status': 'PASS' if np.all(extents <= limits) else 'FAIL',
                                  'model_extents_mm': extents.tolist(), 'configured_extents_mm': limits.tolist(),
                                  'orientation_scope': 'Current XYZ orientation only; no packing/orientation search'}
    checks['powder_removal'] = {'status': 'NOT_VERIFIED',
        'reason': 'Geometric void access, where measured, does not qualify powder flow or factory cleaning rules'}
    statuses = [row['status'] for row in checks.values()]
    return {'status': 'FAIL' if 'FAIL' in statuses else 'NOT_VERIFIED' if 'NOT_VERIFIED' in statuses else 'PASS',
        'rules_file': str(rules_path.resolve()), 'rules_sha256': sha256(rules_path),
        'process': config.get('process'), 'material_profile': config.get('material_profile'),
        'commercial_process_validation': config.get('commercial_process_validation', False),
        'checks': checks, 'physical_printing': 'NOT_TESTED', 'wear_testing': 'NOT_TESTED'}
