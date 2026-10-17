"""FrameRudiments Phase 5 local preview safety and source-clock contracts."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import authoring_preview as author  # noqa: E402


@pytest.fixture
def project_data():
    return {"schema": "framerudiments.project.v1", "title": "test",
            "audio": {"source": {"path": "tone.wav", "sha256": "a"*64},
                      "sample_rate": 48000, "start_sample": 0, "end_sample": 48000},
            "clock": {"bpm": 120, "authority": "synthetic", "status": "synthetic"},
            "layers": [{"id": "wave", "type": "rudiment", "renderer": "waveform",
                        "motion": {"kind": "named", "pattern": "bounce"}}]}


def test_presentation_only_edits_allow_keyframes_and_preserve_pin(project_data):
    draft = deepcopy(project_data)
    draft["layer_animations"] = {"layer:wave": {"opacity": {"keys": [
        {"time": 0.0, "value": 0.5}]}}}
    author.check_draft(project_data, draft)
    draft["audio"]["source"]["sha256"] = "b"*64
    with pytest.raises(ValueError, match="Read-only"):
        author.check_draft(project_data, draft)


def test_untrusted_added_asset_fails_without_changing_original(project_data):
    draft = deepcopy(project_data)
    draft["layers"][0]["style"] = {"sprite_asset": {"path": "../secret.txt", "sha256": "c"*64}}
    with pytest.raises(ValueError, match="unpinned"):
        author.check_draft(project_data, draft)
    assert "style" not in project_data["layers"][0]


def test_source_lrc_serialization_preserves_cue_types_and_unicode():
    cues = [{"id":"x", "start":2.5,"label":"[DROP: CHORUS]"},
            {"id":"y", "start":1.125,"label":"[SECTION: INTRO]"}]
    assert all(author.approved_cue(x["label"]) for x in cues)
    assert not author.approved_cue("[FAKE: OTHER]")
    assert author.lrc_at(cues) == "[00:01.125][SECTION: INTRO]\n[00:02.500][DROP: CHORUS]\n"
    assert "[00:00.020]été" in author.lrc_at([{"start":.02,"label":"été"}])
    with pytest.raises(ValueError, match="Multiline"):
        author.lrc_at([{"start":0,"label":"hello\nworld"}])


def test_localhost_serve_enforces_same_origin_and_no_path_traversal(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>Safe editor</h1>", encoding="utf-8")
    monkeypatch.setattr(author, "DIST", dist)

    class Handler(author.AuthoringHandler):
        session_data = {"project": {"schema":"framerudiments.project.v1"},
                        "audioPath": None}
        project_file = tmp_path / "project.json"

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/project") as response:
            assert json.loads(response.read())["project"]["schema"] == "framerudiments.project.v1"
        with urlopen(base + "/") as response:
            assert b"Safe editor" in response.read()
        request = Request(base + "/api/preview", data=b"{}", headers={"Origin": "https://evil.example"},
                          method="POST")
        with pytest.raises(HTTPError) as exc:
            urlopen(request)
        assert exc.value.code == 403
        with pytest.raises(HTTPError) as exc:
            urlopen(base + "/%2e%2e/secret.json")
        assert exc.value.code == 404
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def test_exact_wav_sample_window_does_not_misrepresent_trimmed_music(tmp_path):
    from types import SimpleNamespace
    import wave

    master = tmp_path / "master.wav"
    with wave.open(str(master), "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(8000)
        sound.writeframes(b"\0\0" * 8000)
    full = SimpleNamespace(start_sample=0, repeats=1, end_sample=8000, sample_rate=8000)
    assert author.exact_wav_audio(full, master)
    assert not author.exact_wav_audio(SimpleNamespace(**{**vars(full), "end_sample": 4000}), master)
    assert not author.exact_wav_audio(SimpleNamespace(**{**vars(full), "repeats": 2}), master)
    assert not author.exact_wav_audio(full, tmp_path / "master.mp3")


def test_only_explicit_loopback_development_origins_are_accepted():
    assert author.validated_dev_origins(["http://127.0.0.1:5173"]) == {"http://127.0.0.1:5173"}
    for bad in ("https://evil.example:5173", "http://0.0.0.0:5173",
                "http://127.0.0.1:5173/path", "http://127.0.0.1:5173@evil.com"):
        with pytest.raises(ValueError, match="loopback"):
            author.validated_dev_origins([bad])
