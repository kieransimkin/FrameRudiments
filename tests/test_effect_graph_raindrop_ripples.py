"""Shared refractive raindrop layer and wash-transition regressions."""
from copy import deepcopy

import numpy as np
from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_spatial import render_spatial_warp
from framerudiments.effect_graph_transition_geometry import geometric_transition


VARIANTS = ('rings', 'droplets', 'storm')


def surface(size=(73, 49)):
    image = Image.new('RGBA', size)
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], 6):
        draw.rectangle((x, 0, min(x + 2, size[0] - 1), size[1] - 1),
                       fill=(25 + x * 2 % 220, 80, 230, 220))
    draw.ellipse((8, 6, size[0] - 9, size[1] - 7), outline=(255, 210, 30, 255), width=3)
    return image


@pytest.mark.parametrize('variant', VARIANTS)
def test_layer_variants_are_deterministic_distinct_and_tile_stable(variant):
    source = surface()
    config = {
        'warp_type': 'raindrop_ripples', 'warp_ripple_variant': variant,
        'warp_amplitude': [11, 9], 'warp_scale': [12, 12],
        'warp_radius': 31, 'warp_phase': 2.2, 'warp_segments': 7, 'warp_seed': 41,
        'warp_border': 'clamp',
    }
    from framerudiments.effect_graph_spatial import render_spatial_warp_with_alpha
    a, lineage_a = render_spatial_warp_with_alpha(
        source, config, original_alpha=source.getchannel('A'), tile_rows=1,
    )
    b, lineage_b = render_spatial_warp_with_alpha(
        source, config, original_alpha=source.getchannel('A'), tile_rows=23,
    )
    assert a.tobytes() == b.tobytes()
    assert lineage_a.tobytes() == lineage_b.tobytes()
    assert a.tobytes() != source.tobytes()
    assert np.all(np.asarray(a)[..., :3][np.asarray(a)[..., 3] == 0] == 0)


def test_three_layer_variants_have_different_pixels_and_seed_is_local():
    source = surface()
    base = {'warp_type': 'raindrop_ripples', 'warp_amplitude': [13, 9],
            'warp_scale': [10, 10], 'warp_radius': 28, 'warp_phase': 1.7,
            'warp_segments': 8, 'warp_seed': 9, 'warp_border': 'clamp'}
    before = np.random.get_state()
    images = [render_spatial_warp(source, {**base, 'warp_ripple_variant': v}) for v in VARIANTS]
    after = np.random.get_state()
    assert len({im.tobytes() for im in images}) == 3
    assert render_spatial_warp(source, {**base, 'warp_ripple_variant': 'storm',
                                        'warp_seed': 10}).tobytes() != images[-1].tobytes()
    assert before[0] == after[0] and np.array_equal(before[1], after[1]) and before[2:] == after[2:]


@pytest.mark.parametrize('variant', VARIANTS)
def test_layer_exact_zero_strength_and_zero_mix(variant):
    source = surface()
    config = {'warp_type': 'raindrop_ripples', 'warp_ripple_variant': variant,
              'warp_amplitude': [0, 0]}
    assert render_spatial_warp(source, config).tobytes() == source.tobytes()
    config = {**config, 'warp_amplitude': [20, 20], 'warp_mix': 0}
    assert render_spatial_warp(source, config).tobytes() == source.tobytes()


@pytest.mark.parametrize('variant', VARIANTS)
def test_wash_exact_endpoints_midpoint_and_tiles(variant):
    outgoing = surface()
    incoming = Image.new('RGBA', outgoing.size, (20, 190, 110, 235))
    config = {'transition_style': 'wash', 'wash_variant': variant, 'wash_seed': 17,
              'segments': 8, 'frequency': 7, 'amplitude': 18, 'strength': .8,
              'phase': .4, 'feather': .1}
    assert geometric_transition(outgoing, incoming, {**config, 'progress': 0}).tobytes() == outgoing.tobytes()
    assert geometric_transition(outgoing, incoming, {**config, 'progress': 1}).tobytes() == incoming.tobytes()
    a = geometric_transition(outgoing, incoming, {**config, 'progress': .48}, tile_rows=1)
    b = geometric_transition(outgoing, incoming, {**config, 'progress': .48}, tile_rows=19)
    assert a.tobytes() == b.tobytes()
    assert a.tobytes() not in {outgoing.tobytes(), incoming.tobytes()}


