"""M11.3 exact planning and native-pixel regressions (no GPU required)."""
from dataclasses import dataclass
from fractions import Fraction
from types import SimpleNamespace
import json

import pytest
from PIL import Image

from framerudiments.temporal_fx_m11 import NativeTemporalFX, temporal_fx_bounds


@dataclass
class Project:
    frame_count: int = 30
    width: int = 16
    height: int = 8
    start_sample: int = 0
    end_sample: int = 600
    sample_rate: int = 100
    fps: int = 1


def fx(options, project=None):
    return NativeTemporalFX(project or Project(), options, lambda i: None)


def frame(n, size=(16,8)):
    return Image.new('RGB', size, (n*11 % 255, n*17 % 255, n*23 % 255))


def run(owner, index):
    plan=owner.plan(index)
    pixels={k:frame(k,owner.size) for k in plan.source_frames}
    return owner.apply(pixels,plan,SimpleNamespace(source_seconds=index))[0]


def sample_color(result,x=0,y=0):return result.getpixel((x,y))


def test_empty_unknown_and_malformed_are_rejected():
    for doc in ({}, {'unknown':{}},{'motion_echoes':[]},{'slit_scan':{'axis':'z'}},
                {'time_remap':{'output_start':0}}, {'time_remap':{'rate':[1,0], 'output_end':20}},
                {'directional_blur':{'strength':float('nan')}},
                {'motion_echoes':{'delay_frames':True}},
                {'motion_echoes':{'count':12,'delay_frames':10}},
                {'slit_scan':{'reverse':1}},
                {'motion_echoes':{},'modulations':[{'target':'temporal_fx.slit_scan.strength'}]}):
        with pytest.raises(ValueError):
            fx(doc)


def test_exact_repeat_cuts_off_history_without_clamp_leak():
    owner=fx({'motion_echoes':{'count':3,'delay_frames':1,'strength':1}})
    assert owner.plan(5).by_role()['echo:1'] == ((4,Fraction(1)),)
    assert owner.plan(6).by_role()['echo:1'] == ()
    assert run(owner,6).tobytes()==frame(6).tobytes()


def test_rational_repeat_resets_on_first_integer_after_boundary():
    project=Project(end_sample=49,sample_rate=10,fps=1)
    owner=fx({'motion_echoes':{'count':2,'delay_frames':1,'strength':.7}},project)
    assert owner.plan(4).by_role()['echo:1']
    assert owner.plan(5).by_role()['echo:1']==()
    assert owner.plan(10).by_role()['echo:1']==()
    assert owner.plan(15).by_role()['echo:1']==()


def test_programme_boundary_permits_previous_cycle():
    owner=fx({'boundary':'programme','motion_echoes':{'count':1,'delay_frames':1,'strength':1}})
    assert owner.plan(6).by_role()['echo:1'][0][0]==5


def test_half_speed_linear_remap_uses_two_observed_frames():
    owner=fx({'time_remap':{'output_start':0,'output_end':6,'source_start':0,'rate':[1,2]}})
    p=owner.plan(3)
    assert p.by_role()['base']==((1,Fraction(1,2)),(2,Fraction(1,2)))
    assert p.source_frames==(1,2)
    r=run(owner,3).getpixel((0,0))
    assert all(a<=v<=b for a,v,b in zip(frame(1).getpixel((0,0)),r,frame(2).getpixel((0,0))))


def test_exact_hold_and_reverse_rate():
    hold=fx({'time_remap':{'output_start':0,'output_end':6,'source_start':4,'rate':[0,1]}})
    assert hold.plan(0).source_frames==(4,)
    assert hold.plan(5).source_frames==(4,)
    backwards=fx({'boundary':'programme','time_remap':{'output_start':1,'output_end':5,'source_start':9,'rate':[-2,1]}})
    assert backwards.plan(2).source_frames==(7,)
    assert backwards.plan(4).source_frames==(3,)
    assert backwards.plan(5).source_frames==(5,)


def test_remap_respects_repeat_boundary_without_wrapping_into_next_cycle():
    owner=fx({'time_remap':{'output_start':1,'output_end':6,'source_start':5,'rate':[1,1]}})
    assert owner.plan(4).source_frames==(5,)
    assert owner.plan(5).source_frames==(5,)
    assert owner.plan(6).source_frames==(6,)


def test_nearest_rounding_and_fraction_plan_json():
    owner=fx({'time_remap':{'output_start':0,'output_end':6,'source_start':0,'rate':[1,2],
                            'interpolation':'nearest'}})
    assert owner.plan(1).source_frames==(1,)
    doc=owner.plan(1).document()
    assert json.loads(json.dumps(doc))==doc
    assert doc['roles'][0]['sources'][0]['weight']=={'numerator':1,'denominator':1}


def test_motion_echoes_change_pixels_without_touching_input():
    owner=fx({'boundary':'programme','motion_echoes':{
        'delay_frames':1,'count':2,'strength':.8,'decay':.5,'shift_x':0,'shift_y':0}})
    plan=owner.plan(7)
    frames={k:frame(k) for k in plan.source_frames}
    before={k:im.tobytes() for k,im in frames.items()}
    result,report=owner.apply(frames,plan,SimpleNamespace(source_seconds=7))
    assert result.tobytes()!=frame(7).tobytes()
    assert before=={k:im.tobytes() for k,im in frames.items()}
    assert report['stages'][0]['count']==2


