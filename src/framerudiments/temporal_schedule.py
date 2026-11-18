"""Bounded, exact-frame temporal declarations and deterministic task schedules.

Coordinates are rational positions on the encoded-frame ruler. A native sample
at virtual frame ``i`` and sampling factor ``s`` is therefore ``Fraction(i, s)``;
the owner still supplies the authoritative source clock and raster operation.
These plans describe dependency order, not source/pixel identity or checkpoints.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
import re

SCHEMA = 'framerudiments.temporal-schedule.v1'
MAX_TASKS = 4096
MAX_EDGES = 65536
MAX_LIVE_RESULTS = 256
MAX_OUTPUTS = 4096
MAX_OFFSETS = 256
MAX_FRAME = 1_000_000_000
MAX_REACH = 4096
MAX_DENOMINATOR = 1_000_000_000
MAX_INTEGER = 2**53 - 1
_STAGE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


def _stage(value):
    if not isinstance(value, str) or not _STAGE.fullmatch(value):
        raise ValueError('temporal stage must be a bounded identifier')
    return value


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in {minimum}..{maximum}')
    return value


def _rational(value, name, *, signed=False, maximum=MAX_FRAME):
    # Floats are deliberately refused: a binary approximation cannot silently
    # become a new frame grid. Owners can provide their exact integer ratio.
    if type(value) not in (int, Fraction):
        raise ValueError(f'{name} must be an integer or exact Fraction')
    value = Fraction(value)
    if (abs(value.numerator) > MAX_INTEGER or value.denominator > MAX_DENOMINATOR
            or abs(value) > maximum or not signed and value < 0):
        raise ValueError(f'{name} exceeds exact temporal coordinate bounds')
    return value


def _ratio(value):
    return {'numerator': value.numerator, 'denominator': value.denominator}


def _ceil(value):
    return -(-value.numerator // value.denominator)


@dataclass(frozen=True)
class FrameRef:
    """One stage at an absolute exact position on the encoded-frame ruler."""

    stage: str
    frame: Fraction

    def __post_init__(self):
        _stage(self.stage)
        object.__setattr__(self, 'frame', _rational(self.frame, 'frame'))

    def document(self):
        return {'stage': self.stage, 'frame': _ratio(self.frame)}


@dataclass(frozen=True)
class TemporalDependency:
    """Ordered signed source taps, with explicit programme/repeat boundaries.

    Resolve against a caller-owned source lattice anchored at zero. ``last_frame``
    is its inclusive final coordinate, not a rounded programme duration. Repeated
    inputs remain repeated ports; a schedule evaluates their source only once.
    """

    source_stage: str
    offsets: tuple[Fraction, ...]
    boundary: str = 'error'
    repeat_local: bool = False

    def __post_init__(self):
        _stage(self.source_stage)
        if (not isinstance(self.offsets, tuple) or not 1 <= len(self.offsets) <= MAX_OFFSETS):
            raise ValueError('temporal offsets must be a tuple of 1..256 exact taps')
        object.__setattr__(self, 'offsets', tuple(
            _rational(value, 'temporal offset', signed=True, maximum=MAX_REACH)
            for value in self.offsets))
        if not isinstance(self.boundary, str) or self.boundary not in {'error', 'drop', 'clamp'}:
            raise ValueError('temporal boundary must be error, drop or clamp')
        if type(self.repeat_local) is not bool:
            raise ValueError('repeat_local must be boolean')

    @property
    def past(self):
        return max(Fraction(0), -min(self.offsets))

    @property
    def future(self):
        return max(Fraction(0), max(self.offsets))

    def document(self):
        return {'source_stage': self.source_stage, 'offsets': [_ratio(v) for v in self.offsets],
                'past': _ratio(self.past), 'future': _ratio(self.future),
                'boundary': self.boundary, 'repeat_local': self.repeat_local}

    def resolve(self, frame, *, last_frame, sample_step=1, repeat_span=None):
        frame = _rational(frame, 'frame')
        last = _rational(last_frame, 'last frame')
        step = _rational(sample_step, 'sample step')
        if step <= 0 or (last / step).denominator != 1:
            raise ValueError('temporal domain requires a positive aligned sample lattice')
        if frame > last:
            raise ValueError('temporal anchor is outside the source programme')
        low, high = Fraction(0), last
        if repeat_span is not None:
            span = _rational(repeat_span, 'repeat span')
            if span <= 0:
                raise ValueError('repeat span must be positive')
        elif self.repeat_local:
            raise ValueError('repeat-local dependencies require an exact repeat span')
        if self.repeat_local:
            cycle = frame // span
            # A nonintegral repeat boundary belongs to neither a preceding nor
            # following rounded frame: admit only actual lattice points within
            # the exact half-open cycle interval.
            low = max(low, _ceil(cycle * span / step) * step)
            high = min(high, (_ceil((cycle + 1) * span / step) - 1) * step)
            if low > high:
                raise ValueError('repeat interval contains no source samples')
        result = []
        for offset in self.offsets:
            requested = frame + offset
            if (requested / step).denominator != 1:
                raise ValueError('temporal dependency is not on the exact source sample lattice')
            if requested < low or requested > high:
                if self.boundary == 'drop':
                    continue
                if self.boundary == 'error':
                    raise ValueError('temporal dependency is outside its declared boundary')
                requested = min(high, max(low, requested))
            result.append(FrameRef(self.source_stage, requested))
        return tuple(result)


@dataclass(frozen=True)
class FrameTask:
    """A source or a consumer with ordered, possibly repeated input ports."""

    ref: FrameRef
    inputs: tuple[FrameRef, ...] = ()

    def __post_init__(self):
        if not isinstance(self.ref, FrameRef):
            raise ValueError('temporal task requires a FrameRef')
        if (not isinstance(self.inputs, tuple) or len(self.inputs) > MAX_EDGES
                or any(not isinstance(value, FrameRef) for value in self.inputs)):
            raise ValueError('temporal task inputs must be bounded immutable FrameRefs')

    def document(self):
        return {'ref': self.ref.document(), 'inputs': [value.document() for value in self.inputs]}


@dataclass(frozen=True)
class FrameSchedule:
    """A canonical dependency-first plan and its logical-result liveness bound.

    The live bound includes a new callback result before releasing its inputs.
    Callback-private scratch allocations, retained aliases and pixel dimensions
    remain the renderer's separate budget. There is no persistent cache here.
    """

    tasks: tuple[FrameTask, ...]
    outputs: tuple[FrameRef, ...]
    peak_live_results: int
    max_tasks: int
    max_edges: int
    max_live_results: int

    @property
    def source_refs(self):
        return tuple(sorted((task.ref for task in self.tasks if not task.inputs), key=_sort_key))

    @property
    def edge_count(self):
        return sum(len(task.inputs) for task in self.tasks)

    @property
    def digest(self):
        return sha256(json.dumps(self.document(), sort_keys=True, separators=(',', ':'),
                                 allow_nan=False).encode('utf-8')).hexdigest()

    def document(self):
        return {'schema': SCHEMA, 'coordinate': 'exact_encoded_frame_position',
                'scope': 'dependency_schedule_not_pixel_or_cache_identity',
                'tasks': [task.document() for task in self.tasks],
                'outputs': [ref.document() for ref in self.outputs],
                'source_refs': [ref.document() for ref in self.source_refs],
                'task_count': len(self.tasks), 'edge_count': self.edge_count,
                'peak_live_results': self.peak_live_results,
                'limits': {'tasks': self.max_tasks, 'edges': self.max_edges,
                           'live_results': self.max_live_results}}


def _sort_key(ref):
    return ref.frame, ref.stage


def _postorder(by_ref, roots):
    """Iterative DFS avoids Python recursion limits on an admitted deep graph."""
    state, order = {}, []
    for root in roots:
        if state.get(root) == 2:
            continue
        state[root] = 1
        stack = [(root, 0)]
        while stack:
            ref, index = stack[-1]
            inputs = by_ref[ref].inputs
            if index == len(inputs):
                stack.pop()
                state[ref] = 2
                order.append(ref)
                continue
            dependency = inputs[index]
            stack[-1] = ref, index + 1
            status = state.get(dependency)
            if status == 1:
                raise ValueError('temporal dependency cycle requires a separate feedback engine')
            if status != 2:
                state[dependency] = 1
                stack.append((dependency, 0))
    return order


def _uses(tasks):
    return Counter(ref for task in tasks for ref in task.inputs)


def build_schedule(tasks, outputs, *, max_tasks=MAX_TASKS, max_edges=MAX_EDGES,
                   max_live_results=MAX_LIVE_RESULTS):
    """Validate all declarations, prune unused work and deterministically order it.

    Task declaration order and requested-output order do not affect execution.
    Output *return* order remains significant and is included in the digest.
    Ordered consumer ports define traversal; shared source/consumer tasks run once.
    Every count, reference, cycle and liveness check completes before execution.
    """
    max_tasks = _integer(max_tasks, 'task limit', 1, MAX_TASKS)
    max_edges = _integer(max_edges, 'edge limit', 0, MAX_EDGES)
    max_live_results = _integer(max_live_results, 'live result limit', 1, MAX_LIVE_RESULTS)
    by_ref, edge_count = {}, 0
    for task in tasks:
        if not isinstance(task, FrameTask):
            raise ValueError('schedule declarations must be FrameTasks')
        if task.ref in by_ref:
            raise ValueError('duplicate temporal task declaration')
        if len(by_ref) >= max_tasks:
            raise ValueError('temporal task budget exceeded')
        edge_count += len(task.inputs)
        if edge_count > max_edges:
            raise ValueError('temporal edge budget exceeded')
        by_ref[task.ref] = task
    if not by_ref:
        raise ValueError('temporal schedule requires at least one task')
    requested = []
    for ref in outputs:
        if len(requested) >= MAX_OUTPUTS:
            raise ValueError('temporal output budget exceeded')
        if not isinstance(ref, FrameRef) or ref not in by_ref:
            raise ValueError('unknown temporal output')
        requested.append(ref)
    if not requested:
        raise ValueError('temporal schedule requires an output')
    if any(ref not in by_ref for task in by_ref.values() for ref in task.inputs):
        raise ValueError('unknown temporal input')
    # A bad declaration must not be hidden by selecting an unrelated output.
    _postorder(by_ref, sorted(by_ref, key=_sort_key))
    ordered = tuple(by_ref[ref] for ref in _postorder(by_ref, sorted(set(requested), key=_sort_key)))
    remaining, kept = _uses(ordered), set(requested)
    live, peak = set(), 0
    for task in ordered:
        live.add(task.ref)
        peak = max(peak, len(live))
        if peak > max_live_results:
            raise ValueError('temporal live-result budget exceeded')
        for ref in task.inputs:
            remaining[ref] -= 1
            if remaining[ref] == 0 and ref not in kept:
                live.remove(ref)
    return FrameSchedule(ordered, tuple(requested), peak, max_tasks, max_edges, max_live_results)


def execute_schedule(schedule, evaluate):
    """Evaluate admitted work once, preserving ordered inputs and output order.

    Evaluators must treat input values as immutable; renderer-specific copies
    and internal work budgets remain their responsibility. On failure no partial
    result is returned and all scheduler-owned references are released.
    """
    if not isinstance(schedule, FrameSchedule) or not callable(evaluate):
        raise ValueError('an immutable FrameSchedule and callable evaluator are required')
    # Public dataclasses can be constructed directly. Recheck their full contract
    # before the first renderer callback instead of trusting a forged receipt.
    checked = build_schedule(schedule.tasks, schedule.outputs, max_tasks=schedule.max_tasks,
                             max_edges=schedule.max_edges, max_live_results=schedule.max_live_results)
    if checked != schedule:
        raise ValueError('temporal schedule is not its canonical admitted plan')
    remaining, kept, values = _uses(schedule.tasks), set(schedule.outputs), {}
    inputs = value = None
    try:
        for task in schedule.tasks:
            inputs = tuple(values[ref] for ref in task.inputs)
            value = evaluate(task.ref, inputs)
            values[task.ref] = value
            del inputs, value
            for ref in task.inputs:
                remaining[ref] -= 1
                if remaining[ref] == 0 and ref not in kept:
                    del values[ref]
        return tuple(values[ref] for ref in schedule.outputs)
    finally:
        # A retained exception traceback also retains this frame's locals.
        # Drop the materialized input tuple even when the callback never returns.
        # References in the callback's own traceback/closures belong to its owner.
        inputs = value = None
        del inputs, value
        values.clear()
