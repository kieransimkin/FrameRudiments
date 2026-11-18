# S5 — Section-scoped preview cache invalidation

**Status:** implementation candidate; focused cache/transport tests and exact-base
patch checks are separate from native pixel qualification and exact-head CI.
**Track:** [seekable adaptive streaming S0–S7](seekable-adaptive-streaming-plan.md).
S5 extends S1's PNG/SQLite store and S2–S4's existing stream and workers. It does
not replace the compositor, audio clock, offline render cache or quality ladder.

## Behaviour

Editing a time-bounded layer or its supported per-layer pipeline invalidates
**every retained frame of every affected section**, regardless of pixel equality
or cached quality rank. Independent sections keep their existing best frames,
image bytes and generation numbers. A later low-quality render cannot replace
retained Q5 pixels. Moving a layer considers both its old and new dependencies;
undo creates a new generation rather than reviving old frame URLs.

The stream uses one section-aware cache namespace per resolved project file,
rather than a separate cache directory for each edited draft. A new draft still
gets a new stream token and rendering owner. The old foreground worker and Q5
child must retire before the revision is admitted. Source media and authored
JSON are never changed by this operation.

## Which edits are local?

The dependency builder discovers scope from the actual document. Browser clients
cannot submit an authoritative list of sections to clear or preserve.

| Dependency | S5 scope |
| --- | --- |
| Time-gated sprite, bars or rudiment layer configuration | All sections that read its active source-time intervals |
| `post_effects.elements["layer:<id>"]` | The target layer's intervals, plus known consumers |
| `layer_animations`, `layer_masks`, `layer_displacement` for a known layer | The target layer's intervals, plus transitive source/consumer dependencies |
| Shared asset pin | Every section depending on any of its consumers |
| Global effect graph, node parameters, routing, full-frame/all-element effects | All sections depending on the shared pipeline; currently all sections |
| Source clock/window, FPS, dimensions, quality ladder or renderer implementation | All sections |
| Edited lyric/word/cue sidecars or unknown project fields | Conservative shared invalidation |

The current schema has no per-section envelope around a global effect-graph
node. S5 does not invent one. To author a truly local effect, use the existing
time-gated layer and scoped element pipeline. Unsupported layer semantics stay
conservative; the adaptive renderer continues to refuse unsupported stateful
execution rather than cache an incomplete proxy as Q5.

Enabling the first isolated layer pipeline can change the compositor's final
alpha/matte path even outside that layer's window. Isolation-dispatch flags are
therefore global dependencies. Changing an existing isolated effect's parameters
is local; changing the compositor's dispatch path is not falsely treated as local.
A change in which quality profiles are supported also invalidates globally.

## Authoritative time and dependency graph

Layer `start_seconds` and `end_seconds` are **source** time, not output time.
The builder intersects their half-open range with the validated audio sample
window and projects it through every repeat using exact rational arithmetic.
Fractional boundary frames are conservatively included where native temporal
supersamples may enter the layer. It never changes FPS, beat phase, frame index,
source timestamps, audio or native modulation.

Sections come from the existing native cue ledger, with the S0 fixed-duration
fallback. Edited cues are passed through `Project.ledger`, including repeat
cycles and initial blank boundaries. The partition is contiguous and half-open.
A boundary change removes obsolete section identities and all their frames;
unchanged disjoint ranges can survive when their dependency closures also match.
Cue-sidecar content changes remain shared because cues can drive other effects.

`preview_dependencies.py` builds a bounded explicit index containing:

- Hashed asset-to-layer/node edges, graph input edges and isolated layer source
  references, plus component-to-section reverse lookups.
- Effective per-section configuration digests, active layer order, complete
  shared settings, source pins, profile descriptors and installed implementation
  identity. Full derived-draft capability hashes are not used as local keys.
- Upstream-section-to-consumer-section edges for engine-declared temporal reach.

Dependency IDs in receipts are hashes, not source paths, layer labels or lyrics.
The implementation identity includes package code/bundled rendering resources
and installed native package versions. These are inspected on admission, not
per frame. This is not a cross-driver pixel-equivalence certification or a
security boundary against a hostile filesystem.

## Temporal reach and the S6 boundary

`TemporalReach` is a server/engine contract, never a browser edit hint. Bounded
past/future read windows expand invalidation into neighbouring sections.
Feedback reads propagate through downstream sections until a verified reset.
Unknown reach invalidates conservatively across the timeline. Tests cover both
halos and feedback/reset propagation.

**This does not implement stateful seeking or checkpoint reconstruction.** The
live adaptive renderer's existing eligibility checks still reject unsupported
feedback/history paths. Verified reset boundaries can only be provided by a
future engine adapter. S6/M11 owns native state restoration and its pixel tests.

## Atomic storage and generation ownership

`preview_segment_cache.py` subclasses the existing lossless `PreviewFrameCache`.
It retains PNG verification, staged/fsynced blob writes, monotonic rank CAS,
RAM LRU, disk quota, free-space reserve and corruption recovery. Small protected
identity hooks in the S1 store keep legacy keys unchanged.

