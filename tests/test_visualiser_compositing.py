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
        {"opacity": 2},
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
