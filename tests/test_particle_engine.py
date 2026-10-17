"""Cue compatibility, determinism, compositor integration and source validation."""

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.io import sha256, file_ref
from framerudiments.particle_engine import ParticleEmitter, _read_cues, _selected, PARAMETERS


class FakeProject:
    def __init__(self, path, *, repeats=2, start=0, end=4):
        self.base = Path(path)
        self.width, self.height, self.fps = 180, 150, 30
        self.sample_rate = 48000
        self.start_sample, self.end_sample = start * 48000, end * 48000
        self.repeats = repeats
        self.frame_count = (end - start) * repeats * self.fps
        self.audio_ref = {"sha256": "a" * 64}
        self.inputs = []
        self.clock = SimpleNamespace(beat_at=lambda x: x * 2)

    def ref(self, value):
        entry = file_ref(value, self.base)
        self.inputs.append(entry)
        return entry

    def context(self, frame):
        seconds = frame / self.fps
        segment = (self.end_sample - self.start_sample) / self.sample_rate
        source = self.start_sample / self.sample_rate + seconds % segment
        return SimpleNamespace(
            frame=frame, source_seconds=source, output_seconds=seconds, beat=seconds * 2, lyric=None, cue=None
        )


def ref(path):
    return {"path": str(path), "sha256": sha256(path)}


def layer(project, *, kind="smoke", emission=None, style=None, **extra):
    config = {
        "type": "emitter",
        "id": "mist",
        "kind": kind,
        "bounds": [0, 0, project.width, project.height],
        "style": {"seed": 42, "rate": 17, "size": 7, "lifetime": 1.8, **(style or {})},
        "emission": emission or {"mode": "continuous"},
        **extra,
    }
    return ParticleEmitter(project, config, project.context)


def render(layer_, frame):
    canvas = Image.new("RGBA", (layer_.project.width, layer_.project.height))
    layer_.draw(canvas, layer_.project.context(frame))
    return canvas


def test_all_emitter_kinds_render_deterministically_and_keep_transparency(tmp_path):
    project = FakeProject(tmp_path)
    for kind in ("particles", "smoke", "sparks", "embers", "confetti", "snow", "rain", "dust", "fireflies"):
        emitter = layer(project, kind=kind)
        at_50 = render(emitter, 50)
        assert at_50.size == (180, 150) and at_50.mode == "RGBA"
        assert at_50.getchannel("A").getbbox()
        render(emitter, 3)
        assert render(emitter, 50).tobytes() == at_50.tobytes()
        assert emitter.frame_report["kind"] == "particle-emitter"


def test_continuous_rate_and_lifetime_are_reproducible(tmp_path):
    project = FakeProject(tmp_path)
    emitter = layer(project, kind="particles", style={"rate": 30, "lifetime": 1.5})
    # Exactly one new particle per 30-fps frame, no independent global RNG.
    assert [len(emitter._births(i, emitter._parameters_at(i)[0])) for i in range(15)] == [1] * 15
    later = render(emitter, 45)
    render(emitter, 5)
    assert render(emitter, 45).tobytes() == later.tobytes()


def test_dancemoves_cue_file_bursts_repeat_on_each_source_excerpt(tmp_path):
    cue_file = tmp_path / "dance-cues.lrc"
    cue_file.write_text(
        "[00:00.60][SECTION: INTRO]\n[00:01.00][SECTION: DROP 1]\n[00:02.00][DROP: SECOND]\n",
        encoding="utf-8",
    )
    project = FakeProject(tmp_path)
    emitter = layer(
        project,
        kind="smoke",
        emission={"mode": "cue", "cue_file": ref(cue_file), "labels": ["DROP*"], "burst_count": 12},
    )
    assert emitter.events and len(emitter.events) == 2
    assert sorted(emitter.bursts) == [30, 60, 150, 180]
    assert render(emitter, 29).getchannel("A").getbbox() is None
    assert render(emitter, 30).getchannel("A").getbbox()
    assert emitter.frame_report["cue_bursts"][0]["cue"] == "DROP 1"
    assert render(emitter, 150).getchannel("A").getbbox()
    assert emitter.frame_report["cue_bursts"][0]["cycle"] == 1
    assert emitter.cues_ref in project.inputs


def test_excerpt_discards_out_of_range_events_and_rebases_repeats(tmp_path):
    cue_file = tmp_path / "sections.lrc"
    cue_file.write_text(
        "[00:00.10][DROP: OUT]\n[00:01.20][DROP: IN]\n[00:03.20][DROP: TOO LATE]\n", encoding="utf-8"
    )
    project = FakeProject(tmp_path, repeats=3, start=1, end=3)
    emitter = layer(
        project, emission={"mode": "cue", "cue_file": ref(cue_file), "labels": ["DROP:*"], "burst_count": 6}
    )
    assert sorted(emitter.bursts) == [6, 66, 126]
    assert len(emitter.events) == 3
    for frame in (6, 66, 126):
        render(emitter, frame)
        assert len(emitter.frame_report["cue_bursts"]) == 1
        assert emitter.frame_report["cue_bursts"][0]["count"] == 6


def test_stemlab_snap_and_generate_json_reports_use_output_time(tmp_path):
    for schema in ("stemlab.cue-snap.v1", "stemlab.cue-generate.v1"):
        path = tmp_path / f"{schema}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": schema,
                    "grid": {"source_sha256": "a" * 64},
                    "cues": [
                        {
                            "label": "[SECTION: DROP 2]",
                            "original_seconds": 1.2,
                            "output_seconds": 1.25,
                            "output_seconds_exact": "1.2500000",
                        }
                    ],
                }
            )
        )
        project = FakeProject(tmp_path)
        emitter = layer(
            project, emission={"mode": "cue", "cue_file": ref(path), "labels": ["DROP*"], "burst_count": 8}
        )
        assert sorted(emitter.bursts) == [38, 158]
        assert len(_read_cues(path, source_sha="a" * 64)) == 1
        with pytest.raises(ValueError, match="hash"):
            _read_cues(path, source_sha="b" * 64)


