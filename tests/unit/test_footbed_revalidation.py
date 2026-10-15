"""Saved-CAD revalidation must retain input identity and unrelated uncertainty."""
import json
from pathlib import Path

import pytest
from build123d import Box, Pos, export_step

from scripts.revalidate_footbed import revalidate
from shoe_poc.exporting import export_mesh
from shoe_poc.reporting import artifact_manifest, write_json


@pytest.fixture
def saved_case(tmp_path):
    last = Pos(0, 0, 5)*Box(10, 10, 10)
    cavity = Pos(0, 0, 5)*Box(12, 12, 12)
    material = (Pos(0, 0, 5)*Box(17, 17, 17)).cut(cavity)
    for name, shape in [('source.step', last), ('last_normalized.step', last),
                        ('cavity.step', cavity), ('base_shoe.step', material)]:
        export_step(shape, tmp_path/name)
    export_mesh(material, tmp_path/'base_shoe.stl')
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root/'input/specs/shoe_spec.json').read_text())
    spec['last']['file'] = str(tmp_path/'source.step')
    write_json(tmp_path/'shoe_spec.json', spec)
    write_json(tmp_path/'design_report.json', {
        'run_id': 'saved', 'geometry_status': 'PARTIAL',
        'unmeasurable_regions': [{'region': 'actual exterior rim', 'reason': 'Not measured'}],
        'checks': [{'name': 'sampled_underfoot_material_thickness', 'status': 'PASS'},
                   {'name': 'underfoot_clearance_and_region_coverage', 'status': 'PASS'}]})
    outputs = artifact_manifest(tmp_path)
    write_json(tmp_path/'execution_report.json', {
        'run_id': 'saved', 'status': 'PASS', 'input_sha256': outputs['source.step']['sha256'],
        'outputs': outputs})
    return tmp_path


def test_revalidation_rejects_modified_threshold_spec(saved_case):
    path = saved_case/'shoe_spec.json'
    spec = json.loads(path.read_text())
    spec['upper']['thickness_mm'] = 1.5
    write_json(path, spec)
    before = (saved_case/'design_report.json').read_bytes()
    with pytest.raises(ValueError, match='shoe_spec.json'):
        revalidate(saved_case)
    assert (saved_case/'design_report.json').read_bytes() == before


def test_real_cad_footbed_pass_does_not_qualify_unmeasured_rim(saved_case):
    assert revalidate(saved_case) is True  # Only the footbed leaf passes.
    report = json.loads((saved_case/'design_report.json').read_text())
    assert report['underfoot']['geometry_status'] == 'PASS'
    assert report['geometry_status'] == 'PARTIAL'
    assert report['unmeasurable_regions'] == [{'region': 'actual exterior rim', 'reason': 'Not measured'}]
    assert json.loads((saved_case/'execution_report.json').read_text())['status'] == 'FAIL'
