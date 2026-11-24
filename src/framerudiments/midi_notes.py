"""MIDI note capture and SMF import; reuse the qualified owned RtMidi input."""

from collections import Counter, defaultdict, deque
from copy import deepcopy
from fractions import Fraction
from io import BytesIO
from pathlib import Path

from .io import integer, number, sha256
from .midi_protocol_m15 import MIDIInput
from .note_timeline import MAX_BYTES, MAX_NOTES, NoteTimeline, source_identity

MAX_EVENTS = MAX_NOTES * 4


def decode_note_message(message):
    if (
        not isinstance(message, (list, tuple))
        or len(message) != 3
        or any(type(v) is not int or not 0 <= v <= 255 for v in message)
        or any(v > 127 for v in message[1:])
    ):
        raise ValueError("Note capture requires complete three-byte MIDI messages")
    status, key, value = message
    channel = (status & 15) + 1
    if status >> 4 == 9:
        return ("on" if value else "off"), channel, key, value
    if status >> 4 == 8:
        return "off", channel, key, value
    if status >> 4 == 11 and key in (64, 120, 123):
        return {64: "sustain", 120: "silence", 123: "release_all"}[key], channel, key, value
    raise ValueError("Unsupported note message")


class NoteEvents:
    """Pair repeated pitches FIFO; distinguish key release from pedal sound."""

    def __init__(self, project):
        self.project = project
        self.notes = []
        self.active = defaultdict(deque)
        self.sustained = defaultdict(list)
        self.pedals = defaultdict(bool)
        self.statistics = Counter()
        self.last_seconds = 0.0

    def add(self, seconds, message, *, track=0):
        seconds = number(
            seconds, "Note event time", minimum=self.last_seconds, maximum=float(self.project.duration)
        )
        track = integer(track, "MIDI track", maximum=127)
        kind, channel, key, value = decode_note_message(message)
        if self.statistics["accepted_messages"] >= MAX_EVENTS:
            raise ValueError("Note message budget exceeded")
        self.last_seconds = seconds
        self.statistics["accepted_messages"] += 1
        if kind == "on":
            if len(self.notes) >= MAX_NOTES:
                raise ValueError("Note count budget exceeded")
            row = dict(
                key=key,
                channel=channel,
                velocity=value,
                track=track,
                start_seconds=seconds,
                release_seconds=None,
                end_seconds=None,
            )
            self.notes.append(row)
            self.active[(track, channel, key)].append(row)
        elif kind == "off":
            pending = self.active[(track, channel, key)]
            if pending:
                self._release(pending.popleft(), seconds)
            else:
                self.statistics["unmatched_note_off"] += 1
        elif kind == "sustain":
            self.pedals[channel] = value >= 64
            if not self.pedals[channel]:
                for row in self.sustained.pop(channel, []):
                    row["end_seconds"] = seconds
        else:
            for (_track, ch, _key), pending in self.active.items():
                if ch == channel:
                    while pending:
                        self._release(pending.popleft(), seconds)
            if kind == "silence":
                for row in self.sustained.pop(channel, []):
                    row["end_seconds"] = seconds

    def _release(self, row, seconds):
        row["release_seconds"] = seconds
        if self.pedals[row["channel"]]:
            self.sustained[row["channel"]].append(row)
        else:
            row["end_seconds"] = seconds

    def finish(self, *, provenance):
        end = float(self.project.duration)
        notes = deepcopy(self.notes)
        held = sustained = 0
        for row in notes:
            if row["release_seconds"] is None:
                row["release_seconds"] = end
                held += 1
            if row["end_seconds"] is None:
                row["end_seconds"] = end
                sustained += 1
        return NoteTimeline.from_notes(
            self.project,
            notes,
            provenance={
                **provenance,
                "pairing": "FIFO per track/channel/key; note-on velocity zero is note-off",
                "statistics": dict(self.statistics),
                "held_keys_closed_at_programme_end": held,
                "sounding_notes_clipped_at_programme_end": sustained,
            },
        )


