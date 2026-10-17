import json

import pytest

from scripts.report_m2_closeout import report


def test_report_document_cannot_overwrite_closeout_evidence(tmp_path):
    source = tmp_path/'output/m2_toe_complete'
    output = tmp_path/'output/m2_closeout'
    source.mkdir(parents=True)
    (output/'debug').mkdir(parents=True)
    (source/'execution_report.json').write_text(json.dumps({'baseline': {'directory': str(tmp_path/'output/m1')}}))
    (output/'debug/preserved_artifacts_before.json').write_text('{}')
    with pytest.raises(ValueError, match='preserve frozen evidence'):
        report(source, output, tmp_path/'docs/approval.json', output/'parameter_change_report.json')


def test_report_document_cannot_overwrite_a_nested_closeout_file(tmp_path):
    source = tmp_path/'output/m2_toe_complete'
    output = tmp_path/'output/m2_closeout'
    source.mkdir(parents=True)
    (output/'debug').mkdir(parents=True)
    (source/'execution_report.json').write_text(json.dumps({'baseline': {'directory': str(tmp_path/'output/m1')}}))
    (output/'debug/preserved_artifacts_before.json').write_text('{}')
    with pytest.raises(ValueError, match='preserve frozen evidence'):
        report(source, output, tmp_path/'docs/approval.json', output/'nested/report.md')
