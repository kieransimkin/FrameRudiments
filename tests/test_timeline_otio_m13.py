"""M13 exact-frame manifest, source provenance and fail-closed interchange tests."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from framerudiments.timeline_otio_m13 import (SCHEMA, _publish, manifest_file, project_manifest,
                                               summary, validate, verify_sources)
from framerudiments.timeline_otio_cli import main as command


SOURCE = "a" * 64


def document(*, fps=(30000, 1001), source_fps=(30000, 1001), duration=120):
    return {"schema": SCHEMA, "name": "Exact rational cut",
            "fps": list(fps), "tracks": [
                {"id": "picture", "kind": "video", "items": [
                    {"type": "clip", "id": "shotA", "name": "Shot A",
                     "source": {"uri": "media/a.mov", "sha256": SOURCE},
                     "source_fps": list(source_fps), "source_start_frame": 240,
                     "duration_frames": duration},
                    {"type": "transition", "id": "cross", "style": "dissolve",
                     "in_frames": 12, "out_frames": 8},
                    {"type": "clip", "id": "shotB", "name": "Shot B",
                     "source": {"uri": "media/b.mov", "sha256": SOURCE},
                     "source_fps": list(source_fps), "source_start_frame": 0,
                     "duration_frames": duration},
                    {"type": "gap", "id": "tail", "duration_frames": 30},
                ]},
                {"id": "soundtrack", "kind": "audio", "items": [
                    {"type": "gap", "id": "audio_silence", "duration_frames": 270},
                ]},
            ], "markers": [
                {"id": "drop", "name": "DROP 1", "frame": 120,
                 "duration_frames": 0, "color": "YELLOW"},
                {"id": "outro", "name": "OUTRO", "frame": 240,
                 "duration_frames": 30, "color": "BLUE"},
            ]}


def test_exact_ntsc_rate_normalised_without_resampling():
    doc = document()
    value = validate(doc)
    assert value == doc
    assert value is not doc and value["tracks"] is not doc["tracks"]
    receipt = summary(value)
    assert receipt["fps"] == [30000, 1001]
    assert receipt["duration_seconds"] == [9009, 1000]
    assert (receipt["clips"], receipt["transitions"], receipt["markers"]) == (2, 1, 2)


def test_audio_sample_clock_uses_source_ticks_not_fake_fps():
    d = document(fps=(60, 1), source_fps=(16000, 1), duration=60)
    d["tracks"] = [d["tracks"][0]]
    d["tracks"][0]["items"] = [d["tracks"][0]["items"][0]]
    d["tracks"][0]["kind"] = "audio"
    d["markers"] = []
    assert validate(d)["tracks"][0]["items"][0]["source_fps"] == [16000, 1]


def test_off_grid_media_frames_never_round_silently():
    d = document(fps=(30, 1), source_fps=(24, 1), duration=91)
    with pytest.raises(ValueError, match="media source frame grid"):
        validate(d)


@pytest.mark.parametrize("field,value", [
    ("fps", [0, 1]), ("fps", [30000, 0]), ("fps", [29.97, 1]),
    ("fps", [30000, True]), ("fps", [150, 1]), ("name", ""),
    ("schema", "unknown"), ("markers", [{}]), ("tracks", []),
])
def test_root_schema_rejects_invalid_fields(field, value):
    bad = document()
    bad[field] = value
    with pytest.raises(ValueError):
        validate(bad)


@pytest.mark.parametrize("uri", [
    "../media/a.mov", "media/../a.mov", "./a.mov", "/etc/passwd",
    "file:///home/user/a.mov", "https://example.test/a.mov", "media//a.mov",
    "C:\\media\\x.mov", "media/a%2Fmov", "media/a.mov?token=1", "",
])
def test_invalid_media_uris_are_rejected(uri):
    d = document()
    d["tracks"][0]["items"][0]["source"]["uri"] = uri
    with pytest.raises(ValueError, match="media uri"):
        validate(d)


def test_unsigned_media_fails_closed_by_default():
    d = document()
    d["tracks"][0]["items"][0]["source"]["sha256"] = None
    with pytest.raises(ValueError, match="Unpinned"):
        validate(d)
    assert summary(validate(d, allow_unpinned=True))["unverified_media"] == 1


def test_transition_must_have_adjacent_clips_and_positive_bounded_overlap():
    d = document()
    d["tracks"][0]["items"][1]["in_frames"] = 121
    with pytest.raises(ValueError, match="offsets exceed"):
        validate(d)
    d = document()
    d["tracks"][0]["items"][1]["in_frames"] = 0
    d["tracks"][0]["items"][1]["out_frames"] = 0
    with pytest.raises(ValueError, match="nonzero"):
        validate(d)
    d = document()
    d["tracks"][0]["items"].insert(2, {"type": "gap", "id": "wrong", "duration_frames": 10})
    with pytest.raises(ValueError, match="precede a clip"):
        validate(d)


def test_global_markers_reject_out_of_bounds_and_duplicate_identity():
    d = document()
    d["markers"][1]["frame"] = 269
    with pytest.raises(ValueError, match="extends beyond"):
        validate(d)
    d = document()
    d["markers"][1]["id"] = "drop"
    with pytest.raises(ValueError, match="Duplicate marker"):
        validate(d)


def test_duplicate_json_fields_and_nan_are_not_accepted(tmp_path):
    file = tmp_path / "timeline.json"
    file.write_text('{"schema":"%s","schema":"%s"}' % (SCHEMA, SCHEMA))
    with pytest.raises(ValueError, match="Duplicate"):
        manifest_file(file)
    file.write_text(json.dumps(document()).replace('"fps": [30000, 1001]', '"fps": NaN'))
    with pytest.raises(ValueError, match="Invalid JSON numeric"):
        manifest_file(file)


def test_source_verification_guards_symlinks_changes_and_returns_receipt(tmp_path):
    folder = tmp_path / 'media'
    folder.mkdir()
    source = folder / 'a.mov'
    source.write_bytes(b'export is not media interpretation')
    d = document()
    d["tracks"] = [d["tracks"][0]]
    d["tracks"][0]["items"] = [d["tracks"][0]["items"][0]]
    d["markers"] = []
    d["tracks"][0]["items"][0]["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    assert verify_sources(d, tmp_path)[0]["size_bytes"] == len(source.read_bytes())
    source.write_bytes(b'changed media')
    with pytest.raises(ValueError, match="differs"):
        verify_sources(d, tmp_path)
    source.unlink()
    outside = tmp_path.parent / ('outside-' + tmp_path.name)
    outside.write_bytes(b'other')
    try:
        source.symlink_to(outside)
    except OSError:
        # Symlink creation is restricted on some Windows CI configurations.
        outside.unlink()
        return
    with pytest.raises(ValueError, match="escapes root"):
        verify_sources(d, tmp_path)
    outside.unlink()


def test_atomic_no_overwrite_and_receipt_rollback(tmp_path, monkeypatch):
    out = tmp_path / 'new.otio'
    payload = b'hello otio'
    report = {"schema": SCHEMA, "status": "test"}
    result = _publish(out, payload, report)
    assert out.read_bytes() == payload
    assert Path(result["receipt"]).is_file()
    with pytest.raises(ValueError, match="already exists"):
        _publish(out, b'bad', report)
    assert out.read_bytes() == payload
    second = tmp_path / 'rollback.otio'
    real_link = os.link
    calls = 0

    def fail_after_first(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('publication failure')
        return real_link(src, dst)

    monkeypatch.setattr(os, 'link', fail_after_first)
    with pytest.raises(OSError):
        _publish(second, payload, report)
    assert not second.exists() and not (tmp_path / 'rollback.otio.receipt.json').exists()
    assert not list(tmp_path.glob('.fr-m13-*'))


def test_native_project_export_only_exact_grid_and_real_source(tmp_path):
    media = tmp_path / 'tone.wav'
    media.write_bytes(b'private synthetic test')
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    p = SimpleNamespace(base=tmp_path, audio_ref={"path": str(media), "sha256": digest},
        slice_duration=Fraction(8), fps=60, repeats=2, frame_count=960,
        sample_rate=16000, start_sample=240, cues=[
            {"text": "DROP", "frame": 480}, {"text": "", "frame": 700}],
        data={"title": "Synthetic project"}, verify_inputs=lambda: None)
    d = project_manifest(p)
    assert len(d['tracks'][0]['items']) == 2
    assert [i['source_start_frame'] for i in d['tracks'][0]['items']] == [240, 240]
    assert d['markers'][0]['frame'] == 480
    assert d['tracks'][0]['items'][0]['source_fps'] == [16000, 1]
    p.slice_duration = Fraction(1001, 30000)
    with pytest.raises(ValueError, match="does not align"):
        project_manifest(p)


def test_cli_validates_manifest_and_reports_exact_clock(tmp_path, capsys):
    path = tmp_path / 'timeline.json'
    path.write_text(json.dumps(document()))
    assert command(['validate', str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['frames'] == 270
    assert command(['import', 'fake.otio', '--output', str(tmp_path / 'out.json')]) == 2


def test_normalization_does_not_mutate_source_data():
    original = document()
    before = deepcopy(original)
    validate(original)
    assert original == before


def test_nonhashable_fields_produce_validation_error_not_type_error():
    d = document()
    d["tracks"][0]["items"][0]["type"] = []
    with pytest.raises(ValueError, match="Unsupported"):
        validate(d)
    d = document()
    d["markers"][0]["color"] = []
    with pytest.raises(ValueError, match="marker colour"):
        validate(d)


def test_otio_transition_in_offset_belongs_to_incoming_clip():
    d = document(fps=(60, 1), source_fps=(60, 1), duration=30)
    d["tracks"][0]["items"][0]["duration_frames"] = 5  # outgoing
    d["tracks"][0]["items"][1]["in_frames"] = 20  # incoming has 30 frames
    d["tracks"][0]["items"][1]["out_frames"] = 4
    d["markers"] = []
    assert validate(d)["tracks"][0]["items"][1]["in_frames"] == 20
    d["tracks"][0]["items"][1]["out_frames"] = 6
    with pytest.raises(ValueError, match="offsets exceed"):
        validate(d)
