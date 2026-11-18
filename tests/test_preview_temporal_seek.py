"""S6 native temporal seeking and whole-section dependency acceptance.

Uses real Project/Compositor/Pillow/SQLite paths with synthetic pinned inputs.
No mocked renderer, downloaded media, native feedback engine, or skipped gate.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import random

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.preview_dependencies import build_dependency_index
from framerudiments.preview_native_renderer import NativePreviewRenderer
from framerudiments.preview_quality import ProfileUnsupported, profile_capabilities, profile_document
from framerudiments.preview_seek_support import temporal_reach
from framerudiments.preview_segment_cache import SectionFrameCache
from framerudiments.preview_stream_contract import quality_profiles
from framerudiments.project import Project
from test_preview_native_project import fixture


def temporal_project(tmp_path, *, repeated=False):
    path, data = fixture(tmp_path)
    data['render_quality'] = {'spatial_scale': 1, 'temporal_samples': 1}
    data['audio']['repeats'] = 1
    if repeated:
        data['audio'].update(start_sample=12000, end_sample=36000, repeats=3)
        data['layer_animations'] = {'layer:square': {'opacity': {'keys': [
            {'time': 0, 'value': .05}, {'time': .4, 'value': 1},
            {'time': .9, 'value': .2}, {'time': 1.5, 'value': .95}]}}}
    data['post_effects'] = {'temporal': {'strength': 1, 'shutter_frames': 8, 'samples': 9}}
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


def rgba_png(image):
    out = BytesIO()
    image.convert('RGBA').save(out, 'PNG')
    return out.getvalue()


def production_frame(compositor, frame):
    # The no-feedback production ordering applies exactly one final AA pass.
    image, context = compositor.frame(frame, apply_quality=False)
    return compositor.output_quality.apply(image).convert('RGBA'), context


def manifest(project):
    return build_dependency_index(project, project.data, {}, namespace='e'*64,
        renderer_abi='test-native-s6-temporal', implementation='f'*64,
        reach=temporal_reach(project.data), capabilities=profile_capabilities(project, allow_stateful=True))


def test_random_temporal_seeks_match_native_sequential_pixels_and_repeated_source_clock(tmp_path):
    path, data = temporal_project(tmp_path, repeated=True)
    before = {name: (tmp_path/name).read_bytes() for name in ('project.json', 'source.png', 'source.wav')}
    project = Project.load(path)
    assert project.frame_count == 90
    reference = Compositor(project)
    expected = {}
    try:
        for frame in range(project.frame_count):
            image, context = production_frame(reference, frame)
            expected[frame] = image.tobytes()
            assert context.frame == frame
    finally:
        reference.close()
    assert len(set(expected.values())) > 10  # Exercise actual varying history, not a constant image.
    renderer = NativePreviewRenderer(path, deepcopy(data), {}, validate_draft=lambda a, b: None,
                                     temp_parent=tmp_path/'native')
    order = list(range(project.frame_count))
    random.Random(610).shuffle(order)
    order = [89, 30, 29, 31, 60, 59, 61, 0, *order]
    try:
        for frame in order:
            with Image.open(BytesIO(renderer(frame, 'Q5'))) as image:
                assert image.mode == 'RGBA' and image.size == (project.width, project.height)
                assert image.tobytes() == expected[frame]
            live = renderer.compositor.project
            assert (live.fps, live.frame_count, live.start_sample, live.end_sample, live.repeats) == (
                project.fps, project.frame_count, project.start_sample, project.end_sample, project.repeats)
            assert live.source_time(frame) == project.source_time(frame)
            assert live.continuous_beat(frame) == project.continuous_beat(frame)
            sampled = renderer.compositor.post_pipeline.last_report['temporal']['source_frames']
            assert sampled[-1] == frame
            assert all(max(0, frame - 8) <= n <= frame for n in sampled)
            assert all(n >= frame // 30 * 30 for n in sampled)
            if frame in (0, 30, 60):
                assert sampled == [frame]  # No previous-cycle pixels at an excerpt reset.
        assert renderer.project.data == data
    finally:
        renderer.close()
    assert not list((tmp_path/'native').iterdir())
    assert {name: (tmp_path/name).read_bytes() for name in before} == before


def test_temporal_preview_retains_authored_settings_and_full_canvas_profile_floor(tmp_path):
    path, data = temporal_project(tmp_path, repeated=True)
    data['render_quality'] = {'spatial_scale': 2, 'temporal_samples': 2, 'antialias_passes': 3}
    path.write_text(json.dumps(data), encoding='utf-8')
    project = Project.load(path)
    original = deepcopy(project.data)
    assert not any(row['supported'] for row in profile_capabilities(project))
    profiles = profile_capabilities(project, allow_stateful=True)
    assert [row['supported'] for row in profiles] == [False, False, False, True, True, True]
    assert profile_document(project, 'Q5', allow_stateful=True) == original
    renderer = NativePreviewRenderer(path, data, {}, validate_draft=lambda a, b: None,
                                     temp_parent=tmp_path/'native')
    reference = Compositor(project)
    try:
        for profile in ('Q3', 'Q4', 'Q5'):
            for frame in (61, 7, 30):
                with Image.open(BytesIO(renderer(frame, profile))) as image:
                    assert image.size == (project.width, project.height)
                    if profile == 'Q5':
                        expected, _ = production_frame(reference, frame)
                        assert image.tobytes() == expected.tobytes()
                view = renderer.compositor.project
                assert view.fps == project.fps and view.frame_count == project.frame_count
                assert view.data['post_effects'] == original['post_effects']
                assert view.source_time(frame) == project.source_time(frame)
        with pytest.raises(ProfileUnsupported):
            renderer(30, 'Q0')
    finally:
        renderer.close()
        reference.close()
    assert project.data == original
    assert not list((tmp_path/'native').iterdir())


def test_shutter_reads_invalidate_adjoining_section_and_preserve_independent_native_q5(tmp_path):
    path, data = temporal_project(tmp_path)
    cue = tmp_path/'cues.lrc'
    cue.write_text(''.join(f'[00:00.{ms:03d}][SECTION: PART {i}]\n'
                           for i, ms in enumerate((0, 250, 500, 750))), encoding='utf-8')
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    data['layers'][0].update(start_seconds=.25, end_seconds=.5)
    path.write_text(json.dumps(data), encoding='utf-8')
    project = Project.load(path)
    old_manifest = manifest(project)
    assert old_manifest.temporal.mode == 'bounded' and old_manifest.temporal.past_frames == 8
    assert [(s.start_frame, s.end_frame_exclusive) for s in old_manifest.sections] == [
        (0, 15), (15, 30), (30, 45), (45, 60)]
    cache = SectionFrameCache(tmp_path/'cache', manifest=old_manifest,
        renderer_abi='test-native-s6-temporal', profiles=quality_profiles(project))
    reference = Compositor(project)
    original_pixels = {}
    try:
        for frame in range(project.frame_count):
            image, _ = production_frame(reference, frame)
            original_pixels[frame] = image.tobytes()
            ticket = cache.ticket(frame, 'Q5')
            assert cache.publish(ticket, rgba_png(image), settings_sha256=cache.settings_identity(ticket)) == 'inserted'
    finally:
        reference.close()
    retained = {frame: cache.get(frame) for frame in (0, 14, 45, 59)}
    stale = cache.ticket(30, 'Q5')
    draft = deepcopy(data)
    draft['layers'][0]['opacity'] = .15
    candidate = Project(draft, project.base)
    revised_manifest = manifest(candidate)
    cache.revise(revised_manifest, renderer_abi='test-native-s6-temporal', profiles=quality_profiles(candidate))
    receipt = cache.last_revision
    assert receipt['invalidated_frames'] == 30
    assert [row['old_range'] for row in receipt['affected_sections']] == [[15, 30], [30, 45]]
    assert len(receipt['retained_sections']) == 2
    assert all(cache.get(frame) is None for frame in range(15, 45))
    assert {frame: cache.get(frame) for frame in retained} == retained
    assert (old_manifest.sections[1].id, old_manifest.sections[2].id) in old_manifest.temporal_edges
    reference = Compositor(candidate)
    try:
        for frame in range(candidate.frame_count):
            image, _ = production_frame(reference, frame)
            cached = cache.get(frame)
            if frame < 15 or frame >= 45:
                assert cached is not None and cached.profile_id == 'Q5'
                with Image.open(BytesIO(cached.png)) as decoded:
                    assert decoded.tobytes() == image.tobytes()
            if frame == 30:
                assert image.tobytes() != original_pixels[30]  # History changed despite an inactive current layer.
                assert cache.publish(stale, rgba_png(image), settings_sha256=cache.settings_identity(stale)) == 'stale'
            if frame == 44:
                assert image.tobytes() == original_pixels[44]  # Whole dependent section stays invalidated.
                assert cached is None
    finally:
        reference.close()
