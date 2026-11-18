"""Read-only transition comparison through the ordinary native compositor.

The authoring adapter changes only a detached graph output and preview quality.
The complete validated graph, native control bindings, source clocks, bounded
temporal post effects and requested backend remain authoritative. Sequential
external visualisers execute after this graph and are explicitly out of scope.
"""
from base64 import b64encode
from copy import copy, deepcopy
from hashlib import sha256
from io import BytesIO
import json

from PIL import Image

from .effect_graph import EffectGraph
from .graph_authoring import _INVALID_NATIVE_INPUT, validate_authoring_graph
from .graph_authoring_controls import _locations
from .output_quality import OutputQuality
from .preview_native_renderer import NativePreviewRenderer
from .preview_stream_contract import canonical_digest


SCHEMA = 'framerudiments.authoring-transition-preview.v1'
MAX_PREVIEW_PIXELS = 8_388_608
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_MESSAGES = {
    'graph_invalid': 'The complete native graph, project or source data is invalid for this preview.',
    'node_invalid': 'Choose a native transition or an expanded transition-chain stage.',
    'frame_invalid': 'The preview needs an absolute integer frame inside the programme.',
    'quality_invalid': 'Choose fast or full native preview quality.',
    'preview_limit': 'The native preview exceeds its bounded image or response size.',
    'input_changed': 'Pinned inputs changed while rendering; reopen the verified project.',
    'render_failed': 'The native transition preview could not render or release its resources.',
}


class AuthoringPreviewError(ValueError):
    """Fixed, path-free public error; native details remain in the causal chain."""

    def __init__(self, code):
        self.code = code
        super().__init__(_MESSAGES[code])


def check_preview_size(width, height):
    """Shared native-dimension bound for direct and transition previews."""
    if width * height > MAX_PREVIEW_PIXELS:
        raise AuthoringPreviewError('preview_limit')


def check_preview_png(encoded):
    if len(encoded) > MAX_IMAGE_BYTES:
        raise AuthoringPreviewError('preview_limit')


def _output_view(project, node, quality):
    """Preserve prepared shader admissions and original native clock objects."""
    view = copy(project)
    view.data = deepcopy(project.data)
    view.data['effect_graph']['output'] = node
    view.effect_graph_spec = deepcopy(project.effect_graph_spec)
    view.effect_graph_spec['output'] = node
    view.inputs = deepcopy(project.inputs)
    view.canonical_inputs = deepcopy(project.canonical_inputs)
    if quality == 'fast':
        view.data['render_quality'] = {'enabled': False}
    view.output_quality = OutputQuality(view.data.get('render_quality'))
    return view


def _verify(owner, view=None):
    try:
        (view or owner.project).verify_inputs()
        owner.input_guard()
    except _INVALID_NATIVE_INPUT as exc:
        raise AuthoringPreviewError('input_changed') from exc


def _clock(context):
    return {'frame': context.frame, 'seconds': context.output_seconds,
            'source_seconds': context.source_seconds, 'beat': context.beat,
            'source_beat': context.source_beat}


def _render_view(owner, node, label, role, frame, quality):
    from .compositor import Compositor

    view = _output_view(owner.project, node, quality)
    _verify(owner, view)
    compositor = Compositor(view)
    try:
        # Construction can register additional native layer/motion inputs.
        _verify(owner, view)
        image, context = compositor.frame(frame, apply_quality=quality == 'full')
        _verify(owner, view)
        if (not isinstance(image, Image.Image) or image.mode != 'RGB'
                or image.size != (view.width, view.height)):
            raise AuthoringPreviewError('render_failed')
        stream = BytesIO()
        image.save(stream, format='PNG', compress_level=1)
        encoded = stream.getvalue()
        check_preview_png(encoded)
        return {'role': role, 'node': node, 'label': label, 'width': image.width, 'height': image.height,
                'mime': 'image/png', 'png_base64': b64encode(encoded).decode('ascii'),
                'sha256': sha256(encoded).hexdigest(), 'pixel_sha256': sha256(image.tobytes()).hexdigest()}, \
            _clock(context), deepcopy(view.output_quality.evidence)
    finally:
        compositor.close()


def preview_transition_draft(project_file, draft, *, sidecars, node, frame, quality,
                             validate_draft, temp_parent):
    """Render From / Result / To for one admitted native transition and frame.

    No authored project is saved or changed. Each projection retains every
    graph node and binding, so preview cannot bypass hidden-branch validation.
    The three native compositors are opened sequentially on this thread and
    closed before the next one. No live GPU context is shared across threads.
    """
    if not isinstance(node, str) or not 1 <= len(node) <= 128:
        raise AuthoringPreviewError('node_invalid')
    if type(frame) is not int or frame < 0:
        raise AuthoringPreviewError('frame_invalid')
    if quality not in ('fast', 'full'):
        raise AuthoringPreviewError('quality_invalid')
    owner = NativePreviewRenderer(project_file, draft, sidecars, validate_draft=validate_draft,
                                  temp_parent=temp_parent)
    try:
        try:
            owner._prepare()
            identity = canonical_digest({'project': draft, 'sidecars': sidecars})
            report = validate_authoring_graph(owner.project, request_sha256=identity)
            if not report['valid'] or not report['graph_present']:
                raise AuthoringPreviewError('graph_invalid')
            graph = EffectGraph(owner.project.effect_graph_spec)
            locations = _locations(draft, graph)
        except AuthoringPreviewError:
            raise
        except _INVALID_NATIVE_INPUT as exc:
            raise AuthoringPreviewError('graph_invalid') from exc
        project = owner.project
        if frame >= project.frame_count:
            raise AuthoringPreviewError('frame_invalid')
        check_preview_size(project.width, project.height)
        selected = graph.nodes.get(node)
        if selected is None or selected.kind != 'transition':
            raise AuthoringPreviewError('node_invalid')
        location = locations[node]
        result = {'schema': SCHEMA, 'request_sha256': identity,
                  'node': {'id': node, 'authored_id': location.authored_id, 'label': location.label,
                           'type': 'transition', 'transition_style': selected.config.get('transition_style', 'dissolve')},
                  'frame': frame, 'quality': quality, 'width': project.width, 'height': project.height,
                  'clock': None, 'render_quality': None,
                  'scope': {'pipeline': 'native_graph_before_sequential_effects',
                            'sequential_effects_excluded': bool(project.data.get('effects')),
                            'native_temporal_post_effects': bool(project.data.get('post_effects', {}).get('temporal')),
                            'black_matte': True, 'production_approved': False},
                  'images': []}
        for role, target in (('from', selected.inputs[0]), ('result', node), ('to', selected.inputs[1])):
            image, clock, settings = _render_view(owner, target, locations[target].label, role, frame, quality)
            if result['clock'] is not None and result['clock'] != clock:
                raise AuthoringPreviewError('render_failed')
            result['clock'], result['render_quality'] = clock, settings
            result['images'].append(image)
            if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_RESPONSE_BYTES:
                raise AuthoringPreviewError('preview_limit')
        _verify(owner)
        return result
    except AuthoringPreviewError:
        raise
    except _INVALID_NATIVE_INPUT as exc:
        raise AuthoringPreviewError('render_failed') from exc
    finally:
        try:
            owner.close()
        except _INVALID_NATIVE_INPUT as exc:
            raise AuthoringPreviewError('render_failed') from exc
