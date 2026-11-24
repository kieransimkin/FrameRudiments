# M15 — experimental ecosystem adapters implementation plan

**Scope:** explicit, opt-in, externally qualified integration layers. No single
experimental adapter may silently change the deterministic RGB8 compositor,
source timing, pinned audio, M11.4 FFmpeg export, M12 native geometry AOVs, M13
editorial interchange, or M14 model-estimate provenance. All executable native
plugins, GPU runtimes and network transports must have independent capability,
resource, cleanup and licensing gates.

## M15.1 — GStreamer offline video export (implementation candidate)

- [x] Introduce `framerudiments-gstreamer inspect` with a real native-element
  availability and version report.
- [x] Encode SHA-256-pinned M11.4 RGB PNG manifests using a closed, bounded
  native GStreamer H.264/MP4 pipeline. Preserve row strides and rational FPS.
- [x] Independent FFmpeg/FFprobe full-decode and frame-by-frame exact-PTS proof.
- [x] No-overwrite publication, staging rollback and machine-readable receipts.
- [x] Dependency-light unit tests and a required native GStreamer Ubuntu CI job
  with published encoded evidence.
- [ ] Exact-head GitHub Actions and cross-platform native qualification; no
  claim of physical GPU encoding, live streaming or audio muxing.

Reference: [GStreamer adapter and acceptance contract](gstreamer-m15.md).

## M15.2 — GStreamer offline ingest and optional live media graph (implementation candidate)

- [x] Read SHA-256-pinned local video via GStreamer and independently check
  **all** decoded presentation PTS against M11.4's exact VFR FFprobe index.
- [x] Produce a bounded, independently hashed PNG/PTS ledger, not a fabricated
  constant-frame-rate export or a changed native render clock.
- [x] Report pinned audio sample-clock alignment without implicit resampling or muxing.
- [x] Finite, opt-in `videotestsrc` capture with bounded leaky queue, timeouts,
  whole-attempt reconnection, OS process supervision and partial-frame cleanup.
- [x] Strictly opt-in `/dev/videoN` and unauthenticated loopback RTSP TCP
  input profiles; no default device enumeration or network access.
- [x] Native GStreamer/FFmpeg qualification fixture and CI-generated visual
  demo with actual decoded images and a playable VFR source MP4.
- [ ] Post-merge exact-head CI, physical capture-device and real RTSP source
  acceptance. These cannot be inferred from a synthetic GStreamer source.

Reference: [M15.2 ingest and finite live capture contract](gstreamer-m15-2.md).

## M15.3 — OpenFX plugin-host boundary (implementation candidate)

- [x] Read-only, versioned, SHA-256-pinned PE/ELF/Mach-O descriptor admission
  with an explicit trusted-execution decision and licence declaration. The
  scan **never loads the binary**, and cannot prove the declared licence.
- [x] Native OpenFX Image Effect v1 **Filter** subset: real `OfxPlugin` entry
  calls and standard property/image-effect suite layouts, one Source/Output,
  CPU RGBA8 straight-alpha pixels and exact output frame time.
- [x] Run plugin code only after separate Windows-Job/POSIX process ownership;
  bounded inputs, timeout, no-overwrite publication, crash/error cleanup.
- [x] Original MIT C reference plugin with independent RGB/alpha pixel golden,
  plugin-abort/failed-status/deadline tests and separate native CI gate.
- [x] Publish generated visual side-by-side frames and hash receipts via the
  existing CI Pages gallery, with no executable binary in published assets.
- [ ] Exact-head native Windows/Mac ABI, independently reviewed third-party
  licences/implementations, parameter suites, GPU frame sharing, total RAM
  caps and perceptual approval remain separate gated work.

Reference: [M15.3 OpenFX host and acceptance contract](openfx-m15.md).

## M15.4 — OpenUSD / Hydra and interchange (implementation candidate)

- [x] Read trusted, SHA-256-pinned single-layer ASCII USD in an optional
  time-limited native `usd-core` worker; reject external `@...@` assets, sublayers,
  payloads, variant arcs and unsupported prims and materials, without modifying
  the main compositor or project clock.
