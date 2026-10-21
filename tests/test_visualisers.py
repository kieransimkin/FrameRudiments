from pathlib import Path
from types import SimpleNamespace
import os

import pytest
from PIL import Image

from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.io import read_json, write_json
from framerudiments.project import Project
from framerudiments.visualisers import EffectAudio, EffectChain, apply_palette_grade, effect_specs


def project(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    assert font and Path(font).is_file(), "Rendering checks require an explicit font"
    path = create_demo(tmp_path / "demo", font, width=160, height=240)
    data = read_json(path)
    data["audio"].update(start_sample=12000, end_sample=36000, repeats=2, fade_seconds=0)
    write_json(path, data)
    from framerudiments.output_quality import OutputQuality

    result = Project.load(path)
    result.output_quality = OutputQuality({"enabled": False})
    return result


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        [dict(id="x", engine="wrong")],
        [dict(id="x", engine="projectm", input_mix=0)],
        [dict(id="x", engine="butterchurn", seed=True)],
        [dict(id="x", engine="projectm", preset="unknown")],
        [dict(id="x", engine="projectm", preset=dict(path="external.milk", sha256="0" * 64))],
        [dict(id="x", engine="projectm", mesh_width=128)],
        [dict(id="x", engine="butterchurn", mesh_width=15)],
        [dict(id="x", engine="butterchurn", output_palette_grade={"stops": ["bad", "#fff"]})],
        [dict(id="x", engine="projectm"), dict(id="x", engine="butterchurn")],
    ],
)
def test_effect_validation(tmp_path, bad):
    p = project(tmp_path)
    p.data["effects"] = bad
    with pytest.raises(ValueError):
        Compositor(p)


def test_presets_hashed_and_optional_runtime_not_loaded_for_plan(tmp_path):
    p = project(tmp_path)
    p.data["effects"] = [
        dict(id="native", engine="projectm", preset="julia"),
        dict(id="web", engine="butterchurn", preset="kaleidoscope"),
    ]
    compositor = Compositor(p)
    assert [item["engine"] for item in compositor.effect_specs] == ["projectm", "butterchurn"]
    assert all(item["preset"] in p.inputs for item in compositor.effect_specs)
    p.verify_inputs()


def test_palette_grade_maps_luminance_to_ordered_stops_and_preserves_alpha():
    image = Image.new("RGBA", (3, 1))
    image.putdata([(0, 0, 0, 17), (128, 128, 128, 99), (255, 255, 255, 231)])
    config = dict(stops=["#0b101c", "#a9633f", "#f2e3c6"], amount=1)
    result = apply_palette_grade(image, config)
    assert [result.getpixel((x, 0)) for x in range(3)] == [
        (11, 16, 28, 17),
        (169, 100, 64, 99),
        (242, 227, 198, 231),
    ]


def test_effect_spec_retains_mesh_and_output_grade(tmp_path):
    p = project(tmp_path)
    p.data["effects"] = [
        dict(
            id="graded",
            engine="butterchurn",
            mesh_width=128,
            mesh_height=72,
            output_palette_grade=dict(
                stops=["#100B19", "#171B37", "#572417", "#8A3C25", "#C49986"], amount=0.85
            ),
        )
    ]
    spec = effect_specs(p)[0]
    assert (spec["mesh_width"], spec["mesh_height"]) == (128, 72)
    assert spec["output_palette_grade"] == dict(
        stops=["#100b19", "#171b37", "#572417", "#8a3c25", "#c49986"], amount=0.85
    )


def test_pcm_reuses_exact_trim_repeat_chain_and_causal_windows(tmp_path):
    p = project(tmp_path)
    ffmpeg = os.environ["FRAMERUDIMENTS_FFMPEG"]
    audio = EffectAudio(p, ffmpeg, tmp_path)
    try:
        assert audio.samples == 48000
        assert set(audio.at(0)) == {0}
        assert audio.at(20).tobytes() == audio.at(50).tobytes()
        assert max(abs(value) for value in audio.at(20)) > 0.2
        assert audio.evidence["filters"].startswith("atrim=start_sample=12000:end_sample=36000,")
    finally:
        audio.close()
    assert audio.data is None and audio.handle is None


