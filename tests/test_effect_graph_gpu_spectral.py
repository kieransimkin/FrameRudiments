"""M4.2 real ModernGL parity, deterministic output and resource gates.

These are *real* shader tests, not a simulated GPU. The dedicated CI workflow
requires an EGL context before running them; a skipped local test is not GPU
validation evidence.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_spectral import spectral_displacement
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported


@pytest.fixture(scope="module")
def gl():
    pytest.importorskip("moderngl")
    try:
        engine = GLMatteBackend(backend="egl")
    except Exception as exc:
        pytest.skip(f"ModernGL EGL 3.3 unavailable: {exc}")
    yield engine
    engine.close()


def samples():
    size = (96, 64)
    image = Image.new("RGBA", size)
    painter = ImageDraw.Draw(image)
    painter.rectangle((4, 5, 30, 40), fill=(255, 0, 0, 255))
    painter.ellipse((33, 5, 69, 47), fill=(0, 250, 15, 170))
    painter.polygon([(76, 7), (93, 50), (57, 54)], fill=(30, 0, 255, 220))
    painter.rectangle((10, 46, 24, 61), fill=(245, 2, 20, 128))
    # Hidden colour must not appear after fractional interpolation.
    image.putpixel((45, 53), (0, 255, 0, 0))
    image.putpixel((46, 53), (0, 255, 0, 0))
    return image


def bands(space="rgb", *, shift=2.25):
    return [
        {"id": "warm", "source_color": "#ff0000", "target_color": "#00ccff",
         "space": space, "offset": [shift, -1.5], "recolor": .7,
         "opacity": .65, "tolerance": .10, "softness": .15},
        {"id": "cool", "source_color": "#0000ff", "target_color": "#ff00ff",
         "space": space, "offset": [-1.25, 1.5], "recolor": .8,
         "opacity": .85, "tolerance": .08, "softness": .12},
    ]


def compare(cpu, gpu, *, limit=8, alpha_limit=5):
    lhs = np.asarray(cpu, dtype=np.int16)
    rhs = np.asarray(gpu, dtype=np.int16)
    assert lhs.shape == rhs.shape
    delta = np.abs(lhs - rhs)
    # Invisible RGB is undefined after straight-alpha reconstruction; alpha
    # and RGB at visible pixels still must match the CPU reference closely.
    assert int(np.max(delta[..., 3])) <= alpha_limit, int(np.max(delta[..., 3]))
    visible = np.maximum(lhs[..., 3], rhs[..., 3]) > 6
    if np.any(visible):
        assert int(np.max(delta[..., :3][visible])) <= limit, int(np.max(delta[..., :3][visible]))


@pytest.mark.parametrize("space", ["rgb", "hsv", "oklab"])
@pytest.mark.parametrize("mode", ["replace", "overlay"])
@pytest.mark.parametrize("alpha_mode", ["expanded", "source"])
def test_real_gpu_matches_cpu_multi_band(gl, space, mode, alpha_mode):
    source = samples()
    spec = {"bands": bands(space), "spectral_mode": mode,
            "alpha_mode": alpha_mode, "mix": .75}
    original = source.tobytes()
    expected = spectral_displacement(source, spec)
    observed = gl.spectral_displacement(source, spec)
    compare(expected, observed)
    assert source.tobytes() == original


@pytest.mark.parametrize("offset", [0, 1, -.5, 2.25])
def test_real_gpu_fractional_bilinear_and_colour_alpha(gl, offset):
    source = samples()
    spec = {"spectral_mode": "replace", "bands": [
        {"id": "red", "source_color": "#ff0000", "space": "rgb",
         "tolerance": .02, "softness": 0, "offset": [offset, .5],
         "recolor": 1.0, "target_color": "#00ffff", "opacity": .5}]}
    compare(spectral_displacement(source, spec), gl.spectral_displacement(source, spec))


def test_real_gpu_repeatable_and_graph_forks(gl):
    source = samples()
    graph = EffectGraph({"nodes": [
        {"id": "s", "type": "source"},
        {"id": "band", "type": "spectral_displacement", "inputs": ["s"],
         "spectral_mode": "replace", "bands": bands("oklab")},
        {"id": "original", "type": "copy", "inputs": ["s"]},
        {"id": "combined", "type": "transition", "inputs": ["original", "band"],
         "progress": .65},
    ], "output": "combined"})
    pure = graph.render({"s": source})
    first = graph.render({"s": source}, gpu_backend=gl, gpu_strict=True)
    second = graph.render({"s": source}, gpu_backend=gl, gpu_strict=True)
    compare(pure, first)
    assert first.tobytes() == second.tobytes()
    assert gl.last_dispatch == {"gpu_nodes": 1, "cpu_fallbacks": 0}


def test_gpu_zero_mix_is_exact_no_op(gl):
    source = samples()
    spec = {"bands": bands(), "mix": 0}
    assert gl.spectral_displacement(source, spec).tobytes() == source.tobytes()
    assert gl.spectral_displacement(source, spec).tobytes() == source.tobytes()


def test_gpu_budget_rejects_before_allocations(gl):
    source = samples()
    previous = gl.max_working_set_bytes
    try:
        gl.max_working_set_bytes = 512
        with pytest.raises(GPUUnsupported, match="budget"):
            gl.spectral_displacement(source, {"bands": bands()})
    finally:
        gl.max_working_set_bytes = previous


def test_gpu_backend_releases_resources_on_shader_error(gl, monkeypatch):
    source = samples()
    original_draw = gl._get_shader

    def fail_once(name, fragment):
        if name == "spectral_band":
            raise RuntimeError("synthetic shader error")
        return original_draw(name, fragment)

    monkeypatch.setattr(gl, "_get_shader", fail_once)
    with pytest.raises(RuntimeError, match="synthetic shader error"):
        gl.spectral_displacement(source, {"bands": bands()})
    monkeypatch.setattr(gl, "_get_shader", original_draw)
    assert gl.spectral_displacement(source, {"bands": bands()}).size == source.size
