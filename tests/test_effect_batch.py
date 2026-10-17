"""Extra renderer and effect test matrix; synthetic source-clocked test vectors."""
from pathlib import Path
import sys

from PIL import Image, ImageDraw
import pytest

from framerudiments.rudiment_renderers import default_renderers
from framerudiments.visualisation_catalogue import EFFECT_VIEWS, ALL_VIEWS
from framerudiments.visual_style import VisualStyle
from framerudiments.post_effects import POST_RANGES

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_visualisation_gallery import fixture  # noqa: E402


def test_all_213_names_register_without_collisions():
    registry = default_renderers()
    names = registry.names
    assert len(names) == len(set(names)) == 213
    assert len(EFFECT_VIEWS) == 60
    assert set(names) == {"particles", "bars", "waveform", "path", "spectrum"} | {x.name for x in ALL_VIEWS}
    # Regression for the original CI cascade: circular modes must never be
    # passed to the older AdvancedSpectrumRenderer factory.
    r, identity = registry.create("spectrum-annular-bars", (220, 180), {})
    assert identity["source"]["path"].endswith("circular_renderers.py")
    assert type(r).__name__ == "CircularRenderer"


@pytest.mark.parametrize("spec", EFFECT_VIEWS, ids=lambda x: x.name)
def test_every_new_effect_is_deterministic_with_synthetic_input(spec):
    registry = default_renderers()
    renderer, identity = registry.create(spec.name, (320, 210),
                                         {"background": "#0a1228", "padding": 8})
    assert identity["source"]["sha256"]
    assert identity["source"]["path"].endswith("effect_renderers.py")
    source = fixture(95, spectral=spec.family != "motion")
    first = renderer.render(source)
    assert first.mode == "RGBA" and first.size == (320, 210)
    assert first.getbbox()
    assert renderer.render(source).tobytes() == first.tobytes()
    earlier = renderer.render(fixture(35, spectral=spec.family != "motion"))
    assert first.tobytes() != earlier.tobytes(), spec.name
    if spec.family != "motion":
        assert renderer.frame_report["kind"] == "stemlab-fourier-band-spectrum"
        with pytest.raises(ValueError, match="calibrated"):
            renderer.render(fixture(95, spectral=False))


@pytest.mark.parametrize("name", ["spectrum-led-matrix", "spectrogram-isobands", "waveform-phosphor"])
def test_new_style_parameters_really_affect_geometry(name):
    r=default_renderers()
    a,_=r.create(name,(300,190),{"line_width":2,"segments":8,"gap":.1,"colour_map":"axis"})
    b,_=r.create(name,(300,190),{"line_width":6,"segments":20,"gap":.4,"colour_map":"rainbow",
                                 "amplitude_curve":"square","gain":1.4})
    frame=fixture(100,spectral=not name.startswith("waveform-"))
    assert a.render(frame).tobytes()!=b.render(frame).tobytes()
    with pytest.raises(ValueError,match="amplitude_curve"):
        r.create(name,(300,190),{"amplitude_curve":"not-a-curve"})
    with pytest.raises(ValueError,match="renderer style"):
        r.create(name,(300,190),{"exec":"no"})


def _image():
    image=Image.new("RGBA",(90,70))
    draw=ImageDraw.Draw(image)
    draw.ellipse((8,6,82,63),fill="#90d8fb")
    draw.line((8,34,82,34),fill="#fb703e",width=6)
    return image


@pytest.mark.parametrize("key,nonzero", [
    ("pixel_size",4), ("scanlines",.8), ("grain",.8),
    ("chromatic_shift",4), ("vignette",.9), ("edge_mix",.7),
    ("dither",.8), ("halftone",.8), ("solarize",1),
    ("glitch",1), ("sharpen",2)])
def test_effects_are_opt_in_alpha_preserving_and_deterministic(key,nonzero):
    source=_image()
    assert VisualStyle({}).apply(source.copy()).tobytes() == source.tobytes()
    style=VisualStyle({key:nonzero})
    first=style.apply(source.copy())
    assert first.size==source.size and first.mode=="RGBA"
    assert first.getpixel((0,0))[3]==0 and first.getpixel((89,69))[3]==0
    assert first.tobytes()==style.apply(source.copy()).tobytes()
    assert first.tobytes()!=source.tobytes()
    assert key in POST_RANGES


def test_universal_colour_gradient_and_effect_chain_combine_without_hidden_state():
    source=_image()
    style=VisualStyle({"palette":"neon", "gradient":{"mode":"angular","stops":"rainbow","blend":"replace"},
                       "pixel_size":2,"grain":.25,"grain_seed":73,"chromatic_shift":2,
                       "scanlines":.4,"dither":.3,"vignette":.6})
    a=style.apply(source.copy())
    style.apply(_image())
    assert a.tobytes()==style.apply(source.copy()).tobytes()
    assert a.getpixel((0,0))[3] == 0


@pytest.mark.parametrize("bad", [
    {"scanlines": 4}, {"grain": -1}, {"pixel_size":0},
    {"vignette":float("nan")}, {"chromatic_shift":1000},
    {"dither":-2}, {"halftone":2}, {"grain_seed":-1},
])
def test_new_visual_fields_fail_closed(bad):
    with pytest.raises(ValueError):
        VisualStyle(bad)


def test_modulation_targets_for_new_effects_are_declared():
    from framerudiments.modulation import STYLE_INTEGERS, VISUAL_RANGES
    assert {"scanlines", "vignette", "grain", "dither", "glitch", "chromatic_shift"} <= VISUAL_RANGES.keys()
    assert {"segments", "quantize", "axis", "stride", "history_limit"} <= STYLE_INTEGERS
    assert len(EFFECT_VIEWS)==60
