"""Reproducible actual raster evidence and executable documentation."""
import importlib.util
import json
from pathlib import Path

from framerudiments.effect_graph import EffectGraph


def test_visual_generator_is_pixel_reproducible(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('spatial_qa', root / 'integration/build_effect_graph_m7_spatial_visuals.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    a, b = module.build(tmp_path / 'a'), module.build(tmp_path / 'b')
    assert a == b and len(a['cases']) == 10
    assert len({x['rgba_sha256'] for x in a['cases']}) == 10
    assert (tmp_path / 'a/spatial-contact-sheet.png').read_bytes() == (tmp_path / 'b/spatial-contact-sheet.png').read_bytes()


def test_documented_graph_and_bindings_validate():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'examples/effect-graph-m7-spatial.json').read_text())
    graph = EffectGraph(config['effect_graph'])
    for row in config['effect_graph_controls']:
        graph.validate_control_target(row['node'], row['property'])
    assert 'wave' in graph.spatial_manifest()
    assert 'warp_phase' in graph.control_descriptors('wave')
