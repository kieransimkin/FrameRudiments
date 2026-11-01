"""The QA gallery must be actual graph output and reproducible without assets."""
import importlib.util
from pathlib import Path

from PIL import Image


def test_alpha_style_gallery_is_repeatable_and_covers_all_style_families(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'integration/build_effect_graph_m6_visuals.py'
    spec = importlib.util.spec_from_file_location('style_visuals', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first, second = module.build(tmp_path/'a'), module.build(tmp_path/'b')
    assert len(first['frames']) == 12
    assert first == second
    assert first['creative_approval'] is False
    assert len({row['pixel_sha256'] for row in first['frames']}) == 12
    for row in first['frames']:
        with Image.open(tmp_path/'a'/row['file']) as image:
            assert image.mode == 'RGBA' and image.size == (240, 180)
    assert (tmp_path/'a'/'alpha-styles-contact-sheet.png').is_file()
