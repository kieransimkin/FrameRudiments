"""Bounded native parameter discovery and immutable authoring edit proposals.

The effect graph owns parameter names, defaults, validation and application.
Motion and EffectGraphControls own source admission, clocks and modulation.
This adapter maps expanded native nodes back to their authored locations and
returns a validated draft; it never saves a project, renders or samples motion.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path

from .effect_graph import EffectGraph, MAX_NODES
from .effect_graph_modulation import MAX_GRAPH_CONTROLS
from .effect_graph_style_contract import STYLE_DRIVER_LIMIT
from .graph_authoring import _INVALID_NATIVE_INPUT, validate_authoring_graph
from .modulation import modulation_descriptors
from .motion import Motion, named_motion_descriptors, native_motion_names
from .preview_native_renderer import NativePreviewRenderer
from .preview_stream_contract import canonical_digest
from .project import Project


CATALOG_SCHEMA = 'framerudiments.authoring-controls.v1'
EDIT_SCHEMA = 'framerudiments.authoring-edit.v1'
MAX_PARAMETERS = 2048
MAX_MOTIONS = 4096
MAX_AXES = 1024
MAX_BINDINGS = MAX_GRAPH_CONTROLS
MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_EDIT_BYTES = 12 * 1024 * 1024
MOTION_KINDS = frozenset({'named', 'recording', 'match', 'table', 'composite'})
NAMED_SOURCE_FIELDS = frozenset({'kind', 'pattern', *named_motion_descriptors()})
BINDING_SETTINGS = frozenset({*modulation_descriptors(), 'choices'})
_MESSAGES = {
    'graph_invalid': 'Native graph parameters, sources or existing control bindings are invalid.',
    'node_invalid': 'Choose an existing native graph node or an expanded transition-chain target.',
    'parameter_readonly': 'This field is not an editable native parameter for the selected node.',
    'motion_unavailable': 'Choose a released native movement or an existing admitted pinned motion.',
    'binding_invalid': 'The binding target, index, source or settings are not valid for this draft.',
    'edit_rejected': 'Native validation rejected this change; check the parameter, inputs and existing bindings.',
    'catalog_limit': 'The native parameter or motion catalogue exceeds the bounded authoring limits.',
    'input_changed': 'Pinned inputs changed while preparing this proposal; reopen the verified project.',
}


class AuthoringControlError(ValueError):
    """A fixed, path-free public rejection; underlying native exceptions stay local."""

    def __init__(self, code):
        self.code = code
        super().__init__(_MESSAGES[code])


def _configured_motions(document):
    """Inventory only actual motion slots, never arbitrary pinned file objects."""
    found = []
    stack = [document]
    visited = 0
    while stack:
        value = stack.pop()
        visited += 1
        if visited > 100000:
            raise AuthoringControlError('catalog_limit')
        if isinstance(value, dict):
            motion = value.get('motion')
            if isinstance(motion, dict):
                found.append(motion)
            sources = value.get('stem_sources')
            if isinstance(sources, dict):
                found.extend(motion for motion in sources.values() if isinstance(motion, dict))
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return found


def check_control_sources(original, draft):
    """Check control-source authority before a native parser opens any file.

    Existing recording/match/table/composite source configurations are immutable.
    New named sources have only the released movement selector and native clock
    options. Generic path/hash admission remains the server's separate guard.
    """
    configs = draft.get('effect_graph_controls', [])
    if not isinstance(configs, list) or len(configs) > MAX_BINDINGS:
        raise AuthoringControlError('binding_invalid')
    admitted = {canonical_digest(motion) for motion in _configured_motions(original)}
    for row in configs:
        if not isinstance(row, dict) or not isinstance(row.get('motion'), dict):
            raise AuthoringControlError('binding_invalid')
        motion = row['motion']
        if canonical_digest(motion) in admitted:
            continue
        if motion.get('kind', 'named') == 'named' and not set(motion) - NAMED_SOURCE_FIELDS:
            continue
        raise AuthoringControlError('motion_unavailable')


@dataclass(frozen=True)
class _Location:
    path: tuple
    authored_id: str
    label: str
    chain_output: bool = False


def _locations(document, graph):
    """Resolve exact expansion metadata, without parsing generated ID strings."""
    result = {}
    for index, authored in enumerate(document['effect_graph']['nodes']):
        name = authored['id']
        path = ('effect_graph', 'nodes', index)
        chain = authored['type'] == 'transition_chain'
        result[name] = _Location(path, name, name, chain)
        if not chain:
            continue
        expansion = graph.chain_expansions[name]
        for stage_index, stage in enumerate(authored['stages']):
            internal = expansion['stages'][stage['id']]
            result[internal] = _Location((*path, 'stages', stage_index), name,
                                         f'{name} · stage {stage["id"]}')
        for input_index, effects in enumerate(authored.get('input_effects', [])):
            for effect_index, effect in enumerate(effects):
                internal = expansion['inputs'][input_index][effect_index]
                result[internal] = _Location((*path, 'input_effects', input_index, effect_index), name,
                                             f'{name} · input {input_index + 1} · {effect["id"]}')
    if set(result) != set(graph.nodes) or len(result) > MAX_NODES:
        raise AuthoringControlError('node_invalid')
    return result


def _normalise_descriptor(prop, descriptor, *, group='binding', config=None):
    default = deepcopy(descriptor.get('default'))
    result = {
        'property': prop,
        'label': descriptor.get('label', prop.replace('_', ' ').replace('.', ' · ')),
        'type': descriptor['type'],
        'value': deepcopy(config.get(prop, default) if config is not None else descriptor.get('value', default)),
        'default': default,
        'editable': descriptor.get('editable', True),
        'bindable': descriptor.get('bindable', descriptor.get('animatable', False)),
        'active': descriptor.get('active', True),
        'group': descriptor.get('group', group),
    }
    interval = descriptor.get('range')
    if interval is not None:
        result['minimum'], result['maximum'] = interval
    for key in ('minimum', 'maximum', 'step', 'units', 'choices', 'reason', 'max_length',
                'min_items', 'max_items', 'excluded', 'constraints', 'factory_default', 'default_semantics'):
        if key in descriptor:
            result[key] = deepcopy(descriptor[key])
    if result['type'] == 'integer':
        result.setdefault('step', 1)
    if not result['editable']:
        result.setdefault('reason', 'This native field is read-only in the authoring editor.')
    return result


def _selected_graph(graph, node):
    """Reuse native validation without opening unrelated admitted shader files."""
    target = graph.nodes[node]
    sources = [{'id': f'input_{index}', 'type': 'source'} for index in range(len(target.inputs))]
    return EffectGraph({'nodes': [*sources, {**deepcopy(target.config), 'id': 'selected',
                                            'inputs': [row['id'] for row in sources]}], 'output': 'selected'})


def _safe_numeric_defaults(graph, prop, descriptor, bounds):
    """Find admitted interval endpoints using the actual native resolver.

    Ordered stops, spectral phase intervals and transition durations have joint
    constraints. Bisection asks the native validator about each candidate; it
    does not duplicate those constraints or evaluate a motion/clock/sample.
    """
    lower, upper = max(bounds[0], descriptor['minimum']), min(bounds[1], descriptor['maximum'])
    anchor = max(lower, min(upper, descriptor['value']))

    def admitted(value):
        try:
            graph.apply_control_values({('selected', prop): value})
            return True
        except _INVALID_NATIVE_INPUT:
            return False

    if lower >= upper or not admitted(anchor):
        return None

    def endpoint(value):
        if admitted(value):
            return value
        valid, invalid = anchor, value
        for _ in range(24):
            middle = (valid + invalid) / 2
            if admitted(middle):
                valid = middle
            else:
                invalid = middle
        return valid

    lower, upper = endpoint(lower), endpoint(upper)
    return [lower, upper] if lower < upper else None


def _parameter_descriptors(graph, node):
    raw = graph.control_descriptors(node, active_only=False)
    if len(raw) > MAX_PARAMETERS:
        raise AuthoringControlError('catalog_limit')
    result = []
    selected = _selected_graph(graph, node)
    binding_range = ([-STYLE_DRIVER_LIMIT, STYLE_DRIVER_LIMIT] if graph.nodes[node].kind == 'layer_style'
                     else modulation_descriptors()['range']['range'])
    for prop, descriptor in raw.items():
        item = _normalise_descriptor(prop, descriptor, group=graph.nodes[node].kind)
        if item['bindable']:
            if graph.is_categorical_control(node, prop):
                choices = item.get('choices', [])
                if 2 <= len(choices) <= 32:
                    item['binding_defaults'] = {'choices': deepcopy(choices)}
                else:
                    item['bindable'] = False
                    item.setdefault('reason', 'The current native topology permits fewer than two binding choices.')
            elif 'minimum' in item and 'maximum' in item:
                item['binding_range'] = list(binding_range)
                interval = _safe_numeric_defaults(selected, prop, item, binding_range)
                if interval is not None:
                    item['binding_defaults'] = {'range': interval}
                else:
                    item['bindable'] = False
                    item.setdefault('reason', 'The native driver cannot vary this parameter within its admitted range.')
        result.append(item)
    return result


def _motion_catalog(project, original, document):
    names = native_motion_names()
    factory = [{'kind': 'named', 'pattern': name} for name in names]
    factory_ids = {canonical_digest(config) for config in factory}
    # Every factory source has the same native route/options. Discover its
    # dimensions once through Motion; the released catalogue admits the names.
    # Configured variants still use their full native constructor individually.
    native_axes = list(Motion(factory[0], project.base, project.clock).value_dimensions) if factory else []
    configs = list(factory)
    configured = _configured_motions(original)
    admitted = {canonical_digest(config) for config in configured}
    configs.extend(configured)
    configs.extend(_configured_motions(document))
    seen, result = set(), []
    for config in configs:
        key = canonical_digest(config)
        if key in seen:
            continue
        seen.add(key)
        kind = config.get('kind', 'named')
        if kind not in MOTION_KINDS:
            continue
        if key not in admitted and (kind != 'named' or set(config) - NAMED_SOURCE_FIELDS):
            continue
        if key in factory_ids:
            axes = list(native_axes)
        else:
            try:
                motion = Motion(config, project.base, project.clock)
            except _INVALID_NATIVE_INPUT:
                # An unused configured source is not a usable binding choice.
                # Bound sources already passed native validation.
                continue
            axes = list(motion.value_dimensions)
        if not 1 <= len(axes) <= MAX_AXES or any(not isinstance(axis, str) or not axis or len(axis) > 128 for axis in axes):
            raise AuthoringControlError('catalog_limit')
        editable = kind == 'named' and not set(config) - NAMED_SOURCE_FIELDS
        fields = [_normalise_descriptor(prop, row, group='motion', config=config)
                  for prop, row in named_motion_descriptors().items()] if editable else []
        label = config.get('pattern', f'Configured {kind} {len(result) + 1}')
        if kind == 'named' and key not in factory_ids:
            label = f'{label} · configured {key[:8]}'
        result.append({'id': f'motion:{key}', 'label': label,
                       'kind': kind, 'axes': axes, 'motion': deepcopy(config), 'motion_fields': fields})
        if len(result) > MAX_MOTIONS:
            raise AuthoringControlError('catalog_limit')
    return result


def authoring_control_catalog(project, document, original, node, *, request_sha256):
    """Discover a selected node from an already prepared, validated Project."""
    graph = EffectGraph(project.effect_graph_spec)
    locations = _locations(document, graph)
    if not isinstance(node, str) or node not in graph.nodes:
        raise AuthoringControlError('node_invalid')
    targets = [{'id': name, 'type': native.kind, 'label': locations[name].label,
                'authored_id': locations[name].authored_id} for name, native in graph.nodes.items()]
    controls = document.get('effect_graph_controls', [])
    fields = modulation_descriptors()
    fields['choices'] = {'type': 'choices', 'default': [], 'min_items': 2, 'max_items': 32}
    result = {
        'schema': CATALOG_SCHEMA, 'request_sha256': request_sha256,
        'node': next(row for row in targets if row['id'] == node),
        'targets': targets, 'parameters': _parameter_descriptors(graph, node),
        'bindings': [{'index': index, 'config': deepcopy(row)} for index, row in enumerate(controls)
                     if row['node'] == node],
        'motions': _motion_catalog(project, original, document),
        'binding_fields': [_normalise_descriptor(prop, row) for prop, row in fields.items()],
        'limits': {'bindings': MAX_BINDINGS},
        'scope': {'rendered': False, 'gpu_context_opened': False},
    }
    if len(json.dumps(result, allow_nan=False).encode('utf-8')) > MAX_CATALOG_BYTES:
        raise AuthoringControlError('catalog_limit')
    return result


@contextmanager
def _prepared(project_file, document, sidecars, validate_draft, temp_parent):
    owner = NativePreviewRenderer(project_file, document, sidecars, validate_draft=validate_draft,
                                  temp_parent=Path(temp_parent))
    try:
        owner._prepare()
        identity = canonical_digest({'project': document, 'sidecars': sidecars})
        report = validate_authoring_graph(owner.project, request_sha256=identity)
        if not report['valid']:
            raise AuthoringControlError('graph_invalid')
        yield owner.project, report
        try:
            owner.project.verify_inputs()
            owner.input_guard()
        except (ValueError, OSError, RuntimeError) as exc:
            raise AuthoringControlError('input_changed') from exc
    finally:
        owner.close()


def control_catalog_draft(project_file, draft, *, sidecars, node, validate_draft, temp_parent):
    """Read-only catalogue with the preview's pinned input/owned sidecar guard."""
    try:
        original = Project.load(project_file).data
        with _prepared(project_file, draft, sidecars, validate_draft, temp_parent) as (project, report):
            result = authoring_control_catalog(project, draft, original, node,
                                               request_sha256=report['request_sha256'])
        return result
    except AuthoringControlError:
        raise
    except _INVALID_NATIVE_INPUT as exc:
        raise AuthoringControlError('graph_invalid') from exc


