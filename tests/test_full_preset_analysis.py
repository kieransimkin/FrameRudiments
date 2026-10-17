"""Offline regression coverage for the resumable full-preset PrismMoves runner."""

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "full_preset_analysis.py"
SPEC = importlib.util.spec_from_file_location("full_preset_analysis", SOURCE)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def test_preset_identity_is_stable_and_cannot_be_a_directory_traversal():
    key = RUNNER.name_key("projectm:012345")
    assert key == RUNNER.name_key("projectm:012345")
    assert key != RUNNER.name_key("projectm:012346")
    assert len(key) == 24 and key.isalnum()
    with pytest.raises(ValueError):
        RUNNER.name_key("")


def test_model_directories_and_caches_are_project_local(tmp_path):
    values = RUNNER.model_environment(tmp_path / "models")
    for name in ("PIXELCUE_MODELS_DIR", "HF_HOME", "HF_HUB_CACHE", "TORCH_HOME", "XDG_CACHE_HOME",
                 "PIP_CACHE_DIR", "UV_CACHE_DIR", "TESSDATA_PREFIX", "FRAMERUDIMENTS_BROWSER_PROFILE_ROOT"):
        value = Path(values[name])
        assert value.is_relative_to(tmp_path / "models")
        assert value.is_dir()


def test_output_location_cannot_escape_checkout_or_write_to_model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "MODELS", tmp_path / "models")
    assert RUNNER.assert_local_directory(tmp_path / "visualiser-analysis" / "full")
    for invalid in (tmp_path, tmp_path.parent, tmp_path / "models" / "results"):
        with pytest.raises(ValueError):
            RUNNER.assert_local_directory(invalid)


def test_experiment_configuration_is_immutable_on_resume(tmp_path):
    config = {"preset_catalogue_sha": "a" * 64, "models": ["clip", "blip"], "conditions": [1, 2]}
    signature = RUNNER.validate_experiment(tmp_path, config)
    assert signature == RUNNER.validate_experiment(tmp_path, config)
    with pytest.raises(ValueError, match="changed"):
        RUNNER.validate_experiment(tmp_path, {**config, "models": ["blip"]})


def test_model_hypotheses_are_explicitly_distinct_from_measured_values():
    item = {"name": "x.png", "status": "completed", "analysis": {
        "source": {"sha256": "f" * 64},
        "models": [
            {"name": "clip", "status": "completed", "result": {
                "axes": {"tone": [{"prompt": "a playful image", "cosine_similarity": 0.32}]}}},
            {"name": "blip", "status": "completed", "result": {"caption": "abstract oscillating rings"}},
            {"name": "pixelcue-tagger:smolvlm-256m", "status": "completed", "result": {"tags": ["rings", "digital"]}},
            {"name": "ocr", "status": "unavailable", "reason": "tesseract not installed"},
        ],
    }}
    selected = RUNNER.short_semantic(item)
    assert selected["caption_hypotheses"] == ["abstract oscillating rings"]
    assert selected["tags"] == ["rings", "digital"]
    assert selected["prompt_hypotheses"]["tone"]["prompt"] == "a playful image"
    assert "not a calibrated probability" in selected["prompt_hypotheses"]["tone"]["interpretation"]
    assert selected["models"][-1]["status"] == "unavailable"


def test_publish_and_index_escape_untrusted_preset_titles(tmp_path, monkeypatch):
    out = tmp_path / "project" / "visualiser-analysis" / "all"
    monkeypatch_root = out.parents[2]
    out.mkdir(parents=True)
    monkeypatch.setattr(RUNNER, "ROOT", monkeypatch_root)
    monkeypatch.setattr(RUNNER, "MODELS", monkeypatch_root / "models")
    RUNNER.ensure_workspace(out)
    row = {"id": "projectm:trusted-0", "name": '<script>alert("x")</script>',
           "engine": "projectm", "source_pack": "original", "licence": "unknown",
           "preset": {"sha256": "a" * 64}}
    completed = {"state": "captured", "complete": True, "conditions": []}
    numeric = {"state": "completed", "measured_response_ranges": {}, "conditions": [], "unknowns": []}
    semantic = {"status": "completed", "models": [{"status": "completed", "name": "clip"}],
                "caption_hypotheses": ["<b>suspect</b>"]}
    result = RUNNER.publish_record(out, row, "sig", completed, numeric, semantic)
    assert result["status"] == "complete"
    assert (out / "reports" / (result["key"] + ".json")).is_file()
    assert "&lt;script&gt;" in (out / "reports" / (result["key"] + ".md")).read_text()
    index = RUNNER.update_index(out, [result], total=2, signature="sig")
    assert index["counts"] == {"complete": 1, "partial": 0, "pending": 1}
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;b&gt;suspect&lt;/b&gt;" in page