S5 uses schema `framerudiments.preview-section-cache.v1` beneath
`<configured-stream-root>/section-cache-v1/<workspace-digest>`. Legacy S1/S2/S3
indices are not silently reinterpreted or imported. Opening with the old store
fails the schema check. Existing cache files are not deleted by migration.

A complete server-built manifest is reconciled in one `BEGIN IMMEDIATE`
transaction. The owner compares range and dependency fingerprints, deletes all
frame rows for affected sections, updates the section partition and hashes, and
commits its revision receipt atomically. Independent entries remain unchanged.
A monotonically increasing high-water counter prevents removed boundaries,
restored drafts and undo from reusing a revoked generation. Exhaustion fails
explicitly instead of wrapping.

Tickets bind workspace, section, generation, dependency closure, renderer ABI,
profile and absolute frame. Both foreground and background Q5 use the same
**per-section** effective-settings identity. An unrelated edit therefore cannot
cause a false same-rank settings conflict on a retained frame. Retired cache
handles cannot publish or read under the new revision; even an old ticket used
through the current handle is rejected for a changed section.

The live `revise()` path avoids re-decoding every cached PNG on each edit. Cold
open still performs corruption recovery; normal reads continue to validate
index/blob identity. Invalidated RAM entries are discarded and unreferenced
blobs are reclaimed under the existing store rules. Receipts count logical
frame bytes, not unique physical blob bytes, because images can be deduplicated.
A finite quota can evict independent frames: eviction is not a quality downgrade
and must not be confused with a guarantee of indefinite retention.

## Draft admission, workers and display

`/api/stream/start` optionally accepts an opaque per-page `client_id` and
increasing `edit_sequence`. The React client supplies them and reuses the same
sequence for retirement retries. A delayed earlier request or conflicting reuse
of a sequence cannot replace the latest draft. Server admission has its own
latest-intent epoch; it checks again after potentially slow source validation.
The anti-replay table is bounded to 64 clients without silently evicting sequence
history. Restart the local preview server to reset that bound.

The coordinator cancels pending work, stops refinement, waits for the previous
native owner to retire, and applies the cache revision. Its next renderer uses
the new immutable draft. The child process never revises or writes the cache.
Old image URLs and old session tokens cannot serve affected frames. An
unapproved pinned-source change revokes all sections once and requires reopening
a verified project.

React immediately hides a decoded image whose draft identity differs, even
before the prior effect cleanup runs. It also processes `cache-invalidated`
events, aborts old fetch/decode work and clears a displayed image in any revoked
old/new half-open range. Frame clocks and the shared audio timeline are untouched.
The UI shows revision, cleared-frame and retained-section counts.

## Diagnostic receipt

Stream status and the `cache-invalidated` SSE event expose a content-free
`framerudiments.preview-invalidation.v1` receipt. The SQLite metadata stores the
last meaningful transaction receipt. It includes the cause, revision, old/new
pipeline hashes, changed hashed dependencies, affected old/new ranges and
generations, retained section IDs, invalidated frame count and logical bytes.
The stream admission response adds the count of cancelled foreground/pending/
background jobs; this session-specific count is not part of the stored transaction.

Initial opens report `initial`; identical manifests report `unchanged`. A draft
change outside all effective render windows can report `nonvisual_revision`
without deleting valid frames. Source revocation, shared pipeline changes,
profile/ABI changes and boundary replacement have distinct causes.

## Tests and qualification

Pure discovery tests cover local edits, scoped effects/maps, global edits,
asset consumers, transitive references, cropped/repeated windows, fractional
boundaries, temporal propagation and malformed declarations. Real PNG/SQLite
tests cover whole-section tombstones, retained Q5 bytes, quality CAS, undo,
boundary restoration, rollback, concurrent publication, corruption and restarts.
Transport tests use the real cache/session code with explicitly synthetic
renderers to exercise reordered edits, retiring owners and source revocation.

`tests/test_preview_invalidation_native.py` separately requires the complete
native Project/Compositor. It compares retained Q5 pixels after opacity,
per-layer post-effect and animation edits and checks native edited-cue ledgers.
It is not replaced by the synthetic transport tests and contains no skip gate.
Run the affected Python checks through the repository-managed test wrapper:

```powershell
pwsh -File scripts/Invoke-Tests.ps1 tests/test_preview_dependencies.py tests/test_preview_segment_cache.py tests/test_preview_revision_stream.py tests/test_preview_invalidation_native.py -q
npm --prefix authoring test
npm --prefix authoring run typecheck
npm --prefix authoring run build
```

Existing S0–S4 focused suites remain regression gates. Exact-head CI owns the
full platform/GPU matrix; physical GPU, encoded-output and perceptual approval
remain separate. No publishing, model downloads, new network service, automatic
source import or production-render changes are introduced by S5.
