"""Actual native scheduling, exact repeat cuts, future sampling and resource gates."""
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
import json
import wave

from PIL import Image, ImageDraw
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.compositor_temporal import temporal_read_bounds
from framerudiments.quality_effects import linear_shutter_composite


@pytest.fixture
def project_factory(tmp_path):
    with wave.open(str(tmp_path / 'source.wav'), 'wb') as stream:
        stream.setparams((1, 2, 10000, 20000, 'NONE', 'not compressed'))
        stream.writeframes(b'\0\0' * 20000)
    sprite = Image.new('RGBA', (16, 16))
    ImageDraw.Draw(sprite).polygon([(1, 14), (8, 1), (15, 14)], fill='#f9b84b')
    sprite.save(tmp_path / 'source.png')

    def ref(name):
        return {'path': name, 'sha256': sha256((tmp_path / name).read_bytes()).hexdigest()}

    base = {
        'schema': 'framerudiments.project.v1', 'width': 64, 'height': 48,
        'fps': 10, 'fps_reason': 'Small deterministic temporal regression', 'purpose': 'visualiser',
        'audio': {'source': ref('source.wav'), 'sample_rate': 10000,
                  'start_sample': 2000, 'end_sample': 8000, 'repeats': 3, 'fade_seconds': 0},
        'clock': {'authority': 'synthetic temporal regression', 'status': 'synthetic',
                  'bpm': 120, 'origin': .125},
        'background': {'colour': '#11243a'},
        'layers': [{'id': 'motion', 'asset': ref('source.png'), 'center': [32, 24], 'width': 16,
                    'motion': {'kind': 'named', 'pattern': 'circle', 'repeat_clock': 'continuous',
                               'interpolate': True}, 'mapping': {'dx': 12, 'dy': 9}}],
        'render_quality': {'enabled': True, 'spatial_scale': 1, 'temporal_samples': 4, 'antialias': 0},
    }

    def create(*, factor=4, shutter=True, span_samples=6000, repeats=3,
               routing=False, injection=False, external=False):
        data = deepcopy(base)
        data['audio'].update(end_sample=2000 + span_samples, repeats=repeats)
        data['render_quality']['temporal_samples'] = factor
        if shutter:
            data['post_effects'] = {'temporal': {'strength': .75, 'shutter_frames': 8,
                                                'samples': 9, 'falloff': 1}}
        if routing or injection or external:
            data['effects'] = [{'id': 'feedback', 'engine': 'butterchurn', 'preset': 'feedback'}]
        if routing:
            data['visualiser_routing'] = {'elements': {'layer:motion': {'overlay': True}}}
        if injection:
            data['effects'].append({'id': 'second', 'engine': 'projectm', 'preset': 'feedback'})
            data['visualiser_stage_injections'] = [{'before': 1, 'elements': ['layer:motion']}]
        return Project(data, tmp_path)
    return create


def test_inspection_is_json_only_and_does_not_construct_a_sampling_renderer(project_factory, monkeypatch):
    project = project_factory()
    before = deepcopy(project.data)
    comp = Compositor(project)
    try:
        monkeypatch.setattr(comp, '_native_frame', lambda *_args, **_kw: pytest.fail('inspection rendered pixels'))
        plan = comp.temporal_plan(2)
        assert not hasattr(comp, '_temporal_virtual')
        assert [ref.frame for ref in plan.source_refs] == [Fraction(i, 4) for i in range(12)]
        contract = comp.temporal_contract()
        assert contract['source_clock']['repeat_span_frames'] == {'numerator': 6, 'denominator': 1}
        assert contract['max_reach']['future_subframe'] == {'numerator': 3, 'denominator': 4}
        assert contract['scope']['native_compositor'] == 'pre_external_effects'
        assert contract['scope']['sequential_external_engines'] is False
        assert json.loads(json.dumps(contract)) == contract
        assert comp.evidence()['temporal_schedule'] == {'contract': contract, 'last_execution': None}
        assert project.data == before
    finally:
        comp.close()


