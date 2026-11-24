"""Required in the M13 CI workflow: actual OpenTimelineIO 0.18 read/write."""

from copy import deepcopy
import hashlib
import json

import pytest

from framerudiments.timeline_otio_m13 import (SCHEMA, _decoded, _encoded, export_otio,
                                               from_otio, import_otio, to_otio, validate)

otio = pytest.importorskip("opentimelineio")


def example(rate=(30000, 1001)):
    source = {"uri": "video/shot.mov", "sha256": "3" * 64}
    return {"schema": SCHEMA, "name": "Professional interchange", "fps": list(rate),
            "tracks": [{"id": "V1", "kind": "video", "items": [
                {"type": "clip", "id": "a", "name": "First shot", "source": source,
                 "source_fps": list(rate), "source_start_frame": 140, "duration_frames": 60},
                {"type": "transition", "id": "fx", "style": "dissolve", "in_frames": 9, "out_frames": 13},
                {"type": "clip", "id": "b", "name": "Second shot", "source": source,
                 "source_fps": list(rate), "source_start_frame": 400, "duration_frames": 120},
                {"type": "gap", "id": "tail", "duration_frames": 20},
            ]}], "markers": [{"id": "drop", "name": "Drop", "frame": 60,
                              "duration_frames": 0, "color": "YELLOW"}]}


@pytest.mark.parametrize("rate", ((24, 1), (30000, 1001), (60000, 1001)))
def test_real_otio_serialized_roundtrip_is_exact(rate):
    original = example(rate)
    source = deepcopy(original)
    timeline = to_otio(original)
    assert len(timeline.tracks) == 1
    assert len(timeline.tracks[0]) == 4
    assert timeline.tracks[0][1].transition_type == otio.schema.TransitionTypes.SMPTE_Dissolve
    assert timeline.tracks[0][0].media_reference.target_url == 'video/shot.mov'
    assert from_otio(timeline) == validate(original)
    encoded = _encoded(timeline)
    assert 'Transition' in encoded and 'RationalTime' in encoded
    assert from_otio(_decoded(encoded)) == validate(original)
    assert original == source  # inputs are never changed


def test_actual_source_audio_rate_and_two_video_tracks(tmp_path):
    d = example((60, 1))
    wav = tmp_path / 'demo.wav'
    wav.write_bytes(b'local synthetic media fixture')
    sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    d['tracks'].append({'id': 'A1', 'kind': 'audio', 'items': [
        {'type': 'clip', 'id': 'tone', 'name': 'Test tone',
         'source': {'uri': 'demo.wav', 'sha256': sha},
         'source_fps': [16000, 1], 'source_start_frame': 0, 'duration_frames': 201},
    ]})
    a = to_otio(d)
    # 201/60 seconds is exactly 53,600 samples at 16 kHz. A 200-frame
    # timeline span would end between samples and must remain inadmissible.
    assert a.tracks[1][0].source_range.duration.value == 53600
    assert a.tracks[1][0].source_range.duration.rate == 16000
    restored = from_otio(_decoded(_encoded(a)))
    assert restored == validate(d)
    target = tmp_path / 'example.otio'
    receipt = export_otio(d, target)
    assert target.exists() and receipt['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
    result_path = tmp_path / 'reimport.json'
    outcome = import_otio(target, result_path)
    assert outcome['sha256'] == hashlib.sha256(result_path.read_bytes()).hexdigest()
    assert json.loads(result_path.read_text()) == restored


def test_otio_import_without_private_fps_requires_explicit_rate():
    timeline = otio.schema.Timeline(name='Third-party')
    track = otio.schema.Track(name='V1', kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    clip = otio.schema.Clip(name='Unpinned')
    clip.source_range = otio.opentime.TimeRange(
        otio.opentime.RationalTime(0, 24), otio.opentime.RationalTime(48, 24))
    clip.media_reference = otio.schema.ExternalReference(target_url='footage.mp4')
    track.append(clip)
    with pytest.raises(ValueError, match='explicit --fps'):
        from_otio(timeline, allow_unpinned=True)
    with pytest.raises(ValueError, match='Unpinned'):
        from_otio(timeline, fps=[24, 1])
    restored = from_otio(timeline, fps=[24, 1], allow_unpinned=True)
    assert restored['tracks'][0]['items'][0]['source']['sha256'] is None
    assert restored['tracks'][0]['items'][0]['duration_frames'] == 48
    assert to_otio(restored, allow_unpinned=True)


def test_reject_effected_clips_and_nonzero_global_start():
    native = to_otio(example((24, 1)))
    native.global_start_time = otio.opentime.RationalTime(120, 24)
    with pytest.raises(ValueError, match='global'):
        from_otio(native)
    native.global_start_time = None
    native.tracks[0][0].effects.append(otio.schema.Effect(name='Unknown'))
    with pytest.raises(ValueError, match='effects'):
        from_otio(native)


def test_wipe_is_explicitly_custom_not_an_undocumented_gl_shader():
    d = example((60, 1))
    d['tracks'][0]['items'][1]['style'] = 'wipe'
    native = to_otio(d)
    assert native.tracks[0][1].transition_type == otio.schema.TransitionTypes.Custom
    assert from_otio(_decoded(_encoded(native))) == d


def test_off_grid_24fps_source_at_30fps_fails_before_otio_encoding():
    d = example((30, 1))
    d['tracks'][0]['items'][0]['source_fps'] = [24, 1]
    d['tracks'][0]['items'][0]['duration_frames'] = 61
    with pytest.raises(ValueError, match='source frame grid'):
        to_otio(d)
