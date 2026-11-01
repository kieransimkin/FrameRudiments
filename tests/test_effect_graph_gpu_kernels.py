"""Required M8.3 GPU parity; CI must execute with a working ModernGL/EGL context."""
import numpy as np
import pytest
from PIL import Image

moderngl = pytest.importorskip('moderngl')

from framerudiments.effect_graph import EffectGraph  # noqa: E402
from framerudiments.effect_graph_filter import render_filter  # noqa: E402
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported  # noqa: E402
from framerudiments.effect_graph_transitions import render_transition  # noqa: E402
from framerudiments.effect_graph import _chromatic_displacement  # noqa: E402


@pytest.fixture(scope='module')
def backend():
    with GLMatteBackend(backend='egl') as gpu:
        yield gpu


def images(size=(49,33)):
    h,w=size[1],size[0]
    y,x=np.indices((h,w))
    src=np.stack([(x*13+y*7)%256,(y*9+x*5)%256,(x*7+y*17)%256,
                  (x*27+y*11)%256],axis=-1).astype('uint8')
    src[3:10,4:13,:3]=[255,6,177]
    src[3:10,4:13,3]=0
    dst=np.stack([(y*15+x*11)%256,(x*23)%256,(x*4+y*9)%256,
                  255-(x*7+y*9)%256],axis=-1).astype('uint8')
    return Image.fromarray(src,'RGBA'),Image.fromarray(dst,'RGBA')


def near(image_a,image_b, limit):
    a,b=np.asarray(image_a,dtype=np.int16),np.asarray(image_b,dtype=np.int16)
    delta=np.abs(a-b)
    # Transparent RGB is not meaningful. The renderer canonicalizes it only
    # for filters, not for legacy colour transformations or transitions.
    valid=(a[...,3]>2)&(b[...,3]>2)
    assert delta[...,3].max()<=2
    assert delta[...,:3][valid].max(initial=0)<=limit


@pytest.mark.parametrize('style,progress,feather,direction', [
    ('dissolve',.27,0,'right'),('wipe',.45,0,'left'),('wipe',.37,.18,'down'),
    ('wipe',.62,.31,'up'),('radial',.42,0,'right'),('radial',.58,.22,'right'),
])
def test_gpu_transition_matches_cpu(backend,style,progress,feather,direction):
    a,b=images()
    cfg={'transition_style':style,'progress':progress,'feather':feather,
         'direction':direction,'center':[.22,.71]}
    backend.begin_transfer_sample()
    result=backend.render_transition(a,b,cfg)
    near(result,render_transition(a,b,cfg),3)
    assert backend.last_transport['uploads']==2
    assert backend.last_transport['readbacks']==1
    assert backend.last_transport['upload_bytes']==a.width*a.height*8
    assert a.getpixel((3,4))==images()[0].getpixel((3,4))


@pytest.mark.parametrize('p', [0,1])
def test_gpu_transition_endpoint_exact_no_transfer(backend,p):
    a,b=images()
    backend.begin_transfer_sample()
    assert backend.render_transition(a,b,{'progress':p}).tobytes()==(a if p==0 else b).tobytes()
    assert backend.last_transport['uploads']==0
    assert backend.last_transport['readbacks']==0


@pytest.mark.parametrize('offsets',[{'red_offset':[1.25,-1.5]},
                                     {'red_offset':[-2.25,3.5], 'green_offset':[.5,.5],
                                      'blue_offset':[0,-2]},
                                     {'blue_offset':[12,-7]}])
def test_gpu_chromatic_legacy_parity(backend,offsets):
    source,_=images()
    backend.begin_transfer_sample()
    near(backend.chromatic_displacement(source,offsets),_chromatic_displacement(source,offsets),2)
    assert backend.last_transport['uploads']==1
    assert backend.last_transport['readbacks']==1


@pytest.mark.parametrize('kind, radius,border,threshold,strength', [
    ('blur',.35,'clamp',.7,1),('blur',1.5,'transparent',.7,1),
    ('blur',3.5,'clamp',.7,1),('bloom',1.5,'transparent',.6,1.2),
    ('bloom',3,'clamp',.3,.7),('bloom',0,'transparent',.8,2),
])
def test_gpu_blur_and_bloom_parity(backend,kind,radius,border,threshold,strength):
    source,_=images()
    config=dict(filter_type=kind,filter_radius=radius,filter_border=border,
                filter_threshold=threshold,filter_strength=strength,filter_mix=.75)
    backend.begin_transfer_sample()
    out=backend.render_filter(source,config)
    reference=render_filter(source,config)
    near(out,reference,5)
    assert backend.last_transport['uploads']==1
    assert backend.last_transport['readbacks']==1


def test_gpu_filter_in_graph_and_stable_alpha(backend):
    a,b=images()
    g=EffectGraph({'nodes':[
        {'id':'a','type':'source'}, {'id':'b','type':'source'},
        {'id':'blur','type':'filter','inputs':['a'],'filter_radius':2},
        {'id':'fade','type':'transition','inputs':['blur','b'],'progress':.5},
    ],'output':'fade'})
    before_a=a.tobytes()
    out=g.render({'a':a,'b':b},gpu_backend=backend,gpu_strict=True)
    near(out,g.render({'a':a,'b':b}),6)
    assert backend.last_dispatch=={'gpu_nodes':2,'cpu_fallbacks':0}
    assert backend.last_transport['uploads']==3
    assert backend.last_transport['readbacks']==2
    assert a.tobytes()==before_a


def test_gpu_budget_rejected_without_uploads():
    src,_=images()
    with GLMatteBackend(backend='egl',max_working_set_bytes=128) as backend:
        with pytest.raises(GPUUnsupported,match='working-set'):
            backend.render_filter(src,{'filter_radius':2})
        assert backend.last_transport['uploads']==0
        assert backend.last_transport['readbacks']==0


def test_unsupported_transition_does_not_silently_cpu_execute(backend):
    a,b=images()
    with pytest.raises(GPUUnsupported):
        backend.render_transition(a,b,{'transition_style':'liquid','progress':.5})
