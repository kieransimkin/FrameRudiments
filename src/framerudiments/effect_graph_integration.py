"""Compositor-to-effect-graph integration and source isolation.

The graph's source IDs refer to *isolated* surfaces. An absent-but-valid
source (for example, an inactive lyric-art or empty sprite layer) is transparent;
invalid names fail before frame rendering. Never mutate provider images.
"""
from __future__ import annotations

from collections.abc import Mapping

from PIL import Image


def validate_graph_sources(graph, available_elements):
    """Fail at compositor construction, before expensive frame evaluation."""
    if graph is None:
        return
    unknown = graph.source_ids - (set(available_elements) | {'frame'})
    if unknown:
        raise ValueError(f"Unknown effect graph source: {', '.join(sorted(unknown))}")


def evaluate_effect_graph(graph, controls, context, frame, surfaces: Mapping[str, Image.Image]):
    """Resolve a frame without transferring ownership of any input surface."""
    if graph is None:
        return frame
    if frame.mode != 'RGBA':
        raise ValueError('Effect graph requires a composited RGBA frame')
    sources = {'frame': frame, **surfaces}
    for source_id in graph.source_ids:
        # Valid source IDs may legitimately have no ink on this frame. Keep
        # the source identity and its transparent alpha rather than raising.
        sources.setdefault(source_id, Image.new('RGBA', frame.size))
    values = controls.values(context, graph) if controls is not None else {}
    return graph.render(sources, parameter_values=values)
