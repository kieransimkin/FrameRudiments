"""M5.2 two-input spectral transition validation, controls, alpha and graph routing."""
import copy

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_transition_spectral import spectral_transition


def band(name='warm', **kw):
    return {'id': name, 'source_color': '#ff0000', 'to_color': '#0000ff',
            'space': 'rgb', 'tolerance': .06, 'softness': 0, **kw}


def graph(*bands, **settings):
    return EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 't', 'type': 'transition', 'inputs': ['a', 'b'], 'progress': .5,
         'transition_style': 'spectral', 'bands': list(bands or [band()]), **settings},
    ], 'output': 't'})


def sources():
    a = Image.new('RGBA', (4, 2), (255, 0, 0, 255))
    b = Image.new('RGBA', (4, 2), (0, 0, 255, 255))
    a.putpixel((3, 0), (0, 255, 0, 255))
    b.putpixel((3, 0), (0, 255, 0, 255))
    return {'a': a, 'b': b}


def rgba(*rows):
    height = len(rows)
    width = len(rows[0])
    out = Image.new('RGBA', (width, height))
    for y, row in enumerate(rows):
        for x, pixel in enumerate(row):
            out.putpixel((x, y), pixel)
    return out


def test_exact_endpoints_and_inactive_source_bytes_unchanged():
    g = graph(band(offset_from=[10, 3], offset_to=[-10, 2], recolor=1,
                   target_color='#00ff00', echoes=3, echo_decay=.9))
    images = sources()
    hashes = {k: im.tobytes() for k, im in images.items()}
    assert g.render(images, parameter_values={('t', 'progress'): 0}).tobytes() == hashes['a']
    assert g.render(images, parameter_values={('t', 'progress'): 1}).tobytes() == hashes['b']
    assert all(im.tobytes() == hashes[k] for k, im in images.items())


def test_colour_bands_reveal_on_independent_windows_and_unselected_dissolve():
    images = sources()
    g = graph(band('early', start=0, end=.5))
    mid = g.render(images)
    # Selected red/blue region completes sooner than the unselected green pixel.
    assert mid.getpixel((0, 0)) == (0, 0, 255, 255)
    assert mid.getpixel((3, 0)) == (0, 255, 0, 255)
    late = graph(band('late', start=.5, end=1)).render(images)
    assert late.getpixel((0, 0)) == (255, 0, 0, 255)


def test_separate_color_matches_for_incoming_source():
    images = sources()
    incoming = Image.new('RGBA', images['b'].size, (25, 40, 220, 255))
    images['b'] = incoming
    g = graph(band('to_only', source_color='#00ff00', to_color='#1928dc',
                   mask_source='to', start=0, end=.6))
    result = g.render(images)
    assert result.getpixel((0, 0))[2] > 175  # earlier than the ordinary 50% dissolve


@pytest.mark.parametrize('space', ['rgb', 'hsv', 'oklab'])
def test_all_three_selection_spaces(space):
    g = graph(band(space=space, tolerance=.08, softness=.03, start=0, end=.5))
    result = g.render(sources())
    assert result.getpixel((0, 0)) == (0, 0, 255, 255)


@pytest.mark.parametrize('mode', ['from', 'to', 'intersection', 'union'])
def test_mask_source_modes_are_reproducible(mode):
    g = graph(band(mask_source=mode, offset_from=[.3, 0], offset_to=[-.7, 0]))
    images = sources()
    original = {k: im.tobytes() for k, im in images.items()}
    first = g.render(images)
    g.render(images, parameter_values={('t', 'progress'): .75})
    assert g.render(images).tobytes() == first.tobytes()
    assert {k: im.tobytes() for k, im in images.items()} == original


