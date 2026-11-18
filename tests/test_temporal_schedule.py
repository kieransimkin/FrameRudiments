"""Exact temporal declarations and scheduler contracts, independent of raster code."""
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import gc
import json
import weakref

import pytest

from framerudiments.temporal_schedule import (
    MAX_DENOMINATOR, MAX_EDGES, MAX_FRAME, MAX_INTEGER, MAX_OFFSETS, MAX_OUTPUTS,
    MAX_REACH, MAX_TASKS, FrameRef, FrameTask, TemporalDependency,
    build_schedule, execute_schedule,
)


def source(frame, stage='native'):
    return FrameTask(FrameRef(stage, frame))


def test_exact_past_future_taps_keep_fractional_addresses_and_duplicate_ports():
    declaration = TemporalDependency('native', (-2, 0, Fraction(3, 4), 0))
    assert declaration.past == 2 and declaration.future == Fraction(3, 4)
    assert declaration.resolve(3, last_frame=10, sample_step=Fraction(1, 4)) == (
        FrameRef('native', 1), FrameRef('native', 3),
        FrameRef('native', Fraction(15, 4)), FrameRef('native', 3),
    )
    assert json.loads(json.dumps(declaration.document()))['offsets'] == [
        {'numerator': -2, 'denominator': 1}, {'numerator': 0, 'denominator': 1},
        {'numerator': 3, 'denominator': 4}, {'numerator': 0, 'denominator': 1},
    ]
    assert type(FrameRef('native', 2).frame) is Fraction


@pytest.mark.parametrize('policy,expected', [
    ('drop', (0, 2)), ('clamp', (0, 0, 2, 3)),
])
def test_programme_boundaries_are_explicit_and_preserve_port_order(policy, expected):
    declaration = TemporalDependency('native', (-2, 0, 2, 5), boundary=policy)
    assert tuple(ref.frame for ref in declaration.resolve(0, last_frame=3)) == expected


def test_boundary_error_does_not_retime_or_invent_missing_frames():
    with pytest.raises(ValueError, match='outside its declared boundary'):
        TemporalDependency('native', (-1, 0)).resolve(0, last_frame=3)
    with pytest.raises(ValueError, match='outside the source programme'):
        TemporalDependency('native', (0,), boundary='clamp').resolve(4, last_frame=3)


def test_exact_repeat_identity_excludes_complete_earlier_cycles():
    declaration = TemporalDependency('native', (-8, -6, -4, -2, 0), boundary='drop', repeat_local=True)
    # Source positions for frames 0, 3 and 6 would all repeat. They are distinct
    # cycles, so frame 6 may read only its own cycle's actual lattice points.
    assert declaration.resolve(6, last_frame=11, repeat_span=3) == (FrameRef('native', 6),)
    assert declaration.resolve(8, last_frame=11, repeat_span=3) == (
        FrameRef('native', 6), FrameRef('native', 8),
    )


@pytest.mark.parametrize('policy,expected', [
    ('drop', (3, 4)), ('clamp', (3, 3, 4, 4)),
])
def test_nonintegral_repeat_boundaries_use_exact_half_open_intervals(policy, expected):
    # Cycle 1 is [2.5, 5). Its integer source lattice contains only 3 and 4.
    declaration = TemporalDependency('native', (-2, 0, 1, 2), boundary=policy, repeat_local=True)
    assert tuple(ref.frame for ref in declaration.resolve(3, last_frame=7, repeat_span=Fraction(5, 2))) == expected
    # Exact boundary 5 begins cycle 2 rather than retaining any earlier sample.
    assert TemporalDependency('native', (-1, 0), boundary='drop', repeat_local=True).resolve(
        5, last_frame=7, repeat_span=Fraction(5, 2)) == (FrameRef('native', 5),)


def test_repeat_clamp_stays_on_fractional_sample_lattice():
    declaration = TemporalDependency('native', (-2, 0, Fraction(1, 2), 2), boundary='clamp', repeat_local=True)
    actual = declaration.resolve(3, last_frame=Fraction(23, 4), sample_step=Fraction(1, 4),
                                 repeat_span=Fraction(11, 4))
    assert tuple(ref.frame for ref in actual) == (Fraction(11, 4), 3, Fraction(7, 2), 5)
    assert all((ref.frame / Fraction(1, 4)).denominator == 1 for ref in actual)


@pytest.mark.parametrize('kwargs', [
    {'last_frame': 3, 'sample_step': 0},
    {'last_frame': Fraction(7, 2)},
    {'last_frame': 3, 'sample_step': 0.5},
    {'last_frame': 3, 'repeat_span': 0},
    {'last_frame': 3, 'repeat_span': 1.5},
])
def test_invalid_or_inexact_sample_domains_fail_explicitly(kwargs):
    with pytest.raises(ValueError):
        TemporalDependency('native', (0,)).resolve(1, **kwargs)


