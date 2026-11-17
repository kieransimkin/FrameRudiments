"""S1: durable, non-downgrading preview-cache contract (no GL/network needed)."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
import sqlite3

from PIL import Image
import pytest

from framerudiments.preview_stream_contract import QualityProfile, Segment
from framerudiments.preview_frame_cache import (
    CACHE_SCHEMA, CacheIdentityError, CacheIntegrityError, PreviewFrameCache,
)


PROJECT = 'a' * 64
PIPELINE = 'b' * 64
SETTINGS = 'c' * 64


def profiles():
    return tuple(QualityProfile(f'Q{n}', n, '1', 4, 4, 1, 1,
                                'synthetic_test_fixture') for n in range(6))


def segments(project=PROJECT):
    return (Segment('first', 0, 4, project), Segment('second', 4, 8, project))


def cache(root, *, quota=2**20, reserve=0, ram=2048):
    return PreviewFrameCache(root, project_fingerprint=PROJECT,
                             pipeline_fingerprint=PIPELINE, renderer_abi='test-renderer-v1',
                             segments=segments(), profiles=profiles(), max_bytes=quota,
                             min_free_bytes=reserve, ram_bytes=ram)


def png(seed=1):
    # Only *verified* RGBA8 PNG pixels are admissible, not arbitrary data.
    image = Image.new('RGBA', (4, 4))
    image.putdata([((i*17 + seed) % 256, (i*29+seed) % 256,
                    (i*37+seed) % 256, (i*11+seed) % 256) for i in range(16)])
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def test_q5_remains_after_q0_q4_out_of_order_and_same_rank_conflicts(tmp_path):
    store = cache(tmp_path/'preview')
    low = store.ticket(0, 'Q0')
    high = store.ticket(0, 'Q5')
    assert store.publish(high, png(5), settings_sha256=SETTINGS) == 'inserted'
    assert store.publish(low, png(1), settings_sha256=SETTINGS) == 'unchanged'
    assert store.publish(store.ticket(0, 'Q4'), png(4), settings_sha256=SETTINGS) == 'unchanged'
    hit = store.get(0)
    assert hit.quality_rank == 5 and hit.profile_id == 'Q5'
    assert hit.png == png(5) and hit.production_equivalent is False
    assert store.publish(high, png(5), settings_sha256=SETTINGS) == 'unchanged'
    with pytest.raises(CacheIntegrityError, match='same-rank'):
        store.publish(high, png(7), settings_sha256=SETTINGS)
    assert store.get(0).png == png(5)
    assert store.stats()['frames'] == 1


def test_insert_then_upgrade_preserves_only_best_and_deduplicates(tmp_path):
    store = cache(tmp_path)
    assert store.publish(store.ticket(0,'Q0'), png(1), settings_sha256=SETTINGS) == 'inserted'
    assert store.publish(store.ticket(0,'Q5'), png(2), settings_sha256=SETTINGS) == 'upgraded'
    assert store.get(0).png == png(2)
    assert store.publish(store.ticket(1,'Q5'), png(2), settings_sha256=SETTINGS) == 'inserted'
    assert store.stats()['frames'] == 2
    assert store.stats()['disk_bytes'] == len(png(2))  # content-addressed dedup
    assert len(list((tmp_path/'blobs').rglob('*.png'))) == 1
    assert store.stats()['upgrades'] == 1


def test_generation_fence_after_global_effect_edit(tmp_path):
    store = cache(tmp_path)
    stale = store.ticket(3, 'Q5')
    store.publish(store.ticket(4,'Q5'), png(2), settings_sha256=SETTINGS)
    store.invalidate_all()  # S1 is conservative until S5 proves local closure
    assert store.get(4) is None
    assert store.publish(stale, png(3), settings_sha256=SETTINGS) == 'stale'
    assert store.ticket(3, 'Q5').generation == stale.generation + 1
    assert store.get(3) is None
    assert store.stats()['stale_rejected'] == 1


def test_revised_pipeline_invalidates_all_sections_no_stale_old_instances(tmp_path):
    store = cache(tmp_path)
    old = store.ticket(1, 'Q5')
    store.publish(old, png(1), settings_sha256=SETTINGS)
    other = cache(tmp_path)
    new_project = 'd' * 64
    store.revise_all(project_fingerprint=new_project, pipeline_fingerprint='e'*64,
                     segments=segments(new_project))
    assert other.get(1) is None
    assert other.publish(old, png(4), settings_sha256=SETTINGS) == 'stale'
    assert store.get(1) is None
    assert store.ticket(1,'Q0').generation >= 1
    with pytest.raises(CacheIdentityError):
        cache(tmp_path)
    replacement = PreviewFrameCache(tmp_path, project_fingerprint=new_project,
                                    pipeline_fingerprint='e'*64, renderer_abi='test-renderer-v1',
                                    segments=tuple(replace(s,generation=store.ticket(1,'Q0').generation)
                                                   for s in segments(new_project)), profiles=profiles())
    assert replacement.get(1) is None


def test_corrupted_blob_is_never_served_and_recovery_discards_orphans(tmp_path):
    store = cache(tmp_path)
    ticket = store.ticket(0, 'Q5')
    store.publish(ticket, png(2), settings_sha256=SETTINGS)
    blob = next((tmp_path/'blobs').rglob('*.png'))
    blob.write_bytes(b'corrupted')
    assert store.get(0) is None
    assert store.stats()['corruptions'] == 1
    assert list((tmp_path/'blobs').rglob('*.png')) == []
    store.publish(ticket, png(3), settings_sha256=SETTINGS)
    stray = tmp_path/'staging'/'abc.tmp'
    stray.write_bytes(b'uncommitted staging data')
    orphan_content = png(99)
    orphan_sha = sha256(orphan_content).hexdigest()
    orphan = tmp_path/'blobs'/orphan_sha[:2]/(orphan_sha+'.png')
    orphan.parent.mkdir(exist_ok=True)
    orphan.write_bytes(orphan_content)
    restored = cache(tmp_path)
    assert restored.get(0).png == png(3)
    assert not stray.exists() and not orphan.exists()
    # A crashed writer's orphan is not a cache hit, even when it is a valid PNG.


def test_quota_eviction_is_whole_frame_never_downgrades_rank(tmp_path):
    size = len(png(1))
    store = cache(tmp_path, quota=2*size+4)
    for n in (0, 1):
        assert store.publish(store.ticket(n,'Q5'), png(n+1), settings_sha256=SETTINGS) == 'inserted'
    assert store.publish(store.ticket(5,'Q1'), png(8), settings_sha256=SETTINGS) == 'inserted'
    assert store.stats()['disk_bytes'] <= 2*size + 4
    # Older nonactive entries are removed, not demoted to Q0.
    assert store.get(5).quality_rank == 1
    assert sum(store.get(n) is not None for n in (0,1)) <= 1
    assert store.stats()['evictions'] >= 1
    assert len(list((tmp_path/'blobs').rglob('*.png'))) <= 2


def test_oversized_frame_or_too_small_disk_quota_rejected_without_eviction(tmp_path):
    store = cache(tmp_path, quota=len(png())-1)
    ticket = store.ticket(0,'Q5')
    assert store.publish(ticket, png(), settings_sha256=SETTINGS) == 'quota_rejected'
    assert store.get(0) is None
    with pytest.raises(ValueError, match='exact RGBA8'):
        buf=BytesIO()
        Image.new('RGB', (4,4), 'red').save(buf, format='PNG')
        store.publish(ticket, buf.getvalue(), settings_sha256=SETTINGS)
    with pytest.raises(ValueError, match='PNG'):
        store.publish(ticket, b'bad', settings_sha256=SETTINGS)
    with pytest.raises(ValueError):
        cache(tmp_path/'wrong', quota=0)


def test_many_threaded_out_of_order_instances_and_reopen(tmp_path):
    a, b = cache(tmp_path), cache(tmp_path)
    tickets = [a.ticket(1, f'Q{n}') for n in range(6)]
    def writer(n):
        store = a if n%2 else b
        return store.publish(tickets[n], png(n+1), settings_sha256=SETTINGS)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(writer, range(6)))
    assert any(x in {'inserted','upgraded'} for x in results)
    assert cache(tmp_path).get(1).quality_rank == 5
    assert len(list((tmp_path/'blobs').rglob('*.png'))) == 1
    # The persistent index contains one row per frame, not one per quality tier.
    with sqlite3.connect(tmp_path/'index.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM frames').fetchone()[0] == 1


def test_root_rejects_symlinks_and_foreign_manifest(tmp_path):
    outside = tmp_path/'outside'
    outside.mkdir()
    link = tmp_path/'link'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass  # Some Windows runners cannot create symlinks without privileges.
    else:
        with pytest.raises(ValueError, match='symlink'):
            cache(link)
    store = cache(outside)
    with pytest.raises(CacheIdentityError):
        PreviewFrameCache(outside, project_fingerprint='f'*64, pipeline_fingerprint=PIPELINE,
                          renderer_abi='test-renderer-v1',segments=segments('f'*64),profiles=profiles())
    assert store.stats()['schema'] == CACHE_SCHEMA
    assert '/private/' not in json.dumps(store.stats())


def test_frame_bounds_profile_identity_and_no_explicit_segment_specific_invalidation(tmp_path):
    store = cache(tmp_path)
    for bad in (-1, 8, True, 1.0):
        with pytest.raises(ValueError):
            store.ticket(bad,'Q0')
    with pytest.raises(ValueError):
        store.ticket(1,'Q9')
    ticket=store.ticket(1,'Q3')
    assert ticket.segment_id=='first' and ticket.frame==1
    assert ticket.profile_id=='Q3' and ticket.quality_rank==3
    assert store.get(1) is None
    assert not hasattr(store,'invalidate_segment')  # S5 adds proven scoped invalidation


def test_foreign_generation_and_settings_cannot_be_reused(tmp_path):
    store = cache(tmp_path)
    ticket=store.ticket(2,'Q5')
    store.publish(ticket, png(1), settings_sha256=SETTINGS)
    with pytest.raises(CacheIntegrityError):
        store.publish(ticket, png(1), settings_sha256='d'*64)
    assert store.get(2).png == png(1)
    altered=replace(ticket, generation=ticket.generation+1)
    assert store.publish(altered, png(2), settings_sha256=SETTINGS)=='stale'
    store.invalidate_all()
    with pytest.raises(CacheIdentityError):
        PreviewFrameCache(tmp_path, project_fingerprint=PROJECT,
                         pipeline_fingerprint='f'*64,renderer_abi='test-renderer-v1',
                         segments=segments(),profiles=profiles())


def test_durable_generations_survive_process_restart(tmp_path):
    a = cache(tmp_path)
    a.invalidate_all()
    old_generation = a.ticket(2, 'Q3').generation
    assert old_generation == 1
    del a
    # Native project ledger still begins at generation zero. The database
    # *must* retain the live generation rather than rejecting a reopen.
    b = cache(tmp_path)
    assert b.ticket(2, 'Q3').generation == old_generation
    b.publish(b.ticket(2, 'Q3'), png(5), settings_sha256=SETTINGS)
    b.invalidate_all()
    c = cache(tmp_path)
    assert c.ticket(2, 'Q5').generation == old_generation + 1
    assert c.get(2) is None


def test_memory_hit_still_checks_disk_identity_and_detects_tampering(tmp_path):
    store = cache(tmp_path, ram=2048)
    store.publish(store.ticket(0, 'Q5'), png(4), settings_sha256=SETTINGS)
    assert store.get(0).png == png(4)
    assert store.get(0).png == png(4)  # RAM fast path after verified read
    assert store.stats()['ram_bytes'] >= len(png(4))
    blob = next((tmp_path/'blobs').rglob('*.png'))
    blob.write_bytes(b'changed on disk after RAM hit')
    assert store.get(0) is None
    assert store.stats()['ram_bytes'] == 0


def test_corrupt_index_header_fails_closed_without_serving_media(tmp_path):
    store = cache(tmp_path)
    store.publish(store.ticket(0, 'Q5'), png(), settings_sha256=SETTINGS)
    db = tmp_path/'index.sqlite3'
    with sqlite3.connect(db) as handle:
        handle.execute('UPDATE meta SET value=? WHERE key=?', ('f'*64, 'project_hash'))
    assert store.get(0) is None
    with pytest.raises(CacheIdentityError):
        cache(tmp_path)

def test_profile_definition_changes_need_explicit_revision(tmp_path):
    cache(tmp_path)
    changed = list(profiles())
    changed[-1] = replace(changed[-1], effect_policy='different_shader_ABI')
    with pytest.raises(CacheIdentityError):
        PreviewFrameCache(tmp_path, project_fingerprint=PROJECT, pipeline_fingerprint=PIPELINE,
                          renderer_abi='test-renderer-v1', segments=segments(),
                          profiles=changed)


def test_smaller_quota_on_restart_eviction_is_not_a_quality_downgrade(tmp_path):
    full=cache(tmp_path, quota=2**20)
    full.publish(full.ticket(0,'Q5'), png(1), settings_sha256=SETTINGS)
    full.publish(full.ticket(1,'Q5'), png(2), settings_sha256=SETTINGS)
    smaller=cache(tmp_path, quota=len(png(1))+5)
    assert smaller.stats()['disk_bytes'] <= len(png(1))+5
    assert smaller.stats()['evictions'] >= 1
    assert smaller.get(0) is None or smaller.get(0).quality_rank == 5
    assert smaller.get(1) is None or smaller.get(1).quality_rank == 5


def test_tampered_frame_metadata_is_never_returned(tmp_path):
    a=cache(tmp_path)
    a.publish(a.ticket(0,'Q5'), png(3), settings_sha256=SETTINGS)
    with sqlite3.connect(tmp_path/'index.sqlite3') as db:
        db.execute('UPDATE frames SET profile_id=? WHERE frame=0', ('Q1',))
    assert a.get(0) is None
    assert cache(tmp_path).get(0) is None


def test_revise_quality_and_renderer_abi_rejects_old_revision(tmp_path):
    store = cache(tmp_path)
    old_ticket = store.ticket(1, 'Q5')
    new_project = 'e' * 64
    changed = list(profiles())
    changed[5] = replace(changed[5], output_width=6)
    store.revise_all(project_fingerprint=new_project, pipeline_fingerprint='f'*64,
                     segments=segments(new_project), profiles=changed,
                     renderer_abi='test-renderer-v2')
    assert store.ticket(1, 'Q5').width == 6
    assert store.publish(old_ticket, png(), settings_sha256=SETTINGS) == 'stale'
    reopened = PreviewFrameCache(tmp_path, project_fingerprint=new_project,
                                 pipeline_fingerprint='f'*64, renderer_abi='test-renderer-v2',
                                 segments=segments(new_project), profiles=changed)
    assert reopened.ticket(1, 'Q5').width == 6
