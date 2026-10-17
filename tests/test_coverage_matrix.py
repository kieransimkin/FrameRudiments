"""Cross-feature acceptance tests for the public rendering contract.

All data is generated locally. No audio models, external assets or GPU engines
are needed; the renderer catalogue's own synthetic fixtures are used.
"""

from pathlib import Path
import os
import sys

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.output_quality import OutputQuality
from framerudiments.project import Project
from framerudiments.rudiment_renderers import default_renderers
from framerudiments.super_sampling import vector_viewport

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_visualisation_gallery import fixture  # noqa: E402


@pytest.mark.parametrize(
    "name,family",
    [
        ("particles", "motion"),
        ("waveform-oscilloscope", "motion"),
        ("spectrum", "spectrum"),
        ("spectrogram-linear", "spectrogram"),
        ("spectrum-annular-bars", "spectrum"),
        ("spectrogram-pixel-mosaic", "spectrogram"),
    ],
)
@pytest.mark.parametrize("scale", [1, 4])
def test_renderer_quality_matrix_is_deterministic(name, family, scale):
    """Spatial quality settings preserve correct size, mode and determinism."""
    registry = default_renderers()
    assert name in registry.names
    data = fixture(95, spectral=family != "motion")
    kwargs = (registry, name, (320, 210), {"background": "#142238"}, data, scale)
    a, _ = vector_viewport(*kwargs, cache={})
    b, _ = vector_viewport(*kwargs, cache={})
    assert isinstance(a, Image.Image)
    assert a.mode == "RGBA" and a.size == (320, 210)
    assert a.tobytes() == b.tobytes()
    assert a.getbbox() is not None


def test_temporal_custom_scene_hook_runs_once_per_encoded_frame_even_after_seek(tmp_path):
    """Lookback/repeated subframe visits must not replay stateful scene hooks."""
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font or not Path(font).is_file():
        pytest.skip("Requires the pinned test font")
    project = Project.load(create_demo(tmp_path / "scene", font, width=180, height=320))
    project.output_quality = OutputQuality({"spatial_scale": 1, "temporal_samples": 4})
    calls = []

    def scene(image, ctx):
        calls.append(ctx.frame)
        return image

    compositor = Compositor(project, scene=scene)
    first, _ = compositor.frame(1, apply_quality=False)
    assert calls == [1]
    second, _ = compositor.frame(0, apply_quality=False)
    again, _ = compositor.frame(1, apply_quality=False)
    assert calls == [1, 0]  # cache also survives a seek back to frame zero
    assert first.tobytes() == again.tobytes()
    assert second.size == first.size
