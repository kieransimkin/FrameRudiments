"""S1: opt-in monotonic, generation-fenced preview frame cache.

Persistent PNG *pixel* cache, separate from WorkingCache and final renders.
SQLite serialises cross-process CAS/index transactions; fsync'ed immutable
content-addressed PNGs are committed before the index references them.
No workers, streaming service, or GPU context are created by this module.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import sqlite3
from threading import RLock
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from .preview_stream_contract import (
    QualityProfile, Segment, _hex, _integer, canonical_digest, frame_cache_identity,
)

CACHE_SCHEMA = 'framerudiments.preview-frame-cache.v1'
FRAME_SCHEMA = 'framerudiments.preview-frame.v1'
MAX_PNG_BYTES = 256 * 1024 * 1024
MAX_FRAMES = 100_000
MAX_CACHE_BYTES = 64 * 1024**3
_DEFAULT_DISK = 2 * 1024**3
_DEFAULT_RAM = 32 * 1024**2
_HEX = re.compile(r'[0-9a-f]{64}\Z')


class CacheIdentityError(ValueError):
    """Existing storage belongs to another project/pipeline or is malformed."""


class CacheIntegrityError(ValueError):
    """Two verified results disagree at the same frame and quality rank."""


@dataclass(frozen=True)
class FrameTicket:
    """An immutable render-time identity; stale tickets cannot be admitted."""
    project_fingerprint: str
    pipeline_fingerprint: str
    renderer_abi: str
    segment_id: str
    generation: int
    frame: int
    profile_id: str
    quality_rank: int
    width: int
    height: int
    cache_key: str
    schema: str = FRAME_SCHEMA


@dataclass(frozen=True)
class CachedFrame:
    frame: int
    segment_id: str
    generation: int
    profile_id: str
    quality_rank: int
    width: int
    height: int
    pixel_sha256: str
    image_sha256: str
    png: bytes
    # No production approval is implied by a cached preview tier.
    production_equivalent: bool = False
    schema: str = FRAME_SCHEMA


def _fsync_directory(directory):
    """Best effort directory durability; regular fsync of files is mandatory."""
    if os.name != 'posix':
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _verified_png(data, width, height):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_PNG_BYTES:
        raise ValueError('cache image must be bounded PNG bytes')
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= 16384 or not 1 <= height <= 16384
            or width * height > 67_108_864):
        raise ValueError('cache image dimensions exceed RGBA8 limits')
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('cache image must be a PNG')
    try:
        with Image.open(BytesIO(data)) as image:
            if (image.format != 'PNG' or image.mode != 'RGBA'
                    or image.size != (width, height)):
                raise ValueError('cache image needs exact RGBA8 profile dimensions')
            image.load()
            pixels = image.tobytes()
    except (UnidentifiedImageError, OSError, OverflowError, Image.DecompressionBombError) as exc:
        raise ValueError('cache image PNG cannot be decoded') from exc
    return sha256(data).hexdigest(), sha256(pixels).hexdigest()


def _clean_segments(segments, project_hash):
    if not isinstance(segments, (list, tuple)) or not segments:
        raise ValueError('cache needs a complete nonempty segment ledger')
    if len(segments) > 2048:
        raise ValueError('too many cache segments')
    cleaned = []
    previous_end = 0
    for segment in segments:
        if not isinstance(segment, Segment) or segment.dependency_fingerprint != project_hash:
            raise ValueError('segments must match the full project fingerprint')
        if segment.start_frame != previous_end:
            raise ValueError('segments must be ordered and contiguous')
        previous_end = segment.end_frame_exclusive
        cleaned.append((segment.id, segment.start_frame, segment.end_frame_exclusive, segment.generation))
    if len({item[0] for item in cleaned}) != len(cleaned):
        raise ValueError('segment identifiers must be unique')
    return cleaned


class PreviewFrameCache:
    """Cross-process safe highest-quality retained frame per absolute frame.

    Initial S1 callers must provide a validated Project-derived fingerprint,
    segments and quality profiles. Nothing is hooked into the authoring server
    until S2. A ticket authorises only a cache attempt, not rendering/approval.
    """

    def __init__(self, root, *, project_fingerprint, pipeline_fingerprint,
                 renderer_abi, segments, profiles, max_bytes=_DEFAULT_DISK,
                 min_free_bytes=0, ram_bytes=_DEFAULT_RAM):
        self.project_hash = _hex(project_fingerprint, 'project fingerprint')
        self.pipeline_hash = _hex(pipeline_fingerprint, 'pipeline fingerprint')
        if not isinstance(renderer_abi, str) or not 1 <= len(renderer_abi) <= 128:
            raise ValueError('renderer ABI must be a stable nonempty string')
        self.renderer_abi = renderer_abi
        self._segments = _clean_segments(segments, self.project_hash)
        if (not isinstance(profiles, (list, tuple)) or len(profiles) != 6
                or any(not isinstance(p, QualityProfile) or p.quality_rank != rank
                       or p.id != f'Q{rank}' for rank, p in enumerate(profiles))):
            raise ValueError('cache expects the canonical Q0..Q5 profile ladder')
        self.profiles = tuple(profiles)
        self.profile_digest = canonical_digest([asdict(p) for p in profiles])
        self.max_bytes = _integer(max_bytes, 'disk quota', 1, MAX_CACHE_BYTES)
        self.min_free_bytes = _integer(min_free_bytes, 'free-space reserve', 0, MAX_CACHE_BYTES)
        self.ram_bytes = _integer(ram_bytes, 'RAM quota', 0, MAX_CACHE_BYTES)
        target = Path(root).expanduser()
        if target.is_symlink():
            raise ValueError('cache root must not be a symlink')
        self.root = target.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs = self.root / 'blobs'
        self.staging = self.root / 'staging'
        for folder in (self.blobs, self.staging):
            if folder.is_symlink():
                raise ValueError('cache directory must not be a symlink')
            folder.mkdir(exist_ok=True)
        self.db_path = self.root / 'index.sqlite3'
        if self.db_path.is_symlink():
            raise ValueError('cache index must not be a symlink')
        self._guard = RLock()
        self._ram = OrderedDict()
        self._ram_size = 0
        self._stats = {'writes': 0, 'upgrades': 0, 'hits': 0,
                       'evictions': 0, 'corruptions': 0, 'stale_rejected': 0}
        self._initialize()
        self.recover()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=30000')
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('PRAGMA journal_mode=WAL')
        return conn

    def _initialize(self):
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('''CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY, first_frame INTEGER NOT NULL,
                    end_frame INTEGER NOT NULL, generation INTEGER NOT NULL)''')
                db.execute('''CREATE TABLE IF NOT EXISTS frames (
                    segment_id TEXT NOT NULL, frame INTEGER NOT NULL, generation INTEGER NOT NULL,
                    profile_id TEXT NOT NULL, quality_rank INTEGER NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, image_sha TEXT NOT NULL,
                    pixel_sha TEXT NOT NULL, byte_size INTEGER NOT NULL,
                    cache_key TEXT NOT NULL, settings_sha TEXT NOT NULL,
                    last_access INTEGER NOT NULL,
                    PRIMARY KEY (segment_id,frame))''')
                values = dict(db.execute('SELECT key, value FROM meta'))
                expected = {'schema': CACHE_SCHEMA, 'project_hash': self.project_hash,
                            'pipeline_hash': self.pipeline_hash, 'renderer_abi': self.renderer_abi,
                            'profile_digest': self.profile_digest}
                if values:
                    if any(values.get(key) != value for key, value in expected.items()):
                        raise CacheIdentityError('cache identity differs; explicitly reset revision')
                    found = [tuple(r) for r in db.execute('SELECT id,first_frame,end_frame,generation '
                                                         'FROM segments ORDER BY first_frame')]
                    if [(a,b,c) for a,b,c,_ in found] != [(a,b,c) for a,b,c,_ in self._segments]:
                        raise CacheIdentityError('segment manifest differs; explicitly reset revision')
                    # Generations are owned by the durable index, never reset by a
                    # fresh Project-derived segment ledger on process restart.
                    self._segments = found
                    if not values.get('revision_counter', '').isdigit():
                        raise CacheIdentityError('cache revision counter is invalid')
                    self._revision_token = int(values['revision_counter'])
                else:
                    self._revision_token = 0
                    expected['revision_counter'] = '0'
                    db.executemany('INSERT INTO meta(key,value) VALUES (?,?)', expected.items())
                    db.executemany('INSERT INTO segments VALUES (?,?,?,?)', self._segments)
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def _blob(self, digest):
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise CacheIntegrityError('malformed image digest in cache index')
        return self.blobs / digest[:2] / f'{digest}.png'

    def _verify_file(self, digest, width, height, expected_pixel, expected_size):
        path = self._blob(digest)
        if (path.parent.is_symlink() or path.is_symlink() or not path.is_file()
                or path.stat().st_size != expected_size):
            return None
        try:
            data = path.read_bytes()
            image_sha, pixel_sha = _verified_png(data, width, height)
        except (OSError, ValueError):
            return None
        if image_sha != digest or pixel_sha != expected_pixel:
            return None
        return data

    def _write_blob(self, data, digest):
        path = self._blob(digest)
        directory = path.parent
        if directory.is_symlink():
            raise CacheIntegrityError('blob prefix is a symlink')
        directory.mkdir(exist_ok=True)
        if path.is_symlink():
            raise CacheIntegrityError('blob path is a symlink')
        if path.is_file():
            if sha256(path.read_bytes()).hexdigest() != digest:
                raise CacheIntegrityError('existing content-addressed blob is corrupt')
            return
        temporary = self.staging / f'{uuid4().hex}.tmp'
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if sha256(temporary.read_bytes()).hexdigest() != digest:
                raise CacheIntegrityError('staged image verification failed')
            os.replace(temporary, path)
            _fsync_directory(directory)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _rows(db):
        return db.execute('SELECT * FROM frames ORDER BY last_access').fetchall()

    def _validate_identity(self, db):
        values = dict(db.execute('SELECT key,value FROM meta'))
        return (values.get('project_hash') == self.project_hash
                and values.get('pipeline_hash') == self.pipeline_hash
                and values.get('renderer_abi') == self.renderer_abi
                and values.get('profile_digest') == self.profile_digest
                and values.get('schema') == CACHE_SCHEMA
                and values.get('revision_counter') == str(self._revision_token))

    def _segment_row(self, db, ticket):
        return db.execute('SELECT * FROM segments WHERE id=?', (ticket.segment_id,)).fetchone()

    def ticket(self, frame, profile_id):
        """Capture generation so an old render cannot commit after invalidation."""
        if not isinstance(profile_id, str) or profile_id not in {p.id for p in self.profiles}:
            raise ValueError('unknown quality profile')
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN')
            try:
                if not self._validate_identity(db):
                    raise CacheIdentityError('cache revision was changed by another owner')
                row = db.execute('SELECT * FROM segments WHERE first_frame<=? AND end_frame>?',
                                 (frame, frame)).fetchone() if type(frame) is int else None
                if row is None:
                    raise ValueError('frame is outside the cache segments')
                profile = next(p for p in self.profiles if p.id == profile_id)
                segment = Segment(row['id'], row['first_frame'], row['end_frame'],
                                  self.project_hash, row['generation'])
                return FrameTicket(self.project_hash, self.pipeline_hash, self.renderer_abi,
                                   segment.id, segment.generation, frame, profile.id,
                                   profile.quality_rank, profile.output_width, profile.output_height,
                                   frame_cache_identity(self.project_hash, segment, frame, profile,
                                                        renderer_abi=self.renderer_abi))
            finally:
                db.execute('ROLLBACK')

    def _valid_index_row(self, db, row):
        seg = db.execute('SELECT * FROM segments WHERE id=?', (row['segment_id'],)).fetchone()
        rank = row['quality_rank']
        if (seg is None or seg['generation'] != row['generation']
                or not seg['first_frame'] <= row['frame'] < seg['end_frame']
                or type(rank) is not int or not 0 <= rank < len(self.profiles)):
            return False
        profile = self.profiles[rank]
        if ((row['profile_id'], row['width'], row['height']) !=
                (profile.id, profile.output_width, profile.output_height)):
            return False
        if (not isinstance(row['settings_sha'], str) or not _HEX.fullmatch(row['settings_sha'])
                or not isinstance(row['image_sha'], str) or not _HEX.fullmatch(row['image_sha'])
                or not isinstance(row['pixel_sha'], str) or not _HEX.fullmatch(row['pixel_sha'])
                or not 0 < row['byte_size'] <= MAX_PNG_BYTES):
            return False
        segment = Segment(seg['id'], seg['first_frame'], seg['end_frame'],
                          self.project_hash, seg['generation'])
        return row['cache_key'] == frame_cache_identity(
            self.project_hash, segment, row['frame'], profile, renderer_abi=self.renderer_abi)

    def _ticket_current(self, db, ticket):
        if not isinstance(ticket, FrameTicket) or not self._validate_identity(db):
            return False
        row = self._segment_row(db, ticket)
        if row is None or row['generation'] != ticket.generation:
            return False
        if not row['first_frame'] <= ticket.frame < row['end_frame']:
            return False
        profile = self.profiles[ticket.quality_rank] if type(ticket.quality_rank) is int and 0 <= ticket.quality_rank < 6 else None
        if profile is None or (ticket.profile_id, ticket.width, ticket.height) != (
                profile.id, profile.output_width, profile.output_height):
            return False
        segment = Segment(row['id'], row['first_frame'], row['end_frame'], self.project_hash,
                          row['generation'])
        return (ticket.project_fingerprint == self.project_hash
                and ticket.pipeline_fingerprint == self.pipeline_hash
                and ticket.renderer_abi == self.renderer_abi
                and ticket.cache_key == frame_cache_identity(self.project_hash, segment,
                                                               ticket.frame, profile, renderer_abi=self.renderer_abi))

    @staticmethod
    def _stat_identity(path):
        if path.is_symlink():
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)

    def _ram_remember(self, key, data):
        if not self.ram_bytes or len(data) > self.ram_bytes:
            return
        old = self._ram.pop(key, None)
        if old is not None:
            self._ram_size -= len(old[0])
        self._ram[key] = (data, self._stat_identity(self._blob(key[2])))
        self._ram_size += len(data)
        while self._ram_size > self.ram_bytes:
            _, dropped = self._ram.popitem(last=False)
            self._ram_size -= len(dropped[0])

    def _ram_forget_segment(self, name=None):
        for key in list(self._ram):
            if name is None or key[0] == name:
                self._ram_size -= len(self._ram.pop(key)[0])

    def _disk_bytes(self, db):
        return sum(row[0] for row in db.execute(
            'SELECT MAX(byte_size) FROM frames GROUP BY image_sha'))

    def _enforce_quota(self, db, *, active_segment, protected):
        removed = 0
        while (self._disk_bytes(db) > self.max_bytes
               or db.execute('SELECT COUNT(*) FROM frames').fetchone()[0] > MAX_FRAMES):
            candidates = [row for row in self._rows(db)
                          if (row['segment_id'], row['frame']) != protected]
            if not candidates:
                return False, removed
            victim = min(candidates, key=lambda row: (
                row['segment_id'] == active_segment, row['quality_rank'], row['last_access'], row['frame']))
            db.execute('DELETE FROM frames WHERE segment_id=? AND frame=?',
                       (victim['segment_id'], victim['frame']))
            self._ram_forget_segment(victim['segment_id'])
            removed += 1
        return True, removed

    def publish(self, ticket, png, *, settings_sha256):
        """Atomic rank CAS. Returns inserted/upgraded/unchanged/stale/quota_rejected.

        The caller, not this store, must prove a quality tier is actually
        renderable. S1 never claims Q5 is equivalent to a production frame.
        """
        settings = _hex(settings_sha256, 'effective render settings')
        if not isinstance(ticket, FrameTicket):
            raise ValueError('expected an issued immutable frame ticket')
        # PNG decoding is bounded by the profile dimensions, independent of GL.
        image_sha, pixel_sha = _verified_png(png, ticket.width, ticket.height)
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not self._ticket_current(db, ticket):
                    db.execute('ROLLBACK')
                    self._stats['stale_rejected'] += 1
                    return 'stale'
                before = db.execute('SELECT * FROM frames WHERE segment_id=? AND frame=?',
                                    (ticket.segment_id, ticket.frame)).fetchone()
                if before and (not self._valid_index_row(db, before)
                               or self._verify_file(before['image_sha'], before['width'], before['height'],
                                                    before['pixel_sha'], before['byte_size']) is None):
                    db.execute('DELETE FROM frames WHERE segment_id=? AND frame=?',
                               (ticket.segment_id, ticket.frame))
                    self._ram_forget_segment(ticket.segment_id)
                    self._stats['corruptions'] += 1
                    before = None
                if before and ticket.quality_rank < before['quality_rank']:
                    db.execute('ROLLBACK')
                    return 'unchanged'
                if before and ticket.quality_rank == before['quality_rank']:
                    if before['pixel_sha'] != pixel_sha or before['settings_sha'] != settings:
                        raise CacheIntegrityError('same-rank render disagrees with retained pixels/settings')
                    db.execute('ROLLBACK')
                    return 'unchanged'
                if len(png) > self.max_bytes:
                    db.execute('ROLLBACK')
                    return 'quota_rejected'
                destination = self._blob(image_sha)
                if not destination.is_file() and shutil.disk_usage(self.root).free < len(png) + self.min_free_bytes:
                    db.execute('ROLLBACK')
                    return 'quota_rejected'
                self._write_blob(png, image_sha)
                now = db.execute('SELECT COALESCE(MAX(last_access),0)+1 FROM frames').fetchone()[0]
                db.execute('''INSERT OR REPLACE INTO frames
                    (segment_id,frame,generation,profile_id,quality_rank,width,height,image_sha,
                     pixel_sha,byte_size,cache_key,settings_sha,last_access)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                    ticket.segment_id, ticket.frame, ticket.generation, ticket.profile_id,
                    ticket.quality_rank, ticket.width, ticket.height, image_sha, pixel_sha,
                    len(png), ticket.cache_key, settings, now))
                ok, removed = self._enforce_quota(db, active_segment=ticket.segment_id,
                                                   protected=(ticket.segment_id, ticket.frame))
                if not ok:
                    db.execute('ROLLBACK')
                    return 'quota_rejected'
                db.execute('COMMIT')
                self._stats['evictions'] += removed
                if before:
                    self._stats['upgrades'] += 1
                else:
                    self._stats['writes'] += 1
                self._ram_remember((ticket.segment_id, ticket.frame, image_sha), png)
                # Reclaim replaced/evicted blobs only after the durable index commit.
                self.collect_garbage()
                return 'upgraded' if before else 'inserted'
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def get(self, frame):
        """Return highest valid cache tier or None. Corruption fails closed."""
        if type(frame) is not int:
            raise ValueError('frame must be an integer')
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not self._validate_identity(db):
                    db.execute('ROLLBACK')
                    return None
                row = db.execute('''SELECT f.* FROM frames f JOIN segments s ON s.id=f.segment_id
                    WHERE f.frame=? AND f.generation=s.generation''', (frame,)).fetchone()
                if row is None:
                    db.execute('ROLLBACK')
                    return None
                if not self._valid_index_row(db, row):
                    db.execute('DELETE FROM frames WHERE segment_id=? AND frame=?',
                               (row['segment_id'], frame))
                    db.execute('COMMIT')
                    self._ram_forget_segment(row['segment_id'])
                    self._stats['corruptions'] += 1
                    return None
                key = (row['segment_id'], frame, row['image_sha'])
                memory = self._ram.get(key)
                if (memory is not None and memory[1] is not None
                        and memory[1] == self._stat_identity(self._blob(row['image_sha']))):
                    raw = memory[0]
                    self._ram.move_to_end(key)
                else:
                    raw = self._verify_file(row['image_sha'], row['width'], row['height'],
                                            row['pixel_sha'], row['byte_size'])
                if raw is None:
                    db.execute('DELETE FROM frames WHERE segment_id=? AND frame=?',
                               (row['segment_id'], frame))
                    db.execute('COMMIT')
                    self._ram_forget_segment(row['segment_id'])
                    self._stats['corruptions'] += 1
                    self.collect_garbage()
                    return None
                db.execute('UPDATE frames SET last_access=(SELECT COALESCE(MAX(last_access),0)+1 FROM frames) '
                           'WHERE segment_id=? AND frame=?', (row['segment_id'], frame))
                db.execute('COMMIT')
                self._ram_remember((row['segment_id'], frame, row['image_sha']), raw)
                self._stats['hits'] += 1
                return CachedFrame(frame, row['segment_id'], row['generation'], row['profile_id'],
                                   row['quality_rank'], row['width'], row['height'],
                                   row['pixel_sha'], row['image_sha'], raw)
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def invalidate_all(self):
        """S1 deliberately invalidates all sections until S5 proves isolation."""
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not self._validate_identity(db):
                    raise CacheIdentityError('cache was revised externally')
                if db.execute('SELECT MAX(generation) FROM segments').fetchone()[0] >= 2**63-1:
                    raise ValueError('segment generation exhausted')
                if self._revision_token >= 2**63-1:
                    raise ValueError('cache revision counter exhausted')
                db.execute('UPDATE segments SET generation=generation+1')
                db.execute('DELETE FROM frames')
                new_revision = self._revision_token + 1
                db.execute('UPDATE meta SET value=? WHERE key="revision_counter"', (str(new_revision),))
                db.execute('COMMIT')
                self._revision_token = new_revision
                self._ram_forget_segment()
                self.collect_garbage()
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def revise_all(self, *, project_fingerprint, pipeline_fingerprint, segments,
                   profiles=None, renderer_abi=None):
        """After a source/effect/pipeline edit, invalidate *all* S1 sections."""
        new_project = _hex(project_fingerprint, 'project fingerprint')
        new_pipeline = _hex(pipeline_fingerprint, 'pipeline fingerprint')
        clean = _clean_segments(segments, new_project)
        next_profiles = self.profiles if profiles is None else tuple(profiles)
        if (len(next_profiles) != 6 or any(not isinstance(p, QualityProfile)
                or p.quality_rank != rank or p.id != f'Q{rank}'
                for rank, p in enumerate(next_profiles))):
            raise ValueError('revised profiles must retain canonical Q0..Q5 rank ordering')
        next_profile_digest = canonical_digest([asdict(p) for p in next_profiles])
        next_renderer = self.renderer_abi if renderer_abi is None else renderer_abi
        if not isinstance(next_renderer, str) or not 1 <= len(next_renderer) <= 128:
            raise ValueError('revised renderer ABI requires a stable nonempty string')
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not self._validate_identity(db):
                    raise CacheIdentityError('cache was revised by another owner')
                previous = db.execute('SELECT value FROM meta WHERE key="revision_counter"').fetchone()
                max_generation = db.execute('SELECT COALESCE(MAX(generation),0) FROM segments').fetchone()[0]
                revised = max(int(previous[0]) if previous else 0, max_generation) + 1
                if revised > 2**63 - 1:
                    raise ValueError('cache revision counter exhausted')
                db.execute('DELETE FROM frames')
                db.execute('DELETE FROM segments')
                db.executemany('INSERT INTO segments VALUES (?,?,?,?)',
                               [(name, start, end, revised) for name, start, end, _ in clean])
                db.executemany('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)', (
                    ('project_hash', new_project), ('pipeline_hash', new_pipeline),
                    ('profile_digest', next_profile_digest), ('renderer_abi', next_renderer),
                    ('revision_counter', str(revised))))
                db.execute('COMMIT')
                self.project_hash = new_project
                self.pipeline_hash = new_pipeline
                self.renderer_abi = next_renderer
                self.profiles = next_profiles
                self.profile_digest = next_profile_digest
                self._segments = [(a,b,c,revised) for a,b,c,_ in clean]
                self._revision_token = revised
                self._ram_forget_segment()
                self.collect_garbage()
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def recover(self):
        """Discard corrupted rows and orphan PNG/temp files after crashes."""
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not self._validate_identity(db):
                    raise CacheIdentityError('cache changed before recovery')
                removed = 0
                for row in self._rows(db):
                    ok = (self._valid_index_row(db, row)
                          and self._verify_file(row['image_sha'], row['width'], row['height'],
                                                row['pixel_sha'], row['byte_size']) is not None)
                    if not ok:
                        db.execute('DELETE FROM frames WHERE segment_id=? AND frame=?',
                                   (row['segment_id'], row['frame']))
                        removed += 1
                _ok, evicted = self._enforce_quota(db, active_segment=None, protected=None)
                db.execute('COMMIT')
                self._stats['evictions'] += evicted
                self._stats['corruptions'] += removed
                if removed:
                    self._ram_forget_segment()
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise
        self.collect_garbage()

    def collect_garbage(self):
        """Remove only unreferenced, strictly named cache-managed PNGs/temps."""
        with self._guard, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                live = {row[0] for row in db.execute('SELECT DISTINCT image_sha FROM frames')}
                for path in self.staging.iterdir():
                    if path.is_file() and path.name.endswith('.tmp'):
                        path.unlink()
                for prefix in self.blobs.iterdir():
                    if prefix.is_symlink():
                        raise CacheIntegrityError('cache blob directory symlink found')
                    if not prefix.is_dir() or not re.fullmatch(r'[0-9a-f]{2}', prefix.name):
                        continue
                    for path in prefix.iterdir():
                        if path.is_symlink():
                            raise CacheIntegrityError('cache blob symlink found')
                        if not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}\.png', path.name):
                            continue
                        if path.stem not in live:
                            path.unlink()
                    if not any(prefix.iterdir()):
                        prefix.rmdir()
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def stats(self):
        with self._guard, closing(self._connect()) as db:
            if not self._validate_identity(db):
                raise CacheIdentityError('cache revision was changed by another owner')
            frames = db.execute('SELECT COUNT(*) FROM frames').fetchone()[0]
            occupied = self._disk_bytes(db)
            generations = [r[0] for r in db.execute('SELECT generation FROM segments ORDER BY first_frame')]
            return {'schema': CACHE_SCHEMA, 'frames': frames, 'disk_bytes': occupied,
                    'ram_bytes': self._ram_size, 'max_bytes': self.max_bytes,
                    'min_free_bytes': self.min_free_bytes,
                    'generations': generations, **self._stats}
