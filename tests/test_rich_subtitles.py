"""Regression coverage for rich caption masks and deterministic word cue particles."""

import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.compositor import TextBlock
from framerudiments.rich_subtitles import RichTextAppearance, gradient_image, stops, word_centres
from framerudiments.lyric_indicator import WordIndicator
from framerudiments.timeline import EventTrack
from framerudiments.word_timing import WordTrack


def font_file():
    for option in (
        os.environ.get("FRAMERUDIMENTS_TEST_FONT"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if option and Path(option).is_file():
            return Path(option)
    pytest.skip("No pinned renderer font installed")


@pytest.fixture
def project():
    project = SimpleNamespace(
        width=600,
        height=260,
        fps=60,
        frame_count=150,
        base=Path("."),
        sample_rate=48000,
        start_sample=0,
        end_sample=120000,
        repeats=1,
        audio_ref={"sha256": "0" * 64},
        inputs=[],
        data={},
        font_ref={"path": str(font_file())},
        clock=SimpleNamespace(beat_at=lambda t: t * 2),
    )
    project.lyrics = [{"frame": 0, "text": "Lights follow every word", "cycle": 0, "source_order": 0}]
    project.words = [
        {
            "frame": n,
            "text": value,
            "cycle": 0,
            "word_index": j,
            "line_frame": 0,
            "line_order": 0,
            "source_order": j,
        }
        for j, (n, value) in enumerate(zip((5, 35, 65, 95), ("Lights", "follow", "every", "word")))
    ]
    project.cues = [{"frame": 70, "text": "[DROP: CHORUS]", "cycle": 0}]
    project.ref = lambda x: x
    words = WordTrack(project.words)
    cues = EventTrack(project.cues)
    project.context = lambda frame: SimpleNamespace(
        frame=frame,
        source_seconds=frame / 60,
        output_seconds=frame / 60,
        beat=frame / 30,
        lyric=project.lyrics[0],
        word=words.at(frame, project.lyrics[0]),
        cue=cues.at(frame),
    )
    return project


def block(project, effects=None, font=None):
    config = {
        "box": [15, 70, 585, 175],
        "font_size": 43,
        "min_font_size": 31,
        "word_mode": "progressive",
        "stroke": 0,
    }
    if effects is not None:
        config["effects"] = effects
    if font:
        config["font"] = font
    return TextBlock(project, config, label="synthetic subtitle")


def image():
    return Image.new("RGBA", (600, 260))


def indicator(project, text_block, particles=None, **other):
    settings = {
        "glyph": "star",
        "size": 17,
        "travel_frames": 9,
        "bounce": {"motion": {"kind": "named", "pattern": "bounce"}, "axis": "x", "amplitude": 16},
        **other,
    }
    if particles is not None:
        settings["particles"] = particles
    return WordIndicator(project, settings, project.context, text_block)


def test_gradient_interpolates_four_uneven_colour_stops():
    fill = gradient_image(
        (12, 48),
        ["#102030", {"at": 0.2, "colour": "#f07830"}, {"at": 0.8, "colour": "#3070cc"}, "#eafaff"],
        angle=90,
    )
    assert fill.mode == "RGBA"
    assert len({fill.getpixel((5, y)) for y in (0, 10, 24, 39, 47)}) == 5
    assert fill.getpixel((5, 0)) == (16, 32, 48, 255)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        ["red"],
        ["red", "blue", "green"] * 6,
        [{"at": 0.1, "colour": "red"}, "blue"],
        ["red", {"at": 0.8, "colour": "blue"}, "green", "yellow"],
        ["red", 37],
        ["red", "not-a-colour"],
    ],
)
def test_invalid_gradient_stops_fail_closed(bad):
    with pytest.raises((ValueError, KeyError)):
        stops(bad)


