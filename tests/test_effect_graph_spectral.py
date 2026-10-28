"""Spectral colour-band CPU reference, alpha, ordering and control contracts."""

import numpy as np
import pytest
from PIL import Image

from framerudiments.effect_graph_spectral import (
    spectral_displacement, validate_spectral, spectral_control_apply,
    spectral_control_base, spectral_control_parts,
)


def band(**options):
    return {"id": "warm", "source_color": "#ff0000", "space": "rgb",
            "tolerance": 0.02, "softness": 0, **options}


def config(**options):
    return {"bands": [band()], **options}


def rgb_strip():
    im = Image.new("RGBA", (5, 1))
    im.putpixel((0, 0), (255, 0, 0, 255))
    im.putpixel((2, 0), (0, 0, 255, 255))
    return im


def test_integer_xy_offset_isolates_selected_colour_not_neighbours():
    im = rgb_strip()
    cfg = config(bands=[band(offset=[1, 0])], spectral_mode="replace")
    out = spectral_displacement(im, cfg)
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((1, 0)) == (255, 0, 0, 255)
    assert out.getpixel((2, 0)) == (0, 0, 255, 255)
    assert im.getpixel((0, 0)) == (255, 0, 0, 255)


def test_fractional_displacement_uses_premultiplied_alpha():
    im = Image.new("RGBA", (3, 1))
    im.putpixel((0, 0), (255, 0, 0, 255))
    im.putpixel((1, 0), (0, 255, 0, 0))  # hidden green must not leak
    cfg = config(bands=[band(offset=[.5, 0])], spectral_mode="replace")
    out = spectral_displacement(im, cfg)
    assert [out.getpixel((x, 0))[3] for x in range(3)] == [128, 128, 0]
    assert out.getpixel((0, 0))[:3] == (255, 0, 0)
    assert out.getpixel((1, 0))[:3] == (255, 0, 0)


def test_recolour_and_per_band_opacity():
    im = rgb_strip()
    cfg = config(bands=[band(offset=[1, 0], target_color="#00ffff", recolor=1, opacity=.5)],
                 spectral_mode="replace")
    result = spectral_displacement(im, cfg)
    assert result.getpixel((1, 0)) == (0, 255, 255, 128)
    assert 120 <= result.getpixel((0, 0))[3] <= 135


def test_alpha_mode_source_vs_expanded():
    im = rgb_strip()
    cfg = config(bands=[band(offset=[1, 0])], spectral_mode="replace")
    expanded = spectral_displacement(im, cfg)
    clipped = spectral_displacement(im, {**cfg, "alpha_mode": "source"})
    assert expanded.getpixel((1, 0))[3] == 255
    assert clipped.getpixel((1, 0))[3] == 0


def test_mix_zero_is_bitwise_original_and_mix_half_correct_premultiplied():
    im = rgb_strip()
    cfg = config(bands=[band(offset=[1, 0], target_color="#00ffff", recolor=1)],
                 spectral_mode="replace")
    assert spectral_displacement(im, {**cfg, "mix": 0}).tobytes() == im.tobytes()
    halfway = spectral_displacement(im, {**cfg, "mix": .5})
    assert halfway.getpixel((1, 0))[3] in (127, 128)
    assert halfway.getpixel((1, 0))[:3] == (0, 255, 255)


def test_separate_bands_same_source_can_have_distinct_offsets_and_destinations():
    im = rgb_strip()
    cfg = {"spectral_mode": "replace", "bands": [
        band(id="one", offset=[1, 0], target_color="#00ffff", recolor=1, opacity=.5),
        band(id="two", offset=[2, 0], target_color="#ff00ff", recolor=1, opacity=1),
    ]}
    out = spectral_displacement(im, cfg)
    assert out.getpixel((1, 0))[:3] == (0, 255, 255)
    assert out.getpixel((2, 0))[:3] == (255, 0, 255)


