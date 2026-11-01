"""M6.5 actual resolved bounds, capture isolation and pixel-preservation gates."""
from copy import deepcopy
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_integration import evaluate_effect_graph
from framerudiments.effect_graph_layer_styles import STYLE_TYPES
from framerudiments.effect_graph_style_contract import layer_style_bounds


def make(style, **config):
    return EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
        {'id': 'styled', 'type': 'layer_style', 'inputs': ['s'], 'style_type': style, **config}],
        'output': 'styled'})


def fixture_image(kind):
    image = Image.new('RGBA', (48, 48))
    draw = ImageDraw.Draw(image)
    if kind == 'text':
        draw.text((9, 12), 'Aa', fill=(220, 60, 20, 180))
    elif kind == 'hole':
        draw.ellipse((8, 8, 39, 39), fill=(15, 180, 230, 128))
        draw.ellipse((18, 18, 29, 29), fill=(255, 0, 255, 0))
    elif kind == 'edge':
        draw.rectangle((0, 0, 15, 15), fill=(90, 200, 70, 255))
    else:
        draw.rectangle((10, 12, 28, 34), fill=(80, 60, 230, 140))
    return image


def contains(outer, inner):
    return inner is None or (outer is not None and
        outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3])


@pytest.mark.parametrize('style', sorted(STYLE_TYPES))
@pytest.mark.parametrize('shape', ['text', 'hole', 'edge', 'rectangle'])
def test_reporting_is_byte_exact_and_bounds_contain_actual_output(style, shape):
    image = fixture_image(shape)
    saved = image.tobytes()
    graph = make(style, style_color='#ff8840', style_blur=1, extrude_length=5)
    config = deepcopy(graph.nodes['styled'].config)
    report = {}
    expected = graph.render({'s': image})
    actual = graph.render({'s': image}, style_report=report)
    assert expected.tobytes() == actual.tobytes()
    row = report['nodes']['styled']
    assert row['bounds']['actual_output_alpha'] == (list(actual.getchannel('A').getbbox())
                                                   if actual.getchannel('A').getbbox() else None)
    assert contains(row['bounds']['conservative_canvas'], row['bounds']['actual_output_alpha'])
    assert row['execution'] == 'cpu'
    assert graph.nodes['styled'].config == config and image.tobytes() == saved
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('style', sorted(STYLE_TYPES))
@pytest.mark.parametrize('output', ['effect', 'combined'])
def test_zero_strength_and_opacity_report_empty_or_fill_not_fictitious_padding(style, output):
    graph = make(style, style_strength=0, style_output=output, style_offset=[-50, 50])
    source = fixture_image('edge')
    report = {}
    out = graph.render({'s': source}, style_report=report)
    row = report['nodes']['styled']['bounds']
    assert not row['may_clip']
    assert row['actual_output_alpha'] == (None if output == 'effect' else [0, 0, 16, 16])
    assert contains(row['conservative_canvas'], row['actual_output_alpha'])
    graph.render({'s': source}, parameter_values={('styled', 'opacity'): 0}, style_report=report)
    assert report['nodes']['styled']['bounds']['conservative_unclipped'] is None
    assert out is not source


def test_fractional_shadow_offsets_and_native_override_are_in_report():
    graph = make('drop_shadow', style_output='effect', style_offset=[0, 0])
    report = {}
    graph.render({'s': fixture_image('edge')}, parameter_values={('styled', 'style_offset.x'): -6.5},
                 style_report=report)
    row = report['nodes']['styled']
    assert row['parameters']['style_offset'] == [-6.5, 0]
    assert row['bounds']['may_clip']
    assert row['bounds']['padding_ltrb'][0] >= 7
    assert graph.nodes['styled'].config['style_offset'] == [0, 0]


def test_original_alpha_selection_report_uses_inherited_silhouette():
    graph = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'},
        {'id': 'gone', 'type': 'opacity', 'inputs': ['s'], 'opacity': 0},
        {'id': 'styled', 'type': 'layer_style', 'inputs': ['gone'], 'style_type': 'outline',
         'style_output': 'effect', 'alpha_source': 'original'},
    ], 'output': 'styled'})
    report = {}
    out = graph.render({'s': fixture_image('rectangle')}, style_report=report)
    bounds = report['nodes']['styled']['bounds']
    assert bounds['input_alpha'] is None
    assert bounds['selected_alpha'] == [10, 12, 29, 35]
    assert out.getchannel('A').getbbox() is not None
    assert contains(bounds['conservative_canvas'], bounds['actual_output_alpha'])


def test_report_published_only_on_complete_graph_success():
    graph = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'}, {'id': 'bad', 'type': 'source'},
        {'id': 'styled', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'outline'},
        {'id': 'out', 'type': 'composite', 'inputs': ['styled', 'bad']},
    ], 'output': 'out'})
    report = {'state': 'old success'}
    with pytest.raises(ValueError, match='dimensions'):
        graph.render({'s': fixture_image('text'), 'bad': Image.new('RGBA', (1, 1))}, style_report=report)
    assert report == {}
    with pytest.raises(ValueError, match='dictionary'):
        graph.render({}, style_report=[])