def test_multiple_bands_with_independent_progress():
    a = rgba([(255, 0, 0, 255), (0, 255, 0, 255)])
    b = rgba([(0, 0, 255, 255), (255, 0, 255, 255)])
    g = graph(band('warm', start=0, end=.5), {
        'id': 'green', 'source_color': '#00ff00', 'to_color': '#ff00ff',
        'space': 'rgb', 'softness': 0, 'tolerance': .05, 'start': .5, 'end': 1,
    })
    result = g.render({'a': a, 'b': b})
    assert result.getpixel((0, 0)) == (0, 0, 255, 255)
    assert result.getpixel((1, 0)) == (0, 255, 0, 255)


def test_transition_recolour_changes_incoming_midframe_but_not_final_source():
    g = graph(band(target_color='#00ff00', recolor=1, start=0, end=.8))
    mid = g.render(sources()).getpixel((0, 0))
    assert mid[1] > 0
    assert mid[2] < 128
    assert g.render(sources(), parameter_values={('t', 'progress'): 1}).getpixel((0, 0)) == (0, 0, 255, 255)


def test_fractional_2d_offsets_and_echo_controls_change_pixels():
    a = rgba([(255, 0, 0, 255), (0, 0, 0, 0), (0, 0, 0, 0)],
             [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)])
    b = rgba([(0, 0, 255, 255), (0, 0, 0, 0), (0, 0, 0, 0)],
             [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)])
    g = graph(band(offset_from=[1.5, 1], offset_to=[1, 1],
                   start=0, end=1, echo_decay=1, echoes=3))
    out = g.render({'a': a, 'b': b})
    assert out.getpixel((1, 1))[3] > 0
    no_echo = g.render({'a': a, 'b': b}, parameter_values={('t', 'bands.warm.echoes'): 0})
    assert out.tobytes() != no_echo.tobytes()


def test_source_alpha_mode_clips_displaced_pixels():
    a = rgba([(255, 0, 0, 255), (0, 0, 0, 0), (0, 0, 0, 0)])
    b = rgba([(0, 0, 255, 255), (0, 0, 0, 0), (0, 0, 0, 0)])
    g = graph(band(offset_to=[1, 0], start=0, end=1), alpha_mode='expanded')
    expanded = g.render({'a': a, 'b': b})
    clipped = g.render({'a': a, 'b': b}, parameter_values={('t', 'alpha_mode'): 'source'})
    assert expanded.getpixel((1, 0))[3] > 0
    assert clipped.getpixel((1, 0))[3] == 0


def test_hidden_transparent_source_colours_cannot_reveal():
    a = rgba([(255, 0, 0, 0), (0, 255, 0, 255)])
    b = rgba([(0, 0, 255, 0), (255, 255, 0, 255)])
    g = graph(band(start=0, end=.1))
    result = g.render({'a': a, 'b': b})
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((1, 0))[3] == 255


def test_tiling_matches_all_row_heights():
    images = sources()
    cfg = graph(band(offset_from=[.5, -.25], offset_to=[-.8, .6],
                     echoes=1, echo_decay=.4)).nodes['t'].config
    a = spectral_transition(images['a'], images['b'], cfg, tile_rows=1)
    assert a.tobytes() == spectral_transition(images['a'], images['b'], cfg, tile_rows=2).tobytes()
    assert a.tobytes() == spectral_transition(images['a'], images['b'], cfg, tile_rows=64).tobytes()


def test_reordering_bands_preserves_named_bindings():
    warm = band('warm', start=.2, end=.8)
    green = {'id': 'green', 'source_color': '#00ff00', 'space': 'rgb', 'start': .5, 'end': 1}
    a, b = graph(warm, green), graph(green, warm)
    for g in (a, b):
        g.validate_control_target('t', 'bands.warm.offset_to.x')
        g.validate_control_target('t', 'bands.green.source_color.g')
        assert g.base_control_value('t', 'bands.warm.start') == .2
        assert g.base_control_value('t', 'bands.green.source_color.g') == 255


