"""S5 end-to-end stream admissions with real cache and explicit test renderers."""
from copy import deepcopy
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from framerudiments.preview_stream_s2 import PreviewStreamAPI, SeekStream
from framerudiments.preview_quality import profile_capabilities
from test_preview_dependencies import project
from test_preview_segment_cache import png, populate, store
from test_preview_stream_s2 import wait_ready

CLIENT = 'synthetic_edit_client_1234'


def open_retry(api, body):
    until = monotonic() + 4
    while True:
        try:
            return api.open(body)
        except RuntimeError as exc:
            if 'retiring' not in str(exc) or monotonic() >= until:
                raise
            sleep(.005)


def make_api(tmp_path, p, calls, **overrides):
    def factory(_path, draft, _sidecars, **_kwargs):
        def render(frame, tier):
            calls.append((frame, tier))
            size = int(tier[1:])
            edge = [4, 8, 12, 16, 16, 16][size]
            return png((edge, edge), seed=frame)
        return render
    return PreviewStreamAPI(tmp_path/'project.json', lambda *_: None, lambda *_: None,
        cache_root=tmp_path/'cache', project_loader=overrides.get('loader', lambda _: p),
        adaptive_renderer_factory=overrides.get('factory', factory))


def test_edit_reuses_same_workspace_cache_retains_q5_and_emits_receipt(tmp_path):
    p, calls = project(), []
    api = make_api(tmp_path, p, calls)
    first = {'project': p.data, 'adaptive': True, 'frame': 0,
             'client_id': CLIENT, 'edit_sequence': 1}
    try:
        reply = api.open(first)
        old = api.get(reply['session'])
        wait_ready(old, 0)
        populate(old.cache, range(1, 30))
        kept = old.cache.get(29)
        before_calls = len(calls)
        draft = deepcopy(p.data)
        draft['layers'][0]['opacity'] = .9
        reply = open_retry(api, {**first, 'project': draft, 'edit_sequence': 2, 'frame': 29})
        new = api.get(reply['session'])
        hit = wait_ready(new, 0)
        assert new.cache.root == old.cache.root
        assert hit['cache_hit'] and new.cache.get(29) == kept
        assert len(calls) == before_calls
        assert new.cache.get(10) is None and new.cache.get(19) is None
        assert reply['invalidation']['invalidated_frames'] == 10
        assert any(e['type'] == 'cache-invalidated' for e in new.since(0, timeout=0))
        with pytest.raises(ValueError, match='invalid stream session'):
            api.get(old.token)
        with pytest.raises(ValueError, match='closed'):
            old.image(29, kept.generation, kept.image_sha256)
    finally:
        api.shutdown()


def test_same_client_older_or_conflicting_edits_cannot_replace_latest_session(tmp_path):
    p, calls = project(), []
    api = make_api(tmp_path, p, calls)
    body = {'project': p.data, 'adaptive': True, 'client_id': CLIENT, 'edit_sequence': 2}
    try:
        reply = api.open(body)
        wait_ready(api.active, 0)
        with pytest.raises(ValueError, match='superseded'):
            api.open({**body, 'edit_sequence': 1})
        with pytest.raises(ValueError, match='conflicting'):
            api.open({**body, 'project': {**p.data, 'background': {'colour': '#000000'}}})
        assert api.active.token == reply['session']
        retried = open_retry(api, body)
        assert retried['invalidation']['cause'] == 'unchanged'
    finally:
        api.shutdown()


def test_slow_older_admission_never_replaces_faster_new_draft(tmp_path):
    p, calls = project(), []
    entered, release = Event(), Event()
    counter = [0]
    def loader(_):
        counter[0] += 1
        if counter[0] == 1:
            entered.set()
            assert release.wait(3)
        return p
    api = make_api(tmp_path, p, calls, loader=loader)
    body = {'project': p.data, 'adaptive': True, 'client_id': CLIENT, 'edit_sequence': 1}
    errors = []
    def older():
        try:
            api.open(body)
        except ValueError as exc:
            errors.append(str(exc))
    task = Thread(target=older)
    task.start()
    try:
        assert entered.wait(2)
        newer = api.open({**body, 'edit_sequence': 2})
        release.set()
        task.join(3)
        assert not task.is_alive()
        assert errors == ['superseded draft admission']
        assert api.active.token == newer['session']
    finally:
        release.set()
        task.join(3)
        api.shutdown()


def test_edit_waits_for_old_foreground_owner_and_old_result_cannot_publish(tmp_path):
    p, calls = project(), []
    entered, release = Event(), Event()
    factories = [0]
    def factory(*_args, **_kwargs):
        factories[0] += 1
        first = factories[0] == 1
        def render(frame, tier):
            if first:
                entered.set()
                assert release.wait(4)
            return png(seed=frame)
        return render
    api = make_api(tmp_path, p, calls, factory=factory)
    body = {'project': p.data, 'adaptive': True, 'frame': 11,
            'client_id': CLIENT, 'edit_sequence': 1}
    try:
        api.open(body)
        old = api.active
        assert entered.wait(2)
        draft = deepcopy(p.data)
        draft['layers'][0]['opacity'] = .1
        edit = {**body, 'project': draft, 'edit_sequence': 2}
        with pytest.raises(RuntimeError, match='retiring'):
            api.open(edit)
        assert old.closed and old.cache.get(11) is None
        release.set()
        old.worker.join(3)
        reply = open_retry(api, edit)
        wait_ready(api.active, 0)
        assert reply['invalidation']['cancelled_jobs'] >= 1
        assert api.active.cache.get(11).generation > 0
    finally:
        release.set()
        api.shutdown()


def test_source_revocation_has_one_generation_bump_and_clears_entire_cache(tmp_path):
    p = project()
    cache = store(tmp_path, p)
    populate(cache)
    changed = [False]
    def guard():
        if changed[0]:
            raise ValueError('synthetic external source change')
    stream = SeekStream(cache, lambda *_: png(), p.frame_count,
                        capabilities=profile_capabilities(p), verifier=guard)
    try:
        stream.command('seek', sequence=0, frame=0)
        old = wait_ready(stream, 0)
        changed[0] = True
        for seq in range(1, 5):
            with pytest.raises(ValueError, match='Pinned source changed'):
                stream.command('seek', sequence=seq, frame=0)
        assert cache.stats()['frames'] == 0
        assert cache.stats()['section_revision'] == 1
        with pytest.raises(ValueError, match='Pinned source changed'):
            stream.image(0, old['generation'], old['image_sha256'])
        events = [e for e in stream.since(0, timeout=0) if e['type'] == 'cache-invalidated']
        assert len(events) == 1
        assert events[0]['invalidation']['invalidated_frames'] == 30
        assert events[0]['invalidation']['cause'] == 'pinned_source_revoked'
    finally:
        stream.close()


@pytest.mark.parametrize('revision', [0, -1, True, 1.5, '1', 2**53])
def test_malformed_edit_sequences_fail_before_any_cache_writes(tmp_path, revision):
    api = make_api(tmp_path, project(), [])
    with pytest.raises(ValueError, match='edit sequence'):
        api.open({'project': project().data, 'client_id': CLIENT, 'edit_sequence': revision})
    assert not (tmp_path/'cache').exists()
