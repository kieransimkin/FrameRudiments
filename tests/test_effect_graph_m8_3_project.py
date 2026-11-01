"""Real native DanceRudiments integration for M8.3; no fake motion driver."""
from copy import deepcopy
import os
from pathlib import Path

import pytest
from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def project(tmp_path):
    preferred = os.getenv('FRAMERUDIMENTS_TEST_FONT')
    fonts = ([Path(preferred)] if preferred else
             [Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),Path('C:/Windows/Fonts/arial.ttf')])
    font = next((p for p in fonts if p.is_file()),None)
    if font is None:
        if preferred:
            pytest.fail('Supplied native test font is missing')
        pytest.skip('A pinned native TrueType font is required')
    source = Project.load(create_demo(tmp_path / 'm83-demo',str(font),width=160,height=272))
    data = deepcopy(source.data)
    data['render_quality']={'enabled':False}
    data['effect_graph']={'nodes':[
        {'id':'frame','type':'source'},
        {'id':'blur','type':'filter','inputs':['frame'], 'filter_type':'blur',
         'filter_radius':2, 'filter_mix':1},
    ],'output':'blur'}
    data['effect_graph_controls']=[{'node':'blur','property':'filter_radius',
        'motion':{'kind':'named','pattern':'circle'},'axis':'x','range':[.5,3]}]
    return Project(data,source.base)


def test_filter_real_controls_and_repeatable_random_access(project):
    authored=deepcopy(project.data)
    comp=Compositor(project)
    try:
        frames=(0,7,11,19,31,47,73)
        values={}
        images={}
        for frame in frames:
            ctx=comp.context(frame)
            values[frame]=comp.effect_graph_controls.values(ctx,comp.effect_graph)[('blur','filter_radius')]
            plan=comp.effect_graph.backend_plan({'frame':(project.width,project.height)},
                      parameter_values={('blur','filter_radius'):values[frame]},context=ctx)
            assert plan['nodes']['blur']['parameters']['filter_radius']==values[frame]
            images[frame]=comp.frame(frame,apply_quality=False)[0].tobytes()
        assert len(set(values.values()))>1
        for frame in reversed(frames):
            assert comp.frame(frame,apply_quality=False)[0].tobytes()==images[frame]
        assert project.data==authored
    finally:
        comp.close()


def test_filter_temporal_supersampling_uses_native_subframe_clock(project):
    data=deepcopy(project.data)
    data['render_quality']={'enabled':True,'spatial_scale':1,'temporal_samples':2}
    comp=Compositor(Project(data,project.base))
    try:
        first=comp.frame(11,apply_quality=False)[0]
        virtual=comp._temporal_virtual
        assert virtual is not None
        ctx=virtual.context(22)
        values=virtual.effect_graph_controls.values(ctx,virtual.effect_graph)
        assert ('blur','filter_radius') in values
        assert first.tobytes()==comp.frame(11,apply_quality=False)[0].tobytes()
    finally:
        comp.close()


def test_graph_transition_native_progress_reaches_backend_plan(project):
    data=deepcopy(project.data)
    data['effect_graph']={'nodes':[
        {'id':'frame','type':'source'},
        {'id':'background','type':'source'},
        {'id':'cross','type':'transition','inputs':['background','frame'],
         'transition_style':'radial','progress':.5},
    ],'output':'cross'}
    data['effect_graph_controls']=[{'node':'cross','property':'progress',
        'motion':{'kind':'named','pattern':'circle'},'axis':'y','range':[.1,.9]}]
    comp=Compositor(Project(data,project.base))
    try:
        samples=[]
        for frame in (0,7,19,37):
            ctx=comp.context(frame)
            resolved=comp.effect_graph_controls.values(ctx,comp.effect_graph)
            plan=comp.effect_graph.backend_plan({'frame':(project.width,project.height),
                       'background':(project.width,project.height)},
                       parameter_values=resolved,context=ctx)
            samples.append(plan['nodes']['cross']['parameters']['progress'])
            assert plan['nodes']['cross']['gpu_kernel']['supported']
            assert comp.frame(frame,apply_quality=False)[0].mode=='RGB'
        assert len(set(samples))>1
    finally:
        comp.close()
