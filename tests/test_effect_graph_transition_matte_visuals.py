"""Determinism and metadata checks for the synthetic M5.1 visual evidence."""

import hashlib
import importlib.util
import json
from pathlib import Path

from PIL import Image


def test_contact_sheet_and_frame_evidence_are_reproducible(tmp_path):
    entry = Path(__file__).resolve().parents[1] / "integration" / "build_effect_graph_m5_visuals.py"
    spec = importlib.util.spec_from_file_location("m5_transition_visuals", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = tmp_path / "a" / "sheet.png"
    second = tmp_path / "b" / "sheet.png"
    _, rows_a = module.build(first)
    _, rows_b = module.build(second)
    assert len(rows_a) == len(rows_b) == 12
    assert first.read_bytes() == second.read_bytes()
    assert first.with_suffix(".json").read_bytes() == second.with_suffix(".json").read_bytes()
    metadata = json.loads(first.with_suffix(".json").read_text())
    assert metadata["schema"] == "framerudiments.m5.1.evidence.v1"
    assert len({item["sha256"] for item in metadata["samples"]}) > 5
    assert len({item["style"] for item in metadata["samples"]}) == 4
    assert all(len(item["sha256"]) == 64 for item in rows_a)
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    with Image.open(first) as sheet:
        assert sheet.size == (508, 531)
