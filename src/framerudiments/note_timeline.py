"""Pinned finite note intervals on the existing continuous output clock."""

from bisect import bisect_left, bisect_right
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile

from .io import fields, integer, number, read_json, sha256

SCHEMA = "framerudiments.note-timeline.v1"
MAX_NOTES = 65536
MAX_BYTES = 8 * 1024 * 1024


def source_identity(project):
    # Seconds survive native shutter sampling, viewport profiles and staging.
    # Changing typography, effects or another layer does not change the take.
    return {
        "audio_sha256": project.audio_ref["sha256"],
        "sample_rate": project.sample_rate,
        "start_sample": project.start_sample,
        "end_sample_exclusive": project.end_sample,
        "repeats": project.repeats,
    }


@dataclass(frozen=True)
class Note:
    key: int
    channel: int
    velocity: int
    track: int
    start_seconds: float
    release_seconds: float
    end_seconds: float


class NoteTimeline:
    def __init__(self, project, document):
        fields(document, {"schema", "source", "notes", "provenance"}, "note timeline")
        source = document.get("source")
        expected = source_identity(project)
        if (
            document.get("schema") != SCHEMA
            or source != expected
            or any(type(source[k]) is not type(v) for k, v in expected.items())
        ):
            raise ValueError("Note timeline schema or programme source identity differs")
        rows = document.get("notes")
        if not isinstance(rows, list) or len(rows) > MAX_NOTES:
            raise ValueError("Note timeline needs a bounded note list")
        if not isinstance(document.get("provenance"), dict):
            raise ValueError("Note timeline needs explicit provenance")
        if len(json.dumps(document, allow_nan=False).encode("utf-8")) > MAX_BYTES:
            raise ValueError("Note timeline exceeds its byte budget")
        duration = float(project.duration)
        admitted = []
        allowed = {"key", "channel", "velocity", "track", "start_seconds", "release_seconds", "end_seconds"}
        for row in rows:
            fields(row, allowed, "note interval")
            if set(row) != allowed:
                raise ValueError("Note interval fields are incomplete")
            key = integer(row["key"], "MIDI key", maximum=127)
            channel = integer(row["channel"], "MIDI channel", minimum=1, maximum=16)
            velocity = integer(row["velocity"], "MIDI velocity", minimum=1, maximum=127)
            track = integer(row["track"], "MIDI track", maximum=127)
            start = number(row["start_seconds"], "Note start", minimum=0, maximum=duration)
            release = number(row["release_seconds"], "Note release", minimum=start, maximum=duration)
            end = number(row["end_seconds"], "Note sounding end", minimum=release, maximum=duration)
            admitted.append(Note(key, channel, velocity, track, start, release, end))
        self.notes = tuple(sorted(admitted, key=lambda n: (n.start_seconds, n.track, n.channel, n.key)))
        self.starts = tuple(n.start_seconds for n in self.notes)
        self._data = deepcopy(document)
        self._data["notes"] = [asdict(n) for n in self.notes]
        self._size = 1
        while self._size < len(self.notes):
            self._size *= 2
        self._ends = [float("-inf")] * (2 * self._size)
        for i, note in enumerate(self.notes):
            self._ends[self._size + i] = note.end_seconds
        for i in range(self._size - 1, 0, -1):
            self._ends[i] = max(self._ends[2 * i], self._ends[2 * i + 1])
        self.evidence = {
            "schema": SCHEMA,
            "notes": len(self.notes),
            "channels": sorted({n.channel for n in self.notes}),
            "tracks": sorted({n.track for n in self.notes}),
            "clock": "continuous output seconds; no device or playback clock",
            "intervals": "half-open; release marks key-up, end includes sustain",
            "source": source_identity(project),
        }

    @property
    def data(self):
        return deepcopy(self._data)

    @classmethod
    def from_notes(cls, project, notes, *, provenance):
        rows = []
        for row in notes:
            item = dict(row)
            item.setdefault("channel", 1)
            item.setdefault("velocity", 96)
            item.setdefault("track", 0)
            item.setdefault("release_seconds", item.get("end_seconds"))
            rows.append(item)
        return cls(
            project,
            {"schema": SCHEMA, "source": source_identity(project), "notes": rows, "provenance": provenance},
        )

    @classmethod
    def load(cls, project, reference):
        ref = project.ref(reference)
        if ref["bytes"] > MAX_BYTES:
            raise ValueError("Note timeline exceeds its byte budget")
        result = cls(project, read_json(ref["path"]))
        if sha256(ref["path"]) != ref["sha256"]:
            raise ValueError("Note timeline changed during admission")
        result.evidence = {**result.evidence, "sha256": ref["sha256"]}
        return result

    def window(self, low, high):
        """Return overlapping immutable intervals in O(log N + visible notes)."""
        low = number(low, "Note window start")
        high = number(high, "Note window end", minimum=low)
        stop = bisect_right(self.starts, high)
        stack, result = [(1, 0, self._size)], []
        while stack:
            node, left, right = stack.pop()
            if left >= stop or self._ends[node] <= low:
                continue
            if right - left == 1:
                if left < len(self.notes):
                    result.append(self.notes[left])
            else:
                mid = (left + right) // 2
                stack.extend(((node * 2 + 1, mid, right), (node * 2, left, mid)))
        return tuple(result)

    def onsets(self, low, high):
        """Return note-on instants in the closed query window, including zero-length hits."""
        low = number(low, "Note onset window start")
        high = number(high, "Note onset window end", minimum=low)
        return self.notes[bisect_left(self.starts, low) : bisect_right(self.starts, high)]

    def active(self, seconds):
        return tuple(
            n for n in self.window(seconds, seconds) if n.start_seconds <= seconds < n.release_seconds
        )

    def write(self, path):
        """Publish complete bytes atomically without replacing an existing take."""
        path = Path(path)
        data = (json.dumps(self.data, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ValueError("Note timeline exceeds its byte budget")
        fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)  # Atomic exclusive admission; never overwrite.
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"path": str(path.resolve()), "sha256": sha256(path)}