def test_off_grid_taps_and_missing_repeat_domain_are_rejected():
    with pytest.raises(ValueError, match='exact source sample lattice'):
        TemporalDependency('native', (Fraction(1, 3),)).resolve(1, last_frame=4, sample_step=Fraction(1, 4))
    with pytest.raises(ValueError, match='exact repeat span'):
        TemporalDependency('native', (0,), repeat_local=True).resolve(1, last_frame=4)


@pytest.mark.parametrize('value', [True, False, 0.0, float('nan'), float('inf'), '2', None,
                                   -1, MAX_FRAME + 1, Fraction(1, MAX_DENOMINATOR + 1),
                                   Fraction(MAX_INTEGER + 1, MAX_DENOMINATOR - 1)])
def test_frame_addresses_refuse_inexact_invalid_or_excessive_values(value):
    with pytest.raises(ValueError):
        FrameRef('native', value)


@pytest.mark.parametrize('value', ['', 'bad name', '../native', '\n', 'n' * 129, None, True])
def test_stage_identifiers_are_bounded_and_portable(value):
    with pytest.raises(ValueError):
        FrameRef(value, 0)


@pytest.mark.parametrize('offsets', [[], (), [0], (True,), (0.5,), (MAX_REACH + 1,),
                                     (-MAX_REACH - 1,), tuple(range(MAX_OFFSETS + 1))])
def test_dependency_declarations_reject_mutable_inexact_and_excessive_taps(offsets):
    with pytest.raises(ValueError):
        TemporalDependency('native', offsets)


@pytest.mark.parametrize('kwargs', [{'boundary': 'wrap'}, {'boundary': []}, {'repeat_local': 1}])
def test_unknown_boundary_or_repeat_policy_is_not_silently_accepted(kwargs):
    with pytest.raises(ValueError):
        TemporalDependency('native', (0,), **kwargs)


def fork_tasks():
    a, b = source(2), source(4)
    left = FrameTask(FrameRef('left', 3), (a.ref, b.ref, a.ref))
    right = FrameTask(FrameRef('right', 3), (a.ref,))
    output = FrameTask(FrameRef('output', 3), (left.ref, right.ref))
    return (a, b, left, right, output), output.ref


def test_canonical_schedule_deduplicates_shared_work_but_preserves_weighted_ports():
    tasks, output = fork_tasks()
    plan = build_schedule(tasks, (output,))
    permuted = build_schedule((tasks[4], tasks[1], tasks[3], tasks[0], tasks[2]), (output,))
    assert plan == permuted and plan.digest == permuted.digest
    assert plan.source_refs == (tasks[0].ref, tasks[1].ref)
    assert tuple(task.ref.stage for task in plan.tasks) == ('native', 'native', 'left', 'right', 'output')
    calls = []

    def evaluate(ref, values):
        calls.append((ref, values))
        return ref.frame if ref.stage == 'native' else sum(values)

    assert execute_schedule(plan, evaluate) == (10,)
    assert len(calls) == 5 and calls[2][1] == (2, 4, 2)
    assert plan.edge_count == 6 and plan.peak_live_results == 3


def test_output_order_is_preserved_without_changing_canonical_execution():
    a, b = source(3), source(1)
    left = build_schedule((a, b), (a.ref, b.ref, a.ref))
    right = build_schedule((b, a), (b.ref, a.ref))
    assert left.tasks == right.tasks
    assert left.digest != right.digest  # The requested return sequence changed.
    calls = []
    assert execute_schedule(left, lambda ref, inputs: calls.append(ref) or ref.frame) == (3, 1, 3)
    assert calls == [b.ref, a.ref]


def test_unused_valid_work_is_pruned_but_invalid_unused_work_cannot_hide():
    a, unused = source(0), source(10)
    assert build_schedule((unused, a), (a.ref,)).tasks == (a,)
    bad = FrameTask(FrameRef('bad', 0), (FrameRef('missing', 0),))
    with pytest.raises(ValueError, match='unknown temporal input'):
        build_schedule((a, bad), (a.ref,))
    cycle = FrameTask(FrameRef('cycle', 0), (FrameRef('cycle', 0),))
    with pytest.raises(ValueError, match='feedback engine'):
        build_schedule((a, cycle), (a.ref,))


def test_dependency_depth_does_not_depend_on_python_recursion_limit():
    tasks = [source(0)]
    for index in range(1, 1200):
        tasks.append(FrameTask(FrameRef('stage-' + str(index), 0), (tasks[-1].ref,)))
    plan = build_schedule(reversed(tasks), (tasks[-1].ref,), max_live_results=2)
    assert len(plan.tasks) == 1200 and plan.peak_live_results == 2
    assert execute_schedule(plan, lambda ref, values: values[0] + 1 if values else 0) == (1199,)


