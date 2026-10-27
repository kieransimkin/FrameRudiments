import json
import importlib

import pytest

from framerudiments.io import sha256, write_json
from framerudiments.project import Project
from framerudiments.work_cache import WorkingCache, cache_status, stage_project_inputs


def test_checksum_pinned_inputs_are_copied_and_live_paths_switch_to_cache(tmp_path):
    source = tmp_path / "source.lrc"
    source.write_text("[00:00.00]hello\n", encoding="utf-8")
    ref = dict(path=str(source), sha256=sha256(source), bytes=source.stat().st_size)
    project = Project.__new__(Project)
    project.inputs = [ref]
    project.canonical_inputs = None
    evidence = stage_project_inputs(project, tmp_path / "cache-inputs")
    assert project.canonical_inputs == [dict(path=str(source), sha256=sha256(source), bytes=source.stat().st_size)]
    assert project.inputs[0]["path"] != str(source)
    assert sha256(project.inputs[0]["path"]) == ref["sha256"]
    assert evidence["files"] == 1
    project.verify_inputs()


def test_prepare_stages_inputs_before_audio_probe_and_compositor(monkeypatch, tmp_path):
    module = importlib.import_module("framerudiments.render")
    events = []
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    project = Project.__new__(Project)
    project.inputs = [dict(path=str(source), sha256=sha256(source), bytes=source.stat().st_size)]
    project.canonical_inputs = None
    project.lyrics = []
    project.words = []
    project.width = 16
    project.height = 16
    project.fps = 1
    project.frame_count = 1

    def verify_inputs():
        events.append("verify-cached" if project.canonical_inputs is not None else "verify-canonical")

    project.verify_inputs = verify_inputs
    project.summary = lambda: {"inputs": [dict(item) for item in project.inputs]}

    class FakeCompositor:
        def __init__(self, prepared, **_kwargs):
            assert prepared.canonical_inputs is not None
            assert prepared.inputs[0]["path"].startswith(str(tmp_path / "cached-inputs"))
            events.append("compositor")

    def validate_audio(prepared, _ffprobe):
        assert prepared.canonical_inputs is not None
        assert prepared.inputs[0]["path"].startswith(str(tmp_path / "cached-inputs"))
        events.append("audio-probe")
        return {"streams": []}

    monkeypatch.setattr(module.encoding, "binaries", lambda *_args: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(module.encoding, "validate_audio", validate_audio)
    monkeypatch.setattr(module, "Compositor", FakeCompositor)
    monkeypatch.setattr(module, "export_subtitles", lambda *_args, **_kwargs: None)

    module._prepare(
        project,
        tmp_path / "output",
        None,
        None,
        None,
        None,
        input_cache=tmp_path / "cached-inputs",
    )

    assert events == ["verify-canonical", "verify-cached", "audio-probe", "compositor"]


def test_working_cache_reserves_promotes_and_returns_to_empty(tmp_path):
    cache_root = tmp_path / "fast-cache"
    final = tmp_path / "delivery" / "render-001"
    cache = WorkingCache(
        cache_root,
        final,
        reserve_bytes=128 * 1024,
        min_free_bytes=0,
        status_interval=0.1,
    )
    output = cache.prepare()
    for item in cache.reservations.values():
        assert item.is_file()
    cache.begin_render()
    assert not cache.reservations["cache_payload"].exists()
    assert cache.reservations["cache_margin"].is_file()
    assert cache.reservations["final_payload"].is_file()
    output.mkdir()
    (output / "qa").mkdir()
    video = output / "video.mp4"
    video.write_bytes(b"synthetic-video")
    analysis = output / "rudiment-analysis.jsonl"
    analysis.write_text("{}\n", encoding="utf-8")
    result = {
        "state": "technical-QA passed; creative and attended listening review pending",
        "frames": 1,
        "output": {"path": str(video), "sha256": sha256(video), "bytes": video.stat().st_size},
        "analysis_sidecar": {
            "path": str(analysis),
            "sha256": sha256(analysis),
            "bytes": analysis.stat().st_size,
        },
    }
    write_json(output / "manifest.json", result)
    write_json(
        output / "candidate-health.json",
        {"artifacts": [{"path": str(video), "bytes": video.stat().st_size}]},
    )
    promoted = cache.promote(result)
    cache.finish()
    assert (final / "video.mp4").read_bytes() == b"synthetic-video"
    assert promoted["output"]["path"] == str(final / "video.mp4")
    assert promoted["analysis_sidecar"]["path"] == str(final / "rudiment-analysis.jsonl")
    health = json.loads((final / "candidate-health.json").read_text(encoding="utf-8"))
    assert health["artifacts"][0]["path"] == str(final / "video.mp4")
    assert (final / "cache-promotion.json").is_file()
    assert cache_status(cache_root)["empty"] is True


def test_failed_cache_job_is_visible_but_reservations_are_released(tmp_path):
    cache_root = tmp_path / "fast-cache"
    cache = WorkingCache(
        cache_root,
        tmp_path / "delivery" / "render-002",
        reserve_bytes=4096,
        min_free_bytes=0,
    )
    cache.prepare()
    cache.begin_render()
    cache.fail("synthetic interruption")
    status = cache_status(cache_root)
    assert status["empty"] is False
    assert status["reservations"] == []
    assert status["jobs"][0]["status"]["state"] == "failed"


def test_render_wrapper_resumes_retained_cache_then_promotes(monkeypatch, tmp_path):
    module = importlib.import_module("framerudiments.render")
    project = Project.__new__(Project)
    calls = []

    def fake_chunked(_project, output, **kwargs):
        output = __import__("pathlib").Path(output)
        calls.append(kwargs["resume"])
        if not kwargs["resume"]:
            output.mkdir()
            (output / "qa").mkdir()
            raise RuntimeError("synthetic interruption")
        video = output / "video.mp4"
        video.write_bytes(b"resumed-video")
        result = {
            "state": "technical-QA passed; creative and attended listening review pending",
            "frames": 1,
            "output": {"path": str(video), "sha256": sha256(video), "bytes": video.stat().st_size},
        }
        write_json(output / "manifest.json", result)
        write_json(output / "candidate-health.json", {"artifacts": [{"path": str(video)}]})
        return result

    monkeypatch.setattr(module, "_render_chunked", fake_chunked)
    cache_root = tmp_path / "fast-cache"
    final = tmp_path / "delivery" / "resumed"
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        module.render(
            project,
            final,
            chunked=True,
            work_cache=cache_root,
            reserve_bytes=128 * 1024,
            min_free_bytes=0,
        )
    assert cache_status(cache_root)["empty"] is False
    result = module.render(
        project,
        final,
        chunked=True,
        resume=True,
        work_cache=cache_root,
        reserve_bytes=128 * 1024,
        min_free_bytes=0,
    )
    assert calls == [False, True]
    assert result["output"]["path"] == str(final / "video.mp4")
    assert cache_status(cache_root)["empty"] is True


def test_render_preserves_cache_setup_error_before_job_exists(tmp_path):
    module = importlib.import_module("framerudiments.render")
    project = Project.__new__(Project)
    cache_root = tmp_path / "fast-cache"
    final = tmp_path / "delivery" / "already-there"
    final.mkdir(parents=True)

    with pytest.raises(FileExistsError, match="Final output already exists"):
        module.render(
            project,
            final,
            chunked=True,
            work_cache=cache_root,
            reserve_bytes=4096,
            min_free_bytes=0,
        )

    assert not cache_root.exists()


def test_cache_status_cli_expect_empty(tmp_path, capsys):
    from framerudiments import cli

    root = tmp_path / "cache"
    assert cli.main(["cache-status", "--work-cache", str(root), "--expect-empty"]) == 0
    assert json.loads(capsys.readouterr().out)["empty"] is True
    (root / "retained-job").mkdir(parents=True)
    assert cli.main(["cache-status", "--work-cache", str(root), "--expect-empty"]) == 2
    assert json.loads(capsys.readouterr().out)["empty"] is False
