"""Project-facing opt-in GPU configuration remains a CPU-safe import."""
import pytest
from PIL import Image
from framerudiments.effect_graph_integration import create_gpu_backend, evaluate_effect_graph
from framerudiments.effect_graph import EffectGraph


def test_gpu_runtime_cpu_path_no_driver_import():
    assert create_gpu_backend('cpu') is None
    with pytest.raises(ValueError, match='effect_graph_backend'):
        create_gpu_backend('silent-gpu-fallback')


def test_auto_is_explicit_about_missing_optional_gpu(monkeypatch):
    from framerudiments import effect_graph_gpu as mod
    def unavailable():
        raise RuntimeError('no GL')
    monkeypatch.setattr(mod, 'GLMatteBackend', unavailable)
    assert create_gpu_backend('auto') is None
    with pytest.raises(RuntimeError, match='no GL'):
        create_gpu_backend('gpu')


def test_context_adapter_passes_backend_and_strict_option():
    graph = EffectGraph({'nodes':[
        {'id':'s','type':'source'}, {'id':'m','type':'source'},
        {'id':'out','type':'matte','inputs':['s','m']}
    ],'output':'out'})
    s = Image.new('RGBA',(3,3),(255,0,0,255))
    m = Image.new('RGBA',(3,3),(0,0,0,128))
    class Spy:
        def apply_matte(self, images, config, *, original_alpha=None):
            assert original_alpha is not None
            from framerudiments.effect_graph_mattes import apply_matte
            return apply_matte(images, config, original_alpha=original_alpha)
    assert evaluate_effect_graph(graph,None,None,s,{'s':s,'m':m},gpu_backend=Spy(),gpu_strict=True).getpixel((1,1))[3]==128
