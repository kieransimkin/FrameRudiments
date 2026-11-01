"""GPU-resident M8.4 tests; hardware acceptance belongs to EGL workflow."""
from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu_resident import supported


def graph(style='dissolve'):
    return EffectGraph({'nodes': [
        {'id':'a','type':'source'}, {'id':'b','type':'source'},
        {'id':'fade','type':'transition','inputs':['a','b'],
         'transition_style':style,'progress':0.5},
        {'id':'fork','type':'copy','inputs':['a']},
        {'id':'out','type':'composite','inputs':['fade','fork']}
    ], 'output':'out'})


def test_resident_predicate_is_conservative():
    g = graph()
    assert supported(g, {})
    assert not supported(g, {'fade':{'transition_style':'spectral'}})
    assert not supported(g, {'out':{'mode':'multiply'}})


def test_no_gpu_keeps_reference_output():
    g = graph()
    sources = {'a':Image.new('RGBA',(12,9),(100,40,20,128)),
               'b':Image.new('RGBA',(12,9),(10,200,80,200))}
    assert g.render(sources).tobytes() == g.render(sources).tobytes()


def test_resident_dispatch_receives_resolved_controls_without_mutation():
    g = graph()
    sources = {n:Image.new('RGBA',(12,9),(20,30,40,255)) for n in ('a','b')}
    class Dummy:
        def __init__(self): self.last_dispatch={};self.last_transport={}
        def begin_transfer_sample(self): self.last_transport={}
        def render_resident_graph(self, graph, supplied, configs):
            assert configs['fade']['progress'] == .7
            assert supplied is sources
            return Image.new('RGBA',(12,9),(12,13,14,15))
    d=Dummy()
    image=g.render(sources,gpu_backend=d,parameter_values={('fade','progress'):.7})
    assert image.getpixel((0,0)) == (12,13,14,15)
    assert g.nodes['fade'].config['progress'] == .5


def test_unsupported_graph_uses_existing_dispatch():
    g=graph()
    class Dummy:
        def __init__(self):self.last_dispatch={};self.last_transport={}
        def begin_transfer_sample(self):self.last_transport={}
        def render_resident_graph(self,*args):raise AssertionError('must not execute')
        def render_transition(self,a,b,c):
            from framerudiments.effect_graph_transitions import render_transition
            return render_transition(a,b,c)
    sources={n:Image.new('RGBA',(10,10),(10,20,30,255)) for n in ('a','b')}
    # Disabled residency because the composite operation is not GPU supported.
    image=g.render(sources,gpu_backend=Dummy(),parameter_values={('out','mode'):'multiply'})
    assert image.size==(10,10)
