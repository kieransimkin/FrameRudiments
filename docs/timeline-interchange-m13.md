# M13 — OpenTimelineIO import/export and exact source provenance

**Status: M13 implementation candidate.** This is a bounded, **opt-in edit-decision-list
interchange**, not a new renderer or a replacement for the native FrameRudiments
clock, DanceRudiments motion or StemLab source analysis. Existing JSON projects
and legacy rendering are unchanged.

## Supported model

M13 serialises a versioned `framerudiments.timeline-interchange.v1` manifest to
OpenTimelineIO 0.18's **built-in `otio_json` adapter only**, using standard
`Timeline`, `Track`, `Clip`, `Gap`, `Transition`, `Marker`, `ExternalReference`,
`RationalTime` and `TimeRange` objects. The adapter imports those same flat
constructs into an independently validated manifest. Audio and video tracks may
have different lengths; the programme duration is the maximum of their clip+gap
lengths. Transitions are separately authored overlaps **between two adjacent
clips** and do not artificially extend the programme length.

All times use exact rational clocks, not float-authored time bases:

- `fps` is `[numerator,denominator]`, e.g. `[30000,1001]`.
- `duration_frames`, marker `frame`, transitions' `in_frames`/`out_frames` are
  **integer output-grid frames**, no implicit fractional-frame rounding.
- `source_fps` uses the same rational pair syntax, but may be a video frame
  rate or an **audio sample rate** (e.g. `[48000,1]`). `source_start_frame` is
  a source-clock tick in that rate. Clips are 1x speed; their duration in
  source ticks must be integral on both declared clocks.
- OTIO stores numerical rates as doubles, so the exact source/output rational
  pairs and edit frame counts are also carried in the private
  `metadata["framerudiments"]` namespace. Import checks OTIO's actual time
  range against the metadata; mismatched edits fail rather than resurrecting a
  stale value. External OTIO must supply `--fps` if that namespace is absent.

### Input manifest

```json
{
  "schema": "framerudiments.timeline-interchange.v1",
  "name": "A documented clip and cue study",
  "fps": [30000, 1001],
  "tracks": [
    {"id": "V1", "kind": "video", "items": [
      {"type": "clip", "id": "start", "name": "First shot",
       "source": {"uri": "media/shot-a.mp4", "sha256": "<actual 64-character lowercase SHA-256>"},
       "source_fps": [30000, 1001], "source_start_frame": 150,
       "duration_frames": 150},
      {"type": "transition", "id": "fade", "style": "dissolve",
       "in_frames": 15, "out_frames": 15},
      {"type": "clip", "id": "end", "name": "Second shot",
       "source": {"uri": "media/shot-b.mp4", "sha256": "<actual SHA-256>"},
       "source_fps": [30000, 1001], "source_start_frame": 0,
       "duration_frames": 150}
    ]}
  ],
  "markers": [
    {"id": "drop", "name": "DROP", "frame": 150,
     "duration_frames": 0, "color": "YELLOW"}
  ]
}
```

The SHA strings above are **placeholders**, not valid executable manifest values.
An empty unverified checksum is represented by JSON `null` and requires an
explicit `--allow-unpinned` flag; it is never silently treated as trusted.

## Command line

```bash
python -m pip install -e '.[dev,timeline_otio]'
framerudiments-timeline validate montage.timeline.json --verify-sources
framerudiments-timeline export montage.timeline.json --output montage.otio --verify-sources
framerudiments-timeline import montage.otio --output reimport.timeline.json
framerudiments-timeline project-export ./my-demo/project.json --output ./my-demo/audio-cues.otio
```

All output filenames are **new**; neither media, project files nor the
pre-existing companion `.receipt.json` may be overwritten. A normal export
writes a canonical `.otio` and cryptographic receipt only after an independent
OTIO JSON decode and frame-exact reimport. Both files are published by local
no-overwrite hardlinks, and partial publishing is rolled back on failure.

`--verify-sources` checks on-disk source bytes against SHA-256 and refuses media
escaping the chosen root, including symlinks, without fetching anything. The
source URI is a portable relative path **from the exported OTIO's directory**.
The importer never follows any media reference. OTIO files are capped at 8 MiB,
with at most 32 tracks, 16,384 items, 4,096 markers and 12 hours at 120 fps.
This is **not** a security sandbox for untrusted native codecs or custom OTIO
plugins; only the explicitly named core JSON adapter is used.

For a foreign `.otio` without FrameRudiments rational metadata, import needs
`--fps 24000/1001` (or another explicitly selected clock). Unpinned editorial
media needs the separate `--allow-unpinned` opt-in. Non-frame-aligned trims,
remote URLs, absolute paths, nested tracks/stacks, timewarps, local clip markers,
unknown effects, unsupported transition types, disabled items and other lossy
structures are rejected rather than dropped. Standard SMPTE dissolves are
represented as dissolves. A wipe uses a documented `Custom` OTIO transition
with private `style=wipe`; other editors are **not** guaranteed to render it.

`project-export` verifies the native project inputs and emits its pinned source
audio and section cues, including repeated exact audio excerpts. It requires
whole output-grid frames for every audio slice and an output `.otio` beside the
original project. It does **not** pretend that procedural 3D layers, visualiser
passes, effects, keyframe animations or original music analysis are finished
video clips. Importing a timeline is an interchange operation; it does not
rewrite `framerudiments.project.v1` or automatically author a new render graph.

## Public timeline viewer and CI

The [live Pages timeline](https://kieransimkin.github.io/FrameRudiments/timeline/)
retains the real React component and original, 16-second, synthetic 120 BPM WAV.
Its **Download OTIO example** link points to a CI-generated `.otio` carrying
that exact WAV's checksum and the four cue markers at seconds 0/4/8/12. The
procedural visualiser lanes are a clearly labelled **video gap**, not invented
video footage. Browser-side edited word markers are not included in the static
example download.

The [M13 CI workflow](../.github/workflows/timeline-otio-m13.yml) requires real
OpenTimelineIO 0.18 bindings on Windows/Linux and Python 3.11/3.13, exercises
import/export with 24 fps, NTSC 30000/1001 and 60000/1001, rejects unsupported
structures and publishes a verified example artifact. The existing gallery
workflow places the same example under `timeline/example.otio` on Pages and
verifies its SHA and readback before deploying. **Exact-head CI and live
publication remain acceptance gates until their runs complete after merge.**

## Further M13 work

The supported v1 subset covers flat interchange deliberately. Editorial
conformance with full nested OTIO constructs, variable-speed effects,
roundtrip editable word/keyframe lanes, media proxy relinking, cross-track
transitions and procedural layer reconstruction would require separate explicit
schemas and rendering semantics, not permissive conversion guesses.

The subsequent main roadmap stage is M14: pinned computer-vision analysis
(semantic mattes/depth/flow) with model identity, resource budgets and opt-in
inference, without violating the native source motion contract.
