"""Deterministic M5.4 contact-sheet and manifest regression."""
import hashlib
import importlib.util
from pathlib import Path

from PIL import Image


def load_visual_generator():
    path = Path(__file__).resolve().parents[1] / 'integration' / 'build_effect_graph_m5_4_visuals.py'
    spec = importlib.util.spec_from_file_location('m54_visuals', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contact_sheet_has_three_distinct_rows_and_is_repeatable(tmp_path):
    generator = load_visual_generator()
    a = tmp_path / 'a'
    b = tmp_path / 'b'
    first = generator.generate(a)
    second = generator.generate(b)
    assert first == second
    png = a / 'transition-chain-contact-sheet.png'
    with Image.open(png) as sheet:
        assert sheet.size == (1440, 471)
        assert sheet.mode == 'RGB'
        assert sheet.getbbox() is not None
    assert hashlib.sha256(png.read_bytes()).hexdigest() == first['contact_sheet_sha256']
    assert len(set(first['frames'].values())) >= 12
    assert len(first['frames']) == 18
    assert (a / 'transition-chain-manifest.json').read_bytes() == (
        b / 'transition-chain-manifest.json').read_bytes()
