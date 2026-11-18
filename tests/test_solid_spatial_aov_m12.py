"""M12.2 geometry-derived native AOVs (all three real CPU shape families)."""
from types import SimpleNamespace

import numpy as np
import pytest

from framerudiments.colour_aov import AOVFrame, scene_linear_to_srgb8
from framerudiments.solid_spatial_aov import SCHEMA, render_solid_aov
from framerudiments.solid_spatial_renderers import SolidSpatialRenderer


def motion(index, values=(.37, -.24)):
    return SimpleNamespace(context=SimpleNamespace(frame=index, output_seconds=index / 60),
                           values=values)


def scene(kind="torus", **options):
    return SolidSpatialRenderer((112, 88), dict(geometry=kind, fog=0, glow=0, **options))


@pytest.mark.parametrize("shape", ["torus", "sphere", "terrain"])
def test_real_geometry_has_depth_camera_normals_and_immutable_evidence(shape):
    renderer = scene(shape, background="#00000000")
    output = render_solid_aov(renderer, motion(20))
    assert isinstance(output, AOVFrame)
    assert output.size == (112, 88)
    assert output.colour_space == "Linear-sRGB"
    assert output.velocity is None
    active = output.depth > 0
    assert np.count_nonzero(active) > 50
    assert np.all(output.alpha[active] == 1.0)
    assert np.all(output.depth[active] > 0.12)
    assert np.all(output.normals[~active] == 0.0)
    assert np.allclose(np.linalg.norm(output.normals[active], axis=1), 1, atol=1e-6)
    assert output.alpha[0, 0] == 0
    assert output.depth[0, 0] == 0
    assert not output.rgb.flags.writeable
    assert not output.depth.flags.writeable
    assert set(output.evidence()["channels"]) == {"RGB", "A", "Z", "N"}
    with pytest.raises(ValueError):
        output.depth[0, 0] = 999.0


def test_opt_in_capture_is_seek_order_independent_and_does_not_mutate_sdr():
    renderer = scene("sphere")
    before = renderer.render(motion(20)).tobytes() if hasattr(renderer, "render") else None
    original_report = renderer.frame_report
    a = render_solid_aov(renderer, motion(20)).evidence()
    render_solid_aov(renderer, motion(9))
    b = render_solid_aov(renderer, motion(20)).evidence()
    assert a == b
    assert renderer.frame_report == original_report
    if before is not None:
        assert renderer.render(motion(20)).tobytes() == before


def test_higher_energy_light_survives_float32_above_one_without_sdr_clip():
    renderer = scene("sphere", metallic=.95, roughness=.12)
    low = render_solid_aov(renderer, motion(12), light_stops=0)
    high = render_solid_aov(renderer, motion(12), light_stops=8)
    mask = low.depth > 0
    assert np.max(high.rgb[mask]) > 1.0
    assert np.max(high.rgb[mask]) > np.max(low.rgb[mask])
    assert np.array_equal(high.depth, low.depth)
    assert np.array_equal(high.normals, low.normals)
    assert scene_linear_to_srgb8(high.rgb, tone_map="clip").dtype == np.uint8


def test_velocity_tracks_reprojected_native_vertices_not_inferred_from_rgb():
    renderer = scene("sphere", orbit=68)
    previous = motion(24, (.3, -.25))
    current = motion(25, (.4, -.13))
    output = render_solid_aov(renderer, current, previous_frame=previous)
    assert output.velocity.shape == (88, 112, 2)
    assert output.velocity.dtype == np.float32
    assert not output.velocity.flags.writeable
    valid = output.depth > 0
    assert np.max(np.abs(output.velocity[valid])) > .015
    assert np.all(output.velocity[~valid] == 0)
    assert "motion" in output.channels()
    assert output.evidence()["velocity_units"] == "pixels_per_encoded_frame_if_supplied"