def test_full_plan_is_the_actual_native_callback_order_and_carries_source_clocks(project_factory, monkeypatch):
    comp = Compositor(project_factory())
    observed = []
    native = Compositor._native_frame

    def record(owner, frame, **kwargs):
        observed.append(Fraction(frame * comp.project.fps, owner.project.fps))
        return native(owner, frame, **kwargs)

    monkeypatch.setattr(Compositor, '_native_frame', record)
    try:
        plan = comp.temporal_plan(4)
        image, context = comp.frame(4)
        assert image.size == (64, 48) and context.frame == 4
        assert observed == [task.ref.frame for task in plan.tasks if task.ref.stage == 'native']
        assert len(observed) == len(set(observed))
        receipt = comp.evidence()['temporal_schedule']['last_execution']
        assert receipt['digest'] == plan.digest
        assert receipt['executed_tasks'] == len(plan.tasks)
        assert receipt['native_samples'] == 20
        assert receipt['completed'] is True
        assert receipt['peak_live_results'] < 24
        assert context.source_seconds == comp.project.source_time(4)
    finally:
        comp.close()


@pytest.mark.parametrize('frame,expected', [(6, [6]), (8, [6, 7, 8]), (12, [12]), (14, [12, 13, 14])])
def test_short_repeats_do_not_admit_a_full_cycle_of_old_history(project_factory, frame, expected):
    project = project_factory(factor=1)
    comp = Compositor(project)
    reference = Compositor(project_factory(factor=1, shutter=False))
    try:
        actual, _ = comp.frame(frame)
        shutter = comp.post_effect_reports()['temporal']
        assert shutter['source_frames'] == expected
        images = [reference.frame(n)[0] for n in expected]
        expected_image = linear_shutter_composite(images, shutter['weights'], .75)
        assert actual.tobytes() == expected_image.tobytes()
        comp.frame(1)
        assert comp.frame(frame)[0].tobytes() == actual.tobytes()
    finally:
        comp.close()
        reference.close()


def test_nonintegral_repeat_cuts_and_final_subframe_clamp_are_exact(project_factory, monkeypatch):
    comp = Compositor(project_factory(span_samples=4900))
    try:
        assert comp.temporal_contract()['source_clock']['repeat_span_frames'] == {'numerator': 49, 'denominator': 10}
        for frame, expected in ((5, [5]), (9, [5, 6, 7, 8, 9]), (10, [10])):
            plan = comp.temporal_plan(frame)
            taps = next(task.inputs for task in plan.tasks if task.ref.stage == 'shutter')
            assert [int(ref.frame) for ref in taps] == expected
        plan = comp.temporal_plan(14)
        last = next(task for task in plan.tasks if task.ref.stage == 'supersample' and task.ref.frame == 14)
        assert [ref.frame for ref in last.inputs] == [Fraction(14), Fraction(57, 4), Fraction(29, 2), Fraction(29, 2)]
        seen = []
        native = Compositor._native_frame

        def record(owner, frame, **kwargs):
            seen.append(Fraction(frame, 4))
            return native(owner, frame, **kwargs)
        monkeypatch.setattr(Compositor, '_native_frame', record)
        comp.frame(14)
        assert seen.count(Fraction(29, 2)) == 1
    finally:
        comp.close()


def test_real_future_frames_are_deduplicated_in_execution_but_detached_in_return(project_factory, monkeypatch):
    comp = Compositor(project_factory(factor=1, shutter=False))
    calls = []
    draw = comp.frame

    def counted(frame, **kwargs):
        calls.append(frame)
        return draw(frame, **kwargs)
    monkeypatch.setattr(comp, 'frame', counted)
    try:
        samples = comp.sample_frames(3, (2, -1, 0, 2))
        assert calls == [2, 3, 5]
        assert [ctx.frame for _, ctx in samples] == [5, 2, 3, 5]
        assert samples[0][0].tobytes() == samples[3][0].tobytes()
        assert samples[0][0] is not samples[3][0]
        unchanged = samples[3][0].tobytes()
        samples[0][0].paste('#ff00ff', (0, 0, 64, 48))
        assert samples[3][0].tobytes() == unchanged
        assert samples[1][0].tobytes() != unchanged
    finally:
        comp.close()


def test_public_repeat_boundary_modes_are_explicit(project_factory):
    comp = Compositor(project_factory(factor=1, shutter=False))
    try:
        with pytest.raises(ValueError, match='boundary'):
            comp.sample_frames(6, (-1, 0))
        assert [ctx.frame for _, ctx in comp.sample_frames(6, (-1, 0), boundary='drop')] == [6]
        assert comp.sample_frames(6, (-1,), boundary='drop') == ()
        assert [ctx.frame for _, ctx in comp.sample_frames(6, (-1, 0, 99), boundary='clamp')] == [6, 6, 11]
        assert [ctx.frame for _, ctx in comp.sample_frames(6, (-1, 0), repeat_local=False)] == [5, 6]
    finally:
        comp.close()


