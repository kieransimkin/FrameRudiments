"""M10.4 native transition projections, quality, random access and ownership."""
from base64 import b64decode
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.effect_graph import EffectGraph
from framerudiments.graph_authoring_preview import AuthoringPreviewError, SCHEMA, preview_transition_draft
from framerudiments.preview_stream_contract import canonical_digest
from framerudiments.project import Project
from test_authoring_preview import author
from test_preview_native_project import fixture as native_project


@pytest.fixture
def native(tmp_path):
    path, data = native_project(tmp_path)
    data['effect_graph'] = {'nodes': [
        {'id': 'frame', 'type': 'source'}, {'id': 'background', 'type': 'source'},
        {'id': 'mix', 'type': 'transition', 'inputs': ['frame', 'background'],
         'transition_style': 'wipe', 'feather': .05,
         'timing': {'duration_beats': 2, 'repeat': 'pingpong'}},
        {'id': 'tail', 'type': 'opacity', 'inputs': ['mix'], 'opacity': .15}], 'output': 'tail'}
    data['layers'][0]['motion'] = {'kind': 'named', 'pattern': 'circle'}
    data['layers'][0]['mapping'] = {'dx': 12, 'dy': 4}
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


def compare(native, *, document=None, node='mix', frame=17, quality='fast', sidecars=None):
    path, original = native
    return preview_transition_draft(path, document or original, sidecars=sidecars or {}, node=node,
                                    frame=frame, quality=quality, validate_draft=author.check_draft,
                                    temp_parent=path.parent/'owned')


def pixels(row):
    encoded = b64decode(row['png_base64'], validate=True)
    assert sha256(encoded).hexdigest() == row['sha256']
    with Image.open(BytesIO(encoded)) as image:
        image.load()
        assert image.mode == 'RGB' and image.size == (row['width'], row['height'])
        raw = image.tobytes()
    assert sha256(raw).hexdigest() == row['pixel_sha256']
    return raw


def reference(path, document, target, frame, quality):
    data = deepcopy(document)
    data['effect_graph']['output'] = target
    if quality == 'fast':
        data['render_quality'] = {'enabled': False}
    compositor = Compositor(Project(data, path.parent))
    try:
        image, context = compositor.frame(frame, apply_quality=quality == 'full')
        return image.tobytes(), context
    finally:
        compositor.close()


