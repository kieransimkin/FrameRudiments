"""S5 native Project/Compositor pixel proof for the existing CPU CI matrix.

No mocked native types, network assets, skips or GPU are used. Separate from
controller/cache unit tests, these prove that retained sections really keep
identical authored Q5 pixels under each supported local edit.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.project import Project
from framerudiments.preview_dependencies import build_dependency_index
from framerudiments.preview_quality import profile_capabilities
from framerudiments.preview_segment_cache import SectionFrameCache
from framerudiments.preview_stream_contract import quality_profiles
from test_preview_native_project import fixture


def index(project, data, sidecars=None):
    return build_dependency_index(project, data, sidecars or {}, namespace='a'*64,
                                 renderer_abi='test-native-s5', implementation='b'*64,
                                 capabilities=profile_capabilities(project))


def encoded(image):
    stream = BytesIO()
    image.convert('RGBA').save(stream, 'PNG')
    return stream.getvalue()


@pytest.mark.parametrize('edit', ['layer_opacity', 'layer_post_effect', 'layer_animation'])
def test_retained_sections_match_native_q5_after_source_scoped_edit(tmp_path, edit):
    path, data = fixture(tmp_path, graph=True)
    cue = tmp_path/'cues.lrc'
    cue.write_text('[00:00.000][SECTION: FIRST]\n[00:00.500][SECTION: SECOND]\n', encoding='utf-8')
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    data['layers'][0].update(start_seconds=0, end_seconds=.5)
    if edit == 'layer_post_effect':
        data['post_effects'] = {'elements': {'layer:square': [
            {'id': 'local', 'visual': {'opacity': 1}}]}}
    if edit == 'layer_animation':
        data['layer_animations'] = {'layer:square': {'opacity': {'keys': [
            {'time': 0, 'value': 1}, {'time': 1.9, 'value': 1}]}}}
    path.write_text(json.dumps(data), encoding='utf-8')
    before = {name: (tmp_path/name).read_bytes() for name in ('project.json', 'source.png', 'source.wav', 'cues.lrc')}
    p = Project.load(path)
    a = SectionFrameCache(tmp_path/'cache', manifest=index(p, data), renderer_abi='test-native-s5',
                          profiles=quality_profiles(p))
    reference = Compositor(p)
    try:
        for frame in range(p.frame_count):
            image, _ = reference.frame(frame)
            ticket = a.ticket(frame, 'Q5')
            a.publish(ticket, encoded(image), settings_sha256=a.settings_identity(ticket))
    finally:
        reference.close()
    draft = deepcopy(data)
    if edit == 'layer_opacity':
        draft['layers'][0]['opacity'] = .2
    elif edit == 'layer_post_effect':
        draft['post_effects']['elements']['layer:square'][0]['visual']['opacity'] = .2
    else:
        for key in draft['layer_animations']['layer:square']['opacity']['keys']:
            key['value'] = .2
    candidate = Project(draft, p.base)
    revised = SectionFrameCache(tmp_path/'cache', manifest=index(candidate, draft), renderer_abi='test-native-s5',
                                profiles=quality_profiles(candidate))
    assert revised.last_revision['invalidated_frames'] == 60
    assert [row['old_range'] for row in revised.last_revision['affected_sections']] == [[0, 30], [60, 90]]
    reference = Compositor(candidate)
    try:
        for frame in range(p.frame_count):
            cached = revised.get(frame)
            if frame < 30 or 60 <= frame < 90:
                assert cached is None
            else:
                assert cached is not None and cached.quality_rank == 5
                expected, _ = reference.frame(frame)
                with Image.open(BytesIO(cached.png)) as decoded:
                    assert decoded.tobytes() == expected.convert('RGBA').tobytes()
    finally:
        reference.close()
    assert {name: (tmp_path/name).read_bytes() for name in before} == before


def test_edited_cue_partitions_use_native_repeated_source_ledger(tmp_path):
    path, data = fixture(tmp_path)
    cue = tmp_path/'cues.lrc'
    cue.write_text('[00:00.500][SECTION: OLD]\n', encoding='utf-8')
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    path.write_text(json.dumps(data), encoding='utf-8')
    p = Project.load(path)
    manifest = index(p, data, {'cues': [{'start': .25, 'label': '[SECTION: EDITED]'}]})
    assert [(s.start_frame, s.end_frame_exclusive) for s in manifest.sections] == [
        (0, 15), (15, 60), (60, 75), (75, 120)]
    assert cue.read_text(encoding='utf-8') == '[00:00.500][SECTION: OLD]\n'
