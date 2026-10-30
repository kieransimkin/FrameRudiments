"""Reproducible M5.2 contact-sheet evidence with manifest of raw-frame hashes."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'spectral_transition_qa', ROOT / 'integration' / 'build_effect_graph_m5_2_spectral_visuals.py'
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_contact_sheet_is_deterministic(tmp_path):
    first = MODULE.make(tmp_path / 'a')
    second = MODULE.make(tmp_path / 'b')
    assert first == second
    assert len(first['frames']) == 12
    assert (tmp_path / 'a' / 'spectral-transition-contact-sheet.png').read_bytes() == (
        tmp_path / 'b' / 'spectral-transition-contact-sheet.png').read_bytes()
    assert first['frames'][0]['sha256'] == first['frames'][1]['sha256']
    assert first['frames'][-2]['sha256'] == first['frames'][-1]['sha256']
    assert first['frames'][6]['progress'] == .6
