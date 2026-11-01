"""Synthetic visual evidence is reproducible, not a substitute for references."""
import importlib.util
from pathlib import Path


def generator():
    path = Path(__file__).resolve().parents[1] / 'integration' / 'build_effect_graph_m7_4_visuals.py'
    spec = importlib.util.spec_from_file_location('raster_visuals', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_visual_evidence_reproducible(tmp_path):
    g = generator()
    a = g.build(tmp_path / 'first')
    b = g.build(tmp_path / 'second')
    assert a == b and len(a['cases']) == 16
    assert len({row['pixels_sha256'] for row in a['cases']}) == 16
    assert (tmp_path / 'first' / 'raster-contact-sheet.png').read_bytes() == (tmp_path / 'second' / 'raster-contact-sheet.png').read_bytes()
