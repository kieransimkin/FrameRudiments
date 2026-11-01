"""Reproducibility of actual CPU renders, not an illustrated reference."""
import importlib.util
from pathlib import Path


def test_field_visual_generator_reproduces_all_renders(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'integration/build_effect_graph_m7_5_visuals.py'
    spec = importlib.util.spec_from_file_location('field_visuals', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.build(tmp_path / 'a')
    second = module.build(tmp_path / 'b')
    assert len(first) == 16 and first == second
    assert (tmp_path/'a/procedural-fields-contact-sheet.png').read_bytes() == (tmp_path/'b/procedural-fields-contact-sheet.png').read_bytes()
