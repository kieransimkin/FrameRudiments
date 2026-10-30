"""M5.3 geometric transition graph contract, alpha parity and controls."""
from __future__ import annotations

import hashlib

import pytest
from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_transition_geometry import (
    GEOMETRIC_STYLES, geometric_transition,
)


def providers(size=(31, 21)):
    a = Image.new('RGBA', size, (0, 255, 0, 0))  # hidden RGB must not appear
    b = Image.new('RGBA', size)
    draw = ImageDraw.Draw(a)
    draw.ellipse((2, 2, size[0] - 3, size[1] - 3), fill=(244, 80, 12, 210))
    draw = ImageDraw.Draw(b)
    draw.rectangle((2, 2, size[0] - 3, size[1] - 3), fill=(30, 95, 235, 240))
    return {'a': a, 'b': b}


def graph(style='zoom', **settings):
    return EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 't', 'type': 'transition', 'inputs': ['a', 'b'],
         'transition_style': style, **settings},
    ], 'output': 't'})


@pytest.mark.parametrize('style', sorted(GEOMETRIC_STYLES))
def test_exact_endpoints_and_source_immutability(style):
    images = providers()
    snapshots = {key: value.tobytes() for key, value in images.items()}
    g = graph(style, progress=0)
    assert g.render(images).tobytes() == snapshots['a']
    assert g.render(images, parameter_values={('t', 'progress'): 1}).tobytes() == snapshots['b']
    assert {key: value.tobytes() for key, value in images.items()} == snapshots


@pytest.mark.parametrize('style', sorted(GEOMETRIC_STYLES))
def test_midpoint_is_distinct_from_both_endpoints_and_repeatable(style):
    g = graph(style, progress=.5, feather=.15)
    images = providers((47, 35))
    mid = g.render(images)
    assert mid.tobytes() not in {images['a'].tobytes(), images['b'].tobytes()}
    assert mid.tobytes() == g.render(images).tobytes()
    assert g.render(images, parameter_values={('t', 'progress'): .2}).tobytes() != mid.tobytes()
    assert g.render(images).tobytes() == mid.tobytes()


@pytest.mark.parametrize('style', sorted(GEOMETRIC_STYLES))
def test_contiguous_random_tiles_and_bounded_working_set(style):
    images = providers((41, 33))
    config = dict(transition_style=style, progress=.43, feather=.1)
    reference = geometric_transition(images['a'], images['b'], config, tile_rows=33)
    for tile_rows in (1, 5, 16):
        assert reference.tobytes() == geometric_transition(
            images['a'], images['b'], config, tile_rows=tile_rows).tobytes()


@pytest.mark.parametrize('style', sorted(GEOMETRIC_STYLES))
def test_hidden_transparent_rgb_is_not_visible(style):
    a = Image.new('RGBA', (29, 25), (0, 255, 0, 0))
    b = Image.new('RGBA', a.size, (180, 25, 220, 255))
    image = geometric_transition(a, b, {'transition_style': style, 'progress': .65,
                                        'feather': .5, 'strength': 0})
    import numpy as np
    for r, g, blue, alpha in np.asarray(image).reshape(-1, 4):
        if alpha:
            assert abs(r - 180) <= 1
            assert abs(g - 25) <= 1
            assert abs(blue - 220) <= 1


@pytest.mark.parametrize('style', sorted(GEOMETRIC_STYLES))
def test_native_control_override_reversible_and_nonmutating(style):
    g = graph(style, progress=.4, feather=.2)
    images = providers()
    reference = g.render(images).tobytes()
    controls = {('t', 'strength'): .2, ('t', 'progress'): .65,
                ('t', 'phase'): 2.1, ('t', 'center.x'): .35}
    altered = g.render(images, parameter_values=controls)
    assert altered.tobytes() != reference
    assert g.render(images).tobytes() == reference
    assert g.nodes['t'].config['progress'] == .4
    assert g.base_control_value('t', 'strength') == .75


