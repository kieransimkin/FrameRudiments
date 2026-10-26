import json
import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from framerudiments.render import _exception_details, _write_candidate_health, render


def test_memory_error_without_message_keeps_useful_identity():
    details = _exception_details(MemoryError())

    assert details["type"] == "MemoryError"
    assert details["message"] == "MemoryError"
    assert details["memory_error"] is True
    assert details["chain"][0]["message"] == "MemoryError"


def test_exception_chain_records_root_context():
    try:
        try:
            raise OSError("disk unavailable")
        except OSError as cause:
            raise RuntimeError("encode failed") from cause
    except RuntimeError as exc:
        details = _exception_details(exc)

    assert [item["type"] for item in details["chain"]] == ["RuntimeError", "OSError"]
    assert details["chain"][1]["message"] == "disk unavailable"


def test_render_setup_failure_writes_closed_candidate_gate(tmp_path):
    missing_project = tmp_path / "missing-project.json"

    with pytest.raises(FileNotFoundError):
        render(missing_project, tmp_path)

    failure = json.loads((tmp_path / "failure.json").read_text(encoding="utf-8"))
    health = json.loads((tmp_path / "candidate-health.json").read_text(encoding="utf-8"))
    assert failure["phase"] == "preparing render"
    assert failure["error"]
    assert health["affected_by_error"] is True
    assert health["review_allowed"] is False


def test_error_health_flags_every_candidate_artifact(tmp_path):
    (tmp_path / "candidate.mp4").write_bytes(b"candidate")
    (tmp_path / "video.mp4").write_bytes(b"promoted-before-later-qa-failure")
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    (chunks / "chunk-0002.partial.mp4").write_bytes(b"partial")
    details = _exception_details(MemoryError())

    value = _write_candidate_health(
        tmp_path,
        state="affected-by-error",
        affected_by_error=True,
        phase="verifying decoded seams",
        error=details,
        frame=240,
        finalized_prefix={"completed_chunks": 2, "total_chunks": 3},
    )
    persisted = json.loads((tmp_path / "candidate-health.json").read_text(encoding="utf-8"))

    assert persisted == value
    assert persisted["technical_qa"] == "not-passed"
    assert persisted["review_allowed"] is False
    assert persisted["publication_allowed"] is False
    assert persisted["error"]["memory_error"] is True
    assert {Path(artifact["path"]).name for artifact in persisted["artifacts"]} == {
        "candidate.mp4",
        "video.mp4",
        "chunk-0002.partial.mp4",
    }


def test_bounded_alpha_composite_is_pixel_identical():
    rng = random.Random(17)
    background = Image.new("RGBA", (641, 359), (18, 4, 31, 255))
    overlay = Image.new("RGBA", background.size)
    draw = ImageDraw.Draw(overlay)
    for _ in range(12):
        x0, y0 = rng.randrange(641), rng.randrange(359)
        x1, y1 = min(641, x0 + rng.randrange(1, 160)), min(359, y0 + rng.randrange(1, 90))
        draw.rectangle(
            (x0, y0, x1 - 1, y1 - 1),
            fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256), rng.randrange(1, 256)),
        )

    full = background.copy()
    full.alpha_composite(overlay)
    bounded = background.copy()
    bounds = overlay.getchannel("A").getbbox()
    bounded.alpha_composite(overlay, dest=bounds[:2], source=bounds)

    assert bounded.tobytes() == full.tobytes()