@pytest.mark.parametrize('quality', ['fast', 'full'])
@pytest.mark.parametrize('temporal', [False, True])
def test_native_comparison_matches_independent_full_projects_across_scrubs_and_repeats(native, quality, temporal):
    path, original = native
    document = deepcopy(original)
    if temporal:
        document['post_effects'] = {'temporal': {'strength': .7, 'shutter_frames': 3, 'samples': 4}}
    before = deepcopy(document), {p: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
    hashes = []
    for frame in (0, 17, 73, 5, 17):
        result = compare(native, document=document, frame=frame, quality=quality)
        assert result['schema'] == SCHEMA
        assert result['request_sha256'] == canonical_digest({'project': document, 'sidecars': {}})
        assert result['node'] == {'id': 'mix', 'authored_id': 'mix', 'label': 'mix',
                                  'type': 'transition', 'transition_style': 'wipe'}
        assert result['frame'] == frame and result['quality'] == quality
        assert (result['width'], result['height']) == (64, 48)
        assert result['render_quality']['enabled'] == (quality == 'full')
        assert result['render_quality']['spatial_scale'] == (2 if quality == 'full' else 1)
        assert result['render_quality']['temporal_samples'] == (2 if quality == 'full' else 1)
        assert result['scope'] == {'pipeline': 'native_graph_before_sequential_effects',
                                  'sequential_effects_excluded': False,
                                  'native_temporal_post_effects': temporal,
                                  'black_matte': True, 'production_approved': False}
        assert [row['role'] for row in result['images']] == ['from', 'result', 'to']
        for row, target in zip(result['images'], ('frame', 'mix', 'background')):
            expected, context = reference(path, document, target, frame, quality)
            assert row['node'] == target and pixels(row) == expected
            assert result['clock'] == {'frame': frame, 'seconds': context.output_seconds,
                'source_seconds': context.source_seconds, 'beat': context.beat, 'source_beat': context.source_beat}
        hashes.append(result['images'][1]['pixel_sha256'])
    assert hashes[1] == hashes[-1] and len(set(hashes)) > 1
    assert before[0] == document
    assert {p: p.read_bytes() for p in before[1]} == before[1]
    assert not list((path.parent/'owned').iterdir())


def test_full_quality_is_observably_different_and_retains_selected_output(native):
    path, document = native
    fast, full = compare(native), compare(native, quality='full')
    assert pixels(fast['images'][1]) != pixels(full['images'][1])
    expected, _ = reference(path, document, 'mix', 17, 'full')
    final_output, _ = reference(path, document, 'tail', 17, 'full')
    assert pixels(full['images'][1]) == expected != final_output
    document['render_quality'] = {'enabled': False}
    assert compare(native, quality='full')['render_quality']['enabled'] is False


@pytest.mark.parametrize('quality', ['fast', 'full'])
def test_native_feedback_is_reconstructed_independently_for_each_graph_projection(native, quality):
    path, original = native
    document = deepcopy(original)
    document['post_effects'] = {'feedback': {'delay_frames': 3, 'mix': .6, 'reset': 'programme',
                                           'checkpoint_interval': 4, 'max_checkpoints': 2}}
    before = deepcopy(document), path.read_bytes()
    for frame in (11, 4):
        result = compare(native, document=document, frame=frame, quality=quality)
        assert result['scope']['native_feedback'] is True
        assert result['scope']['sequential_effects_excluded'] is False
        for row, target in zip(result['images'], ('frame', 'mix', 'background')):
            expected, context = reference(path, document, target, frame, quality)
            assert row['node'] == target and pixels(row) == expected
            assert result['clock']['source_seconds'] == context.source_seconds
        assert not list((path.parent / 'owned').iterdir())
    assert document == before[0] and path.read_bytes() == before[1]


def test_chain_stage_metadata_and_processed_inputs_are_native_not_generated_id_parsing(native):
    path, document = native
    document['effect_graph']['nodes'] = [
        {'id': 'frame', 'type': 'source'}, {'id': 'background', 'type': 'source'},
        {'id': 'Chain :: mixed \u2603', 'type': 'transition_chain', 'inputs': ['frame', 'background'],
         'input_effects': [[{'id': 'dim', 'type': 'opacity', 'opacity': .6}], []],
         'stages': [{'id': 'blend', 'progress': .4, 'transition_style': 'dissolve'}], 'opacity': .1}]
    document['effect_graph']['output'] = 'Chain :: mixed \u2603'
    graph = EffectGraph(document['effect_graph'])
    stage = graph.chain_expansions['Chain :: mixed \u2603']['stages']['blend']
    result = compare(native, node=stage)
    assert result['node']['authored_id'] == 'Chain :: mixed \u2603'
    assert result['node']['label'] == 'Chain :: mixed \u2603 \u00b7 stage blend'
    assert result['images'][0]['label'] == 'Chain :: mixed \u2603 \u00b7 input 1 \u00b7 dim'
    expected = deepcopy(document)
    expected['effect_graph'] = {'nodes': [
        {'id': 'frame', 'type': 'source'}, {'id': 'background', 'type': 'source'},
        {'id': 'dimmed', 'type': 'opacity', 'inputs': ['frame'], 'opacity': .6},
        {'id': 'selected', 'type': 'transition', 'inputs': ['dimmed', 'background'],
         'progress': .4, 'transition_style': 'dissolve'}], 'output': 'selected'}
    for row, target in zip(result['images'], ('dimmed', 'selected', 'background')):
        assert pixels(row) == reference(path, expected, target, 17, 'fast')[0]
    with pytest.raises(AuthoringPreviewError) as rejected:
        compare(native, node='Chain :: mixed \u2603')
    assert rejected.value.code == 'node_invalid'


def test_matte_transition_keeps_its_third_input_and_native_binding(native):
    path, document = native
    document['effect_graph']['nodes'][2] = {'id': 'mix', 'type': 'transition',
        'inputs': ['frame', 'background', 'frame'], 'transition_style': 'matte', 'progress': .4}
    document['effect_graph_controls'] = [{'node': 'mix', 'property': 'progress',
        'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [.2, .8]}]
    for frame in (3, 19, 3):
        result = compare(native, frame=frame)
        assert pixels(result['images'][1]) == reference(path, document, 'mix', frame, 'fast')[0]
    assert document['effect_graph']['nodes'][2]['inputs'] == ['frame', 'background', 'frame']


@pytest.mark.parametrize('change,code', [
    ({'node': 'frame'}, 'node_invalid'), ({'node': 'PRIVATE_MISSING'}, 'node_invalid'),
    ({'node': True}, 'node_invalid'), ({'frame': True}, 'frame_invalid'),
    ({'frame': 1.25}, 'frame_invalid'), ({'frame': -1}, 'frame_invalid'),
    ({'frame': 120}, 'frame_invalid'), ({'quality': 'PRIVATE_INVALID'}, 'quality_invalid'),
])
def test_invalid_target_frame_and_quality_are_fixed_rejections(native, change, code):
    with pytest.raises(AuthoringPreviewError) as rejected:
        compare(native, **change)
    assert rejected.value.code == code and 'PRIVATE' not in str(rejected.value)
    owned = native[0].parent/'owned'
    assert not owned.exists() or not list(owned.iterdir())


def test_hidden_invalid_graph_branch_cannot_be_bypassed_by_selected_transition(native, monkeypatch):
    _, document = native
    document['effect_graph']['nodes'].append({'id': 'hidden', 'type': 'copy', 'inputs': ['missing']})
    def forbidden(*_args, **_kwargs):
        raise AssertionError('invalid disconnected branch reached compositor')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    with pytest.raises(AuthoringPreviewError) as rejected:
        compare(native)
    assert rejected.value.code == 'graph_invalid'


def test_source_change_during_render_rejects_and_closes_owned_resources(native, monkeypatch):
    path, document = native
    audio = path.parent/document['audio']['source']['path']
    original = audio.read_bytes()
    frame = Compositor.frame
    closed = []
    close = Compositor.close
    def changing(self, index, **kwargs):
        result = frame(self, index, **kwargs)
        audio.write_bytes(original[:-1]+b'\x01')
        return result
    def closing(self):
        closed.append(self)
        close(self)
    monkeypatch.setattr(Compositor, 'frame', changing)
    monkeypatch.setattr(Compositor, 'close', closing)
    try:
        with pytest.raises(AuthoringPreviewError) as rejected:
            compare(native)
        assert rejected.value.code == 'input_changed' and closed
        assert not list((path.parent/'owned').iterdir())
    finally:
        audio.write_bytes(original)


@pytest.mark.parametrize('limit', ['MAX_PREVIEW_PIXELS', 'MAX_IMAGE_BYTES', 'MAX_RESPONSE_BYTES'])
def test_size_bounds_fail_explicitly_and_remove_temporary_sidecars(native, monkeypatch, limit):
    import framerudiments.graph_authoring_preview as preview
    monkeypatch.setattr(preview, limit, 1)
    with pytest.raises(AuthoringPreviewError) as rejected:
        compare(native)
    assert rejected.value.code == 'preview_limit'
    assert not list((native[0].parent/'owned').iterdir())


def test_render_failure_closes_compositor_and_removes_owned_inputs(native, monkeypatch):
    closed = []
    close = Compositor.close
    def fail(*_args, **_kwargs):
        raise RuntimeError('PRIVATE_NATIVE_PATH')
    def closing(self):
        closed.append(self)
        close(self)
    monkeypatch.setattr(Compositor, 'frame', fail)
    monkeypatch.setattr(Compositor, 'close', closing)
    with pytest.raises(AuthoringPreviewError) as rejected:
        compare(native)
    assert rejected.value.code == 'render_failed' and 'PRIVATE' not in str(rejected.value)
    assert len(closed) == 1 and not list((native[0].parent/'owned').iterdir())


def test_sequential_visualiser_scope_is_explicit_without_launching_an_external_engine(native):
    _, original = native
    original['effects'] = [{'id': 'feedback', 'engine': 'butterchurn', 'preset': 'feedback', 'seed': 7}]
    # The original import must admit this read-only configuration.
    native[0].write_text(json.dumps(original), encoding='utf-8')
    result = compare(native)
    assert result['scope']['sequential_effects_excluded'] is True
    assert result['scope']['pipeline'] == 'native_graph_before_sequential_effects'


def test_edited_sidecars_bind_identity_and_are_removed_without_touching_originals(native):
    path, original = native
    cue = path.parent/'cues.lrc'
    cue.write_text('[00:00.100][SECTION: ORIGINAL]\n', encoding='utf-8')
    original['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    path.write_text(json.dumps(original), encoding='utf-8')
    before = path.read_bytes(), cue.read_bytes()
    sidecars = {'cues': [{'start': .2, 'label': '[SECTION: EDITED]'}]}
    result = compare(native, sidecars=sidecars)
    assert result['request_sha256'] == canonical_digest({'project': original, 'sidecars': sidecars})
    assert result['request_sha256'] != canonical_digest({'project': original, 'sidecars': {}})
    assert (path.read_bytes(), cue.read_bytes()) == before
    assert not list((path.parent/'owned').iterdir())
