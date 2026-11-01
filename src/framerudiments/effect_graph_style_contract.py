"""M6.5 layer-style discovery and diagnostics, never a second effect renderer.

All descriptors come from the existing style registries. Reports contain small
JSON values only (no textures, private asset paths or sampled audio), and do not
participate in image generation. Bounds are canvas-local and conservative.
"""
from __future__ import annotations

from copy import deepcopy
import math

from PIL import Image

from .effect_graph_alpha_geometry import MAX_DISTANCE_PIXELS, MAX_WORK_PIXELS
from .effect_graph_layer_relief import (
    LIGHT_TYPES, SWEEP_TYPES, MAX_SWEEP_PIXELS, MAX_SWEEP_SAMPLES, MAX_SWEEP_VISITS,
)
from .effect_graph_layer_overlays import (
    MAX_PIXELS, MAX_STOPS, MAX_TEXTURE_PIXELS, OVERLAY_TYPES, requires_texture,
)
from .effect_graph_layer_styles import (
    STYLE_TYPES, style_bounds, style_control_apply, style_control_base,
    style_parameter_descriptors, validate_style, validate_style_inputs,
)

CATALOGUE_SCHEMA = 'framerudiments.layer-styles.v1'
MANIFEST_SCHEMA = 'framerudiments.layer-style-controls.v1'
REPORT_SCHEMA = 'framerudiments.layer-style-report.v1'
_GPU_REASON = 'layer_style has a CPU implementation only; no GPU-native style kernel'


def _validate(config, input_count):
    if type(input_count) is not int or input_count not in (1, 2):
        raise ValueError('layer-style input_count must be 1 or 2')
    validate_style(config)
    validate_style_inputs(config, input_count)
    opacity = config.get('opacity', 1)
    if (type(opacity) not in (int, float) or not math.isfinite(opacity)
            or not 0 <= opacity <= 1):
        raise ValueError('style output opacity must be finite and in [0, 1]')


def style_capabilities(config=None, *, input_count=None):
    """Return detached, configuration-specific CPU/alpha/resource contracts.

    This is not a device probe and does not create an optional GPU context.
    Strict GPU is unsupported even when the selected style is currently a no-op.
    """
    config = {} if config is None else config
    count = (2 if requires_texture(config) else 1) if input_count is None else input_count
    _validate(config, count)
    kind = config.get('style_type', 'drop_shadow')
    coverage = ('interior' if kind in {'inner_shadow', 'inner_glow', 'bevel', 'emboss', 'satin'}
                or kind in OVERLAY_TYPES else 'may_expand')
    if kind in {'outline', 'edge_light'} and config.get('outline_position', 'outer') == 'inner':
        coverage = 'interior'
    limits = {'canvas_pixels': MAX_PIXELS}
    if kind in LIGHT_TYPES:
        limits['distance_workspace_pixels'] = MAX_DISTANCE_PIXELS
    elif kind in SWEEP_TYPES:
        limits.update(sweep_workspace_pixels=MAX_SWEEP_PIXELS,
                      sweep_samples=MAX_SWEEP_SAMPLES, sweep_visits=MAX_SWEEP_VISITS)
    elif kind not in OVERLAY_TYPES:
        limits['alpha_workspace_pixels'] = MAX_WORK_PIXELS
    if kind == 'gradient_overlay':
        limits['gradient_stops'] = MAX_STOPS
    if requires_texture(config):
        limits['texture_pixels'] = MAX_TEXTURE_PIXELS
    return {
        'style_type': kind, 'input_count': count,
        'inputs': ['image'] + (['pattern_texture'] if count == 2 else []),
        'pixel_format': 'RGBA8', 'alpha_association': 'straight',
        'working_space': 'display_referred_srgb',
        'gradient_interpolation_space': (config.get('gradient_space', 'srgb')
                                         if kind == 'gradient_overlay' else None),
        'geometry_source': 'alpha', 'canvas_size': 'unchanged',
        'coverage': coverage, 'random_access': True, 'temporal_history': False,
        'backend': {'cpu': True, 'gpu_native': False, 'strict_gpu': 'reject',
                    'auto': 'cpu_fallback', 'reason': _GPU_REASON},
        'limits': limits,
        'dependency_profile': 'framerudiments[effect_graph]', 'new_dependencies': [],
    }