def test_hsv_hue_wrap_works_near_red_boundary():
    im = Image.new("RGBA", (1, 1), (255, 0, 7, 255))
    cfg = config(bands=[band(space="hsv", tolerance=.05)])
    result = spectral_displacement(im, cfg)
    assert result.getpixel((0, 0))[3] == 255


def test_oklab_can_match_approximate_red_and_exclude_blue():
    im = rgb_strip()
    cfg = config(bands=[band(space="oklab", tolerance=.12)], spectral_mode="replace")
    out = spectral_displacement(im, cfg)
    assert out.getpixel((0, 0))[3] == 255
    assert out.getpixel((2, 0)) == (0, 0, 255, 255)


def test_chunking_does_not_change_pixels_or_order():
    rng = np.random.default_rng(42)
    im = Image.fromarray(rng.integers(0, 256, (19, 23, 4), dtype=np.uint8), "RGBA")
    cfg = config(bands=[band(offset=[1.25, -3.5], tolerance=.6, softness=.2)], mix=.65)
    a = spectral_displacement(im, cfg, tile_rows=1)
    b = spectral_displacement(im, cfg, tile_rows=12)
    assert a.tobytes() == b.tobytes()
    assert spectral_displacement(im, cfg, tile_rows=7).tobytes() == a.tobytes()


def test_frame_local_component_controls_do_not_mutate_original():
    cfg = config()
    moved = spectral_control_apply(cfg, "bands.warm.offset.x", 2.5)
    recolored = spectral_control_apply(moved, "bands.warm.target_color.g", 255)
    space = spectral_control_apply(recolored, "bands.warm.space", "hsv")
    assert spectral_control_base(space, "bands.warm.offset.x") == 2.5
    assert spectral_control_base(space, "bands.warm.target_color.g") == 255
    assert spectral_control_base(space, "bands.warm.space") == "hsv"
    assert spectral_control_base(cfg, "bands.warm.offset.x") == 0
    assert cfg["bands"][0]["source_color"] == "#ff0000"
    assert spectral_control_parts(cfg, "bands.missing.offset.x") is None


@pytest.mark.parametrize("invalid", [
    {"bands": []},
    {"bands": [band(offset=[float("nan"), 0])]},
    {"bands": [band(opacity=2)]},
    {"bands": [band(space="cmyk")]},
    {"bands": [band(source_color="bad-colour")]},
    {"bands": [band(id="same"), band(id="same")]},
    {"bands": [band(id="not valid")]},
    {"bands": [band()] * 9},
    {"bands": [band()], "mix": float("nan")},
    {"bands": [band()], "spectral_mode": "not-valid"},
])
def test_invalid_spectral_specs_fail_before_render(invalid):
    with pytest.raises(ValueError):
        validate_spectral(invalid)


@pytest.mark.parametrize("path,value", [
    ("bands.warm.offset.y", float("inf")),
    ("bands.warm.source_color.r", 256),
    ("bands.warm.space", "cmyk"),
    ("bands.warm.tolerance", True),
    ("bands.warm.other", 1),
])
def test_invalid_spectral_controls_fail(path, value):
    with pytest.raises(ValueError):
        spectral_control_apply(config(), path, value)


def test_spectral_validation_imports_without_optional_numpy(monkeypatch):
    """Loading the graph registry must not require NumPy on a base install."""
    import builtins
    import importlib.util
    from pathlib import Path

    name = "isolated_spectral_without_numpy"
    path = Path(__file__).resolve().parents[1] / "src/framerudiments/effect_graph_spectral.py"
    spec = importlib.util.spec_from_file_location(name, path)
    isolated = importlib.util.module_from_spec(spec)
    actual_import = builtins.__import__

    def without_numpy(module, *args, **kwargs):
        if module == "numpy":
            raise ImportError("NumPy deliberately unavailable")
        return actual_import(module, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(builtins, "__import__", without_numpy)
        spec.loader.exec_module(isolated)
        isolated.validate_spectral(config())
        with pytest.raises(RuntimeError, match="requires NumPy"):
            isolated.spectral_displacement(rgb_strip(), config())
