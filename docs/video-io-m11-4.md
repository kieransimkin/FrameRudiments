# M11.4 — exact-presentation video input and rational-rate output adapters

**Status:** opt-in implementation candidate. This milestone adds local FFmpeg/
ffprobe *adapters* without changing `Project.fps` (still an integer), the
DanceRudiments source/audio clock, M11.1–M11.3 scheduling, post effects, or the
existing H.264/AAC production encoder. M11.5 is the separate gallery milestone.

## Video input: timestamp-indexed, not nominal-rate indexed

`VideoInput.open(path, sha256=..., ffprobe=...)` verifies the full source SHA-256
and builds an index of every *decoded presentation timestamp* reported by
`ffprobe`, together with the stream's **exact rational** `time_base`. Each
presentation time is `(best_effort_timestamp - first_pts) * time_base` as a Python
`Fraction`; first decoded presentation is time zero. This supports variable-
frame-rate media, nonzero origin timestamps and B-frame reordering without
pretending `avg_frame_rate` is the frame clock.

`frame_at(t, policy='floor'|'ceil'|'nearest', boundary='clamp'|'error')` maps an
exact rational source time to a frame ordinal. Nearest ties choose the later
frame. `frame_for_output(n, fps='30000/1001', ...)` maps an output frame grid to
the source presentation times **without floating-point rounding**. The adapter
clamps or errors at boundaries explicitly; it never invents interpolated pixels.
`read(index, ffmpeg=...)` independently decodes the selected ordinal to a PIL
RGB8 image, checking the SHA-256 before *and after* decode. It uses an exact
FFmpeg `select` filter from frame zero rather than a potentially inaccurate
GOP-keyframe seek. Random access is deterministic but can be slow on long clips.
The adapter suppresses automatic display rotation, preserving declared coded
width/height. No GPU decode, audio handling or scene-linear colour conversion
is promised.

The command-line inspection path is read-only:

```powershell
$hash = (Get-FileHash .\source.mp4 -Algorithm SHA256).Hash.ToLower()
framerudiments inspect-video .\source.mp4 --sha256 $hash --frames
framerudiments extract-video-frame .\source.mp4 --sha256 $hash `
  --at 1001/30000 --policy floor --output .\selected.png
```

`--index N` chooses a source ordinal instead of `--at T`; pass exactly one.
`--frames` prints all normalized decoded presentation times (can be large).
Every extraction fails if the PNG output already exists. On success it prints a
receipt with the input/output SHA-256 and exact source presentation timestamp.
No file changes are made to the source.

## Output: explicitly ordered RGB PNG manifest to exact-CFR MP4

`encode_manifest(manifest, output, ffmpeg=..., ffprobe=...)` accepts a UTF-8 JSON
manifest with a fixed ordered list of checksum-pinned RGB PNGs:

```json
{
  "schema": "framerudiments.video-frames.v1",
  "width": 1280,
  "height": 720,
  "fps": "30000/1001",
  "frames": [
    {"path": "frames/000000.png", "sha256": "<64 lowercase hexadecimal characters>"},
    {"path": "frames/000001.png", "sha256": "<64 lowercase hexadecimal characters>"}
  ]
}
```

`fps` may be an exact positive integer, decimal string or rational string in
1–120 FPS (including 24000/1001, 30000/1001 and 60000/1001). No implicit
conversion from a Python float is allowed. Entries are already in presentation
order. Each frame must be the declared size, even dimensions and **RGB8 PNG**.
Alpha conversion, colour-management and VFR *output* are outside this increment.
Frame width/height and each SHA are checked before writing; hashes are
revalidated as frames are read. Duplicate JSON keys are rejected.

```powershell
framerudiments encode-video .\frames.json --output .\video.mp4 `
  --preset fast --crf 20
```

The rawvideo input clock is passed as an exact fraction. H.264 `yuv420p` is
encoded with `-fps_mode cfr` and an explicit video-track timescale equal to the
rate numerator. After encoding, **every decoded frame PTS** must equal `n/fps`
exactly, starting at zero, and the output must pass a second full FFmpeg decode.
Only then are a *new* MP4 and adjacent `video.mp4.receipt.json` published; the
receipt includes frame checksums, manifest hash, output hash, exact FPS and
verification status. Temporary media is removed on failure. Already-existing
outputs/receipts are never overwritten. This adapter outputs *video only*;
the existing FrameRudiments renderer remains responsible for canonical
source-bound H.264/AAC programmes and audio evidence.

## Bounded resources and failure behaviour

- Local files only. No URLs, shell command strings, auto-downloading codecs or
  codecs outside the existing explicit `ffmpeg`/`ffprobe` binaries.
- Maximum indexed/output count: 240,000 frames; maximum probe response: 48 MiB;
  maximum raw RGB payload per frame: 256 MiB. Subprocesses have explicit
  timeouts, and import/export checks operate on checksum-pinned inputs.
- Stream selection is `v:0` by default (Python API may choose an ordinal 0–15).
  Missing/noninteger/repeated/out-of-order decoded PTS fail closed.
- Source and output do not share temporal state. Encoded output is constant
  frame rate by contract; VFR inputs are sampled by an *explicit* policy.
- The video adapter does not become a new effect-graph source node in M11.4;
  nor does it alter visualiser state reconstruction or auto-build clips from
  audio projects. Those integrations require separate schema/timing tests.
- Atomic no-replace publication uses a same-directory hard link. It requires a
  local filesystem that supports hard links (including NTFS); unsupported
  filesystems fail rather than replacing an existing output.

## Validation and acceptance

```bash
python -m pytest -q tests/test_video_adapters_m11.py
ruff check src/framerudiments/video_adapters.py tests/test_video_adapters_m11.py src/framerudiments/cli.py
```

The focused tests cover exact NTSC (24000/1001 and 30000/1001) output PTS and
full H.264 decode, deterministic out-of-order frame extraction, irregular VFR media plus synthetic
input timestamp calculations, rounding ties, invalid timelines, altered
hashes, malformed/duplicated JSON fields, video-only outputs, overwrite
refusal and failed-encode cleanup. Real FFmpeg tests skip if binaries are not
installed. Exact-head Windows/Linux CI with FFmpeg, physical-device playback,
VFR ingest fixture in a full real project, visual approval and the M11.5
Kandi Boom comparison gallery are **not** claimed here.
