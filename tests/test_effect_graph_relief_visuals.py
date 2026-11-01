"""QA is output from the implementation, not a stand-in reference illustration."""
import importlib.util
from pathlib import Path


def test_relief_contact_sheet_is_reproducible(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'integration' / 'build_effect_graph_m6_3_visuals.py'
    spec = importlib.util.spec_from_file_location('relief_qa', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    a = module.build(tmp_path / 'first')
    b = module.build(tmp_path / 'second')
    assert a == b
    assert len(a['cases']) == 12
    assert len({entry['rgba_sha256'] for entry in a['cases']}) == 12
