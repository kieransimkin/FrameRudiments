# S1 — Monotonic persistent preview-frame cache

**Status:** S1 implementation candidate; exact-head CI pending. This is a
storage/invalidation component, **not an active streaming renderer**. See
[seekable adaptive preview plan](seekable-adaptive-streaming-plan.md).

## Storage model and identity

`framerudiments.preview_frame_cache.PreviewFrameCache` stores **one highest-ranked
retained RGBA8 frame** for each absolute frame index in the current validated
project revision. It does **not** share the `WorkingCache` output tree or change
final FFmpeg renders. The caller chooses a private fast-disk root (for example,
`local/preview-frame-cache/`, already ignored by Git). No images, lyrics,
recordings, projects or source paths are put into the codebase or public UI.

- Versioned SQLite index (`framerudiments.preview-frame-cache.v1`) with a WAL,
  `synchronous=FULL`, and write-serialising `BEGIN IMMEDIATE` transactions.
- Immutable content-addressed lossless RGBA PNG blobs under `blobs/ab/<sha256>.png`.
  Source images are encoded as RGBA8, never scaled after admission.
- Explicit project fingerprint, complete input/reference closure, renderer ABI,
  pipeline fingerprint, **profile ladder hash**, half-open segment boundaries
  and segment generations. An old cache root cannot silently become a new
  project's cache. The caller must verify source pins and effective shader or
  rendering pipeline **again** before requesting/publishing a frame; this
  module cannot authenticate arbitrary caller-supplied hash strings.
- `FrameTicket` captures frame, segment, generation and the rank-specific
  fingerprint. The ticket does **not** certify that the caller can really
  render Q0–Q5 or that Q5 has full production/feedback equivalence; S3/S6
  establish and validate those renderer capabilities. `CachedFrame` always
  has `production_equivalent: false` in S1.

## API (not yet wired to playback)

```python
from framerudiments.preview_stream_contract import (
    project_fingerprint, quality_profiles, section_segments, canonical_digest,
)
from framerudiments.preview_frame_cache import PreviewFrameCache

# project is a validated Project, and its SHA-pinned inputs have been checked.
project.verify_inputs()
project_hash = project_fingerprint(project, renderer_abi="app+code+shader-abi-id")
segments = section_segments(project, project_hash=project_hash)
cache = PreviewFrameCache(
    "local/preview-frame-cache",  # private and ignored by Git
    project_fingerprint=project_hash,
    pipeline_fingerprint=canonical_digest({"effective_pipeline": project.data}),
    renderer_abi="app+code+shader-abi-id",
    segments=segments,
    profiles=quality_profiles(project),
    max_bytes=2 * 1024**3,
    ram_bytes=32 * 1024**2,
    min_free_bytes=256 * 1024**2,
)

# An actual renderer in a later milestone will produce a verified RGBA8 PNG
# with the precise dimensions of the requested profile and effective settings.
ticket = cache.ticket(100, "Q3")
# cache.publish(ticket, rendered_png_bytes,
#               settings_sha256=canonical_digest(effective_frame_settings))
cached = cache.get(100)  # None until a frame has been admitted
```

The cache performs **no render or worker scheduling** and cannot invent a
production-quality frame. Pixel PNGs and settings digests must be supplied by
a pipeline that genuinely used the declared profile; fabricated Q5 labels
remain a caller contract violation until S3 validates actual quality variants.

`publish` returns `inserted`, `upgraded`, `unchanged`, `stale`, or
`quota_rejected`. A same-rank pixel/settings disagreement raises
`CacheIntegrityError`; it does not replace the existing result. Reads return
the **highest retained** rank, never a silently downgraded image. A frame
can be missing due to quota eviction, corruption or explicit invalidation.

## Atomicity, cleanup and quotas

1. Validate exact RGBA8 PNG dimensions, byte count, decoded pixels and SHA-256.
2. Start a SQLite immediate transaction. Check the **current** project/pipeline
   identity, segment generation, profile definition and frame ticket.
3. Reject stale results and rank downgrades; equal ranks must have identical
   decoded pixels **and** effective-settings hash.
4. Write each new blob into a unique same-volume staging file; flush and
   `fsync`, verify the payload hash, then atomically rename the blob. A durable
   index commit follows the blob promotion, **never precedes it**.
5. Apply the quota by evicting *whole frames*, considering nonactive sections,
   lower ranks and least-recently-accessed frames first. A protected incoming
   frame is never downgraded; it may be rejected if it cannot fit. Distinct
   blob bytes are counted once; at most 100,000 frame entries are retained, and a configurable free-space reserve is
   checked before writing. SQLite/index overhead and filesystem allocation
   granularity are additional costs outside the PNG quota.
6. Only after index commit, delete unreferenced content-addressed blobs. On
   restart, check each referenced row/image and remove corrupt rows and orphan
   staging files/blobs. Readers validate the current index generation and
   profile; RAM LRU hits are also checked against the file's inode/mtime/ctime
   identity, while cold reads perform full pixel/hash verification.

A crash before the SQLite commit can leave an **orphan**, never a committed
partial frame. A crash after commit can leave an obsolete blob, deleted during
recovery. Corrupt database metadata or a foreign project revision **fails
closed** rather than reusing images. SQLite recovery handles interrupted
transactions. On hostile/untrusted filesystem modifications, separate
OS/filesystem isolation is necessary; this cache is not a security sandbox.

## Invalidation and remaining milestones

`invalidate_all()` atomically increments *every segment generation*, clears
all frame references, and advances a persistent session revision. Running
workers with older tickets may finish, but `publish` refuses their results.
`revise_all()` takes new fingerprints and a new segment ledger, and clears the
cache when the project, shader, effects, colour space, renderer ABI or pipeline
changes. Old cache instances cannot read another instance's revision. Fresh
processes can reopen a root after an invalidation using the original
Project-derived segment boundaries; the persisted generations are authoritative.

**S1 deliberately invalidates the entire project**, because the current S0
project fingerprint cannot prove the dependency closure of a single segment.
S5 will introduce a verified reverse-dependency index and **whole-section**
selective invalidation without incorrectly retaining cross-boundary history.
Until then an edit invalidates all segments rather than risking stale frames.

**Next: S2** adds local streaming/seek orchestration, event transport and
coalesced foreground render requests. S3 adds *renderable* quality tiers and
measured stepwise fallback; S4 adds a separate full-quality refinement worker
anchored at the last stopped playback cursor. S6/M11 is necessary for safe
seeking of stateful Butterchurn/projectM and temporal-feedback effects.

## Tests

```powershell
pwsh -File scripts/Invoke-Tests.ps1 tests/test_preview_stream_contract.py tests/test_preview_frame_cache.py tests/test_authoring_preview.py -q
```

The cache tests cover Q5-before-Q0 races, same-rank mismatches, forced quota
rejections, eviction without downgrade, cross-instance concurrency, persisted
generations after restart, explicit global revision invalidation, blob corruption,
orphan/torn-write recovery, safe root checks, and fail-closed foreign metadata.
They require Pillow and Python 3.11+, not ModernGL, NumPy or FFmpeg. Full
exact-head Linux/Windows CI must still verify SQLite/fsync behaviour.
