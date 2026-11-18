"""Native Project/Compositor/EffectChain with synthetic external engine pixels.

These tests exercise actual PCM extraction, routing, clocks, quality and owned
resource cleanup. The external backend is deliberately a deterministic test
double; actual Butterchurn/projectM qualification lives in integration/.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.encoding import binaries
from framerudiments.preview_replay import ReplayCancelled, StatefulSeekUnavailable
from framerudiments.preview_stateful_renderer import ExternalRuntimeGuard, StatefulPreviewRenderer
from framerudiments.project import Project
from framerudiments.visualisers import EffectChain
from test_preview_native_project import fixture


class ConstantRuntimeGuard:
    digest = 'a' * 64

    def __init__(self, document):
        pass

    def __call__(self):
        pass

    def verify_frame(self):
        pass


@pytest.fixture
def native(tmp_path, monkeypatch):
    path, data = fixture(tmp_path)
    data['render_quality'] = {'spatial_scale': 2, 'temporal_samples': 2,
                              'antialias': .6, 'antialias_passes': 2}
    data['post_effects'] = {'temporal': {'strength': .7, 'shutter_frames': 3, 'samples': 4}}
    data['layer_animations'] = {'layer:square': {'opacity': {'keys': [
        {'time': 0, 'value': .1}, {'time': .3, 'value': .9}, {'time': .6, 'value': .3}]}}}
    data['effects'] = [
        {'id': 'first', 'engine': 'butterchurn', 'preset': 'feedback', 'seed': 37},
        {'id': 'second', 'engine': 'projectm', 'preset': 'feedback', 'seed': 91},
    ]
    data['visualiser_stage_injections'] = [
        {'before': 1, 'elements': ['layer:square'], 'opacity': .4}]
    path.write_text(json.dumps(data), encoding='utf-8')
    backends, contexts = [], []
    settings = {'unstable': False}

    class Feedback:
        def __init__(self, size, fps, preset, mix, seed, work):
            self.size, self.fps, self.seed = size, fps, seed
            self.previous = Image.new('RGBA', size, (0, 0, 0, 255))
            self.next_frame = 0
            self.closed = False
            self.ordinal = len(backends)
            self.evidence = {'engine': 'synthetic-native-contract', 'seed': seed,
                             'device': settings.get('device', 'synthetic-device-one')}
            backends.append(self)

        def process(self, image, context, pcm):
            assert context.frame == self.next_frame
            assert image.mode == 'RGBA' and image.size == self.size
            assert len(pcm) == 2048
            assert context.output_seconds == context.frame / self.fps
            contexts.append((context.frame, context.source_seconds, context.beat))
            self.next_frame += 1
            self.previous = Image.blend(self.previous, image, .57)
            if settings['unstable'] and self.ordinal >= 2 and context.frame == 1:
                self.previous.putpixel((0, 0), (255, 2, 3, 255))
            return self.previous.copy()

        def close(self):
            self.closed = True

    def chain(*args, **kwargs):
        return EffectChain(*args, **kwargs, factories={'butterchurn': Feedback, 'projectm': Feedback})

    monkeypatch.setattr('framerudiments.visualisers.EffectChain', chain)
    monkeypatch.setattr('framerudiments.preview_stateful_renderer.ExternalRuntimeGuard', ConstantRuntimeGuard)
    renderer = StatefulPreviewRenderer(path, deepcopy(data), {}, validate_draft=lambda a, b: None,
                                       temp_parent=tmp_path/'preview')
    return path, data, renderer, backends, contexts, settings, chain


def test_native_replay_matches_production_order_pcm_clock_routing_and_exact_quality(native, tmp_path):
    path, data, renderer, backends, contexts, _, chain_factory = native
    before = {name: (tmp_path/name).read_bytes() for name in ('project.json', 'source.png', 'source.wav')}
    project = Project.load(path)
    compositor = Compositor(project)
    work = tmp_path/'reference'
    work.mkdir()
    ffmpeg, _ = binaries()
    chain = chain_factory(project, compositor.effect_specs, ffmpeg, work,
                          routing=compositor.visualiser_routing, hooks=compositor.visualiser_hooks,
                          context_at=compositor.context)
    expected = {}
    try:
        for n in range(13):
            image, context = compositor.frame(n, apply_quality=False)
            image = chain.process(image, context, isolated_layers=compositor._route_parts)
            expected[n] = compositor.output_quality.apply(image).convert('RGBA').tobytes()
    finally:
        chain.close()
        compositor.close()
    try:
        for n in (7, 1, 4, 10, 0, 12, 7):
            with Image.open(BytesIO(renderer(n))) as decoded:
                assert decoded.mode == 'RGBA' and decoded.size == (64, 48)
                assert decoded.tobytes() == expected[n]
            assert renderer.stateful_status['phase'] == 'ready'
            assert renderer.stateful_status['requested_frame'] == n
            assert sum(not backend.closed for backend in backends) == 2  # One ordered chain.
        assert len(renderer.replay.receipts) == 13
        assert renderer.project.data == data
        assert renderer.stateful_status['tolerance_rgba8'] == 0
        assert renderer.last_setup_ms >= 0
        assert all(source == project.source_time(n) and beat == project.continuous_beat(n)
                   for n, source, beat in contexts)
    finally:
        renderer.close()
    assert all(backend.closed for backend in backends)
    assert not list((tmp_path/'preview').iterdir())
    assert {name: (tmp_path/name).read_bytes() for name in before} == before


def test_native_nondeterminism_refuses_unverified_result_and_clears_receipts(native):
    _, _, renderer, backends, _, settings, _ = native
    settings['unstable'] = True
    try:
        with pytest.raises(StatefulSeekUnavailable, match='replay_mismatch'):
            renderer(4)
        assert renderer.replay.receipts == ()
        assert renderer.stateful_status['phase'] == 'unavailable'
        assert all(backend.closed for backend in backends)
        count = len(backends)
        with pytest.raises(StatefulSeekUnavailable, match='replay_mismatch'):
            renderer(0)
        assert len(backends) == count
    finally:
        renderer.close()


def test_native_pinned_source_change_revokes_proofs_and_cannot_serve_last_result(native, tmp_path):
    _, _, renderer, backends, _, _, _ = native
    try:
        renderer(3)
        source = tmp_path/'source.png'
        source.write_bytes(source.read_bytes() + b'changed')
        with pytest.raises(StatefulSeekUnavailable, match='input_identity_changed'):
            renderer(3)
        assert renderer.replay.receipts == ()
        assert renderer.stateful_status['phase'] == 'unavailable'
        assert all(backend.closed for backend in backends)
    finally:
        renderer.close()


def test_native_cancel_yields_only_hash_receipts_and_closes_all_engine_instances(native):
    _, _, renderer, backends, _, _, _ = native
    renderer(2)
    previous = renderer.replay.receipts
    cancel = [False]
    def progress(status):
        if status['current_request_frames'] >= 3:
            cancel[0] = True
    try:
        with pytest.raises(ReplayCancelled):
            renderer(8, cancelled=lambda: cancel[0], progress=progress)
        assert renderer.replay.receipts is previous
        assert all(backend.closed for backend in backends)
        renderer(1)
        renderer.release_compositor()
        assert renderer.replay.receipts is previous
        assert all(backend.closed for backend in backends)
    finally:
        renderer.close()


def test_native_already_cancelled_request_closes_live_state_before_returning(native):
    _, _, renderer, backends, _, _, _ = native
    try:
        renderer(1)
        with pytest.raises(ReplayCancelled):
            renderer(3, cancelled=lambda: True)
        assert all(backend.closed for backend in backends)
        assert renderer.stateful_status['phase'] == 'cancelled'
    finally:
        renderer.close()


def test_native_profile_refusal_does_not_allocate_or_replace_real_quality(native):
    _, _, renderer, backends, _, _, _ = native
    try:
        with pytest.raises(StatefulSeekUnavailable, match='profile_unsupported'):
            renderer(0, 'Q3')
        assert not backends
        assert renderer.project is None
    finally:
        renderer.close()


def test_native_original_project_replacement_revokes_cached_replay(native):
    path, _, renderer, backends, _, _, _ = native
    try:
        renderer(2)
        path.write_bytes(path.read_bytes() + b'\n')
        with pytest.raises(StatefulSeekUnavailable, match='input_identity_changed'):
            renderer(2)
        assert renderer.replay.receipts == ()
        assert all(backend.closed for backend in backends)
    finally:
        renderer.close()


def test_native_complete_pcm_limit_rejects_before_any_engine_or_decode(native, tmp_path):
    _, data, renderer, backends, _, _, _ = native
    audio = data['audio']
    exact = (audio['end_sample'] - audio['start_sample']) * audio['repeats'] * 8
    renderer.max_pcm_bytes = exact - 1
    try:
        with pytest.raises(StatefulSeekUnavailable, match='pcm_budget_exceeded'):
            renderer(0)
        assert not backends
        assert not list((tmp_path/'preview').rglob('effects-audio.f32'))
        assert renderer.stateful_status['pcm_bytes'] == exact
        renderer.max_pcm_bytes = exact
        assert renderer(0).startswith(b'\x89PNG')
    finally:
        renderer.close()


def test_native_pcm_free_space_guard_reserves_margin_before_decode(native, monkeypatch):
    _, data, renderer, backends, _, _, _ = native
    audio = data['audio']
    exact = (audio['end_sample'] - audio['start_sample']) * audio['repeats'] * 8
    monkeypatch.setattr('framerudiments.preview_stateful_renderer.shutil.disk_usage',
                        lambda path: SimpleNamespace(free=exact + 16 * 1024**2 - 1))
    try:
        with pytest.raises(StatefulSeekUnavailable, match='pcm_disk_budget_exceeded'):
            renderer(0)
        assert not backends
    finally:
        renderer.close()


def test_native_verified_receipt_binds_actual_pixels_prefix_and_runtime(native, tmp_path):
    _, _, renderer, _, _, _, _ = native
    try:
        with pytest.raises(StatefulSeekUnavailable, match='unverified_result'):
            renderer.verified_receipt(0)
        png = renderer(3)
        receipt = renderer.verified_receipt(3)
        with Image.open(BytesIO(png)) as image:
            assert receipt['pixels_sha256'] == sha256(image.tobytes()).hexdigest()
        assert receipt['frame'] == 3
        assert receipt['prefix_sha256'] == renderer.replay.receipts[3].prefix_sha256
        assert receipt['engine_identity_sha256'] == renderer.stateful_status['engine_identity_sha256']
        assert str(tmp_path) not in json.dumps(receipt)
        receipt['pixels_sha256'] = 'f' * 64
        assert renderer.verified_receipt(3)['pixels_sha256'] != receipt['pixels_sha256']
        for frame in (-1, 4, True, 1.5):
            with pytest.raises(StatefulSeekUnavailable, match='unverified_result'):
                renderer.verified_receipt(frame)
        with pytest.raises(ReplayCancelled):
            renderer(4, cancelled=lambda: True)
        with pytest.raises(StatefulSeekUnavailable, match='unverified_result'):
            renderer.verified_receipt(3)
    finally:
        renderer.close()
    with pytest.raises(StatefulSeekUnavailable, match='unverified_result'):
        renderer.verified_receipt(3)


def test_native_engine_identity_is_stable_across_owned_sidecar_paths_and_binds_runtime(native, tmp_path):
    path, data, unused_renderer, backends, _, settings, _ = native
    unused_renderer.close()
    cue = tmp_path / 'cues.lrc'
    cue.write_text('[00:00.000][SECTION: ORIGINAL]\n', encoding='utf-8')
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    path.write_text(json.dumps(data), encoding='utf-8')
    sidecars = {'cues': [{'start': 0, 'label': '[SECTION: EDITED]'}]}
    owners = []
    def create(name, edits):
        renderer = StatefulPreviewRenderer(path, deepcopy(data), deepcopy(edits),
            validate_draft=lambda a, b: None, temp_parent=tmp_path/name)
        owners.append(renderer)
        renderer(2)
        return renderer
    try:
        foreground = create('foreground', sidecars)
        refinement = create('refinement', sidecars)
        assert foreground.project.data['cues']['path'] != refinement.project.data['cues']['path']
        assert foreground.verified_receipt(2) == refinement.verified_receipt(2)
        old_identity = foreground.stateful_status['engine_identity_sha256']
        settings['device'] = 'synthetic-device-two'
        changed_device = create('other-device', sidecars)
        assert changed_device.stateful_status['engine_identity_sha256'] != old_identity
        settings.pop('device')
        changed_sidecars = create('other-sidecars', {'cues': [{'start': 0, 'label': '[SECTION: DIFFERENT]'}]})
        assert changed_sidecars.stateful_status['engine_identity_sha256'] != old_identity
    finally:
        for renderer in owners:
            renderer.close()
    assert all(backend.closed for backend in backends)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import framerudiments.preview_stateful_renderer as module
    for key in module._ENVIRONMENT:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(module, '_executables', lambda: {
        'ffmpeg': None, 'ffprobe': None, 'node': None, 'browser': None})
    root = tmp_path/'runtime'
    root.mkdir()
    node = root/'node_modules'/'engine'
    node.mkdir(parents=True)
    (node/'index.js').write_text('code-v1', encoding='utf-8')
    (root/'package-lock.json').write_text('{}', encoding='utf-8')
    (root/'package.json').write_text('{}', encoding='utf-8')
    monkeypatch.setenv('FRAMERUDIMENTS_BUTTERCHURN_RUNTIME', str(root))
    return root


def test_runtime_guard_pins_transitive_code_and_detects_change_at_publication(runtime):
    guard = ExternalRuntimeGuard({'effects': [{'engine': 'butterchurn', 'preset': 'feedback'}]})
    guard()
    assert len(guard.digest) == 64
    (runtime/'node_modules'/'engine'/'index.js').write_text('code-v2', encoding='utf-8')
    guard.verify_frame()  # Expensive locked-tree check belongs at request boundaries.
    with pytest.raises(StatefulSeekUnavailable, match='runtime_identity_changed'):
        guard()


def test_runtime_guard_detects_new_transitive_module(runtime):
    guard = ExternalRuntimeGuard({'effects': [{'engine': 'butterchurn'}]})
    (runtime/'node_modules'/'engine'/'extra.js').write_text('code', encoding='utf-8')
    with pytest.raises(StatefulSeekUnavailable, match='runtime_identity_changed'):
        guard()


def test_missing_runtime_is_recorded_and_later_availability_invalidates(runtime, monkeypatch):
    missing = runtime/'missing'
    monkeypatch.setenv('FRAMERUDIMENTS_BUTTERCHURN_RUNTIME', str(missing))
    guard = ExternalRuntimeGuard({'effects': [{'engine': 'butterchurn'}]})
    guard()
    missing.mkdir()
    (missing/'package-lock.json').write_text('{}', encoding='utf-8')
    with pytest.raises(StatefulSeekUnavailable, match='runtime_identity_changed'):
        guard()


def test_texture_tree_and_environment_changes_revoke_runtime(runtime, monkeypatch):
    textures = runtime/'textures'
    textures.mkdir()
    (textures/'sample.png').write_bytes(b'image-one')
    monkeypatch.setenv('FRAMERUDIMENTS_TEXTURES_DIR', str(textures))
    guard = ExternalRuntimeGuard({'effects': [{'engine': 'butterchurn'}]})
    (textures/'another.png').write_bytes(b'image-two')
    with pytest.raises(StatefulSeekUnavailable, match='runtime_identity_changed'):
        guard()
    fresh = ExternalRuntimeGuard({'effects': [{'engine': 'butterchurn'}]})
    monkeypatch.setenv('LIBGL_ALWAYS_SOFTWARE', '1')
    with pytest.raises(StatefulSeekUnavailable, match='runtime_identity_changed'):
        fresh()


def test_guard_includes_projectm_library_receipt_and_builtin_milk(runtime, monkeypatch):
    library = runtime/'projectm.so'
    library.write_bytes(b'library-one')
    receipt = runtime/'framerudiments-projectm.json'
    receipt.write_text('{}', encoding='utf-8')
    monkeypatch.setenv('FRAMERUDIMENTS_PROJECTM_LIBRARY', str(library))
    guard = ExternalRuntimeGuard({'effects': [{'engine': 'projectm', 'preset': 'feedback'}]})
    assert any(path.name == 'feedback.milk' for path, _ in guard._entries)
    assert any(path == receipt for path, _ in guard._entries)
    library.write_bytes(b'library-two')
    with pytest.raises(StatefulSeekUnavailable, match='runtime_identity_changed'):
        guard.verify_frame()


def test_guard_does_not_expose_paths_in_failures(runtime, monkeypatch):
    monkeypatch.setenv('FRAMERUDIMENTS_BUTTERCHURN_RUNTIME', str(runtime/'private-song-name'))
    guard = ExternalRuntimeGuard({'effects': [{'engine': 'butterchurn'}]})
    monkeypatch.setenv('FRAMERUDIMENTS_BUTTERCHURN_RUNTIME', str(runtime/'changed'))
    with pytest.raises(StatefulSeekUnavailable) as error:
        guard()
    assert str(runtime) not in str(error.value)
    assert 'private-song-name' not in str(error.value)


def test_guard_rejects_unbounded_runtime_inventory(runtime, monkeypatch):
    monkeypatch.setattr('framerudiments.preview_stateful_renderer._MAX_RUNTIME_FILES', 3)
    with pytest.raises(StatefulSeekUnavailable, match='runtime_manifest_limit'):
        ExternalRuntimeGuard({'effects': [{'engine': 'butterchurn'}]})
