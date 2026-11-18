from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from framerudiments.preview_adaptive import AdaptiveController
from framerudiments.preview_quality import (
    PinnedInputGuard, ProfileUnsupported, profile_capabilities, profile_document,
)


class Clock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now
    def advance(self, ms):
        self.now += ms / 1000


def observe(controller, clock, milliseconds, queue=0):
    clock.advance(max(milliseconds, 1000 / controller.target_fps))
    return controller.observe(milliseconds, queue_ms=queue,
                              epoch=controller.epoch, profile_id=controller.profile_id)


@pytest.mark.parametrize('cost,rank', [(5, 5), (12, 5), (30, 0), (70, 0)])
def test_measured_costs_downgrade_one_rank_without_changing_clock(cost, rank):
    clock = Clock()
    control = AdaptiveController(60, clock=clock)
    changes = []
    for _ in range(500):
        before = control.rank
        decision = observe(control, clock, cost)
        if decision:
            assert control.rank == before - 1
            changes.append(control.rank)
    assert control.rank == rank
    assert changes == list(range(4, rank - 1, -1))
    assert control.snapshot()['target_unattainable'] == (cost > control.budget_ms and rank == 0)


def test_recovery_requires_ninety_frames_two_seconds_and_cooldown():
    clock = Clock()
    control = AdaptiveController(60, clock=clock)
    assert observe(control, clock, 300)['to_profile'] == 'Q4'
    for _ in range(89):
        assert observe(control, clock, 5) is None
    assert control.rank == 4
    for _ in range(50):
        decision = observe(control, clock, 5)
        if decision:
            break
    assert decision['to_profile'] == 'Q5'
    assert control.last_change - 100 >= 2


def test_cached_frames_and_duplicate_acknowledgements_do_not_train_or_inflate_fresh_rate():
    clock = Clock()
    control = AdaptiveController(30, clock=clock)
    for _ in range(1000):
        control.hit()
    clock.advance(1000)
    control.acknowledge(20)
    control.acknowledge(25, repeated=True)
    report = control.snapshot()
    assert report['samples'] == 0 and report['fresh_fps'] == 0
    assert report['acknowledged_fps'] == 1
    assert report['cache_hits'] == 1000 and report['repeated_acknowledgements'] == 1
    clock.advance(2100)
    assert control.snapshot()['acknowledged_fps'] == 0


def test_seek_and_target_change_fence_old_measurements_and_preserve_quality():
    clock = Clock()
    control = AdaptiveController(30, clock=clock)
    observe(control, clock, 300)
    epoch, rank = control.epoch, control.rank
    control.reset('seek', target_fps=60)
    assert control.rank == rank
    assert control.observe(900, epoch=epoch, profile_id='Q4') is None
    assert control.snapshot()['samples'] == 0 and control.snapshot()['stale_samples'] == 1
    assert control.target_fps == 60


def test_unsupported_lower_profiles_are_a_visible_floor_and_oom_does_not_claim_success():
    clock = Clock()
    control = AdaptiveController(60, supported_ranks=[0, 3, 4, 5], clock=clock)
    for _ in range(200):
        observe(control, clock, 70)
    assert control.rank == control.floor == 3
    assert control.snapshot()['target_unattainable']
    assert control.allocation_failure(epoch=control.epoch, profile_id='Q3') is None


@pytest.mark.parametrize('value', [True, -1, 0, 14, 121, '30', float('nan')])
def test_invalid_targets_fail(value):
    with pytest.raises(ValueError):
        AdaptiveController(value)


@pytest.mark.parametrize('value', [True, -1, float('inf'), float('nan'), '5'])
def test_invalid_observations_fail(value):
    controller = AdaptiveController()
    with pytest.raises(ValueError):
        controller.observe(value, epoch=controller.epoch, profile_id='Q5')


def project(**updates):
    data = {'schema': 'framerudiments.project.v1', 'width': 640, 'height': 360,
            'audio': {'source': {'path': 'a.wav', 'sha256': 'a' * 64}},
            'clock': {'bpm': 120}, 'layers': [], 'render_quality': {'spatial_scale': 4, 'temporal_samples': 8}}
    data.update(updates)
    return SimpleNamespace(data=data, width=640, height=360,
                           output_quality=SimpleNamespace(enabled=True, spatial_scale=4, temporal_samples=8))


def test_ladder_is_real_geometry_and_preserves_authored_settings_and_source_identity():
    source = project(layers=[{'type': 'sprite', 'id': 'art', 'width': 160, 'center': [320, 180],
                              'bounds': [0, 0, 640, 360], 'mapping': {'dx': 20, 'dy': 10, 'rotation': 20}}])
    before = deepcopy(source.data)
    sizes, samples = [], []
    for rank in range(6):
        derived = profile_document(source, f'Q{rank}')
        sizes.append((derived['width'], derived['height']))
        samples.append(derived['render_quality']['temporal_samples'])
        assert derived['audio'] == before['audio'] and derived['clock'] == before['clock']
        assert derived['layers'][0]['mapping']['rotation'] == 20
    assert sizes == [(160, 90), (320, 180), (480, 270), (640, 360), (640, 360), (640, 360)]
    assert samples == [1, 1, 1, 2, 4, 8]
    assert source.data == before
    assert profile_document(source, 'Q5') == before
    assert profile_document(source, 'Q0')['layers'][0]['center'] == [80, 45]


