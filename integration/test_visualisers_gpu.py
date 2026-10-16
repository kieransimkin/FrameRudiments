"""Real engine acceptance, run explicitly in the GPU CI job (never silently skipped)."""
from array import array
import json
import os
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageStat
import pytest

from framerudiments.projectm_backend import ProjectMBackend, PROJECTM_COMMIT
from framerudiments.visualisers import ButterchurnBackend, ASSETS
from framerudiments.demo import create_demo, add_demo_effects
from framerudiments.io import read_json, write_json
from framerudiments.render import render


def glyph_frame(size=(320, 240)):
    image = Image.new("RGBA", size, "black")
    draw = ImageDraw.Draw(image)
    draw.text((36, 70), "LYRICS", fill="white", font=ImageFont.truetype(os.environ["FRAMERUDIMENTS_TEST_FONT"], 36))
    draw.rectangle((16, 16, 55, 50), fill="red")
    draw.rectangle((260, 186, 300, 222), fill="blue")
    return image


@pytest.mark.parametrize("engine,factory,ext", [("projectm", ProjectMBackend, "milk"), ("butterchurn", ButterchurnBackend, "json")])
@pytest.mark.parametrize("preset", ["feedback", "kaleidoscope", "julia"])
def test_actual_engine_glyph_distortion_feedback_orientation_and_cleanup(tmp_path, engine, factory, ext, preset):
    image = glyph_frame()
    backend = factory(image.size, 60, ASSETS / f"{preset}.{ext}", .75, 12, tmp_path)
    zero = array("f", [0]*2048)
    try:
        for n in range(8):
            output = backend.process(image, SimpleNamespace(frame=n, output_seconds=n/60), zero)
        output.save(tmp_path / f"{engine}-{preset}.png")
        # The actual caption region changes, not only an unrelated background.
        original = image.convert("RGB").crop((25, 55, 235, 130))
        processed = output.convert("RGB").crop((25, 55, 235, 130))
        assert sum(ImageStat.Stat(ImageChops.difference(original, processed)).mean) > 5
        assert processed.getchannel("R").getextrema()[1] > 100
        if preset == "feedback":
            # An upside-down bridge swaps the two corner markers.
            assert output.getpixel((35, 30))[0] > output.getpixel((35, 30))[2] + 80
            assert output.getpixel((280, 205))[2] > output.getpixel((280, 205))[0] + 80
        # Remove every input pixel: the engine's feedback still contains glyphs.
        black = Image.new("RGBA", image.size, "black")
        feedback = backend.process(black, SimpleNamespace(frame=8, output_seconds=8/60), zero)
        assert feedback.crop((25, 55, 235, 130)).getchannel("R").getextrema()[1] > 20
    finally:
        backend.close()
    assert all(backend.evidence["cleanup"].values())
    assert not list(tmp_path.glob("owned-headless-*"))
    if engine == "projectm":
        assert backend.evidence["source_commit"] == PROJECTM_COMMIT


def test_real_ordered_chain_encoded_lyrics_and_clock(tmp_path):
    path = create_demo(tmp_path / "demo", os.environ["FRAMERUDIMENTS_TEST_FONT"], width=160, height=240)
    add_demo_effects(path, ["butterchurn", "projectm"], "julia")
    data = read_json(path)
    data["audio"].update(start_sample=0, end_sample=24000, repeats=2, fade_seconds=.05)
    data["effects"] = [dict(id="web-feedback", engine="butterchurn", preset="feedback", input_mix=.85),
                       dict(id="native-julia", engine="projectm", preset="julia", input_mix=.85)]
    write_json(path, data)
    result = render(path, tmp_path / "encoded", preset="ultrafast")
    assert result["verification"]["full_decode"] == "passed"
    assert result["frames"] == 60
    motions = result["composition"]["motions"]
    assert "native" in motions and "native-animated-atlas" in motions
    assert motions["native-animated-atlas"]["config"]["kind"] == "named"
    stages = result["full_frame_effects"]["stages"]
    assert [stage["engine"] for stage in stages] == ["butterchurn", "projectm"]
    assert all(all(stage["runtime"]["cleanup"].values()) for stage in stages)
    assert result["full_frame_effects"]["pcm"]["samples_per_channel"] == 48000
    assert (tmp_path / "encoded/qa/composed-00000000.png").is_file()
    assert (tmp_path / "encoded/qa/encoded-00000000.png").is_file()
    assert not list((tmp_path / "encoded").glob("owned-headless-*"))
    (tmp_path / "gpu-evidence.json").write_text(json.dumps(result["full_frame_effects"], indent=2), encoding="utf-8")


def test_butterchurn_shader_failure_closes_browser(tmp_path):
    preset = read_json(ASSETS / "feedback.json")
    preset["warp"] = "shader_body { ret = THIS_IS_NOT_A_VALID_SHADER; }"
    target = tmp_path / "bad.json"
    write_json(target, preset)
    with pytest.raises(RuntimeError, match="Butterchurn"):
        ButterchurnBackend((160, 240), 60, target, .8, 0, tmp_path)
    assert not list(tmp_path.glob("owned-headless-*"))


def test_projectm_missing_preset_fails_instead_of_idle_fallback(tmp_path):
    with pytest.raises(RuntimeError, match="preset failed"):
        ProjectMBackend((160, 240), 60, tmp_path / "does-not-exist.milk", .8, 0, tmp_path)
