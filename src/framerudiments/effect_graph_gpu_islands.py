"""M8.8 conservative mixed CPU/GPU island partitioning.

An island is a maximal connected resident region with exactly one host-visible
exit. Alpha-lineage reset nodes end an island, which keeps the CPU graph's
``alpha_source=original`` contract exact without hidden lineage readbacks.
Rejected regions continue through the established per-node bridge.
"""
from __future__ import annotations

from dataclasses import dataclass

from .effect_graph_gpu_resident import node_supported, render_resident, resident_plan, resident_rejection
from .effect_graph_spatial import spatial_is_identity


RESET_KINDS = frozenset({'source', 'transition', 'composite'})
HOST_ALPHA_RESETS = RESET_KINDS | {'procedural_field'}


@dataclass(frozen=True)
class GPUIsland:
    members: tuple[str, ...]
    output: str
    boundaries: tuple[str, ...]
    graph: object
    source_map: tuple[tuple[str, str], ...]
    configs: dict
    lineage_source: str


def _entry(node, config, inputs):
    row = dict(config)
    row['id'] = node.id
    row['type'] = node.kind
    if inputs:
        row['inputs'] = list(inputs)
    else:
        row.pop('inputs', None)
    return row


def plan_islands(graph, configs, *, precise_gpu=False):
    """Return executable islands plus detached rejection telemetry."""
    from .effect_graph import EffectGraph

    consumers = {key: [] for key in graph.order}
    for key in graph.order:
        for upstream in graph.nodes[key].inputs:
            consumers[upstream].append(key)

    eligible = {
        key for key in graph.order
        if graph.nodes[key].kind != 'source'
        and node_supported(graph.nodes[key], configs.get(key, graph.nodes[key].config), precise_gpu=precise_gpu)
    }
    # Union resident neighbours, except across a node that establishes a new
    # alpha lineage. That node is the previous island's host-visible exit.
    parent = {key: key for key in eligible}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    for key in graph.order:
        if key not in eligible:
            continue
        for upstream in graph.nodes[key].inputs:
            if upstream in eligible and graph.nodes[upstream].kind not in RESET_KINDS:
                union(key, upstream)

    groups = {}
    for key in graph.order:
        if key in eligible:
            groups.setdefault(find(key), []).append(key)

    islands, rejected = [], []
    for members in groups.values():
        member_set = set(members)
        exits = [key for key in members if key == graph.output or any(
            consumer not in member_set for consumer in consumers[key])]
        if len(exits) != 1:
            rejected.append({'members': members, 'reason': 'multiple_host_visible_exits',
                             'exits': exits})
            continue
        output = exits[0]
        boundaries = []
        for key in members:
            for upstream in graph.nodes[key].inputs:
                if upstream not in member_set and upstream not in boundaries:
                    boundaries.append(upstream)
        # Synthetic boundary sources contain current RGBA. An original-alpha
        # consumer can use a boundary only when that boundary resets lineage.
        # An active warp changes lineage coordinates without resetting coverage.
        lineage = {}
        boundary_alpha = {}
        unsafe_alpha = set()
        for key in members:
            node = graph.nodes[key]
            if node.kind in RESET_KINDS:
                lineage[key] = key
                boundary_alpha[key] = None
            else:
                cfg = configs.get(key, node.config)
                primary = node.inputs[0]
                inherited_boundary = boundary_alpha.get(primary, primary)
                boundary_alpha[key] = inherited_boundary
                if node.kind == 'spatial_warp' and not spatial_is_identity(cfg):
                    lineage[key] = key
                else:
                    lineage[key] = lineage.get(primary, primary)
                if (node.kind in {'matte', 'chroma_key'} and cfg.get('alpha_source') == 'original'
                        and inherited_boundary is not None
                        and graph.nodes[inherited_boundary].kind not in HOST_ALPHA_RESETS):
                    unsafe_alpha.add(inherited_boundary)
        if unsafe_alpha:
            rejected.append({'members': members, 'reason': 'external_alpha_lineage',
                             'boundaries': sorted(unsafe_alpha), 'exit': output})
            continue
        owner = lineage[output]
        if (output != graph.output and owner in member_set
                and (owner != output or graph.nodes[owner].kind == 'spatial_warp')):
            # The host bridge can return a separate warped alpha image.
            rejected.append({'members': members, 'reason': 'internal_alpha_lineage_exit',
                             'lineage_owner': owner, 'exit': output})
            continue

        source_map = tuple((f'__m88_boundary_{index}', key)
                           for index, key in enumerate(boundaries))
        replacements = {original: synthetic for synthetic, original in source_map}
        rows = [{'id': synthetic, 'type': 'source'} for synthetic, _ in source_map]
        subconfigs = {}
        for key in members:
            node = graph.nodes[key]
            cfg = configs.get(key, node.config)
            inputs = [upstream if upstream in member_set else replacements[upstream]
                      for upstream in node.inputs]
            rows.append(_entry(node, cfg, inputs))
            subconfigs[key] = cfg
        island_graph = EffectGraph({'nodes': rows, 'output': output})
        reason = resident_rejection(island_graph, subconfigs, precise_gpu=precise_gpu)
        if reason is not None:
            rejected.append({'members': members, 'reason': reason, 'exit': output})
            continue
        if resident_plan(island_graph, subconfigs, precise_gpu=precise_gpu)['resident_draws'] == 0:
            rejected.append({'members': members, 'reason': 'no_resident_draws',
                             'exit': output})
            continue
        islands.append(GPUIsland(tuple(members), output, tuple(boundaries), island_graph,
                                 source_map, subconfigs, owner))
    return islands, rejected


def execute_island(backend, island, results):
    sources = {synthetic: results[original]
               for synthetic, original in island.source_map}
    before = dict(getattr(backend, 'last_transport', {}))
    plan = {}
    image = render_resident(backend, island.graph, sources, island.configs, island_stats=plan)
    report = getattr(backend, 'last_transport', None)
    if report is not None:
        row = {
            'members': list(island.members), 'output': island.output,
            'boundaries': list(island.boundaries), **plan,
            'uploads': report.get('uploads', 0) - before.get('uploads', 0),
            'readbacks': report.get('readbacks', 0) - before.get('readbacks', 0),
            'upload_bytes': report.get('upload_bytes', 0) - before.get('upload_bytes', 0),
            'readback_bytes': report.get('readback_bytes', 0) - before.get('readback_bytes', 0),
        }
        report.setdefault('gpu_islands', []).append(row)
        report['gpu_island_count'] = len(report['gpu_islands'])
        report['gpu_island_nodes'] = sum(len(item['members']) for item in report['gpu_islands'])
    return image
