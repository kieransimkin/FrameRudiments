"""Offline frame composition on a traceable musical clock."""

__version__ = "0.4.4"

from .project import Project
from .render import render, preflight
from .natural_materials import NaturalMaterial
from .fire_renderer import FireRenderer

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
]
