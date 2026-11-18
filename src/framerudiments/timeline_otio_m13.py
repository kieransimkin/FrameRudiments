"""M13: explicit, bounded, exact-frame OpenTimelineIO interchange.

No native compositor, DSP, plugin discovery, network media linking or timeline
rendering is performed. OpenTimelineIO is an *optional* dependency, imported
only when reading or writing a .otio file using the built-in JSON adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

SCHEMA = "framerudiments.timeline-interchange.v1"
NAMESPACE = "framerudiments"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_TRACKS = 32
MAX_ITEMS = 16_384
MAX_MARKERS = 4_096
MAX_FRAMES = 5_184_000  # 12 h at 120 frames/second
MAX_NAME = 256
MAX_SOURCE_BYTES = 2 * 1024**3
_ID = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COLOURS = frozenset(("RED", "GREEN", "BLUE", "YELLOW", "ORANGE", "PURPLE", "CYAN", "MAGENTA", "PINK", "WHITE", "BLACK"))


def _object(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label} must contain exactly {', '.join(sorted(expected))}")
    return value


def _name(value, label):
    if not isinstance(value, str) or not value or len(value) > MAX_NAME or any(
            ord(c) < 32 for c in value):
        raise ValueError(f"{label} needs a nonempty printable name (max {MAX_NAME})")
    return value


def _id(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label} needs an identifier starting with a letter (max 64)")
    return value


def _integer(value, label, *, minimum=0, maximum=MAX_FRAMES):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer between {minimum} and {maximum}")
    return value


def _rate_pair(value, label, *, maximum=120):
    if isinstance(value, (str, bytes, dict)):
        raise ValueError(f"{label} must be [positive numerator, denominator]")
    try:
        pair = list(value)
    except TypeError as exc:
        raise ValueError(f"{label} must be [positive numerator, denominator]") from exc
    if (len(pair) != 2 or any(type(v) is not int for v in pair) or
            pair[1] <= 0 or pair[0] <= 0 or pair[0] > 1_000_000 or pair[1] > 1_000_000):
        raise ValueError(f"{label} must be [positive numerator, denominator]")
    result = Fraction(pair[0], pair[1])
    if not 1 <= result <= maximum:
        raise ValueError(f"{label} must be between 1 and {maximum} ticks/second")
    return [result.numerator, result.denominator]


def _fps(value, label="fps"):
    return _rate_pair(value, label)


def _source_rate(value):
    return _rate_pair(value, "source_fps", maximum=768000)


def _uri(value):
    # Importing an edit decision list is not permission to dereference links.
    # This subset admits portable relative file paths only, no URLs or traversals.
    if (not isinstance(value, str) or not 1 <= len(value) <= 512 or
            any(ord(c) < 32 for c in value) or '\\' in value or ':' in value or
            '%' in value or '?' in value or '#' in value or value.startswith('/')):
        raise ValueError("media uri must be a plain relative POSIX file path")
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError("media uri must not contain empty, '.' or '..' components")
    return value


def _read_json(path):
    target = Path(path)
    if not target.is_file() or target.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Timeline JSON must be a file no larger than 8 MiB")

    def unique(pairs):
        output = {}
        for k, v in pairs:
            if k in output:
                raise ValueError(f"Duplicate timeline JSON field: {k}")
            output[k] = v
        return output

    def invalid_number(value):
        raise ValueError(f"Invalid JSON numeric constant: {value}")

    return json.loads(target.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=invalid_number)


def validate(document, *, allow_unpinned=False):
    """Return a detached canonical JSON document, or reject the whole edit.

    A transition is an edge *between* clips and does not add timeline length.
    Source hashes are required unless explicitly enabling unresolved imports.
    """
    _object(document, ("schema", "name", "fps", "tracks", "markers"), "timeline")
    if document["schema"] != SCHEMA:
        raise ValueError(f"Expected timeline schema {SCHEMA}")
    fps = _fps(document["fps"])
    if not isinstance(document["tracks"], list) or not 1 <= len(document["tracks"]) <= MAX_TRACKS:
        raise ValueError("Timeline must have 1..32 tracks")
    if not isinstance(document["markers"], list) or len(document["markers"]) > MAX_MARKERS:
        raise ValueError("Timeline has too many markers")
    tracks, track_ids, item_ids = [], set(), set()
    count, maximum = 0, 0
    for row in document["tracks"]:
        _object(row, ("id", "kind", "items"), "track")
        identity = _id(row["id"], "track.id")
        if identity in track_ids:
            raise ValueError("Duplicate track id")
        track_ids.add(identity)
        if row["kind"] not in ("video", "audio"):
            raise ValueError("Track kind must be video or audio")
        if not isinstance(row["items"], list) or not row["items"]:
            raise ValueError("Track must contain at least one clip or gap")
        sequence, duration = [], 0
        for item in row["items"]:
            if not isinstance(item, dict):
                raise ValueError("Timeline item must be an object")
            kind = item.get("type")
            fields = {
                "clip": ("type", "id", "name", "source", "source_fps",
                         "source_start_frame", "duration_frames"),
                "gap": ("type", "id", "duration_frames"),
                "transition": ("type", "id", "style", "in_frames", "out_frames"),
            }
            if not isinstance(kind, str) or kind not in fields:
                raise ValueError("Unsupported timeline item type")
            _object(item, fields[kind], f"{kind} item")
            item_id = _id(item["id"], f"{kind}.id")
            if item_id in item_ids:
                raise ValueError("Duplicate item id")
            item_ids.add(item_id)
            count += 1
            if count > MAX_ITEMS:
                raise ValueError("Timeline exceeds item count budget")
            if kind == "clip":
                _object(item["source"], ("uri", "sha256"), "clip.source")
                uri = _uri(item["source"]["uri"])
                digest = item["source"]["sha256"]
                if digest is None:
                    if not allow_unpinned:
                        raise ValueError("Unpinned source requires explicit allow_unpinned")
                elif not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                    raise ValueError("Source SHA-256 must be 64 lowercase hexadecimal characters")
                source_fps = _source_rate(item["source_fps"])
                start = _integer(item["source_start_frame"], "source start", maximum=10**12)
                length = _integer(item["duration_frames"], "clip duration", minimum=1)
                # The clip describes playback at 1x speed. No implicit resampling
                # or source-time quantisation happens in this interchange subset.
                source_frames = Fraction(length) * Fraction(*source_fps) / Fraction(*fps)
                if source_frames.denominator != 1:
                    raise ValueError("Clip duration is off its declared media source frame grid")
                sequence.append({"type": kind, "id": item_id, "name": _name(item["name"], "clip name"),
                                 "source": {"uri": uri, "sha256": digest},
                                 "source_fps": source_fps, "source_start_frame": start,
                                 "duration_frames": length})
                duration += length
            elif kind == "gap":
                length = _integer(item["duration_frames"], "gap duration", minimum=1)
                sequence.append({"type": kind, "id": item_id, "duration_frames": length})
                duration += length
            else:
                style = item["style"]
                if style not in ("dissolve", "wipe"):
                    raise ValueError("Only dissolve and explicitly tagged wipe transitions are supported")
                incoming = _integer(item["in_frames"], "transition in", maximum=512)
                outgoing = _integer(item["out_frames"], "transition out", maximum=512)
                if not incoming and not outgoing:
                    raise ValueError("Transition needs nonzero overlap")
                if not sequence or sequence[-1]["type"] != "clip":
                    raise ValueError("Transition must follow a clip")
                sequence.append({"type": kind, "id": item_id, "style": style,
                                 "in_frames": incoming, "out_frames": outgoing})
            if duration > MAX_FRAMES:
                raise ValueError("Timeline duration exceeds frame budget")
        for pos, item in enumerate(sequence):
            if item["type"] != "transition":
                continue
            if pos + 1 >= len(sequence) or sequence[pos + 1]["type"] != "clip":
                raise ValueError("Transition must precede a clip, without gaps or another transition")
            # OTIO in_offset reads into the *incoming next* clip; out_offset
            # reads from the *outgoing previous* clip. Never swap their bounds.
            if (item["in_frames"] > sequence[pos + 1]["duration_frames"] or
                    item["out_frames"] > sequence[pos - 1]["duration_frames"]):
                raise ValueError("Transition offsets exceed adjacent clip durations")
        tracks.append({"id": identity, "kind": row["kind"], "items": sequence})
        maximum = max(maximum, duration)
    if maximum == 0:
        raise ValueError("Timeline contains no duration")
    markers, ids = [], set()
    for marker in document["markers"]:
        _object(marker, ("id", "name", "frame", "duration_frames", "color"), "marker")
        mid = _id(marker["id"], "marker.id")
        if mid in ids:
            raise ValueError("Duplicate marker id")
        ids.add(mid)
        start = _integer(marker["frame"], "marker frame")
        length = _integer(marker["duration_frames"], "marker duration")
        if start + length > maximum:
            raise ValueError("Marker extends beyond programme length")
        colour = marker["color"]
        if not isinstance(colour, str) or colour not in _COLOURS:
            raise ValueError("Unsupported marker colour")
        markers.append({"id": mid, "name": _name(marker["name"], "marker name"),
                        "frame": start, "duration_frames": length, "color": colour})
    return {"schema": SCHEMA, "name": _name(document["name"], "timeline name"),
            "fps": fps, "tracks": tracks, "markers": markers}


def summary(document):
    normal = validate(document, allow_unpinned=True)
    rate = Fraction(*normal["fps"])
    durations = [sum(i["duration_frames"] for i in t["items"] if i["type"] != "transition")
                 for t in normal["tracks"]]
    length = max(durations)
    seconds = Fraction(length, 1) / rate
    return {"schema": SCHEMA, "fps": normal["fps"], "frames": length,
            "duration_seconds": [seconds.numerator, seconds.denominator],
            "tracks": len(normal["tracks"]), "clips": sum(
                i["type"] == "clip" for track in normal["tracks"] for i in track["items"]),
            "transitions": sum(i["type"] == "transition" for track in normal["tracks"]
                               for i in track["items"]),
            "markers": len(normal["markers"]), "unverified_media": sum(
                i["type"] == "clip" and i["source"]["sha256"] is None
                for track in normal["tracks"] for i in track["items"])}


def verify_sources(document, root):
    """Verify trusted local media bytes without fetching URLs or following links out of root."""
    normal = validate(document)
    root = Path(root).resolve(strict=True)
    seen, receipts = set(), []
    for track in normal["tracks"]:
        for item in track["items"]:
            if item["type"] != "clip":
                continue
            source = item["source"]
            key = (source["uri"], source["sha256"])
            if key in seen:
                continue
            seen.add(key)
            path = (root / source["uri"]).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
                raise ValueError("Pinned source escapes root or exceeds 2 GiB")
            digest = hashlib.sha256()
            with path.open('rb') as reader:
                while block := reader.read(1024 * 1024):
                    digest.update(block)
            if digest.hexdigest() != source["sha256"]:
                raise ValueError("Pinned source content differs from its SHA-256")
            receipts.append({"uri": source["uri"], "sha256": digest.hexdigest(),
                             "size_bytes": path.stat().st_size})
    return receipts


def _otio():
    try:
        import opentimelineio as otio
    except ImportError as exc:
        raise RuntimeError("OpenTimelineIO is optional; install framerudiments[timeline_otio]") from exc
    return otio


def _rt(otio, frames, fps):
    return otio.opentime.RationalTime(float(frames), float(Fraction(*fps)))


def _range(otio, start, duration, fps):
    return otio.opentime.TimeRange(start_time=_rt(otio, start, fps),
                                   duration=_rt(otio, duration, fps))


def to_otio(document, *, allow_unpinned=False):
    """Build only standard OTIO objects with exact rates in namespaced metadata."""
    normal = validate(document, allow_unpinned=allow_unpinned)
    otio = _otio()
    fps = normal["fps"]
    timeline = otio.schema.Timeline(name=normal["name"])
    timeline.metadata[NAMESPACE] = {"schema": SCHEMA, "fps": fps}
    lengths = []
    for track in normal["tracks"]:
        item_track = otio.schema.Track(name=track["id"],
                                      kind=otio.schema.TrackKind.Video if track["kind"] == "video"
                                      else otio.schema.TrackKind.Audio)
        item_track.metadata[NAMESPACE] = {"id": track["id"]}
        size = 0
        for item in track["items"]:
            if item["type"] == "clip":
                sr = item["source_fps"]
                clip = otio.schema.Clip(name=item["name"])
                clip.source_range = _range(otio, item["source_start_frame"],
                    Fraction(item["duration_frames"]) * Fraction(*sr) / Fraction(*fps), sr)
                clip.media_reference = otio.schema.ExternalReference(target_url=item["source"]["uri"])
                clip.metadata[NAMESPACE] = {
                    "id": item["id"], "source_fps": sr,
                    "source_start_frame": item["source_start_frame"],
                    "duration_frames": item["duration_frames"],
                    "sha256": item["source"]["sha256"],
                    "status": "pinned" if item["source"]["sha256"] else "unverified",
                }
                item_track.append(clip)
                size += item["duration_frames"]
            elif item["type"] == "gap":
                gap = otio.schema.Gap(name=item["id"], duration=_rt(otio, item["duration_frames"], fps))
                gap.metadata[NAMESPACE] = {"id": item["id"]}
                item_track.append(gap)
                size += item["duration_frames"]
            else:
                kind = (otio.schema.TransitionTypes.SMPTE_Dissolve if item["style"] == "dissolve"
                        else otio.schema.TransitionTypes.Custom)
                effect = otio.schema.Transition(name=item["id"], transition_type=kind,
                    in_offset=_rt(otio, item["in_frames"], fps),
                    out_offset=_rt(otio, item["out_frames"], fps))
                effect.metadata[NAMESPACE] = {"id": item["id"], "style": item["style"]}
                item_track.append(effect)
        timeline.tracks.append(item_track)
        lengths.append(size)
    # Markers live on the longest track. Their times are relative to that
    # track, and remain visible to conforming OTIO editors.
    longest = timeline.tracks[lengths.index(max(lengths))]
    for marker in normal["markers"]:
        stamp = otio.schema.Marker(name=marker["name"],
            marked_range=_range(otio, marker["frame"], marker["duration_frames"], fps),
            color=getattr(otio.schema.MarkerColor, marker["color"]))
        stamp.metadata[NAMESPACE] = {"id": marker["id"]}
        longest.markers.append(stamp)
    return timeline


def _frame_from_rt(rt, fps, label):
    if rt is None or not math.isfinite(float(rt.value)) or not math.isfinite(float(rt.rate)):
        raise ValueError(f"Invalid OTIO {label}")
    if float(rt.rate) <= 0:
        raise ValueError(f"Invalid OTIO {label} rate")
    count = Fraction(str(float(rt.value))) * Fraction(*fps) / Fraction(str(float(rt.rate)))
    rounded = math.floor(count + Fraction(1, 2))
    # OTIO stores rates as doubles, while private metadata carries their exact
    # rational form. Accept only representational error, not fractional frames.
    if abs(count - rounded) > Fraction(1, 1_000_000):
        raise ValueError(f"OTIO {label} is off the selected exact frame grid")
    return rounded


def _metadata(obj):
    raw = obj.metadata.get(NAMESPACE, {})
    if not isinstance(raw, Mapping) and not hasattr(raw, "items"):
        raise ValueError("Malformed FrameRudiments OTIO metadata")
    return dict(raw.items())


def _otio_identifier(meta, fallback):
    value = meta.get("id", fallback)
    return _id(value, "OTIO id")


def from_otio(timeline, *, fps=None, allow_unpinned=False):
    """Import a deliberately restricted, non-lossy flat OTIO edit decision list."""
    otio = _otio()
    if not isinstance(timeline, otio.schema.Timeline):
        raise ValueError("Only an OTIO Timeline can be imported")
    info = _metadata(timeline)
    native_rate = info.get("fps")
    if fps is None and native_rate is None:
        raise ValueError("External OTIO needs an explicit --fps; no frame rate is guessed")
    if fps is not None and native_rate is not None and _fps(fps) != _fps(native_rate):
        raise ValueError("Requested FPS conflicts with the timeline's exact FPS metadata")
    rate = _fps(fps if fps is not None else native_rate)
    if timeline.global_start_time is not None and _frame_from_rt(
            timeline.global_start_time, rate, "global start") != 0:
        raise ValueError("Nonzero global OTIO timeline origins are not supported")
    tracks, markers = [], []
    for t_index, track in enumerate(timeline.tracks):
        if not isinstance(track, otio.schema.Track):
            raise ValueError("Nested OTIO stacks/tracks are unsupported; flatten explicitly")
        if not track.enabled or track.effects or track.source_range is not None:
            raise ValueError("Disabled/retimed/effected OTIO tracks cannot be imported faithfully")
        kind = "video" if track.kind == otio.schema.TrackKind.Video else (
            "audio" if track.kind == otio.schema.TrackKind.Audio else None)
        if kind is None:
            raise ValueError("Unsupported OTIO track kind")
        tid = _otio_identifier(_metadata(track), f"track{t_index + 1}")
        items = []
        for index, part in enumerate(track):
            meta = _metadata(part)
            identity = _otio_identifier(meta, f"{tid}_item{index + 1}")
            if isinstance(part, otio.schema.Clip):
                if not part.enabled or part.effects or part.markers or part.source_range is None:
                    raise ValueError("Clip effects, local markers, or missing trim need flattening")
                if not isinstance(part.media_reference, otio.schema.ExternalReference):
                    raise ValueError("Only external media references are importable")
                source_uri = _uri(part.media_reference.target_url)
                source_rate = meta.get("source_fps")
                if source_rate is None:
                    approximated = Fraction(str(float(part.source_range.start_time.rate))).limit_denominator(1_000_000)
                    source_rate = [approximated.numerator, approximated.denominator]
                sr = _source_rate(source_rate)
                start = _frame_from_rt(part.source_range.start_time, sr, "media start")
                length = _frame_from_rt(part.source_range.duration, rate, "clip duration")
                if "source_start_frame" in meta and meta["source_start_frame"] != start:
                    raise ValueError("OTIO media trim no longer matches pinned metadata")
                if "duration_frames" in meta and meta["duration_frames"] != length:
                    raise ValueError("OTIO clip length no longer matches exact metadata")
                digest = meta.get("sha256")
                if digest is None and not allow_unpinned:
                    raise ValueError("Unpinned OTIO clip: provide explicit --allow-unpinned")
                if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
                    raise ValueError("Invalid OTIO clip source checksum")
                items.append({"type": "clip", "id": identity, "name": part.name or identity,
                              "source": {"uri": source_uri, "sha256": digest},
                              "source_fps": sr, "source_start_frame": start,
                              "duration_frames": length})
            elif isinstance(part, otio.schema.Gap):
                if not part.enabled or part.effects or part.markers:
                    raise ValueError("Effected OTIO gaps are unsupported")
                items.append({"type": "gap", "id": identity,
                              "duration_frames": _frame_from_rt(part.duration(), rate, "gap")})
            elif isinstance(part, otio.schema.Transition):
                if part.transition_type == otio.schema.TransitionTypes.SMPTE_Dissolve:
                    style = "dissolve"
                elif (part.transition_type == otio.schema.TransitionTypes.Custom and
                      meta.get("style") == "wipe"):
                    style = "wipe"
                else:
                    raise ValueError("Unsupported OTIO transition: only dissolve or tagged wipe")
                items.append({"type": "transition", "id": identity, "style": style,
                              "in_frames": _frame_from_rt(part.in_offset, rate, "transition in"),
                              "out_frames": _frame_from_rt(part.out_offset, rate, "transition out")})
            else:
                raise ValueError("Nested OTIO compositions and generators require flattening")
        tracks.append({"id": tid, "kind": kind, "items": items})
        for midx, marker in enumerate(track.markers):
            if not isinstance(marker, otio.schema.Marker):
                raise ValueError("Unknown OTIO marker schema")
            m = _metadata(marker)
            start = _frame_from_rt(marker.marked_range.start_time, rate, "marker position")
            length = _frame_from_rt(marker.marked_range.duration, rate, "marker length")
            markers.append({"id": _otio_identifier(m, f"{tid}_marker{midx + 1}"),
                            "name": marker.name, "frame": start, "duration_frames": length,
                            "color": str(marker.color).split(".")[-1].upper()})
    return validate({"schema": SCHEMA, "name": timeline.name or "Imported OTIO timeline",
                     "fps": rate, "tracks": tracks, "markers": markers},
                    allow_unpinned=allow_unpinned)


def _encoded(timeline):
    otio = _otio()
    return otio.adapters.write_to_string(timeline, adapter_name="otio_json")


def _decoded(text):
    otio = _otio()
    return otio.adapters.read_from_string(text, adapter_name="otio_json", media_linker_name=None)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _publish(target, payload, receipt):
    """Publish two new files without overwrite, rolling back partial publication."""
    target = Path(target)
    second = target.with_name(target.name + ".receipt.json")
    if (target.exists() or target.is_symlink() or second.exists() or second.is_symlink()):
        raise ValueError("Output or its receipt already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = []
    published = False
    try:
        for data in (payload, json.dumps(receipt, sort_keys=True, indent=2).encode('utf-8') + b'\n'):
            with tempfile.NamedTemporaryFile(mode='wb', prefix='.fr-m13-', dir=target.parent,
                                             delete=False) as handle:
                staged.append(Path(handle.name))
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        os.link(staged[0], target)
        published = True
        os.link(staged[1], second)
    except BaseException:
        if published:
            target.unlink(missing_ok=True)
        raise
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
    return {"output": str(target), "sha256": _sha(payload), "receipt": str(second)}


def export_otio(document, output, *, allow_unpinned=False, media_root=None):
    normal = validate(document, allow_unpinned=allow_unpinned)
    if media_root is not None:
        verify_sources(normal, media_root)
    if Path(output).suffix.lower() != '.otio':
        raise ValueError("M13 export requires a .otio output")
    payload = _encoded(to_otio(normal, allow_unpinned=allow_unpinned)).encode('utf-8')
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("Encoded OTIO exceeds 8 MiB")
    decoded = from_otio(_decoded(payload.decode('utf-8')), allow_unpinned=allow_unpinned)
    if decoded != normal:
        raise ValueError("OTIO roundtrip changed the exact edit decision list")
    return _publish(output, payload, {"schema": SCHEMA, "output_sha256": _sha(payload),
                                      "timeline": summary(normal), "native_roundtrip": "exact"})


def import_otio(input_path, output, *, fps=None, allow_unpinned=False):
    if Path(input_path).suffix.lower() != '.otio':
        raise ValueError("M13 only reads explicit .otio JSON, not third-party adapters")
    path = Path(input_path)
    if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Input OTIO must be a bounded local .otio file")
    source = path.read_bytes()
    normal = from_otio(_decoded(source.decode('utf-8')), fps=fps,
                       allow_unpinned=allow_unpinned)
    if Path(output).suffix.lower() != '.json':
        raise ValueError("Imported M13 timeline output must end with .json")
    data = json.dumps(normal, indent=2, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n'
    return _publish(output, data, {"schema": SCHEMA, "input_otio_sha256": _sha(source),
                                    "output_sha256": _sha(data), "timeline": summary(normal)})


def project_manifest(project):
    """Expose native pinned audio/cues; do not misrepresent procedural layers as media."""
    project.verify_inputs()
    per_cycle = project.slice_duration * project.fps
    if per_cycle.denominator != 1:
        raise ValueError("Audio repeat does not align to an encoded frame; refusing to quantise")
    count = int(per_cycle)
    if count < 1 or count * project.repeats != project.frame_count:
        raise ValueError("Native audio and output frame clocks disagree")
    uri = Path(project.audio_ref["path"]).resolve(strict=True)
    root = project.base.resolve(strict=True)
    if not uri.is_relative_to(root):
        raise ValueError("Portable OTIO audio must live inside the project folder")
    relative = uri.relative_to(root).as_posix()
    items = []
    for index in range(project.repeats):
        items.append({"type": "clip", "id": f"source_cycle_{index + 1}",
                      "name": f"Source audio cycle {index + 1}",
                      "source": {"uri": relative, "sha256": project.audio_ref["sha256"]},
                      "source_fps": [project.sample_rate, 1],
                      "source_start_frame": project.start_sample, "duration_frames": count})
    markers = []
    for index, event in enumerate(project.cues):
        if event["text"] and 0 <= event["frame"] <= project.frame_count:
            markers.append({"id": f"cue_{index + 1}", "name": event["text"],
                            "frame": event["frame"], "duration_frames": 0, "color": "YELLOW"})
    return validate({"schema": SCHEMA, "name": project.data.get("title") or "FrameRudiments audio/cues",
                     "fps": [project.fps, 1], "tracks": [{"id": "source_audio", "kind": "audio",
                       "items": items}], "markers": markers})


def manifest_file(path, *, allow_unpinned=False):
    return validate(_read_json(path), allow_unpinned=allow_unpinned)