def test_dancemoves_json_time_events_and_cd_cue(tmp_path):
    path = tmp_path / "cue-timeline.json"
    path.write_text(
        json.dumps(
            {
                "cues": [
                    {"id": "drop-a", "name": "FIRST", "type": "DROP", "time": 1.25},
                    {"name": "BREAK", "time": 3.3},
                ]
            }
        )
    )
    cues = _read_cues(path)
    assert [float(x.second) for x in cues] == [1.25, 3.3]
    assert _selected(cues[0], ["DROP:*"])
    cd = tmp_path / "cd.cue"
    cd.write_text(
        'FILE "fake.wav" WAVE\nTRACK 01 AUDIO\nTITLE "BUILD"\nINDEX 01 00:00:30\n'
        'TRACK 02 AUDIO\nTITLE "DROP"\nINDEX 01 00:02:00\n'
    )
    assert len(_read_cues(cd)) == 2
    assert float(_read_cues(cd)[0].second) == 0.4


def test_rejects_invalid_cues_unpinned_assets_and_bad_controls(tmp_path):
    project = FakeProject(tmp_path)
    for bad in ({"rate": -1}, {"lifetime": 14}, {"shape": "bogus"}, {"burst_count": 999}):
        with pytest.raises(ValueError):
            layer(project, style=bad)
    with pytest.raises(ValueError, match="cue_file"):
        layer(project, emission={"mode": "cue"})
    with pytest.raises(ValueError, match="emission"):
        layer(project, emission={"mode": "continuous", "unsupported": 9})
    png = tmp_path / "particle.png"
    Image.new("RGBA", (18, 18), "#ff9944").save(png)
    emitter = layer(project, particle_sprite=ref(png))
    assert emitter.sprite_ref in project.inputs
    assert render(emitter, 50).getbbox()
    original_ref = ref(png)
    png.write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        layer(project, particle_sprite=original_ref)


def test_modulation_targets_are_validated(tmp_path):
    project = FakeProject(tmp_path)
    invalid = {"motion": {"kind": "named", "pattern": "circle"}, "target": "style.dangerous", "range": [0, 1]}
    with pytest.raises(ValueError, match="Unknown particle modulation"):
        layer(project, modulations=[invalid])
    assert "size" in PARAMETERS and "wind" in PARAMETERS


def test_real_compositor_preserves_scoped_post_and_analysis(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("Requires CI render font")
    from framerudiments import Project
    from framerudiments.compositor import Compositor
    from framerudiments.demo import create_demo

    project = Project.load(create_demo(tmp_path / "demo", font, width=360, height=640))
    base = copy.deepcopy(project.data)
    base["layers"] = [
        {
            "id": "haze",
            "type": "emitter",
            "kind": "smoke",
            "bounds": [0, 180, 360, 560],
            "style": {"rate": 16, "lifetime": 1.2, "size": 7, "seed": 7},
            "modulations": [
                {
                    "id": "wind",
                    "motion": {"kind": "named", "pattern": "circle"},
                    "target": "style.wind",
                    "axis": "x",
                    "range": [-0.1, 0.1],
                },
                {
                    "id": "glow",
                    "motion": {"kind": "named", "pattern": "bounce"},
                    "target": "visual.glow",
                    "axis": "y",
                    "range": [0, 3],
                },
            ],
        }
    ]
    base["post_effects"] = {"elements": {"layer:haze": [{"id": "colour", "visual": {"hue_shift": 40}}]}}
    comp = Compositor(Project(base, project.base))
    first, _ = comp.frame(40)
    comp.frame(20)
    assert comp.frame(40)[0].tobytes() == first.tobytes()
    assert comp.frame_reports()["haze"]["kind"] == "particle-emitter"
    assert "haze" in comp.evidence()["emitters"]
    assert "layer:haze" in comp.post_pipeline.last_report


def test_blend_stretch_and_trail_change_pixels(tmp_path):
    project = FakeProject(tmp_path)
    base = {"rate": 38, "size": 7, "lifetime": 2, "opacity": 1, "fade_in": 0}
    normal = render(layer(project, kind="sparks", style=base), 45)
    changed = render(
        layer(project, kind="sparks", style={**base, "blend": "add", "stretch": 3, "trail": 2}), 45
    )
    assert normal.tobytes() != changed.tobytes()
    assert changed.getchannel("A").getbbox()


def test_birth_parameters_are_sampled_at_birth_not_renderer_call_time(tmp_path):
    project = FakeProject(tmp_path)
    emitter = layer(
        project,
        kind="smoke",
        style={"rate": 30, "lifetime": 2, "size": 7},
        modulations=[
            {"motion": {"kind": "named", "pattern": "circle"}, "target": "style.wind", "range": [-0.2, 0.2]}
        ],
    )
    first = render(emitter, 40)
    render(emitter, 22)
    assert render(emitter, 40).tobytes() == first.tobytes()
    assert emitter.frame_report["modulations"]


def test_exact_lrc_decimal_offsets_and_multiple_timestamp_tokens(tmp_path):
    path = tmp_path / "accurate.lrc"
    path.write_text(
        "[ti:Instrumental test]\n[offset:+50]\n[00:01.234567890123456789][00:02.00][DROP: PRECISE]\n"
    )
    cues = _read_cues(path)
    from fractions import Fraction

    assert cues[0].second == Fraction("1.284567890123456789")
    assert cues[1].second == Fraction("2.05")
    assert all(_selected(cue, ["DROP:*"]) for cue in cues)
