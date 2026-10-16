"""Repeat actual field sampling and extraction on the completed CAD domain."""
from argparse import ArgumentParser
import json
from pathlib import Path
import time
import shutil

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
              ('lattice.stl', 'domain_field.npz', 'domain_metadata.json', 'gyroid_field.npz', 'shoe_spec.json',
               'debug/refinement_query_seed.json')}
    with MemoryBudget(6144, target, 'repeat-terminal-lattice'):
        spec = ShoeSpec.load(directory/'shoe_spec.json')
        meta = json.loads((directory/'domain_metadata.json').read_text())
        with np.load(directory/'domain_field.npz', allow_pickle=False) as saved:
            values, origin, spacing = saved['field_mm'], saved['origin_mm'], float(saved['spacing_mm'])
        from build123d import import_step
        from shoe_poc.lattice.cache import load_certified_domain
        from shoe_poc.lattice.envelope import SoleEnvelope
        from shoe_poc.lattice.pipeline import accepted_baseline
        from shoe_poc.lattice.refinement import refine_sole_domain
        execution = json.loads((directory/'execution_report.json').read_text())
        baseline = execution['baseline']
        base = Path(baseline['directory'])
        if accepted_baseline(base, spec, Path(baseline['acceptance_file'])) != baseline:
            raise ValueError('Domain replay input does not identify the accepted CAD')
        envelope = SoleEnvelope.from_metadata(meta['envelope'])
        shoe, cavity = import_step(base/'base_shoe.step'), import_step(base/'cavity.step')
        seed = load_certified_domain(Path(execution['domain_source']['seed_source']['directory']),
            baseline=baseline, shape=shoe, z_min_mm=meta['sole_z_bounds_mm'][0],
            z_max_mm=meta['sole_z_bounds_mm'][1], settings=spec.lattice.domain,
            spacing_mm=spacing, max_voxels=25_000_000, max_memory_mb=6144,
            envelope=envelope, allow_refinement_seed=True)
        shutil.copy2(directory/'debug/refinement_query_seed.json', target/'refinement_query_seed.json')
        replay = refine_sole_domain(seed, shoe, cavity, settings=spec.lattice.domain.refinement,
            envelope=envelope, checkpoint_path=target/'refinement_query_seed.json', source_identity=baseline)
        identical_domain = bool(np.array_equal(values, replay.field_mm))
        if not identical_domain or replay.metadata['refinement']['native_distance_queries'] != 0:
            raise ValueError('Exact-query journal replay did not reproduce the certified domain without new native queries')
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
        report = {'generated_at': utc_now(), 'geometry_status': 'PASS' if same and identical_field and identical_domain else 'FAIL',
            'identical_replayed_domain': identical_domain,
            'domain_query_replay': replay.metadata['refinement'],
            'identical_sampled_field': identical_field, 'identical_stl_sha256': same,
            'primary_stl_sha256': source['lattice.stl'], 'repeated_stl_sha256': sha256(target/'lattice.stl'),
            'source_files': source, 'meshing': result.metadata,
            'scope': 'Deterministic domain reconstruction using the bound exact-CAD query journal, followed by independent gyroid sampling, MC extraction and actual STL serialization. No fresh native CAD queries or second native sampling run claimed.',
            'runtime_seconds': time.perf_counter()-started, 'environment': environment()}
        write_json(directory/'repeatability_report.json', report)
        if report['geometry_status'] != 'PASS':
            raise RuntimeError('Independent repeated field/STL does not match')
        return report


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/m2_toe_complete'))
    repeat(parser.parse_args().output)
