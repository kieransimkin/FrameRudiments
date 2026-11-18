"""Native parameter discovery for every effect-graph node family.

Descriptors are detached metadata, not another evaluator. Scalar values use the
same bounds and resolver as rendering; existing effect-family registries own
their specialised controls. Static edits revalidate the complete native graph.
"""
from __future__ import annotations

from copy import deepcopy

from .effect_graph_fields import field_parameter_descriptors
from .effect_graph_filter import filter_parameter_descriptors
from .effect_graph_raster import raster_parameter_descriptors
from .effect_graph_spatial import spatial_parameter_descriptors
from .effect_graph_style_contract import style_control_schema
from .effect_graph_spectral import (
    BAND_COLOUR_PROPERTIES, BAND_FLOAT_PROPERTIES, MAX_OFFSET, SPECTRAL_SPACES,
)
from .effect_graph_transition_geometry import GEOMETRIC_STYLES, GEOMETRY_RANGES
from .effect_graph_transition_mattes import MATTE_TRANSITION_ONLY
from .effect_graph_transition_spectral import (
    COLOUR_FIELDS, MASK_SOURCES, MAX_ECHOES, SCALAR_FIELDS, VECTOR_FIELDS,
)
from .effect_graph_transition_timing import TIMING_DEFAULTS, TIMING_ENUMS, TIMING_RANGES


GENERIC_DEFAULTS = {
    'opacity': 1, 'mix': 1, 'feather': 0, 'tolerance': .1, 'softness': .1,
    'despill': 0, 'progress': 0, 'threshold': .5, 'mask_gamma': 1,
    'expand': 0, 'mask_opacity': 1, 'edge_expand': 0, 'edge_feather': 0,
    'edge_decontaminate': 0, 'edge_radius': 3,
}


def generic_control_bounds(kind, name):
    """The scalar bounds used by EffectGraph's actual frame-local resolver."""
    if kind == 'transition' and name in GEOMETRY_RANGES:
        return GEOMETRY_RANGES[name]
    if name.startswith('center.'):
        return (0, 1)
    if name.endswith(('.x', '.y')):
        return (-4096, 4096)
    if name.startswith(('key_color.', 'key_colors.')):
        return (0, 255)
    if name in {'expand', 'edge_expand'}:
        return (-64, 64)
    if name == 'feather' and kind == 'matte' or name == 'edge_feather':
        return (0, 128)
    if name == 'edge_radius':
        return (.5, 16)
    if name == 'mask_gamma':
        return (.1, 8)
    if name == 'time_seconds':
        return (0, 1e8)
    if name == 'time_delta_seconds':
        return (0, 3600)
    return (0, 1)


def _transition_applicability(config, name):
    """Separate structurally applicable controls from temporarily dormant ones."""
    style = config.get('transition_style', 'dissolve')
    if name in {'opacity', 'progress', 'transition_style'} or name.startswith('timing.'):
        return True, True
    if style == 'gl_transition':
        return name in {'time_seconds', 'time_delta_seconds'}, True
    if name in {'time_seconds', 'time_delta_seconds'}:
        return False, False
    base = name.split('.')[0]
    if base in MATTE_TRANSITION_ONLY:
        active = not (base in {'key_color', 'key_space', 'tolerance', 'softness'}
                      and config.get('matte_channel', 'alpha') != 'chroma')
        return style == 'matte', active
    if name in {'wash_seed', 'wash_variant'}:
        return style == 'wash', style == 'wash'
    if name in GEOMETRY_RANGES:
        used = {
            'strength': GEOMETRIC_STYLES, 'zoom': {'zoom'},
            'rotation': {'rotate', 'kaleidoscope'}, 'twist': {'polar', 'kaleidoscope'},
            'segments': {'kaleidoscope', 'wash'}, 'frequency': {'liquid', 'wash'},
            'amplitude': {'liquid', 'wash'}, 'phase': {'liquid', 'wash'},
        }
        return style in GEOMETRIC_STYLES, style in used.get(name, set())
    if name.startswith('center.'):
        return True, style == 'radial' or style in GEOMETRIC_STYLES - {'liquid'}
    if name == 'direction':
        return True, style == 'wipe' or style in GEOMETRIC_STYLES
    if name == 'feather':
        return True, style in {'wipe', 'radial', 'matte', *GEOMETRIC_STYLES}
    return True, True


