"""Tests for pip-limited visualiser input and independent clean overlays."""

from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from framerudiments.visualiser_routing import VisualiserLayerRouting

SIZE = (120, 72)


def context(frame, beat=None, lyric=None, cue=None, lyric_next=None, lyric_next_beat=None):
    return SimpleNamespace(
        frame=frame,
        output_seconds=frame / 60,
        source_seconds=frame / 60,
        beat=frame / 8 if beat is None else beat,
        lyric=lyric,
        cue=cue,
        lyric_next=lyric_next,
        lyric_next_beat=lyric_next_beat,
    )


def block(colour, rect=(12, 10, 58, 46)):
    image = Image.new("RGBA", SIZE)
    ImageDraw.Draw(image).rectangle(rect, fill=colour)
    return image


def test_continuous_first_pips_never_and_overlay_are_independent():
    routing = VisualiserLayerRouting(
        {
            "default": {"feed": "never"},
            "elements": {
                "layer:signal": {"feed": {"mode": "first_pips", "duration_pips": 16}, "overlay": True},
                "layer:overlay": {"feed": "never", "overlay": True},
            },
        },
        ["layer:signal", "layer:overlay"],
        SIZE,
    )
    surfaces = [("layer:signal", block("#e54433")), ("layer:overlay", block("#22cc99", (65, 8, 110, 30)))]
    feed0, top0 = routing.route(context(0), surfaces)
    feed1, top1 = routing.route(context(1), surfaces)
    feed2, top2 = routing.route(context(2), surfaces)
    assert feed0.getpixel((20, 20))[0] > 200
    assert feed1.getpixel((20, 20))[0] > 200
    assert feed2.getpixel((20, 20))[3] == 0  # exactly at 16 native pips
    assert all(image.getpixel((70, 10))[3] == 0 for image in (feed0, feed1, feed2))
    assert all(image.getpixel((70, 10))[1] > 140 for image in (top0, top1, top2))
    assert routing.last_report["overlays"] == ["layer:signal", "layer:overlay"]


def test_reappearance_rearms_and_once_policy_does_not():
    def run(retrigger):
        router = VisualiserLayerRouting(
            {
                "elements": {
                    "layer:x": {"feed": {"mode": "first_pips", "duration_pips": 8}, "retrigger": retrigger}
                }
            },
            ["layer:x"],
            SIZE,
        )
        frames = []
        for frame in range(6):
            visible = frame in (0, 1, 3, 4, 5)
            routed, _ = router.route(context(frame), [("layer:x", block("yellow"))] if visible else [])
            frames.append(bool(routed.getbbox()))
        return frames

    assert run("reappearance") == [True, False, False, True, False, False]
    assert run("once") == [True, False, False, False, False, False]


def test_retrigger_by_lyric_and_variable_source_pips():
    router = VisualiserLayerRouting(
        {
            "elements": {
                "caption": {"feed": {"mode": "first_pips", "duration_pips": 20}, "retrigger": "lyric"}
            }
        },
        ["caption"],
        SIZE,
    )
    a = dict(cycle=0, source_order=0, frame=0, text="First")
    b = dict(cycle=0, source_order=1, frame=3, text="Second")
    samples = [(0, 0, a), (1, 0.2, a), (2, 0.55, a), (3, 0.6, b), (4, 0.8, b)]
    results = []
    for frame, beat, line in samples:
        injected, _ = router.route(context(frame, beat=beat, lyric=line), [("caption", block("#f7e8bd"))])
        results.append(bool(injected.getbbox()))
    assert results == [True, True, False, True, True]
    assert router.last_report["elements"]["caption"]["elapsed_pips"] == pytest.approx(0.2 * 64)


def test_last_pips_feeds_only_before_a_real_lyric_gap():
    router = VisualiserLayerRouting(
        {
            "elements": {
                "caption": {
                    "feed": {"mode": "last_pips", "duration_pips": 32},
                    "overlay": True,
                    "retrigger": "lyric",
                }
            }
        },
        ["caption"],
        SIZE,
    )
    line = dict(cycle=0, source_order=0, frame=0, text="Light")
    blank = dict(cycle=0, source_order=1, frame=3, text="")
    samples = [(0, 0.0), (1, 0.49), (2, 0.75)]
    results = []
    for frame, beat in samples:
        injected, overlay = router.route(
            context(frame, beat=beat, lyric=line, lyric_next=blank, lyric_next_beat=1.0),
            [("caption", block("#f7c63d"))],
        )
        results.append((bool(injected.getbbox()), bool(overlay.getbbox())))
    assert results == [(False, True), (False, True), (True, True)]
    assert router.last_report["elements"]["caption"]["remaining_pips"] == pytest.approx(16.0)
    assert router.last_report["elements"]["caption"]["lyric_gap_exit"] is True


