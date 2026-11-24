"""Bounded per-layer RGBA pixels, independent of finished preview frames.

Keys are immutable native dependency identities. A changed layer gets new keys;
an old worker cannot overwrite its replacement. Unchanged layers remain reusable.
The optional disk index shares quotas across layers and verifies PNG/pixel and
typed metadata hashes. Cache capacity eviction is separate from invalidation.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from importlib import import_module
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from threading import RLock
from time import time_ns
from uuid import uuid4

from PIL import Image

from .preview_frame_cache import CacheIntegrityError, _verified_png
from .preview_stream_contract import canonical_digest, _hex, _integer

SCHEMA = 'framerudiments.layer-frame-cache.v1'
MAX_METADATA = 256 * 1024
MAX_ENTRIES = 100_000


def _pack(value):
    # Preserve tuple/list distinctions in native technical reports.
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError('layer metadata keys must be strings')
        return ['dict', [[k, _pack(v)] for k, v in sorted(value.items())]]
    if isinstance(value, (list, tuple)):
        return ['tuple' if isinstance(value, tuple) else 'list', [_pack(v) for v in value]]
    if value is None or type(value) in (str, bool, int, float):
        return ['value', value]
    raise ValueError('unsupported layer metadata')


def _unpack(value, depth=0):
    if depth > 64 or not isinstance(value, list) or len(value) != 2:
        raise ValueError('invalid typed layer metadata')
    tag, data = value
    if tag == 'value' and (data is None or type(data) in (str, bool, int, float)):
        return data
    if tag in ('list', 'tuple') and isinstance(data, list):
        rows = [_unpack(v, depth+1) for v in data]
        return tuple(rows) if tag == 'tuple' else rows
    if tag == 'dict' and isinstance(data, list):
        result = {}
        for row in data:
            if not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], str) or row[0] in result:
                raise ValueError('invalid layer metadata map')
            result[row[0]] = _unpack(row[1], depth+1)
        return result
    raise ValueError('invalid layer metadata type')


class LayerFrameCache:
    """One bounded store, with independent namespaces for every composited layer.

    root=None keeps only bounded RAM. Persistent keys include the installed
    implementation fingerprint and workspace supplied by the native owner.
    Returned images/metadata are detached from retained bytes.
    """
    def __init__(self, root=None, *, max_bytes=256*1024**2, ram_bytes=32*1024**2,
                 min_free_bytes=0, implementation=None):
        self.max_bytes = _integer(max_bytes, 'layer disk quota', 1, 64*1024**3)
        self.ram_bytes = _integer(ram_bytes, 'layer RAM quota', 0, 64*1024**3)
        self.min_free_bytes = _integer(min_free_bytes, 'layer free reserve', 0, 64*1024**3)
        self._lock = RLock()
        self._ram, self._ram_size, self._counts = OrderedDict(), 0, {}
        self._ram_files = {}
        self.closed = False
        self._connection, self._initialized, self._closed_stats = None, False, None
        self._depth, self._pending_size = 0, 0
        self._pending, self._aborted = OrderedDict(), False
        self._depth, self._pending_size = 0, 0
        self._pending, self._aborted = OrderedDict(), False
        self.root = None
        if root is not None:
            target = Path(root).expanduser()
            if target.is_symlink():
                raise ValueError('layer cache root cannot be a symlink')
            self.root = target.resolve()
            self.root.mkdir(parents=True, exist_ok=True)
            allowed = {'blobs', 'staging', 'index.sqlite3', 'index.sqlite3-wal', 'index.sqlite3-shm'}
            present = list(self.root.iterdir())
            if any(item.name not in allowed for item in present):
                raise ValueError('Layer cache requires a dedicated directory')
            index = self.root/'index.sqlite3'
            if index.is_symlink():
                raise ValueError('layer cache index cannot be a symlink')
            if index.exists():
                check = sqlite3.connect(index.as_uri()+'?mode=ro', uri=True, timeout=30)
                try:
                    tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not tables:
                        for name in ('blobs', 'staging'):
                            folder = self.root/name
                            if folder.exists() and (folder.is_symlink() or not folder.is_dir()
                                                    or any(folder.iterdir())):
                                raise ValueError('Existing pixels have no owned layer cache index')
                    if tables:
                        if 'meta' not in tables:
                            raise ValueError('Existing directory is not an owned layer cache')
                        owner = check.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                        if owner is None or owner[0] != SCHEMA:
                            raise ValueError('Existing directory belongs to another cache schema')
                finally:
                    check.close()
            else:
                for item in present:
                    if (item.name not in {'blobs', 'staging'} or item.is_symlink()
                            or not item.is_dir() or any(item.iterdir())):
                        raise ValueError('Existing content has no owned layer cache index')
            for name in ('blobs', 'staging'):
                folder = self.root/name
                if folder.is_symlink():
                    raise ValueError('layer cache directory cannot be a symlink')
                folder.mkdir(exist_ok=True)
            self.db_path = self.root/'index.sqlite3'
            if self.db_path.is_symlink():
                raise ValueError('layer cache index cannot be a symlink')
        if implementation is None:
            if self.root is not None:
                from .preview_dependencies import implementation_fingerprint
                natives = []
                for name in ('dancerudiments', 'PIL._imaging'):
                    # Required core modules, independent of prior layer/import order.
                    module = import_module(name)
                    filename = getattr(module, '__file__', None)
                    if filename:
                        natives.append((name, sha256(Path(filename).read_bytes()).hexdigest()))
                implementation = canonical_digest({'source': implementation_fingerprint(),
                    'python': sys.version.split()[0], 'native_binaries': natives})
            else:
                from . import __version__
                implementation = canonical_digest({'process_native_version': __version__})
        self.implementation = _hex(implementation, 'layer implementation')
        if self.root is not None:
            with self._database() as db:
                db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                old = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if old is not None and old[0] != SCHEMA:
                    raise ValueError('unsupported layer cache schema')
                db.execute("INSERT OR IGNORE INTO meta VALUES ('schema',?)", (SCHEMA,))
                db.execute('''CREATE TABLE IF NOT EXISTS frames (
                    layer TEXT NOT NULL,key TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,
                    image_sha TEXT NOT NULL,pixel_sha TEXT NOT NULL,metadata TEXT NOT NULL,
                    metadata_sha TEXT NOT NULL,byte_size INTEGER NOT NULL,last_access INTEGER NOT NULL,
                    PRIMARY KEY(layer,key))''')
                self._collect(db)
        self._initialized = True

    @contextmanager
    def _database(self):
        # Keep the WAL connection alive: closing the last connection after each
        # layer forces checkpoints and turns recomputable cache I/O into syncs.
        with self._lock:
            if self.closed:
                raise RuntimeError('layer cache is closed')
            if self._connection is None:
                db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None,
                                     check_same_thread=False)
                try:
                    db.row_factory = sqlite3.Row
                    db.execute('PRAGMA busy_timeout=30000')
                    mode = db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
                    if mode.lower() != 'wal':
                        raise OSError('Layer cache requires local SQLite WAL storage')
                    db.execute('PRAGMA synchronous=NORMAL')
                except BaseException:
                    db.close()
                    raise
                self._connection = db
            db = self._connection
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                if not self._initialized:
                    db.close()
                    self._connection = None
                raise

    def _count(self, layer, name):
        row = self._counts.setdefault(layer, dict(hits=0, ram_hits=0, misses=0, writes=0, evictions=0,
                                                 corruptions=0, bypasses=0))
        row[name] += 1

    def _check(self, layer, key):
        if self.closed:
            raise RuntimeError('layer cache is closed')
        if not isinstance(layer, str) or not 1 <= len(layer) <= 256:
            raise ValueError('invalid layer cache namespace')
        _hex(key, 'layer sample key')

    def key(self, dependencies):
        return canonical_digest({'schema': SCHEMA, 'implementation': self.implementation,
                                 'dependencies': dependencies})

    def _blob(self, digest):
        _hex(digest, 'layer image digest')
        parent = self.root/'blobs'/digest[:2]
        if parent.is_symlink():
            raise ValueError('layer blob prefix cannot be a symlink')
        path = parent/(digest+'.png')
        if path.is_symlink():
            raise ValueError('layer blob cannot be a symlink')
        return path

    @staticmethod
    def _signature(path):
        s = path.stat()
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    def _remember(self, identity, raw, size, metadata, disk=None):
        self._ram_files.pop(identity, None)
        previous = self._ram.pop(identity, None)
        if previous is not None:
            self._ram_size -= len(previous[0])+len(previous[2])
        weight = len(raw)+len(metadata)
        if weight > self.ram_bytes:
            return
        while self._ram and self._ram_size+weight > self.ram_bytes:
            old, item = self._ram.popitem(last=False)
            self._ram_size -= len(item[0])+len(item[2])
            self._ram_files.pop(old, None)
            if self.root is None:
                self._count(old[0], 'evictions')
        self._ram[identity] = (raw, size, metadata)
        self._ram_files[identity] = disk
        self._ram_size += weight

    def _forget(self, identity):
        self._ram_files.pop(identity, None)
        item = self._ram.pop(identity, None)
        if item is not None:
            self._ram_size -= len(item[0])+len(item[2])

    @staticmethod
    def _decoded(item):
        raw, size, metadata = item
        return Image.frombytes('RGBA', size, raw), _unpack(json.loads(metadata))

    def get(self, layer, key):
        with self._lock:
            self._check(layer, key)
            identity = (layer, key)
            pending = self._pending.get(identity)
            if pending is not None:
                self._count(layer, 'hits')
                return self._decoded(pending)
            item = self._ram.get(identity)
            if item is not None:
                unchanged = self.root is None
                disk = self._ram_files.get(identity)
                if not unchanged and disk is not None:
                    try:
                        unchanged = self._signature(self._blob(disk[0])) == disk[1]
                    except OSError:
                        unchanged = False
                if unchanged:
                    self._ram.move_to_end(identity)
                    self._count(layer, 'hits')
                    self._count(layer, 'ram_hits')
                    return self._decoded(item)
            if self.root is not None:
                with self._database() as db:
                    row = db.execute('SELECT * FROM frames WHERE layer=? AND key=?', identity).fetchone()
                    if row is not None:
                        try:
                            data = self._blob(row['image_sha']).read_bytes()
                            if sha256(data).hexdigest() != row['image_sha']:
                                raise ValueError('changed layer PNG')
                            metadata = row['metadata'].encode('utf-8')
                            if sha256(metadata).hexdigest() != row['metadata_sha']:
                                raise ValueError('changed layer metadata')
                            _unpack(json.loads(metadata))
                            item = self._ram.get(identity)
                            if item is None:
                                image_sha, pixel_sha = _verified_png(data, row['width'], row['height'])
                                if pixel_sha != row['pixel_sha']:
                                    raise ValueError('changed layer pixels')
                                with Image.open(BytesIO(data)) as image:
                                    raw = image.tobytes()
                                item = (raw, (row['width'], row['height']), metadata)
                                self._remember(identity, *item,
                                    disk=(row['image_sha'], self._signature(self._blob(row['image_sha']))))
                            elif sha256(item[0]).hexdigest() != row['pixel_sha'] or item[2] != metadata:
                                raise ValueError('changed retained layer identity')
                        except (OSError, ValueError, json.JSONDecodeError):
                            self._forget(identity)
                            db.execute('DELETE FROM frames WHERE layer=? AND key=?', identity)
                            self._count(layer, 'corruptions')
                            self._collect(db)
                        else:
                            db.execute('UPDATE frames SET last_access=? WHERE layer=? AND key=?',
                                       (time_ns(), *identity))
                            self._count(layer, 'hits')
                            return self._decoded(item)
            self._count(layer, 'misses')
            return None

    def put(self, layer, key, image, metadata):
        with self._lock:
            self._check(layer, key)
            if not isinstance(image, Image.Image) or image.mode != 'RGBA':
                raise ValueError('cached layer needs RGBA pixels')
            try:
                packed = json.dumps(_pack(metadata), allow_nan=False, separators=(',', ':')).encode('utf-8')
            except (ValueError, TypeError, RecursionError):
                self._count(layer, 'bypasses')
                return False
            if len(packed) > MAX_METADATA:
                self._count(layer, 'bypasses')
                return False
            weight = image.width*image.height*4+len(packed)
            limit = min(self.max_bytes, 32*1024**2)
            if ((self._depth and weight > limit)
                    or (self.root is None and weight > self.ram_bytes)):
                self._count(layer, 'bypasses')
                return False
            raw = image.tobytes()
            identity = (layer, key)
            if self._depth:
                previous = self._pending.get(identity)
                if previous is not None:
                    if previous != (raw, image.size, packed):
                        raise CacheIntegrityError('same pending layer input produced different pixels or metadata')
                    return True
                while self._pending and self._pending_size+weight > limit:
                    _, old = self._pending.popitem(last=False)
                    self._pending_size -= len(old[0])+len(old[2])
                self._pending[identity] = raw, image.size, packed
                self._pending_size += weight
                return True
            if self.root is None:
                previous = self._ram.get(identity)
                if previous is not None and (previous[0] != raw or previous[1] != image.size or previous[2] != packed):
                    raise CacheIntegrityError('same layer dependency produced different pixels or metadata')
            else:
                stream = BytesIO()
                image.save(stream, format='PNG', compress_level=1)
                png = stream.getvalue()
                image_sha, pixel_sha = _verified_png(png, *image.size)
                weight = len(png)+len(packed)
                if weight > self.max_bytes:
                    self._count(layer, 'bypasses')
                    return False
                with self._database() as db:
                    previous = db.execute('SELECT * FROM frames WHERE layer=? AND key=?', identity).fetchone()
                    if previous is not None and (previous['pixel_sha'] != pixel_sha
                            or previous['metadata_sha'] != sha256(packed).hexdigest()
                            or (previous['width'], previous['height']) != image.size):
                        raise CacheIntegrityError('same layer dependency produced different pixels or metadata')
                    if shutil.disk_usage(self.root).free < len(png)+self.min_free_bytes:
                        self._count(layer, 'bypasses')
                        return False
                    path = self._blob(image_sha)
                    path.parent.mkdir(exist_ok=True)
                    if not path.is_file():
                        temporary = self.root/'staging'/(uuid4().hex+'.tmp')
                        try:
                            with temporary.open('xb') as out:
                                out.write(png)
                                out.flush()
                            # Atomic, hash-checked working pixels are disposable:
                            # power loss may discard them, never validate partial
                            # bytes. The durable final-frame cache is unchanged.
                            os.replace(temporary, path)
                        finally:
                            temporary.unlink(missing_ok=True)
                    elif sha256(path.read_bytes()).hexdigest() != image_sha:
                        raise CacheIntegrityError('corrupt existing layer blob')
                    db.execute('INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (*identity, *image.size, image_sha, pixel_sha, packed.decode('utf-8'),
                                sha256(packed).hexdigest(), weight, time_ns()))
                    self._enforce(db, identity)
            disk = None
            if self.root is not None:
                try:
                    disk = image_sha, self._signature(self._blob(image_sha))
                except OSError:
                    pass  # A separate cache owner may have evicted the disk tier.
            self._remember(identity, raw, image.size, packed, disk=disk)
            self._count(layer, 'writes')
            return True

    def _enforce(self, db, protected):
        count, occupied = db.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM frames').fetchone()
        if count <= MAX_ENTRIES and occupied <= self.max_bytes:
            return
        removed = set()
        for row in db.execute('SELECT layer,key,byte_size,image_sha FROM frames ORDER BY last_access').fetchall():
            if count <= MAX_ENTRIES and occupied <= self.max_bytes:
                break
            identity = row['layer'], row['key']
            if identity == protected:
                continue
            db.execute('DELETE FROM frames WHERE layer=? AND key=?', identity)
            count -= 1
            occupied -= row['byte_size']
            self._forget(identity)
            self._count(identity[0], 'evictions')
            removed.add(row['image_sha'])
        for digest in removed:
            if not db.execute('SELECT 1 FROM frames WHERE image_sha=? LIMIT 1', (digest,)).fetchone():
                self._blob(digest).unlink(missing_ok=True)

    def _collect(self, db):
        # All blob writers hold this same SQLite transaction before creating a
        # staging file, so no live writer's temporary or payload is removed.
        active = {r[0] for r in db.execute('SELECT DISTINCT image_sha FROM frames')}
        for path in (self.root/'staging').glob('*.tmp'):
            if path.is_symlink():
                raise ValueError('layer staging file cannot be a symlink')
            path.unlink()
        for folder in (self.root/'blobs').iterdir():
            if folder.is_symlink() or not folder.is_dir():
                raise ValueError('invalid layer blob directory')
            for path in folder.glob('*.png'):
                if path.is_symlink():
                    raise ValueError('layer blob cannot be a symlink')
                if path.stem not in active:
                    path.unlink()

    def _begin(self):
        # Native frame owners serialize admission across shared compositors.
        self._lock.acquire()
        if self.closed:
            self._lock.release()
            raise RuntimeError('layer cache is closed')
        outer = self._depth == 0
        self._depth += 1
        return outer

    def _finish(self, success):
        if self._depth < 1:
            raise RuntimeError('no layer cache admission is active')
        self._aborted |= not success
        self._depth -= 1
        try:
            if not self._depth:
                pending = self._pending
                self._pending, self._pending_size = OrderedDict(), 0
                aborted, self._aborted = self._aborted, False
                if not aborted:
                    for (layer, key), item in pending.items():
                        image, metadata = self._decoded(item)
                        self.put(layer, key, image, metadata)
        finally:
            self._lock.release()

    def clear_layer(self, layer):
        """Evict this owner/index layer; other owners may retain valid snapshots."""
        with self._lock:
            self._check(layer, '0'*64)
            for identity in tuple(self._pending):
                if identity[0] == layer:
                    item = self._pending.pop(identity)
                    self._pending_size -= len(item[0])+len(item[2])
            for identity in tuple(self._ram):
                if identity[0] == layer:
                    self._forget(identity)
            if self.root is not None:
                with self._database() as db:
                    db.execute('DELETE FROM frames WHERE layer=?', (layer,))
                    self._collect(db)

    def stats(self):
        with self._lock:
            if self.closed:
                return deepcopy(self._closed_stats)
            retained = {}
            if self.root is not None:
                with self._database() as db:
                    for row in db.execute('SELECT layer,COUNT(*) n,SUM(byte_size) bytes FROM frames GROUP BY layer'):
                        retained[row['layer']] = {'entries': row['n'], 'bytes': row['bytes']}
            else:
                for (layer, _), item in self._ram.items():
                    row = retained.setdefault(layer, dict(entries=0, bytes=0))
                    row['entries'] += 1
                    row['bytes'] += len(item[0])+len(item[2])
            return {'schema': SCHEMA, 'implementation_sha256': self.implementation,
                    'persistent': self.root is not None,
                    'storage_policy': 'verified_recomputable_wal_normal' if self.root is not None else 'bounded_ram',
                    'ram_bytes': self._ram_size, 'max_bytes': self.max_bytes, 'ram_limit': self.ram_bytes,
                    'layers': {name: {**counts, **retained.get(name, dict(entries=0, bytes=0))}
                               for name, counts in deepcopy(self._counts).items()}}

    def close(self):
        with self._lock:
            if self.closed:
                return
            try:
                self._closed_stats = self.stats()
            finally:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
                self._ram.clear()
                self._ram_files.clear()
                self._ram_size = 0
                self.closed = True
                if self._closed_stats is not None:
                    self._closed_stats['ram_bytes'] = 0
                    if self.root is None:
                        for row in self._closed_stats['layers'].values():
                            row.update(entries=0, bytes=0)
