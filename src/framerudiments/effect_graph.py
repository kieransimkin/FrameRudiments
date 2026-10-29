"""Deterministic CPU effect DAG with alpha-correct two-input transitions.

Compositor integration supplies source RGBA images and per-frame DanceRudiment
controls. Temporal feedback and GPU graph execution remain later milestones.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from math import isfinite
from PIL import Image, ImageColor

from .effect_graph_transitions import render_transition
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
         'spectral_displacement', 'composite', 'transition'}
TRANSITION_STYLES = {'dissolve', 'wipe', 'radial'}
TRANSITION_DIRECTIONS = {'right', 'left', 'down', 'up'}
CONTROL_TARGETS = {
    'opacity': {'opacity'},
    'matte': {'feather', 'expand', 'mask_opacity', 'mask_combine', 'matte_channel', 'invert', 'alpha_source'},
    'chroma_key': {'tolerance', 'softness', 'despill', 'edge_expand', 'edge_feather',
                   'edge_decontaminate', 'edge_radius', 'key_color.r', 'key_color.g', 'key_color.b',
                   'key_space', 'alpha_source'},
    'chromatic_displacement': {f'{c}_offset.{axis}' for c in ('red', 'green', 'blue') for axis in ('x', 'y')},
    'spectral_displacement': {'mix', 'spectral_mode', 'alpha_mode'},
    'transition': {'progress', 'feather', 'center.x', 'center.y', 'transition_style', 'direction'},
}
# Every image-processing node has an independent output-opacity control.
for _targets in CONTROL_TARGETS.values():
    _targets.add('opacity')
CONTROL_TARGETS['source'] = {'opacity'}
CONTROL_TARGETS['copy'] = {'opacity'}
CONTROL_TARGETS['composite'] = {'opacity', 'mode'}
ENUM_CONTROLS = {
    'matte': {'mask_combine': MATTE_COMBINES, 'matte_channel': frozenset({'alpha', 'luminance'}),
              'alpha_source': ALPHA_SOURCES,
              'invert': frozenset({True, False})},
    'chroma_key': {'key_space': KEY_SPACES, 'alpha_source': ALPHA_SOURCES},
    'transition': {'transition_style': TRANSITION_STYLES, 'direction': TRANSITION_DIRECTIONS},
    'spectral_displacement': {'spectral_mode': SPECTRAL_MODES, 'alpha_mode': SPECTRAL_ALPHA_MODES},
    'composite': {'mode': frozenset({'over', 'add'})},
}
ENUM_DEFAULTS = {'alpha_source': 'current', 'mask_combine': 'multiply', 'matte_channel': 'alpha', 'invert': False,
                 'key_space': 'rgb', 'transition_style': 'dissolve', 'direction': 'right', 'mode': 'over',
                 'spectral_mode': 'overlay', 'alpha_mode': 'expanded'}




@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    inputs: tuple[str, ...]
    config: dict


class EffectGraph:
    """Validate a bounded DAG and execute using immutable-by-contract RGBA inputs."""

    def __init__(self, spec: dict):
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
                               'bands', 'spectral_mode', 'alpha_mode', 'mix'}:
                raise ValueError('unknown node property')
            key, kind = entry['id'], entry['type']
            if not isinstance(key, str) or not key or len(key) > 128 or key in self.nodes:
                raise ValueError('duplicate or invalid node id')
            if kind not in KINDS:
                raise ValueError(f'unsupported node type: {kind}')
            if 'alpha_source' in entry and kind not in {'matte', 'chroma_key'}:
                raise ValueError('alpha_source is only supported on matte and chroma_key nodes')
            inputs = entry.get('inputs', [])
            if not isinstance(inputs, list) or len(inputs) > MAX_INPUTS or not all(isinstance(i, str) for i in inputs):
                raise ValueError('invalid inputs')
            if (kind == 'source' and inputs) or (kind in {'copy', 'opacity', 'chroma_key', 'chromatic_displacement', 'spectral_displacement'} and len(inputs) != 1) or (kind == 'matte' and not 2 <= len(inputs) <= 9) or (kind == 'composite' and len(inputs) < 2) or (kind == 'transition' and len(inputs) != 2):
                raise ValueError(f'wrong input count for {kind}')
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
            if entry.get('mode', 'over') not in {'over', 'add'}:
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

    def validate_control_target(self, node_id, property_name):
        node = self.nodes.get(node_id)
        if node is None or not isinstance(property_name, str) or (
                property_name not in CONTROL_TARGETS.get(node.kind, set())
                and not self._is_extra_key_colour_target(node, property_name)
                and not (node.kind == 'spectral_displacement'
                         and spectral_control_parts(node.config, property_name) is not None)):
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
        return (property_name in ENUM_CONTROLS.get(node.kind, {})
                or (node.kind == 'spectral_displacement'
                    and spectral_control_parts(node.config, property_name) is not None
                    and property_name.endswith('.space')))

    def validate_control_choices(self, node_id, property_name, choices):
        self.validate_control_target(node_id, property_name)
        node = self.nodes[node_id]
        accepted = ENUM_CONTROLS.get(node.kind, {}).get(property_name)
        if (accepted is None and node.kind == 'spectral_displacement'
                and spectral_control_parts(node.config, property_name) is not None
                and property_name.endswith('.space')):
            accepted = SPECTRAL_SPACES
        if accepted is None:
            raise ValueError('categorical choices require an enum graph control')
        if (not isinstance(choices, list) or not 2 <= len(choices) <= 32
                or any(type(v) is not bool and not isinstance(v, str) for v in choices)
                or any(v not in accepted for v in choices)
                or len({(type(v), v) for v in choices}) != len(choices)):
            raise ValueError('invalid categorical graph control choices')

    def base_control_value(self, node_id, property_name):
        self.validate_control_target(node_id, property_name)
        config = self.nodes[node_id].config
        if self.nodes[node_id].kind == 'spectral_displacement' and property_name.startswith('bands.'):
            return spectral_control_base(config, property_name)
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
        return config.get(property_name, {'opacity': 1, 'mix': 1, 'feather': 0, 'tolerance': .1,
                                          'softness': .1, 'despill': 0,
                                          'progress': 0, 'expand': 0, 'mask_opacity': 1,
                                          'edge_expand': 0, 'edge_feather': 0, 'edge_decontaminate': 0,
                                          'edge_radius': 3}.get(property_name, 0))

    def render(self, sources: dict[str, Image.Image], *, parameter_values=None,
               gpu_backend=None, gpu_strict=False) -> Image.Image:
        """Render with optional resolved per-frame controls keyed by (node_id, property).

        Values are already evaluated by the host modulation engine. Never mutate
        the validated graph or its configuration during frame evaluation.
        """
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
            if node.kind == 'spectral_displacement' and property_name.startswith('bands.'):
                configs[node_id] = spectral_control_apply(
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
            if property_name.startswith('center.'):
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
                config[property_name] = value
        results = {}
        if gpu_backend is not None:
            # Last completed evaluation only; no feedback into deterministic effects.
            gpu_backend.last_dispatch = {'gpu_nodes': 0, 'cpu_fallbacks': 0}
        # Carry each branch's source alpha as immutable metadata. Unary effects,
        # including mattes and colour keys, inherit it through arbitrary forks.
        # A merge/transition starts a new source silhouette at its output.
        original_alphas = {}
        for key in self.order:
            node = self.nodes[key]
            kind = node.kind
            config = configs.get(key, node.config)
            if kind == 'source':
                if key not in sources or sources[key].mode != 'RGBA':
                    raise ValueError(f'missing RGBA source: {key}')
                result = sources[key].copy()
            else:
                inputs = [results[name] for name in node.inputs]
                if any(im.size != inputs[0].size for im in inputs):
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
                elif kind == 'chromatic_displacement':
                    result = _chromatic_displacement(inputs[0], config)
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
                    result = render_transition(inputs[0], inputs[1], config)
                else:
                    result = inputs[0].copy()
                    for layer in inputs[1:]:
                        if config.get('mode', 'over') == 'over':
                            result = Image.alpha_composite(result, layer)
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
            if opacity != 1:
                result = result.copy()
                result.putalpha(result.getchannel('A').point(lambda a: round(a * opacity)))
            results[key] = result
            if kind == 'source' or kind in {'transition', 'composite'}:
                original_alphas[key] = result.getchannel('A')
            else:
                original_alphas[key] = original_alphas[node.inputs[0]]
        return results[self.output].copy()


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
