"""M7.4 real DAG integration, controls, forks, mattes and GPU declarations."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_raster import RASTER_TYPES, render_raster_with_alpha


def surface():
    y, x = np.indices((16, 24))
    a = np.stack([(x * 31 + y * 13) % 256, (x * 7 + y * 43) % 256,
                  (y * 17 + 127) % 256, (x * 29 + y * 31) % 256], axis=-1).astype(np.uint8)
    a[4:8, 7:12, 3] = 0
    return Image.fromarray(a)


def graph(config=None, *, extra=(), output='r'):
    return EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
        {'id': 'r', 'type': 'raster_effect', 'inputs': ['s'], **(config or {})}, *extra], 'output': output})


@pytest.mark.parametrize('kind', sorted(RASTER_TYPES))
def test_graph_is_kernel_plus_exactly_one_output_opacity(kind):
    im = surface()
    cfg = {'raster_type': kind, 'raster_mix': .6, 'opacity': .4}
    direct, _ = render_raster_with_alpha(im, cfg)
    direct.putalpha(direct.getchannel('A').point(lambda a: round(a * .4)))
    assert graph(cfg).render({'s': im}).tobytes() == direct.tobytes()


@pytest.mark.parametrize('kind', sorted(RASTER_TYPES))
def test_frame_local_controls_and_config_immutability(kind):
    cfg = {'raster_type': kind, 'raster_mix': .5}
    g, im = graph(cfg), surface()
    saved = deepcopy(g.nodes['r'].config)
    a = g.render({'s': im}, parameter_values={('r', 'raster_mix'): 0})
    assert a.tobytes() == im.tobytes()
    b = g.render({'s': im}, parameter_values={('r', 'raster_mix'): 1})
    assert b.tobytes() != a.tobytes()
    assert g.nodes['r'].config == saved
    assert g.base_control_value('r', 'raster_mix') == .5
    assert g.render({'s': im}, parameter_values={('r', 'raster_mix'): 0}).tobytes() == a.tobytes()


@pytest.mark.parametrize('kind', sorted(RASTER_TYPES))
def test_chains_expand_raster_input_preprocessing(kind):
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
        {'id': 'c', 'type': 'transition_chain', 'inputs': ['s', 's'],
         'input_effects': [[{'id': 'fx', 'type': 'raster_effect', 'raster_type': kind}], []],
         'stages': [{'id': 'first', 'progress': 0}]}], 'output': 'c'})
    a = g.render({'s': surface()})
    assert a.tobytes() == graph({'raster_type': kind}).render({'s': surface()}).tobytes()
    name = 'c::input::0::fx'
    assert g.control_descriptors(name)['raster_type']['value'] == kind
    assert name in g.raster_manifest()


@pytest.mark.parametrize('kind', sorted(RASTER_TYPES))
def test_strict_gpu_rejects_auto_counts_cpu_fallback(kind):
    g = graph({'raster_type': kind})
    backend = SimpleNamespace()
    with pytest.raises(GPUUnsupported, match='raster_effect'):
        g.render({'s': surface()}, gpu_backend=backend, gpu_strict=True)
    out = g.render({'s': surface()}, gpu_backend=backend)
    assert out.tobytes() == g.render({'s': surface()}).tobytes()
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}


@pytest.mark.parametrize('kind', sorted(RASTER_TYPES))
def test_original_alpha_after_raster_matches_direct_transport(kind):
    im = surface()
    cfg = {'raster_type': kind, 'pixel_alpha': 'average', 'glitch_density': 1}
    dim = im.copy()
    dim.putalpha(im.getchannel('A').point(lambda a: round(a * .25)))
    _, transported = render_raster_with_alpha(dim, cfg, original_alpha=im.getchannel('A'))
    g = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'}, {'id': 'white', 'type': 'source'},
        {'id': 'dim', 'type': 'copy', 'inputs': ['s'], 'opacity': .25},
        {'id': 'r', 'type': 'raster_effect', 'inputs': ['dim'], **cfg},
        {'id': 'out', 'type': 'matte', 'inputs': ['r', 'white'], 'alpha_source': 'original'},
    ], 'output': 'out'})
    actual = g.render({'s': im, 'white': Image.new('RGBA', im.size, 'white')})
    assert actual.getchannel('A').tobytes() == transported.tobytes()


def test_fork_merge_and_matte_key_placement_do_not_modify_sources():
    im = surface()
    mask = Image.new('RGBA', im.size, (100, 100, 100, 128))
    g = graph({'raster_type': 'glitch', 'glitch_density': 1}, extra=[
        {'id': 'm', 'type': 'source'},
        {'id': 'a', 'type': 'matte', 'inputs': ['r', 'm']},
        {'id': 'b', 'type': 'chroma_key', 'inputs': ['s'], 'key_color': '#00ff00'},
        {'id': 'out', 'type': 'composite', 'inputs': ['a', 'b'], 'mode': 'screen'},
    ], output='out')
    before = im.tobytes(), mask.tobytes()
    a = g.render({'s': im, 'm': mask})
    g.render({'s': im, 'm': mask}, parameter_values={('r', 'raster_phase'): 17})
    assert a.tobytes() == g.render({'s': im, 'm': mask}).tobytes()
    assert before == (im.tobytes(), mask.tobytes())


def test_discovery_and_runtime_agree_on_all_control_paths():
    g = graph()
    for path, descriptor in g.control_descriptors('r').items():
        g.validate_control_target('r', path)
        value = g.base_control_value('r', path)
        assert value == descriptor['value']
        if descriptor['type'] == 'enum':
            assert g.is_categorical_control('r', path)
            g.validate_control_choices('r', path, descriptor['choices'])
        else:
            assert not g.is_categorical_control('r', path)
        g.render({'s': surface()}, parameter_values={('r', path): value})
    manifest = g.raster_manifest()
    assert manifest['r']['gpu_native'] is False and manifest['r']['input_count'] == 1
    manifest['r']['controls'].clear()
    assert g.raster_manifest()['r']['controls']


@pytest.mark.parametrize('inputs', [[], ['s', 's']])
def test_bad_topology(inputs):
    with pytest.raises(ValueError, match='one input'):
        EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
            {'id': 'r', 'type': 'raster_effect', 'inputs': inputs}], 'output': 'r'})


def test_wrong_node_fields_and_invalid_overrides_fail_closed():
    with pytest.raises(ValueError, match='raster properties'):
        EffectGraph({'nodes': [{'id': 's', 'type': 'source', 'scan_strength': .5}], 'output': 's'})
    g = graph({'raster_type': 'pixel_sort'})
    with pytest.raises(ValueError, match='sort_low'):
        g.render({'s': surface()}, parameter_values={('r', 'sort_low'): .9, ('r', 'sort_high'): .2})
    # Complete per-frame overrides are validated together, not in insertion order.
    g.render({'s': surface()}, parameter_values={('r', 'sort_min_run'): 300, ('r', 'sort_span'): 500})
    with pytest.raises(ValueError):
        g.render({'s': surface()}, parameter_values={('r', 'crt_noise'): -1})


def test_style_after_raster_and_raster_after_spatial_are_supported():
    g = graph({'raster_type': 'scanlines'}, extra=[
        {'id': 'out', 'type': 'layer_style', 'inputs': ['r'], 'style_type': 'outline',
         'style_width': 2, 'alpha_source': 'original'},
    ], output='out')
    assert g.render({'s': surface()}).size == surface().size
    g = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'},
        {'id': 'w', 'type': 'spatial_warp', 'inputs': ['s'], 'warp_type': 'wave'},
        {'id': 'r', 'type': 'raster_effect', 'inputs': ['w'], 'raster_type': 'dither'},
    ], 'output': 'r'})
    assert g.render({'s': surface()}).mode == 'RGBA'
