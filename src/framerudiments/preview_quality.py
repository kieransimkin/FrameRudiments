"""S3 native-size preview plans, with explicit limits for unaudited pixel units.

A reduced profile changes the compositor canvas BEFORE rendering; it is not a
full-size render followed by resizing. The source/beat clocks, movement counts,
seeds and artistic effect values retain their authored meanings.
"""
from copy import copy, deepcopy
from dataclasses import asdict
from math import isfinite
from pathlib import Path
import sys

from .io import sha256 as file_sha256
from .preview_stream_contract import _hex, canonical_digest, quality_profiles

RENDERER_ABI = 'framerudiments.preview-s6-verified-seek.v1'
# Windows ctime currently denotes creation, and last-write timestamps may be
# reused by rapid same-size writes. Metadata cannot prove byte identity there.
# Keep the stat-only fast path on platforms with a change-time fence.
_REHASH_PINNED_REUSE = sys.platform == 'win32'
_PIXEL_STYLES = {
    'particles': {'padding': (4, 0, False), 'radius': (3, .5, False)},
    'bars': {'padding': (4, 0, False), 'gap': (2, 0, False)},
    'waveform': {'padding': (4, 0, False), 'line_width': (2, 1, True)},
    'path': {'padding': (4, 0, False), 'line_width': (2, 1, True), 'radius': (3, .5, False)},
}
_NORMALIZED_CONTROLS = frozenset({
    'opacity', 'progress', 'filter_mix', 'filter_strength', 'filter_threshold',
    'mask_opacity', 'tolerance', 'softness', 'despill', 'invert', 'mode',
    'matte_channel', 'alpha_source', 'mask_combine', 'key_space',
})


class ProfileUnsupported(ValueError):
    """This operation has no qualified renderer at the requested quality tier."""


def require_stateless(document):
    if not isinstance(document, dict):
        raise ValueError('project document must be an object')
    if document.get('effects'):
        raise ProfileUnsupported('Sequential Butterchurn/projectM effects require the S6 verified replay dispatcher')
    post = document.get('post_effects', {})
    if not isinstance(post, dict):
        raise ValueError('post_effects must be an object')
    if post.get('temporal'):
        raise ProfileUnsupported('Temporal post effects require S6 bounded-history admission')
    if post.get('feedback') is not None:
        raise ProfileUnsupported('Native feedback requires M11.2 checkpoint reconstruction admission')
    layers = document.get('layers', [])
    if not isinstance(layers, list) or any(not isinstance(row, dict) for row in layers):
        raise ValueError('layers must be an array of objects')
    for layer in layers:
        if layer.get('type', 'sprite') not in {'sprite', 'bars', 'rudiment', 'notes'}:
            raise ProfileUnsupported('This layer has not qualified for independent-frame adaptive seeking')


def _number(value):
    if type(value) not in (float, int) or not isfinite(value):
        raise ValueError('pixel dimension must be finite numeric')
    return value


def _pixels(value, factor, minimum=0, integer=False):
    value = _number(value) * factor
    return max(minimum, round(value) if integer else value)


def _vector(value, sx, sy):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('pixel vector needs two components')
    return [_number(value[0]) * sx, _number(value[1]) * sy]


def _bounds(value, sx, sy):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('pixel bounds need four components')
    result = [round(_number(v) * (sx if i % 2 == 0 else sy)) for i, v in enumerate(value)]
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ProfileUnsupported('Viewport collapses at this resolution')
    return result


def _sprite(config, sx, sy):
    if config.get('trail'):
        raise ProfileUnsupported('Sprite trails currently require a full-size canvas')
    if config.get('type') == 'bars':
        raise ProfileUnsupported('Recorded bar dimensions need a full-size canvas')
    if 'bounds' in config:
        config['bounds'] = _bounds(config['bounds'], sx, sy)
    if 'center' in config:
        config['center'] = _vector(config['center'], sx, sy)
    if 'width' in config:
        config['width'] = _pixels(config['width'], sx, 1, True)
    for key, scale in (('dx', sx), ('dy', sy)):
        mapping = config.get('mapping', {})
        if key in mapping:
            mapping[key] = _number(mapping[key]) * scale


def _text(config, sx, sy):
    if not config:
        return
    if config.get('effects'):
        raise ProfileUnsupported('Rich text effects currently require a full-size canvas')
    if 'box' in config:
        config['box'] = _bounds(config['box'], sx, sy)
    scale = min(sx, sy)
    maximum = config.get('font_size', 60)
    config['font_size'] = _pixels(maximum, scale, 1, True)
    config['min_font_size'] = min(config['font_size'], _pixels(
        config.get('min_font_size', maximum), scale, 1, True))
    for key, default in (('line_spacing', 8), ('stroke', 1)):
        config[key] = _pixels(config.get(key, default), scale, 0, True)


