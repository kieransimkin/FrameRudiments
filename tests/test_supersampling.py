"""True high-res glyph/vector rerasterisation and subframe source-clock QA."""

from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont
import pytest

from framerudiments.output_quality import OutputQuality
from framerudiments.super_sampling import supersampled_text, vector_viewport


class SimpleRenderer:
    def __init__(self, size, style):
        self.size = size
        self.style = style
        self.frame_report = None

    def render(self, frame):
        result = Image.new("RGBA", self.size)
        d = ImageDraw.Draw(result)
        x = self.size[0] / 3 + frame / 7
        d.polygon([(x, 2), (x + 23, 25), (x - 13, self.size[1] - 3)], fill="#ff7656")
        self.frame_report = {"native": frame}
        return result


class Registry:
    def __init__(self):
        self.requests = []

    def create(self, name, size, style):
        self.requests.append((name, size, style))
        return SimpleRenderer(size, style), {"name": name}


def test_vector_rerenders_at_four_times_true_physical_resolution():
    r = Registry()
    cache = {}
    out, report = vector_viewport(r, "test-vector", (160, 92), {"line_width": 2}, 7, 4, cache=cache)
    assert r.requests[0][1] == (640, 368)
    assert r.requests[0][2]["line_width"] == 8
    assert out.size == (160, 92) and out.mode == "RGBA"
    assert report["native"] == 7
    same, _ = vector_viewport(r, "test-vector", (160, 92), {"line_width": 2}, 7, 4, cache=cache)
    assert len(r.requests) == 1 and out.tobytes() == same.tobytes()
    low, _ = vector_viewport(r, "test-vector", (160, 92), {"line_width": 2}, 7, 1)
    assert low.tobytes() != out.tobytes()


def _font():
    import os

    for path in (
        os.environ.get("FRAMERUDIMENTS_TEST_FONT"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if path and Path(path).is_file():
            return path
    pytest.skip("Test requires a real TTF/OTF font")


class FontBlock:
    def __init__(self, scale):
        self.box = (12, 10, 236, 97)
        self.maximum = 35
        self.minimum = 35
        self.stroke = 0
        self.spacing = 3
        self.font_ref = {"path": _font()}
        self.project = SimpleNamespace(
            output_quality=OutputQuality({"spatial_scale": scale, "temporal_samples": 1})
        )
        self.config = {"colour": "white"}
        self.colour = "#ffffff"
        self.rich = None

    def layout(self, text):
        font = ImageFont.truetype(self.font_ref["path"], self.maximum)
        box = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
        return font, (text,), (box,), box[3] - box[1]

    def _draw_native(self, image, text, colour=None):
        font, _, boxes, h = self.layout(text)
        box = boxes[0]
        x = self.box[0] + (self.box[2] - self.box[0] - (box[2] - box[0])) / 2 - box[0]
        y = self.box[1] + (self.box[3] - self.box[1] - h) / 2
        ImageDraw.Draw(image).text((x, y - box[1]), text, fill=colour or self.colour, font=font)
        return [(x + box[0], y, x + box[2], y + h)]


def test_text_supersamples_actual_typeface_outlines_and_limits_working_surface():
    text = "Lyric motion"
    high, low = Image.new("RGBA", (248, 112)), Image.new("RGBA", (248, 112))
    result = supersampled_text(FontBlock(4), high, text)
    supersampled_text(FontBlock(1), low, text)
    assert result and high.getbbox() is not None
    assert high.mode == "RGBA" and high.size == low.size
    assert high.tobytes() != low.tobytes()
    assert high.getpixel((0, 0))[3] == 0
    assert high.tobytes() == (
        lambda other: (supersampled_text(FontBlock(4), other, text), other.tobytes())[1]
    )(Image.new("RGBA", high.size))


def test_true_subframes_are_not_repeated_integer_frames(monkeypatch):
    from framerudiments import super_sampling
    from PIL import ImageDraw

    class Fake:
        def __init__(self, project, scene=None, renderers=None):
            self.project = project
            self.frames = []
            self.extents, self.text_states = {}, {}
            self.post_pipeline = SimpleNamespace(last_report={}, temporal=None)
            self.layers = [SimpleNamespace(id="marker", frame_report=None)]

        def frame(self, frame, apply_quality=False):
            self.frames.append(frame)
            self.layers[0].frame_report = dict(kind="test-motion", frame=frame)
            image = Image.new("RGB", (60, 36), "black")
            ImageDraw.Draw(image).ellipse((2 + frame * 3, 5, 16 + frame * 3, 20), fill="white")
            return image, SimpleNamespace(frame=frame)

        def frame_reports(self):
            return {"marker": self.layers[0].frame_report}

    made = []

    def build(p, factor):
        q = SimpleNamespace(
            fps=60 * factor,
            frame_count=100,
            data={"post_effects": {}},
            output_quality=OutputQuality({"temporal_samples": 1}),
        )
        made.append(q)
        return q

    monkeypatch.setattr(super_sampling, "_virtual_project", build)
    import framerudiments.compositor as compositor_mod

    monkeypatch.setattr(compositor_mod, "Compositor", Fake)
    outer = SimpleNamespace(
        output_quality=OutputQuality({"spatial_scale": 1}),
        project=SimpleNamespace(),
        scene=None,
        renderers=None,
        extents={},
        text_states={},
        post_pipeline=SimpleNamespace(last_report={}),
        layers=[SimpleNamespace(id="marker", frame_report=None)],
        context=lambda f: SimpleNamespace(frame=f),
    )
    result, ctx = super_sampling.temporal_supersample(outer, 1)
    assert ctx.frame == 1 and result.size == (60, 36)
    assert outer._temporal_virtual.frames == list(range(8, 16))
    assert len(set(outer._temporal_virtual.frames)) == 8
    assert outer.layers[0].frame_report == dict(kind="test-motion", frame=1, subframe_index=15)
    assert outer._supersampled_reports["marker"] == outer.layers[0].frame_report
    assert result.tobytes() == super_sampling.temporal_supersample(outer, 1)[0].tobytes()
