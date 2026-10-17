"""Intermediate GPU routing must not alter earlier stage feedback inputs."""

from types import SimpleNamespace
from PIL import Image
import pytest
from framerudiments import visualisers


def test_gpu_stage_injection_applies_between_engines(monkeypatch, tmp_path):
    size = (24, 16)

    class Audio:
        def __init__(self, project, *args):
            self.project = project
            self.evidence = {}

        def at(self, frame):
            return [0.0] * 100

        def close(self):
            pass

    monkeypatch.setattr(visualisers, "EffectAudio", Audio)
    seen = []

    class Fake:
        def __init__(self, *args):
            self.evidence = {}

        def process(self, image, context, pcm):
            seen.append(image.getpixel((8, 8))[:3])
            return image.convert("RGBA")

        def close(self):
            pass

    project = SimpleNamespace(
        width=24,
        height=16,
        fps=60,
        data={"visualiser_stage_injections": [{"before": 1, "elements": ["layer:ink"], "opacity": 1}]},
    )
    specs = [
        {"engine": "projectm", "preset": {"path": "unused"}, "input_mix": 1, "seed": 1},
        {"engine": "butterchurn", "preset": {"path": "unused"}, "input_mix": 1, "seed": 1},
    ]
    chain = visualisers.EffectChain(
        project, specs, None, tmp_path, factories={"projectm": Fake, "butterchurn": Fake}
    )
    overlay = Image.new("RGBA", size, "red")
    try:
        chain.process(
            Image.new("RGB", size), SimpleNamespace(frame=0), isolated_layers=[("layer:ink", overlay)]
        )
        assert seen == [(0, 0, 0), (255, 0, 0)]
    finally:
        chain.close()


def test_stage_injection_validation(tmp_path, monkeypatch):
    class Audio:
        def __init__(self, project, *args):
            self.project = project

        def close(self):
            pass

    monkeypatch.setattr(visualisers, "EffectAudio", Audio)
    specs = [{"engine": "projectm", "preset": {"path": "unused"}, "input_mix": 1, "seed": 1}]
    for invalid in (
        [{"before": 0, "elements": ["layer:x"]}],
        [{"before": 1, "elements": ["layer:x"]}],
        [{"before": True, "elements": ["layer:x"]}],
        [{"before": 0, "elements": []}],
        [{"before": 1, "elements": ["layer:x"], "opacity": 2}],
    ):
        p = SimpleNamespace(width=24, height=16, fps=60, data={"visualiser_stage_injections": invalid})
        with pytest.raises(ValueError):
            visualisers.EffectChain(p, specs, None, tmp_path)