def test_chain_full_composition_order_sequential_contract_and_cleanup(tmp_path):
    p = project(tmp_path)
    p.data["effects"] = [dict(id="first", engine="projectm"), dict(id="second", engine="butterchurn")]
    specs = effect_specs(p)
    calls = []

    class Fake:
        def __init__(self, size, fps, preset, mix, seed, work, **kwargs):
            self.index = len(calls)
            calls.append(self)
            self.evidence = dict(engine="test")
            self.closed = False

        def process(self, image, context, pcm):
            assert image.size == (160, 240)
            if self.index == 0:
                # Composition already contains caption/identity glyphs.
                assert image.crop((16, 30, 145, 116)).getchannel("R").getextrema()[1] > 200
                image = image.convert("RGBA")
                image.putpixel((0, 0), (250, 0, 0, 255))
            else:
                assert image.getpixel((0, 0)) == (250, 0, 0, 255)
                image.putpixel((0, 0), (0, 250, 0, 255))
            return image

        def close(self):
            self.closed = True

    chain = EffectChain(
        p,
        specs,
        os.environ["FRAMERUDIMENTS_FFMPEG"],
        tmp_path,
        factories=dict(projectm=Fake, butterchurn=Fake),
    )
    im, ctx = Compositor(p).frame(0)
    assert chain.process(im, ctx).getpixel((0, 0)) == (0, 250, 0)
    with pytest.raises(ValueError, match="sequential"):
        chain.process(im, SimpleNamespace(frame=2))
    chain.close()
    chain.close()
    assert all(backend.closed for backend in calls)
    assert chain.evidence()["sequential_frames"] == 1


def test_partial_chain_failure_closes_prior_engine(tmp_path):
    p = project(tmp_path)
    p.data["effects"] = [dict(id="first", engine="projectm"), dict(id="second", engine="butterchurn")]
    closed = []

    class First:
        def __init__(self, *args):
            pass

        def close(self):
            closed.append(True)

    def fail(*args):
        raise RuntimeError("second engine failed")

    with pytest.raises(RuntimeError, match="second engine"):
        EffectChain(
            p,
            effect_specs(p),
            os.environ["FRAMERUDIMENTS_FFMPEG"],
            tmp_path,
            factories=dict(projectm=First, butterchurn=fail),
        )
    assert closed == [True]


def test_projectm_preset_timeline_switches_once_on_first_frame_at_or_after_boundary(tmp_path, monkeypatch):
    p = project(tmp_path)
    p.data["effects"] = [
        dict(
            id="sections",
            engine="projectm",
            preset="feedback",
            preset_timeline=[
                dict(at_source_seconds=0.25, preset="julia", transition_seconds=0.2),
                dict(at_source_seconds=0.5, preset="kaleidoscope", transition_seconds=0.3),
            ],
        )
    ]

    class Audio:
        def __init__(self, project, *args):
            self.project = project
            self.evidence = {}

        def at(self, frame):
            return [0.0] * 8

        def close(self):
            pass

    monkeypatch.setattr(__import__("framerudiments.visualisers", fromlist=["EffectAudio"]), "EffectAudio", Audio)
    calls = []

    class Fake:
        def __init__(self, *args):
            self.evidence = {}

        def switch_preset(self, preset, transition):
            calls.append((Path(preset).name, transition))

        def process(self, image, context, pcm):
            return image.convert("RGBA")

        def close(self):
            pass

    chain = EffectChain(p, effect_specs(p), None, tmp_path, factories={"projectm": Fake})
    try:
        image = Image.new("RGB", (p.width, p.height))
        for frame, source in enumerate((0.2, 0.25, 0.4, 0.51)):
            chain.process(image, SimpleNamespace(frame=frame, source_seconds=source))
        assert calls == [("julia.milk", 0.2), ("kaleidoscope.milk", 0.3)]
        assert [event["frame"] for event in chain.evidence()["preset_switches"]] == [1, 3]
    finally:
        chain.close()
