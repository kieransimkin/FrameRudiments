"""Stage-level chroma key and track matte compositing."""

from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments import visualisers
from framerudiments.visualiser_compositing import (
    apply_chroma_key,
    composite_stage,
    validate_stage_composite,
)


def config(**updates):
    value = {
        "opacity": 1.0,
        "chroma_key": {
            "colour": "#00ff00",
            "similarity": 0.08,
            "smoothness": 0.12,
            "despill": 0.5,
        },
    }
    value.update(updates)
    return validate_stage_composite(value)


def test_chroma_key_removes_key_colour_keeps_distant_colour_and_softens_edge():
    image = Image.new("RGBA", (3, 1))
    image.putdata([(0, 255, 0, 255), (20, 230, 20, 255), (230, 40, 60, 255)])
    result = apply_chroma_key(image, config()["chroma_key"])
    alpha = [result.getpixel((x, 0))[3] for x in range(3)]
    assert alpha[0] == 0
    assert 0 < alpha[1] < 255
    assert alpha[2] == 255


def test_track_matte_and_key_combine_before_alpha_over():
    base = Image.new("RGBA", (4, 1), "#102030")
    rendered = Image.new("RGBA", (4, 1))
    rendered.putdata([(0, 255, 0, 255), (255, 0, 0, 255)] * 2)
    matte = Image.new("RGBA", (4, 1))
    matte.putdata([(255, 255, 255, 255)] * 2 + [(255, 255, 255, 0)] * 2)
    setting = config(matte={"source": "layer:cloud", "mode": "alpha"})
    result = composite_stage(base, rendered, setting, {"layer:cloud": matte})
    assert result.getpixel((0, 0))[:3] == (16, 32, 48)  # keyed green reveals base
    assert result.getpixel((1, 0))[:3] == (255, 0, 0)  # red inside matte survives
    assert result.getpixel((3, 0))[:3] == (16, 32, 48)  # matte excludes stage


def test_luminance_matte_multiplies_provider_alpha_and_stage_opacity():
    base = Image.new("RGBA", (1, 1), (0, 0, 0, 255))
    rendered = Image.new("RGBA", (1, 1), (255, 255, 255, 255))
    matte = Image.new("RGBA", (1, 1), (128, 128, 128, 128))
    setting = validate_stage_composite(
        {"opacity": 0.5, "matte": {"source": "layer:m", "mode": "luminance", "opacity": 1}}
    )
    result = composite_stage(base, rendered, setting, {"layer:m": matte})
    # 0.5 provider alpha * 0.5 luminance * 0.5 stage opacity ~= 12.5% white.
    assert result.getpixel((0, 0))[0] in range(31, 34)


def test_transparent_visualiser_output_reveals_opaque_underlay_instead_of_input():
    base = Image.new("RGBA", (2, 1), "red")
    rendered = Image.new("RGBA", (2, 1), (0, 255, 0, 0))
    rendered.putpixel((1, 0), (0, 255, 0, 255))
    underlay = Image.new("RGBA", (2, 1), "blue")
    setting = validate_stage_composite({"underlay": {"source": "layer:backdrop"}})
    result = composite_stage(base, rendered, setting, {"layer:backdrop": underlay})
    assert result.getpixel((0, 0)) == (0, 0, 255, 255)
    assert result.getpixel((1, 0)) == (0, 255, 0, 255)


def test_underlay_alpha_then_rendered_alpha_preserve_compositing_order():
    base = Image.new("RGBA", (1, 1), "red")
    underlay = Image.new("RGBA", (1, 1), (0, 0, 255, 128))
    rendered = Image.new("RGBA", (1, 1), (0, 255, 0, 128))
    setting = validate_stage_composite({"underlay": {"source": "layer:backdrop"}})
    result = composite_stage(base, rendered, setting, {"layer:backdrop": underlay})
    assert result.getpixel((0, 0)) == (63, 128, 64, 255)


