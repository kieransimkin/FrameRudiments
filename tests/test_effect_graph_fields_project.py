"""Real Project/Compositor/native motion acceptance. No replacement driver."""
from copy import deepcopy
import os
from pathlib import Path

import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def project(tmp_path):
    supplied = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    candidates = [Path(supplied)] if supplied else [
        Path('C:/Windows/Fonts/arial.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    ]
    font = next((p for p in candidates if p.is_file()), None)
    if font is None:
        if supplied:
            pytest.fail('FRAMERUDIMENTS_TEST_FONT must name an existing font')
        pytest.skip('Installed TTF required; CI and installer must supply one')
    original = Project.load(create_demo(tmp_path / 'fields-demo', str(font), width=180, height=320))
    data = deepcopy(original.data)
    data['render_quality'] = {'enabled': False}
    return Project(data, original.base)


def configured(project, config, controls):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [dict(id='background', type='source'),
        dict(id='f', type='procedural_field', inputs=['background'], **config)], 'output':'f'}
    data['effect_graph_controls'] = controls
    return Project(data, project.base)


@pytest.mark.parametrize('output', ['grayscale', 'alpha', 'color', 'vector'])
def test_native_field_phase_is_seekable_and_clock_owned(project, output):
    control = dict(node='f', property='field_phase', motion=dict(kind='named', pattern='circle'),
                   axis='x', range=[-1, 2])
    p = configured(project, dict(field_type='fbm', field_output=output, field_vector_gain=32), [control])
    saved = deepcopy(p.data)
    comp = Compositor(p)
    try:
        frames = (0, 7, 11, 19, 37, 73)
        values = [comp.effect_graph_controls.values(comp.context(f), comp.effect_graph)[('f', 'field_phase')] for f in frames]
        assert max(values)-min(values) > .01
        images = {f: comp.frame(f, apply_quality=False)[0].tobytes() for f in frames}
        assert len(set(images.values())) > 1
        for f in reversed(frames):
            assert comp.frame(f, apply_quality=False)[0].tobytes() == images[f]
        assert p.data == saved
    finally:
        comp.close()


def test_saved_native_frequency_lane_drives_field_without_audio_analysis(project):
    recording = deepcopy(next(layer['motion'] for layer in project.data['layers'] if layer['id'] == 'bands'))
    assert recording['kind'] == 'recording'
    control = dict(node='f', property='field_phase', motion=recording, axis='low', range=[0, 3])
    comp = Compositor(configured(project, dict(field_type='noise', field_output='color'), [control]))
    try:
        frames = (0, 7, 11, 19, 37, 73)
        values = [comp.effect_graph_controls.values(comp.context(f), comp.effect_graph)[('f', 'field_phase')] for f in frames]
        assert max(values)-min(values) > .01
        assert comp.effect_graph_controls.files
        images = {f: comp.frame(f, apply_quality=False)[0].tobytes() for f in frames}
        assert len(set(images.values())) > 1
        assert comp.frame(11, apply_quality=False)[0].tobytes() == images[11]
    finally:
        comp.close()


def test_native_motions_drive_periodic_julia_parameters(project):
    controls = [
        dict(node='f', property='field_julia_phase',
             motion=dict(kind='named', pattern='akwf_round_saw'), axis='x', range=[-1, 1]),
        dict(node='f', property='field_julia_zoom',
             motion=dict(kind='named', pattern='lfo_breathe'), axis='x', range=[.85, 1.35]),
        dict(node='f', property='field_julia_rotation',
             motion=dict(kind='named', pattern='sway'), axis='x', range=[-.12, .12]),
        dict(node='f', property='field_julia_orbit_radius',
             motion=dict(kind='named', pattern='lfo_breathe'), axis='x', range=[.08, .24]),
    ]
    config = dict(
        field_type='julia', field_output='color', field_scale=[52, 52],
        field_julia_c=[-.745, .113], field_julia_iterations=48,
        field_palette=[
            dict(id='void', position=0, color='#05040f'),
            dict(id='violet', position=.35, color='#6d28d9'),
            dict(id='cyan', position=.68, color='#22d3ee'),
            dict(id='white', position=1, color='#fff7ed'),
        ],
    )
    comp = Compositor(configured(project, config, controls))
    try:
        frames = (0, 15, 30, 45, 60, 75, 90, 105, 120)
        values = [comp.effect_graph_controls.values(comp.context(f), comp.effect_graph) for f in frames]
        for row in values:
            assert -1 <= row[('f', 'field_julia_phase')] <= 1
            assert .85 <= row[('f', 'field_julia_zoom')] <= 1.35
            assert -.12 <= row[('f', 'field_julia_rotation')] <= .12
            assert .08 <= row[('f', 'field_julia_orbit_radius')] <= .24
        images = {f: comp.frame(f, apply_quality=False)[0].tobytes() for f in frames}
        assert len(set(images.values())) >= 6
        assert comp.frame(30, apply_quality=False)[0].tobytes() == images[30]
    finally:
        comp.close()


def test_native_palette_and_categorical_controls(project):
    controls = [dict(node='f', property='field_type', motion=dict(kind='named', pattern='circle'),
                     axis='x', choices=['linear', 'noise', 'cellular']),
                dict(node='f', property='field_palette.start.color.r', motion=dict(kind='named', pattern='circle'),
                     axis='x', range=[0, 255])]
    comp = Compositor(configured(project, dict(field_output='color'), controls))
    try:
        frames = (0, 7, 11, 19, 37, 73)
        samples = [comp.effect_graph_controls.values(comp.context(f), comp.effect_graph) for f in frames]
        assert len({s[('f', 'field_type')] for s in samples}) > 1
        assert len({s[('f', 'field_palette.start.color.r')] for s in samples}) > 1
        assert all(comp.frame(f, apply_quality=False)[0].size == (180, 320) for f in frames)
    finally:
        comp.close()


def test_native_transition_chain_field_controls(project):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [dict(id='background', type='source'),
        dict(id='chain', type='transition_chain', inputs=['background', 'background'],
             input_effects=[[dict(id='field', type='procedural_field', field_type='stripes')], []],
             stages=[dict(id='fade', progress=0)])], 'output':'chain'}
    target = 'chain::input::0::field'
    data['effect_graph_controls'] = [dict(node=target, property='field_phase',
        motion=dict(kind='named', pattern='circle'), axis='x', range=[0, .75])]
    comp = Compositor(Project(data, project.base))
    try:
        assert target in comp.effect_graph.field_manifest()
        frames = (0, 7, 11, 19, 37, 73)
        assert len({comp.frame(f, apply_quality=False)[0].tobytes() for f in frames}) > 1
    finally:
        comp.close()


def test_generator_uses_existing_temporal_supersampling_path(project):
    p = configured(project, dict(field_type='noise'), [dict(node='f', property='field_phase',
        motion=dict(kind='named', pattern='circle'), axis='x', range=[-1, 2])])
    data = deepcopy(p.data)
    data['render_quality'] = dict(enabled=True, spatial_scale=1, temporal_samples=2)
    comp = Compositor(Project(data, project.base))
    try:
        first = comp.frame(7, apply_quality=False)[0]
        comp.frame(19, apply_quality=False)
        assert comp.frame(7, apply_quality=False)[0].tobytes() == first.tobytes()
    finally:
        comp.close()


def test_legacy_final_frame_identity_does_not_change(project):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [dict(id='frame', type='source')], 'output':'frame'}
    legacy = Compositor(project)
    identity = Compositor(Project(data, project.base))
    try:
        for frame in (0, 7, 19):
            assert legacy.frame(frame, apply_quality=False)[0].tobytes() == identity.frame(frame, apply_quality=False)[0].tobytes()
    finally:
        identity.close()
        legacy.close()
