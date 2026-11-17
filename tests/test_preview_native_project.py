"""S3 real native Project/Compositor qualification (base+dev dependencies).

No mocks, EGL, FFmpeg, downloaded assets or skip-only pass. These tests are
collected by the existing CPU CI matrices in a complete repository checkout.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import wave

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.preview_native_renderer import NativePreviewRenderer
from framerudiments.preview_quality import profile_capabilities
from framerudiments.project import Project


def fixture(tmp_path, *, graph=False):
    audio = tmp_path/'source.wav'
    with wave.open(str(audio), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b'\x00\x00' * 48000)
    sprite = tmp_path/'source.png'
    Image.new('RGBA', (8, 8), (230, 80, 20, 180)).save(sprite)
    def ref(path):
        return {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    data = {'schema': 'framerudiments.project.v1', 'purpose': 'visualiser',
            'width': 64, 'height': 48, 'fps': 60,
            'audio': {'source': ref(audio), 'sample_rate': 48000,
                      'start_sample': 0, 'end_sample': 48000, 'repeats': 2, 'fade_seconds': 0},
            'clock': {'bpm': 120, 'authority': 'synthetic S3 fixture', 'status': 'synthetic'},
            'background': {'colour': '#142030'},
            'layers': [{'id': 'square', 'type': 'sprite', 'asset': ref(sprite),
                        'center': [32, 24], 'width': 16, 'bounds': [0, 0, 64, 48]}],
            'render_quality': {'spatial_scale': 2, 'temporal_samples': 2}}
    if graph:
        data['effect_graph'] = {'nodes': [{'id': 'frame', 'type': 'source'},
                                         {'id': 'blur', 'type': 'filter', 'inputs': ['frame'],
                                          'filter_radius': 2}], 'output': 'blur'}
    path = tmp_path/'project.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


@pytest.mark.parametrize('graph', [False, True])
def test_real_native_q5_parity_and_smaller_canvas_at_all_profiles(tmp_path, graph):
    path, data = fixture(tmp_path, graph=graph)
    original_bytes = path.read_bytes()
    project = Project.load(path)
    report = profile_capabilities(project)
    assert all(row['supported'] for row in report)
    def validate(original, draft):
        assert original == draft == data
    renderer = NativePreviewRenderer(path, deepcopy(data), {}, validate_draft=validate,
                                     temp_parent=tmp_path/'cache')
    reference = Compositor(project)
    try:
        for rank in range(6):
            for frame in (3, 69, 0, 118):
                with Image.open(BytesIO(renderer(frame, f'Q{rank}'))) as decoded:
                    decoded.load()
                    live = renderer.compositor.project
                    assert decoded.mode == 'RGBA'
                    assert decoded.size == (report[rank]['output_width'], report[rank]['output_height'])
                    assert decoded.size == (live.width, live.height)  # Not a resized full-canvas result.
                    assert live.fps == project.fps
                    assert live.frame_count == project.frame_count
                    assert live.source_time(frame) == project.source_time(frame)
                    assert live.continuous_beat(frame) == project.continuous_beat(frame)
                    if graph:
                        radius = renderer.compositor.effect_graph.nodes['blur'].config['filter_radius']
                        assert radius == pytest.approx(2 * live.width / project.width)
                    if rank == 5:
                        expected, _ = reference.frame(frame, apply_quality=True)
                        assert decoded.tobytes() == expected.convert('RGBA').tobytes()
    finally:
        renderer.close()
        reference.close()
    assert path.read_bytes() == original_bytes
    assert not list((tmp_path/'cache').iterdir())


def test_real_source_clock_sidecar_is_temporary_and_detects_changes(tmp_path):
    path, data = fixture(tmp_path)
    cue = tmp_path/'cues.lrc'
    cue.write_text('[00:00.250][SECTION: SOURCE]\n', encoding='utf-8')
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    path.write_text(json.dumps(data), encoding='utf-8')
    before = path.read_bytes(), cue.read_bytes()
    renderer = NativePreviewRenderer(path, data,
        {'cues': [{'start': .5, 'label': '[SECTION: EDITED]'}]},
        validate_draft=lambda a,b: None, temp_parent=tmp_path/'cache')
    try:
        renderer(31, 'Q0')
        assert renderer.project.cues[0]['frame'] == 30
        assert renderer.project.cues[1]['frame'] == 90
        modified = Path(renderer.project.data['cues']['path'])
        assert modified.is_relative_to(tmp_path/'cache')
        modified.write_text('[00:00.000][SECTION: ALTERED]\n', encoding='utf-8')
        with pytest.raises(ValueError, match='changed|hash mismatch'):
            renderer(32, 'Q0')
    finally:
        renderer.close()
    assert (path.read_bytes(), cue.read_bytes()) == before
    assert not list((tmp_path/'cache').iterdir())
