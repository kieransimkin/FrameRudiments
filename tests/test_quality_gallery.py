"""The published quality gallery must use the exact production source clock."""
from fractions import Fraction
import importlib
from pathlib import Path

from PIL import Image
import pytest

from framerudiments.quality_effects import TemporalShutter


@pytest.fixture
def gallery(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    return importlib.import_module('generate_quality_gallery')


def test_gallery_temporal_presets_use_declared_source_clock(gallery, monkeypatch):
    calls = []

    def source(frame):
        calls.append(frame)
        return Image.new('RGB', (12, 8), (frame, frame * 2 % 256, frame * 3 % 256))

    monkeypatch.setattr(gallery, '_base', source)
    expected_history = {'temporal-soft': 2, 'temporal-long': 5, 'both-blurs': 3}
    for name, _label, _visual, temporal, _description in gallery.SCENES:
        if temporal is None:
            continue
        for frame in gallery.MOMENTS:
            calls.clear()
            image = gallery._run_temporal(frame, temporal)
            assert calls == list(range(frame - expected_history[name], frame + 1))
            assert image.mode == 'RGB' and image.size == (12, 8)
            assert image.tobytes() != source(frame).tobytes()
    project = gallery._project()
    source_duration = Fraction(project.end_sample - project.start_sample, project.sample_rate)
    assert source_duration * project.fps == project.frame_count
    assert max(gallery.MOMENTS) < project.frame_count
    for frame in gallery.MOMENTS:
        context = gallery._context(frame)
        expected_source = Fraction(project.start_sample, project.sample_rate) + Fraction(frame, project.fps)
        assert context.source_seconds == float(expected_source)


def test_gallery_clock_keeps_production_shutter_inside_short_repeats(gallery):
    project = gallery._project()
    project.end_sample = project.start_sample + project.sample_rate // 10
    project.repeats, project.frame_count = 3, 18

    def context_at(frame):
        context = gallery._context(frame)
        context.source_seconds = (frame % 6) / project.fps
        context.beat = project.clock.beat_at(context.source_seconds)
        return context

    shutter = TemporalShutter(project, {'samples': 7, 'shutter_frames': 6}, context_at)
    # A six-frame delay has the same wrapped timestamp as the new cycle; exact
    # source-cycle membership must still remove the previous cycle's output.
    assert shutter.plan(context_at(6))[0] == [6]
    assert shutter.plan(context_at(8))[0] == [6, 7, 8]
