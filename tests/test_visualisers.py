from pathlib import Path
from types import SimpleNamespace
import os

import pytest

from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.io import read_json, write_json
from framerudiments.project import Project
from framerudiments.visualisers import EffectAudio, EffectChain, effect_specs


def project(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    assert font and Path(font).is_file(), "Rendering checks require an explicit font"
    path = create_demo(tmp_path / "demo", font, width=160, height=240)
    data = read_json(path)
    data["audio"].update(start_sample=12000, end_sample=36000, repeats=2, fade_seconds=0)
    write_json(path, data)
    return Project.load(path)


@pytest.mark.parametrize("bad", [None, {}, [dict(id="x", engine="wrong")],
    [dict(id="x", engine="projectm", input_mix=0)],
    [dict(id="x", engine="butterchurn", seed=True)],
    [dict(id="x", engine="projectm", preset="unknown")],
    [dict(id="x", engine="projectm", preset=dict(path="external.milk", sha256="0"*64))],
    [dict(id="x", engine="projectm"), dict(id="x", engine="butterchurn")]])
def test_effect_validation(tmp_path, bad):
    p = project(tmp_path)
    p.data["effects"] = bad
    with pytest.raises(ValueError):
        Compositor(p)


def test_presets_hashed_and_optional_runtime_not_loaded_for_plan(tmp_path):
    p = project(tmp_path)
    p.data["effects"] = [dict(id="native", engine="projectm", preset="julia"),
                         dict(id="web", engine="butterchurn", preset="kaleidoscope")]
    compositor = Compositor(p)
    assert [item["engine"] for item in compositor.effect_specs] == ["projectm", "butterchurn"]
    assert all(item["preset"] in p.inputs for item in compositor.effect_specs)
    p.verify_inputs()


def test_pcm_reuses_exact_trim_repeat_chain_and_causal_windows(tmp_path):
    p = project(tmp_path)
    ffmpeg = os.environ["FRAMERUDIMENTS_FFMPEG"]
    audio = EffectAudio(p, ffmpeg, tmp_path)
    try:
        assert audio.samples == 48000
        assert set(audio.at(0)) == {0}
        assert audio.at(20).tobytes() == audio.at(50).tobytes()
        assert max(abs(value) for value in audio.at(20)) > .2
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
        def __init__(self, size, fps, preset, mix, seed, work):
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
    chain = EffectChain(p, specs, os.environ["FRAMERUDIMENTS_FFMPEG"], tmp_path,
                        factories=dict(projectm=Fake, butterchurn=Fake))
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
        EffectChain(p, effect_specs(p), os.environ["FRAMERUDIMENTS_FFMPEG"], tmp_path,
                    factories=dict(projectm=First, butterchurn=fail))
    assert closed == [True]
