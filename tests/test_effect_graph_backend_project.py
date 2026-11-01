"""Real Project/Compositor/DanceRudiment acceptance for allocation-free plans."""
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
    choices = [Path(supplied)] if supplied else [Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                                               Path('C:/Windows/Fonts/arial.ttf')]
    font = next((p for p in choices if p.is_file()), None)
    if font is None:
        if supplied:
            pytest.fail('FRAMERUDIMENTS_TEST_FONT does not exist')
        pytest.skip('Installed TTF required; CI/installer supply one')
    original = Project.load(create_demo(tmp_path / 'backend-demo', str(font), width=180, height=320))
    data = deepcopy(original.data)
    data['render_quality'] = {'enabled': False}
    data['effect_graph'] = {'nodes': [dict(id='frame', type='source'),
        dict(id='field', type='procedural_field', inputs=['frame'], field_type='noise'),
        dict(id='out', type='matte', inputs=['frame', 'field'], matte_channel='luminance')], 'output': 'out'}
    data['effect_graph_controls'] = [dict(node='field', property='field_phase',
        motion=dict(kind='named', pattern='circle'), axis='x', range=[0, 2])]
    return Project(data, original.base)


@pytest.mark.parametrize('frame', [0, 7, 11, 37])
def test_real_plan_uses_native_values_without_changing_render_or_authored_data(project, frame):
    original = deepcopy(project.data)
    comp = Compositor(project)
    try:
        graph = comp.effect_graph
        context = comp.context(frame)
        values = comp.effect_graph_controls.values(context, graph)
        before = comp.frame(frame, apply_quality=False)[0].tobytes()
        plan = graph.backend_plan({'frame': (project.width, project.height)},
                                  parameter_values=values, context=context)
        assert plan['eligible']
        assert plan['nodes']['field']['parameters']['field_phase'] == values[('field', 'field_phase')]
        assert plan['nodes']['out']['alpha_owner'] == 'frame'
        assert comp.frame(frame, apply_quality=False)[0].tobytes() == before
        assert project.data == original
    finally:
        comp.close()


def test_real_seeked_values_produce_different_plans_with_no_history(project):
    comp = Compositor(project)
    try:
        def plan(frame):
            ctx = comp.context(frame)
            return comp.effect_graph.backend_plan({'frame': (180, 320)},
                parameter_values=comp.effect_graph_controls.values(ctx, comp.effect_graph), context=ctx)
        frames = (0, 7, 11, 19, 37, 73)
        plans = {f: plan(f) for f in frames}
        assert len({p['nodes']['field']['parameters']['field_phase'] for p in plans.values()}) > 1
        for f in reversed(frames):
            assert plan(f) == plans[f]
    finally:
        comp.close()


def test_saved_recording_drives_planning_through_the_existing_adapter(project):
    data = deepcopy(project.data)
    recording = next(x['motion'] for x in data['layers'] if x['id'] == 'bands')
    data['effect_graph_controls'][0].update(motion=recording, axis='low')
    comp = Compositor(Project(data, project.base))
    try:
        plans = []
        for frame in (0, 7, 19, 37, 73):
            ctx = comp.context(frame)
            values = comp.effect_graph_controls.values(ctx, comp.effect_graph)
            plans.append(comp.effect_graph.backend_plan({'frame': (180, 320)}, parameter_values=values, context=ctx))
        assert len({p['nodes']['field']['parameters']['field_phase'] for p in plans}) > 1
        assert comp.effect_graph_controls.files
    finally:
        comp.close()


def test_temporal_virtual_graph_can_be_planned_without_replacing_its_clock(project):
    data = deepcopy(project.data)
    data['render_quality'] = dict(enabled=True, spatial_scale=1, temporal_samples=2)
    comp = Compositor(Project(data, project.base))
    try:
        image = comp.frame(7, apply_quality=False)[0]
        virtual = comp._temporal_virtual
        ctx = virtual.context(14)
        values = virtual.effect_graph_controls.values(ctx, virtual.effect_graph)
        plan = virtual.effect_graph.backend_plan({'frame': (180, 320)}, parameter_values=values, context=ctx)
        assert plan['eligible'] and plan['context_supplied']
        assert comp.frame(7, apply_quality=False)[0].tobytes() == image.tobytes()
    finally:
        comp.close()
