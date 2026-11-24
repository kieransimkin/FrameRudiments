"""Native adapter lifecycle tests with explicit native-dependency test doubles.

Real Project/Compositor tests are in test_preview_native_project.py, separately
from these unit-level thread/size/cleanup checks.
"""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from threading import Thread
from types import ModuleType, SimpleNamespace
import sys

from PIL import Image
import pytest

from framerudiments.preview_native_renderer import NativePreviewRenderer, _sidecar


@pytest.fixture
def native_doubles(monkeypatch, tmp_path):
    document = {'schema': 'framerudiments.project.v1', 'width': 64, 'height': 48, 'fps': 60,
                'layers': [], 'render_quality': {'spatial_scale': 4, 'temporal_samples': 8}}
    class Quality:
        def __init__(self, config=None):
            cfg = config or {}
            self.enabled = cfg.get('enabled', True)
            self.spatial_scale = cfg.get('spatial_scale', 4)
            self.temporal_samples = cfg.get('temporal_samples', 8)
    class Project:
        @classmethod
        def load(cls, path):
            return cls(deepcopy(document), Path(path).parent)
        def __init__(self, data, base):
            self.data, self.base = data, Path(base)
            self.width, self.height, self.fps, self.frame_count = 64, 48, 60, 120
            self.inputs, self.canonical_inputs, self.effect_graph_spec = [], None, None
            self.output_quality = Quality(data.get('render_quality'))
            self.clock = object()
        def verify_inputs(self):
            return None
        def source_time(self, n):
            return 1 + (n / self.fps) % 1
        def continuous_beat(self, n):
            return 2 + n / 30
    instances, samples = [], []
    class Compositor:
        def __init__(self, project, *, layer_cache):
            self.project, self.closed = project, False
            self.layer_cache = layer_cache
            instances.append(self)
        def frame(self, n, apply_quality=True):
            assert not self.closed and apply_quality is True
            samples.append((n, self.project.width, self.project.height, self.project.fps,
                            self.project.source_time(n), self.project.continuous_beat(n)))
            return Image.new('RGBA', (self.project.width, self.project.height), (n, 2, 3, 255)), None
        def close(self):
            self.closed = True
    for name, key, value in [('project', 'Project', Project), ('compositor', 'Compositor', Compositor),
                             ('output_quality', 'OutputQuality', Quality)]:
        module = ModuleType('framerudiments.'+name)
        setattr(module, key, value)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    renderer = NativePreviewRenderer(tmp_path/'project.json', document, {},
                                     validate_draft=lambda a,b: None, temp_parent=tmp_path/'cache')
    return SimpleNamespace(renderer=renderer, document=document, instances=instances,
                           samples=samples, compositor=Compositor)


def test_compositor_receives_reduced_canvas_and_preserves_frame_clock(native_doubles):
    fixture = native_doubles
    renderer = fixture.renderer
    before = deepcopy(fixture.document)
    try:
        for frame, profile, size in [(61, 'Q0', (16, 12)), (3, 'Q0', (16, 12)),
                                      (62, 'Q2', (48, 36)), (0, 'Q5', (64, 48))]:
            with Image.open(BytesIO(renderer(frame, profile))) as image:
                assert image.size == size and image.getpixel((0, 0)) == (frame, 2, 3, 255)
            assert fixture.samples[-1] == (frame, *size, 60, 1+(frame/60)%1, 2+frame/30)
        assert len(fixture.instances) == 3  # Same-tier arbitrary seeks reuse the native compositor.
        assert renderer.layer_cache is not None
        assert all(c.layer_cache is renderer.layer_cache for c in fixture.instances)
        assert all(c.closed for c in fixture.instances[:-1])
        assert renderer.project.data == before
        assert fixture.document == before
    finally:
        renderer.close()
    assert fixture.instances[-1].closed
    assert not list(renderer.temp_parent.iterdir())


