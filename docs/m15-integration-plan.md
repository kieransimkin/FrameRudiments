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

## M15.4 — OpenUSD / Hydra and interchange

- Optional pinned local USD scene import; strict external asset references,
  materials, camera matrices, scale and frame-time semantics.
- Map supported geometry/AOVs to M12 without converting model-predicted depth
  into native geometry. Unsupported prims and shading are explicit diagnostics.
- Hydra rendering requires dedicated native/driver parity and licensing checks.

## M15.5 — Live transport, WebGPU/Vulkan and control protocols

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