def style_catalogue():
    """Complete stable style inventory; no models, private media or devices."""
    entries = {kind: style_capabilities({'style_type': kind}) for kind in sorted(STYLE_TYPES)}
    entries['pattern_overlay']['variants'] = {
        'texture': style_capabilities({'style_type': 'pattern_overlay', 'pattern_kind': 'texture'})
    }
    return {'schema': CATALOGUE_SCHEMA, 'styles': entries}


def style_control_schema(config, *, input_count, active_only=False):
    """Descriptors usable by the native binding engine and a future editor.

    Preserve the historical `default` field (the configured base). `value` is
    its explicit alias; `factory_default` distinguishes registry defaults. A
    custom stop has no factory default. Numeric range validation is unchanged.
    Joint constraints (e.g. stop order) are checked after frame-local overrides.
    """
    if type(active_only) is not bool:
        raise ValueError('active_only must be boolean')
    _validate(config, input_count)
    kind = config.get('style_type', 'drop_shadow')
    output = config.get('style_output', 'combined')
    texture = requires_texture(config)
    descriptors = style_parameter_descriptors(config)
    for name, item in descriptors.items():
        item['value'] = deepcopy(item['default'])
        item['default_semantics'] = 'configured_base'
        try:
            factory = 1 if name == 'opacity' else style_control_base({}, name)
        except ValueError:
            factory = None
        item['factory_default'] = deepcopy(factory)
        item['active'] = kind in item.get('styles', []) and output in item.get('outputs', [output])
        item['group'] = name.split('.')[0]
        item['constraints'] = []
        if name == 'fill_opacity' and output == 'effect':
            item['active'] = False
        if name.startswith('style_color.') and (kind == 'gradient_overlay' or
                                              kind == 'pattern_overlay' and texture):
            item['active'] = False
        if kind == 'color_overlay' and name == 'overlay_space':
            item['active'] = False
        if kind == 'gradient_overlay' and config.get('gradient_kind', 'linear') == 'radial':
            if name == 'gradient_angle':
                item['active'] = False
        if kind == 'pattern_overlay':
            if name == 'pattern_wrap' and not texture:
                item['active'] = False
            if texture and (name in {'pattern_duty', 'pattern_softness', 'pattern_background_opacity',
                                            'pattern_foreground_opacity'}
                            or name.startswith('pattern_background.')):
                item['active'] = False
            if config.get('pattern_kind', 'checker') == 'checker' and name == 'pattern_duty':
                item['active'] = False
            if config.get('pattern_kind') == 'stripes' and name == 'pattern_scale.y':
                item['active'] = False
        if name in {'style_type', 'pattern_kind'}:
            permitted = []
            for choice in item['choices']:
                if name == 'pattern_kind' and (choice == 'texture') != (input_count == 2):
                    continue
                try:
                    validate_style_inputs(style_control_apply(config, name, choice), input_count)
                except ValueError:
                    continue
                permitted.append(choice)
            item['choices'] = permitted
            item['constraints'].append('fixed_input_arity')
        if item['type'] == 'enum':
            item['can_vary'] = len(item['choices']) > 1
        else:
            item['can_vary'] = True
            if name in {'gradient_center.x', 'gradient_center.y'}:
                item['units'] = 'normalized'
            elif name == 'gradient_phase':
                item['units'] = 'cycles'
        if name.startswith('gradient_stops.') and name.endswith('.position'):
            item['constraints'].append('all_stop_positions_strictly_increasing_after_resolution')
    return {name: deepcopy(item) for name, item in sorted(descriptors.items())
            if not active_only or item['active']}