def test_all_stable_numeric_band_controls_are_ephemeral():
    g = graph(band(offset_from=[1, 0], offset_to=[1, 1], echo_decay=.2))
    images = sources()
    original = copy.deepcopy(g.nodes['t'].config)
    changes = {
        ('t', 'bands.warm.source_color.r'): 200,
        ('t', 'bands.warm.to_color.b'): 180,
        ('t', 'bands.warm.target_color.g'): 200,
        ('t', 'bands.warm.offset_from.x'): 2.25,
        ('t', 'bands.warm.offset_to.y'): -1.75,
        ('t', 'bands.warm.start'): .1,
        ('t', 'bands.warm.end'): .9,
        ('t', 'bands.warm.opacity'): .75,
        ('t', 'bands.warm.echoes'): 2,
        ('t', 'bands.warm.echo_decay'): .9,
        ('t', 'bands.warm.recolor'): .6,
        ('t', 'mix'): .85,
        ('t', 'feather'): .4,
    }
    out = g.render(images, parameter_values=changes)
    assert out.size == (4, 2)
    assert g.nodes['t'].config == original
    assert g.render(images).tobytes() == g.render(images).tobytes()


def test_native_categorical_control_choices_and_frame_values():
    g = graph(band())
    g.validate_control_choices('t', 'bands.warm.space', ['rgb', 'hsv', 'oklab'])
    g.validate_control_choices('t', 'bands.warm.mask_source', ['from', 'to', 'union'])
    g.validate_control_choices('t', 'alpha_mode', ['source', 'expanded'])
    assert g.is_categorical_control('t', 'bands.warm.space')
    assert g.is_categorical_control('t', 'bands.warm.mask_source')
    assert g.is_categorical_control('t', 'alpha_mode')
    assert g.render(sources(), parameter_values={('t', 'bands.warm.space'): 'hsv'}).size == (4, 2)
    assert g.render(sources(), parameter_values={('t', 'bands.warm.mask_source'): 'intersection'}).size == (4, 2)


def test_invalid_categorical_transition_style_changes_rejected():
    g = graph(band())
    with pytest.raises(ValueError, match='spectral bands'):
        g.validate_control_choices('t', 'transition_style', ['spectral', 'dissolve'])
    without_bands = EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 't', 'type': 'transition', 'inputs': ['a', 'b']}
    ], 'output': 't'})
    with pytest.raises(ValueError, match='spectral bands'):
        without_bands.validate_control_choices('t', 'transition_style', ['dissolve', 'spectral'])


def test_spectral_transition_can_follow_forks_and_feed_merge():
    a = rgba([(255, 0, 0, 255), (0, 0, 0, 0)])
    b = rgba([(0, 0, 255, 255), (0, 0, 0, 0)])
    graph_spec = {'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'a_dim', 'type': 'copy', 'inputs': ['a'], 'opacity': .8},
        {'id': 't', 'type': 'transition', 'transition_style': 'spectral',
         'inputs': ['a_dim', 'b'], 'progress': .5, 'bands': [band()]},
        {'id': 'out', 'type': 'composite', 'inputs': ['t', 'a_dim']},
    ], 'output': 'out'}
    graph_obj = EffectGraph(graph_spec)
    out = graph_obj.render({'a': a, 'b': b})
    assert out.size == (2, 1)
    assert graph_obj.render({'a': a, 'b': b}).tobytes() == out.tobytes()
    assert a.getpixel((0, 0))[3] == 255


