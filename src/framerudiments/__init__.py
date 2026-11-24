"""Offline frame composition on a traceable musical clock."""

__version__ = "0.4.39"

from .project import Project
from .render import render, preflight
from .compositor import Compositor
from .layer_frame_cache import LayerFrameCache
from .note_timeline import NoteTimeline
from .midi_notes import MIDINoteCapture, import_midi_notes
from .natural_materials import NaturalMaterial
from .fire_renderer import FireRenderer
from .fractals import (
    ESCAPE_FRACTAL_FAMILIES,
    FRACTAL_FAMILIES,
    escape_time_field,
    julia_constant,
    julia_escape_field,
    newton_fractal_field,
    normalized_turn_radians,
)

from .rudiment_renderers import (
    RendererRegistry,
    RudimentRenderer,
    RudimentFrame,
    RudimentSample,
    default_renderers,
)

__all__ = [
    "Project",
    "Compositor",
    "LayerFrameCache",
    "NoteTimeline",
    "MIDINoteCapture",
    "import_midi_notes",
    "render",
    "preflight",
    "__version__",
    "RendererRegistry",
    "RudimentRenderer",
    "RudimentFrame",
    "RudimentSample",
    "default_renderers",
    "NaturalMaterial",
    "FireRenderer",
    "julia_escape_field",
    "julia_constant",
    "escape_time_field",
    "newton_fractal_field",
    "ESCAPE_FRACTAL_FAMILIES",
    "FRACTAL_FAMILIES",
    "normalized_turn_radians",
]
