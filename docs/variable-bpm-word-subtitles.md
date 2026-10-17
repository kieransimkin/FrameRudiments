# StemLab variable-tempo motion and dual LRC subtitles

This is an **opt-in**, source-bound addition to FrameRudiments. It follows
StemLab's `stemlab.beat-timeline.v1` and the optional DanceMoves variable-tempo
consumer without copying either project's detector, editors or native motion.
Fixed `bpm`, legacy `beat_grid`, and the signed fixed-tempo `beat_alignment`
remain available and unchanged when the new field is absent.

## Source-bound variable-BPM map

StemLab's Beat correction editor exports **one** JSON document with the
sample-frame anchors, Beat 1 ordinal, 64 pips per beat, source SHA-256,
consensus SHA-256, provenance and optional derived `beat_timeline`,
`tempo_segments` and `sync`. Store the exact JSON alongside your project.

```json
{
  "clock": {
    "authority": "StemLab manually corrected consensus beats",
    "status": "estimated",
    "beat_timeline": {
      "path": "timing/beat-timeline.json",
      "sha256": "<real file SHA-256>"
    }
  }
}
```

The import checks `stemlab.beat-timeline.v1`, exact master SHA-256, original
consensus hash, `sample_rate` and source-frame bounds, 2–25,000 increasing beat
anchors, Beat 1, provenance, and the derived time/BPM/pip data if present.
Maximum input is 32 MiB. An **estimated** map cannot silently claim confirmed
BPM/phase. A nominal `clock.bpm` may be included as a display hint but is not
used to advance motion when `beat_timeline` is configured. Other clock sources
cannot be combined with it; that would double-apply phase correction.

The phase is evaluated in the native 64-pip convention:

```text
pip(t) = 64 * (beat_i - origin_beat)
       + 64 * (beat_(i+1) - beat_i) * (source_frame(t) - frame_i)
           / (frame_(i+1) - frame_i)
BPM_i = 60 * sample_rate * (beat_(i+1) - beat_i)
           / (frame_(i+1) - frame_i)
```

Outside the saved anchors, the closest observed interval supplies the slope.
No beat is fabricated. `clock.beat_at(source_seconds)` returns fractional
beat position; `tempo_at(source_seconds)` returns the actual *local* BPM.
Beat 1 can be a later marker; it maps to pip 0 without asserting that it is
an acoustically verified downbeat. Interpolation uses exact rational source
frames before converting to renderer float beats/pips.

The excerpt `start_sample` / `end_sample` is mapped to master time **before**
applying this clock, and procedural motion with `repeat_clock: continuous`
accumulates the exact number of observed beats per excerpt on every repeat.
`repeat_clock: source` replays native source phase. StemLab recorded motion,
existing cue timestamps, word/line LRC and original audio are **not**
re-timed or stretched. If the map doesn't match the chosen master, it fails
preparation rather than falling back to a guessed BPM.

## Word and line LRCs simultaneously

Keep the existing `lyrics` as the authoritative per-line file, then add a
separate checksum-pinned `word_lyrics` LRC:

```json
{
  "lyrics": {"path":"lines.lrc","sha256":"<line file SHA-256>"},
  "word_lyrics": {"path":"words.lrc","sha256":"<word file SHA-256>"},
  "captions": {
    "box": [40, 1200, 1040, 1370],
    "font_size": 52, "min_font_size": 35,
    "word_mode": "progressive", "word_colour": "#ffd480",
    "completed_colour": "#74e4d6"
  },
  "word_captions": {
    "box": [180, 1390, 900, 1500], "font_size": 38,
    "colour": "#f6e3c5", "stroke": 1
  }
}
```

Use *actual* hashes. `captions.word_mode` values are:

| Mode | Behaviour |
| --- | --- |
| `off` | Existing line subtitles, unchanged |
| `highlight` | Full current line, currently timed word highlighted |
| `progressive` | Full line, previously timed words and current word coloured independently |
| `current` | Only the current timed word, in the main caption box |

With both LRCs present and `captions.word_mode` omitted, the default is
`highlight`. `word_captions` adds a **separate**, independently placed current
word lane and can coexist with line highlighting. It uses the ordinary font,
box, stroke, colour and panel contract, so scoped post-effects may target
`caption` and `word-caption` independently. With word-only LRC, set a
`captions` block to render each word, or provide only `word_captions`.

Supported source layouts:

```text
# lines.lrc
[00:00.00]Signals into motion
[00:01.15]Words into light

# words.lrc — one original-source timestamp per word
[00:00.08]Signals
[00:00.39]into
[00:00.72]motion
[00:01.20]Words
[00:01.48]into
[00:01.80]light

# OR enhanced per-word timestamps inside a line
[00:00.00]<00:00.08>Signals <00:00.39>into <00:00.72>motion
```

Word LRC accepts BOM and `[offset:...]`, up to 30 decimal places per tag,
explicit empty clears, and a maximum 4 MiB/100,000 input words. Offsets use
the **same** convention as the existing FrameRudiments line-LRC parser.
Every nonempty timed word in paired mode is matched to the active line using
case-insensitive punctuation-insensitive token comparison. Untimed words may
remain in the line; they are never given fabricated onset timestamps.
Mismatched or out-of-line words are rejected during project preparation.
Highlight mode requires `captions.mode: current`, not `neighbours`.

Timestamps remain bound to the original audio source, even across variable
BPM changes, excerpts and repeated playback. Word start times are rounded
once to the encoded video frame using existing half-up ledger policy. An
explicit clear at an excerpt boundary prevents a stale word carrying into the
next repeat. Multiple words sharing an encoded frame remain deterministically
ordered. Last word presentation continues until the following timed clear or
line boundary; if you need a shorter final hold, include an empty timestamp.

## Exports and verification

Rendering/preflight continue to generate `captions.srt` and `captions.ass`
from the normal line ledger. When `word_lyrics` is present they also write
`word-captions.srt` and `word-captions.ass`, bounded at line changes and repeats.
These exports, previews and raster text all use encoded-frame boundaries.
The project plan records map and input-file SHA identities and word event
counts. `FrameContext.word` exposes the currently active timed word to trusted
scene hooks without changing the meaning of `FrameContext.lyric`.

The CI gallery at
[FrameRudiments/timing](https://kieransimkin.github.io/FrameRudiments/timing/)
demonstrates fixed/variable motion clocks, line-only text, highlighted words
and simultaneous word+line captions. It uses a **synthetic tone and fictional
lyric snippets**, not StemLab acoustic analysis or any private song material.

Local checks:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_variable_bpm_words.py tests/test_beat_alignment.py tests/test_lyrics.py
python scripts/generate_visualisation_gallery.py --output _site
python scripts/generate_timing_gallery.py --output _site
ruff check .
```
