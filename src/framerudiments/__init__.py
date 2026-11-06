"""Offline frame composition on a traceable musical clock."""

__version__ = "0.4.24"

from .project import Project
from .render import render, preflight
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
