"""Repeat actual field sampling and extraction on the completed CAD domain."""
from argparse import ArgumentParser
import json
from pathlib import Path
import time

import numpy as np

from shoe_poc.lattice.domain import GridPlan
from shoe_poc.lattice.gyroid import GyroidParameters, sample_gyroid
from shoe_poc.lattice.meshing import extract_lattice_mesh, export_lattice
from shoe_poc.reporting import sha256, utc_now, write_json, environment
from shoe_poc.resource_limits import MemoryBudget
from shoe_poc.schemas import ShoeSpec


def repeat(directory):
    started = time.perf_counter()
    target = directory/'debug/repeat'
    target.mkdir(parents=True, exist_ok=True)
    source = {name: sha256(directory/name) for name in
              ('lattice.stl', 'domain_field.npz', 'domain_metadata.json', 'gyroid_field.npz', 'shoe_spec.json')}
    with MemoryBudget(6144, target, 'repeat-terminal-lattice'):
        spec = ShoeSpec.load(directory/'shoe_spec.json')
        meta = json.loads((directory/'domain_metadata.json').read_text())
        with np.load(directory/'domain_field.npz', allow_pickle=False) as saved:
            values, origin, spacing = saved['field_mm'], saved['origin_mm'], float(saved['spacing_mm'])
        plan = GridPlan(tuple(origin[i]+np.arange(values.shape[i])*spacing for i in range(3)),
                        spacing, meta['estimated_peak_bytes'])
        field = sample_gyroid(GyroidParameters(spec.lattice.period_mm, spec.lattice.threshold, spacing,
                              tuple(spec.lattice.phase_origin_mm)), plan, domain_signed_field=values,
                              domain_metadata=meta, max_voxels=25_000_000, max_memory_bytes=6*1024**3)
        with np.load(directory/'gyroid_field.npz', allow_pickle=False) as saved:
            identical_field = bool(np.array_equal(saved['values'], field.values))
        result = extract_lattice_mesh(field, max_memory_bytes=6*1024**3)
        export_lattice(result, target/'lattice.stl')
        same = sha256(target/'lattice.stl') == source['lattice.stl']
        if any(sha256(directory/name) != digest for name, digest in source.items()):
            raise ValueError('Completed lattice source changed during repeatability verification')
        report = {'generated_at': utc_now(), 'geometry_status': 'PASS' if same and identical_field else 'FAIL',
            'identical_sampled_field': identical_field, 'identical_stl_sha256': same,
            'primary_stl_sha256': source['lattice.stl'], 'repeated_stl_sha256': sha256(target/'lattice.stl'),
            'source_files': source, 'meshing': result.metadata,
            'scope': 'Independent sampling, MC extraction and actual STL serialization of identical certified CAD domain. No second native CAD domain reconstruction claimed.',
            'runtime_seconds': time.perf_counter()-started, 'environment': environment()}
        write_json(directory/'repeatability_report.json', report)
        if report['geometry_status'] != 'PASS':
            raise RuntimeError('Independent repeated field/STL does not match')
        return report


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/m2_toe_complete'))
    repeat(parser.parse_args().output)
