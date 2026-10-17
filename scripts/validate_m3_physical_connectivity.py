"""Measure M3 shell nesting and positive physical-body count on frozen output."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import trimesh

from shoe_poc.assembly.geometry import validate_native_mesh


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_connectivity(mesh_npz: Path, source_stl: Path, output: Path,
                          *, nesting_face_budget: int = 2_500_000) -> dict:
    """Write a source-bound independent connectivity diagnostic, no repair."""
    with np.load(mesh_npz, allow_pickle=False) as data:
        if not {'vertices', 'faces'} <= set(data.files):
            raise ValueError('Native mesh archive must contain vertices and faces')
        mesh = trimesh.Trimesh(vertices=data['vertices'], faces=data['faces'], process=False)
    validation = validate_native_mesh(mesh, require_single_body=False, area_floor_mm2=0.,
        self_intersection_pair_budget=0,
        nesting_accelerator_face_budget=nesting_face_budget)
    positive = [row for row in validation.get('shells', []) if row.get('signed_volume_mm3', 0) > 0]
    body_status = ('NOT_VERIFIED' if validation.get('shell_nesting_status') != 'PASS'
                   or validation.get('physical_body_count') is None else
                   'PASS' if validation['physical_body_count'] == 1 else 'FAIL')
    report = {
        'milestone': 3,
        'status': 'PARTIAL' if body_status != 'PASS' or validation['global_self_intersections'] != 'PASS' else 'PASS',
        'source': {'native_mesh_npz': str(mesh_npz.resolve()), 'native_mesh_sha256': sha256(mesh_npz),
                   'source_stl': str(source_stl.resolve()), 'source_stl_sha256': sha256(source_stl),
                   'triangle_count': int(len(mesh.faces)), 'vertex_count': int(len(mesh.vertices)),
                   'relation': 'Native double-precision mesh bound to the STL already recorded as coordinate-roundtrip exact in the assembly report'},
        'shell_nesting_status': validation.get('shell_nesting_status'),
        'global_self_intersections': validation.get('global_self_intersections'),
        'physical_body_count': validation.get('physical_body_count'),
        'single_physical_body_status': body_status,
        'boundary_shell_count': validation.get('boundary_shell_count'),
        'positive_orientation_shell_count': validation.get('positive_orientation_shell_count'),
        'positive_shells_by_nesting_depth': {
            str(depth): sum(row.get('nesting_depth') == depth for row in positive)
            for depth in sorted({row.get('nesting_depth') for row in positive})},
        'positive_shell_volume_total_mm3': float(sum(row['signed_volume_mm3'] for row in positive)),
        'nested_positive_shell_volume_mm3': float(sum(row['signed_volume_mm3'] for row in positive
                                                        if row.get('nesting_depth', 0) > 0)),
        'repair_applied': False,
        'components_removed': 0,
        'validation': validation,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mesh-npz', type=Path, required=True)
    parser.add_argument('--source-stl', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--nesting-face-budget', type=int, default=2_500_000)
    args = parser.parse_args()
    report = validate_connectivity(args.mesh_npz, args.source_stl, args.output,
                                   nesting_face_budget=args.nesting_face_budget)
    print(json.dumps({key: report[key] for key in ('status', 'shell_nesting_status',
          'global_self_intersections', 'physical_body_count', 'single_physical_body_status',
          'positive_shells_by_nesting_depth', 'nested_positive_shell_volume_mm3')}))
    return 0 if report['single_physical_body_status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
