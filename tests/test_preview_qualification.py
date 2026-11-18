"""S7 evidence semantics on the real coordinator and persistent pixel cache."""
from io import BytesIO
import json
from threading import Event

from PIL import Image
import pytest

from framerudiments.preview_qualification import MAX_HISTORY, MAX_SAMPLES, StreamQualification
from framerudiments.preview_stream_s2 import SeekStream
from test_preview_adaptive import Clock
from test_preview_adaptive_stream import ready, store, png
from framerudiments.preview_quality import profile_capabilities
from test_preview_stream_s2 import image, make_cache, wait_ready


def test_seek_server_ready_and_decode_ack_timings_are_distinct(tmp_path):
    cache = make_cache(tmp_path)
    clock = Clock()
    def render(frame):
        clock.advance(50)
        return image(frame)
    stream = SeekStream(cache, render, 1001, clock=clock)
    try:
        stream.command('seek', sequence=0, frame=8)
        row = wait_ready(stream, 0)
        clock.advance(25)
        assert stream.acknowledge(0, 8, row['image_sha256'])
        clock.advance(425)
        stream.command('seek', sequence=1, frame=8)
        cached = wait_ready(stream, 1)
        assert cached['cache_hit']
        clock.advance(10)
        assert stream.acknowledge(1, 8, cached['image_sha256'])
        assert not stream.acknowledge(1, 8, cached['image_sha256'])
        assert not stream.acknowledge(0, 8, row['image_sha256'])
        receipt = stream.qualification()
        assert receipt['latency_ms']['seek_first_frame'] == {'p50': 0, 'p95': 50, 'samples': 2}
        assert receipt['latency_ms']['seek_ack']['p95'] == 0
        assert receipt['latency_ms']['seek_decode'] == {'p50': 10, 'p95': 75, 'samples': 2}
        assert receipt['latency_ms']['render']['p95'] == 50
        assert receipt['cache']['hits'] == receipt['cache']['misses'] == 1
        assert receipt['counters']['foreground_completed'] == 1
        assert receipt['counters']['decoded_acknowledgements'] == 2
        assert receipt['counters']['repeated_acknowledgements'] == 1
        assert receipt['rates']['fresh_fps'] == receipt['rates']['decoded_fps'] == round(1/.510, 3)
        assert receipt['measurement']['seek_first_frame_scope'] == 'server_frame_ready'
        assert receipt['measurement']['seek_decode_scope'] == 'server_seek_to_decode_ack'
        assert not receipt['measurement']['physical_display']
    finally:
        stream.close()


def test_q5_coverage_and_upgrade_counters_track_current_index_and_session(tmp_path):
    cache = make_cache(tmp_path)
    observer = StreamQualification(cache, 1001, 30)
    def publish(tier):
        ticket = cache.ticket(1, tier)
        buffer = BytesIO()
        Image.new('RGBA', (ticket.width, ticket.height), (1, 20, 50, 255)).save(buffer, 'PNG')
        return cache.publish(ticket, buffer.getvalue(), settings_sha256='e'*64)
    assert publish('Q0') == 'inserted'
    assert publish('Q5') == 'upgraded'
    current = observer.snapshot(full=True, refresh=True)
    assert current['cache']['frames'] == current['cache']['q5_frames'] == current['cache']['upgrades'] == 1
    next_session = StreamQualification(cache, 1001, 30)
    assert next_session.snapshot()['cache']['upgrades'] == 0
    assert next_session.snapshot()['cache']['q5_frames'] == 1
    cache.invalidate_all()
    revoked = observer.snapshot(full=True, refresh=True)
    assert revoked['cache']['frames'] == revoked['cache']['q5_frames'] == revoked['cache']['bytes'] == 0
    assert revoked['cache']['upgrades'] == 1
    assert revoked['segments'][0]['generation'] > current['segments'][0]['generation']


def test_status_probes_are_throttled_and_export_is_fresh(tmp_path, monkeypatch):
    cache = make_cache(tmp_path)
    clock, calls = Clock(), []
    real_stats = cache.stats
    def stats():
        calls.append(clock())
        return real_stats()
    monkeypatch.setattr(cache, 'stats', stats)
    observer = StreamQualification(cache, 1001, 30, clock=clock)
    for _ in range(120):
        observer.snapshot()
    assert len(calls) == 1  # Controls do not run storage scans at target FPS.
    clock.advance(1000)
    observer.snapshot()
    assert len(calls) == 2
    observer.snapshot(full=True, refresh=True)
    assert len(calls) == 3


