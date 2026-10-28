"""Backend dispatch must preserve old graph semantics without a GPU driver."""
from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported, GLMatteBackend


def build(kind):
    nodes = [{'id':'image', 'type':'source'}, {'id':'mask','type':'source'}]
    if kind == 'matte':
        nodes.append({'id':'out','type':'matte','inputs':['image','mask'], 'mask_combine':'multiply'})
    else:
        nodes.append({'id':'out','type':'chroma_key','inputs':['image'], 'key_color':'#00ff00'})
    return EffectGraph({'nodes':nodes, 'output':'out'})


class FakeGPU:
    def __init__(self, *, unsupported=False):
        self.called = []
        self.unsupported = unsupported
    def apply_matte(self, images, config, *, original_alpha=None):
        self.called.append(('matte', config.get('mask_opacity', 1)))
        if self.unsupported:
            raise GPUUnsupported('GPU radius limit exceeded')
        from framerudiments.effect_graph_mattes import apply_matte
        return apply_matte(images, config, original_alpha=original_alpha)
    def chroma_key(self, image, config, *, original_alpha=None):
        self.called.append(('key', config.get('tolerance', .1)))
        if self.unsupported:
            raise GPUUnsupported('GPU edge decontamination unsupported')
        from framerudiments.effect_graph_mattes import chroma_key
        return chroma_key(image, config, original_alpha=original_alpha)


@pytest.mark.parametrize('kind', ['matte','chroma_key'])
def test_gpu_dispatch_and_cpu_parity(kind):
    graph = build(kind)
    src = Image.new('RGBA',(5,3),(20,225,20,230))
    matte = Image.new('RGBA',(5,3),(255,255,255,128))
    sources = {'image':src,'mask':matte}
    fake = FakeGPU()
    out = graph.render(sources, gpu_backend=fake)
    assert fake.called[0][0] == ('matte' if kind == 'matte' else 'key')
    assert fake.last_dispatch == {'gpu_nodes': 1, 'cpu_fallbacks': 0}
    assert out.tobytes() == graph.render(sources).tobytes()
    assert src.getpixel((0,0)) == (20,225,20,230)


@pytest.mark.parametrize('kind', ['matte','chroma_key'])
def test_unsupported_auto_fallback_vs_strict_gpu(kind):
    g = build(kind)
    sources = {'image':Image.new('RGBA',(2,2),'red'), 'mask':Image.new('RGBA',(2,2),'white')}
    backend = FakeGPU(unsupported=True)
    assert g.render(sources, gpu_backend=backend).tobytes() == g.render(sources).tobytes()
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}
    with pytest.raises(GPUUnsupported):
        g.render(sources, gpu_backend=backend, gpu_strict=True)


@pytest.mark.parametrize('kind,config', [
    ('matte', {'expand':16,'feather':12}),
    ('matte', {'expand':-16,'feather':0}),
    ('chroma_key', {'edge_expand':16,'edge_feather':12}),
])
def test_gpu_supported_radius_bounds(kind, config):
    GLMatteBackend._require_supported(config,kind)


@pytest.mark.parametrize('kind,config', [
    ('matte', {'expand':16.1}),
    ('matte', {'feather':12.1}),
    ('chroma_key', {'edge_expand':-17}),
    ('chroma_key', {'edge_feather':13}),
    ('chroma_key', {'edge_decontaminate':.001}),
])
def test_unsupported_gpu_processing_declared(kind,config):
    with pytest.raises(GPUUnsupported):
        GLMatteBackend._require_supported(config,kind)


def test_import_is_optional_and_no_context_required_to_validate():
    from framerudiments.effect_graph_gpu import GPUCapabilities
    assert GPUCapabilities(330,'egl','test-driver').max_gaussian_radius == 12
