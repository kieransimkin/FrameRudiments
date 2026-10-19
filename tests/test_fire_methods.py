import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from framerudiments import FireRenderer
from framerudiments.fire_assets import FireAtlas, key_black, pack_frames
from framerudiments.fire_fluid import FluidFire
from framerudiments.fire_renderer import builtin_asset
from framerudiments.io import sha256


@pytest.fixture
def atlas(tmp_path):
    frames = []
    for n, colour in enumerate(((255, 30, 0, 255), (0, 10, 255, 128))):
        p = tmp_path / f"{n}.png"
        Image.new("RGBA", (16, 16), colour).save(p)
        frames.append(p)
    manifest = pack_frames(
        frames, tmp_path / "atlas", fps="2", cell_size=(16, 16), provenance={"method": "test synthetic"}
    )
    return dict(path=str(manifest), sha256=sha256(manifest))


def test_pinned_atlas_clock_clamp_repeat_and_close(atlas):
    a = FireAtlas(atlas)
    assert a.frame_at(0.499).getpixel((0, 0)) == (255, 30, 0, 255)
    assert a.frame_at(0.5).getpixel((0, 0)) == (0, 10, 255, 128)
    assert a.frame_at(1).tobytes() == a.frame_at(0.5).tobytes()
    assert a.frame_at(1, repeat=True).tobytes() == a.frame_at(0).tobytes()
    a.close()
    a.close()
    with pytest.raises(RuntimeError, match="closed"):
        a.frame_at(0)


def test_atlas_hash_is_enforced_and_bake_cannot_be_faked(atlas):
    broken = dict(atlas, sha256="0" * 64)
    with pytest.raises(ValueError):
        FireAtlas(broken)
    with pytest.raises(ValueError, match="bake provenance"):
        FireRenderer((32, 32), "mantaflow", asset=atlas)
    path = Path(atlas["path"])
    data = json.loads(path.read_text())
    data["provenance"] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="provenance"):
        FireAtlas(dict(path=str(path), sha256=sha256(path)))


def test_emission_key_unpremultiplies_without_black_fringe():
    rgb = Image.new("RGB", (2, 2), (128, 64, 0))
    r, g, b, a = key_black(rgb, black_level=0).getpixel((0, 0))
    assert r == 255 and 126 <= g <= 129 and b == 0 and a == 128
    assert key_black(Image.new("RGB", (2, 2))).getbbox() is None


@pytest.mark.parametrize("method", ("footage", "sprites", "fluid_2d", "mantaflow", "stylized"))
def test_real_methods_seek_motion_alpha_and_cleanup(method):
    f = FireRenderer(
        (96, 72),
        method,
        resolution=0.7,
        glow=0,
        backend_settings={"resolution": 48} if method == "fluid_2d" else None,
    )
    try:
        a = f.render(0.31)
        f.render(0.72)
        assert f.render(0.31).tobytes() == a.tobytes()
        assert a.mode == "RGBA" and a.size == (96, 72)
        assert a.getchannel("A").getextrema()[1] > 0
        assert f.render(0.72).tobytes() != a.tobytes()
        assert f.render(0.72, amount=0).getbbox() is None
        paper = Image.new("RGBA", f.size, (100, 40, 30, 255))
        assert f.burn(paper, 0.4, 0).tobytes() == paper.tobytes()
        assert f.burn(paper, 0.4, 1).getbbox() is None
    finally:
        f.close()
    assert f.closed and f.evidence["cleanup_verified"]
    f.close()
    with pytest.raises(RuntimeError, match="closed"):
        f.render(0)


def test_grade_keeps_alpha_at_unshifted_front(atlas):
    a = FireRenderer((64, 64), "footage", asset=atlas, warmth=0, glow=0)
    b = FireRenderer((64, 64), "footage", asset=atlas, warmth=1, glow=0)
    try:
        x, y = a.render(0.1, front=1), b.render(0.1, front=1)
        assert x.getchannel("A").tobytes() == y.getchannel("A").tobytes()
        assert x.tobytes() != y.tobytes()
    finally:
        a.close()
        b.close()


def test_native_sprite_particle_age_selects_frame_and_long_source_is_bounded(atlas):
    f = FireRenderer((64, 64), "sprites", asset=atlas)
    try:
        emitter = f.emitter
        style = dict(emitter.base, softness=0, shape="square", stretch=1)
        a, b = Image.new("RGBA", f.internal), Image.new("RGBA", f.internal)
        info = (0.5, 0.5, 12, 0, 1, (255, 255, 255))
        emitter._stamp(a, (*info, 0.1), style)
        emitter._stamp(b, (*info, 0.6), style)
        center = tuple(v // 2 for v in f.internal)
        assert a.getpixel(center)[:3] == (255, 30, 0)
        blue = b.getpixel(center)
        assert blue[0] == 0 and 8 <= blue[1] <= 10 and blue[2] == 255
        assert f.render(161).getbbox() is not None
        assert emitter.frame_report["active"] < 100
    finally:
        f.close()


def test_fluid_projection_reduces_divergence_and_is_bounded():
    f = FluidFire((64, 64), resolution=48, warmup=0)
    rng = np.random.default_rng(6)
    u, v = rng.normal(size=(2, 48, 48)).astype(np.float32)

    def divergence(u, v):
        return (0.5 * (np.roll(u, -1, 1) - np.roll(u, 1, 1) + np.roll(v, -1, 0) - np.roll(v, 1, 0)))[
            3:-3, 3:-3
        ]

    a, b = f.project(u, v)
    assert np.linalg.norm(divergence(a, b)) < np.linalg.norm(divergence(u, v)) * 0.8
    with pytest.raises(ValueError):
        f.frame(30.01)
    with pytest.raises(ValueError):
        FluidFire((64, 64), resolution=400)


def test_bundled_assets_have_reviewed_provenance_and_no_private_paths():
    for name in ("marburg", "mantaflow"):
        atlas = FireAtlas(builtin_asset(name))
        assert atlas.frames >= 120
        assert atlas.evidence["provenance"]["licence"]
        raw = Path(atlas.reference["path"]).read_text()
        assert "Z:" not in raw and "C:" not in raw
        atlas.close()


@pytest.mark.parametrize(
    "settings",
    (
        {"warmth": 2},
        {"repeat": 1},
        {"backend_settings": []},
        {"method": "missing"},
        {"asset": {}, "method": "fluid_2d"},
    ),
)
def test_invalid_fire_options_do_not_fallback(settings):
    with pytest.raises(ValueError):
        FireRenderer((32, 32), **settings)
