"""Inspect bounded native frame dependencies without rendering pixels.

Inspection uses the same compositor planning surface as native execution. It
does not start a virtual sampler, GPU backend or sequential external engine.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from .compositor import Compositor
from .project import Project

SCHEMA = 'framerudiments.temporal-inspection.v1'
MAX_INSPECT_FRAMES = 64
MAX_INSPECTION_BYTES = 8 * 1024 * 1024


def inspect_temporal(project_file, frames):
    """Return exact, ordered dependency plans for at most 64 encoded frames.

    The project and pinned files are verified before and after planning. Native
    motion may be sampled to resolve modulated shutter settings; no raster or
    external visualiser callback runs. Returned evidence contains identities,
    counts and clock coordinates, not source paths or private project text.
    """
    if (not isinstance(frames, (tuple, list)) or not 1 <= len(frames) <= MAX_INSPECT_FRAMES
            or any(type(frame) is not int or frame < 0 for frame in frames)):
        raise ValueError('Temporal inspection requires 1..64 nonnegative integer frames')
    frames = tuple(frames)
    path = Path(project_file)
    original = path.read_bytes()
    project = Project.load(path)
    if path.read_bytes() != original:
        raise ValueError('Project changed during temporal inspection')
    if any(frame >= project.frame_count for frame in frames):
        raise ValueError('Temporal inspection frame is outside the programme')
    project.verify_inputs()
    # Plans describe native sampling even when the authored graph requests GPU.
    # Merely inspecting those dependencies must not open an optional GL context.
    compositor = Compositor(project, _shared_effect_graph_gpu=None)
    try:
        plans = {}
        for frame in frames:
            if frame not in plans:
                plan = compositor.temporal_plan(frame)
                plans[frame] = {'frame': frame, 'digest': plan.digest, 'schedule': plan.document()}
                fx = compositor.temporal_fx_plan(frame)
                if fx is not None:
                    plans[frame]['temporal_fx'] = fx
                feedback = compositor.feedback_plan(frame)
                if feedback is not None:
                    plans[frame]['feedback'] = feedback
        result = {
            'schema': SCHEMA,
            'project_sha256': sha256(original).hexdigest(),
            'contract': compositor.temporal_contract(),
            'frames': [plans[frame] for frame in frames],
            'scope': {'pixels_rendered': False, 'sequential_external_engines': False,
                      'gpu_context_created': False, 'stage': 'pre_external_effects'},
        }
        feedback = compositor.feedback_contract()
        if feedback is not None:
            result['feedback'] = feedback
        fx = compositor.temporal_fx_contract()
        if fx is not None:
            result['temporal_fx'] = fx
        project.verify_inputs()
        if path.read_bytes() != original:
            raise ValueError('Project changed during temporal inspection')
        if len(json.dumps(result, allow_nan=False).encode('utf-8')) > MAX_INSPECTION_BYTES:
            raise ValueError('Temporal inspection exceeds its evidence byte limit')
        return result
    finally:
        compositor.close()


def write_inspection(output, document):
    """Create one bounded report; never replace an existing file or symlink."""
    path = Path(output)
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if len(text.encode('utf-8')) > MAX_INSPECTION_BYTES:
        raise ValueError('Temporal inspection exceeds its evidence byte limit')
    # The caller chooses an existing output parent. Exclusive creation protects
    # pinned inputs and another process's report without a check/write race.
    handle = path.open('x', encoding='utf-8')
    try:
        with handle:
            handle.write(text)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
