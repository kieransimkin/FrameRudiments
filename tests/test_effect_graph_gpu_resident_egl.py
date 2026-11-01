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


def test_m85_resident_chromatic_blur_bloom_parity_and_transfer():
    """Must use real EGL shaders, no per-node host round trips."""
    import numpy as np
    from framerudiments.effect_graph_gpu_resident import resident_plan

    try:
        backend = GLMatteBackend(backend='egl')
    except (ImportError, RuntimeError):
        pytest.skip('ModernGL/EGL unavailable; required GPU workflow forbids skips')
    with backend:
        size = (49, 33)
        y, x = np.indices((size[1], size[0]))
        a = np.stack(((x * 17 + y * 7) % 256, (x * 3 + y * 19) % 256,
                      (x * 5 + y * 13) % 256, (x * 23 + y * 11) % 256), -1).astype('uint8')
        a[4:9, 3:12, :3] = (255, 0, 170)
        a[4:9, 3:12, 3] = 0
        sources = {'a': Image.fromarray(a, 'RGBA'),
                   'b': Image.new('RGBA', size, (100, 240, 75, 147))}
        graph = EffectGraph({'nodes': [
            {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
            {'id': 'blur', 'type': 'filter', 'inputs': ['a'],
             'filter_type': 'blur', 'filter_radius': 1.5, 'filter_border': 'transparent'},
            {'id': 'shift', 'type': 'chromatic_displacement', 'inputs': ['blur'],
             'red_offset': [1.25, -1.5], 'blue_offset': [-.5, 2.25]},
            {'id': 'bloom', 'type': 'filter', 'inputs': ['b'],
             'filter_type': 'bloom', 'filter_radius': .8, 'filter_mix': .7},
            {'id': 'mix', 'type': 'transition', 'inputs': ['shift', 'bloom'],
             'transition_style': 'wipe', 'progress': .43, 'feather': .16},
            {'id': 'out', 'type': 'composite', 'inputs': ['mix', 'a'], 'opacity': .85},
        ], 'output': 'out'})
        reference = graph.render(sources)
        actual = graph.render(sources, gpu_backend=backend, gpu_strict=True)
        expected = np.asarray(reference, dtype=np.int16)
        received = np.asarray(actual, dtype=np.int16)
        delta = np.abs(received - expected)
        # Straight RGB for fully transparent texels is unspecified; alpha and
        # visible colours are compared independently.
        valid = (expected[..., 3] > 16) & (received[..., 3] > 16)
        assert delta[..., 3].max() <= 4
        assert delta[..., :3][valid].max(initial=0) <= 10
        assert backend.last_transport['gpu_resident_graph'] is True
        assert backend.last_transport['uploads'] == 2
        assert backend.last_transport['readbacks'] == 1
        plan = resident_plan(graph)
        assert backend.last_transport['resident_draws'] == plan['resident_draws']
        assert backend.last_transport['resident_peak_allocated_surfaces'] == plan[
            'resident_peak_allocated_surfaces']
        assert graph.render(sources, gpu_backend=backend).tobytes() == actual.tobytes()
        assert sources['a'].tobytes() == a.tobytes()


def test_m85_budget_strict_and_auto_fallback_without_transfers():
    from framerudiments.effect_graph_gpu import GPUUnsupported

    try:
        backend = GLMatteBackend(backend='egl', max_working_set_bytes=16 * 12 * 4 * 2)
    except (ImportError, RuntimeError):
        pytest.skip('ModernGL/EGL unavailable; required GPU workflow forbids skips')
    with backend:
        graph = EffectGraph({'nodes': [
            {'id': 'a', 'type': 'source'},
            {'id': 'blur', 'type': 'filter', 'inputs': ['a'], 'filter_radius': 2},
        ], 'output': 'blur'})
        sources = {'a': Image.new('RGBA', (16, 12), (30, 40, 50, 90))}
        with pytest.raises(GPUUnsupported):
            graph.render(sources, gpu_backend=backend, gpu_strict=True)
        assert backend.last_transport['uploads'] == 0
        cpu = graph.render(sources)
        automatic = graph.render(sources, gpu_backend=backend)
        assert cpu.tobytes() == automatic.tobytes()
        assert backend.last_transport['uploads'] == 0
        assert backend.last_transport['readbacks'] == 0
        assert backend.last_dispatch['cpu_fallbacks'] == 1
