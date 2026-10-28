"""Golden-output checks for the reproducible M3 alpha QA contact sheet."""

from hashlib import sha256
import importlib.util
import json
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "integration" / "build_effect_graph_m3_visuals.py"


def _builder():
    spec = importlib.util.spec_from_file_location("effect_graph_m3_visuals", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qa_samples_have_stable_alpha_and_source_images_are_unchanged():
    builder = _builder()
    samples = builder.render_samples()
    assert list(samples) == [
        "current-alpha", "original-alpha", "key-current", "key-original",
        "matte-before-transition", "matte-after-transition",
    ]
    assert samples["original-alpha"].getpixel((60, 44))[3] == 235
    assert samples["current-alpha"].getpixel((60, 44))[3] == 78
    assert samples["key-current"].getpixel((60, 44))[3] == 0
    assert samples["key-original"].getpixel((60, 44))[3] == 0
    assert samples["original-alpha"].getchannel("A").getbbox() == samples["current-alpha"].getchannel("A").getbbox()
    assert samples["original-alpha"].tobytes() != samples["current-alpha"].tobytes()
    assert samples["matte-before-transition"].tobytes() != samples["matte-after-transition"].tobytes()
    assert sha256(samples["current-alpha"].tobytes()).hexdigest() == (
        "25a8cac93bd115bc93432f28246a0555f0792f0d1d462d22f38601d06c3c460e"
    )
    assert sha256(samples["original-alpha"].tobytes()).hexdigest() == (
        "5f93dbc5e491ccf619bd0df70c19a2821d853440a746e29aa41f651125b846ee"
    )


def test_qa_contact_sheet_manifest_matches_generated_files(tmp_path):
    builder = _builder()
    image = builder.build(tmp_path)
    assert image.is_file()
    manifest = json.loads((tmp_path / "effect-graph-m3-alpha-evidence.json").read_text("utf-8"))
    assert manifest["schema"] == "framerudiments.m3-visual-evidence.v1"
    assert manifest["contact_sheet_sha256"] == sha256(image.read_bytes()).hexdigest()
    assert manifest["cases"]["original-alpha"]["alpha_sum"] > manifest["cases"]["current-alpha"]["alpha_sum"]
    assert manifest["cases"]["key-original"]["alpha_sum"] > manifest["cases"]["key-current"]["alpha_sum"]
    with Image.open(image) as sheet:
        assert sheet.mode == "RGB"
        assert sheet.size == (408, 256)