def test_incomplete_catalogue_cannot_verify_or_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "MODELS", tmp_path / "models")
    out = RUNNER.ensure_workspace(tmp_path / "visualiser-analysis" / "all")
    RUNNER.write_json(out / "experiment.json", {"signature": "abc"})
    RUNNER.update_index(out, [], total=5, signature="abc")
    args = SimpleNamespace(output=out)
    with pytest.raises(ValueError, match="incomplete"):
        RUNNER.verify(args)
    with pytest.raises(ValueError, match="incomplete"):
        RUNNER.commit_if_complete(out, RUNNER.read_json(out / "index.json"))


def test_run_resumes_and_never_retries_existing_partial_without_request(tmp_path, monkeypatch):
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "MODELS", tmp_path / "models")
    (tmp_path / "models").mkdir()
    output = tmp_path / "visualiser-analysis" / "full-catalogue"
    lib = tmp_path / "models" / "catalogue.json"
    rows = [{"id": f"projectm:{j}", "name": f"preset-{j}", "engine": "projectm",
             "preset": {"sha256": str(j) * 64}} for j in range(3)]
    RUNNER.write_json(lib, {"schema": "framerudiments.preset-library.v1", "presets": rows})
    suite = tmp_path / "suite.json"
    RUNNER.write_json(suite, {"schema": "framerudiments.preset-suite.v1", "projects": []})
    args = SimpleNamespace(output=output, catalogue=lib, suite=suite, font=None,
                           intervals=[0, 1], images=["grid-rings"], audio_modes=["silence"],
                           models=["clip", "blip"], keyword_models=["smolvlm-256m"],
                           device="cpu", max_edge=256, semantic_edge=384, semantic_images=1,
                           include_builtin=False, render_python=Path(sys.executable),
                           analysis_python=Path(sys.executable), retry_failed=False,
                           limit=2, commit=False)
    calls = []
    def fake_preset(config, preset, signature, dest, catalogue_path, suite_path, env):
        calls.append(preset["id"])
        return RUNNER._report_failed(dest, preset, signature, "Synthetic failure")
    monkeypatch.setattr(RUNNER, "run_preset", fake_preset)
    assert RUNNER.run(args) == 2
    assert calls == ["projectm:0", "projectm:1"]
    assert RUNNER.run(args) == 2
    assert calls == ["projectm:0", "projectm:1", "projectm:2"]
    assert RUNNER.run(args) == 2
    assert calls == ["projectm:0", "projectm:1", "projectm:2"]
    args.retry_failed = True
    args.limit = 1
    assert RUNNER.run(args) == 2
    assert len(calls) == 4


def test_candidate_image_uses_reproduced_sha256_and_order(tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"fixture")
    record = {"conditions": [{"state": "captured", "imagery": "cover-lyrics", "audio_mode": "programme",
            "captures": [{"output_seconds": 1., "output": {"path": str(image), "sha256": RUNNER.file_digest(image)}}]}]}
    assert RUNNER.semantic_sources(record, 1) == [image]
    image.write_bytes(b"changed")
    assert RUNNER.semantic_sources(record, 1) == []


def test_downloaded_wheels_are_not_placed_in_tracked_output(tmp_path, monkeypatch):
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "MODELS", tmp_path / "models")
    dest = RUNNER.ensure_workspace(tmp_path / "visualiser-analysis" / "full-catalogue")
    assert (dest / ".gitignore").read_text() == "_work/\n*.tmp\n"
    assert "models" not in str(dest.relative_to(tmp_path))


