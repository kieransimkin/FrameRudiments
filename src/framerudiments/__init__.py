"""Offline frame composition on a traceable musical clock."""
__version__ = "0.1.1"

from .project import Project
from .render import render, preflight

__all__ = ["Project", "render", "preflight", "__version__"]