@pytest.mark.parametrize('offsets', [(True,), (.5,), (Fraction(1, 2),), tuple(range(65))])
def test_public_invalid_batch_fails_before_native_callbacks(project_factory, monkeypatch, offsets):
    comp = Compositor(project_factory())
    try:
        monkeypatch.setattr(comp, '_native_frame', lambda *_a, **_k: pytest.fail('invalid work rendered'))
        with pytest.raises(ValueError, match='integer frame offsets'):
            comp.sample_frames(2, offsets)
        assert not hasattr(comp, '_temporal_virtual')
    finally:
        comp.close()


@pytest.mark.parametrize('which', ['MAX_NATIVE_RASTER_BYTES', 'MAX_BATCH_RGB_BYTES'])
def test_rgb_budget_fails_before_any_virtual_renderer_or_callback(project_factory, monkeypatch, which):
    from framerudiments import compositor_temporal

    comp = Compositor(project_factory())
    try:
        monkeypatch.setattr(compositor_temporal, which, 1)
        monkeypatch.setattr(comp, '_native_frame', lambda *_a, **_k: pytest.fail('over-budget work rendered'))
        with pytest.raises(ValueError, match='RGB result budget'):
            comp.sample_frames(2, (-1, 0, 1))
        assert not hasattr(comp, '_temporal_virtual')
    finally:
        comp.close()


def test_failed_native_callback_restores_metadata_and_retires_private_renderer(project_factory, monkeypatch):
    comp = Compositor(project_factory(factor=2, shutter=False))
    comp.frame(2)
    previous = deepcopy(comp.extents), deepcopy(comp.text_states), deepcopy(comp.post_effect_reports())
    native = Compositor._native_frame

    def fail(owner, frame, **kwargs):
        if frame == 1:
            raise RuntimeError('synthetic native callback failure')
        return native(owner, frame, **kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Compositor, '_native_frame', fail)
            with pytest.raises(RuntimeError, match='synthetic native callback failure'):
                comp.frame(0)
        assert (comp.extents, comp.text_states, comp.post_effect_reports()) == previous
        assert not hasattr(comp, '_temporal_virtual')
        assert comp.evidence()['temporal_schedule']['last_execution'] is None
        assert comp.frame(0)[0].size == (64, 48)
        assert comp.evidence()['temporal_schedule']['last_execution']['completed'] is True
    finally:
        comp.close()


def test_final_quality_filter_runs_once_after_the_completed_schedule(project_factory, monkeypatch):
    comp = Compositor(project_factory())
    applications = []
    try:
        monkeypatch.setattr(comp.output_quality, 'apply', lambda image: applications.append(image.size) or image)
        comp.frame(4)
        assert applications == [(64, 48)]
        comp.frame(3, apply_quality=False)
        assert applications == [(64, 48)]
    finally:
        comp.close()


def test_dependency_reach_uses_actual_quality_and_native_shutter_presence():
    assert temporal_read_bounds({}) == (0, 1)
    assert temporal_read_bounds({'render_quality': {'enabled': False}}) == (0, 0)
    assert temporal_read_bounds({'render_quality': {'temporal_samples': 1}}) == (0, 0)
    assert temporal_read_bounds({'post_effects': {'temporal': {}}}) == (8, 1)
    assert temporal_read_bounds({'post_effects': {'temporal': {}}, 'render_quality': {'enabled': False}}) == (8, 0)


@pytest.mark.parametrize('frame,span_samples', [(4, 6000), (14, 4900)])
def test_current_routed_ink_matches_ordered_premultiplied_source_average(project_factory, frame, span_samples):
    from framerudiments.super_sampling import temporal_compositor

    comp = Compositor(project_factory(routing=True, span_samples=span_samples))
    reference = Compositor(project_factory(routing=True, shutter=False, span_samples=span_samples))
    try:
        virtual = temporal_compositor(reference)
        expected = {}
        for tap in range(4):
            virtual._native_frame(min(frame * 4 + tap, virtual.project.frame_count - 1))
            parts = dict(virtual._route_parts)
            for key in reference.visualiser_routing.available:
                value = parts.get(key, Image.new('RGBA', (64, 48))).convert('RGBa')
                expected[key] = value if tap == 0 else Image.blend(expected[key], value, 1 / (tap + 1))
        comp.frame(1)
        comp.frame(frame)
        assert {key: image.tobytes() for key, image in comp._route_parts} == {
            key: image.convert('RGBA').tobytes() for key, image in expected.items()}
        receipt = comp.evidence()['temporal_schedule']['last_execution']
        assert receipt['rgba_route_bytes'] > 0
        assert receipt['retained_raster_bytes'] == receipt['rgb_result_bytes'] + receipt['rgba_route_bytes']
    finally:
        comp.close()
        reference.close()