def _number(value, default, bounds, *, integer=False, units='ratio', active=True):
    return dict(type='integer' if integer else 'number', value=value, default=default,
                range=list(bounds), units=units, active=active, animatable=True,
                interpolation='nearest' if integer else 'linear')


def _categorical(value, default, choices, *, active=True):
    return dict(type='boolean' if type(value) is bool else 'enum', value=value,
                default=default, choices=sorted(choices), active=active,
                animatable=True, interpolation='discrete')


def _generic_descriptors(graph, node):
    # Lazy import keeps the shared scalar constants usable by the graph resolver.
    from .effect_graph import CONTROL_TARGETS, ENUM_CONTROLS, ENUM_DEFAULTS, GEOMETRY_DEFAULTS

    rows = {}
    names = set(CONTROL_TARGETS[node.kind])
    if node.kind == 'chroma_key':
        names.update(f'key_colors.{index}.{axis}'
                     for index in range(len(node.config.get('key_colors', []))) for axis in 'rgb')
    for name in sorted(names):
        try:
            graph.validate_control_target(node.id, name)
        except ValueError:
            continue
        applicable, active = (_transition_applicability(node.config, name)
                              if node.kind == 'transition' else (True, True))
        if not applicable:
            continue
        value = graph.base_control_value(node.id, name)
        choices = ENUM_CONTROLS.get(node.kind, {}).get(name)
        if choices is not None:
            rows[name] = _categorical(value, ENUM_DEFAULTS[name], choices, active=active)
            continue
        bounds = generic_control_bounds(node.kind, name)
        integer = name in {'segments', 'wash_seed'} or name.startswith(('key_color.', 'key_colors.'))
        default = (None if name.startswith('key_colors.') else
                   int(name.endswith('.g')) * 255 if name.startswith('key_color.') else
                   .5 if name.startswith('center.') else 0 if name.endswith(('.x', '.y')) else
                   GEOMETRY_DEFAULTS.get(name, 0) if name in GEOMETRY_RANGES else
                   GENERIC_DEFAULTS.get(name, 0))
        units = ('channel_255' if name.startswith(('key_color.', 'key_colors.')) else
                 'normalized' if name.startswith('center.') else
                 'pixels' if name.endswith(('.x', '.y')) or name in {
                     'expand', 'edge_expand', 'edge_feather', 'edge_radius', 'amplitude'}
                 or name == 'feather' and node.kind == 'matte' else
                 'seconds' if name in {'time_seconds', 'time_delta_seconds'} else
                 'degrees' if name == 'rotation' else 'radians' if name == 'phase' else
                 'integer' if integer else 'ratio')
        rows[name] = _number(value, default, bounds, integer=integer, units=units, active=active)
    if node.kind == 'transition' and isinstance(node.config.get('timing'), dict):
        timing = node.config['timing']
        for key in sorted(TIMING_ENUMS.keys() | TIMING_RANGES.keys()):
            if key == 'preset' and 'keys' in timing:
                continue
            if key == 'period_beats' and timing.get('repeat', 'once') == 'once':
                continue
            name = f'timing.{key}'
            value = graph.base_control_value(node.id, name)
            default = TIMING_DEFAULTS.get(key, timing.get('duration_beats', 4))
            row = (_categorical(value, default, TIMING_ENUMS[key]) if key in TIMING_ENUMS else
                   _number(value, default, TIMING_RANGES[key], integer=key == 'steps',
                           units='integer' if key == 'steps' else 'beats'))
            if key == 'steps':
                row['constraints'] = ['zero_or_integer_from_2_to_64']
            if key in {'period_beats', 'duration_beats'}:
                row['constraints'] = ['period_must_not_be_shorter_than_duration']
            rows[name] = row
    if node.kind == 'spectral_displacement' or (
            node.kind == 'transition' and node.config.get('transition_style') == 'spectral'):
        _spectral_descriptors(graph, node, rows)
    return rows


