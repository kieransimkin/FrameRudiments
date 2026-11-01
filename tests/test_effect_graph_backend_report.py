"""Actual report CLI and exclusive output-file behavior."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def command():
    path = Path(__file__).resolve().parents[1] / 'integration/report_effect_graph_backend.py'
    spec = importlib.util.spec_from_file_location('backend_report_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def graph_file(tmp_path):
    file = tmp_path / 'graph.json'
    file.write_text(json.dumps({'nodes': [{'id': 's', 'type': 'source'},
                                        {'id': 'out', 'type': 'chroma_key', 'inputs': ['s']}], 'output': 'out'}))
    return file


def test_report_cli_never_renders_and_retains_authored_scope(command, graph_file, tmp_path):
    output = tmp_path / 'plan.json'
    assert command.main([str(graph_file), '--size', '1920', '1080', '--output', str(output)]) == 0
    plan = json.loads(output.read_text())
    assert plan['input_scope']['media_assets_validated'] is False
    assert plan['resources']['upload_bytes'] == 0
    with pytest.raises(SystemExit):
        command.main([str(graph_file), '--size', '1920', '1080', '--output', str(output)])


def test_strict_failure_is_reported_with_nonzero_exit(command, graph_file, capsys):
    assert command.main([str(graph_file), '--size', '8', '8', '--mode', 'gpu']) == 2
    assert not json.loads(capsys.readouterr().out)['eligible']


def test_cli_explicit_probe_failure_is_visible(command, graph_file, capsys, monkeypatch):
    def failed(mode, *, report):
        report.update(state='unavailable', fallback_reason='test context failure')
        return None
    monkeypatch.setattr(command, 'create_gpu_backend', failed)
    assert command.main([str(graph_file), '--size', '8', '8', '--mode', 'auto', '--probe-gpu']) == 0
    assert json.loads(capsys.readouterr().out)['initialization']['fallback_reason'] == 'test context failure'


def test_cli_does_not_probe_cpu(command, graph_file):
    with pytest.raises(SystemExit):
        command.main([str(graph_file), '--size', '8', '8', '--probe-gpu'])


def test_cli_project_dimensions_and_native_scope(command, graph_file, tmp_path, capsys):
    data = json.loads(graph_file.read_text())
    path = tmp_path / 'project.json'
    path.write_text(json.dumps({'width': 12, 'height': 9, 'effect_graph': data, 'effect_graph_controls': [{}]}))
    assert command.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['input_scope']['native_bindings_present']
    assert not report['parameters_resolved']