def test_last_pips_does_not_feed_an_immediate_lyric_handoff():
    router = VisualiserLayerRouting(
        {
            "elements": {
                "caption": {
                    "feed": {"mode": "last_pips", "duration_pips": 32},
                    "retrigger": "lyric",
                }
            }
        },
        ["caption"],
        SIZE,
    )
    line = dict(cycle=0, source_order=0, frame=0, text="First")
    following = dict(cycle=0, source_order=2, frame=1, text="Second")
    injected, _ = router.route(
        context(0, beat=0.75, lyric=line, lyric_next=following, lyric_next_beat=1.0),
        [("caption", block("#f7c63d"))],
    )
    assert not injected.getbbox()
    assert router.last_report["elements"]["caption"]["lyric_gap_exit"] is False


def test_ordered_overlay_alpha_blends_at_last_composite():
    router = VisualiserLayerRouting(
        {"default": {"feed": "never", "overlay": True}}, ["layer:base", "layer:top"], SIZE
    )
    base = block((255, 0, 0, 255))
    top = block((0, 0, 255, 128))
    _, overlay = router.route(context(0), [("layer:base", base), ("layer:top", top)])
    red, green, blue, alpha = overlay.getpixel((20, 20))
    assert 120 <= red <= 130 and green == 0 and 125 <= blue <= 130 and alpha == 255


@pytest.mark.parametrize(
    "bad",
    [
        {"unexpected": 1},
        {"elements": {"layer:unknown": {"overlay": True}}},
        {"default": {"feed": {"mode": "first_pips"}}},
        {"default": {"feed": {"mode": "continuous", "duration_pips": 20}}},
        {"default": {"feed": {"mode": "last_pips", "duration_pips": 20}}},
        {"elements": {"layer:x": {"overlay": 1}}},
        {"elements": {"layer:x": {"feed": {"mode": "first_pips", "duration_pips": -1}}}},
        {"elements": {"layer:x": {"retrigger": "every_frame"}}},
    ],
)
def test_invalid_routing_configuration_fails_closed(bad):
    with pytest.raises(ValueError):
        VisualiserLayerRouting(bad, ["layer:x"], SIZE)


def test_invalid_surface_duplicate_and_out_of_order_fail_closed():
    router = VisualiserLayerRouting({}, ["layer:x"], SIZE)
    with pytest.raises(ValueError, match="Duplicate or unknown"):
        router.route(context(0), [("layer:x", block("white")), ("layer:x", block("white"))])
    with pytest.raises(ValueError, match="full-frame RGBA"):
        router.route(context(0), [("layer:x", Image.new("RGB", SIZE))])
    router.route(context(0), [("layer:x", block("white"))])
    with pytest.raises(ValueError, match="sequential"):
        router.route(context(2), [])