def test_compositor_capture_has_context_and_clears_on_failure_and_empty_frame():
    graph = make('outline')
    image = fixture_image('text')
    ctx = SimpleNamespace(frame=8, beat=2.25, source_beat=.25, source_seconds=.5,
                          output_seconds=4.5, lyric='PRIVATE TEXT')
    assert graph.style_render_report() == {}
    evaluate_effect_graph(graph, None, ctx, image, {'s': image})
    first = graph.style_render_report()
    assert first['sample_context']['frame'] == 8
    assert 'PRIVATE' not in json.dumps(first)
    first['nodes'].clear()
    assert graph.style_render_report()['nodes']
    with pytest.raises(ValueError):
        evaluate_effect_graph(graph, None, ctx, Image.new('RGB', image.size), {})
    assert graph.style_render_report() == {}
    evaluate_effect_graph(graph, None, ctx, image, {})
    assert graph.style_render_report()['nodes']['styled']['bounds']['actual_output_alpha'] is None


def test_capture_is_deterministic_out_of_order_not_cumulative():
    graph = make('drop_shadow')
    source = fixture_image('hole')
    first = {}
    graph.render({'s': source}, style_report=first)
    graph.render({'s': source}, parameter_values={('styled', 'style_offset.x'): 12})
    again = {}
    graph.render({'s': source}, style_report=again)
    assert first == again


def test_auto_report_identifies_cpu_execution_not_gpu_native():
    graph = make('outline')
    backend = SimpleNamespace(last_dispatch={})
    report = {}
    graph.render({'s': fixture_image('hole')}, gpu_backend=backend, style_report=report)
    assert report['nodes']['styled']['execution'] == 'cpu'
    assert report['nodes']['styled']['fallback_reason']
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}
    from framerudiments.effect_graph_gpu import GPUUnsupported
    with pytest.raises(GPUUnsupported):
        graph.render({'s': fixture_image('hole')}, gpu_strict=True, style_report=report)
    assert report == {}


def test_reporting_disabled_does_not_compute_bounds(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('unexpected diagnostics computation')
    monkeypatch.setattr('framerudiments.effect_graph.style_node_report', fail)
    make('outline').render({'s': fixture_image('rectangle')})


def test_texture_pattern_uses_image_canvas_not_texture_canvas_for_bounds():
    graph = EffectGraph({'nodes': [{'id': 's', 'type': 'source'}, {'id': 't', 'type': 'source'},
        {'id': 'styled', 'type': 'layer_style', 'inputs': ['s', 't'],
         'style_type': 'pattern_overlay', 'pattern_kind': 'texture'}], 'output': 'styled'})
    report = {}
    graph.render({'s': fixture_image('rectangle'), 't': Image.new('RGBA', (3, 5), 'red')}, style_report=report)
    assert report['nodes']['styled']['bounds']['canvas'] == [0, 0, 48, 48]
    assert report['nodes']['styled']['bounds']['actual_output_alpha'] == [10, 12, 29, 35]


def test_bounds_refuse_invalid_inputs_without_modification():
    image = fixture_image('rectangle')
    with pytest.raises(ValueError):
        layer_style_bounds(image.convert('RGB'), {})
    with pytest.raises(ValueError):
        layer_style_bounds(image, {'alpha_source': 'original'}, original_alpha=Image.new('L', (1, 1)))


def test_supersample_accessor_selects_last_sample_without_relabelling_the_clock():
    from framerudiments.effect_graph_integration import compositor_style_report
    graph = make('outline')
    image = fixture_image('hole')
    ctx = SimpleNamespace(frame=21, source_seconds=.175, output_seconds=.175, beat=.35)
    evaluate_effect_graph(graph, None, ctx, image, {'s': image})
    virtual = SimpleNamespace(effect_graph=graph, project=SimpleNamespace(fps=120))
    outer = SimpleNamespace(_temporal_virtual=virtual, effect_graph=None,
                            project=SimpleNamespace(fps=60), output_quality=SimpleNamespace(time_samples=2))
    report = compositor_style_report(outer)
    assert report['sample_context']['frame'] == 21
    assert report['sampling'] == {'owner': 'temporal_virtual', 'sample_fps': 120,
                                  'encoded_fps': 60, 'complete_shutter_bounds': False}
    assert 'sampling' not in graph.style_render_report()


@pytest.mark.parametrize('style', sorted(STYLE_TYPES))
def test_varied_original_current_and_translucent_bounds_contain_pixels(style):
    """Deterministic coverage stress cases, not guesses from RGB rectangles."""
    import random
    randomizer = random.Random(650)
    image = fixture_image('hole')
    for index in range(12):
        config = {'style_type': style, 'style_output': ['combined', 'effect'][index % 2],
                  'alpha_source': ['current', 'original'][(index // 2) % 2],
                  'style_width': randomizer.choice([0, .5, 2.5, 6]),
                  'style_blur': randomizer.choice([0, 1.25, 3.5]),
                  'style_offset': [randomizer.uniform(-18, 18), randomizer.uniform(-18, 18)],
                  'fill_opacity': randomizer.choice([0, .3, 1]),
                  'style_strength': randomizer.choice([0, .6, 1]),
                  'extrude_length': randomizer.choice([0, 2.5, 12]),
                  'outline_position': randomizer.choice(['inner', 'center', 'outer'])}
        graph = EffectGraph({'nodes': [
            {'id': 's', 'type': 'source'},
            {'id': 'dim', 'type': 'opacity', 'inputs': ['s'], 'opacity': .35},
            {'id': 'styled', 'type': 'layer_style', 'inputs': ['dim'], **config}], 'output': 'styled'})
        report = {}
        graph.render({'s': image}, style_report=report)
        bounds = report['nodes']['styled']['bounds']
        assert contains(bounds['conservative_canvas'], bounds['actual_output_alpha']), (style, config, bounds)
