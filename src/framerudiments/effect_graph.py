"""Deterministic CPU effect DAG with alpha-correct two-input transitions.

Compositor integration supplies source RGBA images and per-frame DanceRudiment
controls. Temporal feedback and GPU graph execution remain later milestones.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from math import isfinite
from PIL import Image, ImageChops, ImageFilter, ImageColor

from .effect_graph_transitions import render_transition

MAX_NODES = 256
MAX_INPUTS = 32
KINDS = {'source', 'copy', 'opacity', 'matte', 'chroma_key', 'chromatic_displacement', 'composite', 'transition'}
TRANSITION_STYLES = {'dissolve', 'wipe', 'radial'}
TRANSITION_DIRECTIONS = {'right', 'left', 'down', 'up'}
CONTROL_TARGETS = {
    'opacity': {'opacity'},
    'matte': {'feather'},
    'chroma_key': {'tolerance', 'softness', 'despill', 'key_color.r', 'key_color.g', 'key_color.b'},
    'chromatic_displacement': {f'{c}_offset.{axis}' for c in ('red', 'green', 'blue') for axis in ('x', 'y')},
    'transition': {'progress', 'feather', 'center.x', 'center.y'},
}



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
            if set(entry) - {'id', 'type', 'inputs', 'opacity', 'mode', 'invert', 'feather', 'key_color', 'tolerance', 'softness', 'despill', 'matte_channel', 'red_offset', 'green_offset', 'blue_offset', 'transition_style', 'direction', 'progress', 'center'}:
                raise ValueError('unknown node property')
            key, kind = entry['id'], entry['type']
            if not isinstance(key, str) or not key or len(key) > 128 or key in self.nodes:
                raise ValueError('duplicate or invalid node id')
            if kind not in KINDS:
                raise ValueError(f'unsupported node type: {kind}')
            inputs = entry.get('inputs', [])
            if not isinstance(inputs, list) or len(inputs) > MAX_INPUTS or not all(isinstance(i, str) for i in inputs):
                raise ValueError('invalid inputs')
            if (kind == 'source' and inputs) or (kind in {'copy', 'opacity', 'chroma_key', 'chromatic_displacement'} and len(inputs) != 1) or (kind == 'matte' and len(inputs) != 2) or (kind == 'composite' and len(inputs) < 2) or (kind == 'transition' and len(inputs) != 2):
                raise ValueError(f'wrong input count for {kind}')
            opacity = entry.get('opacity', 1.0)
            if type(opacity) not in (int, float) or not isfinite(opacity) or not 0 <= opacity <= 1:
                raise ValueError('opacity must be finite and between 0 and 1')
            if kind == 'matte' and entry.get('matte_channel', 'alpha') not in {'alpha', 'luminance'}:
                raise ValueError('matte_channel must be alpha or luminance')
            if kind == 'matte' and (type(entry.get('invert', False)) is not bool or type(entry.get('feather', 0)) not in (int, float) or not isfinite(entry.get('feather', 0)) or not 0 <= entry.get('feather', 0) <= 128):
                raise ValueError('invalid matte controls')
            if kind == 'chromatic_displacement':
                for channel in ('red_offset', 'green_offset', 'blue_offset'):
                    vector = entry.get(channel, [0, 0])
                    if (not isinstance(vector, (tuple, list)) or len(vector) != 2
                            or any(type(v) not in (int, float) or not isfinite(v)
                                   or abs(v) > 4096 for v in vector)):
                        raise ValueError(f'invalid {channel} displacement vector')
            if kind == 'chroma_key':
                try:
                    ImageColor.getrgb(entry.get('key_color', '#00ff00'))
                except (ValueError, TypeError) as exc:
                    raise ValueError('invalid chroma key colour') from exc
                for param in ('tolerance', 'softness', 'despill'):
                    val = entry.get(param, {'tolerance': 0.1, 'softness': 0.1, 'despill': 0}[param])
                    if type(val) not in (float, int) or not isfinite(val) or not 0 <= val <= 1:
                        raise ValueError(f'invalid chroma key {param}')
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

    def validate_control_target(self, node_id, property_name):
        node = self.nodes.get(node_id)
        if node is None or property_name not in CONTROL_TARGETS.get(node.kind, set()):
            raise ValueError('unknown or inapplicable graph control target')

    def base_control_value(self, node_id, property_name):
        self.validate_control_target(node_id, property_name)
        config = self.nodes[node_id].config
        if property_name.endswith(('.x', '.y')):
            key, axis = property_name.rsplit('.', 1)
            return config.get(key, [0.5, 0.5] if key == 'center' else [0, 0])[0 if axis == 'x' else 1]
        if property_name.startswith('key_color.'):
            return ImageColor.getrgb(config.get('key_color', '#00ff00'))[{'r': 0, 'g': 1, 'b': 2}[property_name[-1]]]
        return config.get(property_name, {'opacity': 1, 'feather': 0, 'tolerance': .1, 'softness': .1, 'despill': 0, 'progress': 0}.get(property_name, 0))

    def render(self, sources: dict[str, Image.Image], *, parameter_values=None) -> Image.Image:
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
            if node is None or property_name not in CONTROL_TARGETS.get(node.kind, set()):
                raise ValueError('unknown or inapplicable graph control target')
            if type(value) not in (float, int) or not isfinite(value):
                raise ValueError('graph control must be finite numeric')
            bounds = (0, 1) if property_name.startswith('center.') else ((-4096, 4096) if property_name.endswith(('.x', '.y')) else (
                (0, 255) if property_name.startswith('key_color.') else (
                (0, 128) if property_name == 'feather' and node.kind == 'matte' else (0, 1))
            ))
            if not bounds[0] <= value <= bounds[1]:
                raise ValueError('graph control out of range')
            config = configs.setdefault(node_id, dict(node.config))
            if property_name.endswith(('.x', '.y')):
                vector_name, axis = property_name.rsplit('.', 1)
                vector = list(config.get(vector_name, [0.5, 0.5] if vector_name == 'center' else [0, 0]))
                vector[0 if axis == 'x' else 1] = value
                config[vector_name] = vector
            elif property_name.startswith('key_color.'):
                channel = {'r': 0, 'g': 1, 'b': 2}[property_name.rsplit('.', 1)[1]]
                rgb = list(ImageColor.getrgb(config.get('key_color', '#00ff00')))
                rgb[channel] = round(value)
                config['key_color'] = '#%02x%02x%02x' % tuple(rgb)
            else:
                config[property_name] = value
        results = {}
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
                    result = inputs[0].copy()
                    result.putalpha(result.getchannel('A').point(lambda a: round(a * config.get('opacity', 1))))
                elif kind == 'matte':
                    result = inputs[0].copy()
                    if config.get('matte_channel', 'alpha') == 'luminance':
                        # Alpha-weighted luminance: transparent bright pixels must not reveal ink.
                        matte_rgb = inputs[1].convert('RGB').convert('L')
                        mask = ImageChops.multiply(matte_rgb, inputs[1].getchannel('A'))
                    else:
                        mask = inputs[1].getchannel('A')
                    if config.get('feather', 0):
                        mask = mask.filter(ImageFilter.GaussianBlur(config['feather']))
                    if config.get('invert', False):
                        mask = ImageChops.invert(mask)
                    result.putalpha(ImageChops.multiply(inputs[0].getchannel('A'), mask))
                elif kind == 'chroma_key':
                    result = _chroma_key(inputs[0], config)
                elif kind == 'chromatic_displacement':
                    result = _chromatic_displacement(inputs[0], config)
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
            results[key] = result
        return results[self.output].copy()


def _chroma_key(image: Image.Image, config: dict) -> Image.Image:
    """Soft colour-distance key in straight RGB; preserves existing source alpha.

    This deliberately simple CPU reference is not yet a production-quality
    perceptual keyer. A later milestone adds OKLab selection and edge cleanup.
    """
    import numpy as np
    pixels = np.asarray(image, dtype=np.float32) / 255.0
    key = np.asarray(ImageColor.getrgb(config.get('key_color', '#00ff00')), dtype=np.float32) / 255.0
    distance = np.sqrt(np.mean((pixels[..., :3] - key) ** 2, axis=-1))
    tolerance = config.get('tolerance', 0.1)
    softness = config.get('softness', 0.1)
    if softness == 0:
        keep = (distance > tolerance).astype(np.float32)
    else:
        keep = np.clip((distance - tolerance) / softness, 0, 1)
        keep = keep * keep * (3 - 2 * keep)
    out = pixels.copy()
    out[..., 3] *= keep
    # Suppress the key-colour contribution in semi-transparent edge pixels.
    despill = config.get('despill', 0)
    if despill:
        dominant = int(np.argmax(key))
        other = [i for i in range(3) if i != dominant]
        excess = np.maximum(0, out[..., dominant] - np.maximum(out[..., other[0]], out[..., other[1]]))
        out[..., dominant] -= excess * despill * (1 - keep)
    return Image.fromarray(np.uint8(np.rint(np.clip(out, 0, 1) * 255)), 'RGBA')


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
