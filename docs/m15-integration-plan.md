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

## M15.2 — GStreamer offline ingest and optional live media graph

- Real decoded-presentation-PTS ingestion through a bounded, explicit video
  source contract, compatible with M11.4 VFR rules and SHA-pinned originals.
- Explicitly provided local device permissions / network endpoints. No default
  device enumeration, broadcast, server bind, mDNS, or stream forwarding.
- Audio/video clock reconciliation at an explicit sample/rational-frame boundary.
- Start/stop/reconnect, timeout, backpressure, active process ownership and
  partial-frame cleanup tests. GStreamer owns its decode contexts.
- CI publishes timed comparison media and a displayable Gallery sample, and
  tests fail if native runtime availability is required but absent.

## M15.3 — OpenFX plugin-host boundary

- Versioned descriptor scan **without loading** untrusted binary plugins in the
  renderer. Curated, licensed bundles only; check plugin architecture, ABI and
  declared image format before activation.
- Native OpenFX host runs as a separate owned worker, with child-process scope,
  bounded frame memory, timeout and explicit host-suite capabilities.
- Frame-addressed RGB/RGBA CPU transfers with known colour and alpha space; GPU
  texture sharing is separately qualified.
- Crash/timeout/resource regression evidence and independent CPU golden frames
  for at least one explicitly licensed test plugin.

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