def _graph(spec, sx, sy):
    if spec is None:
        return
    if not isinstance(spec, dict) or not isinstance(spec.get('nodes'), list):
        raise ValueError('effect_graph requires nodes')
    scale = min(sx, sy)
    for node in spec['nodes']:
        if not isinstance(node, dict):
            raise ValueError('invalid effect node')
        kind = node.get('type')
        if kind in {'source', 'copy', 'opacity', 'composite'}:
            continue
        if kind == 'transition':
            if node.get('transition_style', 'dissolve') not in {'dissolve', 'wipe', 'radial', 'matte'}:
                raise ProfileUnsupported('Transition shader/geometry coordinates require full size')
            continue  # Transition feather and centres are normalized, not pixels.
        if kind == 'filter':
            node['filter_radius'] = _pixels(node.get('filter_radius', 1.5), scale)
        elif kind == 'matte':
            for key in ('feather', 'expand'):
                if key in node:
                    node[key] = _number(node[key]) * scale
        elif kind == 'chroma_key':
            for key in ('edge_expand', 'edge_feather'):
                if key in node:
                    node[key] = _number(node[key]) * scale
            node['edge_radius'] = _pixels(node.get('edge_radius', 3), scale, .5)
        elif kind == 'chromatic_displacement':
            for key in ('red_offset', 'green_offset', 'blue_offset'):
                if key in node:
                    node[key] = _vector(node[key], sx, sy)
        else:
            raise ProfileUnsupported('Effect node has no reduced-canvas unit adapter')


def _lower_canvas(document, width, height, source_width, source_height):
    sx, sy = width / source_width, height / source_height
    for field in ('post_effects', 'layer_animations', 'layer_displacement', 'layer_masks', 'word_indicator'):
        if document.get(field):
            raise ProfileUnsupported(f'{field} currently requires a full-size canvas')
    for binding in document.get('effect_graph_controls', []):
        if binding.get('property') not in _NORMALIZED_CONTROLS:
            raise ProfileUnsupported('Pixel-valued or structural controls require full size')
    for layer in document.get('layers', []):
        if layer.get('type') == 'notes':
            # Geometry is normalized within the declared viewport. Timing,
            # MIDI keys, lane assignment and velocity stay unchanged.
            layer['bounds'] = _bounds(layer['bounds'], sx, sy)
            continue
        if layer.get('type', 'sprite') in {'sprite', 'bars'}:
            _sprite(layer, sx, sy)
            continue
        name = layer.get('renderer')
        if name not in _PIXEL_STYLES or layer.get('visual') or layer.get('modulations'):
            raise ProfileUnsupported('Rudiment renderer/style requires full size')
        layer['bounds'] = _bounds(layer.get('bounds', [0, 0, source_width, source_height]), sx, sy)
        style = layer.setdefault('style', {})
        for key, (default, minimum, integer) in _PIXEL_STYLES[name].items():
            style[key] = _pixels(style.get(key, default), min(sx, sy), minimum, integer)
        w, h = layer['bounds'][2] - layer['bounds'][0], layer['bounds'][3] - layer['bounds'][1]
        margin = style['padding'] + max(style.get('radius', 0), style.get('line_width', 0) / 2)
        if margin * 2 + 2 > min(w, h):
            raise ProfileUnsupported('Rudiment strokes do not fit reduced viewport')
        if name in {'bars', 'waveform'}:
            axes = layer.get('axes')
            if not isinstance(axes, list) or not axes:
                raise ProfileUnsupported('Reduced bars/waveforms require explicit axes')
            vertical = style.get('orientation', 'vertical') == 'vertical'
            extent = (w if vertical else h) - 2 * margin - 1
            if name == 'waveform':
                extent = h - 2 * margin - 1
            channels = len(axes) if style.get('layout', 'stacked') == 'stacked' else 1
            minimum = style.get('gap', 0) + 1 if name == 'bars' else style['line_width'] + 1
            if extent / channels < minimum:
                raise ProfileUnsupported('Too many channels for reduced viewport')
    _text(document.get('captions'), sx, sy)
    _text(document.get('word_captions'), sx, sy)
    for row in document.get('identity', []):
        _text(row['style'], sx, sy)
    if document.get('lyric_art'):
        _sprite(document['lyric_art']['placement'], sx, sy)
    _graph(document.get('effect_graph'), sx, sy)
    document['width'], document['height'] = width, height


