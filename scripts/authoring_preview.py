"""Local-only FrameRudiments authoring preview server.

Run: python scripts/authoring_preview.py path/to/project.json [--port 8765]
Build the UI first: cd authoring && npm install && npm run build

The server never overwrites project inputs or stores edited drafts. Preview
compositing does not execute the sequential Butterchurn/projectM chain.
"""

import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
from pathlib import Path
import re
import wave
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from urllib.parse import urlsplit, unquote


MAX_BODY = 2 * 1024 * 1024
PREVIEW_SLOTS = BoundedSemaphore(2)
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "authoring" / "dist"


def refs(tree):
    """All checksum-pinned file references recursively, regardless of field path."""
    if isinstance(tree, dict):
        if "path" in tree and "sha256" in tree:
            yield (str(tree["path"]), str(tree["sha256"]))
        for value in tree.values():
            yield from refs(value)
    elif isinstance(tree, list):
        for value in tree:
            yield from refs(value)


def check_draft(original, draft):
    """Presentation-only edits may not substitute any pinned input or source clock."""
    if not isinstance(draft, dict) or draft.get("schema") != "framerudiments.project.v1":
        raise ValueError("A FrameRudiments project.v1 object is required")
    allowed_top = {"layers", "layer_animations", "layer_masks", "layer_displacement",
                   "visualiser_routing", "visualiser_stage_injections", "post_effects",
                   "effects", "captions", "word_captions", "word_indicator", "identity",
                   "lyric_art", "background", "render_quality"}
    for key in set(original) | set(draft):
        if key not in allowed_top and draft.get(key) != original.get(key):
            raise ValueError(f"Read-only source field changed: {key}")
    # Distinct input identities require explicit import/pinning outside the editor.
    trusted = set(refs(original))
    for ref in refs(draft):
        if ref not in trusted:
            raise ValueError("Edited project introduces an unpinned or changed input reference")
    if set(draft) - set(original) - allowed_top:
        raise ValueError("Unknown draft project field")
    if len(json.dumps(draft)) > MAX_BODY:
        raise ValueError("Draft exceeds maximum request size")


def lrc_at(events):
    """Stable, source-clocked timestamped LRC sidecar from UI event entries."""
    lines = []
    for item in events:
        if not isinstance(item, dict) or not isinstance(item.get("label"), str):
            raise ValueError("Invalid source timing row")
        second = item.get("start")
        if type(second) not in (int, float) or not 0 <= second < 360000:
            raise ValueError("Invalid source timestamp")
        millis = round(second * 1000)
        label = item["label"]
        if "\n" in label or "\r" in label:
            raise ValueError("Multiline LRC event rejected")
        lines.append((millis, label))
    lines.sort(key=lambda row: row[0])
    return "".join(f"[{ms // 60000:02d}:{(ms // 1000) % 60:02d}.{ms % 1000:03d}]{label}\n"
                   for ms, label in lines)


def approved_cue(label):
    return re.fullmatch(r"\[(?:SECTION|ENTRY|DROP|BREAK):\s*[^\]\r\n]{1,120}\]", label) is not None


