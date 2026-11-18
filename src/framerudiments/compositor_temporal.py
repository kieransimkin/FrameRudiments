"""Native compositor adapters for exact, bounded temporal task scheduling.

These stages end before external Butterchurn/projectM processing. Their schedule
identity describes work and order, never a persistent pixel cache or feedback
checkpoint. Native project clocks and input pins remain authoritative.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from math import ceil

from PIL import Image

from .output_quality import OutputQuality
from .quality_effects import linear_shutter_composite
from .temporal_schedule import FrameRef, FrameTask, TemporalDependency, build_schedule, execute_schedule

SCHEMA = 'framerudiments.compositor-temporal.v1'
MAX_NATIVE_TASKS = 128
MAX_NATIVE_EDGES = 256
MAX_NATIVE_LIVE_RESULTS = 24
MAX_NATIVE_RASTER_BYTES = 512 * 1024 * 1024
MAX_BATCH_SAMPLES = 64
MAX_BATCH_RGB_BYTES = 256 * 1024 * 1024


def _ratio(value):
    value = Fraction(value)
    return {'numerator': value.numerator, 'denominator': value.denominator}


def _frame(project, frame):
    if type(frame) is not int or not 0 <= frame < project.frame_count:
        raise ValueError('native temporal frame must be an integer inside the programme')
    return frame


def _repeat_span(project):
    return Fraction((project.end_sample - project.start_sample) * project.fps, project.sample_rate)


def temporal_read_bounds(document):
    """Conservative whole-frame dependency reach derived from native settings.

    Modulation can select the full eight-frame shutter. True subframes read
    forwards by less than one encoded frame, hence one conservative whole-frame
    reach. Neither value is an author-supplied invalidation hint.
    """
    quality = OutputQuality(document.get('render_quality', {}))
    post = document.get('post_effects', {})
    if not isinstance(post, dict):
        raise ValueError('post_effects must be an object')
    past = 8 if post.get('temporal') is not None else 0
    future = 1 if quality.time_samples > 1 else 0
    if 'temporal_fx' in post:
        from .temporal_fx_m11 import temporal_fx_bounds
        fx_past, fx_future = temporal_fx_bounds(post['temporal_fx'])
        past += fx_past
        future += fx_future
    return past, future


def temporal_contract(project):
    """JSON-only native sampling capabilities without raster or GPU evaluation."""
    factor = project.output_quality.time_samples
    past, future = temporal_read_bounds(project.data)
    return {
        'schema': SCHEMA,
        'coordinate': 'exact_encoded_frame_position',
        'source_clock': {
            'fps': project.fps, 'frame_count': project.frame_count,
            'sample_rate': project.sample_rate, 'start_sample': project.start_sample,
            'end_sample_exclusive': project.end_sample, 'repeats': project.repeats,
            'repeat_span_frames': _ratio(_repeat_span(project)),
        },
        'source_audio_sha256': project.audio_ref['sha256'],
        'max_reach': {'past_frames': past, 'future_frames': future,
                      'future_subframe': _ratio(Fraction(factor - 1, factor))},
        'sample_factor': factor,
        'sample_step': _ratio(Fraction(1, factor)),
        'repeat_policy': 'shutter taps stay in exact half-open source cycles; subframes sample the programme grid',
        'limits': {'native_tasks': MAX_NATIVE_TASKS, 'native_edges': MAX_NATIVE_EDGES,
                   'native_live_results': MAX_NATIVE_LIVE_RESULTS,
                   'native_retained_raster_bytes': MAX_NATIVE_RASTER_BYTES,
                   'batch_samples': MAX_BATCH_SAMPLES, 'batch_rgb_result_bytes': MAX_BATCH_RGB_BYTES},
        'scope': {'native_compositor': 'pre_external_effects', 'sequential_external_engines': False,
                  'configured_external_effects': bool(project.data.get('effects')),
                  'persistent_pixel_cache': False, 'feedback_checkpoints': False,
                  'pixel_cache_scope': 'scheduler_owned_inter_call_frame_results',
                  'raster_budget_excludes_existing_renderer_caches': True,
                  'raster_budget_excludes_callback_private_scratch_and_gpu_allocations': True},
    }


def _sample_refs(frame, factor, virtual_frame_count):
    return TemporalDependency('native', tuple(Fraction(j, factor) for j in range(factor)),
        boundary='clamp').resolve(frame,
            last_frame=Fraction(virtual_frame_count - 1, factor), sample_step=Fraction(1, factor))


def _plan_details(compositor, frame):
    project = compositor.project
    _frame(project, frame)
    context = compositor.context(frame)
    factor = compositor.output_quality.time_samples
    virtual_count = ceil(project.duration * project.fps * factor)
    shutter = compositor.post_pipeline.temporal
    if shutter is None:
        indices, weights, settings, reports = [frame], [1], None, {}
    else:
        indices, weights, settings, reports = shutter.plan(context)
    tasks = {}
    encoded = []
    for index in indices:
        refs = _sample_refs(index, factor, virtual_count)
        for ref in refs:
            tasks.setdefault(ref, FrameTask(ref))
        target = FrameRef('supersample', index)
        tasks[target] = FrameTask(target, refs)
        encoded.append(target)
    current = encoded[-1]
    if shutter is not None:
        current = FrameRef('shutter', frame)
        tasks[current] = FrameTask(current, tuple(encoded))
    output = FrameRef('output', frame)
    tasks[output] = FrameTask(output, (current,))
    schedule = build_schedule(tuple(tasks.values()), (output,), max_tasks=MAX_NATIVE_TASKS,
        max_edges=MAX_NATIVE_EDGES, max_live_results=MAX_NATIVE_LIVE_RESULTS)
    _check_raster_budget(schedule, compositor)
    return schedule, context, factor, indices, weights, settings, reports


def _check_raster_budget(schedule, compositor):
    pixels = compositor.project.width * compositor.project.height
    routing = getattr(compositor, 'visualiser_routing', None)
    captures = (getattr(compositor, '_temporal_route_count', 0)
                if routing is not None or getattr(compositor, '_isolate_stage_injections', False) else 0)
    averages = len(routing.available) if routing is not None and compositor.output_quality.time_samples > 1 else 0
    # Previous/current route sets plus one running premultiplied average and its
    # final RGBA conversion. Raw/history task results do not retain route sets.
    rgb = schedule.peak_live_results * pixels * 3
    rgba = (2 * captures + 2 * averages) * pixels * 4
    required = rgb + rgba
    if required > MAX_NATIVE_RASTER_BYTES:
        raise ValueError('native temporal schedule exceeds the bounded RGB result budget including RGBA routes')
    return {'rgb_result_bytes': rgb, 'rgba_route_bytes': rgba, 'retained_raster_bytes': required}


def temporal_plan(compositor, frame):
    """The native input plan executed for one encoded step, before feedback."""
    return _plan_details(compositor, frame)[0]


@dataclass
class _Sample:
    image: Image.Image
    metadata: dict


def _metadata(compositor, *, include_routes=True):
    return {
        'extents': deepcopy(compositor.extents), 'text_states': deepcopy(compositor.text_states),
        'post': deepcopy(compositor.post_pipeline.last_report),
        'reports': deepcopy(compositor.frame_reports()),
        # Route surfaces are immutable between renders; retaining references
        # avoids making a second complete set of private RGBA raster buffers.
        'routes': tuple(getattr(compositor, '_route_parts', ())) if include_routes else (),
        'supersampled': deepcopy(getattr(compositor, '_supersampled_reports', None)),
    }


def _restore(compositor, metadata):
    compositor.extents = deepcopy(metadata['extents'])
    compositor.text_states = deepcopy(metadata['text_states'])
    compositor.post_pipeline.last_report = deepcopy(metadata['post'])
    compositor._route_parts = list(metadata['routes'])
    if metadata['supersampled'] is None:
        compositor.__dict__.pop('_supersampled_reports', None)
    else:
        compositor._supersampled_reports = deepcopy(metadata['supersampled'])
    for layer in getattr(compositor, 'layers', ()):
        if hasattr(layer, 'frame_report'):
            layer.frame_report = deepcopy(metadata['reports'].get(layer.id))
    indicator = getattr(compositor, 'word_indicator', None)
    if indicator is not None:
        indicator.frame_report = deepcopy(metadata['reports'].get('word-indicator'))


def _combine_supersamples(compositor, frame, inputs, factor, baseline, *, retain_metadata=True,
                          route_averages=None):
    # One-sample mode must retain the ordinary native-frame metadata exactly.
    if factor == 1:
        return inputs[0]
    image = linear_shutter_composite([item.image for item in inputs], [1] * len(inputs), strength=1)
    if not retain_metadata:
        return _Sample(image, {})
    last = inputs[-1].metadata
    compositor.extents = deepcopy(baseline['extents'])
    compositor.extents.update(last['extents'])
    compositor.text_states = deepcopy(baseline['text_states'])
    for word, status in last['text_states'].items():
        status = deepcopy(status)
        if 'first_frame' in status:
            status['first_frame'] //= factor
        compositor.text_states.setdefault(word, status)
    if route_averages:
        compositor._route_parts = [(key, value.convert('RGBA')) for key, value in route_averages.items()]
    compositor.post_pipeline.last_report = deepcopy(last['post'])
    reports = deepcopy(last['reports'])
    for report in reports.values():
        if isinstance(report, dict) and 'frame' in report:
            report['subframe_index'] = report['frame']
            report['frame'] = frame
    compositor._supersampled_reports = reports
    for layer in getattr(compositor, 'layers', ()):
        if hasattr(layer, 'frame_report'):
            layer.frame_report = deepcopy(reports.get(layer.id))
    indicator = getattr(compositor, 'word_indicator', None)
    if indicator is not None:
        indicator.frame_report = deepcopy(reports.get('word-indicator'))
    return _Sample(image, _metadata(compositor))


def _accumulate_routes(owner, refs, ref, averages, size, keys):
    """Retain one current-frame average, preserving repeated clamped tap weights."""
    if not keys:
        return
    parts = dict(owner._route_parts)
    for index, requested in enumerate(refs):
        if requested != ref:
            continue
        for key in keys:
            value = parts.get(key)
            if value is None:
                value = Image.new('RGBA', size)
            value = value.convert('RGBa')
            averages[key] = value if index == 0 else Image.blend(averages[key], value, 1 / (index + 1))


def _schedule_summary(schedule, compositor, frame, executed):
    return {'schema': SCHEMA, 'frame': frame, 'digest': schedule.digest,
            'scope': 'pre_external_effects', 'task_count': len(schedule.tasks),
            'executed_tasks': executed, 'native_samples': len(schedule.source_refs),
            'stages': dict(sorted(Counter(task.ref.stage for task in schedule.tasks).items())),
            'peak_live_results': schedule.peak_live_results,
            **_check_raster_budget(schedule, compositor),
            'source_refs': [ref.document() for ref in schedule.source_refs], 'completed': True}


def render_scheduled_frame(compositor, frame, *, apply_quality=True, cancelled=None):
    """Render the immutable dependency plan once, with bounded transient results."""
    compositor._last_temporal_schedule = None
    if cancelled is not None and not callable(cancelled):
        raise ValueError('native cancellation callback must be callable')

    def check_cancelled():
        if cancelled is not None and cancelled():
            from .feedback_history import FeedbackCancelled
            raise FeedbackCancelled('superseded native frame request')

    check_cancelled()
    schedule, context, factor, indices, weights, settings, reports = _plan_details(compositor, frame)
    baseline = _metadata(compositor)
    virtual = None
    executed = 0
    current_refs = next(task.inputs for task in schedule.tasks
                        if task.ref == FrameRef('supersample', frame))
    route_keys = (compositor.visualiser_routing.available
                  if getattr(compositor, 'visualiser_routing', None) is not None else ())
    route_averages = {}
    try:
        if factor > 1:
            from .super_sampling import temporal_compositor
            virtual = temporal_compositor(compositor)

        def evaluate(ref, inputs):
            nonlocal executed
            check_cancelled()
            executed += 1
            if ref.stage == 'native':
                owner = virtual if virtual is not None else compositor
                if virtual is None:
                    compositor.extents = deepcopy(baseline['extents'])
                    compositor.text_states = deepcopy(baseline['text_states'])
                index = ref.frame * factor
                if index.denominator != 1:
                    raise RuntimeError('native schedule left its declared sample grid')
                image, _ = owner._native_frame(int(index), apply_quality=False)
                if virtual is not None and ref in current_refs:
                    _accumulate_routes(owner, current_refs, ref, route_averages, image.size, route_keys)
                metadata = (_metadata(owner, include_routes=virtual is None)
                            if ref == current_refs[-1] else {})
                return _Sample(image, metadata)
            if ref.stage == 'supersample':
                return _combine_supersamples(compositor, int(ref.frame), inputs, factor, baseline,
                    retain_metadata=ref.frame == frame, route_averages=route_averages)
            if ref.stage == 'shutter':
                _restore(compositor, inputs[-1].metadata)
                compositor.post_pipeline.last_report['temporal'] = {
                    'parameters': settings, 'modulations': reports, 'source_frames': indices,
                    'weights': weights, 'source_seconds': context.source_seconds,
                }
                image = compositor.post_pipeline.temporal.combine(
                    [item.image for item in inputs], weights, settings['strength'])
                return _Sample(image, _metadata(compositor))
            if ref.stage == 'output':
                _restore(compositor, inputs[0].metadata)
                image, _ = compositor._final_output(inputs[0].image, context, apply_quality)
                return _Sample(image, _metadata(compositor))
            raise RuntimeError('unknown native temporal stage')

        value, = execute_schedule(schedule, evaluate)
        check_cancelled()
        _restore(compositor, value.metadata)
        compositor._last_temporal_schedule = _schedule_summary(schedule, compositor, frame, executed)
        return value.image, context
    except BaseException:
        _restore(compositor, baseline)
        compositor._last_temporal_schedule = None
        # A failed native callback must not leave a partially advanced private
        # scene hook/cache to be mistaken for a completed next render.
        if virtual is not None:
            try:
                virtual.close()
            finally:
                compositor.__dict__.pop('_temporal_virtual', None)
        raise


def render_supersampled_frame(compositor, frame):
    """Compatibility adapter for the existing source-supersampling helper."""
    from .super_sampling import temporal_compositor

    compositor._last_temporal_schedule = None
    _frame(compositor.project, frame)
    factor = compositor.output_quality.time_samples
    if factor <= 1:
        raise ValueError('Temporal supersampling requires more than one sample')
    virtual_count = ceil(compositor.project.duration * compositor.project.fps * factor)
    refs = _sample_refs(frame, factor, virtual_count)
    result = FrameRef('supersample', frame)
    tasks = [FrameTask(ref) for ref in dict.fromkeys(refs)] + [FrameTask(result, refs)]
    schedule = build_schedule(tasks, (result,), max_tasks=MAX_NATIVE_TASKS,
        max_edges=MAX_NATIVE_EDGES, max_live_results=MAX_NATIVE_LIVE_RESULTS)
    _check_raster_budget(schedule, compositor)
    baseline = _metadata(compositor)
    virtual = None
    route_keys = (compositor.visualiser_routing.available
                  if getattr(compositor, 'visualiser_routing', None) is not None else ())
    route_averages = {}
    try:
        virtual = temporal_compositor(compositor)
        if virtual.project.frame_count != virtual_count:
            raise RuntimeError('virtual compositor differs from its admitted sample grid')

        def evaluate(ref, inputs):
            if inputs:
                return _combine_supersamples(compositor, frame, inputs, factor, baseline,
                    route_averages=route_averages)
            image, _ = virtual.frame(int(ref.frame * factor), apply_quality=False)
            _accumulate_routes(virtual, refs, ref, route_averages, image.size, route_keys)
            return _Sample(image, _metadata(virtual, include_routes=False) if ref == refs[-1] else {})

        value, = execute_schedule(schedule, evaluate)
        return value.image, compositor.context(frame)
    except BaseException:
        _restore(compositor, baseline)
        if virtual is not None:
            try:
                virtual.close()
            finally:
                compositor.__dict__.pop('_temporal_virtual', None)
        raise


def sample_frames(compositor, anchor, offsets, *, boundary='error', repeat_local=True,
                  apply_quality=True):
    """Return detached native frames for signed integer past/future taps.

    This complete-frame API deliberately refuses external sequential engines.
    Native fractional sampling is represented by ``temporal_plan`` and consumed
    internally by the existing supersampling route. No sampled pixels persist
    between calls. Returned order and multiplicity match the resolved taps.
    """
    project = compositor.project
    compositor._last_temporal_schedule = None
    _frame(project, anchor)
    if type(apply_quality) is not bool:
        raise ValueError('apply_quality must be boolean')
    if project.data.get('effects'):
        raise ValueError('sample_frames cannot execute sequential external visualiser effects')
    if (not isinstance(offsets, (tuple, list)) or not 1 <= len(offsets) <= MAX_BATCH_SAMPLES
            or any(type(value) not in (int, Fraction) or Fraction(value).denominator != 1 for value in offsets)):
        raise ValueError('complete-frame sampling requires 1..64 integer frame offsets')
    refs = TemporalDependency('complete_frame', tuple(offsets), boundary=boundary,
        repeat_local=repeat_local).resolve(anchor, last_frame=project.frame_count - 1,
            repeat_span=_repeat_span(project))
    if not refs:
        return ()
    unique = tuple(dict.fromkeys(refs))
    required = (len(unique) + len(refs)) * project.width * project.height * 3
    if required > MAX_BATCH_RGB_BYTES:
        raise ValueError('native frame batch exceeds the bounded RGB result budget')
    schedule = build_schedule(tuple(FrameTask(ref) for ref in unique), refs,
        max_tasks=MAX_BATCH_SAMPLES, max_edges=0, max_live_results=MAX_BATCH_SAMPLES)
    # Validate every nested native schedule before the first rendering callback.
    for ref in unique:
        temporal_plan(compositor, int(ref.frame))
    values = execute_schedule(schedule, lambda ref, _inputs: compositor.frame(int(ref.frame),
        apply_quality=apply_quality))
    return tuple((image.copy(), context) for image, context in values)
