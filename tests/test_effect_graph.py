import pytest
from PIL import Image
from framerudiments.effect_graph import EffectGraph


def graph(nodes, output):
    return EffectGraph({'nodes': nodes, 'output': output})


def test_fork_isolation_and_merge():
    g = graph([
        {'id': 's', 'type': 'source'},
        {'id': 'a', 'type': 'opacity', 'inputs': ['s'], 'opacity': 0.5},
        {'id': 'b', 'type': 'copy', 'inputs': ['s']},
        {'id': 'out', 'type': 'composite', 'inputs': ['a', 'b']},
    ], 'out')
    source = Image.new('RGBA', (2, 2), (200, 10, 10, 255))
    out = g.render({'s': source})
    assert out.getpixel((0, 0)) == (200, 10, 10, 255)
    assert source.getpixel((0, 0))[3] == 255


def test_matte_alpha():
    g = graph([{'id': 's', 'type': 'source'}, {'id': 'm', 'type': 'source'},
               {'id': 'out', 'type': 'matte', 'inputs': ['s', 'm']}], 'out')
    out = g.render({'s': Image.new('RGBA', (1, 1), (1, 2, 3, 200)),
                    'm': Image.new('RGBA', (1, 1), (0, 0, 0, 128))})
    assert abs(out.getpixel((0, 0))[3] - 100) <= 1


@pytest.mark.parametrize('nodes,output', [
    ([{'id': 'a', 'type': 'copy', 'inputs': ['a']}], 'a'),
    ([{'id': 'a', 'type': 'copy', 'inputs': ['missing']}], 'a'),
    ([{'id': 'a', 'type': 'source', 'opacity': float('nan')}], 'a'),
])
def test_invalid_graph(nodes, output):
    with pytest.raises(ValueError):
        graph(nodes, output)


def test_chroma_key_preserves_non_keyed_pixels_and_alpha():
    source = Image.new('RGBA', (2, 1))
    source.putpixel((0, 0), (0, 255, 0, 200))
    source.putpixel((1, 0), (255, 0, 0, 120))
    g = graph([{'id': 's', 'type': 'source'},
               {'id': 'key', 'type': 'chroma_key', 'inputs': ['s'],
                'key_color': '#00ff00', 'tolerance': 0.1, 'softness': 0}], 'key')
    out = g.render({'s': source})
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((1, 0)) == (255, 0, 0, 120)


def test_matte_invert_and_feather():
    g = graph([{'id': 's', 'type': 'source'}, {'id': 'm', 'type': 'source'},
               {'id': 'out', 'type': 'matte', 'inputs': ['s', 'm'], 'invert': True}], 'out')
    result = g.render({'s': Image.new('RGBA', (1, 1), (1, 2, 3, 255)),
                       'm': Image.new('RGBA', (1, 1), (0, 0, 0, 255))})
    assert result.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize('bad', [float('nan'), -0.1, 1.1, 'oops'])
def test_invalid_key_tolerance(bad):
    with pytest.raises(ValueError):
        graph([{'id': 's', 'type': 'source'},
               {'id': 'key', 'type': 'chroma_key', 'inputs': ['s'], 'tolerance': bad}], 'key')


def test_frame_parameter_override_is_ephemeral():
    g = graph([{'id': 's', 'type': 'source'},
               {'id': 'o', 'type': 'opacity', 'inputs': ['s'], 'opacity': 1}], 'o')
    source = {'s': Image.new('RGBA', (1, 1), (10, 20, 30, 200))}
    assert g.render(source, parameter_values={('o', 'opacity'): 0.25}).getpixel((0, 0))[3] == 50
    assert g.render(source).getpixel((0, 0))[3] == 200
    with pytest.raises(ValueError):
        g.render(source, parameter_values={('o', 'opacity'): float('nan')})
    with pytest.raises(ValueError):
        g.render(source, parameter_values={('o', 'unknown'): 1})


def test_chromatic_displacement_channel_vectors_and_alpha():
    source = Image.new('RGBA', (3, 1), (0, 0, 0, 0))
    source.putpixel((0, 0), (255, 20, 30, 200))
    source.putpixel((1, 0), (10, 40, 50, 128))
    g = graph([{'id': 's', 'type': 'source'},
               {'id': 'split', 'type': 'chromatic_displacement', 'inputs': ['s'],
                'red_offset': [1, 0], 'green_offset': [0, 0],
                'blue_offset': [-1, 0]}], 'split')
    result = g.render({'s': source})
    assert result.getpixel((1, 0)) == (255, 40, 0, 128)
    assert source.getpixel((1, 0)) == (10, 40, 50, 128)


@pytest.mark.parametrize('bad', [[1], [float('nan'), 0], [5000, 0], 'invalid'])
def test_chromatic_displacement_rejects_invalid_vectors(bad):
    with pytest.raises(ValueError):
        graph([{'id': 's', 'type': 'source'},
               {'id': 'split', 'type': 'chromatic_displacement',
                'inputs': ['s'], 'red_offset': bad}], 'split')