@pytest.mark.parametrize('style', sorted(GEOMETRIC_STYLES))
def test_separate_branches_can_transition_then_merge(style):
    g = EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'dull', 'type': 'opacity', 'inputs': ['a'], 'opacity': .4},
        {'id': 'geom', 'type': 'transition', 'inputs': ['dull', 'b'],
         'transition_style': style, 'progress': .5, 'feather': .2},
        {'id': 'out', 'type': 'composite', 'inputs': ['geom', 'a'], 'mode': 'over'},
    ], 'output': 'out'})
    src = providers()
    before = {key: im.tobytes() for key, im in src.items()}
    assert g.render(src).size == src['a'].size
    assert g.render(src).tobytes() == g.render(src).tobytes()
    assert {key: im.tobytes() for key, im in src.items()} == before


@pytest.mark.parametrize('style', sorted(GEOMETRIC_STYLES))
def test_only_geometric_parameters_apply_to_geometric_styles(style):
    g = graph(style)
    g.validate_control_target('t', 'rotation')
    g.validate_control_target('t', 'twist')
    g.validate_control_target('t', 'frequency')
    g.validate_control_target('t', 'segments')
    with pytest.raises(ValueError, match='geometric control'):
        graph('wipe').validate_control_target('t', 'phase')


@pytest.mark.parametrize('param,invalid', [
    ('strength', -1), ('strength', 2), ('zoom', -1), ('zoom', 5),
    ('rotation', float('nan')), ('twist', -5), ('segments', 1),
    ('segments', 24.5), ('segments', True), ('frequency', 33),
    ('amplitude', 257), ('phase', 1000), ('progress', 2),
])
def test_invalid_geometry_parameters_fail_at_project_preparation(param, invalid):
    with pytest.raises(ValueError):
        graph('liquid', **{param: invalid})


def test_geometry_parameters_do_not_silently_apply_to_normal_dissolve():
    with pytest.raises(ValueError, match='geometric'):
        graph('dissolve', amplitude=20)


def test_gpu_strict_rejects_unaccelerated_geometric_effect():
    from framerudiments.effect_graph_gpu import GPUUnsupported

    class Backend:
        last_dispatch = None

    images = providers()
    with pytest.raises(GPUUnsupported, match='geometric'):
        graph('liquid', progress=.5).render(images, gpu_backend=Backend(), gpu_strict=True)
    backend = Backend()
    result = graph('liquid', progress=.5).render(images, gpu_backend=backend)
    assert result.mode == 'RGBA'
    assert backend.last_dispatch['cpu_fallbacks'] == 1


def test_geometric_style_choices_preserve_arity_and_settings():
    g = graph('zoom', zoom=1.2)
    g.validate_control_choices('t', 'transition_style', ['zoom', 'rotate', 'polar'])
    with pytest.raises(ValueError, match='geometric'):
        g.validate_control_choices('t', 'transition_style', ['zoom', 'dissolve'])
    with pytest.raises(ValueError, match='input count'):
        g.validate_control_choices('t', 'transition_style', ['zoom', 'matte'])


def test_segments_float_control_rounded_deterministically():
    g = graph('kaleidoscope', progress=.42)
    result = g.render(providers(), parameter_values={('t', 'segments'): 8.7})
    assert result.tobytes() == g.render(providers(), parameter_values={('t', 'segments'): 9}).tobytes()
    assert g.nodes['t'].config.get('segments') is None


def test_style_selection_can_switch_between_geometric_styles():
    g = graph('zoom', progress=.47, strength=1, zoom=2, feather=.2)
    a = g.render(providers(), parameter_values={('t', 'transition_style'): 'rotate'})
    b = g.render(providers(), parameter_values={('t', 'transition_style'): 'zoom'})
    assert a.tobytes() != b.tobytes()


def test_fractional_alpha_and_directions_affect_output():
    images = providers((35, 25))
    left = graph('liquid', progress=.42, direction='left', feather=.15).render(images)
    right = graph('liquid', progress=.42, direction='right', feather=.15).render(images)
    assert left.tobytes() != right.tobytes()
    assert 0 < max(left.getchannel('A').getextrema()) <= 255


def test_pixel_hash_is_stable_for_identical_inputs():
    image = graph('polar', progress=.456, twist=1.12, feather=.05).render(providers())
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    assert digest == hashlib.sha256(graph('polar', progress=.456, twist=1.12,
                                         feather=.05).render(providers()).tobytes()).hexdigest()
