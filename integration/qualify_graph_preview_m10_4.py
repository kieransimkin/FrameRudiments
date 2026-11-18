"""Qualify real HTTP/TypeScript transition previews against native references.

Synthetic pinned sources exercise native clocks, motion, quality, temporal
history and chain projections. PNG decoding and every RGB8 comparison are real.
The Node presentation hook verifies containers; browser paint is a separate gate.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
from http.server import ThreadingHTTPServer
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from threading import Thread
import time

from PIL import Image, ImageDraw
from dancerudiments_authoring.recording import Recording, RecordingClock, write_recording

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from authoring_preview import AuthoringHandler, check_draft  # noqa: E402
from framerudiments import Project  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments.effect_graph import EffectGraph  # noqa: E402
from framerudiments.graph_authoring import validate_authoring_graph  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from qualify_graph_authoring_m10_2 import fixture, SIZE, FRAMES  # noqa: E402


def fixtures(directory):
    base, _ = fixture(directory)
    base['title'] = 'M10.4 synthetic native transition preview qualification'
    base['render_quality'] = {'enabled': True, 'spatial_scale': 2, 'temporal_samples': 3,
        'antialias': .7, 'antialias_radius': .75, 'antialias_threshold': .04, 'antialias_passes': 2}
    cues = directory / 'cues.lrc'
    cues.write_text('[00:00.000][SECTION: HIDE]\n', encoding='utf-8')
    base['cues'] = {'path': cues.name, 'sha256': sha256(cues.read_bytes()).hexdigest()}
    captured = directory / 'recording.json'
    write_recording(captured, Recording('m10_4_capture', ('intensity',),
        tuple((2 * index / 255 - 1,) for index in range(256)),
        RecordingClock(90, 'synthetic recording independent of project tempo', 'confirmed', 0, 'supplied', 0)))
    recording = {'kind': 'recording', 'file': {'path': captured.name,
                                             'sha256': sha256(captured.read_bytes()).hexdigest()}}
    base['stem_sources'] = {'saved-intensity': recording}
    base['effect_graph'] = {'nodes': [{'id': 'frame', 'type': 'source'}], 'output': 'frame'}
    sources = [{'id': name, 'type': 'source'} for name in
               ('frame', 'background', 'layer:red', 'layer:blue', 'layer:mask')]
    requests = [{'frame': frame, 'quality': quality} for quality in ('fast', 'full') for frame in FRAMES]
    cases = []

    def add(name, transition, *, controls=None, sidecars=None, post_effects=None):
        project = deepcopy(base)
        node = {'id': 'mix', 'type': 'transition', 'inputs': ['layer:red', 'layer:blue'], **transition}
        # The authored output differs from the selected preview output. This
        # catches a projection lost inside the temporal supersampling clone.
        project['effect_graph'] = {'nodes': deepcopy(sources) + [node,
            {'id': 'authored-tail', 'type': 'opacity', 'inputs': ['mix'], 'opacity': .2}],
            'output': 'authored-tail'}
        if controls:
            project['effect_graph_controls'] = deepcopy(controls)
        if post_effects:
            project['post_effects'] = deepcopy(post_effects)
        entry = {'name': name, 'project': project, 'node': 'mix',
                 'sidecars': sidecars or {}, 'requests': deepcopy(requests),
                 'reference_targets': ['layer:red', 'mix', 'layer:blue']}
        cases.append(entry)
        return entry

    add('dissolve_start', {'progress': 0})
    add('dissolve_end', {'progress': 1})
    add('timed_continuous', {'transition_style': 'wipe', 'direction': 'right', 'feather': .08,
                            'timing': {'duration_beats': 1.5, 'clock': 'continuous'}})
    add('timed_source', {'transition_style': 'wipe', 'direction': 'right', 'feather': .08,
                        'timing': {'duration_beats': 1.5, 'clock': 'source'}})
    add('matte', {'transition_style': 'matte', 'inputs': ['layer:red', 'layer:blue', 'layer:mask'],
                  'progress': .45, 'feather': .1})
    add('geometry_zoom', {'transition_style': 'zoom', 'progress': .45})
    add('spectral', {'transition_style': 'spectral', 'progress': .45, 'bands': [
        {'id': 'warm', 'source_color': '#f64825', 'to_color': '#26a5f5',
         'space': 'rgb', 'tolerance': .3, 'softness': .1, 'start': 0, 'end': .7,
         'offset_from': [4, 0], 'offset_to': [-3, 1]}]})
    add('native_named', {'transition_style': 'radial', 'progress': .3}, controls=[
        {'id': 'native-circle', 'node': 'mix', 'property': 'progress',
         'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [.1, .9]}])
    add('native_recorded', {'progress': .3}, controls=[
        {'id': 'native-recording', 'node': 'mix', 'property': 'progress',
         'motion': recording, 'axis': 'intensity', 'range': [.1, .9]}])
    add('temporal_history', {'transition_style': 'wipe', 'feather': .05,
                            'timing': {'duration_beats': 1.5}}, post_effects={
        'temporal': {'strength': .7, 'shutter_frames': 3, 'samples': 4}})
    edited = add('edited_cues', {'progress': .4},
                 sidecars={'cues': [{'start': .25, 'label': '[SECTION: SHOW]'}]})
    edited['project']['layers'][0]['cue_labels'] = ['[SECTION: SHOW]']
    edited['reference_cue_text'] = '[00:00.250][SECTION: SHOW]\n'
    chain = {'id': 'chain', 'type': 'transition_chain',
             'inputs': ['layer:red', 'layer:blue', 'background'],
             'stages': [{'id': 'first', 'transition_style': 'wipe', 'timing': {'duration_beats': 1.5}},
                        {'id': 'second', 'transition_style': 'radial', 'progress': .35}],
             'input_effects': [[{'id': 'dim', 'type': 'opacity', 'opacity': .55}], [], []]}
    for stage in ('first', 'second'):
        project = deepcopy(base)
        project['effect_graph'] = {'nodes': deepcopy(sources) + [deepcopy(chain),
            {'id': 'authored-tail', 'type': 'opacity', 'inputs': ['chain'], 'opacity': .2}],
            'output': 'authored-tail'}
        address = EffectGraph(project['effect_graph']).chain_expansions['chain']['stages'][stage]
        reference = deepcopy(project)
        # Independent flat graph written from the authored fixture semantics;
        # do not reuse the adapter's output views or expansion projection.
        reference['effect_graph'] = {'nodes': deepcopy(sources) + [
            {'id': 'dim', 'type': 'opacity', 'inputs': ['layer:red'], 'opacity': .55},
            {'id': 'first', 'type': 'transition', 'inputs': ['dim', 'layer:blue'],
             'transition_style': 'wipe', 'timing': {'duration_beats': 1.5}},
            {'id': 'second', 'type': 'transition', 'inputs': ['first', 'background'],
             'transition_style': 'radial', 'progress': .35}], 'output': stage}
        cases.append({'name': 'chain_'+stage, 'project': project, 'node': address,
                      'sidecars': {}, 'requests': deepcopy(requests), 'reference': reference,
                      'reference_targets': ['dim', 'first', 'layer:blue'] if stage == 'first'
                      else ['first', 'second', 'background']})
    return base, cases


def reference_images(entry, directory, frame, quality):
    """Render independent native output projects, without the preview adapter."""
    document = deepcopy(entry.get('reference', entry['project']))
    if entry.get('reference_cue_text'):
        path = directory / 'reference-cues.lrc'
        path.write_text(entry['reference_cue_text'], encoding='utf-8')
        document['cues'] = {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    if quality == 'fast':
        document['render_quality'] = {'enabled': False}
    result = []
    for target in entry['reference_targets']:
        native = deepcopy(document)
        native['effect_graph']['output'] = target
        project = Project(native, directory)
        compositor = Compositor(project)
        try:
            image, context = compositor.frame(frame, apply_quality=quality == 'full')
            project.verify_inputs()
            assert image.mode == 'RGB' and image.size == SIZE
            clock = {'frame': context.frame, 'seconds': context.output_seconds,
                     'source_seconds': context.source_seconds, 'beat': context.beat,
                     'source_beat': context.source_beat}
            result.append((image.copy(), clock))
        finally:
            compositor.close()
    return result


def _decode(row):
    encoded = base64.b64decode(row['png_base64'], validate=True)
    if sha256(encoded).hexdigest() != row['sha256']:
        raise RuntimeError('preview_png_hash_mismatch')
    with Image.open(BytesIO(encoded)) as image:
        image.load()
        if image.mode != 'RGB' or image.size != SIZE:
            raise RuntimeError('preview_png_format_mismatch')
        if sha256(image.tobytes()).hexdigest() != row['pixel_sha256']:
            raise RuntimeError('preview_pixel_hash_mismatch')
        return image.copy()


def qualify(output):
    output = Path(output).resolve()
    temporary_preflight(output.parent)
    output.mkdir(exist_ok=False)
    temporary_preflight(output)
    node_version = subprocess.check_output(['node', '--version'], text=True).strip()
    if int(node_version.lstrip('v').split('.')[0]) < 24:
        raise RuntimeError('qualification_requires_node_24_or_newer')
    probe = "import fs from 'node:fs'; const d=fs.mkdtempSync('probe-'); try {fs.writeFileSync(d+'/a','m104'); if(fs.readFileSync(d+'/a','utf8')!=='m104')throw Error('readback'); fs.renameSync(d+'/a',d+'/b'); fs.unlinkSync(d+'/b');} finally {fs.rmdirSync(d);}"
    subprocess.run(['node', '--input-type=module', '-e', probe], cwd=output, check=True)
    report = {'schema': 'framerudiments.authoring-preview-qualification.v1', 'passed': False,
              'runtime': {'python': platform.python_version(), 'node': node_version,
                          'dancerudiments': version('dancerudiments'), 'pillow': version('Pillow')},
              'scope': {'actual_same_origin_http': True, 'actual_typescript_controller': True,
                        'native_project_validation': True, 'native_compositor': True,
                        'named_and_recorded_native_motion': True, 'actual_png_decoding': 'Pillow',
                        'browser_decode_and_paint': False, 'gpu': False,
                        'perceptual_approval': False, 'encoded_video': False},
              'pixel_comparison': {'format': 'RGB8', 'maximum_channel_error': 0,
                                   'reference': 'independent native output projects'},
              'frames': list(FRAMES), 'qualities': ['fast', 'full'], 'cases': []}
    with tempfile.TemporaryDirectory(prefix='m10-4-fixture-', dir=output) as owned:
        directory = Path(owned)
        base, cases = fixtures(directory)
        project_file = directory / 'project.json'
        project_file.write_text(json.dumps(base), encoding='utf-8')
        pins = {item.name: sha256(item.read_bytes()).hexdigest() for item in directory.iterdir()}
        snapshots = deepcopy(cases)
        for entry in cases:
            check_draft(base, entry['project'])
            if not validate_authoring_graph(Project(entry['project'], directory))['valid']:
                raise RuntimeError('qualification_fixture_invalid')

        class Handler(AuthoringHandler):
            def log_message(self, *_args):
                pass

        Handler.project_file = project_file
        Handler.session_data = {'project': base}
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.graph_temp_parent = directory / 'owned-previews'
        worker = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        worker.start()
        started = time.monotonic()
        try:
            payload = {'origin': f'http://127.0.0.1:{server.server_port}',
                       'cases': [{key: value for key, value in row.items()
                                  if not key.startswith('reference')} for row in cases]}
            try:
                result = subprocess.run(['node', '--experimental-strip-types',
                                         str(ROOT / 'authoring/integration/graphPreviewRoundtrip.ts')],
                                        input=json.dumps(payload), capture_output=True, text=True,
                                        cwd=ROOT, timeout=180, check=True)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError('native_preview_route_failed: '+exc.stderr[-4000:]) from exc
            received = json.loads(result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
        if worker.is_alive():
            raise RuntimeError('qualification_server_cleanup_failed')
        report['http_and_typescript_seconds'] = round(time.monotonic()-started, 3)
        report['http_requests'] = received['requests']
        report['coalescing'] = received['coalescing']
        if (received['schema'] != 'framerudiments.authoring-transition-preview-export.v1'
                or [row['name'] for row in received['cases']] != [row['name'] for row in cases]
                or not received['coalescing']['only_latest_presented']
                or not received['coalescing']['owned_urls_removed']):
            raise RuntimeError('unexpected_preview_export_contract')
        panel_width, panel_height = SIZE[0]*6, SIZE[1]+38
        gallery = Image.new('RGB', (panel_width, panel_height*len(cases)), '#121b2d')
        draw = ImageDraw.Draw(gallery)
        quality_changes = []
        result_hashes = {}
        for index, (exported, entry) in enumerate(zip(received['cases'], cases)):
            if len(exported['previews']) != len(entry['requests']):
                raise RuntimeError('preview_request_count_mismatch')
            images_by_key, comparisons = {}, []
            for preview, request in zip(exported['previews'], entry['requests']):
                if (preview['frame'], preview['quality']) != (request['frame'], request['quality']):
                    raise RuntimeError('preview_identity_mismatch')
                receipt = preview['receipt']
                if receipt['frame'] != request['frame'] or receipt['node']['id'] != entry['node']:
                    raise RuntimeError('native_preview_identity_mismatch')
                if receipt['quality'] != request['quality'] or len(receipt['images']) != 3:
                    raise RuntimeError('native_preview_quality_mismatch')
                if receipt['scope']['pipeline'] != 'native_graph_before_sequential_effects':
                    raise RuntimeError('native_preview_scope_mismatch')
                expected = reference_images(entry, directory, request['frame'], request['quality'])
                actual = [_decode(row) for row in receipt['images']]
                for image, (reference, clock) in zip(actual, expected):
                    if image.tobytes() != reference.tobytes() or receipt['clock'] != clock:
                        raise RuntimeError('independent_native_pixel_or_clock_mismatch:'+entry['name'])
                if entry['name'] == 'dissolve_start' and actual[0].tobytes() != actual[1].tobytes():
                    raise RuntimeError('transition_start_did_not_match_from')
                if entry['name'] == 'dissolve_end' and actual[2].tobytes() != actual[1].tobytes():
                    raise RuntimeError('transition_end_did_not_match_to')
                key = (request['quality'], request['frame'])
                if key in images_by_key and any(a.tobytes() != b.tobytes()
                                               for a, b in zip(images_by_key[key], actual)):
                    raise RuntimeError('preview_nonsequential_repeat_mismatch')
                images_by_key[key] = actual
                comparisons.append({'frame': request['frame'], 'quality': request['quality'],
                    'clock': receipt['clock'], 'render_quality': receipt['render_quality'],
                    'images': [{key: row[key] for key in ('role', 'sha256', 'pixel_sha256')}
                               for row in receipt['images']], 'max_rgb8_error': 0})
            changed = any(images_by_key['fast', frame][1].tobytes() != images_by_key['full', frame][1].tobytes()
                          for frame in set(FRAMES))
            if changed:
                quality_changes.append(entry['name'])
            result_hashes[entry['name']] = {quality: {
                frame: sha256(images_by_key[quality, frame][1].tobytes()).hexdigest()
                for frame in set(FRAMES)} for quality in ('fast', 'full')}
            y = index*panel_height
            draw.text((5, y+3), entry['name']+' | frame 5 | native RGB8', fill='white')
            for column, label in enumerate(('Fast: From', 'Fast: Result', 'Fast: To',
                                           'Authored: From', 'Authored: Result', 'Authored: To')):
                draw.text((column*SIZE[0]+5, y+19), label, fill='#b5c7e3')
            for column, image in enumerate(images_by_key['fast', 5]+images_by_key['full', 5]):
                gallery.paste(image, (column*SIZE[0], y+38))
            report['cases'].append({'name': entry['name'], 'node': entry['node'],
                'requests': len(comparisons), 'compared_images': len(comparisons)*3,
                'native_validation': True, 'max_rgb8_error': 0, 'quality_changes_pixels': changed,
                'nonsequential_repeats_identical': True, 'comparisons': comparisons})
        for quality in ('fast', 'full'):
            if result_hashes['timed_source'][quality][5] != result_hashes['timed_source'][quality][17]:
                raise RuntimeError('source_clock_repeat_mismatch')
            if result_hashes['timed_continuous'][quality][5] == result_hashes['timed_continuous'][quality][17]:
                raise RuntimeError('continuous_clock_did_not_advance_across_repeat')
            if result_hashes['native_recorded'][quality][5] != result_hashes['native_recorded'][quality][17]:
                raise RuntimeError('recorded_source_clock_repeat_mismatch')
        if not quality_changes:
            raise RuntimeError('quality_switch_has_no_observable_fixture_effect')
        normal = received.get('normal', [])
        if len(normal) != 2:
            raise RuntimeError('normal_preview_exercise_missing')
        normal_receipt = []
        for row in normal:
            entry = next(entry for entry in cases if entry['name'] == row['name'])
            reference_entry = {**entry, 'reference_targets': [entry['project']['effect_graph']['output']]}
            reference, clock = reference_images(reference_entry, directory, row['frame'], row['quality'])[0]
            encoded = base64.b64decode(row['png_base64'], validate=True)
            with Image.open(BytesIO(encoded)) as image:
                image.load()
                if image.mode != 'RGB' or image.size != SIZE or image.tobytes() != reference.tobytes():
                    raise RuntimeError('normal_preview_pixel_mismatch')
            normal_receipt.append({'name': row['name'], 'frame': row['frame'], 'quality': row['quality'],
                'png_sha256': sha256(encoded).hexdigest(), 'pixel_sha256': sha256(reference.tobytes()).hexdigest(),
                'reference_clock': clock, 'max_rgb8_error': 0})
        report['normal_preview'] = normal_receipt
        report['quality_changes_pixels'] = quality_changes
        report['source_and_recorded_clocks_repeat'] = True
        report['continuous_clock_advances_across_repeat'] = True
        if cases != snapshots or any(sha256((directory/name).read_bytes()).hexdigest() != digest
                                     for name, digest in pins.items()):
            raise RuntimeError('authored_project_or_source_inputs_changed')
        if server.graph_temp_parent.exists() and list(server.graph_temp_parent.iterdir()):
            raise RuntimeError('preview_temporary_tree_leaked')
        report['source_inputs_unchanged'] = report['authored_projects_unchanged'] = True
        report['preview_temporary_trees_removed'] = report['http_server_closed'] = True
        png = output / 'graph-preview-contact-sheet.png'
        gallery.save(png)
        report['contact_sheet_sha256'] = sha256(png.read_bytes()).hexdigest()
    report['owned_temporary_tree_removed'] = not directory.exists()
    report['passed'] = report['owned_temporary_tree_removed']
    (output / 'graph-preview-qualification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New qualification output directory')
    args = parser.parse_args()
    report = qualify(args.output)
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
        'compared_images': sum(row['compared_images'] for row in report['cases']),
        'normal_preview_frames': len(report['normal_preview']), 'http_requests': report['http_requests'],
        'http_and_typescript_seconds': report['http_and_typescript_seconds'],
        'owned_temporary_tree_removed': report['owned_temporary_tree_removed']}))


if __name__ == '__main__':
    main()