def profile_document(project, profile_id, *, allow_stateful=False):
    profiles = {p.id: p for p in quality_profiles(project)}
    if not isinstance(profile_id, str) or profile_id not in profiles:
        raise ValueError('unknown preview quality profile')
    profile = profiles[profile_id]
    if allow_stateful:
        # S6 owns feedback replay. Keep the older direct-render admission
        # deliberately strict unless its caller selects the S6 dispatcher.
        admission = deepcopy(project.data)
        admission.pop('effects', None)
        post = admission.get('post_effects', {})
        if isinstance(post, dict):
            post.pop('temporal', None)
            post.pop('feedback', None)
        require_stateless(admission)
        feedback = project.data.get('post_effects', {}).get('feedback') is not None
        if (project.data.get('effects') or feedback) and profile.quality_rank != 5:
            raise ProfileUnsupported('Feedback replay requires authored Q5 settings')
    else:
        require_stateless(project.data)
    if project.data.get('vision_sources'):
        post = project.data.get('post_effects', {})
        if project.data.get('effects') or post.get('feedback') is not None:
            raise ProfileUnsupported('M14.5 vision preview does not qualify stateful visualiser feedback')
    document = deepcopy(project.data)
    if profile.quality_rank == 5:
        return document  # Authored settings, including explicitly disabled AA.
    quality = deepcopy(document.get('render_quality', {}))
    quality['spatial_scale'], quality['temporal_samples'] = profile.spatial_scale, profile.temporal_samples
    quality['antialias_passes'] = min(quality.get('antialias_passes', 3),
                                     1 if profile.quality_rank <= 2 else 2 if profile.quality_rank == 3 else 3)
    if profile.quality_rank < 3:
        # Reduced-canvas prediction inputs must declare their pixel mapping.
        # Without this gate, the adaptive controller would offer an unusable
        # Q0/Q1/Q2 profile and repeatedly fail at compositor construction.
        sources = document.get('vision_sources', {}).get('sources', [])
        if any(row.get('resample', 'none') == 'none' for row in sources):
            raise ProfileUnsupported('Reduced prediction preview requires an explicit resample rule')
        _lower_canvas(document, profile.output_width, profile.output_height, project.width, project.height)
        quality['antialias_radius'] = max(.25, min(3, quality.get('antialias_radius', .75) *
                                            min(profile.output_width / project.width,
                                                profile.output_height / project.height)))
    document['render_quality'] = quality
    return document


def profile_capabilities(project, *, allow_stateful=False):
    rows = []
    for profile in quality_profiles(project):
        try:
            document = profile_document(project, profile.id, allow_stateful=allow_stateful)
            rows.append({**asdict(profile), 'supported': True, 'reason': None,
                         'settings_sha256': canonical_digest({'abi': RENDERER_ABI, 'derived': document}),
                         'execution_status': 'eligible_requires_render', 'production_equivalent': False})
        except ProfileUnsupported as exc:
            rows.append({**asdict(profile), 'supported': False, 'reason': str(exc),
                         'settings_sha256': None, 'execution_status': 'unsupported'})
    return tuple(rows)


def render_project_view(project, profile_id, *, allow_stateful=False):
    """Private view of a validated Project; keeps all original clock attributes.

    Preview sizes may be odd or <16: they are not H.264 encoder output projects.
    No frame-rate rewriting or audio time stretching is used to meet size rules.
    """
    from .output_quality import OutputQuality

    document = profile_document(project, profile_id, allow_stateful=allow_stateful)
    profile = next(p for p in quality_profiles(project) if p.id == profile_id)
    view = copy(project)
    view.data = document
    view.width, view.height = profile.output_width, profile.output_height
    view.output_quality = OutputQuality(document.get('render_quality'))
    view.inputs = deepcopy(project.inputs)
    view.canonical_inputs = deepcopy(getattr(project, 'canonical_inputs', None))
    view.effect_graph_spec = deepcopy(getattr(project, 'effect_graph_spec', None))
    if profile.quality_rank < 3:
        _graph(view.effect_graph_spec, view.width / project.width, view.height / project.height)
    return view


class PinnedInputGuard:
    """Hash declared inputs at admission; fence filesystem changes before reuse.

    Includes symlink resolution, inode, size, mtime and ctime. Windows rehashes
    at reuse because ctime is creation time and last-write timestamps may be
    unchanged after a same-size rewrite. Other platforms retain the stat-only
    reuse path. This is a local-preview change detector, not a hostile-FS sandbox.
    """
    def __init__(self, base, document, references=()):
        def pins(value):
            if isinstance(value, dict):
                for pk, hk in (('path', 'sha256'), ('shader_path', 'shader_sha256')):
                    if pk in value and hk in value:
                        yield value[pk], value[hk]
                for child in value.values():
                    yield from pins(child)
            elif isinstance(value, (tuple, list)):
                for child in value:
                    yield from pins(child)
        self.files = []
        self._rehash_reuse = _REHASH_PINNED_REUSE
        seen = set()
        for name, digest in pins([document, list(references)]):
            if not isinstance(name, str) or not name:
                raise ValueError('pinned source requires a path')
            expected = _hex(digest, 'source pin')
            alias = Path(base) / name
            resolved = alias.resolve(strict=True)
            identity = (alias, expected)
            if identity in seen:
                continue
            seen.add(identity)
            if not resolved.is_file():
                raise ValueError('pinned source must be a regular file')
            before = self._signature(resolved)
            if file_sha256(resolved) != expected or self._signature(resolved) != before:
                raise ValueError('pinned source SHA-256 mismatch or changed during admission')
            self.files.append((alias, resolved, before, expected))

    @staticmethod
    def _signature(path):
        s = path.stat()
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    def __call__(self):
        for alias, resolved, signature, expected in self.files:
            try:
                if alias.resolve(strict=True) != resolved or self._signature(resolved) != signature:
                    raise ValueError('pinned source changed since admission')
                if self._rehash_reuse and (file_sha256(resolved) != expected
                        or self._signature(resolved) != signature or alias.resolve(strict=True) != resolved):
                    raise ValueError('pinned source changed since admission')
            except OSError as exc:
                raise ValueError('pinned source changed since admission') from exc
