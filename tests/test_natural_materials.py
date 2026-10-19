from types import SimpleNamespace

from PIL import Image, ImageDraw
import numpy as np
import pytest

from framerudiments.natural_materials import NaturalMaterial, NaturalMaterialLayer
from framerudiments.io import file_ref


@pytest.mark.parametrize("kind", ["fire", "ice"])
def test_seek_seed_transparency_and_motion(kind):
    material = NaturalMaterial((160, 100), kind, resolution=0.7, seed=4)
    first = material.render(1.3)
    material.render(5.7)
    assert first.tobytes() == material.render(1.3).tobytes()
    assert first.mode == "RGBA" and first.size == (160, 100)
    assert first.getchannel("A").getextrema()[0] < 255
    assert first.getchannel("A").getextrema()[1] > 20
    assert first.tobytes() != NaturalMaterial((160, 100), kind, seed=5).render(1.3).tobytes()
    assert first.tobytes() != material.render(2.5).tobytes()
    assert material.render(1, amount=0).getbbox() is None


def test_fire_connected_density_hot_core_and_finite_rgba():
    a = np.asarray(NaturalMaterial((240, 180), "fire", resolution=1).render(1.7))
    assert np.isfinite(a).all()
    # Connected luminous body with transparent upper space; more than sparse sparks.
    assert (a[:, :, 3] > 80).sum() > a.shape[0] * a.shape[1] * 0.12
    assert (a[:20, :, 3] == 0).mean() > 0.6
    assert ((a[:, :, 0] > a[:, :, 1]) & (a[:, :, 3] > 60)).sum() > 1000
    assert ((a[:, :, 0] > 245) & (a[:, :, 1] > 200) & (a[:, :, 3] > 60)).sum() > 100


def test_burn_endpoints_and_freeze_alpha():
    source = Image.new("RGBA", (180, 90))
    ImageDraw.Draw(source).rounded_rectangle((12, 8, 166, 81), radius=12, fill="#7a3161")
    before = source.tobytes()
    fire = NaturalMaterial(source.size, "fire")
    ice = NaturalMaterial(source.size, "ice")
    assert fire.burn(source, 1, 0).tobytes() == before
    assert fire.burn(source, 1, 1).getbbox() is None
    assert fire.burn(source, 1, 0.5).tobytes() != before
    assert source.tobytes() == before
    assert ice.freeze(source, 1, 0).tobytes() == before
    frozen = ice.freeze(source, 1, 1)
    assert frozen.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    assert frozen.tobytes() != before
    with pytest.raises(ValueError):
        ice.burn(source, 1, 0.5)
    with pytest.raises(ValueError):
        fire.freeze(source, 1, 0.5)


@pytest.mark.parametrize(
    "settings",
    [
        {"resolution": 0},
        {"resolution": 2},
        {"seed": -1},
        {"glow": float("nan")},
        {"speed": -1},
        {"unknown": 1},
    ],
)
def test_invalid_settings_fail_before_render(settings):
    with pytest.raises(ValueError):
        NaturalMaterial((40, 30), "fire", **settings)


@pytest.mark.parametrize("time", [-1, float("nan"), float("inf")])
def test_invalid_source_time(time):
    with pytest.raises(ValueError):
        NaturalMaterial((40, 30)).render(time)


def test_limits_and_rgba_contract():
    with pytest.raises(ValueError):
        NaturalMaterial((4096, 4096))
    with pytest.raises(ValueError):
        NaturalMaterial((2048, 2048), resolution=1)
    with pytest.raises(ValueError):
        NaturalMaterial((20, 20), "steam")
    with pytest.raises(ValueError):
        NaturalMaterial((20, 20)).burn(Image.new("RGB", (20, 20)), 1, 0.4)
    with pytest.raises(ValueError):
        NaturalMaterial((20, 20)).render(1, amount=1.1)


def test_layer_bounds_source_time_gate_progress_and_evidence(tmp_path):
    refs = []
    p = SimpleNamespace(
        width=200, height=150, base=tmp_path, inputs=refs, ref=lambda r: refs.append(file_ref(r, tmp_path))
    )
    cfg = {
        "id": "flame",
        "type": "material",
        "kind": "fire",
        "method": "stylized",
        "bounds": [30, 40, 170, 130],
        "start_seconds": 2,
        "end_seconds": 4,
        "progress": {"keys": [{"time": 2, "value": 0}, {"time": 3, "value": 1}]},
    }
    layer = NaturalMaterialLayer(p, cfg, None)
    image = Image.new("RGBA", (200, 150))
    assert layer.draw(image, SimpleNamespace(source_seconds=1)) is None
    assert layer.draw(image, SimpleNamespace(source_seconds=2)) is None
    bb = layer.draw(image, SimpleNamespace(source_seconds=3))
    assert 30 <= bb[0] < bb[2] <= 170 and 40 <= bb[1] < bb[3] <= 130
    assert layer.frame_report["source_seconds"] == 3
    assert layer.evidence["kind"] == "fire" and len(refs) >= 1
    assert layer.draw(image, SimpleNamespace(source_seconds=4)) is None


def test_compositor_material_routing_and_masks(tmp_path):
    from framerudiments.demo import create_demo
    from framerudiments import Project
    from framerudiments.compositor import Compositor
    import os

    font = os.environ["FRAMERUDIMENTS_TEST_FONT"]
    path = create_demo(tmp_path / "demo", font=font)
    p = Project.load(path)
    p.data["render_quality"] = {"enabled": False}
    p.data["layers"] = [{"id": "ice", "type": "material", "kind": "ice", "bounds": [20, 25, 150, 130]}]
    p.data.pop("captions", None)
    p.data["identity"] = []
    c = Compositor(p)
    frame, _ = c.frame(5, apply_quality=False)
    assert frame.size == (p.width, p.height)
    assert c.frame_reports()["ice"]["kind"] == "ice"
    assert c.evidence()["natural_materials"]["ice"]["kind"] == "ice"
