"""Exercise native parameter/control HTTP edits, actual TS export and native pixels.

Uses synthetic pinned sources and a native DanceRudiments recording. No browser,
GPU, inference, downloads or encoded-video claims. The compositor and motion
engine are real; expected configurations are independently authored below.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
from http.server import ThreadingHTTPServer
from importlib.metadata import version
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from threading import Thread
import time
import zipfile

from PIL import Image, ImageDraw
from dancerudiments_authoring.recording import Recording, RecordingClock, write_recording

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from authoring_preview import AuthoringHandler, check_draft  # noqa: E402
from framerudiments import Project  # noqa: E402
from framerudiments.graph_authoring import validate_authoring_graph  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from qualify_graph_authoring_m10_2 import fixture, render, SIZE, FRAMES  # noqa: E402


def fixtures(directory):
    base, _ = fixture(directory)
    base['title'] = 'M10.3 synthetic native parameter and control qualification'
    recording = Recording('m10_3_saved_signal', ('intensity', 'accent'),
        tuple((2 * index / 255 - 1, 1 - 2 * index / 255) for index in range(256)),
        RecordingClock(90, 'synthetic capture independent of project tempo', 'confirmed', 0, 'supplied', 0))
    recording_path = directory / 'recording.json'
    write_recording(recording_path, recording)
    recorded = {'kind': 'recording', 'file': {'path': recording_path.name,
                                            'sha256': sha256(recording_path.read_bytes()).hexdigest()}}
    base['stem_sources'] = {'saved-intensity': recorded}
    base['effect_graph'] = {'nodes': [{'id': 'frame', 'type': 'source'}], 'output': 'frame'}
    result = []

    def case(name, node, operations, expected_node=None, *, selected='effect', controls=None, expected_controls=None):
        project = deepcopy(base)
        sources = [{'id': f'layer:{key}', 'type': 'source'} for key in ('red', 'blue', 'mask')]
        project['effect_graph'] = {'nodes': sources + [deepcopy(node)], 'output': node['id']}
        if controls is not None:
            project['effect_graph_controls'] = deepcopy(controls)
        expected = deepcopy(project)
        if expected_node is not None:
            expected['effect_graph']['nodes'][-1] = deepcopy(expected_node)
        if expected_controls is not None:
            expected['effect_graph_controls'] = deepcopy(expected_controls)
        result.append({'name': name, 'project': project, 'node': selected,
                       'operations': operations, 'expected': expected})
        return result[-1]

    def parameter(prop, value):
        return {'kind': 'set_parameter', 'property': prop, 'value': value}

    def node(kind, **kwargs):
        return {'id': 'effect', 'type': kind, 'inputs': ['layer:red'], **kwargs}

    opacity = node('opacity', opacity=.8)
    case('opacity', opacity, [parameter('opacity', .35)], {**opacity, 'opacity': .35})
    copied = node('copy')
    case('copy_opacity', copied, [parameter('opacity', .45)], {**copied, 'opacity': .45})
    source = case('source_opacity', copied, [parameter('opacity', .4)], selected='layer:red')
    source['expected']['effect_graph']['nodes'][0]['opacity'] = .4
    matte = node('matte', inputs=['layer:red', 'layer:mask'])
    case('matte_invert', matte, [parameter('invert', True)], {**matte, 'invert': True})
    chroma = node('chroma_key', key_colors=['#f64825', '#ffc123'], tolerance=.05, softness=0)
    case('chroma_colour', chroma, [parameter('key_color.g', 255)],
         {**chroma, 'key_colors': ['#f6ff25', '#ffc123']})
    chromatic = node('chromatic_displacement', red_offset=[0, 0])
    case('chromatic_vector', chromatic, [parameter('red_offset.x', 9)], {**chromatic, 'red_offset': [9, 0]})
    band = {'id': 'warm', 'source_color': '#f64825', 'tolerance': .15, 'softness': .05,
            'target_color': '#20a0ff', 'recolor': 1, 'offset': [0, 0]}
    spectral = node('spectral_displacement', bands=[band], spectral_mode='replace')
    case('spectral_band', spectral, [parameter('bands.warm.offset.x', 14)],
         {**spectral, 'bands': [{**band, 'offset': [14, 0]}]})
    composite = node('composite', inputs=['layer:red', 'layer:blue'])
    case('composite_mode', composite, [parameter('mode', 'screen')], {**composite, 'mode': 'screen'})
    timed = node('transition', inputs=['layer:red', 'layer:blue'], transition_style='wipe',
                 timing={'duration_beats': 4}, progress=.2)
    case('transition_timing', timed, [parameter('timing.duration_beats', 1)],
         {**timed, 'timing': {'duration_beats': 1}})
    stops = [{'id': 'cold', 'position': 0, 'color': '#1234aa'},
             {'id': 'hot', 'position': 1, 'color': '#ffcc44'}]
    gradient = node('layer_style', style_type='gradient_overlay', gradient_stops=stops)
    case('gradient_stop', gradient, [parameter('gradient_stops.cold.color.r', 240)],
         {**gradient, 'gradient_stops': [{**stops[0], 'color': '#f034aa'}, stops[1]]})
    warp = node('spatial_warp', warp_type='wave', warp_amplitude=[3, 4])
    case('spatial_vector', warp, [parameter('warp_amplitude.x', 12)], {**warp, 'warp_amplitude': [12, 4]})
    raster = node('raster_effect', raster_type='pixelate', pixel_size=[3, 3])
    case('raster_integer', raster, [parameter('pixel_size.x', 12)], {**raster, 'pixel_size': [12, 3]})
    field = node('procedural_field', field_type='noise', field_seed=0)
    case('field_integer', field, [parameter('field_seed', 7)], {**field, 'field_seed': 7})
    filtered = node('filter', filter_type='blur', filter_radius=2)
    case('filter_subtype', filtered, [parameter('filter_type', 'bloom'), parameter('filter_threshold', .1)],
         {**filtered, 'filter_type': 'bloom', 'filter_threshold': .1})
    chain = {'id': 'chain', 'type': 'transition_chain', 'inputs': ['layer:red', 'layer:blue'],
             'stages': [{'id': 'reveal', 'progress': .2}],
             'input_effects': [[{'id': 'dim', 'type': 'opacity', 'opacity': .9}], []]}
    case('chain_stage', chain, [parameter('progress', .7)],
         {**chain, 'stages': [{'id': 'reveal', 'progress': .7}]}, selected='chain::stage::reveal')
    case('chain_input', chain, [parameter('opacity', .3)],
         {**chain, 'input_effects': [[{'id': 'dim', 'type': 'opacity', 'opacity': .3}], []]},
         selected='chain::input::0::dim')
    named = {'kind': 'named', 'pattern': 'circle'}

    def put(settings, *, prop='opacity', motion=named, index=None, motion_settings=None):
        row = {'kind': 'put_binding', 'index': index, 'property': prop,
               'motion': {key: motion[key] for key in ('kind', 'pattern') if key in motion}, 'settings': settings}
        if motion_settings is not None:
            row['motion_settings'] = motion_settings
        return row

    def binding(settings, *, prop='opacity', motion=named):
        return {'node': 'effect', 'property': prop, 'motion': deepcopy(motion), **settings}

    settings = {'id': 'native-circle', 'axis': 'x', 'range': [.15, .85], 'curve': 'ease-in',
                'lag_frames': 1, 'smoothing_frames': 3, 'sample_stride': 2, 'combine': 'set', 'weight': 1}
    motion_settings = {'rate': 1.25, 'phase_beats': .125, 'repeat_clock': 'continuous'}
    case('named_binding', opacity, [put(settings, motion_settings=motion_settings)],
         expected_controls=[binding(settings, motion={**named, **motion_settings})])
    settings = {'id': 'native-recording', 'axis': 'intensity', 'range': [.25, .95]}
    case('recording_binding', opacity, [put(settings, motion=recorded)],
         expected_controls=[binding(settings, motion=recorded)])
    wipe = node('transition', inputs=['layer:red', 'layer:blue'], transition_style='wipe', progress=.45)
    settings = {'id': 'native-direction', 'axis': 'x', 'choices': ['right', 'left'], 'curve': 'threshold'}
    case('categorical_binding', wipe, [put(settings, prop='direction')],
         expected_controls=[binding(settings, prop='direction')])
    existing = binding({'id': 'existing', 'axis': 'x', 'range': [.1, .3]})
    changed = {'id': 'edited', 'axis': 'y', 'range': [.5, .9], 'curve': 'smoothstep'}
    case('edited_binding', opacity, [put(changed, index=0)], controls=[existing],
         expected_controls=[binding(changed)])
    case('removed_binding', opacity, [{'kind': 'remove_binding', 'index': 0}],
         controls=[existing], expected_controls=[])
    additive = binding({'id': 'add', 'axis': 'y', 'range': [.2, .4], 'combine': 'add'})
    multiplied = binding({'id': 'multiply', 'axis': 'x', 'range': [.3, .5], 'combine': 'multiply'})
    case('reordered_binding', opacity, [{'kind': 'move_binding', 'index': 2, 'direction': -1}],
         controls=[existing, additive, multiplied], expected_controls=[existing, multiplied, additive])
    return base, result


def qualify(output):
    output = Path(output).resolve()
    temporary_preflight(output.parent)
    output.mkdir(exist_ok=False)
    temporary_preflight(output)
    node_version = subprocess.check_output(['node', '--version'], text=True).strip()
    if int(node_version.lstrip('v').split('.')[0]) < 24:
        raise RuntimeError('qualification_requires_node_24_or_newer')
    probe = "import fs from 'node:fs'; const d=fs.mkdtempSync('probe-'); try {fs.writeFileSync(d+'/a','m103'); if(fs.readFileSync(d+'/a','utf8')!=='m103')throw Error('readback'); fs.renameSync(d+'/a',d+'/b'); fs.unlinkSync(d+'/b');} finally {fs.rmdirSync(d);}"
    subprocess.run(['node', '--input-type=module', '-e', probe], cwd=output, check=True)
    report = {'schema': 'framerudiments.authoring-controls-qualification.v1', 'passed': False,
              'runtime': {'python': platform.python_version(), 'node': node_version,
                          'dancerudiments': version('dancerudiments'), 'pillow': version('Pillow')},
              'scope': {'actual_typescript_operations': True, 'actual_same_origin_http': True,
                        'actual_export_zip': True, 'native_project_validation': True,
                        'native_compositor': True, 'named_and_recorded_native_motion': True,
                        'gpu': False, 'browser': False, 'perceptual_approval': False, 'encoded_video': False},
              'pixel_comparison': {'format': 'RGB8', 'maximum_channel_error': 0,
                                   'reference': 'independently authored native projects'},
              'frames': list(FRAMES), 'cases': []}
    with tempfile.TemporaryDirectory(prefix='m10-3-fixture-', dir=output) as owned:
        directory = Path(owned)
        base, cases = fixtures(directory)
        project_file = directory / 'project.json'
        project_file.write_text(json.dumps(base), encoding='utf-8')
        pins = {item.name: sha256(item.read_bytes()).hexdigest() for item in directory.iterdir()}

        class Handler(AuthoringHandler):
            def log_message(self, *_args):
                pass

        Handler.project_file = project_file
        Handler.session_data = {'project': base}
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.graph_temp_parent = directory / 'owned-proposals'
        worker = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        worker.start()
        started = time.monotonic()
        try:
            payload = {'origin': f'http://127.0.0.1:{server.server_port}',
                       'cases': [{key: value for key, value in row.items() if key != 'expected'} for row in cases]}
            try:
                result = subprocess.run(['node', '--experimental-strip-types',
                                         str(ROOT / 'authoring/integration/graphControlsRoundtrip.ts')],
                                        input=json.dumps(payload), capture_output=True, text=True,
                                        cwd=ROOT, timeout=180, check=True)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError('native_editor_route_failed: '+exc.stderr[-4000:]) from exc
            exports = json.loads(result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
        if worker.is_alive():
            raise RuntimeError('qualification_server_cleanup_failed')
        report['http_and_typescript_seconds'] = round(time.monotonic()-started, 3)
        report['http_requests'] = exports['requests']
        if exports['schema'] != 'framerudiments.authoring-controls-export.v1' or \
                [row['name'] for row in exports['cases']] != [row['name'] for row in cases]:
            raise RuntimeError('unexpected_export_contract')
        panel_width, panel_height = SIZE[0]*3, SIZE[1]+32
        gallery = Image.new('RGB', (panel_width*2, panel_height*((len(cases)+1)//2)), '#121b2d')
        draw = ImageDraw.Draw(gallery)
        for index, (exported, entry) in enumerate(zip(exports['cases'], cases)):
            archive = base64.b64decode(exported['archive'], validate=True)
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                if bundle.namelist() != ['AUTHORING-README.txt', 'project.json'] or bundle.testzip() is not None:
                    raise RuntimeError('export_zip_integrity_failed')
                draft = json.loads(bundle.read('project.json'))
            check_draft(base, draft)
            if draft != entry['expected']:
                raise RuntimeError(f'export_differs_from_independent_native_contract:{entry["name"]}')
            native = Project(draft, directory)
            validation = validate_authoring_graph(native)
            if validation['valid'] is not True:
                raise RuntimeError('exported_native_project_invalid')
            actual, clocks = render(native)
            expected, expected_clocks = render(Project(entry['expected'], directory))
            initial, initial_clocks = render(Project(entry['project'], directory))
            if clocks != expected_clocks or clocks != initial_clocks or any(
                    a.tobytes() != b.tobytes() for a, b in zip(actual, expected)):
                raise RuntimeError('native_export_pixel_or_clock_mismatch')
            if not any(a.tobytes() != b.tobytes() for a, b in zip(actual, initial)):
                raise RuntimeError(f'fixture_edit_did_not_change_pixels:{entry["name"]}')
            if entry['name'] in {'named_binding', 'recording_binding', 'categorical_binding'} and \
                    actual[0].tobytes() == actual[1].tobytes():
                raise RuntimeError('native_control_did_not_vary_fixture')
            x, y = (index % 2)*panel_width, (index // 2)*panel_height
            draw.text((x+5, y+3), entry['name']+' | frames 0, 5, 17', fill='white')
            draw.text((x+5, y+16), 'Actual native RGB8 output; exact reference match', fill='#9caec8')
            for column in range(3):
                gallery.paste(actual[column], (x+column*SIZE[0], y+32))
            report['cases'].append({key: exported[key] for key in (
                'name', 'parameters', 'operations', 'rejected_stale_responses')} | {
                'native_validation': True, 'export_zip_sha256': sha256(archive).hexdigest(),
                'frame_sha256': [sha256(image.tobytes()).hexdigest() for image in actual],
                'clock': clocks, 'compared_frames': len(FRAMES), 'max_rgb8_error': 0,
                'changes_pixels': True})
        if pins != {item.name: sha256(item.read_bytes()).hexdigest() for item in directory.iterdir() if item.is_file()}:
            raise RuntimeError('source_inputs_changed')
        if server.graph_temp_parent.exists() and list(server.graph_temp_parent.iterdir()):
            raise RuntimeError('proposal_temporary_tree_leaked')
        report['source_inputs_unchanged'] = True
        report['proposal_temporary_trees_removed'] = True
        report['http_server_closed'] = True
        png = output / 'graph-controls-contact-sheet.png'
        gallery.save(png)
        report['contact_sheet_sha256'] = sha256(png.read_bytes()).hexdigest()
    report['owned_temporary_tree_removed'] = not directory.exists()
    report['passed'] = report['owned_temporary_tree_removed']
    (output / 'graph-controls-qualification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New qualification output directory')
    args = parser.parse_args()
    report = qualify(args.output)
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
                      'compared_frames': sum(row['compared_frames'] for row in report['cases']),
                      'http_requests': report['http_requests'], 'http_and_typescript_seconds': report['http_and_typescript_seconds'],
                      'owned_temporary_tree_removed': report['owned_temporary_tree_removed']}))


if __name__ == '__main__':
    main()
