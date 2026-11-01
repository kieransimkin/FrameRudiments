"""Actual synthetic graph render reproducibility (not substitute stock imagery)."""
import importlib.util
from pathlib import Path

from PIL import Image


def test_overlay_contact_sheet_and_all_rgba_outputs_are_repeatable(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'integration/build_effect_graph_m6_4_visuals.py'
    spec = importlib.util.spec_from_file_location('m64_visuals', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    a, b = module.build(tmp_path/'a'), module.build(tmp_path/'b')
    assert a == b and len(a['cases']) == 16
    assert len({row['pixel_sha256'] for row in a['cases']}) == 16
    for row in a['cases']:
        with Image.open(tmp_path/'a'/row['file']) as image:
            assert image.mode == 'RGBA' and image.size == (240, 160)
    assert (tmp_path/'a/overlays-contact-sheet.png').read_bytes() == (tmp_path/'b/overlays-contact-sheet.png').read_bytes()