@pytest.mark.parametrize('bad_band', [
    {'id': 'warm', 'source_color': '#ff0000', 'start': .7, 'end': .4},
    {'id': 'warm', 'source_color': '#ff0000', 'softness': -1},
    {'id': 'warm', 'source_color': '#ff0000', 'offset_to': [5000, 0]},
    {'id': 'warm', 'source_color': '#ff0000', 'offset_from': ['bad', 0]},
    {'id': 'warm', 'source_color': '#ff0000', 'echoes': 5},
    {'id': 'warm', 'source_color': '#ff0000', 'echoes': 2.5},
    {'id': 'warm', 'source_color': '#ff0000', 'echo_decay': float('nan')},
    {'id': 'warm', 'source_color': '#ff0000', 'space': 'bad'},
    {'id': 'warm', 'source_color': '#ff0000', 'mask_source': 'bad'},
    {'id': 'warm', 'source_color': '#ff0000', 'bad': 1},
    {'id': 'warm'},
    {'id': [], 'source_color': '#ff0000'},
    {'id': 'warm', 'source_color': '#ff0000', 'target_color': 'not a colour'},
])
def test_bad_spectral_band_rejected_during_construction(bad_band):
    with pytest.raises(ValueError):
        graph(bad_band)


def test_invalid_empty_duplicate_and_extra_spectral_settings_rejected():
    for bands in ([], [band(), band()]):
        with pytest.raises(ValueError):
            graph(*bands) if bands else graph(bands=[])
    with pytest.raises(ValueError):
        graph(band(), alpha_mode='not-a-mode')
    with pytest.raises(ValueError):
        graph(band(), mix=1.1)
    with pytest.raises(ValueError):
        graph(band(), unknown_spectral_property=1)


@pytest.mark.parametrize('path,value', [
    ('bands.warm.start', 1.2), ('bands.warm.end', -.2),
    ('bands.warm.offset_from.x', float('inf')),
    ('bands.warm.target_color.b', 300), ('bands.warm.echoes', 5),
    ('bands.warm.mask_source', []), ('bands.warm.space', 'fake'),
    ('bands.missing.start', .2), ('bands.warm.offset_from.z', .3),
])
def test_invalid_frame_controls_fail(path, value):
    g = graph(band())
    with pytest.raises(ValueError):
        g.render(sources(), parameter_values={('t', path): value})


def test_crossing_start_and_end_fails_at_frame_not_mutate_config():
    g = graph(band())
    with pytest.raises(ValueError, match='end must exceed start'):
        g.render(sources(), parameter_values={('t', 'bands.warm.start'): 1})
    assert g.base_control_value('t', 'bands.warm.start') == 0


def test_legacy_dissolve_and_matte_transition_semantics_unchanged():
    a = Image.new('RGBA', (2, 1), (255, 0, 0, 255))
    b = Image.new('RGBA', (2, 1), (0, 0, 255, 255))
    legacy = EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 't', 'type': 'transition', 'inputs': ['a', 'b'], 'progress': .5},
    ], 'output': 't'})
    assert legacy.render({'a': a, 'b': b}).getpixel((0, 0)) in ((128, 0, 128, 255), (127, 0, 128, 255))


def test_explicit_cpu_fallback_and_strict_gpu_rejection(monkeypatch):
    from types import SimpleNamespace
    import sys

    class Unsupported(ValueError):
        pass

    # The CPU-only test process needs no GL context or optional GPU package.
    monkeypatch.setitem(sys.modules, 'framerudiments.effect_graph_gpu',
                        SimpleNamespace(GPUUnsupported=Unsupported))
    dummy = SimpleNamespace(last_dispatch={})
    g = graph(band())
    original = g.render(sources())
    fallback = g.render(sources(), gpu_backend=dummy, gpu_strict=False)
    assert fallback.tobytes() == original.tobytes()
    assert dummy.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}
    with pytest.raises(Unsupported, match='not implemented'):
        g.render(sources(), gpu_backend=dummy, gpu_strict=True)


@pytest.mark.parametrize('bad_band', [
    {'id': 'warm', 'source_color': '#ff0000', 'space': []},
    {'id': 'warm', 'source_color': '#ff0000', 'mask_source': []},
])
def test_nonhashable_enum_rejected_with_value_error(bad_band):
    with pytest.raises(ValueError):
        graph(bad_band)
