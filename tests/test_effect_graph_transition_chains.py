"""M5.4 transition chain compilation and output parity with explicit DAG nodes."""
from __future__ import annotations

import copy
import hashlib

import pytest
from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph


def images(size=(11, 9)):
    a = Image.new('RGBA', size, (0, 255, 0, 0))
    b = Image.new('RGBA', size, (0, 0, 0, 0))
    c = Image.new('RGBA', size, (0, 0, 0, 0))
    mask = Image.new('RGBA', size)
    ImageDraw.Draw(a).rectangle((1, 1, 6, 7), fill=(245, 65, 25, 230))
    ImageDraw.Draw(b).ellipse((2, 0, 9, 8), fill=(40, 170, 250, 180))
    ImageDraw.Draw(c).rectangle((5, 0, 10, 8), fill=(220, 60, 210, 210))
    ImageDraw.Draw(mask).rectangle((0, 0, 5, 8), fill=(255, 255, 255, 255))
    return {'a': a, 'b': b, 'c': c, 'mask': mask}


def graph(*nodes, output='sequence'):
    return EffectGraph({'nodes': list(nodes), 'output': output})


def test_chain_renders_exactly_as_explicit_multistage_dag():
    stage1 = {'id': 'intro', 'progress': .3, 'transition_style': 'dissolve'}
    stage2 = {'id': 'outro', 'progress': .65, 'transition_style': 'wipe', 'feather': .1}
    compact = graph(
        *[{'id': k, 'type': 'source'} for k in ('a', 'b', 'c')],
        {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b', 'c'],
         'stages': [stage1, stage2]},
    )
    explicit = graph(
        *[{'id': k, 'type': 'source'} for k in ('a', 'b', 'c')],
        {'id': 'sequence::stage::intro', 'type': 'transition', 'inputs': ['a', 'b'],
         'progress': .3, 'transition_style': 'dissolve'},
        {'id': 'sequence::stage::outro', 'type': 'transition',
         'inputs': ['sequence::stage::intro', 'c'], 'progress': .65,
         'transition_style': 'wipe', 'feather': .1},
        {'id': 'sequence', 'type': 'copy', 'inputs': ['sequence::stage::outro'], 'opacity': 1},
    )
    before = copy.deepcopy(images())
    expected = explicit.render(before)
    actual = compact.render(images())
    assert actual.tobytes() == expected.tobytes()
    assert compact.chain_expansions['sequence']['stages'] == {
        'intro': 'sequence::stage::intro', 'outro': 'sequence::stage::outro'}
    assert compact.nodes['sequence'].kind == 'copy'
    assert stage1 == {'id': 'intro', 'progress': .3, 'transition_style': 'dissolve'}


def test_independent_per_input_preprocessing_and_original_input_immutability():
    source = images()
    before = {k: im.tobytes() for k, im in source.items()}
    chain = graph(
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b'],
         'input_effects': [
             [{'id': 'dim', 'type': 'opacity', 'opacity': .5}],
             [{'id': 'split', 'type': 'chromatic_displacement',
               'red_offset': [1.25, 0], 'blue_offset': [-1.5, 0]}],
         ],
         'stages': [{'id': 'intro', 'progress': .5}], 'opacity': .75},
    )
    base = chain.render(source)
    assert base.getchannel('A').getextrema()[1] <= 191
    assert chain.chain_expansions['sequence']['inputs'] == [
        ['sequence::input::0::dim'], ['sequence::input::1::split']]
    changed = chain.render(source, parameter_values={
        ('sequence::input::1::split', 'red_offset.x'): 2.5,
        ('sequence::stage::intro', 'progress'): .85,
        ('sequence', 'opacity'): 1,
    })
    assert base.tobytes() != changed.tobytes()
    assert chain.render(source).tobytes() == base.tobytes()
    assert {k: im.tobytes() for k, im in source.items()} == before


def test_stage_controls_are_native_graph_bindable_and_stable_when_order_changes():
    original = graph(
        *[{'id': k, 'type': 'source'} for k in ('a', 'b', 'c')],
        {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b', 'c'],
         'stages': [{'id': 'first', 'progress': .2}, {'id': 'second', 'progress': .6}]},
    )
    original.validate_control_target('sequence::stage::second', 'progress')
    assert original.base_control_value('sequence::stage::second', 'progress') == .6
    source = images()
    at_0 = original.render(source, parameter_values={
        ('sequence::stage::first', 'progress'): 0,
        ('sequence::stage::second', 'progress'): 0,
    })
    at_1 = original.render(source, parameter_values={
        ('sequence::stage::first', 'progress'): 1,
        ('sequence::stage::second', 'progress'): 1,
    })
    assert at_0.tobytes() == source['a'].tobytes()
    assert at_1.tobytes() == source['c'].tobytes()
    assert original.nodes['sequence::stage::second'].config['progress'] == .6