def render_preview(project_file, draft, frame, *, quality="fast", sidecars=None):
    """Render ONE draft compositor frame; the project folder is never mutated."""
    from framerudiments.project import Project
    from framerudiments.compositor import Compositor
    from framerudiments.io import sha256 as file_sha
    from PIL import Image

    original = Project.load(project_file)
    check_draft(original.data, draft)
    if type(frame) is not int or frame < 0 or frame >= original.frame_count:
        raise ValueError("Preview frame outside programme")
    if quality not in ("fast", "full"):
        raise ValueError("Unknown preview quality")
    contents = deepcopy(draft)
    with TemporaryDirectory(prefix="fr-authoring-preview-") as temporary:
        if sidecars is not None:
            if not isinstance(sidecars, dict) or set(sidecars) - {"lines", "words", "cues"}:
                raise ValueError("Invalid preview sidecars")
            mapping = {"lines": "lyrics", "words": "word_lyrics", "cues": "cues"}
            for kind, values in sidecars.items():
                if mapping[kind] not in contents:
                    raise ValueError(f"New {kind} inputs must be added and pinned explicitly")
                if not isinstance(values, list) or len(values) > 100000:
                    raise ValueError("Too many timing events")
                if kind == "cues" and any(not approved_cue(v.get("label", "")) for v in values):
                    # Existing CD cue title labels are allowed read-only, not editable.
                    raise ValueError("Edited cues require SECTION, ENTRY, DROP or BREAK labels")
                path = Path(temporary) / f"draft-{kind}.lrc"
                path.write_text(lrc_at(values), encoding="utf-8", newline="\n")
                contents[mapping[kind]] = {"path": str(path), "sha256": file_sha(path)}
        if quality == "fast":
            contents["render_quality"] = {"enabled": False}
        candidate = Project(contents, original.base)
        # Independent source refs are always rechecked before paint.
        candidate.verify_inputs()
        image, _ = Compositor(candidate).frame(frame, apply_quality=quality == "full")
        candidate.verify_inputs()
        if not isinstance(image, Image.Image) or image.size != (candidate.width, candidate.height):
            raise RuntimeError("Preview renderer returned an invalid frame")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()



def exact_wav_audio(project, filename):
    """Do not expose a trimmed master as an exact timeline excerpt."""
    if project.start_sample != 0 or project.repeats != 1 or Path(filename).suffix.lower() != ".wav":
        return False
    try:
        with wave.open(str(filename), "rb") as sound:
            return sound.getnframes() == project.end_sample and sound.getframerate() == project.sample_rate
    except (wave.Error, EOFError, OSError):
        return False


def validated_dev_origins(origins):
    """Vite debugging may allow additional *explicit* HTTP loopback origins only."""
    parsed_origins = set(origins)
    for origin in parsed_origins:
        parsed = urlsplit(origin)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or not parsed.port or parsed.path or parsed.query or parsed.fragment
                or parsed.username or parsed.password):
            raise ValueError("Development origin must be an exact HTTP loopback origin with port")
    return frozenset(parsed_origins)


def session(project_file):
    """Read only pinned source-ledger data through the native project parser."""
    from framerudiments.project import Project
    from framerudiments.timeline import parse_cue, parse_lrc
    from framerudiments.word_timing import parse_word_lrc
    from framerudiments.io import read_json

    p = Project.load(project_file)
    p.verify_inputs()
    def events(rows):
        return [{"id": f"item-{i}", "start": float(row.source_seconds), "label": row.text}
                for i, row in enumerate(rows)]
    lines = events(parse_lrc(p.lyric_ref["path"])) if p.lyric_ref else []
    words = ([{"id": f"word-{i}", "start": float(row.time), "label": row.text}
              for i, row in enumerate(parse_word_lrc(p.word_ref["path"]))] if p.word_ref else [])
    ref = p.ref(p.data["cues"]) if "cues" in p.data else None
    cue_format = "cue" if ref and ref["path"].lower().endswith(".cue") else "lrc"
    cues = events(parse_cue(ref["path"]) if cue_format == "cue" else parse_lrc(ref["path"], lyrics=False)) if ref else []
    beat = None
    if "beat_timeline" in p.data["clock"]:
        beat_ref = p.ref(p.data["clock"]["beat_timeline"])
        beat = read_json(beat_ref["path"])
    audio = p.audio_ref["path"]
    audio_available = exact_wav_audio(p, audio)
    return {"project": p.data, "lines": lines, "words": words, "cues": cues,
            "cueFormat": cue_format, "beatTimeline": beat,
            "audioAvailable": audio_available, "audioPath": audio if audio_available else None,
            "notes": ["Preview excludes sequential Butterchurn/projectM processing"] if p.data.get("effects") else []}