@pytest.mark.parametrize(
    "effects",
    [
        {"gradient": {"stops": ["red", "blue"], "space": "xyz"}},
        {"stroke": {"width": 27}},
        {"emboss": {"depth": -1}},
        {"bevel": {"angle": 900}},
        {"inner_glow": {"radius": 500}},
        {"drop_shadow": {"opacity": 2}},
        {"gradient": {"stops": ["red", "blue"], "unknown": 1}},
        {"untrusted_shader": "some code"},
    ],
)
def test_invalid_effect_parameters_rejected_at_configuration(effects):
    with pytest.raises((ValueError, KeyError)):
        RichTextAppearance(effects)


def test_full_mask_effect_stack_is_deterministic_and_preserves_background(project):
    fx = {
        "gradient": {"stops": ["#414975", "#e6efff", "#6c6cb2"], "angle": 90},
        "stroke": {"width": 2, "colour": "#16233b"},
        "bevel": {"depth": 2, "strength": 0.5},
        "emboss": {"depth": 2, "angle": 45},
        "inner_glow": {"radius": 3, "colour": "#f4f9ff"},
        "outer_glow": {"radius": 4, "colour": "#55d6ef"},
        "drop_shadow": {"dx": 7, "dy": 5, "blur": 5, "opacity": 0.7},
    }
    text = block(project, fx)
    a = image()
    b = image()
    bounds = text.draw(a, project.lyrics[0]["text"])
    text.draw(b, project.lyrics[0]["text"])
    assert bounds and a.tobytes() == b.tobytes()
    assert a.getbbox() is not None
    assert a.getpixel((0, 0))[3] == 0
    assert a.getchannel("A").getextrema()[1] > 0


def test_stroke_and_glow_change_ink_without_changing_line_layout(project):
    plain = block(project)
    thick = block(
        project,
        {"stroke": {"width": 6, "colour": "#f3354f"}, "outer_glow": {"radius": 9, "colour": "#f2b453"}},
    )
    normal, fx = image(), image()
    plain.draw(normal, project.lyrics[0]["text"])
    thick.draw(fx, project.lyrics[0]["text"])
    assert normal.tobytes() != fx.tobytes()
    assert plain.layout(project.lyrics[0]["text"])[2] == thick.layout(project.lyrics[0]["text"])[2]


def test_custom_font_reference_uses_text_block_font(project):
    override = {"path": str(font_file()), "sha256": "test-only-fake-pinned"}
    seen = []
    project.ref = lambda source: (seen.append(source), source)[1]
    style = block(project, {"gradient": {"stops": ["#baf", "#acf"]}}, font=override)
    assert style.font_ref == override
    style.draw(image(), "Glowing subtitle")
    assert seen == [override]


def test_word_centres_follow_wrapped_text_layout_and_stay_in_box(project):
    text = block(project, {"stroke": {"width": 2}})
    centres = word_centres(text, "Lights follow every word")
    assert len(centres) == 4
    assert all(text.box[0] <= x <= text.box[2] for x, _ in centres)
    assert all(a[1] <= b[1] for a, b in zip(centres, centres[1:]))
    assert all(a[0] < b[0] for a, b in zip(centres, centres[1:]) if a[1] == b[1])


def test_rich_highlight_can_tint_single_word_without_destroying_gradients(project):
    style = block(project, {"gradient": {"stops": ["#102040", "#f1faff", "#23a5dc"]}})
    with_word, without = image(), image()
    style.rich.draw(with_word, style, project.lyrics[0]["text"], active=1, completed=(0,))
    style.rich.draw(without, style, project.lyrics[0]["text"])
    assert with_word.tobytes() != without.tobytes()
    assert with_word.getbbox() == without.getbbox()


def test_bounce_uses_single_selected_native_rudiment_axis_and_replays(project):
    obj = indicator(project, block(project, {}))
    at_35 = project.context(35)
    first = image()
    obj.draw(first, at_35)
    late = image()
    obj.draw(late, project.context(95))
    again = image()
    obj.draw(again, at_35)
    assert first.tobytes() == again.tobytes()
    assert first.tobytes() != late.tobytes()
    assert obj.frame_report["word_index"] == 1
    assert obj.bounce_axis == 0