@pytest.mark.parametrize('factor', [1, 4])
@pytest.mark.parametrize('kind', ['routing', 'injection'])
def test_rgba_routes_are_admitted_even_without_visualiser_routing(project_factory, monkeypatch, factor, kind):
    from framerudiments import compositor_temporal

    comp = Compositor(project_factory(factor=factor, **{kind: True}))
    try:
        plan = comp.temporal_plan(2)
        assert comp._temporal_route_count == 2
        rgb_bytes = plan.peak_live_results * 64 * 48 * 3
        monkeypatch.setattr(compositor_temporal, 'MAX_NATIVE_RASTER_BYTES', rgb_bytes)
        monkeypatch.setattr(Compositor, '_native_frame', lambda *_a, **_k: pytest.fail('over-budget route rendered'))
        with pytest.raises(ValueError, match='RGBA routes'):
            comp.frame(2)
        assert not hasattr(comp, '_temporal_virtual')
    finally:
        comp.close()


def test_complete_frame_sampling_refuses_configured_sequential_engines(project_factory, monkeypatch):
    comp = Compositor(project_factory(factor=1, external=True))
    try:
        monkeypatch.setattr(comp, '_native_frame', lambda *_a, **_k: pytest.fail('external sampling rendered'))
        assert comp.temporal_contract()['scope']['configured_external_effects'] is True
        with pytest.raises(ValueError, match='sequential external'):
            comp.sample_frames(2, (0, 1))
    finally:
        comp.close()


@pytest.mark.parametrize('reject', ['frame', 'budget'])
def test_admission_failure_clears_a_previous_execution_receipt(project_factory, monkeypatch, reject):
    from framerudiments import compositor_temporal

    comp = Compositor(project_factory(factor=1, shutter=False))
    try:
        comp.frame(2)
        assert comp.evidence()['temporal_schedule']['last_execution']['completed'] is True
        if reject == 'budget':
            monkeypatch.setattr(compositor_temporal, 'MAX_NATIVE_RASTER_BYTES', 1)
        with pytest.raises(ValueError):
            comp.frame(-1 if reject == 'frame' else 2)
        assert comp.evidence()['temporal_schedule']['last_execution'] is None
    finally:
        comp.close()


@pytest.mark.parametrize('reject', ['frame', 'budget'])
def test_legacy_supersample_helper_admits_before_creating_private_renderer(project_factory, monkeypatch, reject):
    from framerudiments import compositor_temporal
    from framerudiments.super_sampling import temporal_supersample

    comp = Compositor(project_factory(factor=2, shutter=False))
    try:
        if reject == 'budget':
            monkeypatch.setattr(compositor_temporal, 'MAX_NATIVE_RASTER_BYTES', 1)
        monkeypatch.setattr(Compositor, '_native_frame', lambda *_a, **_k: pytest.fail('unadmitted native work'))
        with pytest.raises(ValueError):
            temporal_supersample(comp, -1 if reject == 'frame' else 2)
        assert not hasattr(comp, '_temporal_virtual')
    finally:
        comp.close()


def test_legacy_supersample_callback_failure_restores_metadata_and_retires_renderer(project_factory, monkeypatch):
    from framerudiments.super_sampling import temporal_supersample

    comp = Compositor(project_factory(factor=2, shutter=False))
    try:
        comp.frame(2)
        previous = deepcopy(comp.extents), deepcopy(comp.text_states), deepcopy(comp.post_effect_reports())
        native = Compositor._native_frame

        def fail(owner, frame, **kwargs):
            if frame == 1:
                raise RuntimeError('compatibility callback failure')
            return native(owner, frame, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Compositor, '_native_frame', fail)
            with pytest.raises(RuntimeError, match='compatibility callback failure'):
                temporal_supersample(comp, 0)
        assert (comp.extents, comp.text_states, comp.post_effect_reports()) == previous
        assert not hasattr(comp, '_temporal_virtual')
        assert comp.evidence()['temporal_schedule']['last_execution'] is None
        assert temporal_supersample(comp, 0)[0].size == (64, 48)
    finally:
        comp.close()