@pytest.mark.parametrize('kind', ['duplicate', 'missing-output', 'empty-output', 'empty-tasks', 'cycle'])
def test_invalid_task_graphs_fail_before_execution(kind):
    a, b = source(0), source(1)
    if kind == 'duplicate':
        tasks, outputs = (a, a), (a.ref,)
    elif kind == 'missing-output':
        tasks, outputs = (a,), (b.ref,)
    elif kind == 'empty-output':
        tasks, outputs = (a,), ()
    elif kind == 'empty-tasks':
        tasks, outputs = (), (a.ref,)
    else:
        tasks = (FrameTask(a.ref, (b.ref,)), FrameTask(b.ref, (a.ref,)))
        outputs = (a.ref,)
    with pytest.raises(ValueError):
        build_schedule(tasks, outputs)


@pytest.mark.parametrize('kwargs', [
    {'max_tasks': 1}, {'max_edges': 2}, {'max_live_results': 2},
    {'max_tasks': True}, {'max_tasks': MAX_TASKS + 1}, {'max_edges': MAX_EDGES + 1},
    {'max_live_results': 0}, {'max_live_results': 1.5},
])
def test_work_and_liveness_budgets_fail_before_any_raster_callback(kwargs):
    tasks, output = fork_tasks()
    calls = []
    with pytest.raises(ValueError):
        plan = build_schedule(tasks, (output,), **kwargs)
        execute_schedule(plan, lambda ref, inputs: calls.append(ref))
    assert calls == []


def test_output_count_and_task_streams_are_bounded():
    a = source(0)
    with pytest.raises(ValueError, match='output budget'):
        build_schedule((a,), (a.ref for _ in range(MAX_OUTPUTS + 1)))
    seen = []

    def declarations():
        for frame in range(100):
            seen.append(frame)
            yield source(frame)

    with pytest.raises(ValueError, match='task budget'):
        build_schedule(declarations(), (a.ref,), max_tasks=3)
    assert seen == [0, 1, 2, 3]


def test_immutable_plan_and_detached_document_cannot_change_an_admitted_schedule():
    tasks, output = fork_tasks()
    plan = build_schedule(tasks, (output,))
    digest = plan.digest
    with pytest.raises(FrozenInstanceError):
        plan.tasks = ()
    with pytest.raises(FrozenInstanceError):
        plan.tasks[0].ref.frame = 7
    document = plan.document()
    document['tasks'][0]['ref']['frame']['numerator'] = 999
    assert plan.digest == digest
    assert json.loads(json.dumps(plan.document())) == plan.document()


def test_manually_forged_plan_is_revalidated_before_callback():
    tasks, output = fork_tasks()
    plan = build_schedule(tasks, (output,))
    calls = []
    with pytest.raises(ValueError, match='canonical admitted plan'):
        execute_schedule(replace(plan, peak_live_results=0), lambda ref, inputs: calls.append(ref))
    assert calls == []


def test_results_are_released_at_last_use_and_after_callback_failure():
    class Raster:
        pass

    tasks = [source(0)]
    for frame in range(1, 6):
        tasks.append(FrameTask(FrameRef('output', frame), (tasks[-1].ref,)))
    plan = build_schedule(tasks, (tasks[-1].ref,), max_live_results=2)
    weak = []

    def evaluate(ref, inputs):
        gc.collect()
        assert sum(item() is not None for item in weak) <= 1
        image = Raster()
        weak.append(weakref.ref(image))
        return image

    result = execute_schedule(plan, evaluate)
    gc.collect()
    assert sum(item() is not None for item in weak) == 1
    del result
    gc.collect()
    assert not any(item() is not None for item in weak)

    def fail(ref, inputs):
        if ref.frame == 3:
            raise RuntimeError('native callback failed')
        return evaluate(ref, inputs)

    with pytest.raises(RuntimeError, match='native callback failed'):
        execute_schedule(plan, fail)
    gc.collect()
    assert not any(item() is not None for item in weak)


def test_retained_failure_traceback_does_not_keep_scheduler_input_values_alive():
    class Raster:
        pass

    first = source(0)
    final = FrameTask(FrameRef('output', 0), (first.ref,))
    plan = build_schedule((first, final), (final.ref,))
    weak = []

    def evaluate(ref, inputs):
        if ref.stage == 'native':
            raster = Raster()
            weak.append(weakref.ref(raster))
            return raster
        # This renderer explicitly drops its own input alias before failing.
        # The scheduler must also release its alias while the traceback lives.
        del inputs
        raise RuntimeError('retain this native failure')

    retained = None
    try:
        execute_schedule(plan, evaluate)
    except RuntimeError as error:
        retained = error
    assert retained is not None
    gc.collect()
    assert len(weak) == 1 and weak[0]() is None
    traceback = retained.__traceback__
    found = False
    while traceback is not None:
        if traceback.tb_frame.f_code.co_name == 'execute_schedule':
            found = True
            assert traceback.tb_frame.f_locals['values'] == {}
            assert 'inputs' not in traceback.tb_frame.f_locals
            assert 'value' not in traceback.tb_frame.f_locals
        traceback = traceback.tb_next
    assert found
