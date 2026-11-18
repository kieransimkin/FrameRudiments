"""Actual external-engine replay qualification in the existing GLX CI job.

No skips or renderer doubles. These deliberately deterministic reviewed presets
exercise retained feedback while excluding preset noise. Other presets must pass
their own per-prefix comparison at runtime before the preview admits pixels.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import multiprocessing
from pathlib import Path
import wave

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.encoding import binaries
from framerudiments.preview_refinement_process import RefinementSpec
from framerudiments.preview_stateful_process import SpawnStatefulRenderer
from framerudiments.project import Project
from framerudiments.visualisers import EffectChain


def project_file(directory, engine):
    audio = directory / 'silence.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(b'\x00\x00' * 12000)
    sprite = directory / 'signal.png'
    Image.new('RGBA', (16, 16), (230, 65, 20, 255)).save(sprite)
    if engine == 'projectm':
        preset = directory / 'deterministic.milk'
        # The pinned native engine's legacy VideoEcho path applies randomly
        # initialized hue even when fShader=0 and echo alpha=0. A reviewed
        # MilkDrop2 composite reads retained feedback directly and excludes
        # that random diffuse term; the warp/source/decay path stays native.
        preset.write_text(
            'MILKDROP_PRESET_VERSION=201\nPSVERSION_WARP=0\nPSVERSION_COMP=2\n'
            '[preset00]\nfDecay=0.94\nfGammaAdj=1\nfWaveAlpha=0\nfShader=0\nmv_a=0\n'
            'ob_size=0\nib_size=0\nwarp=0\nzoom=1\nfZoomExponent=1\nrot=0\nsx=1\nsy=1\n'
            'dx=0\ndy=0\nfVideoEchoAlpha=0\n'
            'comp_1=`shader_body\ncomp_2=`{\n'
            'comp_3=`ret = tex2D(sampler_main, uv).xyz;\ncomp_4=`}\n', encoding='utf-8')
    else:
        preset = directory / 'deterministic.json'
        preset.write_text(json.dumps({
            'baseVals': {'decay': .94, 'gammaadj': 1, 'wave_a': 0, 'shader': 0,
                         'mv_a': 0, 'ob_size': 0, 'ib_size': 0, 'warp': 0, 'zoom': 1,
                         'zoomexp': 1, 'rot': 0, 'sx': 1, 'sy': 1, 'dx': 0, 'dy': 0,
                         'echo_alpha': 0},
            'shapes': [], 'waves': [], 'init_eqs_str': '', 'frame_eqs_str': '',
            'pixel_eqs_str': '', 'warp': '', 'comp': '',
        }), encoding='utf-8')
    def pin(path):
        return {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    data = {
        'schema': 'framerudiments.project.v1', 'purpose': 'visualiser',
        'width': 96, 'height': 64, 'fps': 60,
        'audio': {'source': pin(audio), 'sample_rate': 48000, 'start_sample': 0,
                  'end_sample': 12000, 'repeats': 1, 'fade_seconds': 0},
        'clock': {'bpm': 120, 'authority': 'synthetic S6 acceptance', 'status': 'synthetic'},
        'background': {'colour': '#080b10'},
        'layers': [{'id': 'signal', 'type': 'sprite', 'asset': pin(sprite),
                    'center': [48, 32], 'width': 24, 'bounds': [0, 0, 96, 64]}],
        'layer_animations': {'layer:signal': {'opacity': {'keys': [
            {'time': 0, 'value': .05}, {'time': .1, 'value': 1}, {'time': .24, 'value': .1}]}}},
        'effects': [{'id': 'feedback', 'engine': engine, 'preset': pin(preset),
                     'trusted_preset': True, 'input_mix': .45, 'seed': 613}],
        'render_quality': {'spatial_scale': 1, 'temporal_samples': 1},
    }
    path = directory / 'project.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


@pytest.mark.parametrize('engine', ['butterchurn', 'projectm'])
def test_actual_native_sequential_and_verified_random_seek_prefixes_agree(tmp_path, monkeypatch, engine):
    path, document = project_file(tmp_path, engine)
    original = path.read_bytes()
    project = Project.load(path)
    compositor = Compositor(project)
    reference_dir = tmp_path / 'reference'
    reference_dir.mkdir()
    ffmpeg, _ = binaries()
    chain = EffectChain(project, compositor.effect_specs, ffmpeg, reference_dir,
                        routing=compositor.visualiser_routing, hooks=compositor.visualiser_hooks,
                        context_at=compositor.context)
    expected = {}
    reference_backends = list(chain.backends)
    try:
        for frame in range(10):
            image, context = compositor.frame(frame, apply_quality=False)
            image = chain.process(image, context,
                isolated_layers=compositor._route_parts
                if compositor.visualiser_routing is not None or compositor._isolate_stage_injections else None)
            expected[frame] = compositor.output_quality.apply(image).convert('RGBA').tobytes()
    finally:
        chain.close()
        compositor.close()
    assert all(all(backend.evidence['cleanup'].values()) for backend in reference_backends)
    external_profile_root = tmp_path / 'must-not-use-external-browser-profiles'
    monkeypatch.setenv('FRAMERUDIMENTS_BROWSER_PROFILE_ROOT', str(external_profile_root))
    renderer = SpawnStatefulRenderer(RefinementSpec.from_snapshot(
        path, document, deepcopy(document), {}, tmp_path/'preview'))
    statuses = []
    owned_directory = None
    try:
        for frame in (7, 2, 9, 0, 8):
            png = renderer.render_cancellable(frame, 'Q5', lambda: False, statuses.append)
            with Image.open(BytesIO(png)) as decoded:
                assert decoded.mode == 'RGBA' and decoded.size == (96, 64)
                assert decoded.tobytes() == expected[frame]
            assert renderer.stateful_status['phase'] == 'ready'
            assert renderer.stateful_status['requested_frame'] == frame
            assert renderer.process.is_alive()
            owned_directory = Path(renderer.directory.name)
        assert renderer.stateful_status['verified_prefix_frames'] == 10
        assert renderer.stateful_status['tolerance_rgba8'] == 0
        assert len(set(expected.values())) > 2  # Changing input and real feedback.
    finally:
        renderer.close()
    assert renderer.process is None and renderer.scope is None
    assert renderer.last_pid not in {child.pid for child in multiprocessing.active_children()}
    assert owned_directory is not None and not owned_directory.exists()
    assert not external_profile_root.exists()
    assert not list((tmp_path/'preview').iterdir())
    assert path.read_bytes() == original
    assert any(row['phase'] == 'verifying' for row in statuses)
