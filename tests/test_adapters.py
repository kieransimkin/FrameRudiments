import pytest
import dancerudiments as dr
from framerudiments.io import write_json, sha256
from framerudiments.motion import Motion
from framerudiments.timeline import BeatClock
from framerudiments.effects import atlas_cell, directional_trail, transformed_bounds
from PIL import Image


def ref(path):
    return dict(path=str(path), sha256=sha256(path))


def test_saved_native_export_parity_and_explicit_interpolation(tmp_path):
    path = tmp_path / "native.json"
    samples = []
    for pip in range(256):
        v = dr.sample("circle", pip)
        samples.append([v.x, v.y, v.z])
    write_json(path, dict(version="test export", pipsPerBeat=64, extension_sha256="synthetic-binary-id",
                         movements=dict(circle=dict(periodPips=256, samples=samples))))
    motion = Motion(dict(kind="table", file=ref(path), pattern="circle"), tmp_path, BeatClock(bpm=120))
    for pip in (-257, -1, 0, 64, 255, 512):
        expected = dr.sample("circle", pip)
        assert motion.sample(pip / 128) == (expected.x, expected.y, expected.z)
    smooth = Motion(dict(kind="table", file=ref(path), pattern="circle", interpolate=True),
                    tmp_path, BeatClock(bpm=120))
    assert smooth.sample(.5 / 128) == pytest.approx(tuple((a + b) / 2 for a, b in zip(samples[0], samples[1])), abs=1e-15)


def test_match_offset_phase_and_window_are_explicit(tmp_path):
    path = tmp_path / "match.json"
    match = dict(pattern="circle", phase_beats=.5, start_beat=2, start_seconds=1,
                 end_seconds=3, similarity=.9, signal="position_excursion")
    write_json(path, dict(schema="stemlab.dancerudiments.candidates.v1",
                         sources=[dict(stem="bass", matches=[match])]))
    config = dict(kind="match", file=ref(path), source_index=0, match_index=0, source_offset_seconds=10)
    motion = Motion(config, tmp_path, BeatClock(bpm=120))
    expected = dr.sample("circle", 32)
    assert motion.sample(11) == (expected.x, expected.y, expected.z)
    assert motion.evidence["match"]["source"]["stem"] == "bass"
    with pytest.raises(ValueError, match="matched window"):
        motion.sample(13)


def test_sheet_geometry_and_trail_extents():
    sheet = Image.new("RGBA", (20, 10), "#ffffff")
    assert atlas_cell(sheet, columns=2, rows=1, index=1).size == (10, 10)
    with pytest.raises(ValueError):
        atlas_cell(sheet, columns=3, rows=1, index=0)
    trailed = directional_trail(sheet, offset=(8, -4), opacity=.2, radius=2)
    assert trailed.width > sheet.width and trailed.height > sheet.height
    bounds = transformed_bounds(trailed, 5, 9)
    assert bounds[0] >= 5 and bounds[1] >= 9


def test_independent_motion_channels():
    clock = BeatClock(bpm=120)
    motion = Motion(dict(kind="composite", channels=[
        dict(motion=dict(kind="named", pattern="circle"), axis=0),
        dict(motion=dict(kind="named", pattern="bounce"), axis=0),
        dict(motion=dict(kind="named", pattern="circle", rate=.5), axis=1)]), ".", clock)
    circle, bounce, slower = dr.sample("circle", 64), dr.sample("bounce", 64), dr.sample("circle", 32)
    assert motion.sample(.5) == (circle.x, bounce.x, slower.y)