def _binding_index(controls, index, node):
    if type(index) is not int or not 0 <= index < len(controls) or controls[index].get('node') != node:
        raise AuthoringControlError('binding_invalid')
    return index


def _apply_parameter(document, graph, locations, node, operation, parameters):
    if set(operation) != {'kind', 'property', 'value'}:
        raise AuthoringControlError('parameter_readonly')
    prop = operation['property']
    descriptor = next((row for row in parameters if row['property'] == prop), None)
    if descriptor is None or not descriptor['editable']:
        raise AuthoringControlError('parameter_readonly')
    updated = graph.apply_control_values({(node, prop): operation['value']})
    before, after = graph.nodes[node].config, updated.nodes[node].config
    location = locations[node]
    authored = document
    for segment in location.path:
        authored = authored[segment]
    for key in set(before) | set(after):
        if before.get(key) == after.get(key) and (key in before) == (key in after):
            continue
        if key in {'id', 'type', 'inputs'} or location.chain_output and key != 'opacity':
            raise AuthoringControlError('parameter_readonly')
        if key in after:
            authored[key] = deepcopy(after[key])
        else:
            authored.pop(key, None)


def _apply_binding(document, graph, node, operation, catalog):
    controls = document.setdefault('effect_graph_controls', [])
    kind = operation['kind']
    if kind == 'remove_binding':
        if set(operation) != {'kind', 'index'}:
            raise AuthoringControlError('binding_invalid')
        del controls[_binding_index(controls, operation['index'], node)]
        return
    if kind == 'move_binding':
        if (set(operation) != {'kind', 'index', 'direction'} or type(operation['direction']) is not int
                or operation['direction'] not in {-1, 1}):
            raise AuthoringControlError('binding_invalid')
        index = _binding_index(controls, operation['index'], node)
        positions = [i for i, row in enumerate(controls) if row['node'] == node]
        target = positions.index(index) + operation['direction']
        if not 0 <= target < len(positions):
            raise AuthoringControlError('binding_invalid')
        other = positions[target]
        controls[index], controls[other] = controls[other], controls[index]
        return
    if (kind != 'put_binding' or set(operation) - {
            'kind', 'index', 'property', 'motion_id', 'settings', 'motion_settings'}
            or not {'index', 'property', 'motion_id', 'settings'} <= operation.keys()):
        raise AuthoringControlError('binding_invalid')
    prop = operation['property']
    descriptor = next((row for row in catalog['parameters'] if row['property'] == prop), None)
    if descriptor is None or not descriptor['bindable']:
        raise AuthoringControlError('binding_invalid')
    source = next((row for row in catalog['motions'] if row['id'] == operation['motion_id']), None)
    if source is None:
        raise AuthoringControlError('motion_unavailable')
    settings = operation['settings']
    if not isinstance(settings, dict) or set(settings) - BINDING_SETTINGS:
        raise AuthoringControlError('binding_invalid')
    motion = deepcopy(source['motion'])
    changes = operation.get('motion_settings', {})
    if (not isinstance(changes, dict) or set(changes) - set(named_motion_descriptors())
            or changes and (source['kind'] != 'named' or not source['motion_fields'])):
        raise AuthoringControlError('motion_unavailable')
    if 'interpolate' in changes and not isinstance(changes['interpolate'], bool):
        raise AuthoringControlError('motion_unavailable')
    motion.update(deepcopy(changes))
    row = {'node': node, 'property': prop, 'motion': motion, **deepcopy(settings)}
    if 'range' not in row and 'choices' not in row:
        row.update(deepcopy(descriptor.get('binding_defaults', {})))
    interval = row.get('range')
    if (row.get('combine', 'set') == 'set' and row.get('weight', 1) == 1
            and isinstance(interval, (list, tuple)) and len(interval) == 2):
        selected = _selected_graph(graph, node)
        for value in interval:
            selected.apply_control_values({('selected', prop): value})
    index = operation['index']
    if index is None:
        if len(controls) >= MAX_BINDINGS:
            raise AuthoringControlError('binding_invalid')
        controls.append(row)
    else:
        controls[_binding_index(controls, index, node)] = row


