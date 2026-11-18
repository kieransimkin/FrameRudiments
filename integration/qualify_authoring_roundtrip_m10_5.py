"""Qualify shared authoring history/export through native reopen and the CLI.

Only the checked-in synthetic JSON fixture is used. The real TypeScript model,
history, export writer and native HTTP validator produce ZIP bytes. Each bundle
is bounded, checked, extracted beside unchanged relative source pins and reopened
with the native session parser. Independent literal expectations drive separate
native projects and CLI encodes. External visualiser routing is retained and
validated without starting sequential engines. This is not browser/device or
perceptual approval.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from hashlib import sha256
from http.server import ThreadingHTTPServer
from importlib.metadata import version
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from threading import Thread
from time import monotonic
import wave
import zipfile

from PIL import Image, ImageDraw
from dancerudiments_authoring.recording import Recording, RecordingClock, write_recording

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from authoring_preview import AuthoringHandler, check_draft, session  # noqa: E402
from framerudiments import Project  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments.graph_authoring import validate_authoring_graph  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402

FIXTURE = ROOT / 'integration/fixtures/authoring_roundtrip_m10_5.json'
MAX_ENTRY_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * MAX_ENTRY_BYTES
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 65536
ENTRY_PATTERN = re.compile(r'(?:AUTHORING-README\.txt|project\.json|authoring-(?:lines|words|cues)(?:-(?:[2-9]|[1-9][0-9]+))?\.lrc)\Z')
SIDECAR_FIELDS = {'lines': 'lyrics', 'words': 'word_lyrics', 'cues': 'cues'}


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def safe_extract_archive(encoded, destination, *, expected_names, protected=()):
    """Validate the whole bounded STORE archive before writing any entry.

    The qualification owns the destination and permits only the production
    export's five possible root files. It never calls ZipFile.extract/all, follows
    links, overwrites a file, or accepts an entry overlapping an original source.
    This harness does not add a production project-import API.
    """
    if not isinstance(encoded, bytes) or not 1 <= len(encoded) <= MAX_ARCHIVE_BYTES:
        raise ValueError('archive_size_limit')
    destination = Path(destination)
    if not destination.is_dir() or destination.is_symlink():
        raise ValueError('archive_destination_must_be_owned_directory')
    protected = {str(name).replace('\\', '/').casefold() for name in protected}
    payload = {}
    with zipfile.ZipFile(io.BytesIO(encoded)) as bundle:
        entries = bundle.infolist()
        if not 2 <= len(entries) <= 5:
            raise ValueError('archive_entry_count')
        names = [entry.filename for entry in entries]
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError('archive_duplicate_entry')
        if set(names) != set(expected_names):
            raise ValueError('archive_unexpected_entries')
        if sum(entry.file_size for entry in entries) > MAX_TOTAL_BYTES:
            raise ValueError('archive_total_size_limit')
        for entry in entries:
            mode = (entry.external_attr >> 16) & 0xffff
            if (not ENTRY_PATTERN.fullmatch(entry.filename) or entry.is_dir()
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or entry.compress_type != zipfile.ZIP_STORED or entry.flag_bits & 1
                    or not 0 <= entry.file_size <= MAX_ENTRY_BYTES
                    or entry.compress_size != entry.file_size):
                raise ValueError('archive_unsafe_entry')
            target = destination / entry.filename
            if entry.filename.casefold() in protected or target.exists() or target.is_symlink():
                raise ValueError('archive_would_overwrite_input')
            with bundle.open(entry) as stream:
                content = stream.read(MAX_ENTRY_BYTES + 1)
            if len(content) != entry.file_size:
                raise ValueError('archive_entry_size_mismatch')
            payload[entry.filename] = content
    # All metadata, lengths and CRCs have passed before the first write.
    for name, content in payload.items():
        with (destination / name).open('xb') as handle:
            handle.write(content)
    return {'archive_sha256': sha256(encoded).hexdigest(), 'bytes': len(encoded),
            'entries': [{'name': name, 'bytes': len(content), 'sha256': sha256(content).hexdigest()}
                        for name, content in payload.items()]}


def _change(value, changes):
    """Apply literal independent expected values, never an editor operation."""
    result = deepcopy(value)
    for change in changes:
        cursor = result
        for component in change['path'][:-1]:
            cursor = cursor[component]
        component = change['path'][-1]
        if isinstance(cursor, list) and component == len(cursor):
            cursor.append(deepcopy(change['value']))
        else:
            cursor[component] = deepcopy(change['value'])
    return result


def _resolve_pins(value, directory):
    if isinstance(value, dict):
        if set(value) == {'$pin'}:
            path = value['$pin']
            return {'path': path, 'sha256': digest(directory / path)}
        return {key: _resolve_pins(item, directory) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_pins(item, directory) for item in value]
    return value


def create_sources(directory, fixture, font):
    """Generate source bytes once; every later stage only reads/copies them."""
    directory.mkdir(parents=True, exist_ok=False)
    size = fixture['project']['width'], fixture['project']['height']
    for parent in ('art', 'fonts', 'motion', 'captions', 'structure'):
        (directory / parent).mkdir()
    with wave.open(str(directory / 'source.wav'), 'wb') as handle:
        handle.setparams((1, 2, 48000, 96000, 'NONE', 'not compressed'))
        handle.writeframes(b''.join(struct.pack('<h', round(2200 * math.sin(2 * math.pi * 220 * n / 48000)))
                                  for n in range(96000)))
    for name in ('red', 'blue', 'mask'):
        image = Image.new('RGBA', size)
        draw = ImageDraw.Draw(image)
        if name == 'red':
            draw.ellipse((18, 10, 141, 91), fill=(246, 72, 37, 220))
            draw.rectangle((75, 29, 173, 76), fill=(255, 193, 35, 180))
        elif name == 'blue':
            draw.polygon(((20, 87), (98, 7), (174, 90)), fill=(38, 165, 245, 210))
            draw.ellipse((112, 14, 171, 69), fill=(133, 69, 233, 230))
        else:
            for x in range(size[0]):
                draw.line((x, 0, x, size[1] - 1), fill=(255, 255, 255, round(255 * x / (size[0] - 1))))
        image.save(directory / 'art' / f'{name}.png')
    shutil.copyfile(font, directory / 'fonts/font.ttf')
    recording = Recording('m10_5_saved_signal', ('intensity', 'accent'),
        tuple((2 * index / 255 - 1, 1 - 2 * index / 255) for index in range(256)),
        RecordingClock(90, 'synthetic capture independent of project tempo', 'confirmed', 0, 'supplied', 0))
    write_recording(directory / 'motion/recording.json', recording)
    for name, text in fixture['sidecar_files'].items():
        (directory / name).write_text(text, encoding='utf-8', newline='\n')
    return {path.relative_to(directory).as_posix(): digest(path)
            for path in sorted(directory.rglob('*')) if path.is_file()}


def copy_sources(source, destination, pins):
    destination.mkdir(parents=True, exist_ok=False)
    for name, checksum in pins.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        if digest(target) != checksum:
            raise RuntimeError('fixture_copy_hash_mismatch')


def verify_sources(directory, pins):
    if any(digest(directory / name) != checksum for name, checksum in pins.items()):
        raise RuntimeError('source_inputs_changed')


def _write_project(path, project):
    path.write_text(json.dumps(project, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _session_snapshot(path):
    current = session(path)
    return {'project': current['project'],
            'sidecars': {key: current[key] for key in ('lines', 'words', 'cues', 'cueFormat')},
            'dirty': dict.fromkeys(SIDECAR_FIELDS, False)}


def expected_states(case, initial):
    states = {'initial': deepcopy(initial)}
    for name, definition in case['expected_states'].items():
        states[name] = _change(states[definition['from']], definition['changes'])
    return states


def _lrc(rows):
    """Independent UTF-8 LRC oracle from literal source-time expected rows."""
    lines = []
    for row in sorted(rows, key=lambda row: (row['start'], row['id'])):
        millis = int((Decimal(str(row['start'])) * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        minutes, rest = divmod(millis, 60000)
        seconds, millis = divmod(rest, 1000)
        lines.append(f'[{minutes:02}:{seconds:02}.{millis:03}]' + row['label'].strip())
    return ('\n'.join(lines) + '\n').encode('utf-8')


def expected_export(snapshot, export_paths):
    project, sidecars = deepcopy(snapshot['project']), {}
    for kind, field in SIDECAR_FIELDS.items():
        if snapshot['dirty'][kind]:
            name = export_paths[kind]
            content = _lrc(snapshot['sidecars'][kind])
            sidecars[name] = content
            project[field] = {'path': name, 'sha256': sha256(content).hexdigest()}
    return project, sidecars


@contextmanager
def native_server(project_file, temp_parent):
    class Handler(AuthoringHandler):
        def log_message(self, *_args):
            pass

    Handler.project_file = project_file
    Handler.session_data = {'project': Project.load(project_file).data}
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.graph_temp_parent = temp_parent
    worker = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        if worker.is_alive():
            raise RuntimeError('qualification_server_cleanup_failed')
        if temp_parent.exists() and list(temp_parent.iterdir()):
            raise RuntimeError('native_proposal_temporary_tree_leaked')


def run_authoring(project_file, snapshot, operations, name, temp_parent, *, reopen=False):
    with native_server(project_file, temp_parent) as origin:
        payload = {'origin': origin, 'cases': [{'name': name, 'project': snapshot['project'],
            'sidecars': snapshot['sidecars'], 'operations': operations, 'reopen': reopen}]}
        result = subprocess.run(['node', '--experimental-strip-types',
            str(ROOT / 'authoring/integration/authoringRoundtrip.ts')], input=json.dumps(payload),
            capture_output=True, text=True, cwd=ROOT, timeout=180, check=False)
        if result.returncode:
            raise RuntimeError('native_authoring_runner_failed: ' + result.stderr[-6000:])
        received = json.loads(result.stdout)
        if (received.get('schema') != 'framerudiments.authoring-roundtrip-export.v1'
                or [row['name'] for row in received['cases']] != [name]):
            raise RuntimeError('unexpected_authoring_runner_contract')
        return received


def native_frames(project_file, frames):
    project = Project.load(project_file)
    validation = validate_authoring_graph(project)
    if validation['valid'] is not True:
        raise RuntimeError('exported_native_project_invalid')
    compositor = Compositor(project)
    images, clocks, controls = [], [], []
    try:
        for frame in frames:
            image, context = compositor.frame(frame)
            if image.mode != 'RGB' or image.size != (project.width, project.height):
                raise RuntimeError('unexpected_native_pixel_format')
            images.append(image.copy())
            clocks.append({'frame': context.frame, 'source_seconds': context.source_seconds,
                           'beat': context.beat, 'source_beat': context.source_beat})
            controls.append([{'node': node, 'property': prop, 'value': value}
                for (node, prop), value in sorted(compositor.effect_graph_controls.values(
                    context, compositor.effect_graph).items())])
        if images[frames.index(5)].tobytes() != images[-1].tobytes() and frames[-1] == 5:
            raise RuntimeError('native_random_access_mismatch')
        project.verify_inputs()
    finally:
        compositor.close()
    return images, clocks, controls


def expected_clocks(project, frames):
    audio, bpm = project['audio'], project['clock']['bpm']
    start = Fraction(audio['start_sample'], audio['sample_rate'])
    span = Fraction(audio['end_sample'] - audio['start_sample'], audio['sample_rate'])
    origin = Fraction(str(project['clock'].get('origin', 0)))
    result = []
    for frame in frames:
        output = Fraction(frame, project['fps'])
        cycle = int(output // span)
        source = start + output % span
        source_beat = (source - origin) * Fraction(str(bpm)) / 60
        result.append({'frame': frame, 'source_seconds': float(source),
                       'source_beat': float(source_beat),
                       'beat': float(source_beat + cycle * span * Fraction(str(bpm)) / 60)})
    return result


def _assert_pixels(left, right, label):
    if len(left) != len(right) or any(a.tobytes() != b.tobytes() for a, b in zip(left, right)):
        raise RuntimeError('independent_native_pixels_mismatch:' + label)


def clock_formula_error(actual, expected):
    if len(actual) != len(expected) or any(a['frame'] != b['frame'] for a, b in zip(actual, expected)):
        raise RuntimeError('independent_frame_clock_mismatch')
    error = max(abs(a[key] - b[key]) for a, b in zip(actual, expected)
                for key in ('source_seconds', 'source_beat', 'beat'))
    # The independent Fraction oracle rounds once; the native float clock also
    # adds an integer repeat phase. Permit only its sub-picosecond round-off.
    if error > 1e-12:
        raise RuntimeError('independent_source_clock_mismatch')
    return error


def _cli(command, project_file, output, ffmpeg, ffprobe):
    temporary_preflight(output.parent)
    arguments = [sys.executable, '-m', 'framerudiments', command, str(project_file), '--output', str(output)]
    if command != 'plan':
        arguments += ['--ffmpeg', ffmpeg, '--ffprobe', ffprobe]
    if command == 'render':
        arguments += ['--single-pass', '--preset', 'ultrafast', '--crf', '0', '--min-free-gib', '0']
    child_env = dict(os.environ)
    child_env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + child_env.get('PYTHONPATH', '')
    child_env.pop('FRAMERUDIMENTS_WORK_CACHE', None)
    started = monotonic()
    result = subprocess.run(arguments, cwd=ROOT, env=child_env, capture_output=True, text=True,
                            timeout=180, check=False)
    if result.returncode:
        raise RuntimeError('native_cli_' + command + '_failed: ' + result.stderr[-6000:])
    document = json.loads((output if command == 'plan' else output / 'manifest.json').read_text(encoding='utf-8'))
    return document, {'command': command, 'returncode': 0, 'seconds': round(monotonic() - started, 3),
                      'frames': document['frames'], 'state': document['state']}


def _decode_video(ffmpeg, video, frame_count, size):
    result = subprocess.run([ffmpeg, '-v', 'error', '-threads', '1', '-i', str(video),
        '-map', '0:v:0', '-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', '-'],
        capture_output=True, timeout=60, check=True)
    frame_bytes = size[0] * size[1] * 3
    if len(result.stdout) != frame_count * frame_bytes:
        raise RuntimeError('encoded_frame_count_mismatch')
    return [result.stdout[index:index + frame_bytes] for index in range(0, len(result.stdout), frame_bytes)]


def qualify_cli(actual, reference, reopened, directory, output, ffmpeg, ffprobe):
    """Run actual public CLI commands and compare independently encoded videos."""
    directory.mkdir()
    plan, plan_receipt = _cli('plan', actual, directory / 'plan.json', ffmpeg, ffprobe)
    preflight, preflight_receipt = _cli('preflight', actual, directory / 'preflight', ffmpeg, ffprobe)
    if (preflight['preflight_scope']['complete'] is not True
            or preflight['preflight_scope']['frames_rendered'] != plan['frames']):
        raise RuntimeError('cli_full_preflight_missing')
    movies, receipts = {}, [plan_receipt, preflight_receipt]
    for label, project_file in (('exported', actual), ('reference', reference), ('reexported', reopened)):
        target = directory / label
        document, receipt = _cli('render', project_file, target, ffmpeg, ffprobe)
        health = json.loads((target / 'candidate-health.json').read_text(encoding='utf-8'))
        if (document['verification']['full_decode'] != 'passed'
                or document['frames'] != plan['frames'] or health['technical_qa'] != 'passed'
                or document['verification'].get('listening_approved') is not False):
            raise RuntimeError('cli_render_technical_qa_missing')
        receipts.append({'input': label, **receipt})
        movies[label] = _decode_video(ffmpeg, target / 'video.mp4', plan['frames'], tuple(plan['dimensions']))
    if movies['exported'] != movies['reference'] or movies['reexported'] != movies['reference']:
        raise RuntimeError('independent_cli_encoded_pixels_mismatch')
    # The authored raster and encoded raster have different colour formats;
    # compare equivalent native paths and equivalent encodes separately.
    frames = list(range(plan['frames']))
    actual_frames, actual_clock, _ = native_frames(actual, frames)
    reference_frames, reference_clock, _ = native_frames(reference, frames)
    _assert_pixels(actual_frames, reference_frames, 'cli-full-programme')
    if actual_clock != reference_clock:
        raise RuntimeError('cli_full_programme_clock_mismatch')
    movie = output / 'authoring-roundtrip.mp4'
    shutil.copyfile(directory / 'exported/video.mp4', movie)
    return {'commands': receipts, 'full_preflight': preflight['preflight_scope'],
            'native_programme_frames': len(frames), 'native_max_rgb8_error': 0,
            'encoded_frames_per_video': plan['frames'], 'encoded_videos': len(movies),
            'decoded_comparisons': plan['frames'] * 2, 'decoded_max_rgb8_error': 0,
            'encoded_reference': 'separate public CLI render of the independent literal expected project',
            'pixel_hashes': [sha256(frame).hexdigest() for frame in movies['reference']],
            'video_sha256': digest(movie), 'source_start_sample': plan['source_start_sample'],
            'source_end_sample_exclusive': plan['source_end_sample_exclusive'],
            'sample_rate': plan['sample_rate'], 'repeats': plan['repeats'],
            'perceptual_approval': False}


def qualify(output, *, font, ffmpeg, ffprobe):
    output = Path(output).resolve()
    temporary_preflight(output.parent)
    output.mkdir(exist_ok=False)
    temporary_preflight(output)
    node_version = subprocess.check_output(['node', '--version'], text=True).strip()
    if int(node_version.lstrip('v').split('.')[0]) < 24:
        raise RuntimeError('qualification_requires_node_24_or_newer')
    probe = ("import fs from 'node:fs'; const d=fs.mkdtempSync('probe-'); try {"
             "fs.writeFileSync(d+'/a','m105'); if(fs.readFileSync(d+'/a','utf8')!=='m105')throw Error('readback');"
             "fs.renameSync(d+'/a',d+'/b'); fs.unlinkSync(d+'/b');} finally {fs.rmdirSync(d);}")
    subprocess.run(['node', '--input-type=module', '-e', probe], cwd=output, check=True)
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    if fixture['schema'] != 'framerudiments.authoring-roundtrip-fixture.v1':
        raise RuntimeError('unexpected_fixture_schema')
    frames = fixture['frames']
    size = fixture['project']['width'], fixture['project']['height']
    row_height = size[1] + 30
    checkpoint_count = sum(len(case['checkpoints']) for case in fixture['cases'])
    gallery = Image.new('RGB', (size[0] * 3, row_height * checkpoint_count), '#121b2d')
    draw = ImageDraw.Draw(gallery)
    report = {'schema': 'framerudiments.authoring-roundtrip-qualification.v1', 'passed': False,
        'fixture_sha256': digest(FIXTURE),
        'runtime': {'python': platform.python_version(), 'node': node_version,
                    'dancerudiments': version('dancerudiments'), 'pillow': version('Pillow'),
                    'ffmpeg': subprocess.check_output([ffmpeg, '-version'], text=True).splitlines()[0]},
        'implementation_sha256': {name: digest(ROOT / name) for name in (
            'authoring/src/authoringHistory.ts', 'authoring/src/authoringExport.ts', 'authoring/src/archive.ts',
            'authoring/src/model.ts', 'authoring/integration/authoringRoundtrip.ts',
            'integration/qualify_authoring_roundtrip_m10_5.py')},
        'scope': {'actual_shared_typescript_model_history_export': True, 'actual_same_origin_http': True,
            'actual_export_zip': True, 'bounded_safe_extraction': True, 'native_project_and_session_reopen': True,
            'independent_native_compositor': True, 'named_and_recorded_native_motion': True,
            'native_cli_plan_preflight_render': True, 'actual_encoded_frame_comparison': True,
            'routing_and_stage_injection': 'retained configuration and native compositor admission only',
            'sequential_external_engines': False, 'browser_interaction': False, 'gpu': False,
            'physical_device': False, 'perceptual_approval': False},
        'frames': frames, 'cases': [], 'http_requests': 0}
    started = monotonic()
    with tempfile.TemporaryDirectory(prefix='m10-5-roundtrip-', dir=output) as owned:
        directory = Path(owned)
        sources = directory / 'sources'
        pins = create_sources(sources, fixture, font)
        report['source_sha256'] = pins
        base = _resolve_pins(fixture['project'], sources)
        gallery_row = 0
        for case in fixture['cases']:
            name = case['name']
            project = _resolve_pins(_change(base, case['project_changes']), sources)
            original = directory / name / 'original'
            copy_sources(sources, original, pins)
            original_file = original / 'project.json'
            _write_project(original_file, project)
            original_hash = digest(original_file)
            initial = _session_snapshot(original_file)
            states = expected_states(case, initial)
            received = run_authoring(original_file, initial, case['operations'], name,
                                     directory / name / 'native-http')
            report['http_requests'] += received['requests']
            exported_case = received['cases'][0]
            if [row['name'] for row in exported_case['checkpoints']] != [row['name'] for row in case['checkpoints']]:
                raise RuntimeError('checkpoint_order_mismatch:' + name)
            case_report = {key: exported_case[key] for key in (
                'name', 'validations', 'native_edits', 'rejected_stale_responses', 'assertions', 'dispositions')}
            case_report['checkpoints'] = []
            native_by_checkpoint = {}
            final_paths = None
            for exported, checkpoint in zip(exported_case['checkpoints'], case['checkpoints']):
                label = checkpoint['name']
                expected = states[checkpoint['state']]
                if (exported['snapshot_project'] != expected['project']
                        or exported['sidecars'] != expected['sidecars'] or exported['dirty'] != expected['dirty']
                        or exported['past'] != checkpoint['past'] or exported['future'] != checkpoint['future']):
                    raise RuntimeError('independent_snapshot_mismatch:' + name + ':' + label)
                check_draft(project, expected['project'])
                expected_project, expected_sidecars = expected_export(expected, fixture['export_paths'])
                if exported['project'] != expected_project:
                    raise RuntimeError('independent_export_project_mismatch:' + name + ':' + label)
                actual_dir = directory / name / label / 'extracted'
                copy_sources(sources, actual_dir, pins)
                archive = base64.b64decode(exported['archive'], validate=True)
                archive_receipt = safe_extract_archive(archive, actual_dir,
                    expected_names=['AUTHORING-README.txt', 'project.json', *expected_sidecars], protected=pins)
                actual_file = actual_dir / 'project.json'
                if json.loads(actual_file.read_text(encoding='utf-8')) != expected_project:
                    raise RuntimeError('actual_zip_project_mismatch')
                for filename, content in expected_sidecars.items():
                    if (actual_dir / filename).read_bytes() != content:
                        raise RuntimeError('independent_sidecar_bytes_mismatch')
                reference_dir = directory / name / label / 'independent-reference'
                copy_sources(sources, reference_dir, pins)
                reference_file = reference_dir / 'project.json'
                _write_project(reference_file, expected_project)
                for filename, content in expected_sidecars.items():
                    (reference_dir / filename).write_bytes(content)
                actual_images, clocks, controls = native_frames(actual_file, frames)
                expected_images, reference_clocks, reference_controls = native_frames(reference_file, frames)
                _assert_pixels(actual_images, expected_images, name + ':' + label)
                if clocks != reference_clocks or controls != reference_controls:
                    raise RuntimeError('independent_native_clock_or_control_mismatch')
                clock_error = clock_formula_error(clocks, expected_clocks(project, frames))
                reopened_snapshot = _session_snapshot(actual_file)
                for kind in SIDECAR_FIELDS:
                    actual_timing = [(row['start'], row['label']) for row in reopened_snapshot['sidecars'][kind]]
                    expected_timing = [(row['start'], row['label']) for row in expected['sidecars'][kind]]
                    if actual_timing != expected_timing:
                        raise RuntimeError('reopened_native_timing_semantics_mismatch:' + kind)
                if reopened_snapshot['sidecars']['cueFormat'] != expected['sidecars']['cueFormat']:
                    raise RuntimeError('reopened_native_cue_format_mismatch')
                reopened = run_authoring(actual_file, reopened_snapshot, [], name + '-' + label,
                    directory / name / label / 'reopen-http', reopen=True)
                report['http_requests'] += reopened['requests']
                again = reopened['cases'][0]['checkpoints'][0]
                if (again['project'] != expected_project or any(again['dirty'].values())
                        or again['past'] != 0 or again['future'] != 0):
                    raise RuntimeError('reopened_semantic_identity_mismatch')
                reopened_dir = directory / name / label / 'reexported'
                reopened_pins = {**pins, **{key: sha256(value).hexdigest() for key, value in expected_sidecars.items()}}
                copy_sources(actual_dir, reopened_dir, reopened_pins)
                reopened_receipt = safe_extract_archive(base64.b64decode(again['archive'], validate=True), reopened_dir,
                    expected_names=['AUTHORING-README.txt', 'project.json'], protected=reopened_pins)
                reexported_file = reopened_dir / 'project.json'
                reopened_images, reopened_clock, reopened_controls = native_frames(reexported_file, frames)
                _assert_pixels(reopened_images, expected_images, name + ':' + label + ':reopened')
                if clocks != reopened_clock or controls != reopened_controls:
                    raise RuntimeError('reopened_clock_or_control_mismatch')
                for tree in (actual_dir, reference_dir, reopened_dir):
                    verify_sources(tree, pins)
                native_by_checkpoint[label] = (actual_images, controls)
                y = gallery_row * row_height
                draw.text((5, y + 3), name + ' / ' + label + ' | exact native RGB8 reference match', fill='white')
                draw.text((5, y + 16), 'Frames 2, 5, 17 | original source window 0.25-1.25 s, repeated twice', fill='#b5c7e3')
                for column, frame in enumerate((2, 5, 17)):
                    gallery.paste(actual_images[frames.index(frame)], (column * size[0], y + 30))
                gallery_row += 1
                case_report['checkpoints'].append({'name': label, 'state': checkpoint['state'],
                    'past': exported['past'], 'future': exported['future'], 'dirty': exported['dirty'],
                    'cue_format': exported['sidecars']['cueFormat'], 'archive': archive_receipt,
                    'reexport_archive': reopened_receipt, 'native_validation': True,
                    'semantic_reopen_identity': True, 'native_reopened_timing_semantics': True,
                    'original_source_bytes_unchanged': True,
                    'native_compared_frames': len(frames) * 2, 'max_rgb8_error': 0,
                    'frame_sha256': [sha256(image.tobytes()).hexdigest() for image in actual_images],
                    'clocks': clocks, 'native_control_values': controls,
                    'rational_clock_formula_max_abs_error': clock_error,
                    'nonsequential_repeated_frame_identical': True})
                if label == 'full_redo':
                    final_paths = actual_file, reference_file, reexported_file
            initial_images, initial_controls = native_by_checkpoint['initial']
            if (initial_images[frames.index(5)].tobytes() != initial_images[frames.index(17)].tobytes()
                    or initial_controls[frames.index(5)] != initial_controls[frames.index(17)]):
                raise RuntimeError('named_and_recorded_source_clock_repeat_mismatch')
            case_report['named_and_recorded_source_clocks_repeat'] = True
            if case['cli']:
                native_images, native_controls = native_by_checkpoint['native']
                if native_controls[frames.index(5)] == native_controls[frames.index(17)]:
                    raise RuntimeError('continuous_native_control_did_not_advance')
                for changed in ('graph', 'native', 'sidecars', 'keyframes', 'branch'):
                    actual_images = native_by_checkpoint[changed][0]
                    if all(a.tobytes() == b.tobytes() for a, b in zip(actual_images, initial_images)):
                        raise RuntimeError('fixture_checkpoint_has_no_observable_edit:' + changed)
                case_report['continuous_native_control_advances_across_repeat'] = True
                case_report['cli'] = qualify_cli(*final_paths, directory / name / 'cli', output, ffmpeg, ffprobe)
            verify_sources(original, pins)
            if digest(original_file) != original_hash:
                raise RuntimeError('original_project_file_changed')
            report['cases'].append(case_report)
        verify_sources(sources, pins)
        png = output / 'authoring-roundtrip-contact-sheet.png'
        gallery.save(png)
        report['contact_sheet_sha256'] = digest(png)
    report['owned_temporary_tree_removed'] = not directory.exists()
    report['native_http_temporary_trees_removed'] = True
    report['original_projects_and_sources_unchanged'] = True
    report['seconds'] = round(monotonic() - started, 3)
    report['passed'] = report['owned_temporary_tree_removed']
    (output / 'authoring-roundtrip-qualification.json').write_text(
        json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New qualification evidence directory')
    parser.add_argument('--font', default=os.environ.get('FRAMERUDIMENTS_TEST_FONT'), required=not os.environ.get('FRAMERUDIMENTS_TEST_FONT'))
    parser.add_argument('--ffmpeg', default=os.environ.get('FRAMERUDIMENTS_FFMPEG') or shutil.which('ffmpeg'))
    parser.add_argument('--ffprobe', default=shutil.which('ffprobe'))
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe or not Path(args.font).is_file():
        parser.error('An installed font, FFmpeg and ffprobe are required; this command never downloads them')
    report = qualify(args.output, font=args.font, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    checkpoints = [row for case in report['cases'] for row in case['checkpoints']]
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
        'checkpoints': len(checkpoints), 'native_compared_frames': sum(row['native_compared_frames'] for row in checkpoints),
        'http_requests': report['http_requests'], 'seconds': report['seconds'],
        'owned_temporary_tree_removed': report['owned_temporary_tree_removed']}))


if __name__ == '__main__':
    main()