def test_source_reads_deduplicated_for_two_effect_families():
    owner=fx({'motion_echoes':{'count':3,'delay_frames':1},
              'directional_blur':{'samples':4,'history_frames':3},
              'slit_scan':{'strips':8,'history_frames':3}})
    plan=owner.plan(5)
    assert plan.source_frames==(2,3,4,5)
    assert plan.limits['unique_frame_taps']==4


def test_spatial_directional_sample_is_moved_towards_angle():
    owner=fx({'boundary':'programme', 'directional_blur':{
        'samples':2,'history_frames':1,'angle_degrees':0,'distance_px':2,'strength':1}})
    plan=owner.plan(2)
    frames={k:Image.new('RGB',owner.size,'black') for k in plan.source_frames}
    frames[1].putpixel((2,2),(250,0,0))
    result,_=owner.apply(frames,plan,SimpleNamespace(source_seconds=2))
    assert result.getpixel((4,2))[0]>0
    assert result.getpixel((1,2))[0]==0


def test_slit_scan_reads_different_times_for_vertical_slices():
    owner=fx({'boundary':'programme','slit_scan':{'axis':'x','strips':4,
                                                 'history_frames':3,'strength':1}})
    out=run(owner,6)
    assert sample_color(out,0,0)==frame(6).getpixel((0,0))
    assert sample_color(out,14,0)==frame(3).getpixel((0,0))
    assert sample_color(out,8,0)==frame(4).getpixel((0,0))


def test_reverse_slit_y_moves_past_to_top():
    owner=fx({'boundary':'programme','slit_scan':{'axis':'y','strips':4,
                                                 'history_frames':3,'reverse':True,'strength':1}})
    out=run(owner,6)
    assert sample_color(out,0,0)==frame(3).getpixel((0,0))
    assert sample_color(out,0,7)==frame(6).getpixel((0,0))


def test_stage_order_is_stable_and_shuffled_frame_order_is_pure():
    owner=fx({'boundary':'programme','time_remap':{'output_start':1,'output_end':12,
            'source_start':0,'rate':[1,2]},'motion_echoes':{'count':2,'delay_frames':1},
            'directional_blur':{'samples':3,'history_frames':2},
            'slit_scan':{'strips':4,'history_frames':3}})
    expected={n:run(owner,n).tobytes() for n in range(1,10)}
    for n in (9,3,6,2,4,8,1,7,5):
        assert run(owner,n).tobytes()==expected[n]


def test_modulated_strength_routes_through_same_native_modulator_contract():
    owner=fx({'motion_echoes':{'count':1,'delay_frames':1,'strength':0}})
    # The native adapter is separately tested by the Project/Compositor
    # integration cases; here we exercise the strength-combination contract
    # using an explicit synthetic output, without a fake recording file.
    owner.drivers['temporal_fx.motion_echoes.strength'] = SimpleNamespace(
        output=lambda _context: (1, {'synthetic': True}), combine='set', weight=1)
    plan=owner.plan(2)
    images={k:frame(k) for k in plan.source_frames}
    result,report=owner.apply(images,plan,SimpleNamespace(source_seconds=2))
    assert result.tobytes()==frame(1).tobytes()
    assert report['modulations']['temporal_fx.motion_echoes.strength']['value']==1


def test_conservative_invalidation_bounds_include_temporal_and_remap():
    assert temporal_fx_bounds({'motion_echoes':{'count':2,'delay_frames':3},
        'directional_blur':{'history_frames':4}, 'slit_scan':{'history_frames':5}})==(6,0)
    past,future=temporal_fx_bounds({'motion_echoes':{'count':2,'delay_frames':3},
        'time_remap':{'output_start':20,'output_end':28,'source_start':15,'rate':[1,2]}})
    assert past>=10 and future>=1


def test_invalid_or_missing_tap_fails_closed():
    owner=fx({'motion_echoes':{'count':2}})
    plan=owner.plan(3)
    with pytest.raises(ValueError):
        owner.apply({},plan,SimpleNamespace(source_seconds=3))
    with pytest.raises(ValueError):
        owner.apply({k:Image.new('RGBA', owner.size) for k in plan.source_frames},
                    plan, SimpleNamespace(source_seconds=3))


def test_resource_budget_rejects_wide_frames_pre_render():
    project=Project(width=7680,height=4320)
    owner=fx({'slit_scan':{'strips':24,'history_frames':24}},project)
    with pytest.raises(ValueError,match='budget'):
        owner.plan(25)


def test_large_future_remap_is_bounded_to_programme():
    owner=fx({'boundary':'programme','time_remap':{'output_start':1,'output_end':30,
        'source_start':29,'rate':[20,1]}})
    assert owner.plan(29).source_frames==(29,)


def test_input_order_never_changes_native_source_plan():
    owner=fx({'motion_echoes':{'count':3,'delay_frames':2},
              'slit_scan':{'strips':6,'history_frames':5}})
    plan=owner.plan(7)
    a={k:frame(k) for k in plan.source_frames}
    b=dict(reversed(list(a.items())))
    x=owner.apply(a,plan,SimpleNamespace(source_seconds=7))[0].tobytes()
    y=owner.apply(b,plan,SimpleNamespace(source_seconds=7))[0].tobytes()
    assert x==y
