"""M15.2 contract regression tests; no native GStreamer installation required."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments import gstreamer_ingest_m15 as ingest


def _line(n, pts):
    return (f'Got message #100 from element "multifilesink0" (element): GstMultiFileSink, '
            f'filename=(string)frame-{n:06d}.png, index=(int){n}, '
            f'timestamp=(guint64){pts}, stream-time=(guint64){pts};')


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_gstreamer_message_parser_preserves_actual_variable_pts():
    values = ingest._messages('\n'.join(_line(i, v) for i, v in enumerate([0, 40_000_000, 117_000_000])))
    assert values == [(0, 0), (1, 40_000_000), (2, 117_000_000)]


@pytest.mark.parametrize("invalid", [
    "", "GstMultiFileSink, fake", _line(1, 0), _line(0, 0) + "\n" + _line(0, 1),
    _line(0, 2) + "\n" + _line(1, 1), _line(0, (1 << 64) - 1),
    _line(0, 1 << 63), _line(0, 0).replace('frame-000000', 'frame-000010'),
])
def test_gstreamer_messages_fail_closed(invalid):
    with pytest.raises(ValueError):
        ingest._messages(invalid)


def test_pts_comparison_uses_exact_video_presentation_not_nominal_fps():
    fake = SimpleNamespace(frame_count=3, timestamps=(Fraction(0), Fraction(1, 30), Fraction(1, 10)))
    assert ingest._verify_pts([(0, 0), (1, 33_333_333), (2, 100_000_000)], fake) is None
    with pytest.raises(ValueError, match="PTS"):
        ingest._verify_pts([(0, 0), (1, 33_333_340), (2, 100_000_000)], fake)
    with pytest.raises(ValueError, match="count"):
        ingest._verify_pts([(0, 0)], fake)


def test_rational_audio_clock_never_resamples_or_uses_floating_seconds():
    value = ingest.reconcile_audio_clock(['0', '1001/30000', '1/15'], sample_rate=48000)
    assert value['samples'][0]['nearest_audio_sample'] == 0
    assert value['samples'][1]['nearest_audio_sample'] == 1602
    assert value['samples'][1]['quantisation_error_samples'] == '2/5'
    assert value['audio_resampled'] is False
    assert value['clock_alignment_only'] is True
    assert ingest.reconcile_audio_clock(['0', '1/2'], sample_rate=10)['samples'][1]['nearest_audio_sample'] == 5


def test_audio_clock_negative_offset_and_bounds():
    result = ingest.reconcile_audio_clock(['-1/60', '0'], sample_rate=48000, audio_origin='-1/30')
    assert [r['nearest_audio_sample'] for r in result['samples']] == [800, 1600]
    with pytest.raises(ValueError, match="quantisation"):
        ingest.reconcile_audio_clock(['1001/30000'], sample_rate=48000,
                                     max_error_samples='1/10')
    with pytest.raises(ValueError):
        ingest.reconcile_audio_clock(['0'], sample_rate=0)
    with pytest.raises(ValueError):
        ingest.reconcile_audio_clock([0], sample_rate=48000)
    with pytest.raises(ValueError):
        ingest.reconcile_audio_clock([], sample_rate=48000)


@pytest.mark.parametrize("opts", [
    {"source": "rtsp", "rtsp_url": "rtsp://127.0.0.1/live"},
    {"source": "rtsp", "rtsp_url": "rtsp://example.org/live", "allow_network": True},
    {"source": "rtsp", "rtsp_url": "rtsp://127.0.0.1/a!x", "allow_network": True},
    {"source": "rtsp", "rtsp_url": "rtsp://127.0.0.1:abc/live", "allow_network": True},
    {"source": "rtsp", "rtsp_url": "rtsp://user:pass@127.0.0.1/live", "allow_network": True},
    {"source": "test", "allow_network": True},
    {"source": "test", "device": "/dev/video0"},
    {"source": "v4l2", "device": "/dev/video999"},
    {"source": "invalid"},
])
def test_live_source_requires_explicit_trusted_capabilities(opts):
    with pytest.raises(ValueError):
        ingest._admit_live_source(opts["source"], **{k: v for k, v in opts.items() if k != "source"})


def test_rational_live_test_and_explicit_loopback_rtsp():
    src, fps = ingest._admit_live_source('test', limit=10, fps='30000/1001', width=80, height=64)
    assert src[0] == 'videotestsrc' and 'is-live=true' in src
    assert fps == Fraction(30000, 1001)
    src, _ = ingest._admit_live_source('rtsp', rtsp_url='rtsp://127.0.0.1:8554/live',
                                      allow_network=True)
    assert src[:2] == ['rtspsrc', 'location=rtsp://127.0.0.1:8554/live']
    assert 'protocols=tcp' in src


def test_raw_image_resource_gates(tmp_path):
    with pytest.raises(ValueError):
        ingest._bounded_dimensions(3840, 2160, 256)
    with pytest.raises(ValueError):
        ingest._bounded_dimensions(128, 64, 257)
    with pytest.raises(ValueError):
        ingest._target_directory(tmp_path)
    with pytest.raises(ValueError):
        ingest._target_directory(tmp_path / 'missing-parent' / 'entry')


def test_pinned_frame_readback_and_manifest(tmp_path):
    for n in range(3):
        Image.new('RGB', (32, 24), (n, 20, 30)).save(tmp_path / f'frame-{n:06d}.png')
    rows = ingest._frame_evidence(tmp_path, [(i, i * 10) for i in range(3)], width=32, height=24)
    assert len(rows) == 3 and rows[0]['sha256'] == _hash(tmp_path / 'frame-000000.png')
    (tmp_path / 'extra.png').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='unexpected'):
        ingest._frame_evidence(tmp_path, [(i, i * 10) for i in range(3)], width=32, height=24)


def test_live_reconnect_discards_incomplete_attempt_and_publishes_only_full(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, '_launcher', lambda *_: 'gst')
    calls = []

    def fake_run(command, *, cwd, timeout):
        calls.append((command, cwd))
        count = 2 if len(calls) == 1 else 3
        for index in range(count):
            Image.new('RGB', (32, 32), (index * 50, 10, 50)).save(cwd / f'frame-{index:06d}.png')
        return '\n'.join(_line(i, i * 33_333_333) for i in range(count))

    monkeypatch.setattr(ingest, '_run', fake_run)
    target = tmp_path / 'capture'
    proof = ingest.capture_live(target, source='test', limit=3, width=32, height=32, retries=1)
    assert len(calls) == 2 and proof['reconnections'] == 1
    assert len(proof['frames']) == 3 and (target / 'index.json').is_file()
    assert not list(tmp_path.glob('.fr-m15-live-*'))
    assert json.loads((target / 'index.json').read_text())['source'] == 'test'
    with pytest.raises(ValueError):
        ingest.capture_live(target, source='test', limit=3)


def test_live_failed_reconnect_never_publishes_partial_output(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, '_launcher', lambda *_: 'gst')
    monkeypatch.setattr(ingest, '_run', lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError('late')))
    with pytest.raises(TimeoutError):
        ingest.capture_live(tmp_path / 'failed', source='test', limit=3, retries=2)
    assert not (tmp_path / 'failed').exists()
    assert not list(tmp_path.glob('.fr-m15-live-*'))


def test_offline_rejects_modified_and_unpinned_files_before_gstreamer(tmp_path, monkeypatch):
    video = tmp_path / 'source.mp4'
    video.write_bytes(b'mock')
    monkeypatch.setattr(ingest, '_launcher', lambda *_: pytest.fail('GST must not start'))
    with pytest.raises(ValueError, match='SHA-256'):
        ingest.ingest_video(video, tmp_path / 'decoded', sha256='0' * 64)
    with pytest.raises(ValueError, match='both'):
        ingest.ingest_video(video, tmp_path / 'decoded', sha256=_hash(video),
                            audio_source=video, audio_sha256=None)


def test_ingest_probe_never_opens_device_or_network_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, '_executable', lambda value, fallback: None)
    receipt = ingest.ingest_capabilities()
    assert receipt['available'] is False and receipt['device_opened'] is False
    assert receipt['network_opened'] is False


def test_native_capture_timeout_stops_owned_child(tmp_path):
    import sys
    with pytest.raises(TimeoutError, match='deadline'):
        ingest._run([sys.executable, '-c', 'import time;time.sleep(8)'], cwd=tmp_path, timeout=1)


def test_live_eos_counts_published_frames_not_terminal_trigger():
    # identity's eos-after=N triggers before forwarding buffer N.
    stages = ingest._video_pipeline(live=True, limit=6)
    assert 'eos-after=7' in stages
    assert 'post-messages=true' in stages