def test_hidden_underlay_does_not_reuse_an_earlier_provider_surface():
    base = Image.new("RGBA", (1, 1), "red")
    rendered = Image.new("RGBA", (1, 1), (0, 255, 0, 0))
    setting = validate_stage_composite({"underlay": {"source": "layer:backdrop"}})
    shown = composite_stage(base, rendered, setting, {"layer:backdrop": Image.new("RGBA", (1, 1), "blue")})
    hidden = composite_stage(base, rendered, setting, {})
    assert shown.getpixel((0, 0)) == (0, 0, 255, 255)
    assert hidden.getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("source", [Image.new("RGB", (1, 1)), Image.new("RGBA", (2, 1))])
def test_underlay_provider_requires_frame_size_and_rgba(source):
    setting = validate_stage_composite({"underlay": {"source": "layer:backdrop"}})
    with pytest.raises(ValueError, match="underlay source must be full-frame RGBA"):
        composite_stage(Image.new("RGBA", (1, 1)), Image.new("RGBA", (1, 1)), setting, {"layer:backdrop": source})


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"unknown": 1},
        {"chroma_key": {"colour": "transparent"}},
        {"chroma_key": {"colour": "#00ff00", "similarity": -1}},
        {"chroma_key": {"colour": "#00ff00", "smoothness": 2}},
        {"matte": {"source": "", "mode": "alpha"}},
        {"matte": {"source": "layer:x", "mode": "bad"}},
        {"matte": {"source": "layer:x", "invert": 1}},
        {"underlay": {}},
        {"underlay": {"source": ""}},
        {"underlay": {"source": "layer:x", "path": "unreviewed.png"}},
        {"opacity": 2},
        {"modulations": {}},
        {"modulations": [{"target": "visual.glow", "motion": {"kind": "named", "pattern": "bounce"}, "range": [0, 1]}]},
        {"modulations": [{"id": "bad-range", "target": "composite.opacity", "motion": {"kind": "named", "pattern": "bounce"}, "range": [-0.1, 1]}]},
        {"modulations": [{"id": "missing-matte", "target": "composite.matte.opacity", "motion": {"kind": "named", "pattern": "bounce"}, "range": [0, 1]}]},
        {"modulations": [{"id": "missing-key", "target": "composite.chroma_key.despill", "motion": {"kind": "named", "pattern": "bounce"}, "range": [0, 1]}]},
        {"modulations": [{"id": "", "target": "composite.opacity", "motion": {"kind": "named", "pattern": "bounce"}, "range": [0, 1]}]},
        {"modulations": [
            {"id": "same", "target": "composite.opacity", "motion": {"kind": "named", "pattern": "bounce"}, "range": [0, 1]},
            {"id": "same", "target": "composite.opacity", "motion": {"kind": "named", "pattern": "bounce"}, "range": [0, 1]},
        ]},
    ],
)
def test_composite_validation_rejects_ambiguous_or_out_of_range_values(bad):
    with pytest.raises(ValueError):
        validate_stage_composite(bad)


def test_effect_chain_applies_each_stage_composite_before_next_engine(monkeypatch, tmp_path):
    size = (4, 1)

    class Audio:
        def __init__(self, project, *args):
            self.project = project
            self.evidence = {}

        def at(self, frame):
            return [0.0] * 8

        def close(self):
            pass

    monkeypatch.setattr(visualisers, "EffectAudio", Audio)
    seen = []

    class Fake:
        def __init__(self, *args):
            self.index = len(seen)
            self.evidence = {}

        def process(self, image, context, pcm):
            seen.append([image.convert("RGB").getpixel((x, 0)) for x in range(image.width)])
            if len(seen) == 1:
                return Image.new("RGBA", size, "red")
            return image.convert("RGBA")

        def close(self):
            pass

    stage_composite = validate_stage_composite(
        {"matte": {"source": "layer:half", "mode": "alpha"}}
    )
    specs = [
        {
            "engine": "projectm",
            "preset": {"path": "unused"},
            "input_mix": 1,
            "seed": 1,
            "composite": stage_composite,
        },
        {
            "engine": "butterchurn",
            "preset": {"path": "unused"},
            "input_mix": 1,
            "seed": 1,
            "composite": None,
        },
    ]
    project = SimpleNamespace(width=4, height=1, fps=60, data={})
    matte = Image.new("RGBA", size)
    matte.putdata([(255, 255, 255, 255)] * 2 + [(255, 255, 255, 0)] * 2)
    chain = visualisers.EffectChain(
        project, specs, None, tmp_path, factories={"projectm": Fake, "butterchurn": Fake}
    )
    try:
        chain.process(
            Image.new("RGB", size, "blue"),
            SimpleNamespace(frame=0, source_seconds=0),
            isolated_layers=[("layer:half", matte)],
        )
        assert seen[1] == [(255, 0, 0)] * 2 + [(0, 0, 255)] * 2
    finally:
        chain.close()


