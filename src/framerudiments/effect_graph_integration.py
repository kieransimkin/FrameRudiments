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


def create_gpu_backend(mode='cpu', *, report=None):
    """Explicit opt-in, with optional retained context/fallback diagnostics.

    Existing callers need not pass a report. Discovery itself never calls this
    factory; only callers explicitly asking to open a GPU backend do so.
    """
    if report is not None:
        if not isinstance(report, dict):
            raise ValueError('GPU initialization report must be a dictionary')
        report.clear()
    if mode not in {'cpu', 'gpu', 'auto'}:
        raise ValueError('effect_graph_backend must be cpu, gpu or auto')
    if report is not None:
        report.update(mode=mode, state='not_attempted', fallback_reason=None)
    if mode == 'cpu':
        if report is not None:
            report['state'] = 'cpu_requested'
        return None
    from .effect_graph_gpu import GLMatteBackend
    try:
        backend = GLMatteBackend()
    except (RuntimeError, ImportError) as exc:
        if report is not None:
            report.update(state='unavailable', fallback_reason=str(exc), error_type=type(exc).__name__)
        if mode == 'gpu':
            raise
        return None
    if report is not None:
        report['state'] = 'attached'
    return backend


def evaluate_effect_graph(graph, controls, context, frame, surfaces: Mapping[str, Image.Image],
                          *, gpu_backend=None, gpu_strict=False):
    """Resolve a frame without transferring ownership of any input surface."""
    if graph is None:
        return frame
    graph._style_render_report = {}
    if frame.mode != 'RGBA':
        raise ValueError('Effect graph requires a composited RGBA frame')
    sources = {'frame': frame, **surfaces}
    for source_id in graph.source_ids:
        # Valid source IDs may legitimately have no ink on this frame. Keep
        # the source identity and its transparent alpha rather than raising.
        if source_id not in sources:
            sources[source_id] = Image.new('RGBA', frame.size)
    values = controls.values(context, graph) if controls is not None else {}
    if getattr(graph, 'layer_style_ids', ()):
        report = {}
        result = graph.render(sources, parameter_values=values, context=context,
                              gpu_backend=gpu_backend, gpu_strict=gpu_strict, style_report=report)
        graph._style_render_report = report
        return result
    return graph.render(sources, parameter_values=values, context=context,
                        gpu_backend=gpu_backend, gpu_strict=gpu_strict)


def compositor_style_report(compositor):
    """Last evaluated style sample, including the high-fps sampling compositor.

    This accessor never renders, unions shutter samples or asserts a whole
    project succeeded. At temporal sample counts >1, graph-local bounds belong
    to the last completed subframe, whose original clock values are retained.
    """
    owner = compositor
    quality = getattr(compositor, 'output_quality', None)
    virtual = getattr(compositor, '_temporal_virtual', None)
    if virtual is not None and getattr(quality, 'time_samples', 1) > 1:
        owner = virtual
    graph = getattr(owner, 'effect_graph', None)
    report = graph.style_render_report() if graph is not None else {}
    if report:
        report['sampling'] = {
            'owner': 'temporal_virtual' if owner is not compositor else 'compositor',
            'sample_fps': getattr(getattr(owner, 'project', None), 'fps', None),
            'encoded_fps': getattr(getattr(compositor, 'project', None), 'fps', None),
            'complete_shutter_bounds': False,
        }
    return report
