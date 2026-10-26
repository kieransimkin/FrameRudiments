import os
from pathlib import Path
import json
import pytest
import dancerudiments as dr
from PIL import Image

from framerudiments import Project, render, preflight
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.io import sha256, write_json
from framerudiments.motion import Motion
from framerudiments.stemlab import selected_loop
from framerudiments.timeline import BeatClock


@pytest.fixture
def demo(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("Set FRAMERUDIMENTS_TEST_FONT to an installed font")
    return create_demo(tmp_path / "demo", font, width=180, height=320)


def test_native_matches_binding_and_negative_clock():
    motion = Motion(dict(kind="named", pattern="circle"), Path.cwd(), BeatClock(bpm=120))
    for t in (-0.01, 0, 0.117, 1.0):
        a = motion.sample(t)
        b = dr.sample("circle", motion.clock.pip_at(t))
        assert a == (b.x, b.y, b.z)


def test_recording_does_not_wrap_and_named_axis_projection(demo):
    project = Project.load(demo)
    cfg = dict(kind="recording", file=project.data["layers"][1]["motion"]["file"], axes=["high", "low"])
    motion = Motion(cfg, demo.parent, project.clock)
    values = motion.recording.sample(64)
    assert motion.sample(0.5) == (values[2], values[0], 0)
    with pytest.raises(ValueError, match="outside"):
        motion.sample(4)
    assert motion.sample(0) != motion.sample(0.5)


def test_hashes_and_unknown_fields_fail_closed(demo):
    data = json.loads(demo.read_text())
    data["fpps"] = 30
    with pytest.raises(ValueError, match="Unknown"):
        Project(data, demo.parent)
    (demo.parent / "lyrics.lrc").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        Project.load(demo)


def test_sprite_extent_and_caption_overflow_are_rejected(demo):
    p = Project.load(demo)
    p.data["layers"][0]["center"] = [0, 0]
    with pytest.raises(ValueError, match="transformed alpha"):
        Compositor(p).frame(0)
    p.data["layers"] = []
    p.data["captions"]["box"] = [1, 1, 10, 10]
    with pytest.raises(ValueError, match="cannot fit"):
        Compositor(p).frame(0)


def test_loop_report_checks_identity_and_accepted_pool(tmp_path):
    path = tmp_path / "loops.json"
    write_json(
        path,
        dict(
            schema="stemlab.loops.v1",
            source_sha256="abc",
            sample_rate=48000,
            loops=[dict(id="loop-001", start_sample=12, end_sample=48012)],
            exploratory_candidates=[dict(id="explore")],
        ),
    )
    assert selected_loop(path, "loop-001", "abc")["start_sample"] == 12
    with pytest.raises(ValueError):
        selected_loop(path, "loop-001", "wrong")
    with pytest.raises(ValueError):
        selected_loop(path, "explore", "abc")


def test_project_native_recording_bars_are_drawn(demo):
    p = Project.load(demo)
    compositor = Compositor(p)
    first, _ = compositor.frame(0)
    later, _ = compositor.frame(30)
    assert first.size == (180, 320)
    assert first.tobytes() != later.tobytes()
    assert set(compositor.evidence()["motions"]) == {"native", "bands"}


@pytest.mark.parametrize("dimensions", [(180, 320), (320, 180), (180, 180)])
def test_actual_encode_decode_cfr_and_fractional_sample_repeats(demo, tmp_path, dimensions):
    if not os.environ.get("FRAMERUDIMENTS_FFMPEG"):
        pytest.skip("FFmpeg integration: set FRAMERUDIMENTS_FFMPEG")
    # Rebuild responsive example at the target size; repeat an exact native-rate fractional interval.
    font = os.environ["FRAMERUDIMENTS_TEST_FONT"]
    path = create_demo(
        tmp_path / f"demo-{dimensions[0]}x{dimensions[1]}", font, width=dimensions[0], height=dimensions[1]
    )
    data = json.loads(path.read_text())
    data["audio"].update(start_sample=12001, end_sample=36002, repeats=2, fade_seconds=0)
    write_json(path, data)
    out = tmp_path / f"encode-{dimensions[0]}x{dimensions[1]}"
    result = render(path, out, preset="ultrafast")
    assert result["frames"] == 61
    assert result["verification"]["full_decode"] == "passed"
    assert result["output"]["sha256"] == sha256(out / "video.mp4")
    assert not result["verification"]["listening_approved"]
    assert Image.open(out / "poster.png").size == dimensions
    with pytest.raises(FileExistsError):
        render(path, out)


def test_inputs_rechecked_after_preparation(demo):
    p = Project.load(demo)
    (demo.parent / "sprite.png").write_bytes(b"changed")
    # Compositor's own fresh pin check rejects a stale project before rendering.
    with pytest.raises(ValueError, match="hash mismatch"):
        Compositor(p)


def test_custom_scene_hook_and_preflight(demo, tmp_path):
    if not os.environ.get("FRAMERUDIMENTS_FFMPEG"):
        pytest.skip("Set FRAMERUDIMENTS_FFMPEG")
    calls = []

    def scene(image, ctx):
        calls.append(ctx.frame)
        return image

    result = preflight(demo, tmp_path / "preflight", scene=scene)
    assert calls == list(range(240))
    assert result["composition"]["custom_scene"]
    assert "no encoded" in result["state"]
    assert result["preflight_scope"] == {
        "mode": "full",
        "frames_rendered": 240,
        "total_frames": 240,
        "complete": True,
        "sequential": True,
        "note": "All project frames were composed.",
    }

    calls.clear()
    bounded = preflight(demo, tmp_path / "bounded-preflight", scene=scene, max_frames=3)
    assert calls == [0, 1, 2]
    assert bounded["preflight_scope"] == {
        "mode": "bounded-prefix",
        "frames_rendered": 3,
        "total_frames": 240,
        "complete": False,
        "sequential": True,
        "note": "Only the declared sequential prefix was composed; later frames remain unvalidated.",
    }
    assert "later frames unvalidated" in bounded["state"]
    assert (tmp_path / "bounded-preflight" / "qa" / "source-00000002.png").exists()


@pytest.mark.parametrize("limit", [0, -1, 241, 1.5, True])
def test_bounded_preflight_rejects_invalid_frame_limit(demo, tmp_path, limit):
    with pytest.raises(ValueError, match="Preflight max_frames"):
        preflight(demo, tmp_path / f"bad-preflight-{limit}", max_frames=limit)


def test_cli_forwards_bounded_preflight_limit(monkeypatch, capsys):
    from framerudiments import cli

    received = {}

    def fake_preflight(project, output, **kwargs):
        received.update(project=project, output=output, **kwargs)
        return {"state": "bounded-prefix preflight passed", "frames": 240}

    monkeypatch.setattr(cli, "preflight", fake_preflight)
    assert cli.main(["preflight", "project.json", "--output", "out", "--max-frames", "3"]) == 0
    assert received == {
        "project": "project.json",
        "output": "out",
        "ffmpeg": None,
        "ffprobe": None,
        "max_frames": 3,
    }
    assert '"state": "bounded-prefix preflight passed"' in capsys.readouterr().out


def test_cli_forwards_chunk_controls(monkeypatch):
    from framerudiments import cli

    received = {}

    def fake_render(project, output, **kwargs):
        received.update(project=project, output=output, **kwargs)
        return {"state": "ok", "frames": 1}

    monkeypatch.setattr(cli, "render", fake_render)
    assert (
        cli.main(
            [
                "render",
                "project.json",
                "--output",
                "out",
                "--chunked",
                "--chunk-seconds",
                "42",
                "--resume",
                "--min-free-gib",
                "0.75",
            ]
        )
        == 0
    )
    assert received["chunked"] is True
    assert received["chunk_seconds"] == 42
    assert received["resume"] is True
    assert received["min_free_bytes"] == round(0.75 * 1024**3)


def test_chunked_render_resumes_finalized_prefix_with_exact_seams(demo, tmp_path):
    if not os.environ.get("FRAMERUDIMENTS_FFMPEG"):
        pytest.skip("Set FRAMERUDIMENTS_FFMPEG")
    state = {"fail": True}

    def scene(image, ctx):
        if state["fail"] and ctx.frame == 130:
            raise RuntimeError("synthetic interruption")
        return image

    output = tmp_path / "chunked"
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        render(demo, output, scene=scene, preset="ultrafast", chunked=True, chunk_seconds=9)
    first = output / "chunks" / "chunk-0000.mp4"
    receipt = output / "chunks" / "chunk-0000.json"
    assert first.is_file() and receipt.is_file()
    retained = sha256(first)
    failure = json.loads((output / "failure.json").read_text(encoding="utf-8"))
    health = json.loads((output / "candidate-health.json").read_text(encoding="utf-8"))
    assert failure["error"] == "synthetic interruption"
    assert failure["exception"]["type"] == "RuntimeError"
    assert failure["phase"] == "replaying finalized prefix and rendering chunks"
    assert failure["frame"] == 130
    assert health["state"] == "affected-by-error"
    assert health["affected_by_error"] is True
    assert health["review_allowed"] is False
    assert health["finalized_prefix"]["completed_chunks"] == 1

    state["fail"] = False
    result = render(
        demo,
        output,
        scene=scene,
        preset="ultrafast",
        chunked=True,
        chunk_seconds=9,
        resume=True,
    )
    assert sha256(first) == retained
    assert result["render_mode"] == "chunked-sequential"
    assert result["replayed_frames"] == result["chunk_plan"]["chunks"][0]["end_frame_exclusive"]
    assert result["seam_verification"]["all_matched"]
    assert result["verification"]["full_decode"] == "passed"
    assert result["output"]["sha256"] == sha256(output / "video.mp4")
    health = json.loads((output / "candidate-health.json").read_text(encoding="utf-8"))
    assert health["state"] == "technical-QA passed"
    assert health["affected_by_error"] is False
    assert health["review_allowed"] is True
    assert health["publication_allowed"] is False
    assert list(output.glob("candidate-health-before-resume-*.json"))
