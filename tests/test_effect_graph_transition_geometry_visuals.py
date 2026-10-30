"""M5.3 deterministic visual-evidence generator smoke and reproducibility."""
import hashlib
import importlib.util
from pathlib import Path


def load_builder():
    path = Path(__file__).resolve().parents[1] / 'integration' / 'build_effect_graph_m5_3_visuals.py'
    spec = importlib.util.spec_from_file_location('geometry_m53_visuals', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build


def test_contact_sheet_contains_all_styles_and_is_reproducible(tmp_path):
    build = load_builder()
    first = build(tmp_path / 'first')
    second = build(tmp_path / 'second')
    assert first == second
    assert len(first['frame_sha256']) == 30
    assert len(set(first['frame_sha256'].values())) > 20
    png = tmp_path / 'first' / 'geometric-transition-contact-sheet.png'
    assert hashlib.sha256(png.read_bytes()).hexdigest() == first['contact_sheet_sha256']