def graph_style_manifest(graph, *, include_unreachable=False, active_only=False):
    """Discover stable graph/chain control addresses without loading sources."""
    if type(include_unreachable) is not bool or type(active_only) is not bool:
        raise ValueError('discovery flags must be boolean')
    reachable = set(graph.order)
    order = list(graph.order)
    if include_unreachable:
        order += sorted(set(graph.nodes) - reachable)
    chain_nodes = {}
    for chain, expansion in graph.chain_expansions.items():
        for stage, internal in expansion['stages'].items():
            chain_nodes[internal] = {'chain': chain, 'role': 'stage', 'stage': stage}
        for ordinal, ids in enumerate(expansion['inputs']):
            for internal in ids:
                chain_nodes[internal] = {'chain': chain, 'role': 'input_effect', 'input': ordinal}
    nodes = {}
    for key in order:
        node = graph.nodes[key]
        if node.kind != 'layer_style':
            continue
        controls = style_control_schema(node.config, input_count=len(node.inputs), active_only=active_only)
        for name, descriptor in controls.items():
            # Arrays retain IDs containing punctuation; never split node IDs.
            descriptor['address'] = [key, name]
        nodes[key] = {
            'reachable': key in reachable, 'inputs': list(node.inputs),
            'chain': deepcopy(chain_nodes.get(key)),
            'capabilities': style_capabilities(node.config, input_count=len(node.inputs)),
            'controls': controls,
        }
    return {'schema': MANIFEST_SCHEMA, 'output': graph.output, 'nodes': nodes}


def _box(value):
    return list(value) if value is not None else None


def _union(first, second):
    if first is None:
        return second
    if second is None:
        return first
    return (min(first[0], second[0]), min(first[1], second[1]),
            max(first[2], second[2]), max(first[3], second[3]))


def _intersect(box, width, height):
    if box is None:
        return None
    clipped = (max(0, box[0]), max(0, box[1]), min(width, box[2]), min(height, box[3]))
    return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None


def layer_style_bounds(image, config, *, original_alpha=None):
    """Conservative support estimate, separate from actual rendered alpha.

    The estimate may include transparent pixels. It can flag *possible* canvas
    clipping, not prove clipping occurred, restore cropped input, or include
    downstream transformations. No image is modified or expanded here.
    """
    _validate(config, 2 if requires_texture(config) else 1)
    if not isinstance(image, Image.Image) or image.mode != 'RGBA' or min(image.size) < 1:
        raise ValueError('style bounds need a nonempty RGBA image')
    current = image.getchannel('A')
    selected = current
    use_original = config.get('alpha_source', 'current') == 'original' and original_alpha is not None
    if use_original:
        if (not isinstance(original_alpha, Image.Image) or original_alpha.mode != 'L'
                or original_alpha.size != image.size):
            raise ValueError('style bounds original alpha must be a same-size L image')
        selected = original_alpha
    kind = config.get('style_type', 'drop_shadow')
    combined = config.get('style_output', 'combined') == 'combined'
    fill = current.getbbox() if combined and config.get('fill_opacity', 1) > 0 else None
    no_effect = not config.get('style_strength', 1) or selected.getbbox() is None
    no_effect |= kind in (LIGHT_TYPES | {'outline'}) and config.get('style_width', 3) == 0
    no_effect |= kind in {'bevel', 'emboss'} and config.get('bevel_depth', 4) == 0
    no_effect |= kind in SWEEP_TYPES and config.get('extrude_length', 16) == 0
    if config.get('opacity', 1) == 0:
        estimate = None
    elif no_effect:
        estimate = fill
    elif combined and (kind in OVERLAY_TYPES or kind in {'inner_shadow', 'inner_glow', 'bevel', 'emboss', 'satin'}
                       or kind in {'outline', 'edge_light'} and config.get('outline_position') == 'inner'):
        # Conditional interior shading cannot add coverage outside current ink.
        estimate = current.getbbox()
    else:
        estimate = _union(style_bounds(selected, config), fill)
    width, height = image.size
    padding = ([max(0, -estimate[0]), max(0, -estimate[1]),
                max(0, estimate[2] - width), max(0, estimate[3] - height)]
               if estimate is not None else [0, 0, 0, 0])
    return {
        'input_alpha': _box(current.getbbox()), 'selected_alpha': _box(selected.getbbox()),
        'alpha_source': config.get('alpha_source', 'current'),
        'original_alpha_available': original_alpha is not None,
        'conservative_unclipped': _box(estimate),
        'conservative_canvas': _box(_intersect(estimate, width, height)),
        'canvas': [0, 0, width, height], 'padding_ltrb': padding,
        'may_clip': any(padding), 'coordinate_space': 'canvas_pixels',
        'extent_convention': 'left_top_inclusive_right_bottom_exclusive',
        'scope': 'this_node_before_downstream_effects',
    }


