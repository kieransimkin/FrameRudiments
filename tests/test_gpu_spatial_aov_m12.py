"""M12.3 strict CPU / real OpenGL float-MRT AOV parity qualifications."""

import os
from types import SimpleNamespace

import numpy as np
import pytest

from framerudiments.colour_aov import AOVFrame
from framerudiments.gpu_spatial_aov import (_check, _triangles, parity_report,
                                            render_solid_aov_gpu)
from framerudiments.solid_spatial_aov import render_solid_aov
from framerudiments.solid_spatial_renderers import SolidSpatialRenderer


def frame(n, values=(0.4, -0.2)):
    return SimpleNamespace(context=SimpleNamespace(frame=n, output_seconds=n / 60), values=values)


def scene(geometry="torus", **opts):
    return SolidSpatialRenderer((128, 96), {"geometry": geometry, "fog": 0, "glow": 0,
                                           "background": "#00000000", **opts})


def require_egl():
    try:
        import moderngl
        ctx = moderngl.create_standalone_context(require=330, backend="egl")
    except (ImportError, Exception) as exc:
        if os.environ.get("FRAMERUDIMENTS_REQUIRE_GPU_AOV") == "1":
            pytest.fail(f"Required real EGL context unavailable: {exc}")
        pytest.skip(f"Opt-in real GL runtime unavailable: {exc}")
    else:
        ctx.release()


def test_mesh_stream_matches_native_geometry_and_premapped_flat_shading():
    a = scene("sphere")
    vertices = _triangles(a, frame(20), frame(19), 0)
    assert vertices.ndim == 2 and vertices.shape[1] == 12
    assert len(vertices) > 100 and len(vertices) % 3 == 0
    assert vertices.dtype == np.float32
    assert np.all(np.isfinite(vertices))
    assert np.min(vertices[:, 2]) > 0.12
    assert np.min(vertices[:, 5]) > 0.12
    assert np.max(vertices[:, 11]) > 0


def test_bounded_preflight_rejects_invalid_clock_backend_or_type_without_context():
    a = scene()
    for kwargs in ({"backend": "bad"}, {"light_stops": float("nan")},
                   {"light_stops": True}, {"previous_frame": frame(3)}):
        with pytest.raises(ValueError):
            _check(a, frame(20), kwargs.get("previous_frame"),
                   kwargs.get("light_stops", 0), kwargs.get("backend", "egl"))
    with pytest.raises(ValueError):
        _check(object(), frame(20), None, 0, "egl")


def test_parity_is_float32_and_rejects_missing_geometry_or_divergent_pixels():
    a = np.zeros((16, 18, 3), dtype=np.float32)
    a[3:12, 2:15] = [2.5, 0.8, 0.1]  # HDR, not a clipped 8-bit comparison
    z = np.zeros((16, 18), dtype=np.float32)
    z[3:12, 2:15] = 3.1
    n = np.zeros_like(a)
    n[3:12, 2:15, 2] = 1
    base = AOVFrame(a, "Linear-sRGB", depth=z, normals=n)
    identical = AOVFrame(a.copy(), "Linear-sRGB", depth=z.copy(), normals=n.copy())
    result = parity_report(base, identical)
    assert result["passed"] and result["rgb_p99_absolute"] == 0
    bad = a.copy()
    bad[3:12, 2:15, 0] = 0.5
    assert not parity_report(base, AOVFrame(bad, "Linear-sRGB", depth=z, normals=n))["passed"]
    with pytest.raises(ValueError, match="depth and normals"):
        parity_report(base, AOVFrame(a, "Linear-sRGB"))
    with pytest.raises(ValueError, match="no rasterized"):
        parity_report(AOVFrame(a, "Linear-sRGB", depth=np.zeros_like(z), normals=n),
                      AOVFrame(a, "Linear-sRGB", depth=np.zeros_like(z), normals=n))


@pytest.mark.parametrize("geometry", ["torus", "sphere", "terrain"])
def test_real_gl_depth_normals_colour_and_motion_parity(geometry):
    require_egl()
    a = scene(geometry)
    old, now = frame(19), frame(20)
    cpu = render_solid_aov(a, now, previous_frame=old, light_stops=3)
    gl, evidence = render_solid_aov_gpu(a, now, previous_frame=old, light_stops=3,
                                         return_evidence=True)
    report = parity_report(cpu, gl)
    assert report["passed"], report
    assert report["coverage_iou"] >= 0.94
    assert evidence["raster"] == "hardware_depth_test_float32_mrt"
    assert evidence["triangles"] > 0
    assert evidence["shader_sha256"]
    assert evidence["vendor"] and evidence["driver"]
    assert "motion" in gl.channels()
    assert np.max(np.abs(gl.velocity[gl.depth > 0])) > 0.001


def test_real_gl_repeatable_out_of_order_and_static_velocity_zero():
    require_egl()
    a = scene("sphere", orbit=0, depth=0)
    target = frame(20, (.2, -.3))
    earlier = frame(19, (.2, -.3))
    first = render_solid_aov_gpu(a, target, previous_frame=earlier)
    render_solid_aov_gpu(a, frame(9))
    second = render_solid_aov_gpu(a, target, previous_frame=earlier)
    assert first.evidence() == second.evidence()
    assert np.max(np.abs(second.velocity[second.depth > 0])) < 0.002


def test_real_gl_preserves_highlights_above_one():
    require_egl()
    renderer = scene("sphere", metallic=.95, roughness=.12)
    output = render_solid_aov_gpu(renderer, frame(20), light_stops=8)
    assert np.max(output.rgb[output.depth > 0]) > 1