def edit_graph_draft(project_file, draft, *, sidecars, node, operation, validate_draft, temp_parent):
    """Apply one native operation to a clone, then validate the complete result.

    Only original-path draft data is returned. Preparation's temporary sidecar
    references and native resolved shader paths never enter the returned draft.
    """
    try:
        if not isinstance(operation, dict) or not isinstance(operation.get('kind'), str):
            raise AuthoringControlError('edit_rejected')
        original = Project.load(project_file).data
        identity = canonical_digest({'project': draft, 'sidecars': sidecars})
        candidate = deepcopy(draft)
        with _prepared(project_file, draft, sidecars, validate_draft, temp_parent) as (project, _report):
            catalog = authoring_control_catalog(project, draft, original, node, request_sha256=identity)
            graph = EffectGraph(project.effect_graph_spec)
            if operation['kind'] == 'set_parameter':
                _apply_parameter(candidate, graph, _locations(draft, graph), node, operation, catalog['parameters'])
            else:
                _apply_binding(candidate, graph, node, operation, catalog)
        validate_draft(original, candidate)
        with _prepared(project_file, candidate, sidecars, validate_draft, temp_parent) as (project, validation):
            catalog = authoring_control_catalog(project, candidate, original, node,
                                               request_sha256=validation['request_sha256'])
        result = {'schema': EDIT_SCHEMA, 'request_sha256': identity, 'project': candidate,
                  'validation': validation, 'catalog': catalog}
        if len(json.dumps(result, allow_nan=False).encode('utf-8')) > MAX_EDIT_BYTES:
            raise AuthoringControlError('catalog_limit')
        return result
    except AuthoringControlError:
        raise
    except _INVALID_NATIVE_INPUT as exc:
        raise AuthoringControlError('edit_rejected') from exc