def style_node_report(node, config, input_image, output_image, *, original_alpha, gpu_backend=None):
    bounds = layer_style_bounds(input_image, config, original_alpha=original_alpha)
    actual = output_image.getchannel('A').getbbox()
    bounds['actual_output_alpha'] = _box(actual)
    bounds['output_touches_canvas'] = bool(actual is not None and
        (actual[0] == 0 or actual[1] == 0 or actual[2] == output_image.width or actual[3] == output_image.height))
    return {
        'id': node.id, 'inputs': list(node.inputs), 'style_type': config.get('style_type', 'drop_shadow'),
        'execution': 'cpu', 'gpu_backend_present': gpu_backend is not None,
        'fallback_reason': _GPU_REASON if gpu_backend is not None else None,
        'parameters': deepcopy({k: v for k, v in config.items() if k not in {'id', 'type', 'inputs'}}),
        'resolved_controls': {name: item['value'] for name, item in
                              style_control_schema(config, input_count=len(node.inputs), active_only=True).items()},
        'bounds': bounds,
    }


def completed_style_report(graph, nodes, result, *, context=None):
    """Build bounded diagnostics only after the entire graph rendered successfully."""
    clock = {}
    if context is not None:
        for field in ('frame', 'output_seconds', 'source_seconds', 'beat', 'source_beat'):
            value = getattr(context, field, None)
            if type(value) in (int, float) and math.isfinite(value):
                clock[field] = value
    return {
        'schema': REPORT_SCHEMA, 'state': 'complete', 'output': graph.output,
        'sample_context': clock, 'nodes': nodes,
        'final_graph_alpha': _box(result.getchannel('A').getbbox()),
        'clipping_candidates': [name for name, row in nodes.items() if row['bounds']['may_clip']],
        'scope': 'one_graph_evaluation_not_temporal_supersample_union',
    }


def style_driver_interval(interval):
    """Adapt only extended style ranges to the existing driver's [0,1] output.

    Existing [-10000,10000] bindings are unchanged. Extended ranges cover all
    registered numeric style paths, including +/-65536-pixel pattern offsets.
    Native sampling/curves/lag/FIR and combination order remain authoritative.
    Final resolved graph values are still validated; this never clamps them.
    """
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        raise ValueError('style control range must be [minimum, maximum]')
    lower, upper = interval
    if (any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 65536 for v in interval)
            or lower >= upper):
        raise ValueError('style control range must be increasing, finite and within [-65536, 65536]')
    return (lower, upper) if lower < -10000 or upper > 10000 else None


def remap_style_driver(value, interval):
    """Post-transfer range mapping, before native set/add/multiply/min/max."""
    if interval is None:
        return value
    return interval[0] + value * (interval[1] - interval[0])
