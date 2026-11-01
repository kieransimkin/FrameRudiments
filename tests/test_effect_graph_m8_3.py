"""M8.3 CPU reference, graph dispatch, control and resource contract checks."""
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_filter import (
    filter_capabilities, filter_parameter_descriptors, render_filter, validate_filter,
)
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_backend_contract import (
    SurfaceContract, gpu_requirements, plan_effect_graph,
)
from framerudiments.effect_graph_transitions import render_transition


def image(size=(27, 19)):
    y, x = np.indices((size[1], size[0]))
    rgba = np.stack([(x*13+y*7)%256,(x*3+y*19)%256,(x*23+y)%256,
                     (x*21+y*14)%256], axis=-1).astype('uint8')
    rgba[2:5,5:10,:3] = [255,17,222]
    rgba[2:5,5:10,3] = 0
    return Image.fromarray(rgba,'RGBA')


def graph(kind, config=None):
    config = config or {}
    specs = [{'id':'a','type':'source'}, {'id':'b','type':'source'}]
    specs.append({'id':'out','type':kind,'inputs':['a','b'] if kind=='transition' else ['a'], **config})
    return EffectGraph({'nodes':specs,'output':'out'})


@pytest.mark.parametrize('kind', ['blur','bloom'])
@pytest.mark.parametrize('radius', [0, .5, 1.5, 3.5, 4])
@pytest.mark.parametrize('border', ['clamp','transparent'])
def test_cpu_filter_reproducible_and_does_not_mutate_source(kind,radius,border):
    source=image()
    before=source.tobytes()
    config=dict(filter_type=kind,filter_radius=radius,filter_border=border)
    a=render_filter(source,config)
    b=render_filter(source,config)
    assert a.mode=='RGBA' and a.size==source.size
    assert a.tobytes()==b.tobytes()
    assert source.tobytes()==before
    if kind=='blur' and radius==0:
        assert a.tobytes()==before


@pytest.mark.parametrize('bad', [
    {'filter_radius':-1},{'filter_radius':4.01},{'filter_radius':float('nan')},
    {'filter_threshold':1},{'filter_strength':5},{'filter_mix':-1},
    {'filter_border':'wrap'},{'filter_type':'glow'},{'filter_radius':True},
    {'filter_threshold':float('inf')},{'unregistered':10},
])
def test_invalid_filter_rejected_early(bad):
    with pytest.raises(ValueError):
        validate_filter(bad,1)
    with pytest.raises(ValueError):
        graph('filter',bad)


def test_blur_does_not_bleed_hidden_transparent_rgb():
    a=np.zeros((13,13,4),dtype='uint8')
    a[6,6]=[40,90,210,255]
    b=a.copy()
    b[2:4,3:7,:3]=[255,0,255]
    for mode in ['clamp','transparent']:
        conf=dict(filter_type='blur',filter_radius=2,filter_border=mode)
        assert render_filter(Image.fromarray(a,'RGBA'),conf).tobytes()==render_filter(Image.fromarray(b,'RGBA'),conf).tobytes()


def test_bloom_threshold_and_transparency():
    arr=np.zeros((31,31,4),dtype='uint8')
    arr[15,15]=[255,200,90,255]
    src=Image.fromarray(arr,'RGBA')
    output=np.asarray(render_filter(src,dict(filter_type='bloom',filter_radius=2,filter_threshold=.6)))
    assert output[15,15,3]>0
    assert output[15,16,3]>0
    assert np.all(output[output[...,3]==0,:3]==0)
    assert render_filter(src,{'filter_type':'bloom','filter_strength':0}).tobytes()==src.tobytes()


def test_filter_control_schema_independent_and_validated():
    g=graph('filter',{'filter_type':'bloom','filter_threshold':.8})
    descriptors=g.control_descriptors('out')
    assert descriptors['filter_radius']['units']=='pixels'
    assert descriptors['filter_threshold']['active']
    assert descriptors['filter_border']['choices']==['clamp','transparent']
    assert g.is_categorical_control('out','filter_type')
    g.validate_control_choices('out','filter_type',['blur','bloom'])
    with pytest.raises(ValueError):
        g.validate_control_choices('out','filter_type',['blur','unknown'])
    source=image()
    base=g.render({'a':source})
    authored=deepcopy(g.nodes['out'].config)
    modified=g.render({'a':source},parameter_values={('out','filter_radius'):2.5,('out','filter_mix'):.75})
    assert base.tobytes()!=modified.tobytes()
    assert g.nodes['out'].config==authored
    assert g.filter_manifest()['out']['gpu_native']
    assert filter_parameter_descriptors({})['filter_threshold']['active'] is False
    assert filter_capabilities({})['gpu_resident_graph'] is False


def test_filter_can_preprocess_transition_chain():
    g=EffectGraph({'nodes':[
        {'id':'a','type':'source'}, {'id':'b','type':'source'},
        {'id':'chain','type':'transition_chain','inputs':['a','b'],
         'input_effects':[[{'id':'blur','type':'filter','filter_type':'blur','filter_radius':2}],[]],
         'stages':[{'id':'cross','transition_style':'dissolve','progress':.3}]},
    ],'output':'chain'})
    assert g.nodes['chain::input::0::blur'].kind=='filter'
    result=g.render({'a':image(),'b':Image.new('RGBA',image().size,'#ff000099')})
    assert result.mode=='RGBA'


