"""Qualify M11.1 dependencies with independent native frames and actual CLI output.

The checked-in fixture is synthetic. Its reference derives repeat boundaries,
history taps and fractional source coordinates independently, then asks a native
one-sample compositor for those source pixels. It never calls the production
dependency resolver or schedule builder to choose reference samples. Both paths
retain the existing native renderer and linear-light blending semantics.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from time import monotonic
import wave

from PIL import Image, ImageChops, ImageDraw
from dancerudiments_authoring.recording import Recording, RecordingClock, write_recording

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from framerudiments import Project  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments import encoding  # noqa: E402
from framerudiments.output_quality import OutputQuality  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from framerudiments.quality_effects import linear_shutter_composite  # noqa: E402
from framerudiments.super_sampling import _virtual_project  # noqa: E402
from framerudiments.temporal_schedule import (  # noqa: E402
    FrameRef, FrameTask, TemporalDependency, build_schedule, execute_schedule,
)

FIXTURE = ROOT / 'integration/fixtures/temporal_schedule_m11_1.json'


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def rational(value):
    value = Fraction(value)
    return [value.numerator, value.denominator]


def _pin(path):
    return {'path': path.name, 'sha256': digest(path)}


def create_sources(directory, fixture, font):
    directory.mkdir()
    audio = directory / 'source.wav'
    with wave.open(str(audio), 'wb') as handle:
        handle.setparams((1, 2, 48000, 96000, 'NONE', 'not compressed'))
        handle.writeframes(b''.join(struct.pack('<h', round(1800 * math.sin(2 * math.pi * 220 * n / 48000)))
                                   for n in range(96000)))
    for name, colour in (('amber', '#f39537'), ('cyan', '#39bde5')):
        image = Image.new('RGBA', (64, 64))
        draw = ImageDraw.Draw(image)
        if name == 'amber':
            draw.ellipse((4, 4, 59, 59), fill=colour, outline='#ffe7ae', width=4)
            draw.rectangle((28, 9, 35, 54), fill='#ad293b')
        else:
            draw.polygon(((32, 3), (61, 57), (3, 57)), fill=colour)
            draw.line((15, 49, 49, 49), fill='#bff9ff', width=5)
        image.save(directory / f'{name}.png')
    shutil.copyfile(font, directory / 'font.ttf')
    recording = Recording('m11_1_saved_signal', ('intensity', 'accent'),
        tuple((2 * index / 255 - 1, 1 - 2 * index / 255) for index in range(256)),
        RecordingClock(90, 'synthetic capture independent of project tempo', 'confirmed', 0, 'supplied', 0))
    write_recording(directory / 'recording.json', recording)
    for name, text in fixture['source_text'].items():
        (directory / name).write_text(text, encoding='utf-8', newline='\n')
    return {path.name: digest(path) for path in sorted(directory.iterdir()) if path.is_file()}


def resolve_pins(value, directory):
    if isinstance(value, dict):
        if set(value) == {'$pin'}:
            return _pin(directory / value['$pin'])
        return {key: resolve_pins(child, directory) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_pins(child, directory) for child in value]
    return value


def verify_sources(directory, hashes):
    if any(digest(directory / name) != value for name, value in hashes.items()):
        raise RuntimeError('original_source_bytes_changed')


def _same_pixels(actual, expected, label):
    if actual.mode != expected.mode or actual.size != expected.size or actual.tobytes() != expected.tobytes():
        raise RuntimeError('independent_native_pixels_mismatch:' + label)


def _expected_cycle(project, coordinate):
    span = Fraction(project.end_sample - project.start_sample, project.sample_rate) * project.fps
    return int(Fraction(coordinate) // span)


def _expected_clock(project, frame):
    output = min(Fraction(frame, project.fps), project.duration - Fraction(1, project.sample_rate))
    span = Fraction(project.end_sample - project.start_sample, project.sample_rate)
    source = Fraction(project.start_sample, project.sample_rate) + output % span
    origin = Fraction(str(project.data['clock'].get('origin', 0)))
    bpm = Fraction(str(project.data['clock']['bpm']))
    source_beat = (source - origin) * bpm / 60
    return {'frame': frame, 'output_seconds': float(Fraction(frame, project.fps)),
            'source_seconds': float(source), 'source_beat': float(source_beat),
            'beat': float(source_beat + int(output // span) * span * bpm / 60)}


def _check_clock(project, frame, context):
    expected = _expected_clock(project, frame)
    if context.frame != frame:
        raise RuntimeError('native_context_frame_mismatch')
    error = max(abs(getattr(context, key) - value) for key, value in expected.items() if key != 'frame')
    if error > 1e-12:
        raise RuntimeError('independent_source_clock_mismatch')
    return error


class IndependentReference:
    """Independent sample selection over the unchanged native raster/clock APIs."""

    def __init__(self, project, case):
        self.project = project
        self.settings = case['temporal']
        self.quality = OutputQuality(project.data.get('render_quality'))
        self.factor = self.quality.time_samples
        document = deepcopy(project.data)
        document.get('post_effects', {}).pop('temporal', None)
        document['render_quality']['temporal_samples'] = 1
        raw = Project(document, project.base)
        # This released adapter preserves native interpolation, lag units and
        # timing ledgers. The oracle itself selects every source address below.
        raw = _virtual_project(raw, self.factor) if self.factor > 1 else raw
        self.native = Compositor(raw)
        self.raw = {}
        self.complete = {}
        self.source_clock_max_abs_error = 0
        self.last_tick = math.ceil(project.duration * project.fps * self.factor) - 1

    def indices(self, frame, *, permit_legacy_cross_cycle=False):
        if self.settings is None:
            return [frame]
        settings = self.settings
        values = {frame}
        span = Fraction(str(settings['shutter_frames']))
        count = settings['samples']
        if settings['strength'] and span >= 1:
            for index in range(1, count):
                lag = round(index * span / (count - 1))
                previous = frame - lag
                if lag <= 0 or previous < 0:
                    continue
                if permit_legacy_cross_cycle:
                    # Explicit bug witness only. This is the former timestamp
                    # comparison, not the expected reset contract.
                    if self.project.source_time(previous) > self.project.source_time(frame) + 1e-9:
                        continue
                elif _expected_cycle(self.project, previous) != _expected_cycle(self.project, frame):
                    continue
                values.add(previous)
        return sorted(values)

    def sample_coordinates(self, frame):
        return tuple(Fraction(min(frame * self.factor + index, self.last_tick), self.factor)
                     for index in range(self.factor))

    def source_coordinates(self, frame):
        return tuple(sorted({coordinate for previous in self.indices(frame)
                             for coordinate in self.sample_coordinates(previous)}))

    def current(self, frame):
        images = []
        for coordinate in self.sample_coordinates(frame):
            tick = int(coordinate * self.factor)
            if tick not in self.raw:
                image, context = self.native.frame(tick, apply_quality=False)
                self.source_clock_max_abs_error = max(self.source_clock_max_abs_error,
                    _check_clock(self.native.project, tick, context))
                self.raw[tick] = image.copy()
            images.append(self.raw[tick])
        return images[0].copy() if self.factor == 1 else linear_shutter_composite(images, [1] * self.factor, 1)

    def render(self, frame, *, permit_legacy_cross_cycle=False):
        if not permit_legacy_cross_cycle and frame in self.complete:
            return self.complete[frame].copy()
        indices = self.indices(frame, permit_legacy_cross_cycle=permit_legacy_cross_cycle)
        images = [self.current(index) for index in indices]
        if self.settings is None:
            image = images[0]
        else:
            settings = self.settings
            weights = [math.exp(-settings['falloff'] * (frame - index) /
                                max(1, settings['shutter_frames'])) for index in indices]
            image = linear_shutter_composite(images, weights, settings['strength'])
        image = self.quality.apply(image)
        if not permit_legacy_cross_cycle:
            self.complete[frame] = image.copy()
        return image

    def close(self):
        self.native.close()


def qualify_future_sampling(project, reference, fixture):
    renderer = Compositor(project)
    spec = fixture['future_sampling']
    offsets = tuple(spec['offsets'])
    try:
        results = renderer.sample_frames(spec['anchor'], offsets, boundary=spec['boundary'],
                                        repeat_local=spec['repeat_local'])
        expected = [min(project.frame_count - 1, max(0, spec['anchor'] + offset)) for offset in offsets]
        if len(results) != len(expected):
            raise RuntimeError('future_sampling_output_count_mismatch')
        for (image, context), frame in zip(results, expected):
            _same_pixels(image, reference.render(frame), 'future-sample:' + str(frame))
            _check_clock(project, frame, context)
        if results[2][0] is results[3][0]:
            raise RuntimeError('duplicate_sample_outputs_alias')
        original = results[3][0].tobytes()
        results[2][0].putpixel((0, 0), (1, 2, 3))
        if results[3][0].tobytes() != original:
            raise RuntimeError('sample_output_mutation_leaked')
        local = renderer.sample_frames(6, (-1, 0, 1), boundary='drop', repeat_local=True)
        permitted = [frame for frame in (5, 6, 7) if _expected_cycle(project, frame) == _expected_cycle(project, 6)]
        if [context.frame for _, context in local] != permitted:
            raise RuntimeError('future_repeat_boundary_mismatch')
        for image, context in local:
            _same_pixels(image, reference.render(context.frame), 'repeat-local-sample')
        return {'requested_anchor': spec['anchor'], 'offsets': list(offsets),
                'returned_frames': expected, 'future_frames_observed': any(offset > 0 for offset in offsets),
                'native_comparisons': len(results) + len(local), 'max_rgb8_error': 0,
                'duplicate_results_detached': True, 'repeat_local_frames': permitted}
    finally:
        renderer.close()


def qualify_generic_graph(project, fixture):
    """Actual future/fork graph with native pixels, independent direct references."""
    spec = fixture['generic_graph']
    sample_step = Fraction(*spec['sample_step'])
    half = Fraction(*spec['fractional_future'])
    window = TemporalDependency('native', tuple(spec['history_future_offsets']), boundary='clamp')
    forward = TemporalDependency('native', (half,), boundary='clamp')
    last = project.frame_count - 1
    frames = tuple(spec['frames'])
    nodes = {}
    for frame in frames:
        refs = window.resolve(frame, last_frame=last, sample_step=sample_step)
        ahead = forward.resolve(frame, last_frame=last, sample_step=sample_step)
        for source in (*refs, *ahead):
            nodes[source] = FrameTask(source)
        past_future = FrameRef('past-future', frame)
        fork = FrameRef('future-fork', frame)
        nodes[past_future] = FrameTask(past_future, refs)
        nodes[fork] = FrameTask(fork, ahead)
        out = FrameRef('output', frame)
        nodes[out] = FrameTask(out, (past_future, fork, past_future))
    outputs = tuple(FrameRef('output', frame) for frame in frames)
    tasks = tuple(nodes.values())
    schedule = build_schedule(tasks, outputs)
    shuffled = list(tasks)
    random.Random(1111).shuffle(shuffled)
    reordered = build_schedule(tuple(shuffled), outputs)
    if schedule.document() != reordered.document() or schedule.digest != reordered.digest:
        raise RuntimeError('task_declaration_order_changed_schedule')
    reversed_outputs = build_schedule(tasks, tuple(reversed(outputs)))
    if schedule.tasks != reversed_outputs.tasks or schedule.source_refs != reversed_outputs.source_refs:
        raise RuntimeError('request_order_changed_dependency_topology')
    raw_document = deepcopy(project.data)
    raw_document.pop('post_effects', None)
    raw_document['render_quality'] = {'spatial_scale': 1, 'temporal_samples': 1, 'antialias': 0}
    raw = Project(raw_document, project.base)
    native = Compositor(_virtual_project(raw, 2))
    independent = Compositor(_virtual_project(Project(deepcopy(raw_document), project.base), 2))
    evaluations = []
    try:
        def evaluate(ref, inputs):
            evaluations.append(ref)
            if ref.stage == 'native':
                return native.frame(int(ref.frame * 2), apply_quality=False)[0]
            if ref.stage == 'past-future':
                return linear_shutter_composite(list(inputs), [1, 2, 1], 1)
            if ref.stage == 'future-fork':
                return inputs[0].copy()
            return linear_shutter_composite(list(inputs), [1, 1, 1], 1)
        actual = execute_schedule(schedule, evaluate)
        if len(evaluations) != len(set(evaluations)) or set(evaluations) != set(nodes):
            raise RuntimeError('shared_source_or_task_evaluated_more_than_once')
        source_count = sum(ref.stage == 'native' for ref in evaluations)
        if source_count != len(schedule.source_refs):
            raise RuntimeError('native_source_count_mismatch')
        expected = []
        for frame in frames:
            coordinates = [min(Fraction(last), max(Fraction(0), Fraction(frame + offset)))
                           for offset in spec['history_future_offsets']]
            source_images = [independent.frame(int(value * 2), apply_quality=False)[0] for value in coordinates]
            window_image = linear_shutter_composite(source_images, [1, 2, 1], 1)
            ahead = min(Fraction(last), Fraction(frame) + half)
            forward_image = independent.frame(int(ahead * 2), apply_quality=False)[0]
            expected.append(linear_shutter_composite([window_image, forward_image, window_image], [1, 1, 1], 1))
        for actual_image, expected_image in zip(actual, expected):
            _same_pixels(actual_image, expected_image, 'generic-future-fork')
        evaluations.clear()
        replay = execute_schedule(reversed_outputs, evaluate)
        for actual_image, expected_image in zip(replay, reversed(expected)):
            _same_pixels(actual_image, expected_image, 'generic-reversed-requests')
        return {'declarations': [window.document(), forward.document()],
                'domain': {'last_frame': last, 'sample_step': rational(sample_step)},
                'schedule': schedule.document(), 'schedule_digest': schedule.digest,
                'permuted_declarations_identical': True, 'reversed_request_pixels_identical': True,
                'requested_frames': list(frames), 'tasks': len(schedule.tasks),
                'unique_native_source_evaluations': source_count,
                'shared_sources_evaluated_once': True, 'native_comparisons': len(expected) * 2,
                'max_rgb8_error': 0}
    finally:
        native.close()
        independent.close()


def _cli(command, project_file, output, ffmpeg, ffprobe):
    temporary_preflight(output.parent)
    arguments = [sys.executable, '-m', 'framerudiments', command, str(project_file), '--output', str(output)]
    if command != 'plan':
        arguments += ['--ffmpeg', ffmpeg, '--ffprobe', ffprobe]
    if command == 'render':
        arguments += ['--single-pass', '--preset', 'ultrafast', '--crf', '0', '--min-free-gib', '0']
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + environment.get('PYTHONPATH', '')
    environment.pop('FRAMERUDIMENTS_WORK_CACHE', None)
    started = monotonic()
    result = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError('native_cli_' + command + '_failed: ' + result.stderr[-6000:])
    document = json.loads((output if command == 'plan' else output / 'manifest.json').read_text())
    return document, {'command': command, 'returncode': 0, 'seconds': round(monotonic() - started, 3),
                      'frames': document['frames'], 'state': document['state']}


def _decoded_frames(ffmpeg, path, project):
    result = subprocess.run([ffmpeg, '-v', 'error', '-threads', '1', '-i', str(path),
        '-map', '0:v:0', '-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', '-'],
        capture_output=True, timeout=60, check=True)
    frame_bytes = project.width * project.height * 3
    if len(result.stdout) != project.frame_count * frame_bytes:
        raise RuntimeError('decoded_frame_count_mismatch')
    return [result.stdout[index:index + frame_bytes] for index in range(0, len(result.stdout), frame_bytes)]


def qualify_inspection(project_file, renderer, frames, output):
    temporary_preflight(output.parent)
    arguments = [sys.executable, '-m', 'framerudiments', 'inspect-temporal', str(project_file),
                 '--output', str(output)]
    for frame in frames:
        arguments += ['--frame', str(frame)]
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + environment.get('PYTHONPATH', '')
    result = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('inspect_temporal_cli_failed:' + result.stderr[-4000:])
    inspected = json.loads(output.read_text())
    if (inspected['schema'] != 'framerudiments.temporal-inspection.v1'
            or inspected['project_sha256'] != digest(project_file)
            or inspected['contract'] != renderer.temporal_contract()
            or inspected['scope']['pixels_rendered'] is not False
            or [item['frame'] for item in inspected['frames']] != list(frames)):
        raise RuntimeError('inspect_temporal_cli_contract_mismatch')
    for item in inspected['frames']:
        plan = renderer.temporal_plan(item['frame'])
        if item['digest'] != plan.digest or item['schedule'] != plan.document():
            raise RuntimeError('inspect_temporal_cli_schedule_mismatch')
    return {'command': 'inspect-temporal', 'returncode': 0,
            'requested_frames': list(frames), 'source_sha256': inspected['project_sha256'],
            'schedule_digests': [item['digest'] for item in inspected['frames']],
            'native_schedule_documents_match': True, 'pixels_rendered': False}


def qualify_cli(project_file, project, expected, directory, output, name, ffmpeg, ffprobe):
    directory.mkdir()
    plan, plan_receipt = _cli('plan', project_file, directory / 'plan.json', ffmpeg, ffprobe)
    preflight, preflight_receipt = _cli('preflight', project_file, directory / 'preflight', ffmpeg, ffprobe)
    rendered, render_receipt = _cli('render', project_file, directory / 'render', ffmpeg, ffprobe)
    health = json.loads((directory / 'render/candidate-health.json').read_text())
    if (preflight['preflight_scope']['complete'] is not True
            or preflight['preflight_scope']['frames_rendered'] != project.frame_count
            or rendered['verification']['full_decode'] != 'passed'
            or health['technical_qa'] != 'passed' or rendered['frames'] != project.frame_count):
        raise RuntimeError('native_cli_complete_technical_qa_missing')
    if ('temporal_schedule' not in plan
            or plan['temporal_schedule'] != rendered['composition']['temporal_schedule']['contract']):
        raise RuntimeError('native_cli_temporal_schedule_evidence_missing')
    reference_video = directory / 'independent-reference.mp4'
    temporary_preflight(directory)
    result = subprocess.run(encoding.command(project, reference_video, ffmpeg, preset='ultrafast', crf=0),
        input=b''.join(image.tobytes() for image in expected), capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError('independent_reference_encode_failed:' + result.stderr.decode(errors='replace')[-4000:])
    verified = encoding.verify(reference_video, project, ffmpeg, ffprobe)
    actual_video = directory / 'render/video.mp4'
    actual = _decoded_frames(ffmpeg, actual_video, project)
    reference = _decoded_frames(ffmpeg, reference_video, project)
    if actual != reference:
        raise RuntimeError('independent_encoded_pixels_mismatch')
    delivered = output / f'temporal-schedule-{name}.mp4'
    shutil.copyfile(actual_video, delivered)
    return {'commands': [plan_receipt, preflight_receipt, render_receipt],
            'full_preflight': preflight['preflight_scope'],
            'independent_encode_verification': {key: value for key, value in verified.items() if key != 'probe'},
            'plan_and_composition_temporal_contract_match': True,
            'reference': 'independently selected native source samples, encoded with the existing native encoder command',
            'encoded_frames_per_video': project.frame_count, 'encoded_videos': 2,
            'decoded_comparisons': len(reference), 'max_rgb8_error': 0,
            'source_start_sample': plan['source_start_sample'],
            'source_end_sample_exclusive': plan['source_end_sample_exclusive'],
            'sample_rate': plan['sample_rate'], 'repeats': plan['repeats'],
            'video_sha256': digest(delivered), 'decoded_reference_sha256': [sha256(row).hexdigest() for row in reference],
            'perceptual_approval': False}


def qualify(output, *, font, ffmpeg, ffprobe):
    output = Path(output).resolve()
    temporary_preflight(output.parent)
    output.mkdir(exist_ok=False)
    temporary_preflight(output)
    fixture = json.loads(FIXTURE.read_text())
    if fixture['schema'] != 'framerudiments.temporal-schedule-fixture.v1':
        raise RuntimeError('unexpected_fixture_schema')
    report = {'schema': 'framerudiments.temporal-schedule-qualification.v1', 'passed': False,
        'fixture_sha256': digest(FIXTURE),
        'runtime': {'python': platform.python_version(), 'dancerudiments': version('dancerudiments'),
                    'pillow': version('Pillow'),
                    'ffmpeg': subprocess.check_output([ffmpeg, '-version'], text=True).splitlines()[0]},
        'implementation_sha256': {name: digest(ROOT / name) for name in (
            'src/framerudiments/temporal_schedule.py', 'src/framerudiments/compositor_temporal.py',
            'src/framerudiments/compositor.py', 'src/framerudiments/quality_effects.py',
            'src/framerudiments/super_sampling.py', 'integration/qualify_temporal_schedule_m11_1.py')},
        'scope': {'independent_rational_dependency_oracle': True, 'actual_native_compositor': True,
            'actual_future_sampling_api': True, 'actual_generic_future_fork_graph': True,
            'actual_cli_plan_full_preflight_render': True, 'actual_encoded_comparison': True,
            'reference_uses_native_raster_and_linear_light_blend': True,
            'feedback_checkpoints': False, 'sequential_external_engines': False,
            'browser_interaction': False, 'gpu': False, 'physical_device': False, 'perceptual_approval': False},
        'cases': []}
    started = monotonic()
    width, height = fixture['project']['width'], fixture['project']['height']
    rows = sum(len(case['gallery_frames']) for case in fixture['cases'])
    gallery = Image.new('RGB', (width * 3, (height + 34) * rows), '#121b2d')
    draw = ImageDraw.Draw(gallery)
    row = 0
    with tempfile.TemporaryDirectory(prefix='m11-1-temporal-', dir=output) as owned:
        directory = Path(owned)
        sources = directory / 'sources'
        pins = create_sources(sources, fixture, font)
        report['source_sha256'] = pins
        base = resolve_pins(fixture['project'], sources)
        for case in fixture['cases']:
            document = deepcopy(base)
            document['audio'].update(case['audio'])
            document['render_quality'].update(case['quality'])
            if case['temporal'] is not None:
                document['post_effects'] = {'temporal': case['temporal']}
            project_file = sources / f"{case['name']}.json"
            project_file.write_text(json.dumps(document, indent=2) + '\n')
            original_hash = digest(project_file)
            project = Project.load(project_file)
            reference = IndependentReference(project, case)
            renderer = Compositor(Project.load(project_file))
            sequential = []
            checkpoints = []
            maximum_clock_error = 0
            try:
                for frame in range(project.frame_count):
                    expected = reference.render(frame)
                    actual, context = renderer.frame(frame)
                    _same_pixels(actual, expected, case['name'] + ':' + str(frame))
                    maximum_clock_error = max(maximum_clock_error, _check_clock(project, frame, context))
                    plan = renderer.temporal_plan(frame)
                    execution = renderer.evidence()['temporal_schedule']['last_execution']
                    if (execution['completed'] is not True or execution['digest'] != plan.digest
                            or execution['executed_tasks'] != len(plan.tasks)
                            or execution['native_samples'] != len(plan.source_refs)):
                        raise RuntimeError('native_plan_execution_receipt_mismatch')
                    observed = tuple(sorted(Fraction(ref.frame) for ref in plan.source_refs))
                    wanted = reference.source_coordinates(frame)
                    if observed != wanted:
                        raise RuntimeError('independent_native_source_addresses_mismatch:' + str(frame))
                    sequential.append(actual.copy())
                    checkpoints.append({'frame': frame, 'history_frames': reference.indices(frame),
                        'native_source_coordinates': [rational(value) for value in wanted],
                        'schedule_digest': plan.digest, 'tasks': len(plan.tasks),
                        'executed_tasks': execution['executed_tasks'],
                        'native_source_evaluations': execution['native_samples'],
                        'peak_live_results': plan.peak_live_results,
                        'pixel_sha256': sha256(actual.tobytes()).hexdigest()})
                order = list(range(project.frame_count))
                random.Random(111).shuffle(order)
                order += [project.frame_count - 1, 0, project.frame_count // 2, project.frame_count - 1]
                for frame in order:
                    image, context = renderer.frame(frame)
                    _same_pixels(image, sequential[frame], case['name'] + ':nonsequential:' + str(frame))
                    maximum_clock_error = max(maximum_clock_error, _check_clock(project, frame, context))
                if len({image.tobytes() for image in sequential}) < 3:
                    raise RuntimeError('fixture_has_insufficient_observable_motion')
                case_report = {'name': case['name'], 'frame_count': project.frame_count,
                    'repeat_span_frames': rational(Fraction(project.end_sample - project.start_sample,
                                                          project.sample_rate) * project.fps),
                    'quality_samples': reference.factor, 'checkpoints': checkpoints,
                    'independent_native_comparisons': project.frame_count,
                    'nonsequential_native_comparisons': len(order), 'nonsequential_order': order,
                    'max_rgb8_error': 0, 'independent_rational_clock_max_abs_error': maximum_clock_error,
                    'independent_rational_subframe_clock_max_abs_error': reference.source_clock_max_abs_error,
                    'temporal_contract': renderer.temporal_contract(),
                    'exact_source_coordinates': True, 'original_inputs_unchanged': True}
                case_report['inspection'] = qualify_inspection(project_file, renderer,
                    (*case['gallery_frames'], case['gallery_frames'][0]),
                    directory / (case['name'] + '-inspection.json'))
                if case['name'] == 'short_repeat':
                    correct = reference.render(6)
                    old = reference.render(6, permit_legacy_cross_cycle=True)
                    if correct.tobytes() == old.tobytes() or reference.indices(6) != [6]:
                        raise RuntimeError('short_repeat_regression_not_observable')
                    case_report['repeat_reset_regression'] = {'frame': 6,
                        'correct_history_frames': [6],
                        'former_source_time_comparison_frames': reference.indices(6, permit_legacy_cross_cycle=True),
                        'wrong_previous_cycle_pixels_differ': True}
                    case_report['future_sampling'] = qualify_future_sampling(project, reference, fixture)
                if case['name'] == 'ordinary':
                    case_report['generic_future_graph'] = qualify_generic_graph(project, fixture)
                if case['cli']:
                    case_report['cli'] = qualify_cli(project_file, project,
                        [reference.render(frame) for frame in range(project.frame_count)],
                        directory / (case['name'] + '-cli'), output, case['name'], ffmpeg, ffprobe)
                for frame in case['gallery_frames']:
                    y = row * (height + 34)
                    draw.text((5, y + 3), f"{case['name']} / frame {frame} | exact native RGB8 match", fill='white')
                    draw.text((5, y + 18), 'Independent sample oracle | Scheduled compositor | Absolute difference (zero)', fill='#b5c7e3')
                    expected = reference.render(frame)
                    gallery.paste(expected, (0, y + 34))
                    gallery.paste(sequential[frame], (width, y + 34))
                    gallery.paste(ImageChops.difference(expected, sequential[frame]), (width * 2, y + 34))
                    row += 1
                verify_sources(sources, pins)
                project.verify_inputs()
                if digest(project_file) != original_hash:
                    raise RuntimeError('original_project_changed')
                report['cases'].append(case_report)
            finally:
                renderer.close()
                reference.close()
        png = output / 'temporal-schedule-contact-sheet.png'
        gallery.save(png)
        report['contact_sheet_sha256'] = digest(png)
    report['owned_temporary_tree_removed'] = not directory.exists()
    report['original_projects_and_sources_unchanged'] = True
    report['seconds'] = round(monotonic() - started, 3)
    report['passed'] = report['owned_temporary_tree_removed']
    (output / 'temporal-schedule-qualification.json').write_text(
        json.dumps(report, indent=2, allow_nan=False) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New qualification evidence directory')
    parser.add_argument('--font', default=os.environ.get('FRAMERUDIMENTS_TEST_FONT'),
                        required=not os.environ.get('FRAMERUDIMENTS_TEST_FONT'))
    parser.add_argument('--ffmpeg', default=os.environ.get('FRAMERUDIMENTS_FFMPEG') or shutil.which('ffmpeg'))
    parser.add_argument('--ffprobe', default=os.environ.get('FRAMERUDIMENTS_FFPROBE') or shutil.which('ffprobe'))
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe or not Path(args.font).is_file():
        parser.error('An installed font, FFmpeg and ffprobe are required; this command never downloads them')
    report = qualify(args.output, font=args.font, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
        'independent_native_comparisons': sum(case['independent_native_comparisons'] for case in report['cases']),
        'nonsequential_native_comparisons': sum(case['nonsequential_native_comparisons'] for case in report['cases']),
        'decoded_comparisons': sum(case.get('cli', {}).get('decoded_comparisons', 0) for case in report['cases']),
        'seconds': report['seconds'], 'owned_temporary_tree_removed': report['owned_temporary_tree_removed']}))


if __name__ == '__main__':
    main()