def test_matte_stage_uses_arbitrary_graph_output_as_third_mask():
    source = images()
    stack = graph(
        *[{'id': k, 'type': 'source'} for k in ('a', 'b', 'mask')],
        {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b'],
         'stages': [{'id': 'reveal', 'transition_style': 'matte', 'matte_input': 'mask',
                     'progress': .4, 'feather': .2}]},
    )
    direct = graph(
        *[{'id': k, 'type': 'source'} for k in ('a', 'b', 'mask')],
        {'id': 't', 'type': 'transition', 'inputs': ['a', 'b', 'mask'],
         'transition_style': 'matte', 'progress': .4, 'feather': .2}, output='t',
    )
    assert stack.render(source).tobytes() == direct.render(source).tobytes()


def test_parallel_transition_chains_join_through_existing_compositor():
    graph_spec = graph(
        *[{'id': k, 'type': 'source'} for k in ('a', 'b', 'c')],
        {'id': 'left', 'type': 'transition_chain', 'inputs': ['a', 'b'],
         'stages': [{'id': 'glide', 'transition_style': 'liquid',
                     'progress': .5, 'amplitude': 2}]},
        {'id': 'right', 'type': 'transition_chain', 'inputs': ['a', 'c'],
         'stages': [{'id': 'rotate', 'transition_style': 'rotate',
                     'progress': .5, 'rotation': 180}]},
        {'id': 'out', 'type': 'composite', 'inputs': ['left', 'right']}, output='out',
    )
    source = images()
    output = graph_spec.render(source)
    assert output.mode == 'RGBA'
    assert output.size == source['a'].size
    assert output.tobytes() == graph_spec.render(source).tobytes()
    assert graph_spec.nodes['left::stage::glide'].config['transition_style'] == 'liquid'


def test_chain_render_is_random_access_and_has_stable_frame_hashes():
    g = graph(
        *[{'id': k, 'type': 'source'} for k in ('a', 'b', 'c')],
        {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b', 'c'],
         'stages': [{'id': 'a_to_b', 'progress': .2}, {'id': 'b_to_c', 'progress': .8}]},
    )
    src = images()
    def at(t):
        return hashlib.sha256(g.render(src, parameter_values={
            ('sequence::stage::a_to_b', 'progress'): t,
            ('sequence::stage::b_to_c', 'progress'): 1 - t,
        }).tobytes()).hexdigest()
    assert at(.6) == at(.6)
    assert at(.2) != at(.6)
    assert at(.6) == at(.6)


@pytest.mark.parametrize('definition,match', [
    ({'stages': []}, 'one stage'),
    ({'inputs': ['a']}, '2..8'),
    ({'stages': [{'id': 'bad.dot'}]}, 'lowercase id'),
    ({'stages': [{'id': 'a', 'matte_input': 'mask'}]}, 'only matte'),
    ({'stages': [{'id': 'a', 'transition_style': 'matte'}]}, 'requires matte_input'),
    ({'stages': [{'id': 'a', 'type': 'source'}]}, 'cannot override'),
    ({'input_effects': [[]]}, 'input_effects'),
    ({'input_effects': [[{'type': 'source', 'id': 'bad'}], []]}, 'unary'),
    ({'input_effects': [[{'type': 'copy', 'id': 'v', 'inputs': ['a']}], []]}, 'unary'),
    ({'input_effects': [[{'type': 'copy', 'id': 'Upper'}], []]}, 'lowercase id'),
    ({'opacity': float('nan')}, 'opacity'),
    ({'unknown': 5}, 'unknown transition_chain'),
])
def test_malformed_chains_fail_before_render(definition, match):
    chain = {'id': 'sequence', 'type': 'transition_chain',
             'inputs': ['a', 'b'], 'stages': [{'id': 'a'}], **definition}
    with pytest.raises(ValueError, match=match):
        graph({'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'}, chain)


def test_reject_duplicate_stage_and_generated_name_collisions():
    with pytest.raises(ValueError, match='duplicate transition stage id'):
        graph(*[{'id': k, 'type': 'source'} for k in ('a', 'b', 'c')],
              {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b', 'c'],
               'stages': [{'id': 'repeat'}, {'id': 'repeat'}]})
    with pytest.raises(ValueError, match='collision'):
        graph({'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
              {'id': 'sequence::stage::intro', 'type': 'source'},
              {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b'],
               'stages': [{'id': 'intro'}]})


def test_reject_cycle_from_stage_matte_and_bad_references():
    with pytest.raises(ValueError, match='cycle'):
        graph({'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
              {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b'],
               'stages': [{'id': 'masked', 'transition_style': 'matte',
                           'matte_input': 'sequence'}]})
    with pytest.raises(ValueError, match='unknown input'):
        graph({'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
              {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b'],
               'stages': [{'id': 'masked', 'transition_style': 'matte',
                           'matte_input': 'missing'}]})


def test_reject_unbounded_graph_after_expansion():
    sources = [{'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'}]
    # 48 macros * (4 input effects + one stage + wrapper) exceed 256 nodes.
    chains = [
        {'id': f'chain{i}', 'type': 'transition_chain', 'inputs': ['a', 'b'],
         'input_effects': [[{'id': f'fx{j}', 'type': 'copy'} for j in range(4)], []],
         'stages': [{'id': 'first', 'progress': .5}]}
        for i in range(48)
    ]
    with pytest.raises(ValueError, match='node limit'):
        graph(*sources, *chains, output='chain47')