class ProxyGPU:
    def __init__(self,fail=False):
        self.fail=fail
        self.seen=[]
    def render_transition(self,a,b,config):
        self.seen.append('transition')
        if self.fail:
            raise GPUUnsupported('out of budget')
        return render_transition(a,b,config)
    def chromatic_displacement(self,a,config):
        self.seen.append('chromatic')
        if self.fail:
            raise GPUUnsupported('out of budget')
        from framerudiments.effect_graph import _chromatic_displacement
        return _chromatic_displacement(a,config)
    def render_filter(self,a,config):
        self.seen.append('filter')
        if self.fail:
            raise GPUUnsupported('out of budget')
        return render_filter(a,config)


@pytest.mark.parametrize('kind,cfg', [
    ('transition',{'transition_style':'dissolve','progress':.4}),
    ('transition',{'transition_style':'wipe','progress':.6,'feather':.1}),
    ('transition',{'transition_style':'radial','progress':.6,'center':[.2,.7]}),
    ('chromatic_displacement',{'red_offset':[1.25,-2],'green_offset':[0.5,1]}),
    ('filter',{'filter_type':'blur','filter_radius':2}),
    ('filter',{'filter_type':'bloom','filter_radius':1.5}),
])
def test_dispatch_and_auto_fallback(kind,cfg):
    g=graph(kind,cfg)
    src={'a':image(),'b':Image.new('RGBA',image().size,(20,200,60,70))}
    baseline=g.render(src)
    gpu=ProxyGPU()
    assert baseline.tobytes()==g.render(src,gpu_backend=gpu,gpu_strict=True).tobytes()
    assert gpu.seen==[kind if kind!='chromatic_displacement' else 'chromatic']
    assert gpu.last_dispatch=={'gpu_nodes':1,'cpu_fallbacks':0}
    no=ProxyGPU(fail=True)
    assert baseline.tobytes()==g.render(src,gpu_backend=no).tobytes()
    assert no.last_dispatch=={'gpu_nodes':0,'cpu_fallbacks':1}
    with pytest.raises(GPUUnsupported):
        g.render(src,gpu_backend=ProxyGPU(fail=True),gpu_strict=True)


def test_unsupported_transition_gpu_auto_and_strict():
    g=graph('transition',{'transition_style':'liquid','progress':.6})
    sources={'a':image(),'b':image()}
    fake=ProxyGPU()
    assert g.render(sources,gpu_backend=fake).tobytes()==g.render(sources).tobytes()
    assert fake.seen==[]
    assert fake.last_dispatch=={'gpu_nodes':0,'cpu_fallbacks':1}
    with pytest.raises(GPUUnsupported):
        g.render(sources,gpu_backend=fake,gpu_strict=True)


@pytest.mark.parametrize('kind,cfg,count,textures,uploads,draws', [
    ('transition',{'transition_style':'dissolve','progress':.5},2,3,2,1),
    ('transition',{'transition_style':'radial','progress':0},2,0,0,0),
    ('transition',{'transition_style':'liquid','progress':.5},2,0,0,0),
    ('chromatic_displacement',{},1,0,0,0),
    ('chromatic_displacement',{'red_offset':[.5,0]},1,2,1,1),
    ('filter',{'filter_type':'blur','filter_radius':2},1,4,1,3),
    ('filter',{'filter_type':'bloom','filter_radius':2},1,5,1,4),
    ('filter',{'filter_type':'bloom','filter_mix':0},1,0,0,0),
])
def test_resource_contract_matches_actual_passes(kind,cfg,count,textures,uploads,draws):
    r=gpu_requirements(kind,cfg,count)
    assert (r.texture_count,r.upload_count,r.draw_passes)==(textures,uploads,draws)
    assert r.supported == (cfg.get('transition_style') != 'liquid')
    if textures:
        assert r.readback_count==1


def test_planner_selects_new_kernels_and_accounts_for_transfer():
    from framerudiments.effect_graph_gpu import GLMatteBackend
    g=graph('transition',{'transition_style':'wipe','progress':.4})
    class Context:
        version_code=330
        info={'GL_MAX_TEXTURE_SIZE':8192,'GL_RENDERER':'fake-gl','GL_VENDOR':'test'}
    backend=object.__new__(GLMatteBackend)
    backend.ctx=Context()
    backend.closed=False
    backend.max_working_set_bytes=1024**3
    plan=plan_effect_graph(g,{'a':SurfaceContract(32,20),'b':SurfaceContract(32,20)},mode='gpu',gpu_backend=backend)
    row=plan['nodes']['out']
    assert row['selected_backend']=='gpu_bridge'
    assert row['upload_bytes']==2*32*20*4
    assert row['readback_bytes']==32*20*4
    assert row['gpu_kernel']['draw_passes']==1
    assert plan['gpu_resident_graph'] is False
    assert plan['new_kernels'] is True


def test_identity_does_not_allocate_or_upload():
    source=image()
    for kind,config in [('transition',{'progress':0}),('chromatic_displacement',{}),
                        ('filter',{'filter_type':'blur','filter_radius':0})]:
        g=graph(kind,config)
        gpu=ProxyGPU()
        assert g.render({'a':source,'b':source},gpu_backend=gpu,gpu_strict=True).tobytes()==source.tobytes()
        assert gpu.seen==[]
