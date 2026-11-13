"""Deterministic CPU effect DAG with alpha-correct two-input transitions.

Compositor integration supplies source RGBA images and per-frame DanceRudiment
controls. Temporal feedback and GPU graph execution remain later milestones.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from collections.abc import Mapping
from math import isfinite
from PIL import Image, ImageColor

from .effect_graph_fields import (
    FIELD_CONTROLS, FIELD_ENUMS, FIELD_FIELDS, field_capabilities,
    field_control_apply, field_control_base, field_control_valid,
    field_parameter_descriptors, render_procedural_field, validate_field,
)
from .effect_graph_raster import (
    RASTER_CONTROLS, RASTER_ENUMS, RASTER_FIELDS, raster_capabilities,
    raster_control_apply, raster_control_base, raster_parameter_descriptors,
    render_raster_with_alpha, validate_raster,
)
from .effect_graph_spatial import (
    WARP_CONTROLS, WARP_ENUMS, WARP_FIELDS, spatial_capabilities,
    spatial_control_apply, spatial_control_base, spatial_parameter_descriptors,
    render_spatial_warp_with_alpha, spatial_is_identity, validate_spatial,
)
from .effect_graph_blend import COMPOSITE_MODES, composite_blend
from .effect_graph_style_contract import (
    completed_style_report, graph_style_manifest, style_control_schema, style_node_report,
)
from .effect_graph_layer_styles import (
    STYLE_CONTROLS, STYLE_ENUMS, STYLE_FIELDS, render_layer_style,
    style_control_valid, validate_style_inputs,
    style_control_apply, style_control_base, validate_style,
)
from .effect_graph_filter import (
    FILTER_FIELDS, FILTER_CONTROLS, FILTER_ENUMS, filter_capabilities,
    filter_control_apply, filter_control_base, filter_parameter_descriptors,
    render_filter, validate_filter,
)
from .effect_graph_transitions import render_transition
from .effect_graph_transition_chains import expand_transition_chains
from .effect_graph_transition_timing import (
    TIMING_DEFAULTS, TIMING_ENUMS, TIMING_RANGES,
    transition_timing_progress, validate_transition_timing,
)
from .effect_graph_transition_geometry import (
    GEOMETRIC_STYLES, GEOMETRY_DEFAULTS, GEOMETRY_RANGES, validate_transition_geometry,
)
from .effect_graph_ripples import RIPPLE_VARIANTS
from .effect_graph_transition_mattes import MATTE_TRANSITION_CHANNELS, validate_transition_matte
from .effect_graph_transition_spectral import (
    control_apply as spectral_transition_control_apply,
    control_base as spectral_transition_control_base,
    control_choices as spectral_transition_control_choices,
    control_parts as spectral_transition_control_parts, validate_transition_spectral,
)
from .effect_graph_mattes import (
    ALPHA_SOURCES, MATTE_COMBINES, KEY_SPACES, apply_matte, chroma_key, validate_key, validate_matte,
)
from .effect_graph_spectral import (
    SPECTRAL_ALPHA_MODES, SPECTRAL_MODES, SPECTRAL_SPACES,
    spectral_control_apply, spectral_control_base, spectral_control_parts,
    spectral_displacement, validate_spectral,
)

MAX_NODES = 256
MAX_INPUTS = 32
KINDS = {'source', 'copy', 'opacity', 'matte', 'chroma_key', 'chromatic_displacement',
         'spectral_displacement', 'composite', 'transition', 'layer_style', 'spatial_warp', 'raster_effect', 'procedural_field', 'filter'}
TRANSITION_STYLES = {'dissolve', 'wipe', 'radial', 'matte', 'spectral', *GEOMETRIC_STYLES}
TRANSITION_DIRECTIONS = {'right', 'left', 'down', 'up'}
CONTROL_TARGETS = {
    'filter': set(FILTER_CONTROLS),
    'procedural_field': set(FIELD_CONTROLS),
    'raster_effect': set(RASTER_CONTROLS),
    'spatial_warp': set(WARP_CONTROLS),
    'layer_style': set(STYLE_CONTROLS),
    'opacity': {'opacity'},
    'matte': {'feather', 'expand', 'mask_opacity', 'mask_combine', 'matte_channel', 'invert', 'alpha_source'},
    'chroma_key': {'tolerance', 'softness', 'despill', 'edge_expand', 'edge_feather',
                   'edge_decontaminate', 'edge_radius', 'key_color.r', 'key_color.g', 'key_color.b',
                   'key_space', 'alpha_source'},
    'chromatic_displacement': {f'{c}_offset.{axis}' for c in ('red', 'green', 'blue') for axis in ('x', 'y')},
    'spectral_displacement': {'mix', 'spectral_mode', 'alpha_mode'},
    'transition': {'progress', 'feather', 'center.x', 'center.y', 'transition_style', 'direction',
                   'threshold', 'mask_gamma', 'matte_channel', 'invert', 'key_color.r',
                   'key_color.g', 'key_color.b', 'key_space', 'tolerance', 'softness',
                   'wash_variant', *GEOMETRY_RANGES},
}
# Every image-processing node has an independent output-opacity control.
for _targets in CONTROL_TARGETS.values():
    _targets.add('opacity')
CONTROL_TARGETS['source'] = {'opacity'}
CONTROL_TARGETS['copy'] = {'opacity'}
CONTROL_TARGETS['composite'] = {'opacity', 'mode'}
ENUM_CONTROLS = {
    'filter': FILTER_ENUMS,
    'procedural_field': FIELD_ENUMS,
    'raster_effect': RASTER_ENUMS,
    'spatial_warp': WARP_ENUMS,
    'layer_style': STYLE_ENUMS,
    'matte': {'mask_combine': MATTE_COMBINES, 'matte_channel': frozenset({'alpha', 'luminance'}),
              'alpha_source': ALPHA_SOURCES,
              'invert': frozenset({True, False})},
    'chroma_key': {'key_space': KEY_SPACES, 'alpha_source': ALPHA_SOURCES},
    'transition': {'transition_style': TRANSITION_STYLES, 'direction': TRANSITION_DIRECTIONS,
                   'matte_channel': MATTE_TRANSITION_CHANNELS, 'invert': frozenset({True, False}),
                   'key_space': KEY_SPACES, 'alpha_mode': SPECTRAL_ALPHA_MODES,
                   'wash_variant': RIPPLE_VARIANTS},
    'spectral_displacement': {'spectral_mode': SPECTRAL_MODES, 'alpha_mode': SPECTRAL_ALPHA_MODES},
    'composite': {'mode': COMPOSITE_MODES},
}
ENUM_DEFAULTS = {'alpha_source': 'current', 'mask_combine': 'multiply', 'matte_channel': 'alpha', 'invert': False,
                 'key_space': 'rgb', 'transition_style': 'dissolve', 'direction': 'right', 'mode': 'over',
                 'spectral_mode': 'overlay', 'alpha_mode': 'expanded'}
ENUM_DEFAULTS['wash_variant'] = 'rings'




@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    inputs: tuple[str, ...]
    config: dict


class EffectGraph:
    """Validate a bounded DAG and execute using immutable-by-contract RGBA inputs."""

    def __init__(self, spec: dict):
        spec, self.chain_expansions = expand_transition_chains(spec, max_nodes=MAX_NODES)
        if not isinstance(spec, dict) or set(spec) != {'nodes', 'output'}:
            raise ValueError('graph must contain exactly nodes and output')
        entries = spec['nodes']
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_NODES:
            raise ValueError('graph must contain 1..256 nodes')
        self.nodes = {}
        for entry in entries:
            if not isinstance(entry, dict) or not {'id', 'type'} <= entry.keys():
                raise ValueError('invalid node')
            if set(entry) - {'id', 'type', 'inputs', 'opacity', 'mode', 'invert', 'feather', 'key_color', 'tolerance', 'softness', 'despill', 'matte_channel', 'red_offset', 'green_offset', 'blue_offset', 'transition_style', 'direction', 'progress', 'center', 'expand', 'mask_opacity', 'mask_combine', 'key_space',
                               'key_colors', 'edge_expand', 'edge_feather', 'edge_decontaminate', 'edge_radius', 'alpha_source',
                               'bands', 'spectral_mode', 'alpha_mode', 'mix', 'threshold', 'mask_gamma',
                               'timing', 'wash_variant', *GEOMETRY_RANGES, *STYLE_FIELDS, *WARP_FIELDS, *RASTER_FIELDS, *FIELD_FIELDS, *FILTER_FIELDS}:
                raise ValueError('unknown node property')
            key, kind = entry['id'], entry['type']
            if not isinstance(key, str) or not key or len(key) > 128 or key in self.nodes:
                raise ValueError('duplicate or invalid node id')
            if kind not in KINDS:
                raise ValueError(f'unsupported node type: {kind}')
            if 'alpha_source' in entry and kind not in {'matte', 'chroma_key', 'layer_style'}:
                raise ValueError('alpha_source is only supported on matte, chroma_key and layer_style nodes')
            if kind != 'layer_style' and (STYLE_FIELDS - {'alpha_source'}) & entry.keys():
                raise ValueError('layer-style properties require a layer_style node')
            if kind == 'layer_style':
                validate_style(entry)
            if kind != 'spatial_warp' and WARP_FIELDS & entry.keys():
                raise ValueError('warp properties require a spatial_warp node')
            if kind != 'procedural_field' and FIELD_FIELDS & entry.keys():
                raise ValueError('field properties require a procedural_field node')
            if kind != 'raster_effect' and RASTER_FIELDS & entry.keys():
                raise ValueError('raster properties require a raster_effect node')
            if kind != 'filter' and FILTER_FIELDS & entry.keys():
                raise ValueError('filter properties require a filter node')
            if 'timing' in entry and kind != 'transition':
                raise ValueError('timing is only supported on transition nodes')
            inputs = entry.get('inputs', [])
            if not isinstance(inputs, list) or len(inputs) > MAX_INPUTS or not all(isinstance(i, str) for i in inputs):
                raise ValueError('invalid inputs')
            if (kind == 'source' and inputs) or (kind in {'copy', 'opacity', 'chroma_key', 'chromatic_displacement', 'spectral_displacement', 'filter'} and len(inputs) != 1) or (kind == 'matte' and not 2 <= len(inputs) <= 9) or (kind == 'composite' and len(inputs) < 2) or (kind == 'transition' and len(inputs) != (3 if entry.get('transition_style') == 'matte' else 2)):
                raise ValueError(f'wrong input count for {kind}')
            if kind == 'layer_style':
                validate_style_inputs(entry, len(inputs))
            if kind == 'filter':
                validate_filter(entry, len(inputs))
            if kind == 'procedural_field':
                validate_field(entry, len(inputs))
            if kind == 'raster_effect':
                validate_raster(entry, len(inputs))
            if kind == 'spatial_warp':
                validate_spatial(entry, len(inputs))
            opacity = entry.get('opacity', 1.0)
            if type(opacity) not in (int, float) or not isfinite(opacity) or not 0 <= opacity <= 1:
                raise ValueError('opacity must be finite and between 0 and 1')
            if kind == 'matte':
                validate_matte(entry, len(inputs))
            if kind == 'chromatic_displacement':
                for channel in ('red_offset', 'green_offset', 'blue_offset'):
                    vector = entry.get(channel, [0, 0])
                    if (not isinstance(vector, (tuple, list)) or len(vector) != 2
                            or any(type(v) not in (int, float) or not isfinite(v)
                                   or abs(v) > 4096 for v in vector)):
                        raise ValueError(f'invalid {channel} displacement vector')
            if kind == 'chroma_key':
                validate_key(entry)
            if kind == 'spectral_displacement':
                validate_spectral(entry)
            if kind == 'transition':
                if entry.get('transition_style', 'dissolve') not in TRANSITION_STYLES:
                    raise ValueError('unsupported transition style')
                validate_transition_matte(entry)
                validate_transition_spectral(entry)
                validate_transition_geometry(entry)
                if 'timing' in entry:
                    validate_transition_timing(entry['timing'])
                if entry.get('direction', 'right') not in TRANSITION_DIRECTIONS:
                    raise ValueError('unsupported transition direction')
                for name, default, maximum in [('progress', 0.0, 1.0), ('feather', 0.0, 1.0)]:
                    value = entry.get(name, default)
                    if type(value) not in (float, int) or not isfinite(value) or not 0 <= value <= maximum:
                        raise ValueError(f'invalid transition {name}')
                center = entry.get('center', [0.5, 0.5])
                if (not isinstance(center, (tuple, list)) or len(center) != 2
                        or any(type(v) not in (float, int) or not isfinite(v) or not 0 <= v <= 1
                               for v in center)):
                    raise ValueError('invalid transition center')
            accepted_modes = COMPOSITE_MODES if kind == 'composite' else {'over', 'add'}
            if not isinstance(entry.get('mode', 'over'), str) or entry.get('mode', 'over') not in accepted_modes:
                raise ValueError('unsupported blend mode')
            self.nodes[key] = Node(key, kind, tuple(inputs), entry)
        self.output = spec['output']
        if self.output not in self.nodes:
            raise ValueError('unknown output')
        self.order = []
        states = {}

        def visit(key):
            if key not in self.nodes:
                raise ValueError(f'unknown input: {key}')
            if states.get(key) == 1:
                raise ValueError('effect graph cycle')
            if states.get(key) == 2:
                return
            states[key] = 1
            for upstream in self.nodes[key].inputs:
                visit(upstream)
            states[key] = 2
            self.order.append(key)

        visit(self.output)
        # Only nodes reachable from the selected output participate in execution.
        # The compositor uses this set to validate provider names at preparation
        # and to supply transparent images for valid but inactive layers.
        self.source_ids = frozenset(
            key for key in self.order if self.nodes[key].kind == 'source'
        )
        self.layer_style_ids = frozenset(
            key for key in self.order if self.nodes[key].kind == 'layer_style'
        )
        # Diagnostics only: never read by rendering or native motion evaluation.
        self._style_render_report = {}


    def validate_control_target(self, node_id, property_name):
        node = self.nodes.get(node_id)
        if node is not None and node.kind == 'procedural_field':
            if property_name == 'opacity' or field_control_valid(node.config, property_name):
                return
            raise ValueError('unknown procedural_field control target')
        if node is not None and node.kind == 'layer_style':
            if property_name == 'opacity' or style_control_valid(node.config, property_name):
                return
            raise ValueError('unknown or inapplicable layer_style control target')
        if (node is not None and node.kind == 'transition'
                and isinstance(property_name, str) and property_name.startswith('timing.')
                and isinstance(node.config.get('timing'), dict)
                and property_name.removeprefix('timing.') in (TIMING_ENUMS.keys() | TIMING_RANGES.keys())):
            if property_name == 'timing.preset' and 'keys' in node.config['timing']:
                raise ValueError('timing preset cannot override keyframes')
            return
        if (node is not None and node.kind == 'transition'
                and property_name in GEOMETRY_RANGES
                and node.config.get('transition_style', 'dissolve') not in GEOMETRIC_STYLES):
            raise ValueError('geometric control target requires geometric transition style')
        if (node is not None and node.kind == 'transition' and property_name == 'wash_variant'
                and node.config.get('transition_style') != 'wash'):
            raise ValueError('wash variant control requires wash transition style')
        if node is None or not isinstance(property_name, str) or (
                property_name not in CONTROL_TARGETS.get(node.kind, set())
                and not self._is_extra_key_colour_target(node, property_name)
                and not (node.kind == 'spectral_displacement'
                         and spectral_control_parts(node.config, property_name) is not None)
                and not (node.kind == 'transition' and node.config.get('transition_style') == 'spectral'
                         and (property_name in {'mix', 'alpha_mode'} or
                              spectral_transition_control_parts(node.config, property_name) is not None))):
            raise ValueError('unknown or inapplicable graph control target')

    @staticmethod
    def _is_extra_key_colour_target(node, property_name):
        if node.kind != 'chroma_key' or not isinstance(property_name, str):
            return False
        parts = property_name.split('.')
        return (len(parts) == 3 and parts[0] == 'key_colors' and parts[1].isdigit()
                and parts[2] in {'r', 'g', 'b'} and
                int(parts[1]) < len(node.config.get('key_colors', [])))

    def is_categorical_control(self, node_id, property_name):
        self.validate_control_target(node_id, property_name)
        node = self.nodes[node_id]
        return (node.kind == 'transition' and property_name.startswith('timing.')
                and property_name.removeprefix('timing.') in TIMING_ENUMS
                or property_name in ENUM_CONTROLS.get(node.kind, {})
                or (node.kind == 'spectral_displacement'
                    and spectral_control_parts(node.config, property_name) is not None
                    and property_name.endswith('.space'))
                or (node.kind == 'transition' and node.config.get('transition_style') == 'spectral'
                    and spectral_transition_control_choices(node.config, property_name) is not None))

    def validate_control_choices(self, node_id, property_name, choices):
        self.validate_control_target(node_id, property_name)
        node = self.nodes[node_id]
        accepted = ENUM_CONTROLS.get(node.kind, {}).get(property_name)
        if node.kind == 'transition' and property_name.startswith('timing.'):
            accepted = TIMING_ENUMS.get(property_name.removeprefix('timing.'))
        if (accepted is None and node.kind == 'spectral_displacement'
                and spectral_control_parts(node.config, property_name) is not None
                and property_name.endswith('.space')):
            accepted = SPECTRAL_SPACES
        if (accepted is None and node.kind == 'transition'
                and node.config.get('transition_style') == 'spectral'):
            accepted = spectral_transition_control_choices(node.config, property_name)
        if accepted is None:
            raise ValueError('categorical choices require an enum graph control')
        if (not isinstance(choices, list) or not 2 <= len(choices) <= 32
                or any(type(v) is not bool and not isinstance(v, str) for v in choices)
                or any(v not in accepted for v in choices)
                or len({(type(v), v) for v in choices}) != len(choices)):
            raise ValueError('invalid categorical graph control choices')
        if node.kind == 'spatial_warp' and property_name == 'warp_type':
            for value in choices:
                validate_spatial({**node.config, 'warp_type': value}, len(node.inputs))
        if node.kind == 'layer_style' and property_name in {'style_type', 'pattern_kind'}:
            for value in choices:
                validate_style_inputs(style_control_apply(node.config, property_name, value), len(node.inputs))
        # A transition's input arity is immutable; categorical style changes
        # cannot swap a two-input effect into a three-input matte node.
        if node.kind == 'transition' and property_name == 'transition_style':
            if any((style == 'matte') != (len(node.inputs) == 3) for style in choices):
                raise ValueError('transition style choice changes input count')
            if any((style == 'spectral') != ('bands' in node.config) for style in choices):
                raise ValueError('transition style choice changes spectral bands')
            if GEOMETRY_RANGES.keys() & node.config.keys():
                if any(style not in GEOMETRIC_STYLES for style in choices):
                    raise ValueError('geometric transition settings require geometric style')
            if 'wash_variant' in node.config and any(style != 'wash' for style in choices):
                raise ValueError('wash transition settings require wash style')

    def control_descriptors(self, node_id):
        """Typed M6/M7 controls for authoring; other node descriptors remain separate."""
        node = self.nodes.get(node_id)
        if node is not None and node.kind == 'procedural_field':
            return field_parameter_descriptors(node.config, input_count=len(node.inputs))
        if node is not None and node.kind == 'raster_effect':
            return raster_parameter_descriptors(node.config, input_count=len(node.inputs))
        if node is not None and node.kind == 'spatial_warp':
            return spatial_parameter_descriptors(node.config, input_count=len(node.inputs))
        if node is not None and node.kind == 'filter':
            return filter_parameter_descriptors(node.config)
        if node is None or node.kind != 'layer_style':
            raise ValueError('descriptor discovery requires a style, spatial, raster or procedural node')
        return style_control_schema(node.config, input_count=len(node.inputs))

    def field_manifest(self):
        """Detached M7.5 capabilities and controls for reachable generated fields."""
        return {
            key: {'inputs': list(self.nodes[key].inputs),
                  **field_capabilities(self.nodes[key].config, input_count=len(self.nodes[key].inputs))}
            for key in self.order if self.nodes[key].kind == 'procedural_field'
        }

    def raster_manifest(self):
        """Detached M7.4 capability/control report for reachable raster nodes."""
        return {
            key: {'inputs': list(self.nodes[key].inputs),
                  **raster_capabilities(self.nodes[key].config, input_count=len(self.nodes[key].inputs))}
            for key in self.order if self.nodes[key].kind == 'raster_effect'
        }

    def filter_manifest(self):
        """Detached capability/control inventory for GPU-eligible effect filters."""
        return {key: filter_capabilities(self.nodes[key].config)
                for key in self.order if self.nodes[key].kind == 'filter'}

    def spatial_manifest(self):
        """Detached capability/control report for reachable spatial-warp nodes."""
        return {
            key: {'inputs': list(self.nodes[key].inputs),
                  **spatial_capabilities(self.nodes[key].config, input_count=len(self.nodes[key].inputs))}
            for key in self.order if self.nodes[key].kind == 'spatial_warp'
        }

    def style_manifest(self, *, include_unreachable=False, active_only=False):
        """Detached M6 capability/control inventory, including expanded chain IDs."""
        return graph_style_manifest(self, include_unreachable=include_unreachable, active_only=active_only)

    def style_render_report(self):
        """Last successful adapter-mediated graph sample (not project success).

        The adapter clears this before attempting a new graph evaluation. A
        failure elsewhere in the compositor may leave the previous sample here;
        inspect its sample_context. Direct render() callers use their own report.
        Capture is sample-local, not a temporal-shutter bounds union. Use
        separate compositor instances when rendering in concurrent workers.
        """
        return deepcopy(self._style_render_report)

    def base_control_value(self, node_id, property_name):
        self.validate_control_target(node_id, property_name)
        config = self.nodes[node_id].config
        if self.nodes[node_id].kind == 'procedural_field' and property_name != 'opacity':
            return field_control_base(config, property_name)
        if self.nodes[node_id].kind == 'raster_effect' and property_name != 'opacity':
            return raster_control_base(config, property_name)
        if self.nodes[node_id].kind == 'spatial_warp' and property_name != 'opacity':
            return spatial_control_base(config, property_name)
        if self.nodes[node_id].kind == 'filter' and property_name != 'opacity':
            return filter_control_base(config, property_name)
        if self.nodes[node_id].kind == 'layer_style' and property_name != 'opacity':
            return style_control_base(config, property_name)
        if self.nodes[node_id].kind == 'transition' and property_name.startswith('timing.'):
            name = property_name.removeprefix('timing.')
            return config['timing'].get(name, TIMING_DEFAULTS.get(name, config['timing'].get('duration_beats', 4)))
        if self.nodes[node_id].kind == 'spectral_displacement' and property_name.startswith('bands.'):
            return spectral_control_base(config, property_name)
        if self.nodes[node_id].kind == 'transition' and property_name.startswith('bands.'):
            return spectral_transition_control_base(config, property_name)
        if property_name in ENUM_CONTROLS.get(self.nodes[node_id].kind, {}):
            return config.get(property_name, ENUM_DEFAULTS[property_name])
        if property_name.startswith('key_colors.'):
            _, index, component = property_name.split('.')
            return ImageColor.getrgb(config['key_colors'][int(index)])[{'r': 0, 'g': 1, 'b': 2}[component]]
        if property_name.endswith(('.x', '.y')):
            key, axis = property_name.rsplit('.', 1)
            return config.get(key, [0.5, 0.5] if key == 'center' else [0, 0])[0 if axis == 'x' else 1]
        if property_name.startswith('key_color.'):
            primary = config.get('key_color', (config.get('key_colors') or ['#00ff00'])[0])
            return ImageColor.getrgb(primary)[{'r': 0, 'g': 1, 'b': 2}[property_name[-1]]]
        if self.nodes[node_id].kind == 'transition' and property_name in GEOMETRY_RANGES:
            return config.get(property_name, 0 if property_name == 'wash_seed' else GEOMETRY_DEFAULTS[property_name])
        return config.get(property_name, {'opacity': 1, 'mix': 1, 'feather': 0, 'tolerance': .1,
                                          'softness': .1, 'despill': 0,
                                          'progress': 0, 'threshold': .5, 'mask_gamma': 1,
                                          'expand': 0, 'mask_opacity': 1,
                                          'edge_expand': 0, 'edge_feather': 0, 'edge_decontaminate': 0,
                                          'edge_radius': 3}.get(property_name, 0))

    def backend_plan(self, source_surfaces, **kwargs):
        """Allocation-free, sample-specific backend/resource preflight (M8.1).

        No context is created and no render is performed. Supply native-resolved
        parameter_values/context to inspect the same controls used by render().
        """
        from .effect_graph_backend_contract import plan_effect_graph
        return plan_effect_graph(self, source_surfaces, **kwargs)

    def _resolved_configs(self, parameter_values=None, context=None):
        """One shared resolver for rendering and preflight; authored data stays immutable."""
        parameter_values = parameter_values or {}
        if not isinstance(parameter_values, Mapping):
            raise ValueError('parameter_values must be a mapping')
        configs = {}
        for target, value in parameter_values.items():
            if not isinstance(target, tuple) or len(target) != 2:
                raise ValueError('graph control target must be (node_id, property)')
            node_id, property_name = target
            node = self.nodes.get(node_id)
            self.validate_control_target(node_id, property_name)
            if node.kind == 'procedural_field' and property_name != 'opacity':
                configs[node_id] = field_control_apply(configs.get(node_id, node.config), property_name, value)
                continue
            if node.kind == 'raster_effect' and property_name != 'opacity':
                configs[node_id] = raster_control_apply(configs.get(node_id, node.config), property_name, value)
                continue
            if node.kind == 'spatial_warp' and property_name != 'opacity':
                configs[node_id] = spatial_control_apply(configs.get(node_id, node.config), property_name, value)
                continue
            if node.kind == 'filter' and property_name != 'opacity':
                configs[node_id] = filter_control_apply(configs.get(node_id, node.config), property_name, value)
                continue
            if node.kind == 'layer_style' and property_name != 'opacity':
                configs[node_id] = style_control_apply(
                    configs.get(node_id, node.config), property_name, value
                )
                continue
            if node.kind == 'transition' and property_name.startswith('timing.'):
                part = property_name.removeprefix('timing.')
                if part in TIMING_ENUMS:
                    if (type(value) is not bool and not isinstance(value, str)) or value not in TIMING_ENUMS[part]:
                        raise ValueError('invalid categorical timing control')
                elif (type(value) not in (int, float) or not isfinite(value)
                      or not TIMING_RANGES[part][0] <= value <= TIMING_RANGES[part][1]):
                    raise ValueError('transition timing control out of range')
                if part == 'steps':
                    if not float(value).is_integer() or value == 1:
                        raise ValueError('timing steps must be 0 or 2..64')
                    value = int(value)
                cfg = configs.setdefault(node_id, dict(node.config))
                cfg['timing'] = {**cfg.get('timing', {}), part: value}
                continue
            if node.kind == 'spectral_displacement' and property_name.startswith('bands.'):
                configs[node_id] = spectral_control_apply(
                    configs.get(node_id, node.config), property_name, value
                )
                continue
            if node.kind == 'transition' and property_name.startswith('bands.'):
                configs[node_id] = spectral_transition_control_apply(
                    configs.get(node_id, node.config), property_name, value
                )
                continue
            enum_values = ENUM_CONTROLS.get(node.kind, {}).get(property_name)
            if enum_values is not None:
                if (type(value) is not bool and not isinstance(value, str)) or value not in enum_values:
                    raise ValueError('invalid categorical graph control value')
                configs.setdefault(node_id, dict(node.config))[property_name] = value
                continue
            if type(value) not in (float, int) or not isfinite(value):
                raise ValueError('graph control must be finite numeric')
            if node.kind == 'transition' and property_name in GEOMETRY_RANGES:
                bounds = GEOMETRY_RANGES[property_name]
            elif property_name.startswith('center.'):
                bounds = (0, 1)
            elif property_name.endswith(('.x', '.y')):
                bounds = (-4096, 4096)
            elif property_name.startswith(('key_color.', 'key_colors.')):
                bounds = (0, 255)
            elif property_name in {'expand', 'edge_expand'}:
                bounds = (-64, 64)
            elif property_name == 'feather' and node.kind == 'matte' or property_name == 'edge_feather':
                bounds = (0, 128)
            elif property_name == 'edge_radius':
                bounds = (0.5, 16)
            elif property_name == 'mask_gamma':
                bounds = (0.1, 8)
            else:
                bounds = (0, 1)
            if not bounds[0] <= value <= bounds[1]:
                raise ValueError('graph control out of range')
            config = configs.setdefault(node_id, dict(node.config))
            if property_name.endswith(('.x', '.y')):
                vector_name, axis = property_name.rsplit('.', 1)
                vector = list(config.get(vector_name, [0.5, 0.5] if vector_name == 'center' else [0, 0]))
                vector[0 if axis == 'x' else 1] = value
                config[vector_name] = vector
            elif property_name.startswith('key_colors.'):
                _, ordinal, component = property_name.split('.')
                index = int(ordinal)
                colours = list(config.get('key_colors', []))
                rgb = list(ImageColor.getrgb(colours[index]))
                rgb[{'r': 0, 'g': 1, 'b': 2}[component]] = round(value)
                colours[index] = '#%02x%02x%02x' % tuple(rgb)
                config['key_colors'] = colours
            elif property_name.startswith('key_color.'):
                channel = {'r': 0, 'g': 1, 'b': 2}[property_name.rsplit('.', 1)[1]]
                # For key_colors-only configs, key_color.* aliases the first
                # colour rather than silently injecting an extra key.
                if 'key_color' not in config and config.get('key_colors'):
                    colours = list(config['key_colors'])
                    rgb = list(ImageColor.getrgb(colours[0]))
                    rgb[channel] = round(value)
                    colours[0] = '#%02x%02x%02x' % tuple(rgb)
                    config['key_colors'] = colours
                else:
                    rgb = list(ImageColor.getrgb(config.get('key_color', '#00ff00')))
                    rgb[channel] = round(value)
                    config['key_color'] = '#%02x%02x%02x' % tuple(rgb)
            else:
                config[property_name] = (round(value) if property_name in {'segments', 'wash_seed'}
                                         and node.kind == 'transition' else value)
        # The host clock drives timing without accumulating mutable state.
        # An explicit native progress binding takes priority over the timer.
        for key in self.order:
            node = self.nodes[key]
            if node.kind != 'transition' or 'timing' not in node.config:
                continue
            effective = configs.get(key, node.config)
            validate_transition_timing(effective['timing'])
            if context is not None and (key, 'progress') not in parameter_values:
                progress = transition_timing_progress(
                    effective['timing'], continuous_beat=context.beat,
                    source_beat=getattr(context, 'source_beat', None),
                )
                configs.setdefault(key, dict(node.config))['progress'] = progress
        for key, config in configs.items():
            if self.nodes[key].kind == 'filter':
                validate_filter(config, len(self.nodes[key].inputs))
            if self.nodes[key].kind == 'procedural_field':
                validate_field(config, len(self.nodes[key].inputs))
            if self.nodes[key].kind == 'raster_effect':
                validate_raster(config, len(self.nodes[key].inputs))
            if self.nodes[key].kind == 'layer_style':
                validate_style(config)
                validate_style_inputs(config, len(self.nodes[key].inputs))
            if self.nodes[key].kind == 'transition':
                validate_transition_geometry(config)
        return configs

    def render(self, sources: dict[str, Image.Image], *, parameter_values=None,
               gpu_backend=None, gpu_strict=False, context=None, style_report=None) -> Image.Image:
        """Render with optional resolved per-frame controls keyed by (node_id, property).

        Values are already evaluated by the host modulation engine. Never mutate
        the validated graph or its configuration during frame evaluation.
        """
        if style_report is not None:
            if not isinstance(style_report, dict):
                raise ValueError('style_report must be a dictionary')
            # Clear stale caller diagnostics before validating this evaluation.
            style_report.clear()
        style_rows = {} if style_report is not None else None
        configs = self._resolved_configs(parameter_values, context)
        results = {}
        if gpu_backend is not None:
            # Last completed evaluation only; no feedback into deterministic effects.
            gpu_backend.last_dispatch = {'gpu_nodes': 0, 'cpu_fallbacks': 0}
            if callable(getattr(gpu_backend, 'begin_transfer_sample', None)):
                gpu_backend.begin_transfer_sample()
        # M8.5 GPU-resident closed island. Unsupported graphs keep node dispatch.
        if gpu_backend is not None and style_report is None and callable(
                getattr(gpu_backend, 'render_resident_graph', None)):
            from .effect_graph_gpu_resident import supported
            if supported(self, configs):
                from .effect_graph_gpu import GPUUnsupported
                try:
                    return gpu_backend.render_resident_graph(self, sources, configs)
                except GPUUnsupported:
                    if gpu_strict:
                        raise
                    # A resident budget rejection must fall back as a WHOLE
                    # graph, not incur more per-node uploads into the same budget.
                    gpu_backend.last_dispatch['cpu_fallbacks'] = 1
                    return self.render(sources, parameter_values=parameter_values,
                                       context=context)
        # Carry each branch's source alpha as immutable metadata. Unary effects,
        # including mattes and colour keys, inherit it through arbitrary forks.
        # A merge/transition starts a new source silhouette at its output.
        original_alphas = {}
        islands_by_member = {}
        if (gpu_backend is not None and not gpu_strict and style_report is None
                and getattr(gpu_backend, 'supports_gpu_islands', False)
                and callable(getattr(gpu_backend, 'render_resident_graph', None))):
            from .effect_graph_gpu_islands import plan_islands
            islands, rejected_islands = plan_islands(self, configs)
            for island in islands:
                for member in island.members:
                    islands_by_member[member] = island
            if hasattr(gpu_backend, 'last_transport'):
                gpu_backend.last_transport['gpu_island_rejections'] = rejected_islands
        for key in self.order:
            node = self.nodes[key]
            kind = node.kind
            config = configs.get(key, node.config)
            island = islands_by_member.get(key)
            if island is not None and key != island.output:
                continue
            if island is not None:
                from .effect_graph_gpu_islands import execute_island
                from .effect_graph_gpu import GPUUnsupported
                try:
                    result = execute_island(gpu_backend, island, results)
                except GPUUnsupported:
                    gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                    return self.render(sources, parameter_values=parameter_values,
                                       context=context, style_report=style_report)
                inputs = [results[name] for name in node.inputs if name in results]
            if kind == 'source':
                if key not in sources or sources[key].mode != 'RGBA':
                    raise ValueError(f'missing RGBA source: {key}')
                result = sources[key].copy()
            elif island is None:
                inputs = [results[name] for name in node.inputs]
                if kind != 'layer_style' and any(im.size != inputs[0].size for im in inputs):
                    raise ValueError('graph input dimensions must match')
                if kind == 'copy':
                    result = inputs[0].copy()
                elif kind == 'opacity':
                    # Applied by the universal per-node output-opacity stage.
                    result = inputs[0].copy()
                elif kind == 'matte':
                    if gpu_backend is not None:
                        from .effect_graph_gpu import GPUUnsupported
                        try:
                            result = gpu_backend.apply_matte(
                                inputs, config, original_alpha=original_alphas[node.inputs[0]])
                            gpu_backend.last_dispatch['gpu_nodes'] += 1
                        except GPUUnsupported:
                            if gpu_strict:
                                raise
                            gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                            result = apply_matte(inputs, config, original_alpha=original_alphas[node.inputs[0]])
                    else:
                        result = apply_matte(inputs, config, original_alpha=original_alphas[node.inputs[0]])
                elif kind == 'chroma_key':
                    if gpu_backend is not None:
                        from .effect_graph_gpu import GPUUnsupported
                        try:
                            result = gpu_backend.chroma_key(
                                inputs[0], config, original_alpha=original_alphas[node.inputs[0]])
                            gpu_backend.last_dispatch['gpu_nodes'] += 1
                        except GPUUnsupported:
                            if gpu_strict:
                                raise
                            gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                            result = chroma_key(inputs[0], config, original_alpha=original_alphas[node.inputs[0]])
                    else:
                        result = chroma_key(inputs[0], config, original_alpha=original_alphas[node.inputs[0]])
                elif kind == 'procedural_field':
                    if gpu_strict:
                        from .effect_graph_gpu import GPUUnsupported
                        raise GPUUnsupported('procedural_field GPU execution is not implemented; use cpu or auto')
                    if gpu_backend is not None:
                        gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                    result = render_procedural_field(
                        inputs[0], config, original_alpha=original_alphas[node.inputs[0]],
                    )
                elif kind == 'raster_effect':
                    # CPU reference, including identity operations in strict mode.
                    # Never advertise a raster node as an executed GPU shader.
                    if gpu_strict:
                        from .effect_graph_gpu import GPUUnsupported
                        raise GPUUnsupported('raster_effect GPU execution is not implemented; use cpu or auto')
                    if gpu_backend is not None:
                        gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                    result, raster_alpha = render_raster_with_alpha(
                        inputs[0], config, original_alpha=original_alphas[node.inputs[0]],
                    )
                elif kind == 'spatial_warp':
                    validate_spatial(config, len(inputs))
                    from .effect_graph_gpu import GPUUnsupported
                    original = original_alphas[node.inputs[0]]
                    warp = getattr(gpu_backend, 'spatial_warp', None)
                    warped = None
                    if callable(warp):
                        try:
                            warped = warp(inputs, config, original_alpha=original)
                        except GPUUnsupported:
                            if gpu_strict:
                                raise
                        else:
                            if not spatial_is_identity(config):
                                gpu_backend.last_dispatch['gpu_nodes'] += 1
                    elif gpu_strict:
                        raise GPUUnsupported('spatial_warp requires a compatible GPU backend')
                    if warped is None:
                        if gpu_backend is not None:
                            gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                        warped = render_spatial_warp_with_alpha(
                            inputs[0], config, map_image=inputs[1] if len(inputs) == 2 else None,
                            original_alpha=original,
                        )
                    result, warped_alpha = warped
                elif kind == 'layer_style':
                    # M6 CPU reference: no claim of GPU-native style execution.
                    if gpu_strict:
                        from .effect_graph_gpu import GPUUnsupported
                        raise GPUUnsupported('layer_style GPU execution is not implemented; use cpu or auto')
                    if gpu_backend is not None:
                        gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                    result = render_layer_style(
                        inputs[0], config, original_alpha=original_alphas[node.inputs[0]],
                        pattern_image=inputs[1] if len(inputs) == 2 else None,
                    )
                elif kind == 'chromatic_displacement':
                    if gpu_backend is None or not any(any(config.get(f'{c}_offset', [0, 0]))
                                                      for c in ('red', 'green', 'blue')):
                        result = _chromatic_displacement(inputs[0], config)
                    else:
                        from .effect_graph_gpu import GPUUnsupported
                        try:
                            result = gpu_backend.chromatic_displacement(inputs[0], config)
                            gpu_backend.last_dispatch['gpu_nodes'] += 1
                        except GPUUnsupported:
                            if gpu_strict:
                                raise
                            gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                            result = _chromatic_displacement(inputs[0], config)
                elif kind == 'filter':
                    if (gpu_backend is None or config.get('filter_mix', 1) == 0
                            or config.get('filter_type', 'blur') == 'blur' and config.get('filter_radius', 1.5) == 0
                            or config.get('filter_type', 'blur') == 'bloom' and config.get('filter_strength', 1) == 0):
                        result = render_filter(inputs[0], config)
                    else:
                        from .effect_graph_gpu import GPUUnsupported
                        try:
                            result = gpu_backend.render_filter(inputs[0], config)
                            gpu_backend.last_dispatch['gpu_nodes'] += 1
                        except GPUUnsupported:
                            if gpu_strict:
                                raise
                            gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                            result = render_filter(inputs[0], config)
                elif kind == 'spectral_displacement':
                    if gpu_backend is None or config.get('mix', 1) == 0:
                        # Zero mix is an exact, allocation-free source pass-through.
                        result = (inputs[0].copy() if config.get('mix', 1) == 0
                                  else spectral_displacement(inputs[0], config))
                    else:
                        from .effect_graph_gpu import GPUUnsupported
                        try:
                            result = gpu_backend.spectral_displacement(inputs[0], config)
                            gpu_backend.last_dispatch['gpu_nodes'] += 1
                        except GPUUnsupported:
                            if gpu_strict:
                                raise
                            gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                            result = spectral_displacement(inputs[0], config)
                elif kind == 'transition':
                    # Revalidate after resolving DanceRudiment overrides.
                    validate_transition_matte(config)
                    validate_transition_spectral(config)
                    validate_transition_geometry(config)
                    style = config.get('transition_style', 'dissolve')
                    if gpu_backend is not None and 0 < config.get('progress', 0) < 1:
                        if style not in {'dissolve', 'wipe', 'radial'}:
                            from .effect_graph_gpu import GPUUnsupported
                            if gpu_strict:
                                raise GPUUnsupported(f'{style} geometric/spectral/matte transition GPU shader is not implemented')
                            gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                            result = render_transition(inputs[0], inputs[1], config,
                                                       matte_image=inputs[2] if len(inputs) == 3 else None)
                        else:
                            from .effect_graph_gpu import GPUUnsupported
                            try:
                                result = gpu_backend.render_transition(inputs[0], inputs[1], config)
                                gpu_backend.last_dispatch['gpu_nodes'] += 1
                            except GPUUnsupported:
                                if gpu_strict:
                                    raise
                                gpu_backend.last_dispatch['cpu_fallbacks'] += 1
                                result = render_transition(inputs[0], inputs[1], config)
                    else:
                        # Exact endpoints never need a GPU upload.
                        result = render_transition(inputs[0], inputs[1], config,
                                                   matte_image=inputs[2] if len(inputs) == 3 else None)
                else:
                    result = inputs[0].copy()
                    for layer in inputs[1:]:
                        if config.get('mode', 'over') == 'over':
                            result = Image.alpha_composite(result, layer)
                        elif config.get('mode', 'over') != 'add':
                            result = composite_blend(result, layer, config['mode'])
                        else:
                            # Additive blend in premultiplied space, then unassociate.
                            import numpy as np
                            a = np.asarray(result, dtype=np.float32) / 255
                            b = np.asarray(layer, dtype=np.float32) / 255
                            ap, bp = a[..., 3:4], b[..., 3:4]
                            alpha = 1 - (1 - ap) * (1 - bp)
                            premul = np.clip(a[..., :3] * ap + b[..., :3] * bp, 0, alpha)
                            rgb = np.divide(premul, alpha, out=np.zeros_like(premul), where=alpha > 0)
                            result = Image.fromarray(np.uint8(np.rint(np.concatenate([rgb, alpha], axis=-1) * 255)), 'RGBA')
            # A branch's opacity belongs to its resulting surface, regardless
            # of effect kind. This also makes source, matte, transition and
            # composite opacity independently DanceRudiment-addressable.
            opacity = config.get('opacity', 1)
            if island is None and opacity != 1:
                result = result.copy()
                result.putalpha(result.getchannel('A').point(lambda a: round(a * opacity)))
            if kind == 'layer_style' and style_rows is not None:
                style_rows[key] = style_node_report(
                    node, config, inputs[0], result,
                    original_alpha=original_alphas[node.inputs[0]], gpu_backend=gpu_backend,
                )
            results[key] = result
            if island is not None:
                if key != self.output:
                    owner = island.lineage_source
                    original_alphas[key] = result.getchannel('A') if owner == key else original_alphas[owner]
            elif kind == 'raster_effect':
                original_alphas[key] = raster_alpha
            elif kind == 'spatial_warp':
                original_alphas[key] = warped_alpha
            elif kind == 'source' or kind in {'transition', 'composite', 'procedural_field'}:
                original_alphas[key] = result.getchannel('A')
            else:
                original_alphas[key] = original_alphas[node.inputs[0]]
        final = results[self.output].copy()
        if style_report is not None:
            style_report.update(completed_style_report(self, style_rows, final, context=context))
        return final


def _chromatic_displacement(image: Image.Image, config: dict) -> Image.Image:
    """Displace independently sampled RGB channels with transparent borders.

    Original alpha is retained so the effect cannot accidentally reveal pixels
    outside the source silhouette. A future spectral-band node will support
    arbitrary source/destination colours and separate channel-alpha policies.
    """
    import numpy as np
    src = np.asarray(image, dtype=np.uint8)
    height, width = src.shape[:2]
    output = np.zeros_like(src)
    output[..., 3] = src[..., 3]
    yy, xx = np.indices((height, width), dtype=np.float64)
    for index, key in enumerate(('red_offset', 'green_offset', 'blue_offset')):
        dx, dy = config.get(key, [0, 0])
        sx, sy = xx - dx, yy - dy
        # Bilinear sampling with a transparent (zero-valued) border. The
        # integer-offset case remains exact and agrees with the legacy path.
        x0 = np.floor(sx).astype(np.int64)
        y0 = np.floor(sy).astype(np.int64)
        tx, ty = sx - x0, sy - y0
        channel = np.zeros((height, width), dtype=np.float64)
        for ox, oy, weight in (
            (0, 0, (1 - tx) * (1 - ty)),
            (1, 0, tx * (1 - ty)),
            (0, 1, (1 - tx) * ty),
            (1, 1, tx * ty),
        ):
            xi, yi = x0 + ox, y0 + oy
            valid = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
            channel[valid] += src[yi[valid], xi[valid], index] * weight[valid]
        output[..., index] = np.uint8(np.rint(np.clip(channel, 0, 255)))
    return Image.fromarray(output, 'RGBA')
