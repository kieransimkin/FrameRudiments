"""S5 transactional section revision on the existing lossless S1 pixel store.

A separate schema/namespace prevents v1 clients from opening section-aware
indices. PNG validation, atomic blob writes, monotonic rank CAS, quotas and
recovery are inherited rather than replaced. No claimed 'affected range' is
accepted: admission requires a complete server-built dependency manifest.
"""
from contextlib import closing
from dataclasses import asdict
import json

from .preview_dependencies import DependencyIndex
from .preview_frame_cache import CacheIdentityError, PreviewFrameCache
from .preview_stream_contract import canonical_digest

SCHEMA = 'framerudiments.preview-section-cache.v1'
RECEIPT_SCHEMA = 'framerudiments.preview-invalidation.v1'
MAX_GENERATION = 2**63 - 1


class SectionFrameCache(PreviewFrameCache):
    scoped_dependencies = True

    def __init__(self, root, *, manifest, renderer_abi, profiles, **limits):
        if not isinstance(manifest, DependencyIndex):
            raise ValueError('a server-built dependency manifest is required')
        if not manifest.sections or len(manifest.sections) > 2048:
            raise ValueError('invalid section manifest size')
        self.manifest = manifest
        self.last_revision = None
        super().__init__(root, project_fingerprint=manifest.namespace,
                         pipeline_fingerprint=manifest.digest, renderer_abi=renderer_abi,
                         segments=manifest.segments(), profiles=profiles, **limits)

    def _initialize(self):
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self._create_tables(db)
                db.execute('''CREATE TABLE IF NOT EXISTS section_hashes (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)''')
                values = dict(db.execute('SELECT key,value FROM meta'))
                expected_revision = getattr(self, '_expected_revision', None)
                if expected_revision is not None and values.get('revision_counter') != str(expected_revision):
                    raise CacheIdentityError('section cache was revised by another owner')
                old_sections = {row['id']: dict(row) for row in db.execute('SELECT * FROM segments')}
                old_hashes = dict(db.execute('SELECT id,fingerprint FROM section_hashes'))
                if values:
                    if values.get('schema') != SCHEMA or values.get('project_hash') != self.project_hash:
                        raise CacheIdentityError('section cache belongs to another workspace/schema')
                    counter = values.get('revision_counter', '')
                    if not counter.isascii() or not counter.isdecimal() or len(counter) > 19:
                        raise CacheIdentityError('invalid section revision counter')
                    revision = int(counter)
                    if not 0 <= revision <= MAX_GENERATION:
                        raise CacheIdentityError('section revision counter outside bounds')
                    if set(old_sections) != set(old_hashes):
                        raise CacheIdentityError('incomplete persisted dependency manifest')
                    for row in old_sections.values():
                        if type(row['generation']) is not int or not 0 <= row['generation'] <= revision:
                            raise CacheIdentityError('invalid persisted section generation')
                    try:
                        previous_components = dict(json.loads(values['dependency_components']))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise CacheIdentityError('invalid persisted dependency components') from exc
                else:
                    revision, previous_components = 0, {}
                    if old_sections or old_hashes or db.execute('SELECT 1 FROM frames LIMIT 1').fetchone():
                        raise CacheIdentityError('unowned cache contents')
                desired = {s.id: s for s in self.manifest.sections}
                profile_changed = bool(values) and values.get('profile_digest') != self.profile_digest
                abi_changed = bool(values) and values.get('renderer_abi') != self.renderer_abi
                retained = set()
                for name, section in desired.items():
                    before = old_sections.get(name)
                    if (before is not None and not profile_changed and not abi_changed
                            and (before['first_frame'], before['end_frame']) ==
                                (section.start_frame, section.end_frame_exclusive)
                            and old_hashes.get(name) == section.fingerprint):
                        retained.add(name)
                changed_old = set(old_sections) - retained
                changed_new = set(desired) - retained
                changed = (not values or self.pipeline_hash != values.get('pipeline_hash')
                           or bool(changed_old or changed_new) or profile_changed or abi_changed)
                # A single high-water counter also prevents a removed section
                # from returning later (undo/boundary restore) with an old URL.
                if changed and values:
                    if revision == MAX_GENERATION:
                        raise ValueError('section generation exhausted')
                    revision += 1
                affected = []
                removed_frames = removed_bytes = 0
                removed_ids = set()
                for name in sorted(changed_old | changed_new):
                    before, after = old_sections.get(name), desired.get(name)
                    count, occupied = db.execute(
                        'SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM frames WHERE segment_id=?',
                        (name,)).fetchone()
                    removed_frames += count
                    removed_bytes += occupied  # logical bytes; blobs may be shared/deduplicated
                    if before:
                        removed_ids.add(name)
                    affected.append(dict(segment_id=name,
                        old_range=[before['first_frame'], before['end_frame']] if before else None,
                        new_range=[after.start_frame, after.end_frame_exclusive] if after else None,
                        old_generation=before['generation'] if before else None,
                        new_generation=revision if after else None,
                        old_pipeline_hash=old_hashes.get(name),
                        new_pipeline_hash=after.fingerprint if after else None,
                        invalidated_frames=count, invalidated_bytes=occupied))
                next_components = dict(self.manifest.components)
                changed_components = sorted(k for k in set(previous_components) | set(next_components)
                                            if previous_components.get(k) != next_components.get(k))
                boundary_changed = bool(values) and {
                    (s['id'], s['first_frame'], s['end_frame']) for s in old_sections.values()
                } != {(s.id, s.start_frame, s.end_frame_exclusive) for s in desired.values()}
                cause = ('initial' if not values else 'boundaries_changed' if boundary_changed else
                         'quality_or_renderer_changed' if profile_changed or abi_changed else
                         'shared_pipeline_changed' if 'shared-pipeline' in changed_components else
                         'section_dependencies_changed' if changed_old or changed_new else
                         'nonvisual_revision' if changed else 'unchanged')
                receipt = dict(schema=RECEIPT_SCHEMA, revision=revision, cause=cause,
                               old_pipeline_hash=values.get('pipeline_hash'),
                               new_pipeline_hash=self.pipeline_hash,
                               changed_dependencies=changed_components,
                               affected_sections=affected, retained_sections=sorted(retained),
                               invalidated_frames=removed_frames, invalidated_bytes=removed_bytes,
                               byte_accounting='logical_frame_bytes_not_unique_blob_bytes',
                               cancelled_jobs=0, scope=self.manifest.scope)
                if changed:
                    for name in removed_ids:
                        db.execute('DELETE FROM frames WHERE segment_id=?', (name,))
                    db.execute('DELETE FROM segments')
                    db.execute('DELETE FROM section_hashes')
                    db.executemany('INSERT INTO segments VALUES (?,?,?,?)', [
                        (s.id, s.start_frame, s.end_frame_exclusive,
                         old_sections[s.id]['generation'] if s.id in retained else revision)
                        for s in self.manifest.sections])
                    db.executemany('INSERT INTO section_hashes VALUES (?,?)',
                                   [(s.id, s.fingerprint) for s in self.manifest.sections])
                    expected = dict(schema=SCHEMA, project_hash=self.project_hash,
                                    pipeline_hash=self.pipeline_hash, renderer_abi=self.renderer_abi,
                                    profile_digest=self.profile_digest, revision_counter=str(revision),
                                    dependency_components=json.dumps(self.manifest.components, separators=(',', ':')),
                                    last_invalidation=json.dumps(receipt, separators=(',', ':')))
                    db.executemany('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)', expected.items())
                db.execute('COMMIT')
                self._revision_token = revision
                self.last_revision = receipt
                self._segments = [(s.id, s.start_frame, s.end_frame_exclusive,
                                   old_sections[s.id]['generation'] if s.id in retained else revision)
                                  for s in self.manifest.sections]
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def revise(self, manifest, *, renderer_abi, profiles):
        """Atomically edit the live owner's manifest, without re-decoding every PNG.

        Normal reads still verify pixels/stat identities. Full corruption recovery
        remains mandatory on cold open. Call only after retiring the old render
        owner; other process handles are fenced by the durable revision counter.
        """
        if not isinstance(manifest, DependencyIndex) or manifest.namespace != self.project_hash:
            raise ValueError('revised manifest must belong to the same workspace')
        from .preview_frame_cache import _clean_segments
        from .preview_stream_contract import QualityProfile
        if not isinstance(renderer_abi, str) or not 1 <= len(renderer_abi) <= 128:
            raise ValueError('renderer ABI required')
        profiles = tuple(profiles)
        if len(profiles) != 6 or any(not isinstance(p, QualityProfile) or p.id != f'Q{i}'
                                     or p.quality_rank != i for i, p in enumerate(profiles)):
            raise ValueError('revised quality ladder must be Q0..Q5')
        clean = _clean_segments(manifest.segments(), self.project_hash)
        names = ('manifest', 'pipeline_hash', 'renderer_abi', 'profiles', 'profile_digest',
                 '_segments', '_revision_token', 'last_revision')
        with self._guard:
            previous = {name: getattr(self, name) for name in names}
            self._expected_revision = self._revision_token
            try:
                self.manifest, self.pipeline_hash = manifest, manifest.digest
                self.renderer_abi, self.profiles = renderer_abi, profiles
                self.profile_digest, self._segments = canonical_digest([asdict(p) for p in profiles]), clean
                self._initialize()
            except BaseException:
                for name, value in previous.items():
                    setattr(self, name, value)
                raise
            finally:
                del self._expected_revision
            for row in self.last_revision['affected_sections']:
                self._ram_forget_segment(row['segment_id'])
            # An unchanged open is a no-op, including blob/disk scans.
            if self.last_revision['affected_sections']:
                self.collect_garbage()
            return self.last_revision

    def _validate_identity(self, db):
        values = dict(db.execute('SELECT key,value FROM meta'))
        return (values.get('schema') == SCHEMA
                and values.get('project_hash') == self.project_hash
                and values.get('pipeline_hash') == self.pipeline_hash
                and values.get('renderer_abi') == self.renderer_abi
                and values.get('profile_digest') == self.profile_digest
                and values.get('revision_counter') == str(self._revision_token))

    def _pipeline_identity(self, db, segment):
        row = db.execute('SELECT fingerprint FROM section_hashes WHERE id=?', (segment['id'],)).fetchone()
        return row[0] if row else None

    def _frame_identity(self, db, segment, frame, profile):
        return canonical_digest({'schema': SCHEMA, 'workspace': self.project_hash,
                                 'section': segment['id'], 'generation': segment['generation'],
                                 'dependency': self._pipeline_identity(db, segment), 'frame': frame,
                                 'profile': asdict(profile), 'renderer_abi': self.renderer_abi})

    def settings_identity(self, ticket):
        """Shared foreground/background identity independent of unrelated edits."""
        return canonical_digest({'schema': SCHEMA, 'renderer': ticket.renderer_abi,
                                 'dependency': ticket.pipeline_fingerprint,
                                 'profile': asdict(self.profiles[ticket.quality_rank])})

    def invalidate_all(self):
        """Revoke unapproved source changes atomically, including a durable receipt."""
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not self._validate_identity(db):
                    raise CacheIdentityError('cache was revised externally')
                if self._revision_token >= MAX_GENERATION:
                    raise ValueError('section generation exhausted')
                revision = self._revision_token + 1
                affected = []
                for row in db.execute('SELECT * FROM segments ORDER BY first_frame').fetchall():
                    count, size = db.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM frames '
                                             'WHERE segment_id=?', (row['id'],)).fetchone()
                    fingerprint = self._pipeline_identity(db, row)
                    affected.append(dict(segment_id=row['id'], old_range=[row['first_frame'], row['end_frame']],
                        new_range=[row['first_frame'], row['end_frame']], old_generation=row['generation'],
                        new_generation=revision, old_pipeline_hash=fingerprint, new_pipeline_hash=fingerprint,
                        invalidated_frames=count, invalidated_bytes=size))
                receipt = dict(schema=RECEIPT_SCHEMA, revision=revision, cause='pinned_source_revoked',
                    old_pipeline_hash=self.pipeline_hash, new_pipeline_hash=self.pipeline_hash,
                    changed_dependencies=['unapproved_source_change'], affected_sections=affected,
                    retained_sections=[], invalidated_frames=sum(r['invalidated_frames'] for r in affected),
                    invalidated_bytes=sum(r['invalidated_bytes'] for r in affected), cancelled_jobs=0,
                    byte_accounting='logical_frame_bytes_not_unique_blob_bytes', scope='whole_project_revocation')
                db.execute('DELETE FROM frames')
                db.execute('UPDATE segments SET generation=?', (revision,))
                db.execute('UPDATE meta SET value=? WHERE key="revision_counter"', (str(revision),))
                db.execute('UPDATE meta SET value=? WHERE key="last_invalidation"',
                           (json.dumps(receipt, separators=(',', ':')),))
                db.execute('COMMIT')
                self._revision_token = revision
                self.last_revision = receipt
                self._ram_forget_segment()
                self.collect_garbage()
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def revise_all(self, **_kwargs):
        raise ValueError('section caches require a complete new dependency manifest')

    def stats(self):
        return {**super().stats(), 'schema': SCHEMA, 'section_revision': self._revision_token}