def test_dancerudiment_modulates_visualiser_stage_opacity(monkeypatch, tmp_path):
    size = (1, 1)

    class Audio:
        def __init__(self, project, *args):
            self.project = project
            self.evidence = {}

        def at(self, frame):
            return [0.0] * 8

        def close(self):
            pass

    monkeypatch.setattr(visualisers, "EffectAudio", Audio)

    class Fake:
        def __init__(self, *args):
            self.evidence = {}

        def process(self, image, context, pcm):
            return Image.new("RGBA", size, "white")

        def close(self):
            pass

    rows = [{
        "id": "sea-breathe",
        "motion": {"kind": "named", "pattern": "bounce"},
        "axis": "x",
        "target": "composite.opacity",
        "range": [0.2, 0.8],
        "curve": "smoothstep",
    }]
    composite = validate_stage_composite({"opacity": 0.5, "modulations": rows})
    specs = [{
        "engine": "butterchurn",
        "preset": {"path": "unused"},
        "input_mix": 1,
        "seed": 1,
        "composite": composite,
    }]
    project = SimpleNamespace(
        width=1,
        height=1,
        fps=60,
        data={},
        inputs=[],
        base=tmp_path,
        clock=SimpleNamespace(beat_at=lambda seconds: seconds * 2),
    )

    def context(frame):
        seconds = frame / 60
        return SimpleNamespace(
            frame=frame,
            source_seconds=seconds,
            output_seconds=seconds,
            beat=seconds * 2,
        )

    chain = visualisers.EffectChain(
        project,
        specs,
        None,
        tmp_path,
        factories={"butterchurn": Fake},
        context_at=context,
    )
    try:
        first = chain.process(Image.new("RGB", size, "black"), context(0)).getpixel((0, 0))[0]
        second = chain.process(Image.new("RGB", size, "black"), context(1)).getpixel((0, 0))[0]
        assert 51 <= first <= 204
        assert 51 <= second <= 204
        assert first != second
        evidence = chain.evidence()
        assert evidence["composite_modulations"][0][0]["target"] == "composite.opacity"
        assert evidence["composite_modulation_last_values"][0]["sea-breathe"]["value"] <= 0.8
    finally:
        chain.close()


