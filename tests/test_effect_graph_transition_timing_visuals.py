"""Reproducible pixel/manifest evidence for beat-timed transitions."""
import importlib.util
from pathlib import Path


def test_visual_evidence_deterministic(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'integration' / 'build_effect_graph_m5_5_visuals.py'
    spec = importlib.util.spec_from_file_location('timing_visuals', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.build(tmp_path / 'first.png', tmp_path / 'a.json')
    second = module.build(tmp_path / 'second.png', tmp_path / 'b.json')
    assert first == second
    assert len(first['frame_hashes']) == 30
    assert first['frame_hashes']['linear:0'] != first['frame_hashes']['linear:4']
    assert first['frame_hashes']['pingpong:0'] == first['frame_hashes']['pingpong:4']
    assert (tmp_path / 'first.png').read_bytes() == (tmp_path / 'second.png').read_bytes()
