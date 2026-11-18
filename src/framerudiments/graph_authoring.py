"""Native effect-graph authoring validation without rendering pixels.

The renderer's schema and DanceRudiment binding validators remain authoritative.
Authoring additionally checks disconnected topology and source names, so choosing
a new output cannot activate an invalid hidden branch. RGBA8 mask/texture roles
describe interpretation, not a second incompatible pixel format.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from .effect_graph import EffectGraph, KINDS, MAX_INPUTS, MAX_NODES
from .effect_graph_integration import validate_graph_sources
from .effect_graph_layer_overlays import requires_texture
from .effect_graph_modulation import EffectGraphControls
from .preview_stream_contract import canonical_digest

SCHEMA = 'framerudiments.authoring-graph-validation.v1'
MAX_SOURCES = 2048
_INVALID_NATIVE_INPUT = (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError,
                         RuntimeError, ImportError, OverflowError, RecursionError)
_MESSAGES = {
    'graph_schema': 'Native graph schema, parameters or input-port counts are invalid.',
    'graph_topology': 'Every graph node must reference existing inputs and remain free of cycles.',
    'source_unavailable': 'A source node does not name an available surface in this authoring project.',
    'control_binding': 'An existing graph control has an invalid target, parameter, choice or motion binding.',
    'project_invalid': 'The native project, source catalogue or edited sidecar data could not be prepared.',
    'input_changed': 'Pinned inputs changed during validation; reopen the verified project.',
}


def available_graph_sources(document):
    """Names supplied by the ordinary authoring Compositor (no scene adapter).

    This only inventories surface identities. It does not claim layer assets,
    text layout, a device, or output pixels have rendered successfully.
    """
    names = {'frame', 'background'}
    layers = document.get('layers', [])
    if not isinstance(layers, list):
        raise ValueError('invalid source catalogue')
    layer_names = []
    for layer in layers:
        if not isinstance(layer, dict) or 'id' not in layer:
            raise ValueError('invalid layer source')
        layer_names.append(f'layer:{layer["id"]}')
    if len(set(layer_names)) != len(layer_names):
        raise ValueError('duplicate layer source')
    names.update(layer_names)
    identity = document.get('identity', [])
    art = document.get('lyric_art', {})
    if not isinstance(identity, list) or not isinstance(art, dict):
        raise ValueError('invalid text source catalogue')
    items = art.get('items', [])
    if not isinstance(items, list):
        raise ValueError('invalid artwork source catalogue')
    names.update(f'identity:{index}' for index in range(len(identity)))
    names.update(f'lyric-art:{index}' for index in range(len(items)))
    if document.get('captions') and ('lyrics' in document or document.get('word_lyrics')):
        names.add('caption')
    if 'word_captions' in document:
        names.add('word-caption')
    if 'word_indicator' in document:
        names.add('word-indicator')
    # EffectGraph node IDs have this native limit; longer provider names cannot
    # be graph source nodes and do not consume the bounded authoring catalogue.
    names = {name for name in names if len(name) <= 128}
    if len(names) > MAX_SOURCES:
        raise ValueError('authoring source catalogue limit exceeded')
    return sorted(names)


def _all_topology(graph):
    visits = {}

    def visit(name):
        if name not in graph.nodes or visits.get(name) == 1:
            raise ValueError('invalid authoring topology')
        if visits.get(name) == 2:
            return
        visits[name] = 1
        for source in graph.nodes[name].inputs:
            visit(source)
        visits[name] = 2

    for name in graph.nodes:
        visit(name)


def _ports(node):
    """Describe already-native-validated authored ports, preserving their order."""
    kind = node['type']
    count = len(node.get('inputs', []))
    if kind == 'source':
        lower = upper = 0
    elif kind == 'composite':
        lower, upper = 2, MAX_INPUTS
    elif kind == 'matte':
        lower, upper = 2, 9
    elif kind == 'transition_chain':
        lower, upper = 2, 8
    else:
        lower = upper = count
    if kind == 'transition':
        roles = ['from', 'to', 'mask'][:count]
    elif kind == 'transition_chain':
        roles = ['from', *['to'] * (count-1)]
    elif kind == 'matte':
        roles = ['image', *['mask'] * (count-1)]
    elif kind == 'composite':
        roles = ['layer'] * count
    elif kind == 'spatial_warp':
        roles = ['image', 'displacement'][:count]
    elif kind == 'layer_style' and requires_texture(node):
        roles = ['image', 'texture']
    elif kind == 'procedural_field':
        roles = ['canvas']
    else:
        roles = ['image'] * count
    return {'id': node['id'], 'type': kind, 'min_inputs': lower, 'max_inputs': upper,
            'inputs': [{'index': index, 'role': roles[index], 'format': 'RGBA8', 'source': source}
                       for index, source in enumerate(node.get('inputs', []))],
            'output': {'format': 'RGBA8'}}


def _reject(report, code):
    report['valid'] = False
    report['errors'] = [{'code': code, 'message': _MESSAGES[code]}]
    return report


def validate_authoring_graph(project, *, request_sha256=None):
    """Validate a prepared native Project without rendering or opening a GPU.

    Controls use the native constructor, including actual named/recorded motion
    admission, but no frame is evaluated. The callback is deliberately absent:
    construction never asks for a source clock sample. No production or device
    qualification is inferred by a successful response.
    """
    document = project.data
    present = 'effect_graph' in document
    graph_hash = canonical_digest({'effect_graph': document.get('effect_graph'),
                                   'effect_graph_controls': document.get('effect_graph_controls', [])}) if present else None
    report = {'schema': SCHEMA, 'valid': True, 'graph_present': present,
              'request_sha256': request_sha256 or canonical_digest({'project': document, 'sidecars': {}}),
              'graph_sha256': graph_hash, 'sources': [], 'nodes': [], 'errors': [],
              'scope': {'rendered': False, 'gpu_context_opened': False, 'all_nodes': True,
                        'source_names': True, 'control_bindings': True}}
    try:
        sources = available_graph_sources(document)
        report['sources'] = [{'id': name, 'format': 'RGBA8'} for name in sources]
    except (ValueError, TypeError, KeyError):
        return _reject(report, 'project_invalid')
    if not present:
        return _reject(report, 'control_binding') if document.get('effect_graph_controls') else report
    try:
        graph = EffectGraph(project.effect_graph_spec)
    except _INVALID_NATIVE_INPUT as exc:
        topology = str(exc) == 'effect graph cycle' or str(exc) == 'unknown output' or str(exc).startswith('unknown input:')
        return _reject(report, 'graph_topology' if topology else 'graph_schema')
    try:
        _all_topology(graph)
    except (ValueError, RecursionError):
        return _reject(report, 'graph_topology')
    try:
        validate_graph_sources(SimpleNamespace(source_ids={
            name for name, node in graph.nodes.items() if node.kind == 'source'}), sources)
    except ValueError:
        return _reject(report, 'source_unavailable')
    try:
        EffectGraphControls(project, graph, document.get('effect_graph_controls', []), None)
    except _INVALID_NATIVE_INPUT:
        return _reject(report, 'control_binding')
    authored = document['effect_graph']['nodes']
    if len(authored) > MAX_NODES or any(node['type'] not in KINDS | {'transition_chain'} for node in authored):
        return _reject(report, 'graph_schema')
    report['nodes'] = [_ports(node) for node in authored]
    return report


def validate_graph_draft(project_file, draft, *, sidecars, validate_draft, temp_parent):
    """Prepare the same pinned native draft/sidecars used by streaming preview.

    The caller has already checked its transport and presentation-only policy.
    The existing native preparation rechecks that policy and source pins and
    owns every temporary sidecar. This never calls the renderer or compositor.
    """
    from .preview_native_renderer import NativePreviewRenderer

    identity = canonical_digest({'project': draft, 'sidecars': sidecars})
    owner = NativePreviewRenderer(project_file, deepcopy(draft), deepcopy(sidecars),
                                  validate_draft=validate_draft, temp_parent=Path(temp_parent))
    fallback = {'schema': SCHEMA, 'valid': False, 'graph_present': 'effect_graph' in draft,
                'request_sha256': identity,
                'graph_sha256': canonical_digest({'effect_graph': draft.get('effect_graph'),
                    'effect_graph_controls': draft.get('effect_graph_controls', [])}) if 'effect_graph' in draft else None,
                'sources': [], 'nodes': [], 'errors': [],
                'scope': {'rendered': False, 'gpu_context_opened': False, 'all_nodes': True,
                          'source_names': True, 'control_bindings': True}}
    try:
        try:
            owner._prepare()
        except _INVALID_NATIVE_INPUT:
            return _reject(fallback, 'project_invalid')
        report = validate_authoring_graph(owner.project, request_sha256=identity)
        try:
            owner.project.verify_inputs()
            owner.input_guard()
        except (ValueError, OSError, RuntimeError):
            return _reject(report, 'input_changed')
        return report
    finally:
        owner.close()