def _spectral_descriptors(graph, node, rows):
    transition = node.kind == 'transition'
    if transition:
        from .effect_graph import ENUM_CONTROLS, ENUM_DEFAULTS
        rows['mix'] = _number(graph.base_control_value(node.id, 'mix'), 1, (0, 1))
        rows['alpha_mode'] = _categorical(graph.base_control_value(node.id, 'alpha_mode'),
                                         ENUM_DEFAULTS['alpha_mode'], ENUM_CONTROLS['transition']['alpha_mode'])
    for band in node.config['bands']:
        prefix = f"bands.{band['id']}."
        scalars = SCALAR_FIELDS if transition else BAND_FLOAT_PROPERTIES
        colors = COLOUR_FIELDS if transition else BAND_COLOUR_PROPERTIES
        vectors = VECTOR_FIELDS if transition else {'offset'}
        for field in sorted(scalars):
            name = prefix + field
            rows[name] = _number(graph.base_control_value(node.id, name), None, (0, 1))
            if field in {'start', 'end'}:
                rows[name]['constraints'] = ['band_start_must_precede_end']
        for field in sorted(colors):
            for axis in 'rgb':
                name = prefix + field + '.' + axis
                rows[name] = _number(graph.base_control_value(node.id, name), None, (0, 255),
                                     integer=True, units='channel_255')
        for field in sorted(vectors):
            for axis in 'xy':
                name = prefix + field + '.' + axis
                rows[name] = _number(graph.base_control_value(node.id, name), None,
                                     (-MAX_OFFSET, MAX_OFFSET), units='pixels')
        name = prefix + 'space'
        rows[name] = _categorical(graph.base_control_value(node.id, name), None, SPECTRAL_SPACES)
        if transition:
            name = prefix + 'mask_source'
            rows[name] = _categorical(graph.base_control_value(node.id, name), None, MASK_SOURCES)
            name = prefix + 'echoes'
            rows[name] = _number(graph.base_control_value(node.id, name), None, (0, MAX_ECHOES), integer=True)


def _safe_choices(graph, node, name, row):
    """Use native validators to omit choices requiring other structural edits.

    Validate only the selected node's configuration against equivalent source
    ports, so discovery cannot repeatedly open unrelated pinned shaders.
    """
    from .effect_graph import EffectGraph

    choices = []
    for choice in row['choices']:
        if type(choice) is type(row['value']) and choice == row['value']:
            choices.append(choice)
            continue
        try:
            graph.validate_control_choices(node.id, name, [row['value'], choice])
            config = graph._resolved_configs({(node.id, name): choice})[node.id]
            inputs = [f'input_{index}' for index in range(len(node.inputs))]
            EffectGraph({'nodes': [{'id': key, 'type': 'source'} for key in inputs] + [
                {**config, 'id': 'selected', 'inputs': inputs}], 'output': 'selected'})
        except ValueError:
            continue
        choices.append(choice)
    return choices


def graph_parameter_descriptors(graph, node_id, *, active_only=False):
    """Return one detached, typed native registry, including dynamic paths."""
    if type(active_only) is not bool:
        raise ValueError('active_only must be boolean')
    node = graph.nodes.get(node_id)
    if node is None:
        raise ValueError('unknown graph node for parameter discovery')
    functions = {
        'procedural_field': field_parameter_descriptors,
        'raster_effect': raster_parameter_descriptors,
        'spatial_warp': spatial_parameter_descriptors,
        'layer_style': style_control_schema,
    }
    if node.kind in functions:
        rows = functions[node.kind](node.config, input_count=len(node.inputs))
    elif node.kind == 'filter':
        rows = filter_parameter_descriptors(node.config)
    else:
        rows = _generic_descriptors(graph, node)
    for name, row in rows.items():
        row.setdefault('group', '.'.join(name.split('.')[:2]) if name.startswith(
            ('bands.', 'gradient_stops.', 'field_palette.', 'key_colors.')) else name.split('.')[0])
        row.setdefault('factory_default', deepcopy(row.get('default')))
        row.setdefault('default_semantics', 'factory_default')
        row.setdefault('constraints', [])
        row.setdefault('editable', True)
        if row['type'] in {'enum', 'boolean'}:
            row['choices'] = _safe_choices(graph, node, name, row)
            row['can_vary'] = len(row['choices']) > 1
        else:
            row.setdefault('can_vary', True)
        row['bindable'] = bool(row.get('animatable', True) and row['can_vary'])
        if name == 'timing.steps':
            row['bindable'] = False
            row['reason'] = ('Native timing steps require exactly 0 or an integer from 2 to 64; '
                             'a continuous motion driver cannot guarantee that set of values.')
    return {name: deepcopy(row) for name, row in sorted(rows.items())
            if not active_only or row.get('active', True)}
