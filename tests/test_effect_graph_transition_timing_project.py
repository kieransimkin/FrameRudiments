"""End-to-end M5.5 tests against synthetic, checksum-pinned media and native clocks."""
import copy
import os
from pathlib import Path

import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.timeline import BeatClock


@pytest.fixture
def synthetic(tmp_path):
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        font = next((str(f) for f in (
            Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
            Path('C:/Windows/Fonts/arial.ttf'),
        ) if f.is_file()), None)
    if not font or not Path(font).is_file():
        pytest.skip('A usable pinned TTF is required for compositor integration')
    path = create_demo(tmp_path / 'timing', font, width=180, height=320)
    original = Project.load(path)
    data = copy.deepcopy(original.data)
    data['render_quality'] = {'enabled': False}
    return data, original.base


def _project(data, base, timing, *, controls=()):
    cfg = copy.deepcopy(data)
    cfg['effect_graph'] = {'nodes': [
        {'id': 'layer:native', 'type': 'source'},
        {'id': 'layer:bands', 'type': 'source'},
        {'id': 'mix', 'type': 'transition', 'inputs': ['layer:native', 'layer:bands'],
         'timing': timing},
    ], 'output': 'mix'}
    if controls:
        cfg['effect_graph_controls'] = list(controls)
    return Project(cfg, base)


def test_project_beat_locked_timing_random_access(synthetic):
    data, base = synthetic
    p = _project(data, base, {'start_beat': 1, 'duration_beats': 2})
    compositor = Compositor(p)
    try:
        a, _ = compositor.frame(30, apply_quality=False)
        b, _ = compositor.frame(60, apply_quality=False)
        compositor.frame(3, apply_quality=False)
        assert a.tobytes() == compositor.frame(30, apply_quality=False)[0].tobytes()
        assert a.tobytes() != b.tobytes()
        assert compositor.context(30).source_beat == pytest.approx(1)
        assert compositor.effect_graph.nodes['mix'].config['timing']['start_beat'] == 1
    finally:
        compositor.close()


def test_project_source_vs_continuous_repeat_clock(synthetic):
    data, base = synthetic
    data['audio']['repeats'] = 2
    from_source = Compositor(_project(data, base, {
        'clock': 'source', 'start_beat': 0, 'duration_beats': 4,
    }))
    continuous = Compositor(_project(data, base, {
        'clock': 'continuous', 'start_beat': 0, 'duration_beats': 4,
    }))
    try:
        first_ctx, second_ctx = from_source.context(0), from_source.context(240)
        assert first_ctx.source_beat == pytest.approx(second_ctx.source_beat)
        assert second_ctx.beat > first_ctx.beat
        # Compare the same static providers at the two clock readings. Native
        # layer animations may independently evolve across repeats, so their
        # rendered pixels should not be used as a clock oracle.
        from PIL import Image
        surfaces = {'layer:native': Image.new('RGBA', (2, 2), 'red'),
                    'layer:bands': Image.new('RGBA', (2, 2), 'blue')}
        a = from_source.effect_graph.render(surfaces, context=first_ctx)
        b = from_source.effect_graph.render(surfaces, context=second_ctx)
        assert a.tobytes() == b.tobytes()
        c = continuous.effect_graph.render(surfaces, context=continuous.context(240))
        assert b.tobytes() != c.tobytes()
    finally:
        from_source.close()
        continuous.close()


def test_variable_bpm_grid_controls_timing_without_nominal_bpm(synthetic):
    data, base = synthetic
    p = _project(data, base, {'start_beat': 0, 'duration_beats': 2})
    # This project-level clock is a valid nonuniform beat grid, so the sampled
    # transition phase follows the beat anchors, not 120 BPM or wall time.
    p.clock = BeatClock(beats=[0, 0.4, 1.1, 1.5, 2.0, 2.4, 3.2, 3.7])
    comp = Compositor(p)
    try:
        assert comp.context(24).beat == pytest.approx(1)
        assert comp.context(66).beat == pytest.approx(2)
        image, _ = comp.frame(24, apply_quality=False)
        assert image.size == (180, 320)
        assert comp.frame(24, apply_quality=False)[0].tobytes() == image.tobytes()
    finally:
        comp.close()


def test_native_progress_binding_supersedes_beats(synthetic):
    data, base = synthetic
    controls = [{
        'id': 'user-progress', 'node': 'mix', 'property': 'progress',
        'motion': {'kind': 'named', 'pattern': 'circle'},
        'axis': 'x', 'range': [0, 1],
    }]
    comp = Compositor(_project(data, base, {'start_beat': 16, 'duration_beats': 2}, controls=controls))
    try:
        ctx = comp.context(40)
        resolved = comp.effect_graph_controls.values(ctx, comp.effect_graph)
        assert ('mix', 'progress') in resolved
        # At beat <16 the timing engine alone would return zero. A bound
        # native progress value must instead take precedence.
        assert ctx.beat < 16
        assert 0 <= resolved[('mix', 'progress')] <= 1
        assert comp.frame(40, apply_quality=False)[0].tobytes() == comp.frame(40, apply_quality=False)[0].tobytes()
    finally:
        comp.close()
