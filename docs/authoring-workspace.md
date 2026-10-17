# FrameRudiments Phase 5 authoring workspace

The authoring interface uses **[react-timeline-sequence](https://github.com/kieransimkin/react-timeline-sequence)
0.4.0**, the same released shared timeline control used by StemLab. FrameRudiments
does not copy, fork or reimplement its playhead, zoom, beat grid, loop transport
or drag-to-edit timing behaviour. It supplies domain-specific lanes, safe
authoring state, a local renderer preview and a deliberately export-only path.

## Run locally

Node.js >=22, Python >=3.11, FFmpeg, and the normal FrameRudiments dependencies
are required. From a current checkout, run:

```powershell
python -m pip install -e '.[dev]'
cd authoring
npm install
npm run test
npm run typecheck
npm run build
cd ..
python scripts/authoring_preview.py my-project/project.json --port 8765
```

Open **http://127.0.0.1:8765**. The server binds only to loopback and never
updates project, audio, lyric, cue or analysis files. No accounts or cloud
backend are involved. Never proxy the preview server onto the public internet.

For frontend development, keep the Python server running, then execute
`npm --prefix authoring run dev`. Vite runs on `127.0.0.1:5173` with a
same-origin `/api` proxy. Start the backend with
`--dev-origin http://127.0.0.1:5173` to explicitly allow this second local
origin for preview POST requests. No non-loopback origins are accepted.
Generated `authoring/dist/` files are ignored by Git.

The first npm install creates a local package lock. Commit that lockfile before
publishing or redistributing the UI; the stable core dependency is pinned to
**react-timeline-sequence@0.4.0**. The React package has the transport and
editable timing contracts in `docs/timing-editing.md` of its upstream project.

## Editing operations

- **Timeline:** Actual LRC lines, enhanced per-word LRC timings, cue LRC or CD
  CUE sheet display, and the project layer visibility windows; times share the
  output programme ruler. Existing `.cue` sheet titles are read-only until
  explicitly renamed as valid LRC section cues.
- **Pips/tempo:** A fixed BPM view is derived from the existing project clock;
  a variable grid uses ONLY the exact `stemlab.beat-timeline.v1` sample-frame
  anchors. The grid is disabled for repeated programme excerpts to prevent
  falsely presenting a continuous single-source frame clock.
- **Keyframes:** Existing `layer_animations` normalized envelopes become
  draggable points using the shared component's editable point timing control.
  The label-value inspector authors `opacity`, `x`, `y` and `rotation` values
  as 0..1 before FrameRudiments maps them to native property ranges.
- **Graph:** Native layer nodes, Butterchurn/projectM stages and final output;
  configure per-layer `visualiser_routing` feed/overlay and individual
  `visualiser_stage_injections` for later stages. Effect-graph presets preserve
  masks, displacement and routing, but NEVER include source assets or music.
- **Presets:** Style presets are copied into a selected Rudiment layer without
  changing its native DanceRudiments `motion` or pinned StemLab recording.
- **Undo/redo:** Up to 100 complete reversible draft operations, in browser
  memory only. Nothing is automatically pushed to GitHub or published.
- **Preview:** A local `Compositor` renders a PNG for the requested output frame.
  Fast mode explicitly lowers quality to 1× spatial / 1 temporal sample; full
  mode uses the original project settings. **Sequential Butterchurn/projectM
  effects are not replayed in this interactive preview.** Use a real preflight
  and encoded render to approve those stages.

Source lyric/cue timestamps are converted to/from the output excerpt's display
ruler without modifying the approved source audio clock. Repeat cycles share
one authoritative source-timed sidecar edit; the editor does not materialise
one new LRC per repeat. StemLab and DanceRudiments inputs and provenance remain
source-bound, checksum-pinned and immutable during authoring.

## Export and verification

**Export project & sidecars** produces a standards-compatible ZIP with a new
`project.json`, edited `.lrc` sidecars, and `AUTHORING-README.txt`. Every newly
written sidecar is hashed using browser WebCrypto SHA-256, with its hash stored
in the new project ref. Unedited font, image, audio, model and recording file
references retain their original SHA-256. No original master/source bytes are
included. Extract the ZIP **into the original project directory** to preserve
relative pinned paths; do not overwrite sources automatically.

Run the normal `framerudiments plan`, `preflight` and `render` commands against
that exported project. Preview success does not imply approved encoded output,
correct song analysis, acceptable GPU effects or platform compliance.

## Testing and known constraints

`tests/test_authoring_preview.py` tests source protection, cue conventions,
local-only static/audio APIs, and refusal to read arbitrary new project refs.
The UI's Node tests exercise repeated excerpts, LRC parsing/export, cue naming,
keyframes, effects graphs, presets, hashes and valid ZIP headers. The
`.github/workflows/authoring.yml` matrix tests Python 3.11/3.13 on both
Windows/Ubuntu and builds the React UI with the released timeline dependency.

The first release uses the shared component's editable **cue-shaped timing
marker** for keyframes, because its published v0.4.0 `TimingKind` supports only
`line|word|cue`. A separate companion patch for `react-timeline-sequence`
introduces a semantic `keyframe` kind and accessible styles. Publish that
upstream change as a new stable version first; FrameRudiments must not install
an unpublished Git commit or fork to consume it. The currently published
package works without this extension.

For very long songs, large FFT archives or CPU-heavy 3D scenes, preview requests
may be slow; they are bounded to two concurrent renders and are not cached
across edits. Playback of clipped or repeated source programmes requires the
user to select an exact rendered audio excerpt. Direct full-file audio is
served only when a WAV has `start_sample=0`, `repeats=1` and a matching
sample-rate/end-sample count. Trimmed or compressed masters require an exact
rendered audio excerpt selected by the user.
