"""Required shader parity; CI must execute with a working ModernGL/EGL context."""
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

moderngl = pytest.importorskip('moderngl')

from framerudiments.effect_graph import EffectGraph  # noqa: E402
from framerudiments.effect_graph_filter import render_filter  # noqa: E402
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported  # noqa: E402
from framerudiments.effect_graph_transitions import render_transition  # noqa: E402
from framerudiments.effect_graph import _chromatic_displacement  # noqa: E402
from framerudiments.gl_transitions import shader_catalogue  # noqa: E402


@pytest.fixture(scope='module')
def backend():
    with GLMatteBackend(backend='egl') as gpu:
        yield gpu


@pytest.fixture(scope='module')
def transition_backend():
    with GLMatteBackend(backend=None if sys.platform == 'win32' else 'egl') as gpu:
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


def gl_graph(shader_id, **config):
    shader = next(row for row in shader_catalogue() if row['id'] == shader_id)
    return EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'out', 'type': 'gl_transition', 'inputs': ['a', 'b'], 'shader': shader, **config},
    ], 'output': 'out'})


@pytest.mark.parametrize('progress', [0, 1])
def test_gl_transition_endpoint_bytes_and_zero_transfers(transition_backend, progress):
    backend = transition_backend
    a, b = images()
    graph = gl_graph('ripple-wipe', progress=progress)
    result = graph.render({'a': a, 'b': b}, gpu_backend=backend)
    assert result.tobytes() == (a if progress == 0 else b).tobytes()
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 0}
    assert backend.last_transport['uploads'] == backend.last_transport['readbacks'] == 0


def test_gl_transition_binds_uv_clock_and_project_timing(transition_backend):
    backend = transition_backend
    a, b = images((81, 41))
    sources = {'a': a, 'b': b}
    graph = gl_graph('ripple-wipe', timing={'clock': 'source', 'duration_beats': 4})
    t0 = SimpleNamespace(output_seconds=0, source_seconds=50, beat=5, source_beat=2)
    t1 = SimpleNamespace(output_seconds=2 / 3, source_seconds=50, beat=5, source_beat=2)
    first = graph.render(sources, gpu_backend=backend, context=t0)
    second = graph.render(sources, gpu_backend=backend, context=t1)
    assert graph.render(sources, gpu_backend=backend, context=t0).tobytes() == first.tobytes()
    for y, from_image, to_image in [(10, a, b), (30, b, a)]:
        assert first.getpixel((40, y)) == from_image.getpixel((40, y))
        assert second.getpixel((40, y)) == to_image.getpixel((40, y))
    assert first.getpixel((0, 10)) == b.getpixel((0, 10))
    assert first.getpixel((80, 10)) == a.getpixel((80, 10))
    assert backend.last_dispatch == {'gpu_nodes': 1, 'cpu_fallbacks': 0}
    assert backend.last_transport['uploads'] == 2
    assert backend.last_transport['readbacks'] == 1
    p = graph.backend_plan({key: im.size for key, im in sources.items()},
                           mode='auto', gpu_backend=backend, context=t0)
    assert p['eligible'] and p['nodes']['out']['selected_backend'] == 'gpu_bridge'
    assert p['nodes']['out']['alpha_owner'] == 'out'
    assert p['resources']['upload_bytes'] == a.width * a.height * 8
    assert graph.render(sources, gpu_backend=backend, context=t0,
                        parameter_values={('out', 'progress'): 1}).tobytes() == b.tobytes()


def test_gl_transition_ratio_and_alpha_association(transition_backend):
    size = (160, 80)
    a = Image.new('RGBA', size, (255, 0, 0, 0))
    b = Image.new('RGBA', size, (0, 255, 0, 200))
    result = gl_graph('circular-reveal', progress=.31, opacity=.5).render(
        {'a': a, 'b': b}, gpu_backend=transition_backend, gpu_strict=True,
    )
    assert result.getpixel((80, 40)) == (0, 255, 0, 100)
    assert result.getpixel((80 + 28, 40))[3] == result.getpixel((80, 40 + 28))[3] == 0
    assert result.getpixel((80 + 20, 40))[3] == result.getpixel((80, 40 + 20))[3] == 100
    pixels = np.asarray(result)
    feather = pixels[(0 < pixels[..., 3]) & (pixels[..., 3] < 100)]
    assert len(feather) and np.all(feather[:, :3] == (0, 255, 0))


def test_gl_transition_budget_rejection_has_no_transfers_or_cpu_substitute(transition_backend, monkeypatch):
    backend = transition_backend
    a, b = images()
    sources = {'a': a, 'b': b}
    graph = gl_graph('circular-reveal', progress=.4)
    monkeypatch.setattr(backend, 'max_working_set_bytes', 12 * a.width * a.height - 1)
    plan = graph.backend_plan({key: im.size for key, im in sources.items()}, mode='auto', gpu_backend=backend)
    assert not plan['eligible']
    assert plan['nodes']['out']['decision_reason'] == 'texture_payload_budget'
    with pytest.raises(GPUUnsupported, match='exceeds budget'):
        graph.render(sources, gpu_backend=backend)
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 0}
    assert backend.last_transport['uploads'] == backend.last_transport['readbacks'] == 0


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
    # M8.5 resident DAG counts shader draws, not graph effect nodes.
    assert backend.last_transport['gpu_resident_graph'] is True
    assert backend.last_dispatch=={'gpu_nodes':backend.last_transport['resident_draws'],'cpu_fallbacks':0}
    assert backend.last_transport['uploads']==2
    assert backend.last_transport['readbacks']==1
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
