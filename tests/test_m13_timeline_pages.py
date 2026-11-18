"""Public OTIO example must match the real hosted synthetic timeline source."""

import hashlib
import json
import wave

import pytest

from scripts.generate_m13_timeline_example import example, build, verify
from scripts.generate_timeline_gallery_audio import generate
from framerudiments.timeline_otio_m13 import validate


def test_generated_audio_and_exact_cues_are_safe_to_publish(tmp_path):
    generate(tmp_path)
    doc = validate(example(tmp_path))
    assert [m['frame'] for m in doc['markers']] == [0, 240, 480, 720]
    assert doc['tracks'][0]['items'][0]['source_fps'] == [16000, 1]
    with wave.open(str(tmp_path / 'synthetic-click.wav'), 'rb') as wav:
        assert wav.getnframes() == 16 * 16000
    assert doc['tracks'][1]['items'][0]['type'] == 'gap'


def test_mutated_audio_cannot_publish(tmp_path):
    generate(tmp_path)
    with (tmp_path / 'synthetic-click.wav').open('ab') as dest:
        dest.write(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        example(tmp_path)


def test_true_otio_gallery_generation_and_reimport(tmp_path):
    pytest.importorskip('opentimelineio')
    generate(tmp_path)
    result = build(tmp_path)
    assert (tmp_path / 'example.otio').is_file()
    assert result['sha256'] == hashlib.sha256((tmp_path / 'example.otio').read_bytes()).hexdigest()
    assert verify(tmp_path)['native_roundtrip'] == 'exact'
    assert json.loads((tmp_path / 'example.timeline.json').read_text()) == example(tmp_path)
    with pytest.raises(ValueError, match='already exists'):
        build(tmp_path)
