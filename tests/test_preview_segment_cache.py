"""S5 real SQLite/PNG admission, revision races and monotonic retention."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
import sqlite3
from threading import Event

from PIL import Image
import pytest

from framerudiments.preview_frame_cache import PreviewFrameCache, CacheIdentityError
from framerudiments.preview_segment_cache import SectionFrameCache
from framerudiments.preview_stream_contract import quality_profiles
from test_preview_dependencies import manifest, project


def png(size=(16, 16), seed=0):
    out = BytesIO()
    Image.new('RGBA', size, (seed % 256, 70, 140, 255)).save(out, 'PNG')
    return out.getvalue()


def store(path, p, data=None, **limits):
    return SectionFrameCache(path, manifest=manifest(p, data), profiles=quality_profiles(p),
                             renderer_abi='test-s5', max_bytes=2**20, **limits)


def populate(cache, frames=range(30)):
    for n in frames:
        ticket = cache.ticket(n, 'Q5')
        assert cache.publish(ticket, png(seed=n), settings_sha256=cache.settings_identity(ticket)) == 'inserted'


def test_local_edit_tombstones_entire_section_and_retains_other_q5_bytes(tmp_path):
    p = project()
    old = store(tmp_path, p)
    populate(old)
    unchanged = old.get(0), old.get(29)
    obsolete = old.ticket(11, 'Q5')
    snapshot = old.segment_snapshot()
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .8
    revised = store(tmp_path, p, draft)
    assert [n for n in range(30) if revised.get(n) is None] == list(range(10, 20))
    assert (revised.get(0), revised.get(29)) == unchanged
    report = revised.last_revision
    assert report['invalidated_frames'] == 10
    assert report['invalidated_bytes'] == sum(len(png(seed=n)) for n in range(10, 20))
    assert len(report['affected_sections']) == 1 and len(report['retained_sections']) == 2
    assert report['cause'] == 'section_dependencies_changed'
    assert revised.ticket(11, 'Q5').generation > obsolete.generation
    assert revised.segment_snapshot()[0].generation == snapshot[0].generation
    # Old processes cannot read or publish under their retired revision.
    assert old.get(0) is None
    assert old.publish(obsolete, png(seed=11), settings_sha256=old.settings_identity(obsolete)) == 'stale'
    # A re-render at unchanged Q5 has the same settings identity, not an entire-draft hash.
    again = revised.ticket(0, 'Q5')
    assert revised.publish(again, unchanged[0].png, settings_sha256=revised.settings_identity(again)) == 'unchanged'
    low = revised.ticket(0, 'Q0')
    assert revised.publish(low, png((low.width, low.height)), settings_sha256=revised.settings_identity(low)) == 'unchanged'


def test_unchanged_reopen_has_no_generation_changes_and_old_handles_remain_valid(tmp_path):
    p = project()
    a = store(tmp_path, p)
    populate(a, [3, 13, 23])
    b = store(tmp_path, p)
    assert b.last_revision['cause'] == 'unchanged'
    assert b.last_revision['invalidated_frames'] == 0
    assert b.segment_snapshot() == a.segment_snapshot()
    assert a.get(13) == b.get(13)


def test_undo_uses_new_generation_and_never_resurrects_old_frame_url(tmp_path):
    p = project()
    a = store(tmp_path, p)
    populate(a, [13])
    before = a.ticket(13, 'Q5')
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .9
    b = store(tmp_path, p, draft)
    middle_generation = b.ticket(13, 'Q5').generation
    restored = store(tmp_path, p)
    assert restored.get(13) is None
    assert restored.ticket(13, 'Q5').generation > middle_generation
    assert restored.ticket(13, 'Q5').generation >= before.generation + 2
    assert restored.publish(before, png(seed=13), settings_sha256=a.settings_identity(before)) == 'stale'


def test_boundary_move_replaces_overlapping_old_new_ranges_but_preserves_disjoint(tmp_path):
    p = project()
    old = store(tmp_path, p)
    populate(old)
    p.cues = [{'frame': n} for n in (0, 12, 20)]
    new = store(tmp_path, p)
    assert new.last_revision['cause'] == 'boundaries_changed'
    assert new.last_revision['invalidated_frames'] == 20
    assert all(new.get(n) is None for n in range(20))
    assert all(new.get(n).profile_id == 'Q5' for n in range(20, 30))
    with sqlite3.connect(new.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM frames WHERE frame<20').fetchone()[0] == 0
        assert db.execute('SELECT MIN(first_frame),MAX(end_frame) FROM segments').fetchone() == (0, 30)


def test_returning_removed_boundary_cannot_reuse_generation(tmp_path):
    p = project()
    a = store(tmp_path, p)
    ticket = a.ticket(11, 'Q5')
    p.cues = [{'frame': 0}, {'frame': 20}]
    store(tmp_path, p)
    p.cues = [{'frame': n} for n in (0, 10, 20)]
    c = store(tmp_path, p)
    assert c.ticket(11, 'Q5').generation > ticket.generation
    assert c.publish(ticket, png(seed=1), settings_sha256=c.settings_identity(ticket)) == 'stale'


def test_transaction_rollback_preserves_original_index(tmp_path, monkeypatch):
    p = project()
    old = store(tmp_path, p)
    populate(old, [1, 12, 22])
    original_connect = SectionFrameCache._connect
    class Failure:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, key):
            return getattr(self.connection, key)
        def executemany(self, sql, values):
            if 'INSERT INTO section_hashes' in sql:
                raise OSError('synthetic interrupted transaction')
            return self.connection.executemany(sql, values)
    monkeypatch.setattr(SectionFrameCache, '_connect', lambda self: Failure(original_connect(self)))
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .1
    with pytest.raises(OSError, match='synthetic'):
        store(tmp_path, p, draft)
    monkeypatch.undo()
    for n in (1, 12, 22):
        assert old.get(n) is not None
    assert store(tmp_path, p).last_revision['cause'] == 'unchanged'


def test_two_process_handles_cannot_commit_after_section_revision(tmp_path):
    p = project()
    first, writer = store(tmp_path, p), store(tmp_path, p)
    tickets = [writer.ticket(n, 'Q5') for n in (2, 12, 22)]
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .1
    new = store(tmp_path, p, draft)
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda t: writer.publish(t, png(seed=t.frame),
                                        settings_sha256=writer.settings_identity(t)), tickets))
    assert results == ['stale'] * 3
    assert first.get(2) is None
    assert new.stats()['frames'] == 0


def test_corrupt_retained_frame_is_not_preserved_through_revision(tmp_path):
    p = project()
    old = store(tmp_path, p)
    populate(old, [0, 10, 20])
    blob = old._blob(old.get(0).image_sha256)
    blob.write_bytes(b'broken')
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .3
    new = store(tmp_path, p, draft)
    assert new.get(0) is None and new.get(10) is None and new.get(20) is not None


def test_profile_or_shared_pipeline_change_invalidates_every_section(tmp_path):
    p = project()
    old = store(tmp_path, p)
    populate(old, [0, 10, 20])
    profiles = tuple(replace(q, effect_policy='new_ladder') for q in quality_profiles(p))
    new = SectionFrameCache(tmp_path, manifest=manifest(p), profiles=profiles, renderer_abi='test-s5')
    assert new.stats()['frames'] == 0
    assert new.last_revision['cause'] == 'quality_or_renderer_changed'
    populate(new, [0, 10, 20])
    draft = {**p.data, 'background': {'colour': '#ffffff'}}
    revised = store(tmp_path, p, draft)
    assert revised.stats()['frames'] == 0


def test_schema_is_not_silently_opened_by_legacy_s1(tmp_path):
    p = project()
    current = store(tmp_path, p)
    with pytest.raises(CacheIdentityError):
        PreviewFrameCache(tmp_path, project_fingerprint=current.project_hash,
                          pipeline_fingerprint=current.pipeline_hash, renderer_abi=current.renderer_abi,
                          segments=current.segment_snapshot(), profiles=current.profiles)
    with pytest.raises(ValueError, match='complete new dependency'):
        current.revise_all()


def test_receipt_is_durable_and_contains_no_private_source_values(tmp_path):
    p = project()
    old = store(tmp_path, p)
    populate(old, [12])
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .1
    new = store(tmp_path, p, draft)
    with sqlite3.connect(new.db_path) as db:
        receipt = json.loads(db.execute('SELECT value FROM meta WHERE key="last_invalidation"').fetchone()[0])
    assert receipt == new.last_revision
    assert 'opacity' not in json.dumps(receipt)


def test_inplace_edit_preserves_verified_lru_without_full_recovery_and_fences_stale_owner(tmp_path, monkeypatch):
    p = project()
    a = store(tmp_path, p)
    populate(a, [0, 12, 22])
    other = store(tmp_path, p)
    kept = a.get(22)
    old = a.ticket(12, 'Q5')
    monkeypatch.setattr(a, 'recover', lambda: pytest.fail('live edits must not decode the entire cache'))
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .6
    report = a.revise(manifest(p, draft), renderer_abi='test-s5', profiles=quality_profiles(p))
    assert report['invalidated_frames'] == 1
    assert a.get(22) == kept and a.get(12) is None
    assert a.publish(old, png(), settings_sha256=a.settings_identity(old)) == 'stale'
    with pytest.raises(CacheIdentityError, match='another owner'):
        other.revise(manifest(p), renderer_abi='test-s5', profiles=quality_profiles(p))


def test_inplace_failed_revision_restores_owner_identity(tmp_path, monkeypatch):
    p = project()
    a = store(tmp_path, p)
    populate(a, [2, 12, 22])
    before = a.pipeline_hash, a.manifest
    initialize = a._initialize
    def fail():
        raise OSError('synthetic pre-commit failure')
    monkeypatch.setattr(a, '_initialize', fail)
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .6
    with pytest.raises(OSError):
        a.revise(manifest(p, draft), renderer_abi='test-s5', profiles=quality_profiles(p))
    assert (a.pipeline_hash, a.manifest) == before
    monkeypatch.setattr(a, '_initialize', initialize)
    assert a.get(12) is not None


def test_publication_serializes_with_revision_and_cannot_restore_deleted_section(tmp_path, monkeypatch):
    p = project()
    writer = store(tmp_path, p)
    owner = store(tmp_path, p)
    ticket = writer.ticket(12, 'Q5')
    entered, release, revision_started = Event(), Event(), Event()
    write = writer._write_blob
    def blocked(data, digest):
        entered.set()
        assert release.wait(5), 'publication test barrier timed out'
        write(data, digest)
    monkeypatch.setattr(writer, '_write_blob', blocked)
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .8
    def revise():
        revision_started.set()
        return owner.revise(manifest(p, draft), renderer_abi='test-s5', profiles=quality_profiles(p))
    with ThreadPoolExecutor(2) as pool:
        published = pool.submit(writer.publish, ticket, png(seed=12),
                                settings_sha256=writer.settings_identity(ticket))
        try:
            assert entered.wait(5)
            revised = pool.submit(revise)
            assert revision_started.wait(5)
        finally:
            release.set()
        assert published.result(timeout=5) == 'inserted'
        receipt = revised.result(timeout=5)
    assert receipt['invalidated_frames'] == 1
    assert owner.get(12) is None
    assert writer.publish(ticket, png(seed=12), settings_sha256=writer.settings_identity(ticket)) == 'stale'
