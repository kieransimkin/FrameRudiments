# Beat alignment from saved StemLab evidence

![Actual FrameRudiments 0.2.0 encoded example: original and aligned native movement with source-time captions and recorded bands](media/beat-alignment.png)

[Play the four-second example](media/beat-alignment.mp4). Original synthetic
120 BPM clicks begin at 0.125 seconds. The left native bounce shows the original
grid; the right follows the -16 pip reading. The bands retain their recording
clock. Text changes at the original LRC times: “Signals into motion” at 0,
“Words into light” at 1.25, “Keep the clock in sight” at 2.50, then clears at
3.75 seconds. The synthetic text is a caption fixture, not sung lyrics.
[Encoded verification, source identity and hashes](media/beat-alignment-evidence.json).

Generate a source-bound reading from saved StemLab evidence, reviewing canonical,
combined, fitted and half/double-time interpretations and individual detectors:

```sh
stemlab beat-offset RESULTS --output NEW --audio EXACT-MASTER.wav \
  --alignment-analysis consensus --alignment-bpm-basis combined_fit
```

Use `canonical` only when canonical BPM has an established authority. Retain the
full comparison report; a good beat-grid fit is not a listening or downbeat
confirmation. A normal half-time reading does not mean a detector failed.

Pin the resulting file exactly as every other project input:

```json
"clock": {
  "authority": "Reviewed StemLab consensus constant-grid fit",
  "status": "estimated",
  "phase_status": "estimated",
  "beat_alignment": {"path": "beat-alignment.json", "sha256": "EXACT_FILE_SHA256"}
}
```

The adapter validates the master SHA-256, source sample rate/frame bounds,
64-pip units, detector/report identities and exact rational clock equation.
An explicit BPM must agree. Do not also supply `origin` or `beat_grid`; the
reading owns this clock. An estimated BPM cannot become `confirmed` through
project metadata, and phase authority stays separate.

The existing BeatClock uses the first source beat as its origin. At that instant
the procedural clock is pip zero; before it, pips can be negative. Excerpts and
repeats map source time first and apply phase once. Keep the original master,
LRC, CUE and captured recording clocks unchanged. A recording's captured beat
zero and integer samples remain authoritative, including Fourier dimensions.

Missing, mismatched or poor evidence remains a review hold. A constant-grid
reading is not proof that a variable-tempo song fits one BPM. Existing projects
without a reading retain their explicit clock and report unverified phase when
no phase status was supplied.

Reproduce the actual fixture with `python examples/beat-alignment/generate.py
NEW-DIRECTORY --font /path/to/font.ttf`, then use normal `plan`, `preflight` and
`render` commands. Keep technical encoded QA separate from attended listening,
creative approval and any platform publication.