class MIDINoteCapture:
    """Explicit finite capture sampled by an existing video transport owner."""

    def __init__(self, project, *, api, port_name):
        self.project = project
        self._source = source_identity(project)
        self.receiver = MIDIInput(api, port_name)
        self.events = NoteEvents(project)
        self._last_frame = -1
        self._failed = False
        self.statistics = Counter()

    def __enter__(self):
        try:
            self.project.verify_inputs()
            self.receiver.__enter__()
            return self
        except BaseException:
            self._failed = True
            self.close()
            raise

    def close(self):
        self.receiver.close()

    def __exit__(self, exc_type, *_exc):
        self._failed |= exc_type is not None
        self.close()

    def wait_for_input(self, timeout=1, *, minimum_messages=1):
        try:
            return self.receiver.wait_ready(timeout, minimum_messages=minimum_messages)
        except BaseException:
            self._failed = True
            self.close()
            raise

    def sample(self, frame):
        try:
            integer(frame, "MIDI note frame", maximum=self.project.frame_count - 1)
            if frame <= self._last_frame or self._last_frame < 0 and frame != 0:
                raise ValueError("MIDI note capture needs increasing frames starting at zero")
            messages = self.receiver.poll()
            for message in messages:
                try:
                    decode_note_message(message)
                except ValueError:
                    self.statistics["unsupported_messages"] += 1
                    continue
                self.events.add(frame / self.project.fps, message)
            self.statistics["received_messages"] += len(messages)
            self.statistics["sampled_frames"] += 1
            self._last_frame = frame
            return {"encoded_frame": frame, "statistics": dict(self.statistics)}
        except BaseException:
            self._failed = True
            self.close()
            raise

    def freeze(self):
        if (
            self._failed
            or not self.receiver.closed
            or self.receiver.native is not None
            or self.receiver.error_count
            or self._last_frame != self.project.frame_count - 1
        ):
            raise ValueError("Close a healthy note input after the final programme frame before freezing")
        self.project.verify_inputs()
        if source_identity(self.project) != self._source:
            raise ValueError("Programme source changed during note capture")
        return self.events.finish(
            provenance={
                "kind": "native_midi_capture",
                "capture_fps": self.project.fps,
                "time_owner": "existing encoded programme frames",
                "capture": {
                    **self.receiver.evidence,
                    "last_frame": self._last_frame,
                    "statistics": dict(self.statistics),
                },
            }
        )


def import_midi_notes(project, reference, *, offset_seconds):
    """Import SMF 0/1 through Mido; never open an output or native input port."""
    offset = number(offset_seconds, "Authored MIDI offset", minimum=-86400, maximum=86400)
    ref = project.ref(reference)
    if ref["bytes"] > MAX_BYTES:
        raise ValueError("MIDI file exceeds the bounded import budget")
    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("SMF import needs the optional framerudiments[notes] extra") from exc
    midi = mido.MidiFile(file=BytesIO(Path(ref["path"]).read_bytes()), clip=False)
    if midi.type not in (0, 1) or not 1 <= midi.ticks_per_beat <= 32767:
        raise ValueError("SMF import supports synchronous type 0/1 with positive PPQN only")
    if not 1 <= len(midi.tracks) <= 128:
        raise ValueError("MIDI track budget exceeded")
    events = []
    track_names = {}
    for track, messages in enumerate(midi.tracks):
        ticks = 0
        for index, message in enumerate(messages):
            if message.type == "track_name" and str(track) not in track_names:
                name = "".join(c if c.isprintable() else " " for c in message.name).strip()[:80]
                if name:
                    track_names[str(track)] = name
            ticks += integer(message.time, "MIDI delta ticks")
            events.append((ticks, track, index, message))
            if len(events) > MAX_EVENTS:
                raise ValueError("MIDI message budget exceeded")
    events.sort(key=lambda row: row[:3])
    builder = NoteEvents(project)
    tempo, previous, elapsed = 500000, 0, Fraction(0)
    tempo_at_tick = {}
    clipped = ignored = 0
    for tick, track, _index, message in events:
        elapsed += Fraction((tick - previous) * tempo, midi.ticks_per_beat * 1000000)
        previous = tick
        if message.type == "set_tempo":
            tempo = integer(message.tempo, "MIDI tempo", minimum=1, maximum=0xFFFFFF)
            if tick in tempo_at_tick and tempo_at_tick[tick] != tempo:
                raise ValueError("Conflicting simultaneous MIDI tempos")
            tempo_at_tick[tick] = tempo
        seconds = float(elapsed) + offset
        if seconds > float(project.duration):
            clipped += 1
            continue
        if message.type not in ("note_on", "note_off", "control_change"):
            ignored += 1
            continue
        raw = message.bytes()
        try:
            decode_note_message(raw)
        except ValueError:
            ignored += 1
            continue
        if seconds < 0:
            clipped += 1
        builder.add(max(0.0, seconds), raw, track=track)
    if sha256(ref["path"]) != ref["sha256"]:
        raise ValueError("MIDI input changed during import")
    project.verify_inputs()
    return builder.finish(
        provenance={
            "kind": "standard_midi_file",
            "smf_sha256": ref["sha256"],
            "parser": "mido " + mido.version.__version__,
            "smf_type": midi.type,
            "track_names": track_names,
            "ticks_per_quarter": midi.ticks_per_beat,
            "offset_seconds": offset,
            "alignment": "explicit authored offset; not inferred from audio",
            "tempo_changes": len(tempo_at_tick),
            "clipped_events": clipped,
            "ignored_events": ignored,
        }
    )
