"""Must execute (not skip) in required GPU EGL matrix."""
from PIL import Image
import pytest
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend


def test_gpu_resident_shared_fork_and_transfer():
    try:
        backend = GLMatteBackend(backend='egl')
    except (ImportError, RuntimeError):
        pytest.skip('ModernGL/EGL unavailable; required GPU workflow forbids skips')
    with backend:
        g=EffectGraph({'nodes':[
            {'id':'a','type':'source'}, {'id':'b','type':'source'},
            {'id':'t','type':'transition','inputs':['a','b'],'progress':.4},
            {'id':'clone','type':'copy','inputs':['a']},
            {'id':'out','type':'composite','inputs':['t','clone']}], 'output':'out'})
        sources={'a':Image.new('RGBA',(16,12),(100,50,20,180)),
                 'b':Image.new('RGBA',(16,12),(20,100,80,140))}
        reference=g.render(sources)
        actual=g.render(sources,gpu_backend=backend)
        import numpy as np
        delta=np.abs(np.asarray(reference,dtype=np.int16)-np.asarray(actual,dtype=np.int16))
        assert delta.max()<=3
        assert backend.last_transport['gpu_resident_graph']
        assert backend.last_transport['uploads']==2
        assert backend.last_transport['readbacks']==1
        assert backend.last_transport['resident_draws']==2
        # Deterministic random access; no lingering state or mutations.
        assert g.render(sources,gpu_backend=backend).tobytes()==actual.tobytes()


def test_resident_budget_fails_without_partial_uploads():
    try:
        backend=GLMatteBackend(backend='egl',max_working_set_bytes=128)
    except (ImportError, RuntimeError):
        pytest.skip('ModernGL/EGL unavailable')
    with backend:
        g=EffectGraph({'nodes':[{'id':'a','type':'source'}, {'id':'b','type':'source'},
                                {'id':'out','type':'transition','inputs':['a','b'],'progress':.5}],
                       'output':'out'})
        images={k:Image.new('RGBA',(32,32),'#ffffff') for k in ('a','b')}
        from framerudiments.effect_graph_gpu import GPUUnsupported
        with pytest.raises(GPUUnsupported):g.render(images,gpu_backend=backend,gpu_strict=True)
        assert backend.last_transport['uploads']==0
