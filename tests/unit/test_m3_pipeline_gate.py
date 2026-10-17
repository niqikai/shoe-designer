"""The M3 refusal path must leave absent or existing output untouched."""
import json

import pytest

from shoe_poc.assembly.pipeline import AssemblyGateError, assemble_shoe


@pytest.mark.parametrize('existing_output', [False, True])
def test_nonready_m2_gate_refuses_before_creating_or_overwriting_output(
        tmp_path, monkeypatch, existing_output):
    from shoe_poc import acceptance

    project = tmp_path / 'project'
    project.mkdir()
    spec = project / 'assembly.json'
    spec.write_text(json.dumps({'schema_version': 'm3-0.1', 'm1_directory': 'm1-source',
                                'm2_directory': 'm2-source',
                                'm2_acceptance_report': 'approval.json'}))
    source = project / 'm2-source'
    closeout = project / 'm2-closeout'
    approval = project / 'approval.json'
    output = project / 'm3-output'
    if existing_output:
        output.mkdir()
        (output / 'keep.txt').write_text('existing user data')

    def blocked(*args, **kwargs):
        raise ValueError('M3 gate BLOCKED_PENDING_REGRESSION')

    monkeypatch.setattr(acceptance, 'validate_m2_acceptance', blocked)

    with pytest.raises(AssemblyGateError, match='not READY'):
        assemble_shoe(spec_path=spec, m2_source=source, m2_closeout=closeout,
                      appearance_acceptance=approval, output=output,
                      project_root=project)

    if existing_output:
        assert sorted(path.name for path in output.iterdir()) == ['keep.txt']
        assert (output / 'keep.txt').read_text() == 'existing user data'
    else:
        assert not output.exists()


def test_cli_gate_refusal_precedes_output_and_rss_watcher(tmp_path, monkeypatch):
    from shoe_poc import acceptance
    from shoe_poc.cli import main

    spec = tmp_path / 'assembly.json'
    spec.write_text(json.dumps({'schema_version': 'm3-0.1'}))
    output = tmp_path / 'm3-output'

    def blocked(*args, **kwargs):
        raise ValueError('M3 gate BLOCKED_PENDING_REGRESSION')

    monkeypatch.setattr(acceptance, 'validate_m2_acceptance', blocked)

    result = main(['assemble-shoe', '--spec', str(spec), '--output', str(output)])

    assert result == 3
    assert not output.exists()
