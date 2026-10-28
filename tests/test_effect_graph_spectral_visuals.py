"""Synthetic M4 QA manifest and visual evidence are deterministic."""

import importlib.util
from pathlib import Path

from framerudiments.effect_graph_spectral import spectral_displacement
from PIL import Image


def _builder():
    path = Path(__file__).resolve().parents[1] / "integration" / "build_effect_graph_m4_visuals.py"
    spec = importlib.util.spec_from_file_location("spectral_evidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_visual_evidence_reproducible(tmp_path):
    builder = _builder()
    first = builder.build(tmp_path / "first")
    second = builder.build(tmp_path / "second")
    assert first == second
    assert (tmp_path / "first" / "spectral-contact-sheet.png").read_bytes() == (
        tmp_path / "second" / "spectral-contact-sheet.png"
    ).read_bytes()
    assert len(first["cases"]) == 6
    assert len({case["sha256"] for case in first["cases"]}) > 3


def test_spectral_source_frame_unchanged_and_returns_rgba():
    builder = _builder()
    frame = builder.source_image()
    original = frame.tobytes()
    _, spec = builder.scenarios()[2]
    out = spectral_displacement(frame, spec)
    assert isinstance(out, Image.Image)
    assert out.mode == "RGBA" and out.size == frame.size
    assert frame.tobytes() == original
