"""Sprite atlas pinning, spectrum calibration and deterministic preview coverage."""

from hashlib import sha256
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw
import pytest

from framerudiments.rudiment_renderers import default_renderers
from framerudiments.sprite_catalogue import SPRITE_SPECS
from framerudiments.sprite_renderers import SpriteRenderer, SPRITE_DIRECTORY, builtin_atlas
from framerudiments.visualisation_catalogue import ALL_VIEWS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_visualisation_gallery import fixture  # noqa: E402


def test_registry_has_independent_sprite_and_vector_names():
    registry = default_renderers()
    names = registry.names
    assert len(names) == len(set(names)) == 213
    assert len(SPRITE_SPECS) == 52
    assert set(name for name, *_ in SPRITE_SPECS).issubset(names)
    assert {"spectrum", "waveform", "spectrogram-linear", "spectrum-annular-bars"}.issubset(names)
    assert set(spec.name for spec in ALL_VIEWS).issubset(names)


def test_generated_and_procedural_atlases_are_real_pinned_rgba_assets():
    catalogue = json.loads((SPRITE_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    assert len(catalogue["themes"]) == 17
    assert len([x for x in catalogue["themes"].values() if x["generated"]]) == 1
    for theme, config in catalogue["themes"].items():
        path = SPRITE_DIRECTORY / config["file"]
        assert sha256(path.read_bytes()).hexdigest() == config["sha256"]
        atlas = builtin_atlas(theme)
        assert atlas.mode == "RGBA" and atlas.size == (768, 768)
        assert atlas.getchannel("A").getextrema()[0] == 0
        assert atlas.getchannel("A").getextrema()[1] >= 190


@pytest.mark.parametrize("spec", SPRITE_SPECS, ids=lambda row: row[0])
def test_each_sprite_mode_is_image_backed_deterministic_and_source_bound(spec):
    name, family, *_ = spec
    registry = default_renderers()
    renderer, identity = registry.create(name, (420, 250), {"background": "#102037"})
    assert identity["source"]["path"].endswith("sprite_renderers.py")
    spectral = family != "motion"
    late = fixture(95, spectral=spectral)
    first = renderer.render(late)
    assert first.mode == "RGBA" and first.size == (420, 250)
    assert first.tobytes() == renderer.render(late).tobytes()
    assert first.tobytes() != renderer.render(fixture(35, spectral=spectral)).tobytes()
    assert first.getbbox() is not None
    if spectral:
        assert renderer.requires_spectrum
        assert renderer.frame_report["kind"] == "stemlab-fourier-band-spectrum"
        with pytest.raises(ValueError, match="calibrated"):
            renderer.render(fixture(95, spectral=False))
    else:
        assert not renderer.requires_spectrum


def test_lcd_segments_are_parameterized_and_originals_remain_unchanged():
    registry = default_renderers()
    frame = fixture(95, spectral=True)
    few, _ = registry.create("sprite-spectrum-lcd", (480, 270), {"segment_count": 6})
    many, _ = registry.create("sprite-spectrum-lcd", (480, 270), {"segment_count": 24})
    off, _ = registry.create("sprite-spectrum-lcd", (480, 270), {"off_opacity": 0.8})
    assert few.render(frame).tobytes() != many.render(frame).tobytes()
    assert off.render(frame).tobytes() != few.render(frame).tobytes()
    original, _ = registry.create("spectrum", (480, 270), {"labels": False})
    before = original.render(frame).tobytes()
    few.render(frame)
    assert original.render(frame).tobytes() == before


def test_custom_atlas_must_be_pinned_prepared_and_divisible(tmp_path):
    sheet = Image.new("RGBA", (96, 96))
    ImageDraw.Draw(sheet).rectangle((5, 5, 43, 43), fill="#33ddff")
    source = tmp_path / "sprite.png"
    sheet.save(source)
    asset = {"path": str(source), "sha256": sha256(source.read_bytes()).hexdigest()}
    cfg = {"sprite_asset": asset, "sprite_columns": 2, "sprite_rows": 2}
    renderer = SpriteRenderer((360, 200), cfg, "sprite-spectrum-led")
    with pytest.raises(ValueError, match="prepare"):
        renderer.render(fixture(65, spectral=True))

    class Project:
        def __init__(self):
            self.inputs = []

        def ref(self, row):
            path = Path(row["path"])
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("Custom sprite digest mismatch")
            return {"path": str(path), "sha256": row["sha256"]}

    project = Project()
    renderer.prepare(project)
    assert project.inputs == [asset]
    assert renderer.render(fixture(65, spectral=True)).size == (360, 200)
    with pytest.raises(ValueError, match="digest"):
        SpriteRenderer(
            (360, 200), {**cfg, "sprite_asset": {**asset, "sha256": "0" * 64}}, "sprite-spectrum-led"
        ).prepare(Project())
    odd = tmp_path / "odd.png"
    Image.new("RGBA", (97, 96)).save(odd)
    invalid_asset = {"path": str(odd), "sha256": sha256(odd.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="divide evenly"):
        SpriteRenderer((360, 200), {**cfg, "sprite_asset": invalid_asset}, "sprite-spectrum-led").prepare(
            Project()
        )


@pytest.mark.parametrize(
    "style",
    [
        {"sprite_theme": "unknown"},
        {"shape": "fake"},
        {"segment_count": 0},
        {"sprite_asset": "unhashed.png"},
        {"outer_radius": 0.2, "inner_radius": 0.7},
        {"sprite_index": 1000},
        {"sprite_size": 10000},
        {"unrecognised": 1},
        {"sprite_opacity": -1},
        {"animation_stride": 0},
        {"max_stamps": 0},
    ],
)
def test_sprite_style_fails_closed(style):
    with pytest.raises((ValueError, TypeError)):
        SpriteRenderer((330, 210), style, "sprite-spectrum-lcd")


def test_native_motion_modulation_can_drive_numeric_sprite_settings(tmp_path):
    import os
    from framerudiments import Project
    from framerudiments.compositor import Compositor
    from framerudiments.demo import create_demo

    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("A pinned font is required for full composer integration")
    project = Project.load(create_demo(tmp_path / "demo", font, width=360, height=640))
    project.data["layers"] = [
        {
            "id": "sprite",
            "type": "rudiment",
            "renderer": "sprite-waveform-dots",
            "bounds": [24, 240, 336, 560],
            "motion": {"kind": "named", "pattern": "circle"},
            "style": {"sprite_size": 10, "sprite_theme": "generated-neon"},
            "visual": {"glow": 2},
            "modulations": [
                {
                    "id": "glyph-size",
                    "target": "style.sprite_size",
                    "motion": {"kind": "named", "pattern": "bounce"},
                    "axis": "x",
                    "range": [6, 30],
                },
                {
                    "id": "glow",
                    "target": "visual.glow",
                    "motion": {"kind": "named", "pattern": "circle"},
                    "axis": "y",
                    "range": [0, 8],
                },
            ],
        }
    ]
    comp = Compositor(project)
    first, _ = comp.frame(75)
    comp.frame(18)
    again, _ = comp.frame(75)
    assert first.tobytes() == again.tobytes()
    report = comp.layers[0].frame_report
    assert set(report["modulations"]) == {"glyph-size", "glow"}
    assert any(Path(row["path"]).name == "generated-neon.png" for row in project.inputs)


def test_published_sprite_gallery_has_every_builtin_theme(tmp_path):
    from generate_sprite_gallery import build_site

    site = tmp_path / "_site"
    site.mkdir()
    (site / "index.html").write_text("<header>Atlas</header>", encoding="utf-8")
    result = build_site(site)
    assert result["themes"] == 17
    assert result["sprite_renderer_count"] == 52
    assert len(list((site / "sprites" / "themes").glob("*/*.png"))) == 17 * 4 * 2
    assert len(list((site / "sprites" / "atlases").glob("*.png"))) == 17
    assert len(result["modulation_scenes"]) == 16
    assert result["modulation_images"] == 64
    assert len(list((site / "sprites" / "modulation").glob("*/*.png"))) == 64
    assert len(result["modulation_scenes"][0]["samples"]) == 4
    assert 'href="sprites/index.html"' in (site / "index.html").read_text(encoding="utf-8")


def test_cli_lists_all_packaged_sprite_themes(capsys):
    from framerudiments.cli import main

    assert main(["sprites"]) == 0
    atlas_rows = json.loads(capsys.readouterr().out)
    assert len(atlas_rows) == 17
    assert atlas_rows["generated-neon"]["generated"]
    assert "sha256" in atlas_rows["lcd"]


@pytest.mark.parametrize(
    "field,value,extra",
    [
        ("sprite_scale", 1.7, {}),
        ("sprite_scale_x", 2.1, {}),
        ("sprite_scale_y", 1.8, {}),
        ("sprite_rotation", 98, {}),
        ("sprite_spin", 2, {}),
        ("sprite_offset_x", 34, {}),
        ("sprite_offset_y", -27, {}),
        ("sprite_layout_rotation", 35, {}),
        ("sprite_spread", 0.5, {}),
        ("sprite_jitter", 12, {}),
        ("sprite_shear_x", 0.6, {}),
        ("sprite_shear_y", -0.5, {}),
        ("sprite_visible_fraction", 0.4, {}),
        ("sprite_palette_shift", 3, {}),
        ("sprite_animation_phase", 21, {"animation_frames": 5}),
        ("sprite_animation_rate", 0, {"animation_frames": 5}),
        ("sprite_hue_shift", 90, {}),
        ("sprite_saturation", 0.2, {}),
        ("sprite_brightness", 0.4, {}),
        ("sprite_contrast", 2.0, {}),
        ("sprite_gamma", 0.6, {}),
        ("sprite_temperature", 0.8, {}),
        ("sprite_red", 0.3, {}),
        ("sprite_green", 0.3, {}),
        ("sprite_blue", 0.3, {}),
        ("sprite_posterize", 3, {}),
        ("sprite_pixel_size", 5, {}),
        ("sprite_glow", 4, {}),
        ("sprite_blur", 2, {}),
        ("sprite_outline", 2, {}),
        ("sprite_shadow", 3, {}),
        ("sprite_scanlines", 0.7, {}),
        ("sprite_grain", 0.8, {}),
        ("sprite_sharpen", 3, {}),
        ("sprite_blend", "screen", {}),
    ],
)
def test_independent_sprite_appearance_controls_are_pixel_active(field, value, extra):
    style = {"background": "#182e43", "sprite_theme": "generated-neon", "sprite_size": 12}
    frame = fixture(95, spectral=True)
    original = SpriteRenderer((360, 220), style, "sprite-spectrum-lcd").render(frame).tobytes()
    control = SpriteRenderer((360, 220), {**style, field: value, **extra}, "sprite-spectrum-lcd")
    result = control.render(frame)
    assert result.tobytes() != original, f"Sprite property {field} did not affect output"
    assert result.tobytes() == control.render(frame).tobytes()


def test_sprite_modulation_ranges_and_untrusted_choice_targets_are_rejected():
    from framerudiments.sprite_renderers import SPRITE_NUMERIC, SpriteModulations
    from types import SimpleNamespace

    for field, (lower, upper, _) in SPRITE_NUMERIC.items():
        with pytest.raises(ValueError):
            SpriteRenderer((280, 180), {field: upper + 1}, "sprite-spectrum-lcd")
    project = SimpleNamespace(
        base=Path("."), clock=SimpleNamespace(beat_at=lambda value: value * 2), inputs=[]
    )

    def context(n):
        return SimpleNamespace(frame=n, output_seconds=n / 60, source_seconds=n / 60, beat=n / 30)

    motion = {"kind": "named", "pattern": "circle"}
    with pytest.raises(ValueError, match="categorical"):
        SpriteModulations(
            project,
            [{"target": "style.sprite_asset", "choices": ["a", "b"], "motion": motion}],
            context,
            base_style={},
            base_visual={},
        )
    with pytest.raises(ValueError, match="theme choices"):
        SpriteModulations(
            project,
            [{"target": "style.sprite_theme", "choices": ["lcd", "not-an-atlas"], "motion": motion}],
            context,
            base_style={},
            base_visual={},
        )


def test_native_sources_modulate_geometry_colour_texture_and_theme(tmp_path):
    import os
    from framerudiments import Project
    from framerudiments.compositor import Compositor
    from framerudiments.demo import create_demo

    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("Requires the CI-pinned render font")
    project = Project.load(create_demo(tmp_path / "sprite-mod-demo", font, width=360, height=640))

    def pattern(name):
        return {"kind": "named", "pattern": name}

    project.data["layers"] = [
        {
            "id": "animated-tiles",
            "type": "rudiment",
            "renderer": "sprite-waveform-dots",
            "bounds": [20, 270, 340, 590],
            "motion": pattern("circle"),
            "history_frames": 90,
            "style": {"sprite_size": 12, "sprite_theme": "lcd", "sprite_colour": "original"},
            "modulations": [
                {
                    "id": "rotate",
                    "target": "style.sprite_rotation",
                    "motion": pattern("circle"),
                    "axis": "x",
                    "range": [-180, 180],
                },
                {
                    "id": "scale",
                    "target": "style.sprite_scale",
                    "motion": pattern("bounce"),
                    "axis": "y",
                    "range": [0.65, 1.5],
                },
                {
                    "id": "temperature",
                    "target": "style.sprite_temperature",
                    "motion": pattern("circle"),
                    "axis": "z",
                    "range": [-0.7, 0.7],
                },
                {
                    "id": "red",
                    "target": "style.sprite_red",
                    "motion": pattern("bounce"),
                    "axis": "x",
                    "range": [0.3, 1.8],
                },
                {
                    "id": "glow",
                    "target": "style.sprite_glow",
                    "motion": pattern("bounce"),
                    "axis": "x",
                    "range": [0, 4],
                },
                {
                    "id": "density",
                    "target": "style.sprite_visible_fraction",
                    "motion": pattern("circle"),
                    "axis": "y",
                    "range": [0.3, 1],
                },
                {
                    "id": "animated-theme",
                    "target": "style.sprite_theme",
                    "motion": pattern("circle"),
                    "axis": "x",
                    "choices": ["lcd", "crystal", "generated-neon"],
                },
                {
                    "id": "sprite-shape",
                    "target": "style.shape",
                    "motion": pattern("bounce"),
                    "axis": "z",
                    "choices": ["segment", "dot", "crystal", "flame"],
                },
                {
                    "id": "blend",
                    "target": "style.sprite_blend",
                    "motion": pattern("circle"),
                    "axis": "y",
                    "choices": ["normal", "screen", "add"],
                },
                {
                    "id": "palette",
                    "target": "visual.palette",
                    "motion": pattern("circle"),
                    "axis": "z",
                    "choices": ["aurora", "fire", "ice"],
                },
            ],
        }
    ]
    compositor = Compositor(project)
    first, _ = compositor.frame(75)
    other, _ = compositor.frame(24)
    again, _ = compositor.frame(75)
    assert first.tobytes() == again.tobytes()
    assert first.tobytes() != other.tobytes()
    report = compositor.layers[0].frame_report["modulations"]
    assert len(report) == 10
    assert report["animated-theme"]["value"] in {"lcd", "crystal", "generated-neon"}
    assert report["sprite-shape"]["value"] in {"segment", "dot", "crystal", "flame"}
    assert report["palette"]["value"] in {"aurora", "fire", "ice"}
    assert {"lcd.png", "crystal.png", "generated-neon.png"}.issubset(
        {Path(ref["path"]).name for ref in project.inputs}
    )
    before = len(project.inputs)
    compositor.frame(38)
    assert len(project.inputs) == before, (
        "Dynamic theme renderer must not add untracked assets after preparation"
    )