def test_all_numeric_composite_parameters_accept_dancerudiment_modulation(monkeypatch, tmp_path):
    size = (1, 1)

    class Audio:
        def __init__(self, project, *args):
            self.project = project
            self.evidence = {}

        def at(self, frame):
            return [0.0] * 8

        def close(self):
            pass

    class Fake:
        def __init__(self, *args):
            self.evidence = {}

        def process(self, image, context, pcm):
            return image.convert("RGBA")

        def close(self):
            pass

    monkeypatch.setattr(visualisers, "EffectAudio", Audio)
    observed = []
    monkeypatch.setattr(
        visualisers,
        "composite_stage",
        lambda base, rendered, composite, surfaces: observed.append(composite) or rendered,
    )
    targets = [
        "composite.opacity",
        "composite.matte.opacity",
        "composite.chroma_key.similarity",
        "composite.chroma_key.smoothness",
        "composite.chroma_key.despill",
    ]
    rows = [
        {
            "id": f"driver-{index}",
            "motion": {"kind": "named", "pattern": "bounce"},
            "axis": "x",
            "target": target,
            "range": [0.1, 0.9],
        }
        for index, target in enumerate(targets)
    ]
    composite = validate_stage_composite({
        "opacity": 0.5,
        "matte": {"source": "layer:sea", "mode": "alpha", "opacity": 0.5},
        "chroma_key": {
            "colour": "#00ff00",
            "similarity": 0.2,
            "smoothness": 0.2,
            "despill": 0.2,
        },
        "modulations": rows,
    })
    project = SimpleNamespace(
        width=1,
        height=1,
        fps=60,
        data={},
        inputs=[],
        base=tmp_path,
        clock=SimpleNamespace(beat_at=lambda seconds: seconds * 2),
    )

    def context(frame):
        seconds = frame / 60
        return SimpleNamespace(
            frame=frame,
            source_seconds=seconds,
            output_seconds=seconds,
            beat=seconds * 2,
        )

    chain = visualisers.EffectChain(
        project,
        [{
            "engine": "projectm",
            "preset": {"path": "unused"},
            "input_mix": 1,
            "seed": 1,
            "composite": composite,
        }],
        None,
        tmp_path,
        factories={"projectm": Fake},
        context_at=context,
    )
    try:
        chain.process(
            Image.new("RGB", size, "black"),
            context(0),
            isolated_layers=[("layer:sea", Image.new("RGBA", size, "white"))],
        )
        actual = observed[-1]
        values = [
            actual["opacity"],
            actual["matte"]["opacity"],
            actual["chroma_key"]["similarity"],
            actual["chroma_key"]["smoothness"],
            actual["chroma_key"]["despill"],
        ]
        assert all(0.1 <= value <= 0.9 for value in values)
        assert set(chain.evidence()["composite_modulation_last_values"][0]) == {
            f"driver-{index}" for index in range(5)
        }
    finally:
        chain.close()


def test_input_feedback_and_output_hooks_are_distinct_and_feedback_reaches_next_frame(
    monkeypatch, tmp_path
):
    size = (2, 1)

    class Audio:
        def __init__(self, project, *args):
            self.project = project
            self.evidence = {}

        def at(self, frame):
            return [0.0] * 8

        def close(self):
            pass

    monkeypatch.setattr(visualisers, "EffectAudio", Audio)

    class Fake:
        def __init__(self, *args):
            self.evidence = {}
            self.feedback = None
            self.inputs = []

        def process(self, image, context, pcm):
            self.inputs.append([image.convert("RGB").getpixel((x, 0)) for x in range(image.width)])
            return (self.feedback or image).convert("RGBA")

        def set_feedback(self, image):
            self.feedback = image.copy()

        def close(self):
            pass

    project = SimpleNamespace(width=2, height=1, fps=60, data={}, inputs=[])
    specs = [
        {
            "engine": "projectm",
            "preset": {"path": "unused"},
            "input_mix": 1,
            "seed": 1,
            "input_effects": [{"id": "input-red", "visual": {"hue_shift": 120}}],
            "feedback_effects": [{"id": "feedback-dark", "visual": {"brightness": 0.5}}],
            "output_effects": [{"id": "output-pass", "visual": {}}],
            "composite": None,
        }
    ]
    hooks = visualisers.VisualiserEffectHooks(project, specs, lambda frame: None)
    chain = visualisers.EffectChain(
        project, specs, None, tmp_path, factories={"projectm": Fake}, hooks=hooks
    )
    context0 = SimpleNamespace(frame=0, source_seconds=0, output_seconds=0, beat=0)
    context1 = SimpleNamespace(frame=1, source_seconds=1 / 60, output_seconds=1 / 60, beat=0)
    try:
        first = chain.process(Image.new("RGB", size, "red"), context0)
        second = chain.process(Image.new("RGB", size, "red"), context1)
        assert first.getpixel((0, 0)) != second.getpixel((0, 0))
        assert chain.backends[0].feedback is not None
        assert set(hooks.last_report) == {
            "stage:0:input",
            "stage:0:feedback",
            "stage:0:output",
        }
    finally:
        chain.close()
