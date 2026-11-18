"""Public timeline gallery audio, site boundary and provenance regressions."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import wave

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from generate_timeline_gallery_audio import generate, verify, SAMPLE_RATE, DURATION_SECONDS  # noqa: E402


def test_synthetic_audio_has_exact_beat_grid_and_is_not_silent(tmp_path):
    a = tmp_path / 'a'
    b = tmp_path / 'b'
    left, right = generate(a), generate(b)
    assert left == right
    assert left['sha256'] == hashlib.sha256((a/'synthetic-click.wav').read_bytes()).hexdigest()
    with wave.open(str(a / 'synthetic-click.wav'), 'rb') as recording:
        assert recording.getnframes() == SAMPLE_RATE * DURATION_SECONDS
        samples = struct.unpack('<' + 'h' * recording.getnframes(), recording.readframes(recording.getnframes()))
    assert len(set(samples)) > 500
    assert max(abs(x) for x in samples) <= 32767
    assert left['source'] == 'generated_original_synthetic_audio'


def test_no_overwrite_and_reject_tampered_audio(tmp_path):
    folder = tmp_path / 'site'
    generate(folder)
    with pytest.raises(ValueError, match='already exist'):
        generate(folder)
    (folder / 'synthetic-click.wav').write_bytes(b'tampered')
    (folder / 'index.html').write_text('<div id="root"></div>', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA-256'):
        verify(folder)


def test_manifest_refuses_private_backend_or_invalid_bundle(tmp_path):
    folder = tmp_path / 'site'
    generate(folder)
    page = folder / 'index.html'
    page.write_text('<div id="root"></div><script src="/FrameRudiments/timeline/assets/index.js"></script>',
                    encoding='utf-8')
    assert verify(folder)['offline_only']
    doc = json.loads((folder / 'catalogue.json').read_text(encoding='utf-8'))
    doc['no_native_preview_api'] = False
    (folder / 'catalogue.json').write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(ValueError, match='public timeline'):
        verify(folder)


def test_static_entry_never_references_authoring_http_endpoints():
    source = (ROOT / 'authoring/src/TimelineGallery.tsx').read_text(encoding='utf-8')
    for name in ('/api/', 'fetch(', 'EventSource', 'WebSocket', 'localStorage'):
        assert name not in source
    assert 'TimelineSequence' in source
    assert 'onTimeChange' in source
    assert 'onEdit' in source
    assert 'synthetic-click.wav' in source
    assert 'NativeFramePreview' not in source