def test_two_ordered_engine_stages_receive_routed_alpha_then_clean_overlay(monkeypatch, tmp_path):
    import framerudiments.visualisers as visualisers

    class Audio:
        def __init__(self, *args):
            self.project = args[0]
            self.evidence = {"test": True}

        def at(self, frame):
            return [0.0] * 2048

        def close(self):
            pass

    monkeypatch.setattr(visualisers, "EffectAudio", Audio)
    calls = []

    class Backend:
        def __init__(self, *args):
            self.position = len(calls)
            self.evidence = {"test_engine": True}
            calls.append(self)
            self.inputs = []

        def process(self, image, ctx, pcm):
            self.inputs.append(image.copy())
            result = Image.new("RGBA", SIZE, (15, 22, 40, 255))
            if self.position == 0:
                ImageDraw.Draw(result).rectangle((0, 0, 6, 6), fill="#aabbcc")
            else:
                # An earlier effect remains the input of the later one.
                assert image.getpixel((2, 2))[:3] == (170, 187, 204)
            return result

        def close(self):
            pass

    project = SimpleNamespace(width=SIZE[0], height=SIZE[1], fps=60)
    router = VisualiserLayerRouting(
        {
            "default": {"feed": "never"},
            "elements": {
                "layer:signal": {"feed": {"mode": "first_pips", "duration_pips": 16}},
                "caption": {"overlay": True},
            },
        },
        ["layer:signal", "caption"],
        SIZE,
    )
    specs = [
        dict(engine="butterchurn", id="web", preset={"path": "mock"}, input_mix=0.6, seed=1),
        dict(engine="projectm", id="native", preset={"path": "mock"}, input_mix=0.6, seed=1),
    ]
    chain = visualisers.EffectChain(
        project,
        specs,
        None,
        tmp_path,
        factories={"butterchurn": Backend, "projectm": Backend},
        routing=router,
    )
    signal = block("#dd7733")
    label = block("#eaf9ed", (65, 8, 110, 30))
    try:
        for n in range(3):
            result = chain.process(
                Image.new("RGB", SIZE),
                context(n),
                isolated_layers=[("layer:signal", signal), ("caption", label)],
            )
            assert result.getpixel((70, 12)) == (234, 249, 237)
        assert calls[0].inputs[0].getpixel((20, 20))[3] == 255
        assert calls[0].inputs[2].getpixel((20, 20))[3] == 0
        assert chain.evidence()["routing"]["available"] == ["layer:signal", "caption"]
    finally:
        chain.close()


def test_output_palette_grade_precedes_clean_overlay(monkeypatch, tmp_path):
    import framerudiments.visualisers as visualisers

    class Audio:
        def __init__(self, *args):
            self.project = args[0]
            self.evidence = {"test": True}

        def at(self, frame):
            return [0.0] * 2048

        def close(self):
            pass

    class Backend:
        def __init__(self, *args):
            self.evidence = {"test_engine": True}

        def process(self, image, ctx, pcm):
            return Image.new("RGBA", SIZE, "#ffffff")

        def close(self):
            pass

    monkeypatch.setattr(visualisers, "EffectAudio", Audio)
    project = SimpleNamespace(width=SIZE[0], height=SIZE[1], fps=60)
    router = VisualiserLayerRouting(
        {"default": {"feed": "never"}, "elements": {"caption": {"overlay": True}}},
        ["caption"],
        SIZE,
    )
    specs = [
        dict(
            engine="butterchurn",
            id="graded",
            preset={"path": "mock"},
            input_mix=0.6,
            seed=1,
            output_palette_grade={"stops": ["#100b19", "#c49986"], "amount": 1},
        )
    ]
    chain = visualisers.EffectChain(
        project, specs, None, tmp_path, factories={"butterchurn": Backend}, routing=router
    )
    try:
        label = block("#00ff00", (65, 8, 110, 30))
        result = chain.process(Image.new("RGB", SIZE), context(0), isolated_layers=[("caption", label)])
        assert result.getpixel((2, 2)) == (196, 153, 134)
        assert result.getpixel((70, 12)) == (0, 255, 0)
    finally:
        chain.close()


def test_full_compositor_exposes_isolated_parts_with_opt_in(tmp_path):
    import os
    from pathlib import Path
    from framerudiments.demo import create_demo
    from framerudiments.io import read_json, write_json
    from framerudiments.project import Project
    from framerudiments.compositor import Compositor

    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font or not Path(font).is_file():
        pytest.skip("Requires pinned CI font")
    path = create_demo(tmp_path / "routing", font, width=160, height=240)
    data = read_json(path)
    data["effects"] = [dict(id="feedback", engine="butterchurn", preset="feedback")]
    data["visualiser_routing"] = {"elements": {"caption": {"overlay": True}}}
    data["render_quality"] = {"spatial_scale": 1, "temporal_samples": 1}
    write_json(path, data)
    composition = Compositor(Project.load(path))
    image, context = composition.frame(40)
    names = [name for name, _ in composition._route_parts]
    assert image.size == (160, 240) and context.frame == 40
    assert "background" in names and "caption" in names and "layer:native" in names
    assert all(part.mode == "RGBA" and part.size == image.size for _, part in composition._route_parts)