def test_one_preset_runs_capture_measurement_and_optional_semantics(tmp_path, monkeypatch):
    """All three existing executables are called; no duplicate image engine."""
    from PIL import Image
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "MODELS", tmp_path / "models")
    (tmp_path / "models").mkdir()
    out = RUNNER.ensure_workspace(tmp_path / "visualiser-analysis" / "full")
    row = {"id": "butterchurn:abcd", "name": "Test preset", "engine": "butterchurn",
           "preset": {"path": "somewhere", "sha256": "f" * 64}}
    args = SimpleNamespace(render_python=Path(sys.executable), analysis_python=Path(sys.executable),
                           intervals=[0, 0.5], images=["cover-lyrics"], audio_modes=["programme"],
                           models=["clip", "blip"], keyword_models=["smolvlm-256m"],
                           max_edge=128, semantic_edge=128, semantic_images=1, device="cpu",
                           capture_timeout=10, analysis_timeout=10, semantic_timeout=10, retry_failed=False)
    calls = []
    def simulated_subprocess(command, log, *, environment, timeout):
        calls.append(command)
        key = RUNNER.name_key(row["id"])
        if "presets-profile" in command:
            folder = out / "_work" / "jobs" / key / "run" / "presets" / row["id"].replace(":", "-")
            folder.mkdir(parents=True, exist_ok=True)
            sample = folder / "capture.png"
            Image.new("RGB", (32, 32), "navy").save(sample)
            RUNNER.write_json(folder / "captures.json", {
                "schema": "framerudiments.preset-captures.v1", "preset": {"id": row["id"]},
                "state": "captured", "complete": True,
                "conditions": [{"state": "captured", "imagery": "cover-lyrics", "audio_mode": "programme",
                                "sample": 1, "captures": [{"output_seconds": 0.5,
                                                             "output": {"path": str(sample),
                                                                        "sha256": RUNNER.file_digest(sample)}}]}]})
        elif "profile_analysis.py" in " ".join(command):
            dest = out / "_work" / "jobs" / key / "run" / "presets" / row["id"].replace(":", "-") / "identity"
            dest.mkdir(parents=True, exist_ok=True)
            RUNNER.write_json(dest / "response.json", {"state": "completed", "measured_response_ranges": {
                "hue_js_divergence_bits": {"min": .1, "median": .2, "max": .3, "n": 1}},
                "conditions": [{"id": "song", "state": "captured", "audio_mode": "programme"}]})
        elif "prismmoves" in command:
            dest = Path(command[command.index("--out") + 1])
            dest.mkdir(parents=True)
            RUNNER.write_json(dest / "analysis.json", {"items": [{"status": "completed", "name": "capture.png",
                "analysis": {"source": {"sha256": "f" * 64}, "measurements": {"colour": {}},
                             "models": [{"name": "clip", "status": "completed", "result": {"axes": {}}},
                                        {"name": "blip", "status": "completed", "result": {"caption": "abstract geometry"}},
                                        {"name": "pixelcue-tagger:smolvlm-256m", "status": "completed",
                                         "result": {"tags": ["abstract", "blue"]}}]}}]})
        else:
            raise AssertionError(command)
        return 0
    monkeypatch.setattr(RUNNER, "run_logged", simulated_subprocess)
    result = RUNNER.run_preset(args, row, "signature", out, tmp_path / "catalogue.json",
                               tmp_path / "suite.json", {})
    assert result["status"] == "complete"
    assert len(calls) == 3
    assert any("--models-dir" in call and str(tmp_path / "models") in call for call in calls)
    assert any("--pixelcue-models" in call and "smolvlm-256m" in call for call in calls)
    report = RUNNER.read_json(out / "reports" / f"{result['key']}.json")
    assert report["semantic"]["caption_hypotheses"] == ["abstract geometry"]
    assert report["quantitative"]["measured_response_ranges"]["hue_js_divergence_bits"]["median"] == .2
    assert report["preview"]
    assert (out / report["preview"]).is_file()
    assert (out / "reports" / f"{result['key']}.pixelcue.json.gz").is_file()
    RUNNER.write_json(out / "experiment.json", {"signature": "signature"})
    RUNNER.update_index(out, [result], total=1, signature="signature")
    assert RUNNER.verify(SimpleNamespace(output=out)) == 0
    # A second visit reuses the completed semantic analysis receipt, not the models.
    again = RUNNER.run_preset(args, row, "signature", out, tmp_path / "catalogue.json",
                              tmp_path / "suite.json", {})
    assert again["status"] == "complete"
    assert len(calls) == 5  # only the capture revalidation + numeric receipt check


