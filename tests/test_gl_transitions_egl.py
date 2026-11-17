"""Mandatory M9.2 real-EGL acceptance: never skip shader compilation."""
from hashlib import sha256
from pathlib import Path

from PIL import Image

from framerudiments.effect_graph_gpu import GLMatteBackend
from framerudiments.gl_transitions import render_gl_transition

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/gl-transition-crossfade.glsl"


def test_real_egl_parity_and_no_source_mutation():
    a = Image.new("RGBA", (4, 3), (255, 0, 0, 120))
    b = Image.new("RGBA", (4, 3), (0, 40, 220, 255))
    before = (a.tobytes(), b.tobytes())
    config = {"transition_style": "gl_transition", "shader_path": str(EXAMPLE),
              "shader_sha256": sha256(EXAMPLE.read_bytes()).hexdigest(),
              "trusted_shader": True, "progress": 0.5,
              "time_seconds": 1.25, "time_delta_seconds": 1 / 24, "frame_index": 30}
    with GLMatteBackend(backend="egl") as backend:
        result = render_gl_transition(backend, a, b, config)
        assert backend.last_transport["upload_bytes"] > 0
    expected = (128, 20, 110, 188)
    actual = result.getpixel((1, 1))
    assert all(abs(c - e) <= 1 for c, e in zip(actual, expected, strict=True))
    assert (a.tobytes(), b.tobytes()) == before
