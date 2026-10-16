import json
import math
import os

from dancerudiments_authoring.recording import Recording, RecordingClock, write_recording
import pytest

from framerudiments import Project, render
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.io import sha256
from framerudiments.spectrum import analyse_spectrum, peak_indices


@pytest.fixture
def spectrum_project(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("Set font")
    project = Project.load(create_demo(tmp_path / "demo", font, width=360, height=640))
    rows = [dict(name=f"band_{j:03d}", mode="fourier", output_scale="bipolar", clipped_windows=0,
                 silent_axis=j == 3, normalization_divisor=.5, gain=-.5 if j == 2 else 1,
                 frequency_low_hz=j * 100, frequency_high_hz=(j + 1) * 100,
                 source_sample_rate=8000, fourier=dict(fft_size=256), source_sha256=project.audio_ref["sha256"])
            for j in range(4)]
    values = tuple(tuple(0 if row["silent_axis"] else (2 * value / row["normalization_divisor"] - 1) * row["gain"]
                         for row, value in zip(rows, [.1, .45 if i < 20 else .05, .02, 0])) for i in range(512))
    path = project.base / "fourier.json"
    rec = Recording("synthetic_fourier_contract_fixture", tuple(row["name"] for row in rows), values,
                    RecordingClock(120, "synthetic fixture", "confirmed", 0, "supplied", 0),
                    json.dumps(dict(producer="StemLab", axes=rows)))
    write_recording(path, rec)
    project.data["layers"] = [dict(id="spectrum", type="rudiment", renderer="spectrum",
                                   bounds=[10, 280, 350, 590], history_frames=256,
                                   motion=dict(kind="recording", file=dict(path=str(path), sha256=sha256(path))))]
    return project, rows, rec


def test_calibrated_level_recovery_silence_negative_gain_and_frequency_peaks(spectrum_project):
    project, rows, rec = spectrum_project
    comp = Compositor(project)
    view = comp.layers[0]
    data = view.data(comp.context(0))
    report = analyse_spectrum(data)
    assert [b["rms"] for b in report["bands"]] == pytest.approx([.1, .45, .02, 0], abs=1e-12)
    assert report["bands"][0]["dbfs"] == pytest.approx(-20)
    assert report["bands"][3]["dbfs"] is None
    assert report["dominant"]["low_hz"] == 100
    assert report["peaks"][0]["high_hz"] == 200
    assert report["fft_bin_resolution_hz"] == 31.25
    assert report["bands"][1]["dbfs"] == pytest.approx(20 * math.log10(.45))


def test_peak_hold_decays_deterministically_without_hidden_state(spectrum_project):
    project, _, _ = spectrum_project
    comp = Compositor(project)
    view = comp.layers[0]
    midway = analyse_spectrum(view.data(comp.context(30)))
    assert midway["bands"][1]["held_dbfs"] > midway["bands"][1]["dbfs"]
    late = analyse_spectrum(view.data(comp.context(180)))
    assert late["bands"][1]["held_dbfs"] == pytest.approx(late["bands"][1]["dbfs"])
    assert analyse_spectrum(view.data(comp.context(30))) == midway
    first = comp.frame(30)[0].tobytes()
    comp.frame(180)
    assert comp.frame(30)[0].tobytes() == first


def test_local_peak_plateaus_threshold_contrast_and_minimum_distance():
    assert peak_indices([-60, -10, -10, -60, -20, -60], floor=-72, threshold=-50,
                        contrast=3, distance=2, count=3) == [(1, 1, 2, 50), (4, 4, 4, 40)]
    assert peak_indices([-72] * 4, floor=-72, threshold=-60, contrast=3, distance=1, count=3) == []
    peaks = peak_indices([-60, -10, -60, -20, -60], floor=-72, threshold=-50, contrast=3, distance=3, count=2)
    assert [row[0] for row in peaks] == [1]


@pytest.mark.parametrize("change", [{"clipped_windows": 1}, {"gain": 0}, {"normalization_divisor": 0},
                                    {"mode": "peak"}, {"source_sha256": "unknown"},
                                    {"frequency_high_hz": 6000}, {"fourier": {"fft_size": 255}}])
def test_unrecoverable_or_unverified_calibration_is_rejected(spectrum_project, change):
    project, rows, rec = spectrum_project
    rows[0].update(change)
    rec = Recording(rec.name, rec.dimensions, rec.samples, rec.clock, json.dumps(dict(producer="StemLab", axes=rows)))
    path = project.base / "bad.json"
    write_recording(path, rec)
    project.data["layers"][0]["motion"]["file"] = dict(path=str(path), sha256=sha256(path))
    with pytest.raises(ValueError):
        Compositor(project)


def test_generic_motion_and_reversed_bands_cannot_be_labelled_a_spectrum(spectrum_project):
    project, _, _ = spectrum_project
    project.data["layers"][0]["axes"] = [1, 0]
    with pytest.raises(ValueError, match="ascending"):
        Compositor(project)
    project.data["layers"][0].pop("axes")
    project.data["layers"][0]["motion"] = dict(kind="named", pattern="circle")
    with pytest.raises(ValueError, match="Fourier recording"):
        Compositor(project)


def test_encoded_spectrum_exports_actual_per_frame_peaks(spectrum_project, tmp_path):
    if not os.environ.get("FRAMERUDIMENTS_FFMPEG"):
        pytest.skip("Set FFmpeg")
    project, _, _ = spectrum_project
    project.data["audio"].update(end_sample=24000, fade_seconds=0)
    project = Project(project.data, project.base)
    output = tmp_path / "spectrum-output"
    result = render(project, output, preset="ultrafast")
    reports = [json.loads(line) for line in (output / "rudiment-analysis.jsonl").read_text().splitlines()]
    assert len(reports) == result["frames"] == 30
    assert reports[0]["layers"]["spectrum"]["peaks"][0]["low_hz"] == 100
    assert result["analysis_sidecar"]["sha256"] == sha256(output / "rudiment-analysis.jsonl")
    assert result["verification"]["full_decode"] == "passed"
    assert not result["verification"]["listening_approved"]