def test_missing_requested_model_is_never_mislabeled_as_complete(tmp_path):
    out = tmp_path / "visualiser-analysis"
    out.mkdir()
    row = {"id": "r", "name": "r", "engine": "projectm", "preset": {"sha256": "f" * 64}}
    captured = {"state": "captured", "complete": True, "conditions": []}
    response = {"state": "completed", "conditions": []}
    semantic = {"status": "completed", "models": [{"name": "blip", "status": "completed"},
                           {"name": "ocr", "status": "unavailable", "reason": "Missing OCR"}],
                "caption_hypotheses": ["Animated stars"]}
    result = RUNNER.publish_record(out, row, "s", captured, response, semantic)
    assert result["status"] == "partial"
    assert "Missing OCR" in (out / "reports" / f"{result['key']}.json").read_text()


def test_legacy_model_override_cannot_escape_project(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXELCUE_MODEL_DIR", str(tmp_path.parent / "unsafe-global-cache"))
    env = RUNNER.model_environment(tmp_path / "models")
    assert "PIXELCUE_MODEL_DIR" not in env
    for name in ("HF_HOME", "HF_XET_CACHE", "HF_DATASETS_CACHE", "HUGGINGFACE_HUB_CACHE"):
        assert Path(env[name]).is_relative_to(tmp_path / "models")
        assert Path(env[name]).is_dir()


def test_missing_requested_model_cannot_be_called_complete(tmp_path):
    out = tmp_path / "results"
    (out / "reports").mkdir(parents=True)
    (out / "previews").mkdir()
    row = {"id": "projectm:abc", "name": "Geometry", "engine": "projectm",
           "preset": {"sha256": "f" * 64}}
    captures = {"state": "captured", "complete": True, "conditions": []}
    numeric = {"state": "completed", "conditions": []}
    semantic = {"status": "completed", "models": [
        {"name": "clip", "status": "completed"},
        {"name": "blip", "status": "completed"},
    ]}
    missing = RUNNER.publish_record(out, row, "s", captures, numeric, semantic,
                                   required_models=("clip", "blip", "ocr"))
    assert missing["status"] == "partial"
    semantic["models"].append({"name": "ocr", "status": "completed"})
    correct = RUNNER.publish_record(out, row, "s", captures, numeric, semantic,
                                   required_models=("clip", "blip", "ocr"))
    assert correct["status"] == "complete"
    semantic["examples"] = [{"status": "completed", "models": [
        {"name": "clip", "status": "completed"},
        {"name": "blip", "status": "unavailable"},
        {"name": "ocr", "status": "completed"},
    ]}]
    failed_second_image = RUNNER.publish_record(out, row, "s", captures, numeric, semantic,
                                                required_models=("clip", "blip", "ocr"))
    assert failed_second_image["status"] == "partial"


def test_complete_inventory_with_explicit_partial_receipts_can_be_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "MODELS", tmp_path / "models")
    out = RUNNER.ensure_workspace(tmp_path / "visualiser-analysis" / "all")
    RUNNER.write_json(out / "experiment.json", {"signature": "abc"})
    row = {"id": "broken:shader", "name": "Unsupported shader", "engine": "projectm",
           "preset": {"sha256": "a" * 64}}
    result = RUNNER._report_failed(out, row, "abc", "Shader error receipt")
    idx = RUNNER.update_index(out, [result], total=1, signature="abc")
    assert idx["counts"] == {"complete": 0, "partial": 1, "pending": 0}
    assert RUNNER.verify(SimpleNamespace(output=out)) == 0
    with pytest.raises(ValueError, match="partial"):
        RUNNER.verify(SimpleNamespace(output=out, require_success=True))
    # A partial is visibly labelled as such, not silently called successful.
    assert "Partial: 1" in (out / "SUMMARY.md").read_text(encoding="utf-8")