def test_pixel_kernel_radii_scale_but_normalized_transition_feather_does_not():
    graph = {'nodes': [{'id': 'a', 'type': 'source'},
                      {'id': 'blur', 'type': 'filter', 'inputs': ['a'], 'filter_radius': 4},
                      {'id': 'x', 'type': 'transition', 'inputs': ['a', 'blur'],
                       'transition_style': 'wipe', 'progress': .4, 'feather': .2}], 'output': 'x'}
    p = project(effect_graph=graph)
    result = profile_document(p, 'Q0')['effect_graph']
    assert result['nodes'][1]['filter_radius'] == 1
    assert result['nodes'][2]['feather'] == .2
    assert graph['nodes'][1]['filter_radius'] == 4


@pytest.mark.parametrize('extra', [
    {'effects': [{'engine': 'butterchurn'}]},
    {'post_effects': {'temporal': {'strength': 1}}},
    {'layers': [{'type': 'emitter'}]},
])
def test_stateful_or_unqualified_paths_fail_explicitly(extra):
    p = project(**extra)
    assert not profile_capabilities(p)[5]['supported']
    with pytest.raises(ProfileUnsupported):
        profile_document(p, 'Q5')


def test_unknown_low_resolution_semantics_preserve_full_graph_at_a_capability_floor():
    p = project(effect_graph={'nodes': [{'id': 'x', 'type': 'spatial_warp', 'inputs': ['a']}], 'output': 'x'})
    capabilities = profile_capabilities(p)
    assert [r['supported'] for r in capabilities] == [False, False, False, True, True, True]
    assert profile_document(p, 'Q3')['effect_graph'] == p.data['effect_graph']
    assert all(r['production_equivalent'] is False for r in capabilities)


def test_source_guard_rehashes_admission_detects_rewrites_and_preserves_bytes(tmp_path):
    path = tmp_path / 'source.bin'
    path.write_bytes(b'original')
    pin = {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    guard = PinnedInputGuard(tmp_path, {'audio': pin})
    guard()
    path.write_bytes(b'altered!')
    with pytest.raises(ValueError, match='changed'):
        guard()
    with pytest.raises(ValueError, match='SHA-256'):
        PinnedInputGuard(tmp_path, {'audio': pin})


def test_windows_source_guard_detects_same_size_rewrite_even_when_all_stat_fields_repeat(tmp_path, monkeypatch):
    from framerudiments import preview_quality

    path = tmp_path / 'source.bin'
    path.write_bytes(b'original')
    pin = {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    signature = PinnedInputGuard._signature(path)
    # Reproduce the Windows CI failure on every platform: creation-time ctime,
    # an unchanged last-write timestamp and an in-place same-size rewrite.
    monkeypatch.setattr(preview_quality, '_REHASH_PINNED_REUSE', True)
    monkeypatch.setattr(PinnedInputGuard, '_signature', staticmethod(lambda _path: signature))
    guard = PinnedInputGuard(tmp_path, {'audio': pin})
    guard()
    path.write_bytes(b'altered!')
    assert PinnedInputGuard._signature(path) == signature
    with pytest.raises(ValueError, match='changed'):
        guard()
    assert path.read_bytes() == b'altered!' and pin['sha256'] == sha256(b'original').hexdigest()
    with pytest.raises(ValueError, match='SHA-256'):
        PinnedInputGuard(tmp_path, {'audio': pin})


def test_windows_source_guard_fences_a_change_during_rehash(tmp_path, monkeypatch):
    from framerudiments import preview_quality

    path = tmp_path / 'source.bin'
    path.write_bytes(b'original')
    pin = {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(preview_quality, '_REHASH_PINNED_REUSE', True)
    guard = PinnedInputGuard(tmp_path, {'audio': pin})
    native_hash = preview_quality.file_sha256
    def rewrite_after_read(filename):
        digest = native_hash(filename)
        path.write_bytes(b'changed after the digest read')
        return digest
    monkeypatch.setattr(preview_quality, 'file_sha256', rewrite_after_read)
    with pytest.raises(ValueError, match='changed'):
        guard()


def test_stat_capable_source_guard_keeps_unchanged_reuse_free_of_full_file_reads(tmp_path, monkeypatch):
    from framerudiments import preview_quality

    path = tmp_path / 'source.bin'
    path.write_bytes(b'original')
    pin = {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(preview_quality, '_REHASH_PINNED_REUSE', False)
    guard = PinnedInputGuard(tmp_path, {'audio': pin})
    def forbidden(_path):
        raise AssertionError('unchanged stat-capable reuse must not read whole pinned media')
    monkeypatch.setattr(preview_quality, 'file_sha256', forbidden)
    guard()
    guard()


def test_no_oscillation_around_budget_and_invalid_capabilities():
    clock = Clock()
    control = AdaptiveController(30, clock=clock)
    changes = []
    for i in range(400):
        change = observe(control, clock, 28 if i % 2 else 32)
        if change:
            changes.append(change)
    assert all(c['reason'] != 'sustained_headroom' for c in changes)
    assert control.rank == 0
    with pytest.raises(ValueError):
        AdaptiveController(30, supported_ranks=[0, 1])


def test_native_setup_is_reported_but_does_not_trigger_quality_rebuild_loop():
    clock = Clock()
    control = AdaptiveController(60, clock=clock)
    clock.advance(605)
    assert control.observe(605, setup_ms=600, epoch=control.epoch, profile_id='Q5') is None
    snapshot = control.snapshot()
    assert snapshot['render_ms_p95'] == 5
    assert snapshot['total_service_ms_p95'] == 605
    assert snapshot['setup_ms_last'] == 600
    assert snapshot['fresh_fps'] < 2
    with pytest.raises(ValueError, match='setup time exceeds'):
        control.observe(3, setup_ms=4, epoch=control.epoch, profile_id='Q5')
