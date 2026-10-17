"""Actual PrismMoves/PixelCue measurements on sequential native-engine captures."""

import json
import os
from pathlib import Path

from PIL import Image

from framerudiments.demo import create_demo
from framerudiments.io import read_json, write_json, sha256
from framerudiments.visualisers import ASSETS
from framerudiments.preset_profiling import profile_presets
from framerudiments.profile_analysis import summarise


def test_real_prism_pixelcue_response_synthesis_and_controls(tmp_path):
    project = create_demo(tmp_path / "sample", os.environ["FRAMERUDIMENTS_TEST_FONT"], width=128, height=128)
    cover = project.parent / "cover.png"
    Image.new("RGB", (128, 128), "navy").save(cover)
    data = read_json(project)
    data["background"] = dict(image=dict(path=str(cover), sha256=sha256(cover)))
    data["layers"], data["identity"] = [], []
    data["audio"].update(end_sample=48000, fade_seconds=0)
    write_json(project, data)
    suite = tmp_path / "suite.json"
    write_json(
        suite,
        dict(
            schema="framerudiments.preset-suite.v1",
            projects=[dict(path=str(project), sha256=sha256(project))],
        ),
    )
    preset = ASSETS / "feedback.milk"
    catalogue = tmp_path / "catalogue.json"
    write_json(
        catalogue,
        dict(
            schema="framerudiments.preset-library.v1",
            presets=[
                dict(
                    id="native",
                    name="Native fixture",
                    engine="projectm",
                    preset=dict(path=str(preset), sha256=sha256(preset)),
                )
            ],
        ),
    )
    result = profile_presets(
        suite, catalogue, tmp_path / "run", intervals=[0, 0.1], images=["cover-lyrics", "grid-rings"]
    )
    response = summarise(result[0]["manifest"], max_edge=64, models_dir=tmp_path / "models")
    assert response["state"] == "completed"
    assert len(response["conditions"]) == 4
    assert len(response["controlled_comparisons"]) == 2
    assert all(len(x["source_to_output"]) == 2 for x in response["conditions"])
    assert all(len(x["temporal"]) == 1 for x in response["conditions"])
    assert response["settings"]["versions"]["prismmoves"] == "0.1.0"
    assert response["settings"]["versions"]["pixelcue"] == "0.9.0"
    assert all("median" in x for x in response["measured_response_ranges"].values())
    assert summarise(result[0]["manifest"], max_edge=64, models_dir=tmp_path / "models") == response
    report = Path(result[0]["manifest"]).parent / "identity/report.html"
    assert report.is_file() and "matching-time capture" in report.read_text(encoding="utf-8")
    assert json.loads((report.parent / "response.json").read_text())["settings"] == response["settings"]
