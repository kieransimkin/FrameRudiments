"""M7.3 actual Pillow/NumPy reference, alpha-lineage and contract regressions."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_spatial import (
    WARP_TYPES, WARP_RANGES, WARP_ENUMS, _sample_alpha,
    render_spatial_warp, render_spatial_warp_with_alpha,
    spatial_control_apply, spatial_control_base, spatial_parameter_descriptors, validate_spatial,
)


@pytest.fixture
def surface():
    image = Image.new('RGBA', (31, 27))
    draw = ImageDraw.Draw(image)
    draw.rectangle((4, 5, 23, 21), fill=(240, 60, 30, 179))
    draw.ellipse((7, 4, 17, 15), fill=(20, 200, 240, 255))
    draw.rectangle((12, 9, 15, 12), fill=(255, 0, 255, 0))
    return image


def warp_graph(**config):
    sources = [{'id': 'source', 'type': 'source'}]
    inputs = ['source']
    if config.get('warp_type') == 'displacement':
        sources.append({'id': 'map', 'type': 'source'})
        inputs.append('map')
    return EffectGraph({'nodes': sources + [{'id': 'out', 'type': 'spatial_warp', 'inputs': inputs, **config}],
                        'output': 'out'})


@pytest.mark.parametrize('kind', sorted(WARP_TYPES))
def test_zero_mix_is_byte_exact_and_independent(surface, kind):
    cfg = {'warp_type': kind, 'warp_mix': 0}
    map_image = Image.new('RGBA', surface.size, (255, 128, 0, 255)) if kind == 'displacement' else None
    out, lineage = render_spatial_warp_with_alpha(surface, cfg, map_image=map_image,
                                                original_alpha=surface.getchannel('A'))
    assert out is not surface and out.tobytes() == surface.tobytes()
    assert lineage.tobytes() == surface.getchannel('A').tobytes()
    out.putpixel((0, 0), (1, 2, 3, 4))
    assert surface.getpixel((0, 0)) == (0, 0, 0, 0)


@pytest.mark.parametrize('kind', sorted(WARP_TYPES))
@pytest.mark.parametrize('border', ['transparent', 'clamp', 'repeat', 'mirror'])
def test_tiles_determinism_alpha_and_immutable_inputs(surface, kind, border):
    cfg = {'warp_type': kind, 'warp_border': border, 'warp_radius': 15, 'warp_phase': .7,
           'warp_amplitude': [2.25, -1.75]}
    saved = deepcopy(cfg)
    source_bytes = surface.tobytes()
    map_image = Image.new('RGBA', surface.size, (200, 40, 0, 123)) if kind == 'displacement' else None
    original = surface.getchannel('A')
    outputs = [render_spatial_warp_with_alpha(surface, cfg, map_image=map_image,
                                             original_alpha=original, tile_rows=rows)
               for rows in (1, 7, 48)]
    assert len({out.tobytes() for out, _ in outputs}) == 1
    assert len({alpha.tobytes() for _, alpha in outputs}) == 1
    rendered = np.asarray(outputs[0][0])
    assert np.max(np.abs(rendered[..., 3].astype(int) - np.asarray(outputs[0][1]).astype(int))) <= 1
    assert np.all(rendered[..., :3][rendered[..., 3] == 0] == 0)
    assert cfg == saved and source_bytes == surface.tobytes()


@pytest.mark.parametrize('dx', [-3, -1, 1, 3])
def test_displacement_integer_direction_and_neutral_axis(dx):
    source = Image.new('RGBA', (9, 3))
    source.putpixel((4, 1), (255, 50, 20, 197))
    displacement = Image.new('RGBA', source.size, (255, 128, 0, 255))
    out = render_spatial_warp(source, {'warp_type': 'displacement', 'warp_amplitude': [dx, 0]},
                              map_image=displacement)
    assert out.getpixel((4 + dx, 1)) == (255, 50, 20, 197)
    assert out.getchannel('A').getbbox() == (4 + dx, 1, 5 + dx, 2)


def test_fractional_displacement_never_leaks_transparent_rgb():
    source = Image.new('RGBA', (5, 1), (0, 0, 255, 0))
    source.putpixel((2, 0), (255, 0, 0, 255))
    out = render_spatial_warp(source, {'warp_type': 'displacement', 'warp_amplitude': [.5, 0]},
                              map_image=Image.new('RGBA', source.size, (255, 128, 0, 255)))
    assert out.getpixel((2, 0)) == (255, 0, 0, 128)
    assert out.getpixel((3, 0)) == (255, 0, 0, 128)
    assert not np.asarray(out)[..., 2].any()


@pytest.mark.parametrize('border,red', [('transparent', 0), ('clamp', 10), ('repeat', 30), ('mirror', 20)])
def test_border_footprints_have_known_integer_values(border, red):
    source = Image.fromarray(np.array([[[v, 0, 0, 255] for v in (10, 20, 30, 40)]], dtype=np.uint8))
    out = render_spatial_warp(source, {'warp_type': 'displacement', 'warp_amplitude': [2, 0],
                                      'warp_border': border},
                              map_image=Image.new('RGBA', source.size, (255, 128, 0, 255)))
    assert out.getpixel((0, 0))[0] == red
    assert out.getpixel((0, 0))[3] == (0 if border == 'transparent' else 255)


def test_hidden_map_colours_are_neutral(surface):
    out = render_spatial_warp(surface, {'warp_type': 'displacement'},
                              map_image=Image.new('RGBA', surface.size, (255, 0, 100, 0)))
    assert out.tobytes() == surface.tobytes()


def test_map_128_is_exact_neutral_at_every_pixel():
    source = Image.fromarray(np.random.default_rng(4).integers(0, 256, (13, 17, 4), dtype=np.uint8))
    data = np.asarray(source).copy()
    data[data[..., 3] == 0] = 0
    source = Image.fromarray(data)
    out = render_spatial_warp(source, {'warp_type': 'displacement'},
                              map_image=Image.new('RGBA', source.size, (128, 128, 255, 255)))
    assert out.tobytes() == source.tobytes()


@pytest.mark.parametrize('map_channel', ['r', 'g', 'b', 'a', 'luminance'])
def test_map_channels_are_selectable(surface, map_channel):
    out = render_spatial_warp(surface, {'warp_type': 'displacement', 'warp_map_x': map_channel},
                              map_image=Image.new('RGBA', surface.size, (10, 80, 240, 200)))
    assert out.mode == 'RGBA' and out.size == surface.size


def test_turbulence_seed_phase_and_global_rng_independence(surface):
    cfg = {'warp_type': 'turbulence', 'warp_scale': [7, 9], 'warp_seed': 7}
    before = np.random.get_state()
    a = render_spatial_warp(surface, cfg)
    b = render_spatial_warp(surface, {**cfg, 'warp_seed': 8})
    c = render_spatial_warp(surface, {**cfg, 'warp_phase': .2})
    after = np.random.get_state()
    assert a.tobytes() != b.tobytes() != c.tobytes()
    assert a.tobytes() == render_spatial_warp(surface, cfg).tobytes()
    assert before[0] == after[0] and np.array_equal(before[1], after[1]) and before[2:] == after[2:]


def test_mirror_has_exact_even_canvas_symmetry(surface):
    source = surface.resize((32, 28))
    out = np.asarray(render_spatial_warp(source, {'warp_type': 'mirror', 'warp_axis': 'xy'}))
    assert np.array_equal(out, out[:, ::-1])
    assert np.array_equal(out, out[::-1])


@pytest.mark.parametrize('border', ['transparent', 'clamp', 'repeat', 'mirror'])
def test_single_pixel_canvas_has_defined_sampling(border):
    source = Image.new('RGBA', (1, 1), (100, 200, 50, 120))
    for kind in WARP_TYPES - {'displacement'}:
        out = render_spatial_warp(source, {'warp_type': kind, 'warp_border': border})
        assert out.size == (1, 1)
        if border != 'transparent':
            assert out.tobytes() == source.tobytes()


@pytest.mark.parametrize('name', sorted(WARP_RANGES))
@pytest.mark.parametrize('bad', [True, float('nan'), float('inf')])
def test_invalid_control_numbers_fail(name, bad):
    with pytest.raises(ValueError):
        spatial_control_apply({}, name, bad)


@pytest.mark.parametrize('name', sorted(WARP_ENUMS))
def test_invalid_categories_fail(name):
    for value in (None, 1, [], 'not-an-option'):
        with pytest.raises(ValueError):
            spatial_control_apply({}, name, value)


@pytest.mark.parametrize('config', [
    {'warp_scale': [0, 1]}, {'warp_center': [2, 0]}, {'warp_amplitude': [1]},
    {'warp_octaves': 3.5}, {'warp_seed': -1}, {'warp_segments': True}, {'warp_neutral': 0},
    {'warp_neutral': 1}, {'typo': 2}, {'warp_mix': 2}, {'warp_radius': 0},
])
def test_invalid_authored_configs_fail(config):
    with pytest.raises(ValueError):
        validate_spatial(config)


def test_immutable_vector_overrides_and_integer_quantization():
    config = {'warp_amplitude': [1, 2]}
    out = spatial_control_apply(config, 'warp_amplitude.x', 5)
    assert config == {'warp_amplitude': [1, 2]}
    assert out['warp_amplitude'] == [5, 2]
    assert spatial_control_apply({}, 'warp_segments', 7.2)['warp_segments'] == 7
    assert spatial_control_base(out, 'warp_amplitude.y') == 2


def test_descriptor_defaults_values_active_and_topology():
    desc = spatial_parameter_descriptors({'warp_type': 'wave', 'warp_phase': 1}, input_count=1)
    assert desc['warp_phase']['value'] == 1 and desc['warp_phase']['default'] == 0
    assert desc['warp_phase']['active'] and not desc['warp_radius']['active']
    assert 'displacement' not in desc['warp_type']['choices']
    desc['warp_mix']['range'][0] = 99
    assert spatial_parameter_descriptors()['warp_mix']['range'] == [0, 1]
    assert spatial_parameter_descriptors({'warp_type': 'displacement'}, input_count=2)['warp_type']['choices'] == ['displacement']


def test_topology_controls_reject_changes_before_render(surface):
    graph = warp_graph()
    with pytest.raises(ValueError):
        graph.validate_control_choices('out', 'warp_type', ['wave', 'displacement'])
    with pytest.raises(ValueError):
        graph.render({'source': surface}, parameter_values={('out', 'warp_type'): 'displacement'})
    graph.validate_control_choices('out', 'warp_type', ['wave', 'swirl'])


def test_graph_controls_forks_and_universal_opacity(surface):
    graph = warp_graph(warp_type='wave', warp_amplitude=[5, 0])
    saved = deepcopy(graph.nodes['out'].config)
    a = graph.render({'source': surface}, parameter_values={('out', 'warp_phase'): .3})
    b = graph.render({'source': surface}, parameter_values={('out', 'warp_phase'): 1.2})
    assert a.tobytes() != b.tobytes()
    assert graph.render({'source': surface}, parameter_values={('out', 'warp_phase'): .3}).tobytes() == a.tobytes()
    assert graph.nodes['out'].config == saved
    zero = graph.render({'source': surface}, parameter_values={('out', 'opacity'): 0})
    assert zero.getchannel('A').getbbox() is None
    assert graph.control_descriptors('out')['warp_phase']['active']
    manifest = graph.spatial_manifest()
    assert manifest['out']['backend'] == 'cpu' and not manifest['out']['gpu_native']
    manifest['out']['controls'].clear()
    assert graph.spatial_manifest()['out']['controls']


def test_lineage_is_warped_before_original_alpha_restoration(surface):
    config = {'warp_type': 'wave', 'warp_amplitude': [7, 3], 'warp_phase': .6}
    nodes = [
        {'id': 'source', 'type': 'source'},
        {'id': 'dim', 'type': 'opacity', 'inputs': ['source'], 'opacity': .2},
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': ['dim'], **config},
        {'id': 'mask', 'type': 'source'},
        {'id': 'restore', 'type': 'matte', 'inputs': ['warp', 'mask'], 'alpha_source': 'original'},
    ]
    graph = EffectGraph({'nodes': nodes, 'output': 'restore'})
    result = graph.render({'source': surface, 'mask': Image.new('RGBA', surface.size, 'white')})
    expected = render_spatial_warp(surface, config).getchannel('A')
    assert result.getchannel('A').tobytes() == expected.tobytes()
    assert result.getchannel('A').tobytes() != surface.getchannel('A').tobytes()


def test_chain_preprocessing_compiles_to_existing_warp_node(surface):
    graph = EffectGraph({'nodes': [
        {'id': 'source', 'type': 'source'},
        {'id': 'chain', 'type': 'transition_chain', 'inputs': ['source', 'source'],
         'input_effects': [[{'id': 'wave', 'type': 'spatial_warp', 'warp_amplitude': [5, 0]}], []],
         'stages': [{'id': 'fade', 'progress': 0}]},
    ], 'output': 'chain'})
    params = {('chain::input::0::wave', 'warp_phase'): 1}
    result = graph.render({'source': surface}, parameter_values=params)
    expected = render_spatial_warp(surface, {'warp_amplitude': [5, 0], 'warp_phase': 1})
    assert result.tobytes() == expected.tobytes()


def test_gpu_contract_explicit_reject_and_auto_fallback(surface):
    graph = warp_graph()
    for backend in (None, SimpleNamespace()):
        with pytest.raises(GPUUnsupported, match='spatial_warp'):
            graph.render({'source': surface}, gpu_backend=backend, gpu_strict=True)
    backend = SimpleNamespace()
    result = graph.render({'source': surface}, gpu_backend=backend)
    assert result.tobytes() == graph.render({'source': surface}).tobytes()
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}


def test_resource_budget_rejects_before_sampling(surface, monkeypatch):
    import framerudiments.effect_graph_spatial as module
    monkeypatch.setattr(module, 'MAX_PIXELS', 1)
    with pytest.raises(ValueError, match='budget'):
        render_spatial_warp(surface, {})


@pytest.mark.parametrize('bad', [0, -1, 257, True, 1.5])
def test_invalid_tile_size(surface, bad):
    with pytest.raises(ValueError):
        render_spatial_warp(surface, {}, tile_rows=bad)


def test_map_size_and_mode_rejected(surface):
    for bad in (Image.new('RGBA', (1, 1)), Image.new('RGB', surface.size)):
        with pytest.raises(ValueError):
            render_spatial_warp(surface, {'warp_type': 'displacement'}, map_image=bad)
    with pytest.raises(ValueError):
        render_spatial_warp(surface, {}, map_image=surface)
    with pytest.raises(ValueError):
        render_spatial_warp_with_alpha(surface, {}, original_alpha=Image.new('L', (1, 1)))


def test_spatial_fields_cannot_be_silently_ignored_by_other_nodes():
    with pytest.raises(ValueError, match='warp properties'):
        EffectGraph({'nodes': [{'id': 'source', 'type': 'source', 'warp_phase': .5}], 'output': 'source'})


def test_alpha_sampler_matches_existing_rgba_sampler():
    from framerudiments.effect_graph_transition_geometry import _sample
    rng = np.random.default_rng(11)
    source = rng.integers(0, 256, (7, 9, 4), dtype=np.uint8)
    y, x = np.indices((5, 11), dtype=np.float64)
    x, y = x - 2.4, y + .73
    assert np.allclose(_sample(source, x, y)[1], _sample_alpha(source[..., 3], x, y, 'transparent'), atol=1e-7)
