"""Real effect-graph composition contracts for M7.5 (no native-driver stubs)."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_fields import FIELD_TYPES
from framerudiments.effect_graph_gpu import GPUUnsupported


def graph(config=None, extra=(), output='f'):
    return EffectGraph({'nodes': [dict(id='s', type='source'),
        dict(id='f', type='procedural_field', inputs=['s'], **(config or {})), *extra], 'output': output})


def source():
    y, x = np.indices((31, 43))
    a = np.stack([(x*31+y)%256, (x+y*13)%256, (x*7+y)%256, (x*13+y*5)%256], -1).astype(np.uint8)
    return Image.fromarray(a)


@pytest.mark.parametrize('kind', sorted(FIELD_TYPES))
def test_graph_does_not_register_generated_ids_as_external_sources(kind):
    g = graph(dict(field_type=kind))
    assert g.source_ids == {'s'}
    assert g.render({'s': source()}).size == (43, 31)
    assert g.field_manifest()['f']['field_type'] == kind


def test_graph_control_roundtrip_and_seek_order_do_not_mutate_authored_fields():
    g = graph(dict(field_type='fbm', field_scale=[9, 11]))
    saved = deepcopy(g.nodes['f'].config)
    phases = [0, .17, 1.7, -.2, 4]
    renders = [g.render({'s': source()}, parameter_values={('f', 'field_phase'): p}).tobytes() for p in phases]
    assert len(set(renders)) == len(phases)
    for p, expected in reversed(list(zip(phases, renders))):
        assert g.render({'s': source()}, parameter_values={('f', 'field_phase'): p}).tobytes() == expected
    assert g.nodes['f'].config == saved


def test_controls_include_palette_categories_and_universal_opacity():
    g = graph(dict(field_type='constant', field_value=.5, field_output='color'))
    g.validate_control_choices('f', 'field_type', ['constant', 'fbm'])
    g.validate_control_choices('f', 'field_invert', [False, True])
    assert g.is_categorical_control('f', 'field_invert')
    assert not g.is_categorical_control('f', 'field_palette.start.color.r')
    assert g.base_control_value('f', 'field_palette.start.color.r') == 0
    assert 'field_palette.start.color.r' in g.control_descriptors('f')
    out = g.render({'s': source()}, parameter_values={('f', 'field_palette.start.color.r'): 255,
                                                     ('f', 'opacity'): .5})
    assert out.getpixel((0, 0)) == (255, 128, 128, 128)


def test_joint_stop_controls_are_validated_after_all_updates():
    cfg = dict(field_output='color', field_palette=[dict(id='a', position=.1, color='red'),
                                                   dict(id='b', position=.2, color='blue')])
    g = graph(cfg)
    for values in [dict([( ('f', 'field_palette.a.position'), .7), (('f', 'field_palette.b.position'), .8)]),
                   dict([( ('f', 'field_palette.b.position'), .8), (('f', 'field_palette.a.position'), .7)])]:
        assert g.render({'s': source()}, parameter_values=values).size == (43, 31)
    with pytest.raises(ValueError, match='strictly increasing'):
        g.render({'s': source()}, parameter_values={('f', 'field_palette.a.position'): .7})


@pytest.mark.parametrize('output,channel', [('alpha', 'alpha'), ('grayscale', 'luminance')])
def test_generated_masks_feed_existing_matte(output, channel):
    g = graph(dict(field_type='constant', field_value=.4, field_output=output),
              [dict(id='m', type='matte', inputs=['s', 'f'], matte_channel=channel)], 'm')
    im = Image.new('RGBA', (7, 9), (17, 33, 55, 200))
    out = g.render({'s': im})
    assert out.getpixel((0, 0))[3] == 80


def test_generated_mask_feeds_transition_and_exact_endpoints():
    extra = [dict(id='b', type='source'), dict(id='t', type='transition', inputs=['s', 'b', 'f'],
             transition_style='matte', matte_channel='luminance', progress=.5)]
    g = graph(dict(field_type='linear', field_scale=[43, 31]), extra, 't')
    inputs = {'s': Image.new('RGBA', (43, 31), 'red'), 'b': Image.new('RGBA', (43, 31), 'blue')}
    for p, key in [(0, 's'), (1, 'b')]:
        assert g.render(inputs, parameter_values={('t', 'progress'): p}).tobytes() == inputs[key].tobytes()
    out = g.render(inputs)
    assert out.getpixel((0, 0)) != out.getpixel((42, 0))


def test_generated_texture_feeds_existing_pattern_overlay():
    g = graph(dict(field_type='checker', field_scale=[7, 9]), [
        dict(id='p', type='layer_style', style_type='pattern_overlay', pattern_kind='texture', inputs=['s', 'f'])
    ], 'p')
    out = g.render({'s': source()})
    assert out.getchannel('A').tobytes() == source().getchannel('A').tobytes()
    assert out.tobytes() != source().tobytes()


def test_neutral_field_feeds_spatial_displacement_with_exact_visible_identity():
    g = graph(dict(field_type='constant', field_output='vector'), [
        dict(id='w', type='spatial_warp', inputs=['s', 'f'], warp_type='displacement', warp_amplitude=[100, 100])
    ], 'w')
    im = source()
    out = np.asarray(g.render({'s': im}))
    expected = np.asarray(im).copy()
    expected[expected[..., 3] == 0, :3] = 0
    assert np.array_equal(out, expected)


def test_transparent_field_has_no_displacement_influence():
    g = graph(dict(field_type='constant', field_value=1, field_vector='independent', field_output='vector', opacity=0), [
        dict(id='w', type='spatial_warp', inputs=['s', 'f'], warp_type='displacement')
    ], 'w')
    im = source()
    assert g.render({'s': im}).tobytes() == im.tobytes()


def test_generator_resets_alpha_lineage_to_its_own_new_surface():
    g = graph(dict(field_type='constant', field_value=.4, field_output='alpha'), [
        dict(id='fade', type='opacity', inputs=['f'], opacity=0),
        dict(id='paint', type='layer_style', inputs=['fade'], style_type='color_overlay',
             alpha_source='original', style_output='effect', style_color='#ff0000'),
    ], 'paint')
    out = g.render({'s': Image.new('RGBA', (7, 9))})
    assert out.getpixel((0, 0)) == (255, 0, 0, 102)


def test_original_reference_coverage_survives_faded_branch_but_output_lineage_is_new():
    g = EffectGraph({'nodes': [dict(id='s', type='source'), dict(id='fade', type='opacity', inputs=['s'], opacity=0),
        dict(id='f', type='procedural_field', inputs=['fade'], field_type='constant', field_output='alpha',
             field_value=.5, field_alpha_source='original')], 'output':'f'})
    assert g.render({'s': Image.new('RGBA', (7, 9), (0, 0, 0, 200))}).getpixel((0, 0))[3] == 100


def test_chain_preprocessing_reuses_generator_and_stable_control_ids():
    spec = {'nodes': [dict(id='s', type='source'), dict(id='chain', type='transition_chain', inputs=['s', 's'],
        input_effects=[[dict(id='noise', type='procedural_field', field_type='noise')], []],
        stages=[dict(id='fade', progress=0)])], 'output':'chain'}
    before = deepcopy(spec)
    g = EffectGraph(spec)
    target = 'chain::input::0::noise'
    assert target in g.field_manifest()
    a = g.render({'s': source()}, parameter_values={(target, 'field_phase'): .2})
    b = g.render({'s': source()}, parameter_values={(target, 'field_phase'): 1.4})
    assert a.tobytes() != b.tobytes() and spec == before


def test_forks_are_independent():
    g = graph(dict(field_type='noise'), [dict(id='copy', type='copy', inputs=['s']),
        dict(id='c', type='composite', inputs=['f', 'copy'])], 'c')
    im = Image.new('RGBA', (7, 9), (19, 33, 51, 255))
    assert g.render({'s':im}).tobytes() == im.tobytes()


def test_strict_gpu_rejects_and_auto_records_cpu_without_claiming_gpu_work():
    # Only dispatch counters are needed; this is not an emulated GPU or motion driver.
    backend = SimpleNamespace(last_dispatch={})
    g = graph()
    with pytest.raises(GPUUnsupported, match='procedural_field'):
        g.render({'s': source()}, gpu_backend=backend, gpu_strict=True)
    out = g.render({'s': source()}, gpu_backend=backend)
    assert out.size == (43, 31)
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}


@pytest.mark.parametrize('target', ['field_palette.missing.color.r', 'field_scale.z', 'madeup'])
def test_invalid_control_targets_rejected(target):
    with pytest.raises(ValueError):
        graph().validate_control_target('f', target)


def test_field_parameters_cannot_leak_to_other_node_types():
    with pytest.raises(ValueError, match='field properties'):
        EffectGraph({'nodes':[dict(id='s',type='source',field_seed=17)], 'output':'s'})


def test_manifest_is_detached_and_ignores_unreachable_generators():
    g = graph(extra=[dict(id='unused', type='procedural_field', inputs=['s'])])
    m = g.field_manifest()
    assert set(m) == {'f'}
    m['f']['controls'].clear()
    assert g.field_manifest()['f']['controls']


def test_documented_fragment_validates_and_renders_with_resolved_controls():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'examples/effect-graph-procedural-fields.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    g = EffectGraph(data['effect_graph'])
    for row in data['effect_graph_controls']:
        g.validate_control_target(row['node'], row['property'])
        assert row['property'] in g.control_descriptors(row['node'])
    assert g.render({'frame': source()}, parameter_values={('flow', 'field_phase'): .7}).size == (43, 31)