def test_wash_variants_and_seed_produce_different_transitions():
    a, b = surface(), Image.new('RGBA', (73, 49), (8, 210, 155, 255))
    base = {'transition_style': 'wash', 'progress': .52, 'segments': 9,
            'frequency': 6, 'amplitude': 16, 'strength': 1, 'feather': .08}
    images = [geometric_transition(a, b, {**base, 'wash_variant': v, 'wash_seed': 5})
              for v in VARIANTS]
    assert len({im.tobytes() for im in images}) == 3
    assert geometric_transition(a, b, {**base, 'wash_variant': 'storm',
                                        'wash_seed': 6}).tobytes() != images[-1].tobytes()


def test_graph_controls_are_reversible_and_do_not_mutate_config():
    project = EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'wash', 'type': 'transition', 'inputs': ['a', 'b'],
         'transition_style': 'wash', 'wash_variant': 'rings', 'wash_seed': 4,
         'progress': .45, 'segments': 6, 'frequency': 5, 'amplitude': 14},
    ], 'output': 'wash'})
    providers = {'a': surface(), 'b': Image.new('RGBA', (73, 49), (40, 180, 230, 255))}
    saved = deepcopy(project.nodes['wash'].config)
    baseline = project.render(providers)
    altered = project.render(providers, parameter_values={
        ('wash', 'wash_variant'): 'storm', ('wash', 'wash_seed'): 19,
    })
    assert altered.tobytes() != baseline.tobytes()
    assert project.nodes['wash'].config == saved
    assert project.base_control_value('wash', 'wash_variant') == 'rings'
    assert project.base_control_value('wash', 'wash_seed') == 4


@pytest.mark.parametrize('bad', [None, 'mist', 1, []])
def test_invalid_variants_are_rejected(bad):
    with pytest.raises(ValueError):
        EffectGraph({'nodes': [
            {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
            {'id': 'wash', 'type': 'transition', 'inputs': ['a', 'b'],
             'transition_style': 'wash', 'wash_variant': bad},
        ], 'output': 'wash'})
    with pytest.raises(ValueError):
        EffectGraph({'nodes': [
            {'id': 'a', 'type': 'source'},
            {'id': 'rain', 'type': 'spatial_warp', 'inputs': ['a'],
             'warp_type': 'raindrop_ripples', 'warp_ripple_variant': bad},
        ], 'output': 'rain'})


@pytest.mark.parametrize('bad', [-1, 1.2, 3.5, True, float('nan')])
def test_invalid_wash_seed_is_rejected(bad):
    with pytest.raises(ValueError):
        EffectGraph({'nodes': [
            {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
            {'id': 'wash', 'type': 'transition', 'inputs': ['a', 'b'],
             'transition_style': 'wash', 'wash_seed': bad},
        ], 'output': 'wash'})


def test_wash_only_properties_do_not_silently_apply_elsewhere():
    with pytest.raises(ValueError, match='wash transition'):
        EffectGraph({'nodes': [
            {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
            {'id': 'fade', 'type': 'transition', 'inputs': ['a', 'b'],
             'transition_style': 'dissolve', 'wash_variant': 'storm'},
        ], 'output': 'fade'})
    with pytest.raises(ValueError, match='wash transition'):
        EffectGraph({'nodes': [
            {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
            {'id': 'zoom', 'type': 'transition', 'inputs': ['a', 'b'],
             'transition_style': 'zoom', 'wash_seed': 4},
        ], 'output': 'zoom'})