- [x] Resolve time-sampled triangle meshes through actual USD XformCache,
  support Y/Z stage up-axis and explicit metres-per-unit, read perspective
  camera transforms/optics and constant UsdPreviewSurface/displayColor materials.
- [x] Deterministic CPU raster in the M12 unassociated scene-linear float32
  buffer contract; independent positive camera-space depth in **metres**,
  camera-space facet normals and alpha, with exact EXR readback and receipts.
- [x] Cross-platform native USD/OpenEXR CI with three time-sampled frames and
  a CI-generated visual gallery; no auto-downloads during scene processing.
- [ ] Hydra delegate/device rendering, texture networks, subdivision, skeletal
  animation, partial near-plane clipping, independent OpenUSD cross-vendor
  parity and licensing acceptance remain explicit separate gates. Capability
  inspection must not silently advertise a renderer.

Reference: [M15.4 OpenUSD scene and AOV contract](usd-m15.md).

## M15.5 — Live transport, WebGPU/Vulkan and control protocols

- [x] First bounded control increment: explicitly configured IPv4 loopback OSC
  int32/float32 capture, native numeric graph bindings, source-frame leases,
  closed finite takes and hash-pinned network-free replay.
- [x] Native compositor integration preserves encoded/source/continuous-beat
  identity and holds controls across native temporal subframes. Real UDP,
  packet rejection, closure, input-pin and native render/decode qualification.
Required qualification uses exact-source CI and immutable 0.4.35 publication.
LAN, bundled OSC/timetags and the transport/device work below remain
separate increments. The MIDI increment is recorded below. The workflow run and release receipts establish delivery.

Reference: [M15.5 OSC capture and replay contract](osc-controls-m15.md).

- [x] Next control milestone (0.4.36): explicit MIDI CC/pitchbend capture,
  native numeric mapping and source-frame leases, closed finite hash-pinned
  takes and ordinary device-free replay using the shared numeric take engine.
- [x] Capture host admission uses official Python 3.11/3.12 native binaries;
  metadata inspection and replay stay native-import-free on Python 3.13+.
- [x] Required native RtMidi/JACK virtual-port CI, exact close/delete and owned
  dummy-server cleanup, native 60 fps exports/full decode and Windows binary
  inspection. Physical-controller acceptance remains separate.

MIDI delivery requires exact-source checks and immutable 0.4.36 publication;
the release and qualification receipts establish the completed software state.
This does not complete all M15.5 network/device phases.
Reference: [MIDI contract, actual frame and native demonstration](midi-controls-m15.md).


- Transport profiles for explicit, consented local-network endpoints (GStreamer,
  NDI, Spout, Syphon), with bounded buffers, monotonic clocks and disconnect tests.
- Vulkan/WebGPU adapter capability plans with shader parity against CPU/Mesa
  reference, real device identity, memory telemetry and zero silent fallback.
- Opt-in OSC/MIDI control bindings use existing DanceRudiments time and typed
  effect-graph parameters rather than a competing media clock.

**Acceptance common to every phase:** no hard dependency for ordinary installs;
exact input/output pins; reproducible generated fixtures; explicit unsupported
capabilities; no background processes surviving the owner; per-adapter CI; no
claimed hardware/codec or perceptual success without measured results.


## Note-performance visualisers — 0.4.39 increment

The next authorised increment adds a source-bound note timeline, a bottom piano
keyboard with falling or causal rising blocks, and a perspective note highway.
MIDI CC/pitchbend controls keep their existing contract; a separate note capture
owner reuses the same qualified input lifecycle. SMF conversion retains native
tempo changes with an explicit offset. Notes share ordinary composition,
per-layer cache, native quality profiles and spawned Q5 replay.

Acceptance is owned by exact-source CI, the native note qualifier and matching
immutable package publication. Required checks include real RtMidi/JACK note
capture, key-up/sustain distinction, independent wire vectors, tempo/seek
behaviour, selective cache reuse, exact decoded reference parity and legal
chunk/resume output. Physical-controller acceptance remains a separate result.

[Actual native views and input contract](note-visualisers.md) · [Research shortlist](note-visualiser-research.md).
Drum lanes, pitch-class wheels, horizontal rolls and authored string/fretboards
remain proposed follow-ups; this increment does not close the separate network,
GPU, transport or physical-device phases of M15.5.
