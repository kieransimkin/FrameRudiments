"""Export actual TypeScript graph edits and compare native project renders.

An explicit cross-language integration command, independent of pytest collection.
Requires Node 24 and the ordinary Python package. Creates synthetic pinned inputs
in one owned temporary tree; no browser, GPU, FFmpeg, downloads or private media.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
from importlib.metadata import version
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import wave
import zipfile

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from authoring_preview import check_draft  # noqa: E402
from framerudiments import Project  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments.graph_authoring import validate_authoring_graph  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402

SIZE = (160, 96)
FRAMES = (0, 5, 17, 5)
CASE_NAMES = ('identity', 'fork_merge', 'merge_reordered', 'rewired', 'matte',
              'multi_matte', 'transition', 'transition_start', 'transition_end',
              'matte_transition', 'control_preserving_rewire')


def fixture(directory):
    with wave.open(str(directory / 'source.wav'), 'wb') as handle:
        handle.setparams((1, 2, 48000, 48000, 'NONE', 'not compressed'))
        handle.writeframes(b'\0\0' * 48000)
    for name in ('red', 'blue', 'mask'):
        image = Image.new('RGBA', SIZE)
        draw = ImageDraw.Draw(image)
        if name == 'red':
            draw.ellipse((15, 9, 124, 88), fill=(246, 72, 37, 220))
            draw.rectangle((64, 26, 150, 66), fill=(255, 193, 35, 180))
        elif name == 'blue':
            draw.polygon(((18, 78), (84, 5), (150, 83)), fill=(38, 165, 245, 210))
            draw.ellipse((90, 16, 145, 65), fill=(133, 69, 233, 230))
        else:
            for x in range(SIZE[0]):
                draw.line((x, 0, x, SIZE[1]-1), fill=(255, 255, 255, round(255*x/(SIZE[0]-1))))
        image.save(directory / f'{name}.png')

    def ref(name):
        return {'path': name, 'sha256': sha256((directory / name).read_bytes()).hexdigest()}

    base = {'schema': 'framerudiments.project.v1', 'title': 'M10.2 synthetic graph qualification',
            'purpose': 'visualiser', 'width': SIZE[0], 'height': SIZE[1], 'fps': 12,
            'fps_reason': 'Bounded synthetic authoring roundtrip qualification',
            'audio': {'source': ref('source.wav'), 'sample_rate': 48000, 'start_sample': 0,
                      'end_sample': 48000, 'repeats': 2, 'fade_seconds': 0},
            'clock': {'bpm': 120, 'origin': 0, 'authority': 'synthetic fixture', 'status': 'synthetic'},
            'background': {'colour': '#121b2d'}, 'render_quality': {'enabled': False},
            'layers': [{'id': name, 'asset': ref(f'{name}.png'), 'width': SIZE[0],
                        'center': [SIZE[0]/2, SIZE[1]/2], 'bounds': [0, 0, *SIZE]}
                       for name in ('red', 'blue', 'mask')]}
    controlled = deepcopy(base)
    controlled['effect_graph'] = {
        'nodes': [{'id': name, 'type': 'source'} for name in ('layer:red', 'layer:blue')] +
                 [{'id': 'control-branch', 'type': 'opacity', 'inputs': ['layer:red'], 'opacity': .8}],
        'output': 'control-branch'}
    controlled['effect_graph_controls'] = [{
        'id': 'existing-motion', 'node': 'control-branch', 'property': 'opacity',
        'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [.2, .8]}]
    return base, controlled


def expected_project(name, base, controlled):
    """Independent hand-authored native graphs, not copied from TypeScript output."""
    if name == 'control_preserving_rewire':
        result = deepcopy(controlled)
        result['effect_graph']['nodes'][-1]['inputs'] = ['layer:blue']
        return result
    result = deepcopy(base)
    if name == 'identity':
        return result  # Identity graph must equal the legacy no-graph compositor.
    nodes = [{'id': source, 'type': 'source'} for source in
             ('frame', 'layer:red', 'layer:blue', 'layer:mask')]
    if name in ('fork_merge', 'merge_reordered', 'rewired'):
        nodes += [
            {'id': 'editor-opacity-1', 'type': 'opacity', 'inputs': ['layer:red'], 'opacity': .5},
            {'id': 'editor-opacity-2', 'type': 'opacity', 'inputs': ['layer:blue'], 'opacity': .75}]
        inputs = {'fork_merge': ['editor-opacity-1', 'editor-opacity-2'],
                  'merge_reordered': ['editor-opacity-2', 'editor-opacity-1'],
                  'rewired': ['editor-opacity-1', 'layer:red']}[name]
        nodes += [{'id': 'editor-composite-1', 'type': 'composite', 'inputs': inputs, 'mode': 'over'}]
    elif name in ('matte', 'multi_matte'):
        nodes += [{'id': 'editor-matte-1', 'type': 'matte',
                   'inputs': ['layer:red', 'layer:mask'] + (['layer:blue'] if name == 'multi_matte' else []),
                   'matte_channel': 'alpha', 'mask_combine': 'multiply'}]
    else:
        matte = name == 'matte_transition'
        nodes += [{'id': 'editor-matte_transition-1' if matte else 'editor-transition-1',
                   'type': 'transition', 'inputs': ['layer:red', 'layer:blue'] + (['layer:mask'] if matte else []),
                   'transition_style': 'matte' if matte else 'dissolve',
                   'progress': {'transition': .35, 'transition_start': 0, 'transition_end': 1,
                                'matte_transition': .5}[name]}]
    result['effect_graph'] = {'nodes': nodes, 'output': nodes[-1]['id']}
    return result


def render(project):
    compositor = Compositor(project)
    try:
        pixels, clocks = [], []
        for frame in FRAMES:
            image, context = compositor.frame(frame, apply_quality=False)
            if image.mode != 'RGB' or image.size != SIZE:
                raise RuntimeError('unexpected_native_pixel_format')
            pixels.append(image.copy())
            clocks.append({'frame': context.frame, 'source_seconds': context.source_seconds,
                           'beat': context.beat, 'source_beat': context.source_beat})
        if pixels[1].tobytes() != pixels[3].tobytes():
            raise RuntimeError('native_random_access_mismatch')
        project.verify_inputs()
        return pixels, clocks
    finally:
        compositor.close()


def qualify(output):
    output = Path(output).resolve()
    temporary_preflight(output.parent)
    output.mkdir(exist_ok=False)
    temporary_preflight(output)
    node_version = subprocess.run(['node', '--version'], check=True, capture_output=True, text=True).stdout.strip()
    if int(node_version.lstrip('v').split('.')[0]) < 24:
        raise RuntimeError('qualification_requires_node_24_or_newer')
    report = {'schema': 'framerudiments.authoring-graph-qualification.v1', 'passed': False,
              'runtime': {'python': platform.python_version(), 'node': node_version,
                          'dancerudiments': version('dancerudiments'), 'pillow': version('Pillow')},
              'scope': {'actual_typescript_operations': True, 'actual_export_zip': True,
                        'native_project_validation': True, 'native_compositor': True,
                        'native_motion_control': True, 'gpu': False, 'browser': False,
                        'perceptual_approval': False, 'encoded_video': False},
              'pixel_comparison': {'format': 'RGB8', 'maximum_channel_error': 0,
                                   'reference': 'independently authored native projects'},
              'frames': list(FRAMES), 'cases': [], 'owned_temporary_tree_removed': False}
    with tempfile.TemporaryDirectory(prefix='m10-2-fixture-', dir=output) as owned:
        directory = Path(owned)
        base, controlled = fixture(directory)
        original_pins = {p.name: sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
        for document in (base, controlled):
            project = Project(document, directory)
            if not validate_authoring_graph(project)['valid']:
                raise RuntimeError('original_native_fixture_invalid')
        source = json.dumps({'base': base, 'controlled': controlled})
        result = subprocess.run(['node', '--experimental-strip-types',
                                 str(ROOT / 'authoring/integration/graphRoundtrip.ts')],
                                input=source, capture_output=True, text=True, check=True,
                                cwd=ROOT, timeout=30)
        exports = json.loads(result.stdout)
        if exports.get('schema') != 'framerudiments.authoring-export-fixtures.v1' or \
                [row['name'] for row in exports['cases']] != list(CASE_NAMES):
            raise RuntimeError('unexpected_export_fixture_contract')
        gallery = Image.new('RGB', (SIZE[0]*3, (SIZE[1]+24)*len(CASE_NAMES)), '#121b2d')
        draw = ImageDraw.Draw(gallery)
        first_hashes = {}
        for row_index, exported in enumerate(exports['cases']):
            archive = base64.b64decode(exported['archive'], validate=True)
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                if bundle.namelist() != ['AUTHORING-README.txt', 'project.json'] or bundle.testzip() is not None:
                    raise RuntimeError('export_zip_integrity_failed')
                draft = json.loads(bundle.read('project.json'))
            original = controlled if exported['original'] == 'controlled' else base
            check_draft(original, draft)
            if {key: value for key, value in draft.items() if key != 'effect_graph'} != \
                    {key: value for key, value in original.items() if key != 'effect_graph'}:
                raise RuntimeError('authoring_modified_non_graph_data')
            expected = expected_project(exported['name'], base, controlled)
            if exported['name'] != 'identity' and draft['effect_graph'] != expected['effect_graph']:
                raise RuntimeError('exported_graph_differs_from_native_contract')
            native = Project(draft, directory)
            validation = validate_authoring_graph(native)
            if validation['valid'] is not True:
                raise RuntimeError('exported_native_graph_invalid')
            actual, clocks = render(native)
            reference, expected_clocks = render(Project(expected, directory))
            if clocks != expected_clocks or any(a.tobytes() != b.tobytes() for a, b in zip(actual, reference)):
                raise RuntimeError('export_native_render_mismatch')
            hashes = [sha256(image.tobytes()).hexdigest() for image in actual]
            first_hashes[exported['name']] = hashes[0]
            if exported['name'] == 'control_preserving_rewire' and hashes[0] == hashes[1]:
                raise RuntimeError('native_motion_did_not_change_fixture')
            draw.text((4, row_index*(SIZE[1]+24)+4),
                      f'{exported["name"]} | frames 0, 5, 17', fill='white')
            for col in range(3):
                x, y = col*SIZE[0], row_index*(SIZE[1]+24)
                gallery.paste(actual[col], (x, y+24))
            report['cases'].append({'name': exported['name'], 'native_validation': True,
                                    'export_zip_sha256': sha256(archive).hexdigest(),
                                    'frame_sha256': hashes, 'clock': clocks,
                                    'compared_frames': len(FRAMES), 'max_rgb8_error': 0})
        for pair in (('fork_merge', 'merge_reordered'), ('fork_merge', 'rewired'), ('matte', 'multi_matte'),
                     ('transition_start', 'transition_end')):
            if first_hashes[pair[0]] == first_hashes[pair[1]]:
                raise RuntimeError('fixture_does_not_distinguish_connections')
        if original_pins != {p.name: sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}:
            raise RuntimeError('source_inputs_changed')
        report['source_inputs_unchanged'] = True
        png = output / 'graph-authoring-contact-sheet.png'
        gallery.save(png)
        report['contact_sheet_sha256'] = sha256(png.read_bytes()).hexdigest()
    report['owned_temporary_tree_removed'] = not directory.exists()
    report['passed'] = report['owned_temporary_tree_removed']
    (output / 'graph-authoring-qualification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New qualification output directory')
    args = parser.parse_args()
    result = qualify(args.output)
    print(json.dumps({'passed': result['passed'], 'cases': len(result['cases']),
                      'compared_frames': sum(row['compared_frames'] for row in result['cases']),
                      'owned_temporary_tree_removed': result['owned_temporary_tree_removed']}))


if __name__ == '__main__':
    main()
