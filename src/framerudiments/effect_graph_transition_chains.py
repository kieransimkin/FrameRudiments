"""M5.4 bounded transition-chain expansion into the existing effect DAG.

A chain is authoring shorthand, not a second renderer. Each input effect and
transition becomes a normal graph node with a stable, controllable identifier.
The existing graph executor handles all alpha, GPU fallback, matte and clock
semantics. The caller's authored specification is never modified.
"""
from __future__ import annotations

import re

MAX_CHAIN_INPUTS = 8
MAX_INPUT_EFFECTS = 4
CHAIN_PROPERTIES = frozenset({'id', 'type', 'inputs', 'stages', 'input_effects', 'opacity'})
UNARY_EFFECTS = frozenset({
    'copy', 'opacity', 'chromatic_displacement', 'spectral_displacement', 'chroma_key', 'layer_style', 'spatial_warp', 'raster_effect', 'filter', 'procedural_field',
})
_ID = re.compile(r'[a-z][a-z0-9_-]{0,31}\Z')


def _name(value, label):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f'{label} needs a stable lowercase id (1..32 characters)')
    return value


def expand_transition_chains(spec, *, max_nodes):
    """Return (expanded specification, discovery metadata) without mutation.

    Chains execute in the declared input order: A→B, then result→C, etc.
    For an individual matte stage, ``matte_input`` supplies the third input.
    Output opacity belongs to the chain's final copy node, after all stages.
    """
    if not isinstance(spec, dict) or set(spec) != {'nodes', 'output'}:
        raise ValueError('graph must contain exactly nodes and output')
    authored = spec['nodes']
    if not isinstance(authored, list) or not 1 <= len(authored) <= max_nodes:
        raise ValueError('graph must contain a bounded nodes list')

    # Reserve every explicitly authored name before generating any internal IDs.
    reserved = set()
    for node in authored:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str):
            raise ValueError('invalid graph node')
        if node['id'] in reserved:
            raise ValueError('duplicate node id')
        reserved.add(node['id'])

    expanded = []
    discovered = {}

    def append(node):
        name = node['id']
        if len(name) > 128:
            raise ValueError('expanded graph node id exceeds 128 characters')
        if name in generated or name in reserved:
            raise ValueError(f'transition chain generated node id collision: {name}')
        generated.add(name)
        expanded.append(node)
        if len(expanded) > max_nodes:
            raise ValueError('transition chain exceeds graph node limit')

    generated = set()
    for node in authored:
        if node.get('type') != 'transition_chain':
            # Existing nodes are kept unchanged; no new constraints or copies.
            expanded.append(node)
            continue
        if set(node) - CHAIN_PROPERTIES:
            raise ValueError('unknown transition_chain property')
        key = node['id']
        inputs = node.get('inputs')
        if (not isinstance(inputs, list) or not 2 <= len(inputs) <= MAX_CHAIN_INPUTS
                or not all(isinstance(source, str) and source for source in inputs)):
            raise ValueError('transition_chain requires 2..8 named inputs')
        stages = node.get('stages')
        if not isinstance(stages, list) or len(stages) != len(inputs) - 1:
            raise ValueError('transition_chain requires one stage per additional input')
        input_effects = node.get('input_effects', [[] for _ in inputs])
        if (not isinstance(input_effects, list) or len(input_effects) != len(inputs)
                or any(not isinstance(chain, list) or len(chain) > MAX_INPUT_EFFECTS
                       for chain in input_effects)):
            raise ValueError('transition_chain input_effects must contain one list per input (max 4)')
        if (type(node.get('opacity', 1)) not in (int, float)):
            raise ValueError('invalid chain opacity')

        prepared = []
        processed_ids = []
        for i, (source, filters) in enumerate(zip(inputs, input_effects)):
            previous = source
            ids = []
            for effect in filters:
                if (not isinstance(effect, dict) or not isinstance(effect.get('type'), str)
                        or effect['type'] not in UNARY_EFFECTS
                        or 'inputs' in effect):
                    raise ValueError('input preprocessing requires unary effect nodes without inputs')
                effect_name = _name(effect.get('id'), 'input effect')
                internal = f'{key}::input::{i}::{effect_name}'
                config = {**effect, 'id': internal, 'inputs': [previous]}
                append(config)
                ids.append(internal)
                previous = internal
            prepared.append(previous)
            processed_ids.append(ids)

        previous = prepared[0]
        stage_ids = {}
        for i, stage in enumerate(stages):
            if not isinstance(stage, dict):
                raise ValueError('transition chain stages must be objects')
            stage_name = _name(stage.get('id'), 'transition stage')
            if stage_name in stage_ids:
                raise ValueError('duplicate transition stage id')
            if 'type' in stage or 'inputs' in stage:
                raise ValueError('transition stages cannot override node type or inputs')
            style = stage.get('transition_style', 'dissolve')
            if not isinstance(style, str):
                raise ValueError('invalid transition stage style')
            matte_source = stage.get('matte_input')
            if style == 'matte':
                if not isinstance(matte_source, str) or not matte_source:
                    raise ValueError('matte stage requires matte_input')
            elif 'matte_input' in stage:
                raise ValueError('only matte transition stages accept matte_input')
            internal = f'{key}::stage::{stage_name}'
            transition_inputs = [previous, prepared[i + 1]]
            if style == 'matte':
                transition_inputs.append(matte_source)
            step = {k: v for k, v in stage.items() if k not in {'id', 'matte_input'}}
            append({**step, 'id': internal, 'type': 'transition', 'inputs': transition_inputs})
            stage_ids[stage_name] = internal
            previous = internal

        # Preserve the publicly authored chain output name, which other DAG
        # branches may reference. Copy is a standard unary node, not an image
        # recomputation or a separate alpha-compositing implementation.
        expanded.append({'id': key, 'type': 'copy', 'inputs': [previous],
                         'opacity': node.get('opacity', 1)})
        discovered[key] = {'stages': stage_ids, 'inputs': processed_ids}
        if len(expanded) > max_nodes:
            raise ValueError('transition chain exceeds graph node limit')

    return {'nodes': expanded, 'output': spec['output']}, discovered