def test_histories_and_timing_windows_are_bounded_and_do_not_export_text(tmp_path):
    clock = Clock()
    observer = StreamQualification(make_cache(tmp_path), 1001, 30, clock=clock)
    for number in range(MAX_SAMPLES + 40):
        clock.advance(300)
        observer.command(number, number, 'seek', received_at=clock())
        observer.command_ack('seek', clock())
        observer.rendered(render_ms=5, queue_ms=0)
        observer.ready(number, number)
        observer.acknowledged(repeated=False)
        observer.transition('Q5', 'Q4', 'p95_over_budget')
    observer.transition('Q5', 'Q4', '/private/song-name')
    observer.invalidated({'cause': '/private/song-name', 'changed_dependencies': ['Secret lyrics'],
                         'affected_sections': [], 'revision': 1})
    receipt = observer.snapshot(full=True, refresh=True)
    assert receipt['latency_ms']['render']['samples'] == MAX_SAMPLES
    assert len(receipt['transitions']) == MAX_HISTORY
    assert receipt['history_dropped']['transitions'] == MAX_SAMPLES + 40 - MAX_HISTORY
    assert receipt['invalidations'][-1]['cause'] == 'unknown'
    assert all(value is False for value in receipt['release_gates'].values())
    assert receipt['resources']['scope'] == 'preview_server_process'
    assert receipt['resources']['gpu_memory_bytes'] is None
    assert not any(s in json.dumps(receipt) for s in ('/private/', 'Secret lyrics', str(tmp_path)))
    receipt['segments'][0]['generation'] = 100
    receipt['resources']['scope'] = 'all_hardware'
    again = observer.snapshot(full=True)
    assert again['segments'][0]['generation'] == 0
    assert again['resources']['scope'] == 'preview_server_process'


def test_saturated_rate_buffer_is_unavailable_instead_of_undercounting(tmp_path):
    clock = Clock()
    observer = StreamQualification(make_cache(tmp_path), 1001, 30, clock=clock,
                                   acknowledgement_source='harness_png_decode')
    clock.advance(500)
    for _ in range(MAX_SAMPLES):
        observer.acknowledged(repeated=False)
    receipt = observer.snapshot()
    assert receipt['rates']['decoded_fps'] is None
    assert receipt['rates']['acknowledgement_source'] == 'harness_png_decode'
    clock.advance(3100)
    assert observer.snapshot()['rates']['decoded_fps'] == 0


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, True, 'secret text'])
def test_nonfinite_timings_cannot_enter_export(tmp_path, bad):
    observer = StreamQualification(make_cache(tmp_path), 1001, 30)
    with pytest.raises(ValueError, match='finite nonnegative'):
        observer.rendered(render_ms=bad, queue_ms=0)
    assert observer.snapshot()['latency_ms']['render']['samples'] == 0


def test_stale_foreground_work_never_inflates_completion_or_latency_evidence(tmp_path):
    started, release = Event(), Event()
    def render(frame):
        if frame == 1:
            started.set()
            assert release.wait(4)
        return image(frame)
    stream = SeekStream(make_cache(tmp_path), render, 1001)
    try:
        stream.command('seek', sequence=1, frame=1)
        assert started.wait(3)
        for sequence in range(2, 20):
            stream.command('seek', sequence=sequence, frame=sequence)
        release.set()
        wait_ready(stream, 19)
        receipt = stream.qualification()
        assert receipt['counters']['commands'] == 19
        assert receipt['counters']['stale_results'] == 1
        assert receipt['counters']['coalesced_requests'] >= 17
        assert receipt['counters']['foreground_completed'] == 1
        assert receipt['latency_ms']['render']['samples'] == 1
        assert receipt['latency_ms']['seek_first_frame']['samples'] == 1
    finally:
        release.set()
        stream.close()


def test_actual_controller_decisions_are_retained_and_background_does_not_train_rates(tmp_path):
    cache, native = store(tmp_path)
    clock = Clock()
    def render(frame, tier):
        clock.advance(70)
        profile = cache.profiles[int(tier[1:])]
        return png((profile.output_width, profile.output_height), frame)
    stream = SeekStream(cache, render, 1001, 60, capabilities=profile_capabilities(native), clock=clock)
    try:
        for frame in range(14):
            stream.command('advance', sequence=frame, frame=frame)
            ready(stream, frame)
        with stream.condition:
            stream.observations.rendered(render_ms=0, queue_ms=0, origin='refinement')
        receipt = stream.qualification()
        assert receipt['transitions'][0]['from_profile'] == 'Q5'
        assert receipt['transitions'][0]['to_profile'] == 'Q4'
        assert receipt['transitions'][0]['reason'] == 'p95_over_budget'
        assert receipt['counters']['foreground_completed'] == receipt['latency_ms']['render']['samples'] == 14
        assert receipt['counters']['background_completed'] == 1
    finally:
        stream.close()
