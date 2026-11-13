"""M8.8 mixed CPU/GPU island planning and dispatch telemetry."""
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu_islands import plan_islands


def _graph(nodes, output):
    return EffectGraph({'nodes': nodes, 'output': output})


def test_cpu_node_partitions_two_explicit_islands():
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'blur', 'type': 'filter', 'inputs': ['a'], 'filter_radius': 1.2},
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': ['blur'],
         'warp_type': 'raindrop_ripples', 'warp_amplitude': [3, 0]},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['warp'],
         'key_color': '#00ff00'},
        {'id': 'out', 'type': 'opacity', 'inputs': ['key'], 'opacity': .8},
    ], 'out')
    islands, rejected = plan_islands(graph, {})
    assert rejected == []
    assert [island.members for island in islands] == [('blur',), ('key', 'out')]
    assert [island.boundaries for island in islands] == [('a',), ('warp',)]
    assert [island.output for island in islands] == ['blur', 'out']


def test_lineage_reset_splits_islands_without_hidden_alpha_readback():
    graph = _graph([
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'mix', 'type': 'transition', 'inputs': ['a', 'b'],
         'transition_style': 'dissolve', 'progress': .4},
        {'id': 'blur', 'type': 'filter', 'inputs': ['mix'], 'filter_radius': 1.1},
        {'id': 'out', 'type': 'chroma_key', 'inputs': ['blur'],
         'alpha_source': 'original'},
    ], 'out')
    islands, rejected = plan_islands(graph, {})
    assert rejected == []
    assert [island.members for island in islands] == [('mix',), ('blur', 'out')]
    assert islands[1].lineage_source == 'mix'


def test_multi_exit_region_is_rejected_with_reason():
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'blur', 'type': 'filter', 'inputs': ['a'], 'filter_radius': 1.2},
        {'id': 'left_gpu', 'type': 'filter', 'inputs': ['blur'], 'filter_radius': 1.1},
        {'id': 'right_gpu', 'type': 'opacity', 'inputs': ['blur'], 'opacity': .8},
        {'id': 'left', 'type': 'spatial_warp', 'inputs': ['left_gpu'],
         'warp_type': 'raindrop_ripples'},
        {'id': 'right', 'type': 'raster_effect', 'inputs': ['right_gpu'],
         'raster_type': 'pixel_sort'},
        {'id': 'out', 'type': 'composite', 'inputs': ['left', 'right']},
    ], 'out')
    islands, rejected = plan_islands(graph, {})
    assert [(island.members, island.output) for island in islands] == [(('out',), 'out')]
    assert rejected == [{
        'members': ['blur', 'left_gpu', 'right_gpu'],
        'reason': 'multiple_host_visible_exits',
        'exits': ['left_gpu', 'right_gpu'],
    }]


def test_auto_render_dispatches_island_then_cpu(monkeypatch):
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'blur', 'type': 'filter', 'inputs': ['a'], 'filter_radius': 1.2},
        {'id': 'out', 'type': 'spatial_warp', 'inputs': ['blur'],
         'warp_type': 'raindrop_ripples', 'warp_amplitude': [0, 0]},
    ], 'out')
    calls = []

    def execute(_backend, island, results):
        calls.append((island.members, tuple(results)))
        return results['a'].copy()

    monkeypatch.setattr('framerudiments.effect_graph_gpu_islands.execute_island', execute)

    class Backend:
        supports_gpu_islands = True
        def begin_transfer_sample(self):
            self.last_transport = {}
        def render_resident_graph(self, *_):
            raise AssertionError('whole graph is unsupported')

    source = Image.new('RGBA', (13, 9), (20, 80, 120, 190))
    backend = Backend()
    result = graph.render({'a': source}, gpu_backend=backend)
    assert result.tobytes() == source.tobytes()
    assert calls == [(('blur',), ('a',))]
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}


def test_identity_only_region_does_not_create_transfer_island():
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'out', 'type': 'copy', 'inputs': ['a']},
    ], 'out')
    islands, rejected = plan_islands(graph, {})
    assert islands == []
    assert rejected == [{'members': ['out'], 'reason': 'no_resident_draws',
                         'exit': 'out'}]
