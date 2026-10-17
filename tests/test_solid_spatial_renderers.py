"""CPU 3D regression contract: genuine depth, controllable materials and source-clock seeking."""

from array import array
import math
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.rudiment_renderers import default_renderers
from framerudiments.solid_spatial_renderers import (
    SolidSpatialRenderer,
    _fog_pass,
    _raster_triangle,
)


def frame(seconds=1.0, values=(0.42, -0.32)):
    return SimpleNamespace(
        context=SimpleNamespace(output_seconds=seconds, frame=round(seconds * 60)),
        values=values,
    )


@pytest.mark.parametrize("kind", ("torus", "sphere", "terrain"))
def test_solid_mesh_registered_and_deterministic_when_seeking(kind):
    renderer, identity = default_renderers().create("spatial-solid-" + kind, (180, 120), {})
    assert identity["source"]["path"].endswith("solid_spatial_renderers.py")
    target = renderer.render(frame(2))
    assert target.mode == "RGBA" and target.size == (180, 120)
    assert renderer.frame_report["triangles"] > 10
    assert renderer.frame_report["covered_pixels"] > 100
    assert renderer.frame_report["depth_buffer"].startswith("per-pixel")
    assert renderer.render(frame(1)).tobytes() != target.tobytes()
    assert renderer.render(frame(2)).tobytes() == target.tobytes()


def test_z_buffer_visibility_is_independent_of_draw_order():
    size = (32, 28)
    vertices_near = ((4, 3, 2.0), (27, 4, 2.0), (14, 24, 2.0))
    vertices_far = ((4, 3, 5.0), (27, 4, 5.0), (14, 24, 5.0))

    def draw(order):
        image = Image.new("RGBA", size)
        depths = array("f", [math.inf]) * (size[0] * size[1])
        for vertices, colour in order:
            _raster_triangle(image.load(), depths, size, vertices, colour)
        return image, depths

    a, da = draw(((vertices_far, (0, 0, 255, 255)),
                  (vertices_near, (255, 0, 0, 255))))
    b, db = draw(((vertices_near, (255, 0, 0, 255)),
                  (vertices_far, (0, 0, 255, 255))))
    assert a.tobytes() == b.tobytes()
    assert a.getpixel((15, 11)) == (255, 0, 0, 255)
    assert da[11 * size[0] + 15] == pytest.approx(2.0)
    assert da == db


def test_camera_light_and_material_controls_affect_result():
    base = SolidSpatialRenderer((180, 120), {"geometry": "torus", "fog": 0, "glow": 0})
    other = SolidSpatialRenderer((180, 120), {
        "geometry": "torus", "camera_distance": 3.3, "metallic": 1,
        "roughness": 0.1, "light_direction": [1, 0, 1], "fog": 0, "glow": 0,
    })
    assert base.render(frame()).tobytes() != other.render(frame()).tobytes()
    assert base.render(frame(values=(-0.9, 0.2))).tobytes() != base.render(frame()).tobytes()


def test_ray_marched_fog_respects_occluder_depth():
    size = (24, 12)
    depths = array("f", [math.inf]) * (size[0] * size[1])
    depths[5 * size[0] + 8] = 1.0
    image = Image.new("RGBA", size, "#112233")
    out = _fog_pass(image, depths, distance=4.0, intensity=0.95,
                    colour=(220, 230, 255), steps=8, resolution=64, time=0)
    # The same volume is less opaque along a ray terminated by foreground mesh.
    near = out.getpixel((8, 5))
    far = out.getpixel((9, 5))
    assert near[0] < far[0]
    assert out.mode == "RGBA" and out.size == size


@pytest.mark.parametrize("invalid", (
    {"geometry": "cube"}, {"ambient": -0.1}, {"metallic": 1.1},
    {"roughness": 0}, {"fog": float("nan")}, {"fog_steps": 1},
    {"fog_resolution": 10}, {"light_direction": [0, 0, 0]},
    {"light_direction": [1, "wrong", 0]}, {"colours": []},
    {"fov": 180}, {"unexpected_option": 1},
))
def test_invalid_solid_scene_configuration_fails_closed(invalid):
    with pytest.raises(ValueError):
        SolidSpatialRenderer((160, 90), invalid)


def test_visualizer_layer_is_opaque_and_compatible_with_alpha_compositor():
    renderer = SolidSpatialRenderer((128, 80), {"background": "#0b1023"})
    foreground = renderer.render(frame())
    composite = Image.new("RGBA", foreground.size, (0, 0, 0, 0))
    composite.alpha_composite(foreground)
    assert composite.tobytes() == foreground.tobytes()


def test_transparent_3d_background_supports_native_alpha_over_and_masks():
    renderer = SolidSpatialRenderer((160, 96), {
        "background": "#00000000", "fog": 0, "glow": 0,
    })
    surface = renderer.render(frame())
    assert surface.getpixel((0, 0))[3] == 0
    assert surface.getchannel("A").getbbox() is not None
    matte = Image.new("RGBA", surface.size, "#304050")
    matte.alpha_composite(surface)
    assert matte.getpixel((0, 0)) == (48, 64, 80, 255)