class AuthoringHandler(BaseHTTPRequestHandler):
    """Same-origin localhost-only API and static files, no writes into checkout."""

    project_file = None
    session_data = None
    server_version = "FrameRudimentsAuthoring/1"

    def _send(self, status, data, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self';")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return False
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        allowed.update(getattr(self.server, "allowed_dev_origins", ()))
        return origin in allowed

    def do_GET(self):
        target = urlsplit(self.path).path
        if target == "/api/project":
            data = {k: v for k, v in self.session_data.items() if k != "audioPath"}
            return self._json(200, data)
        if target == "/api/audio":
            path = self.session_data.get("audioPath")
            if not path:
                return self._json(409, {"error":"Supply an exact rendered excerpt for audio playback"})
            src = Path(path)
            size = src.stat().st_size
            lower, upper = 0, size - 1
            partial = False
            request = self.headers.get("Range", "")
            if request:
                matched = re.fullmatch(r"bytes=(\d*)-(\d*)", request)
                if not matched:
                    return self._json(416, {"error": "Invalid media range"})
                lower = int(matched[1]) if matched[1] else 0
                upper = int(matched[2]) if matched[2] else min(size-1,lower+2*1024*1024-1)
                if lower > upper or upper >= size:
                    return self._json(416, {"error":"Media range outside file"})
                partial = True
            count = upper - lower + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mimetypes.guess_type(src.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {lower}-{upper}/{size}")
            self.send_header("Content-Length", str(count))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with src.open("rb") as reader:
                reader.seek(lower)
                remaining = count
                while remaining:
                    part = reader.read(min(65536, remaining))
                    if not part:
                        break
                    self.wfile.write(part)
                    remaining -= len(part)
            return
        if target == "/":
            target = "/index.html"
        name = unquote(target.lstrip("/"))
        target_file = (DIST / name).resolve()
        if not target_file.is_relative_to(DIST.resolve()) or not target_file.is_file():
            return self._json(404, {"error":"Not found"})
        data = target_file.read_bytes()
        self._send(200, data, mimetypes.guess_type(target_file.name)[0] or "application/octet-stream")

    def do_POST(self):
        if urlsplit(self.path).path != "/api/preview":
            return self._json(404, {"error":"Unknown endpoint"})
        if not self._origin_ok():
            return self._json(403, {"error":"Same-origin local previews only"})
        try:
            size = int(self.headers.get("Content-Length", "-1"))
            if not 1 <= size <= MAX_BODY:
                raise ValueError("Preview request exceeds 2 MiB")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or set(body) - {"project", "frame", "quality", "sidecars"}:
                raise ValueError("Unexpected preview payload")
            if not PREVIEW_SLOTS.acquire(blocking=False):
                return self._json(429, {"error":"Preview busy; retry after completion"})
            try:
                image = render_preview(self.project_file, body.get("project"), body.get("frame"),
                                       quality=body.get("quality", "fast"), sidecars=body.get("sidecars"))
            finally:
                PREVIEW_SLOTS.release()
            self._send(200, image, "image/png")
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
            self._json(400, {"error": str(exc)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dev-origin", action="append", default=[],
                        help="Explicit additional loopback UI origin, e.g. http://127.0.0.1:5173")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Port must be 1..65535")
    try:
        origins = validated_dev_origins(args.dev_origin)
    except ValueError as exc:
        parser.error(str(exc))
    if not (DIST / "index.html").is_file():
        parser.error("Authoring UI not built: cd authoring && npm install && npm run build")
    project_file = args.project.expanduser().resolve(strict=True)
    data = session(project_file)
    class Handler(AuthoringHandler):
        pass
    Handler.project_file = project_file
    Handler.session_data = data
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.allowed_dev_origins = frozenset(origins)
    print(f"FrameRudiments authoring preview at http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