def test_static_mesh_with_no_camera_orbit_has_zero_motion_when_values_equal():
    renderer = scene("sphere", orbit=0, rotation=0)
    aov = render_solid_aov(renderer, motion(9, (.2, -.3)), previous_frame=motion(8, (.2, -.3)))
    mask = aov.depth > 0
    assert np.max(np.abs(aov.velocity[mask])) < 2e-5


def test_velocity_binds_exact_adjacency_and_rejects_reversed_clocks():
    renderer = scene()
    with pytest.raises(ValueError, match="adjacent"):
        render_solid_aov(renderer, motion(10), previous_frame=motion(7))
    with pytest.raises(ValueError, match="adjacent"):
        render_solid_aov(renderer, motion(10), previous_frame=motion(11))
    with pytest.raises(ValueError, match="adjacent"):
        render_solid_aov(renderer, motion(0), previous_frame=motion(0))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, -9, 9, "4"])
def test_invalid_hdr_light_strength_fails_closed(bad):
    with pytest.raises(ValueError, match="light_stops"):
        render_solid_aov(scene(), motion(3), light_stops=bad)


@pytest.mark.parametrize("bad", [(.1, float("nan")), (2.0, -.1), (), (0.0, "x")])
def test_invalid_native_motion_channels_fail_closed(bad):
    with pytest.raises(ValueError, match="normalized native"):
        render_solid_aov(scene(), motion(3, bad))


def test_budget_checked_before_allocating_large_capture():
    big = scene()
    big.size = (8000, 8000)
    with pytest.raises(ValueError, match="budget"):
        render_solid_aov(big, motion(1), previous_frame=motion(0))


def test_opt_in_adapter_rejects_unrelated_renderers():
    with pytest.raises(ValueError, match="SolidSpatialRenderer"):
        render_solid_aov(object(), motion(1))


def test_default_background_stays_sdr_converted_not_invented_hdr():
    renderer = scene(background="#101b2e")
    out = render_solid_aov(renderer, motion(9))
    assert out.alpha[0, 0] == 1.0
    assert out.depth[0, 0] == 0.0
    assert np.array_equal(scene_linear_to_srgb8(out.rgb[0:1, 0:1], tone_map="clip"),
                          np.array([[[16, 27, 46]]], dtype=np.uint8))


def test_protocol_is_versioned_and_optional_velocity_is_not_faked():
    renderer = scene("terrain")
    assert SCHEMA == "framerudiments.solid-spatial-aov.v1"
    frame = render_solid_aov(renderer, motion(15))
    assert frame.velocity is None
    assert "motion" not in frame.channels()


def test_native_exr_export_passes_geometry_provenance(monkeypatch, tmp_path):
    from framerudiments import solid_spatial_aov
    seen = {}

    def fake_write(path, image, *, source_evidence):
        seen["path"] = path
        seen["image"] = image
        seen["source"] = source_evidence
        return {"verified": True}

    monkeypatch.setattr("framerudiments.colour_aov.write_exr", fake_write)
    target = tmp_path / "test.exr"
    assert solid_spatial_aov.export_solid_exr(
        scene("torus"), motion(11), target, previous_frame=motion(10)) == {"verified": True}
    assert seen["path"] == target
    assert seen["image"].velocity is not None
    assert seen["source"]["producer"] == "native-cpu-solid-triangle-raster"
    assert seen["source"]["output_frame"] == 11
    assert seen["source"]["previous_frame"] == 10


def test_native_sdr_reference_uses_same_covered_pixels_and_pbr_when_fog_off():
    renderer = scene("terrain")
    if not hasattr(renderer, "render"):
        # The standalone development fixture exercises the same projection and
        # raster kernels; actual repository CI always has renderer.render().
        return
    sample = motion(19)
    old = np.asarray(renderer.render(sample))
    linear = render_solid_aov(renderer, sample)
    expected = scene_linear_to_srgb8(linear.rgb, tone_map="clip")
    assert np.max(np.abs(old[..., :3].astype("int16") - expected.astype("int16"))) <= 1
    assert np.max(np.abs(old[..., 3].astype("int16") -
                         np.rint(linear.alpha * 255).astype("int16"))) <= 1