def test_cross_thread_native_access_is_refused(native_doubles):
    renderer = native_doubles.renderer
    renderer(0, 'Q5')
    errors = []
    def foreign():
        try:
            renderer(1, 'Q4')
        except RuntimeError as exc:
            errors.append(str(exc))
    worker = Thread(target=foreign)
    worker.start()
    worker.join(timeout=2)
    renderer.close()
    assert errors and 'owning thread' in errors[0]
    with pytest.raises(RuntimeError, match='closed'):
        renderer(0, 'Q5')


def test_native_render_failure_discards_context_and_recovers(native_doubles, monkeypatch):
    fixture = native_doubles
    renderer = fixture.renderer
    original = fixture.compositor.frame
    def fail(self, *_args, **_kwargs):
        raise MemoryError('injected raster allocation failure')
    monkeypatch.setattr(fixture.compositor, 'frame', fail)
    try:
        with pytest.raises(MemoryError):
            renderer(0, 'Q5')
        assert renderer.compositor is None and renderer.profile is None
        assert fixture.instances[-1].closed
        monkeypatch.setattr(fixture.compositor, 'frame', original)
        assert renderer(0, 'Q4').startswith(b'\x89PNG')
    finally:
        renderer.close()


@pytest.mark.parametrize('rows', [
    [{'start': True, 'label': 'x'}], [{'start': float('nan'), 'label': 'x'}],
    [{'start': 1, 'label': 'x\ny'}], [{'start': -1, 'label': 'x'}],
])
def test_sidecar_rejects_ambiguous_source_times_or_labels(rows):
    with pytest.raises(ValueError):
        _sidecar(rows)


def test_sidecar_uses_source_milliseconds_and_cue_schema():
    assert _sidecar([{'start': 2, 'label': 'é'}, {'start': 1.125, 'label': 'hello'}]) == \
        '[00:01.125]hello\n[00:02.000]é\n'
    with pytest.raises(ValueError, match='edited cues'):
        _sidecar([{'start': 1, 'label': '[OTHER: bad]'}], cues=True)


def test_native_rgb_final_frame_gets_lossless_transport_alpha(native_doubles, monkeypatch):
    fixture = native_doubles
    original = fixture.compositor.frame
    def rgb(self, *args, **kwargs):
        image, context = original(self, *args, **kwargs)
        return image.convert('RGB'), context
    monkeypatch.setattr(fixture.compositor, 'frame', rgb)
    try:
        with Image.open(BytesIO(fixture.renderer(4, 'Q0'))) as image:
            assert image.mode == 'RGBA'
            assert image.size == (16, 12)
            assert image.getpixel((0, 0)) == (4, 2, 3, 255)
    finally:
        fixture.renderer.close()


def test_paused_release_closes_context_on_owner_and_can_rebuild(native_doubles):
    renderer = native_doubles.renderer
    renderer(3, 'Q5')
    project, temporary = renderer.project, renderer.temporary.name
    previous = native_doubles.instances[-1]
    renderer.release_compositor()
    assert previous.closed
    assert renderer.compositor is None and renderer.profile is None
    assert renderer.project is project and renderer.temporary.name == temporary
    assert Path(temporary).is_dir()
    try:
        renderer(6, 'Q0')
        assert renderer.project is project
        assert native_doubles.instances[-1] is not previous
        assert native_doubles.samples[-1][:3] == (6, 16, 12)
    finally:
        renderer.close()
    assert not list(renderer.temp_parent.iterdir())


def test_foreign_thread_cannot_release_foreground_context(native_doubles):
    renderer = native_doubles.renderer
    renderer(0, 'Q5')
    errors = []
    def release():
        try:
            renderer.release_compositor()
        except RuntimeError as exc:
            errors.append(str(exc))
    worker = Thread(target=release)
    worker.start()
    worker.join(2)
    assert errors and 'owning thread' in errors[0]
    assert not native_doubles.instances[-1].closed
    renderer.close()