def test_chromatic_displacement_vector_controls_are_independent_and_ephemeral():
    source = Image.new('RGBA', (4, 1), (0, 0, 0, 255))
    source.putpixel((0, 0), (250, 0, 0, 255))
    g = graph([{'id': 's', 'type': 'source'},
               {'id': 'split', 'type': 'chromatic_displacement', 'inputs': ['s']}], 'split')
    moved = g.render({'s': source}, parameter_values={('split', 'red_offset.x'): 2})
    assert moved.getpixel((2, 0))[0] == 250
    assert g.render({'s': source}).getpixel((2, 0))[0] == 0
    assert source.getpixel((0, 0))[0] == 250


@pytest.mark.parametrize('target,value', [
    (('split', 'red_offset.x'), 5000),
    (('split', 'blue_offset.y'), float('nan')),
    (('missing', 'red_offset.x'), 1),
    ('split.red_offset.x', 1),
])
def test_invalid_vector_control_targets(target, value):
    g = graph([{'id': 's', 'type': 'source'},
               {'id': 'split', 'type': 'chromatic_displacement', 'inputs': ['s']}], 'split')
    with pytest.raises(ValueError):
        g.render({'s': Image.new('RGBA', (1, 1))}, parameter_values={target: value})


def test_keying_controls_are_frame_local():
    g = graph([{'id': 's', 'type': 'source'},
               {'id': 'key', 'type': 'chroma_key', 'inputs': ['s'],
                'key_color': '#00ff00', 'tolerance': 0, 'softness': 0}], 'key')
    image = Image.new('RGBA', (1, 1), (0, 220, 0, 255))
    baseline = g.render({'s': image}).getpixel((0, 0))[3]
    changed = g.render({'s': image}, parameter_values={('key', 'tolerance'): 0.2}).getpixel((0, 0))[3]
    assert changed < baseline
    assert g.render({'s': image}).getpixel((0, 0))[3] == baseline


def test_fractional_chromatic_offset_bilinear_and_transparent_border():
    source = Image.new('RGBA', (3, 1), (0, 0, 0, 255))
    source.putpixel((0, 0), (200, 0, 0, 255))
    g = graph([{'id': 's', 'type': 'source'},
               {'id': 'split', 'type': 'chromatic_displacement', 'inputs': ['s']}], 'split')
    result = g.render({'s': source}, parameter_values={('split', 'red_offset.x'): 0.5})
    assert [result.getpixel((i, 0))[0] for i in range(3)] == [100, 100, 0]
    assert [result.getpixel((i, 0))[3] for i in range(3)] == [255, 255, 255]
    assert source.getpixel((0, 0))[0] == 200


def test_fractional_chromatic_offset_in_y_axis():
    source = Image.new('RGBA', (1, 3), (0, 0, 0, 255))
    source.putpixel((0, 0), (0, 180, 0, 255))
    g = graph([{'id': 's', 'type': 'source'},
               {'id': 'split', 'type': 'chromatic_displacement', 'inputs': ['s'],
                'green_offset': [0, 1.5]}], 'split')
    result = g.render({'s': source})
    assert [result.getpixel((0, i))[1] for i in range(3)] == [0, 90, 90]


def test_chroma_key_colour_components_are_independently_modulatable():
    g = graph([{'id':'s','type':'source'}, {'id':'k','type':'chroma_key', 'inputs':['s'],
               'key_color':'#00ff00', 'tolerance':0.01, 'softness':0}], 'k')
    source = Image.new('RGBA', (1, 1), (255, 0, 0, 255))
    assert g.render({'s':source}).getpixel((0,0))[3] == 255
    keyed = g.render({'s':source}, parameter_values={('k','key_color.r'):255, ('k','key_color.g'):0})
    assert keyed.getpixel((0,0))[3] == 0
    assert g.render({'s':source}).getpixel((0,0))[3] == 255

@pytest.mark.parametrize('value', [-1, 256, float('inf'), True])
def test_chroma_key_colour_controls_reject_invalid_values(value):
    g = graph([{'id':'s','type':'source'}, {'id':'k','type':'chroma_key','inputs':['s']}], 'k')
    with pytest.raises(ValueError):
        g.render({'s':Image.new('RGBA',(1,1))}, parameter_values={('k','key_color.b'):value})


def test_luminance_track_matte_respects_matte_alpha():
    src = Image.new('RGBA', (3, 1), (200, 50, 10, 200))
    matte = Image.new('RGBA', (3, 1))
    matte.putpixel((0, 0), (255, 255, 255, 255))
    matte.putpixel((1, 0), (0, 0, 0, 255))
    matte.putpixel((2, 0), (255, 255, 255, 0))
    g = graph([{'id':'s','type':'source'}, {'id':'m','type':'source'},
               {'id':'out','type':'matte','inputs':['s','m'], 'matte_channel':'luminance'}], 'out')
    result = g.render({'s':src,'m':matte})
    assert [result.getpixel((x,0))[3] for x in range(3)] == [200,0,0]

def test_invalid_matte_channel_fails_validation():
    with pytest.raises(ValueError, match='matte_channel'):
        graph([{'id':'s','type':'source'}, {'id':'m','type':'source'},
               {'id':'out','type':'matte','inputs':['s','m'],'matte_channel':'depth'}], 'out')
