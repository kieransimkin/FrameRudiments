# Bounded sequential preflight

`framerudiments preflight` remains a full, unencoded composition pass by
default. For a long or unusually expensive project, `--max-frames N` composes
only the first `N` frames in order:

```sh
framerudiments preflight project.json --output preflight-prefix --max-frames 3
```

The mode is a cost and failure-surface check. It verifies pinned inputs,
audio identity, project construction, sequential state creation, the declared
prefix and cleanup. It saves frame zero, ordinary checkpoints encountered in
the prefix and the last rendered prefix frame. `manifest.json` records:

```json
{
  "preflight_scope": {
    "mode": "bounded-prefix",
    "frames_rendered": 3,
    "total_frames": 15850,
    "complete": false,
    "sequential": true,
    "note": "Only the declared sequential prefix was composed; later frames remain unvalidated."
  }
}
```

## Why the prefix is sequential

Feedback visualisers, emitters and historical effects can depend on every
preceding frame. Randomly drawing distant checkpoints would reset or omit that
history and could create misleading evidence. The bounded mode therefore starts
at frame zero and advances normally. It does not describe later checkpoints,
the ending, full cleanup behaviour or the encoded result.

Run the complete `render` afterward. The renderer still composes every frame,
encodes the soundtrack, extracts all encoded QA checkpoints and verifies full
decode, constant frame rate, exact frame count, timestamps, dimensions, audio
shape and duration. Creative review and attended listening remain separate.

![Generated comparison of full and bounded preflight workloads](media/bounded-preflight.svg)

Generated explanatory diagram. The short amber prefix represents frames that
were actually composed; the outlined remainder is explicitly unvalidated.
