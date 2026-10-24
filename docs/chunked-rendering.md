# Chunked rendering

FrameRudiments 0.4.10 makes long renders recoverable without changing the
authored frame clock. Projects over 30 seconds use sequential chunks by default.
The planner balances the requested chunk duration, then prefers a nearby cue
from the pinned CUE/LRC structure ledger. Boundaries are start-inclusive and
end-exclusive, covering every project frame once.

![Generated diagram showing cue-aware chunks, replay and final assembly](media/chunked-rendering.svg)

*Generated architecture diagram. It explains the verified pipeline; it is not
a rendered campaign frame or proof of a particular production run.*

## Continuity contract

- Every frame keeps its absolute project number, source time and beat time.
- Composition, temporal supersampling, long-shutter accumulation and visualiser
  feedback advance sequentially from frame zero.
- Resume accepts only an exact project and chunk-plan match. It verifies the
  contiguous completed receipt prefix, replays those frames without encoding,
  then writes the first missing chunk.
- Chunks contain H.264 video only, start at timestamp zero and use compatible
  stream parameters. The original programme audio is sliced, repeated and
  faded once during final assembly.
- Final assembly uses FFmpeg's concat demuxer and stream-copies video. The full
  result then receives frame-count, CFR timestamp, codec, dimensions, audio,
  fast-start and full-decode checks. Decoded RGB hashes immediately before and
  after every boundary must match their source chunk frames.

This preserves gap-free playback, source-time alignment and composed frame
continuity. Independently encoded H.264 chunks are not promised to be
byte-identical to one monolithic H.264 encode; decoded seam verification proves
that assembly did not drop, duplicate or alter the boundary frames.

## Commands

```powershell
# Automatic for projects longer than 30 seconds
framerudiments render project.json --output render-001

# Force chunking and aim for section cues near 45-second intervals
framerudiments render project.json --output render-001 --chunked --chunk-seconds 45

# Continue the verified finalized prefix after an interruption
framerudiments render project.json --output render-001 --resume --chunk-seconds 45
```

`--min-free-gib` reserves disk space before each new chunk and before final
assembly. An interrupted active chunk is archived on resume; finalized chunks
with valid receipts remain immutable. Keep a long or high-quality invocation
under an operating-system scheduler or service so the render survives the
calling application.

The documentation uses a generated SVG because the feature is an execution and
evidence contract. Synthetic integration tests encode, interrupt, resume,
assemble and fully decode real video; no private release footage is included.
