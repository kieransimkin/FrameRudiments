"""Native note view dispatch, separate from MIDI ownership and the compositor."""

from .note_arrangement import ArrangementView
from .note_percussion import PercussionView
from .note_pitch_wheel import PitchWheelView
from .note_fingering import FingeringView
from .note_score import ScoreView
from .note_impacts import NoteImpactsView

SETTINGS = {
    "arrangement": "arrangement",
    "drum-lanes": "drums",
    "pad-grid": "drums",
    "pitch-wheel": "wheel",
    "fretboard": "fretboard",
    "tablature": "fretboard",
    "score": "score",
    "note-impacts": "impacts",
}
LABELLED = frozenset(set(SETTINGS) - {"score", "note-impacts"})


def make_view(layer):
    if layer.view == "arrangement":
        return ArrangementView(layer, layer.cfg.get("arrangement", {}))
    if layer.view in ("drum-lanes", "pad-grid"):
        return PercussionView(layer, layer.cfg.get("drums", {}))
    if layer.view == "pitch-wheel":
        return PitchWheelView(layer, layer.cfg.get("wheel", {}))
    if layer.view in ("fretboard", "tablature"):
        return FingeringView(layer, layer.cfg.get("fretboard", {}))
    if layer.view == "score":
        return ScoreView(layer, layer.cfg.get("score", {}))
    if layer.view == "note-impacts":
        return NoteImpactsView(layer, layer.cfg.get("impacts", {}))
    return None
