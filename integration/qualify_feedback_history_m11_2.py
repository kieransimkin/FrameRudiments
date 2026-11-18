"""Qualify native delayed feedback and real checkpoint reconstruction.

The reference stores a literal indexed recurrence over feedback-disabled native
source images. It never calls the production feedback planner, delay history or
checkpoint engine. Native source rasterisation and established linear-light
blending remain shared primitives. No opaque external engine is checkpointed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
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
from framerudiments import encoding  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from framerudiments.quality_effects import linear_shutter_composite  # noqa: E402

FIXTURE = ROOT / 'integration/fixtures/feedback_history_m11_2.json'


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _same_pixels(actual, expected, label):
    if actual.mode != expected.mode or actual.size != expected.size or actual.tobytes() != expected.tobytes():
        raise RuntimeError('independent_feedback_pixels_mismatch:' + label)


def create_sources(directory, fixture, font):
    directory.mkdir()
    with wave.open(str(directory / 'source.wav'), 'wb') as audio:
        audio.setparams((1, 2, 48000, 96000, 'NONE', 'not compressed'))
        audio.writeframes(b''.join(struct.pack('<h', round(1800 * math.sin(2 * math.pi * 220 * n / 48000)))
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
    recording = Recording('m11_2_saved_signal', ('intensity', 'accent'),
        tuple((2 * index / 255 - 1, 1 - 2 * index / 255) for index in range(256)),
        RecordingClock(90, 'synthetic capture independent of project tempo', 'confirmed', 0, 'supplied', 0))
    write_recording(directory / 'recording.json', recording)
    for name, text in fixture['source_text'].items():
        (directory / name).write_text(text, encoding='utf-8', newline='\n')
    return {path.name: digest(path) for path in sorted(directory.iterdir()) if path.is_file()}


def resolve_pins(value, directory):
    if isinstance(value, dict):
        if set(value) == {'$pin'}:
            path = directory / value['$pin']
            return {'path': path.name, 'sha256': digest(path)}
        return {key: resolve_pins(child, directory) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_pins(child, directory) for child in value]
    return value


def verify_sources(directory, pins):
    if any(digest(directory / name) != value for name, value in pins.items()):
        raise RuntimeError('original_source_bytes_changed')


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


class IndependentRecurrence:
    """Literal forward recurrence; no production feedback/history/checkpoint calls."""

    def __init__(self, project):
        self.project = project
        self.settings = deepcopy(project.data['post_effects']['feedback'])
        document = deepcopy(project.data)
        del document['post_effects']['feedback']
        self.source = Compositor(Project(document, project.base))
        self.inputs, self.history, self.outputs = [], [], []
        self.clock_max_abs_error = 0
        self.span = Fraction((project.end_sample - project.start_sample) * project.fps, project.sample_rate)

    def reset_frame(self, frame):
        if self.settings['reset'] == 'programme':
            return 0
        cycle = Fraction(frame) // self.span
        return math.ceil(cycle * self.span)

    def previous(self, frame):
        previous = frame - self.settings['delay_frames']
        return previous if previous >= self.reset_frame(frame) else None

    def render(self, frame, *, apply_quality=True):
        while len(self.history) <= frame:
            index = len(self.history)
            current, context = self.source.frame(index, apply_quality=False)
            self.clock_max_abs_error = max(self.clock_max_abs_error, _check_clock(self.project, index, context))
            self.inputs.append(current.copy())
            previous = self.previous(index)
            if previous is None:
                result = current.copy()
            else:
                mix = self.settings['mix']
                result = linear_shutter_composite([current, self.history[previous]], [1 - mix, mix], 1)
            self.history.append(result.copy())
            self.outputs.append(self.project.output_quality.apply(result.copy()))
        return (self.outputs if apply_quality else self.history)[frame].copy()

    def close(self):
        self.source.close()


@contextmanager
def observed_native_calls():
    """Count actual native sample callbacks, including the private subframe grid."""
    original = Compositor._native_frame
    calls = []

    def observed(owner, frame, **kwargs):
        calls.append((owner.project.fps, frame))
        return original(owner, frame, **kwargs)

    Compositor._native_frame = observed
    try:
        yield calls
    finally:
        Compositor._native_frame = original


def execution(renderer, frame):
    evidence = renderer.evidence()['feedback']
    if evidence['contract'] != renderer.feedback_contract():
        raise RuntimeError('feedback_execution_contract_mismatch')
    last = evidence['state']['last_execution']
    if last['completed'] is not True or last['target_frame'] != frame:
        raise RuntimeError('feedback_execution_receipt_missing')
    if last['executed_steps'] != last['steps'] or last['evaluated_frames'][-1] != frame:
        raise RuntimeError('feedback_execution_steps_mismatch')
    if last['evaluated_frames'] != list(range(last['start_frame'], frame + 1)):
        raise RuntimeError('feedback_execution_frame_order_mismatch')
    return deepcopy(last)


def qualify_checkpoint(project, reference, case):
    producer = Compositor(Project(deepcopy(project.data), project.base))
    restored = Compositor(Project(deepcopy(project.data), project.base))
    cold = Compositor(Project(deepcopy(project.data), project.base))
    seed, target = case['checkpoint_seed'], case['checkpoint_target']
    try:
        image, _ = producer.frame(seed)
        _same_pixels(image, reference.render(seed), case['name'] + ':checkpoint-seed')
        checkpoint = producer.feedback_checkpoint()
        if checkpoint is None or checkpoint.next_frame != seed + 1:
            raise RuntimeError('actual_feedback_checkpoint_missing')
        expected_start = max(reference.reset_frame(seed), seed + 1 - case['feedback']['delay_frames'])
        if [frame for frame, _payload in checkpoint.history] != list(range(expected_start, seed + 1)):
            raise RuntimeError('feedback_checkpoint_did_not_capture_complete_delay_ring')
        before = checkpoint.digest
        image.paste('#ff00ff', (0, 0, image.width, image.height))
        if checkpoint.digest != before:
            raise RuntimeError('returned_image_aliases_checkpoint_state')
        with observed_native_calls() as admission_calls:
            restored.restore_feedback(checkpoint, expected_sha256=before)
        if admission_calls:
            raise RuntimeError('checkpoint_restore_drew_pixels_during_admission')
        plan = restored.feedback_plan(target)
        with observed_native_calls() as restored_calls:
            image, context = restored.frame(target)
        _same_pixels(image, reference.render(target), case['name'] + ':restored')
        _check_clock(project, target, context)
        restored_execution = execution(restored, target)
        if (restored_execution['origin'] != 'checkpoint'
                or restored_execution['checkpoint_sha256'] != before
                or restored_execution['start_frame'] != checkpoint.next_frame):
            raise RuntimeError('checkpoint_restore_not_used_for_reconstruction')
        with observed_native_calls() as cold_calls:
            direct, _ = cold.frame(target)
        _same_pixels(image, direct, case['name'] + ':cold-vs-restored')
        cold_execution = execution(cold, target)
        if not 0 < len(restored_calls) < len(cold_calls):
            raise RuntimeError('checkpoint_did_not_reduce_actual_native_work')
        if checkpoint.digest != before:
            raise RuntimeError('checkpoint_payload_mutated_during_restore')
        return {'checkpoint': checkpoint.document(), 'checkpoint_sha256': before,
                'seed_frame': seed, 'target_frame': target, 'plan': plan,
                'complete_delay_ring_frames': [frame for frame, _payload in checkpoint.history],
                'admission_native_callbacks': 0,
                'restored_native_sample_callbacks': len(restored_calls),
                'cold_native_sample_callbacks': len(cold_calls),
                'restored_execution': restored_execution, 'cold_execution': cold_execution,
                'actual_native_work_reduced': True, 'checkpoint_unchanged': True,
                'returned_image_does_not_mutate_checkpoint': True,
                'native_comparisons': 3, 'max_rgb8_error': 0,
                'checkpoint_transport': 'immutable_in_memory_state_with_explicit_expected_sha256',
                'metadata_json_is_restorable_state': False}
    finally:
        producer.close()
        restored.close()
        cold.close()


def qualify_quality_switch(project, reference, case):
    renderer = Compositor(Project(deepcopy(project.data), project.base))
    frames = (case['checkpoint_target'] - 1, case['checkpoint_target'], case['checkpoint_target'] + 1)
    settings = (False, True, False)
    try:
        for frame, enabled in zip(frames, settings):
            image, _ = renderer.frame(frame, apply_quality=enabled)
            _same_pixels(image, reference.render(frame, apply_quality=enabled),
                         case['name'] + ':quality-switch:' + str(frame))
        return {'frames': list(frames), 'apply_quality': list(settings), 'native_comparisons': len(frames),
                'max_rgb8_error': 0, 'history_precedes_final_quality': True}
    finally:
        renderer.close()


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
    arguments = [sys.executable, '-m', 'framerudiments', 'inspect-temporal', str(project_file), '--output', str(output)]
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
            or inspected['feedback'] != renderer.feedback_contract()
            or inspected['scope']['pixels_rendered'] is not False
            or [item['frame'] for item in inspected['frames']] != list(frames)):
        raise RuntimeError('inspect_temporal_cli_contract_mismatch')
    # A new CLI inspector owns no previous feedback history. Compare with a new
    # native owner, not the warmed renderer used by the other qualification.
    cold = Compositor(Project.load(project_file))
    try:
        for item in inspected['frames']:
            plan = cold.temporal_plan(item['frame'])
            if item['digest'] != plan.digest or item['schedule'] != plan.document():
                raise RuntimeError('inspect_temporal_cli_native_schedule_mismatch')
            if item['feedback'] != cold.feedback_plan(item['frame']):
                raise RuntimeError('inspect_temporal_cli_feedback_plan_mismatch')
    finally:
        cold.close()
    return {'command': 'inspect-temporal', 'returncode': 0,
            'requested_frames': list(frames), 'source_sha256': inspected['project_sha256'],
            'native_schedule_documents_match': True, 'feedback_plan_documents_match': True,
            'pixels_rendered': False, 'feedback_contract': renderer.feedback_contract()}


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
    if plan['feedback'] != rendered['composition']['feedback']['contract']:
        raise RuntimeError('native_cli_feedback_contract_missing')
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
        raise RuntimeError('independent_feedback_encoded_pixels_mismatch')
    delivered = output / f'feedback-history-{name}.mp4'
    shutil.copyfile(actual_video, delivered)
    return {'commands': [plan_receipt, preflight_receipt, render_receipt],
            'full_preflight': preflight['preflight_scope'],
            'independent_encode_verification': {key: value for key, value in verified.items() if key != 'probe'},
            'plan_and_composition_feedback_contract_match': True,
            'reference': 'independent delayed recurrence over native input frames, using the existing native encoder command',
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
    if fixture['schema'] != 'framerudiments.feedback-history-fixture.v1':
        raise RuntimeError('unexpected_fixture_schema')
    report = {'schema': 'framerudiments.feedback-history-qualification.v1', 'passed': False,
        'fixture_sha256': digest(FIXTURE),
        'implementation_sha256': {name: digest(ROOT / name) for name in (
            'src/framerudiments/feedback_history.py', 'src/framerudiments/compositor_feedback.py',
            'src/framerudiments/compositor.py', 'src/framerudiments/compositor_temporal.py',
            'src/framerudiments/scoped_effects.py', 'src/framerudiments/quality_effects.py',
            'src/framerudiments/super_sampling.py', 'src/framerudiments/temporal_inspection.py',
            'src/framerudiments/cli.py', 'integration/qualify_feedback_history_m11_2.py')},
        'runtime': {'python': platform.python_version(), 'dancerudiments': version('dancerudiments'),
                    'pillow': version('Pillow'),
                    'ffmpeg': subprocess.check_output([ffmpeg, '-version'], text=True).splitlines()[0]},
        'scope': {'independent_literal_delayed_recurrence': True, 'actual_native_compositor': True,
            'actual_native_checkpoint_restore': True, 'actual_native_callback_counts': True,
            'reference_uses_native_raster_and_linear_light_blend': True,
            'actual_cli_inspection_plan_full_preflight_render': True, 'actual_encoded_comparison': True,
            'checkpoint_transport': 'immutable_in_memory_state_with_pinned_digest',
            'metadata_json_is_restorable_state': False, 'sequential_external_engines': False,
            'browser_interaction': False, 'gpu': False, 'physical_device': False, 'perceptual_approval': False},
        'cases': []}
    started = monotonic()
    width, height = fixture['project']['width'], fixture['project']['height']
    rows = sum(len(case['gallery_frames']) for case in fixture['cases'])
    gallery = Image.new('RGB', (width * 3, (height + 34) * rows), '#121b2d')
    draw = ImageDraw.Draw(gallery)
    row = 0
    with tempfile.TemporaryDirectory(prefix='m11-2-feedback-', dir=output) as owned:
        directory = Path(owned)
        sources = directory / 'sources'
        pins = create_sources(sources, fixture, font)
        report['source_sha256'] = pins
        for case in fixture['cases']:
            document = resolve_pins(deepcopy(fixture['project']), sources)
            document['audio'].update(case['audio'])
            document['render_quality'].update(case['quality'])
            document['post_effects'] = {'feedback': deepcopy(case['feedback'])}
            if case['temporal'] is not None:
                document['post_effects']['temporal'] = deepcopy(case['temporal'])
            project_file = sources / f"{case['name']}.json"
            project_file.write_text(json.dumps(document, indent=2) + '\n')
            original_hash = digest(project_file)
            project = Project.load(project_file)
            reference = IndependentRecurrence(project)
            renderer = Compositor(Project.load(project_file))
            sequential, checkpoints = [], []
            maximum_clock_error = 0
            try:
                for frame in range(project.frame_count):
                    expected = reference.render(frame)
                    image, context = renderer.frame(frame)
                    _same_pixels(image, expected, case['name'] + ':sequential:' + str(frame))
                    maximum_clock_error = max(maximum_clock_error, _check_clock(project, frame, context))
                    actual = execution(renderer, frame)
                    if actual['reset_frame'] != reference.reset_frame(frame):
                        raise RuntimeError('independent_feedback_reset_boundary_mismatch')
                    sequential.append(image.copy())
                    checkpoints.append({'frame': frame, 'reset_frame': reference.reset_frame(frame),
                        'previous_feedback_frame': reference.previous(frame),
                        'execution': actual, 'pixel_sha256': sha256(image.tobytes()).hexdigest()})
                if len({image.tobytes() for image in sequential}) < 3:
                    raise RuntimeError('fixture_has_insufficient_observable_motion')
                if all(image.tobytes() == project.output_quality.apply(source).tobytes()
                       for image, source in zip(sequential, reference.inputs)):
                    raise RuntimeError('fixture_has_no_observable_delayed_feedback')
                bounded = renderer.feedback_snapshot()
                if (bounded['checkpoint_count'] > case['feedback']['max_checkpoints']
                        or bounded['checkpoint_evictions'] < 1
                        or bounded['retained_state_bytes'] + bounded['working_reserve_bytes']
                            > case['feedback']['max_state_bytes']):
                    raise RuntimeError('feedback_checkpoint_bounds_not_observed')
                old_frame = case['eviction_seek']
                if any(next_frame <= old_frame for next_frame in bounded['checkpoint_frames']):
                    raise RuntimeError('old_seek_checkpoint_was_not_evicted')
                prior_plan = renderer.feedback_plan(old_frame)
                with observed_native_calls() as replay_calls:
                    old, _ = renderer.frame(old_frame)
                _same_pixels(old, reference.render(old_frame), case['name'] + ':evicted-reconstruction')
                old_execution = execution(renderer, old_frame)
                if old_execution['start_frame'] != reference.reset_frame(old_frame):
                    raise RuntimeError('old_seek_did_not_reconstruct_after_bounded_history')
                order = list(range(project.frame_count))
                random.Random(112).shuffle(order)
                order += [project.frame_count - 1, 0, project.frame_count // 2, project.frame_count - 1]
                for frame in order:
                    image, context = renderer.frame(frame)
                    _same_pixels(image, sequential[frame], case['name'] + ':nonsequential:' + str(frame))
                    maximum_clock_error = max(maximum_clock_error, _check_clock(project, frame, context))
                case_report = {'name': case['name'], 'frame_count': project.frame_count,
                    'feedback': case['feedback'], 'quality_samples': project.output_quality.time_samples,
                    'repeat_span_frames': [reference.span.numerator, reference.span.denominator],
                    'independent_native_comparisons': project.frame_count,
                    'nonsequential_native_comparisons': len(order), 'nonsequential_order': order,
                    'max_rgb8_error': 0, 'independent_rational_clock_max_abs_error': maximum_clock_error,
                    'independent_source_clock_max_abs_error': reference.clock_max_abs_error,
                    'checkpoints': checkpoints, 'feedback_contract': renderer.feedback_contract(),
                    'bounded_checkpoint_storage': {key: bounded[key] for key in (
                        'checkpoint_frames', 'checkpoint_count', 'checkpoint_evictions',
                        'retained_state_bytes', 'working_reserve_bytes', 'limits')},
                    'old_seek_reconstruction': {'frame': old_frame, 'plan': prior_plan,
                        'execution': old_execution, 'actual_native_sample_callbacks': len(replay_calls),
                        'native_comparisons': 1, 'max_rgb8_error': 0},
                    'original_inputs_unchanged': True}
                case_report['checkpoint_restore'] = qualify_checkpoint(project, reference, case)
                case_report['quality_switch'] = qualify_quality_switch(project, reference, case)
                case_report['inspection'] = qualify_inspection(project_file, renderer,
                    (*case['gallery_frames'], case['gallery_frames'][0]),
                    directory / (case['name'] + '-inspection.json'))
                if case['cli']:
                    case_report['cli'] = qualify_cli(project_file, project,
                        [reference.render(frame) for frame in range(project.frame_count)],
                        directory / (case['name'] + '-cli'), output, case['name'], ffmpeg, ffprobe)
                for frame in case['gallery_frames']:
                    y = row * (height + 34)
                    draw.text((5, y + 3), f"{case['name']} / frame {frame} | exact native RGB8 match", fill='white')
                    draw.text((5, y + 18), 'Independent recurrence | Checkpointed compositor | Absolute difference (zero)', fill='#b5c7e3')
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
        png = output / 'feedback-history-contact-sheet.png'
        gallery.save(png)
        report['contact_sheet_sha256'] = digest(png)
    report['owned_temporary_tree_removed'] = not directory.exists()
    report['original_projects_and_sources_unchanged'] = True
    report['seconds'] = round(monotonic() - started, 3)
    report['passed'] = report['owned_temporary_tree_removed']
    (output / 'feedback-history-qualification.json').write_text(
        json.dumps(report, indent=2, allow_nan=False) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New qualification evidence directory')
    parser.add_argument('--font', default=os.environ.get('FRAMERUDIMENTS_TEST_FONT'))
    parser.add_argument('--ffmpeg', default=os.environ.get('FRAMERUDIMENTS_FFMPEG') or shutil.which('ffmpeg'))
    parser.add_argument('--ffprobe', default=os.environ.get('FRAMERUDIMENTS_FFPROBE') or shutil.which('ffprobe'))
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe or not args.font or not Path(args.font).is_file():
        parser.error('An installed font, FFmpeg and ffprobe are required; this command never downloads them')
    report = qualify(args.output, font=args.font, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
        'native_comparisons': sum(case['independent_native_comparisons'] + case['nonsequential_native_comparisons']
            + case['checkpoint_restore']['native_comparisons'] + case['quality_switch']['native_comparisons']
            + case['old_seek_reconstruction']['native_comparisons'] for case in report['cases']),
        'independent_native_comparisons': sum(case['independent_native_comparisons'] for case in report['cases']),
        'nonsequential_native_comparisons': sum(case['nonsequential_native_comparisons'] for case in report['cases']),
        'checkpoint_native_comparisons': sum(case['checkpoint_restore']['native_comparisons'] for case in report['cases']),
        'decoded_comparisons': sum(case.get('cli', {}).get('decoded_comparisons', 0) for case in report['cases']),
        'seconds': report['seconds'], 'owned_temporary_tree_removed': report['owned_temporary_tree_removed']}))


if __name__ == '__main__':
    main()
