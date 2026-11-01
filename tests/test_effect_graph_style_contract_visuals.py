"""Real M6.5 fixture images and serializable reports reproduce byte for byte."""
import importlib.util
import json
from pathlib import Path

import pytest


def test_style_bounds_visuals_are_reproducible_and_inspect_actual_graph_output(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'integration' / 'build_effect_graph_m6_5_visuals.py'
    spec = importlib.util.spec_from_file_location('style_bounds_visuals', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first, second = tmp_path/'a', tmp_path/'b'
    a, b = module.build(first), module.build(second)
    assert a == b and len(a['cases']) == 12
    assert any(case['bounds']['may_clip'] for case in a['cases'])
    assert any(not case['bounds']['may_clip'] for case in a['cases'])
    assert (first/'style-bounds-contact-sheet.png').read_bytes() == (second/'style-bounds-contact-sheet.png').read_bytes()
    for path in first.iterdir():
        assert path.read_bytes() == (second/path.name).read_bytes()
        if path.suffix == '.json':
            json.loads(path.read_text(encoding='utf-8'))
    with pytest.raises(FileExistsError):
        module.build(first)


def test_documented_graph_fragment_validates_and_descriptors_cover_its_binding():
    from framerudiments.effect_graph import EffectGraph
    path = Path(__file__).resolve().parents[1] / 'examples' / 'effect-graph-style-report.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    graph = EffectGraph(data['effect_graph'])
    manifest = graph.style_manifest(active_only=True)
    for binding in data['effect_graph_controls']:
        graph.validate_control_target(binding['node'], binding['property'])
        assert binding['property'] in manifest['nodes'][binding['node']]['controls']