def test_between_word_arc_lands_on_the_next_word_frame(project):
    settings = {
        "glyph": "star",
        "size": 17,
        "travel_frames": 20,
        "travel_mode": "between_words",
        "travel_arc": 24,
    }
    obj = WordIndicator(project, settings, project.context, block(project, {}))
    first = obj._centre(5)
    midpoint = obj._centre(25)
    before_landing = obj._centre(34)
    landed = obj._centre(35)
    assert first and midpoint and before_landing and landed
    assert first[0] < midpoint[0] < landed[0]
    assert midpoint[1] < first[1] and midpoint[1] < landed[1]
    assert before_landing[0] < landed[0]
    assert landed[1] == pytest.approx(first[1])
    frame = image()
    obj.draw(frame, project.context(35))
    assert obj.frame_report["landed"] is True
    assert obj.frame_report["travel_mode"] == "between_words"


def test_constant_particles_stay_on_and_word_events_trigger_bursts(project):
    obj = indicator(
        project,
        block(project, {}),
        particles={
            "mode": "hybrid",
            "kind": "sparks",
            "rate": 60,
            "lifetime_frames": 25,
            "on_word": {"burst_count": 25, "colour": "#ff7e25", "rate_multiplier": 1.5},
        },
    )
    at35, at45 = image(), image()
    obj.draw(at35, project.context(35))
    word_report = dict(obj.frame_report)
    obj.draw(at45, project.context(45))
    later_report = dict(obj.frame_report)
    assert word_report["word_trigger"] and word_report["particles_births"] >= 25
    assert not later_report["word_trigger"] and later_report["particles_active"] > 0
    assert at35.tobytes() != at45.tobytes()


def test_cue_can_change_indicator_colour_and_emit_burst_while_constant_smoke_runs(project):
    obj = indicator(
        project,
        block(project, {}),
        particles={
            "mode": "hybrid",
            "kind": "smoke",
            "rate": 26,
            "lifetime_frames": 44,
            "cue_labels": ["*DROP*"],
            "burst_count": 0,
            "on_word": {"burst_count": 0},
            "on_cue": {
                "burst_count": 38,
                "colour": "#ff4646",
                "indicator_colour": "#ff3962",
                "rate_multiplier": 3,
                "size_multiplier": 1.8,
                "duration_frames": 16,
            },
        },
    )
    cue_frame = image()
    obj.draw(cue_frame, project.context(70))
    state = dict(obj.frame_report)
    assert state["cue_trigger"] and state["particles_births"] >= 38
    assert obj._profile_at(75)["indicator_colour"] is not None
    assert obj._profile_at(94)["indicator_colour"] is None


def test_only_word_and_cue_source_events_fire_after_seek(project):
    obj = indicator(
        project,
        block(project, {}),
        particles={
            "mode": "hybrid",
            "rate": 22,
            "on_word": {"burst_count": 10},
            "on_cue": {"burst_count": 18},
        },
    )
    f70 = image()
    obj.draw(f70, project.context(70))
    obj.draw(image(), project.context(35))
    other = image()
    obj.draw(other, project.context(70))
    assert other.tobytes() == f70.tobytes()
    assert 70 in obj._events["cue"] and 65 in obj._events["word"]


def test_empty_unmatched_word_timing_rejects_indicator(project):
    project.words = []
    with pytest.raises(ValueError, match="requires both lyrics"):
        indicator(project, block(project))


def test_untrusted_indicator_fields_fail_closed(project):
    with pytest.raises(ValueError):
        WordIndicator(project, {"execute": "python"}, project.context, block(project))
    with pytest.raises(ValueError, match="glyph"):
        indicator(project, block(project), glyph="untrusted-url")


def test_gallery_produces_all_examples_without_external_media(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from generate_rich_lyric_gallery import build_site  # noqa: E402

    (tmp_path / "index.html").write_text("<header></header>", encoding="utf-8")
    result = build_site(tmp_path)
    assert result["synthetic"] is True and result["measured_audio"] is False
    assert len(result["scenes"]) == 8
    assert len(list((tmp_path / "rich-lyrics").glob("*/*.png"))) == 32
    assert "rich-lyrics/index.html" in (tmp_path / "index.html").read_text(encoding="utf-8")