def test_semantic_receipt_is_hashed_and_required_by_verification(tmp_path, monkeypatch):
    import gzip
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "MODELS", tmp_path / "models")
    out = RUNNER.ensure_workspace(tmp_path / "visualiser-analysis" / "all")
    RUNNER.write_json(out / "experiment.json", {"signature": "abc"})
    row = {"id": "some:preset", "name": "Some Preset", "engine": "butterchurn",
           "preset": {"sha256": "b" * 64}}
    key = RUNNER.name_key(row["id"])
    zipped = out / "reports" / f"{key}.pixelcue.json.gz"
    zipped.write_bytes(gzip.compress(RUNNER.stable_json({"models": ["clip"]}),mtime=0))
    semantic = {"status": "completed", "models": [{"name": "clip", "status": "completed"}]}
    result = RUNNER.publish_record(out, row, "abc", {"state": "captured", "complete": True},
                                  {"state": "completed"}, semantic,
                                  required_models=("clip",))
    RUNNER.update_index(out, [result], total=1, signature="abc")
    assert RUNNER.verify(SimpleNamespace(output=out)) == 0
    zipped.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="semantic receipt"):
        RUNNER.verify(SimpleNamespace(output=out))
    assert RUNNER.existing_rows(out, "abc")[row["id"]]["interrupted"] is True


def test_python_entrypoint_help_uses_only_standard_library():
    import subprocess
    result = subprocess.run([sys.executable, str(SOURCE), "--help"],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0
    assert "install-library" in result.stdout
    assert "--checkpoint-every" in result.stdout


def test_pixelcue_tagging_is_enabled_by_default():
    assert RUNNER.DEFAULT_VLM == ("smolvlm-256m",)
    assert "pixelcue-tagger:" + RUNNER.DEFAULT_VLM[0] == "pixelcue-tagger:smolvlm-256m"


def test_pixelcue_model_setup_stays_project_local(tmp_path, monkeypatch):
    import importlib.util

    path = SOURCE.with_name("prepare_pixelcue_taggers.py")
    spec = importlib.util.spec_from_file_location("prepare_pixelcue_taggers", path)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setenv("PIXELCUE_MODEL_DIR", str(tmp_path / "external-old-override"))

    made = []

    class FakeTagger:
        def __init__(self, directory):
            self.model_dir = directory

        def ensure_downloaded(self):
            made.append(self.model_dir)
            self.model_dir.mkdir(parents=True, exist_ok=True)
            return self.model_dir

    root = tmp_path / "models"
    selected = installer.prepare_taggers(root, ["smolvlm-256m", "smolvlm-256m"],
                                          factory=lambda name: FakeTagger(root / name))
    assert selected == [root / "smolvlm-256m"]
    assert made == selected
    assert "PIXELCUE_MODEL_DIR" not in installer.os.environ
    assert installer.os.environ["PIXELCUE_MODELS_DIR"] == str(root)

    def escaping(name):
        return FakeTagger(tmp_path / "external")

    with pytest.raises(ValueError, match="outside"):
        installer.prepare_taggers(root, ["smolvlm-256m"], factory=escaping)
    with pytest.raises(ValueError, match="supported"):
        installer.prepare_taggers(root, ["untrusted-vlm"], factory=escaping)


def test_pixelcue_tags_are_searchable_and_html_escaped(tmp_path, monkeypatch):
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "MODELS", tmp_path / "models")
    out = RUNNER.ensure_workspace(tmp_path / "visualiser-analysis" / "full-catalogue")
    preset = {"id": "projectm:tag-test", "name": "Interesting visualiser", "engine": "projectm",
              "preset": {"sha256": "a" * 64}}
    captures = {"state": "captured", "complete": True, "conditions": []}
    numerical = {"state": "completed", "measured_response_ranges": {}}
    semantic = {"status": "completed", "models": [{"name": "pixelcue-tagger:smolvlm-256m", "status": "completed"}],
                "caption_hypotheses": ["Spinning lights"], "tags": ["neon tunnel", "<script>alert(1)</script>"]}
    row = RUNNER.publish_record(out, preset, "sig", captures, numerical, semantic,
                                required_models=("pixelcue-tagger:smolvlm-256m",))
    assert row["tags"] == ["neon tunnel", "<script>alert(1)</script>"]
    RUNNER.update_index(out, [row], total=1, signature="sig")
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "neon tunnel" in page
    assert 'data-search="interesting visualiser projectm projectm:tag-test neon tunnel' in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    restored = RUNNER.existing_rows(out, "sig")
    assert restored[preset["id"]]["tags"] == row["tags"]
