# M15.2 — GStreamer video ingest, rational source clocks and bounded live capture

**Status:** opt-in implementation candidate. The exact-head GStreamer/FFmpeg CI and
live device/RTSP acceptance remain separate gates. M15.1 export and all native
FrameRudiments rendering/clock paths are unchanged.

## Offline, pinned VFR ingest

`framerudiments-gstreamer inspect-ingest` reports required plugin availability
without opening any source. `framerudiments-gstreamer ingest-video` reads a **SHA-256-pinned local video**
through GStreamer's native `filesrc → decodebin → videoconvert → pngenc →
multifilesink` pipeline. GStreamer's real per-buffer `GstMultiFileSink` messages
supply the **nanosecond PTS** for every emitted PNG. FFprobe independently
inspects the original stream's *decoded presentation order* and **exact rational**
`best_effort_timestamp × time_base` (M11.4). We compare every inter-frame
presentation interval with at most one nanosecond of quantisation tolerance.
Frame count/order and dimensions must agree; unexpected files or missing timestamps
fail closed. **No nominal-FPS resampling** occurs, including for VFR input.

```powershell
framerudiments-gstreamer ingest-video ./clip.mp4 \
  --sha256 <actual-clip-sha256> --output ./decoded-vfr

# With independently pinned audio, optionally report sample alignment:
framerudiments-gstreamer ingest-video ./clip.mp4 \
  --sha256 <actual-clip-sha256> --output ./decoded-with-clock \
  --audio-source ./audio.wav --audio-sha256 <actual-audio-sha256>
```

Output is a new directory of numbered RGB8 PNGs and `index.json` with original
PTS/timebase, source hash, PNG hashes and GStreamer-buffer nanosecond PTS.
This is a **VFR ingest ledger**, not an M11.4 CFR video export manifest.
Audio is **not decoded, muxed or resampled**. When supplied, a separate pinned
audio file's sample rate/start PTS defines an audio-sample clock. Missing
container start PTS (notably WAV) is **explicitly** labelled `no_start_pts_assumed_zero`;
a caller can alternatively supply both `--audio-rate` and exact rational
`--audio-origin`. The report gives nearest sample indices and quantisation
errors without claiming automatic lip-sync or hardware clock locking.

Bounds: at most **256 video frames**, 512 MiB of decoded RGB budget, 1 GiB of
pinned source media, a 120-second subprocess deadline, fixed local output path,
no silent overwrites and complete staging rollback. No arbitrary GStreamer
pipeline string, URL or device is accepted by offline ingest.

## Live capture is always explicitly initiated

`capture-live` is a finite command, **not a service**. It supports three source
kinds: `test` (GStreamer's own test source), `v4l2` (caller-selected,
OS-permitted `/dev/videoN` on Linux), and `rtsp` (explicit unauthenticated
**loopback** TCP H.264 URL with `--allow-network`; no remote servers by default).
The RTSP and physical-device paths are feature candidates requiring their own
real source acceptance. The automated CI only certifies `videotestsrc`.

```bash
framerudiments-gstreamer capture-live --source test \
  --output ./live-test --frames 24 --width 160 --height 96 --fps 30

# Explicit camera permission and device path are required:
framerudiments-gstreamer capture-live --source v4l2 \
  --device /dev/video0 --output ./camera-capture --frames 60

# Intentionally loopback-only RTSP, no credential-bearing URLs:
framerudiments-gstreamer capture-live --source rtsp \
  --rtsp-url rtsp://127.0.0.1:8554/live --allow-network \
  --output ./rtsp-capture --frames 60 --retries 2
```

A **four-buffer, leaky-downstream** queue applies explicit backpressure; dropped
frames are possible and **never filled with invented optical flow or synthetic
presentation timestamps**. The native `identity` terminates the stream after
an exact output-buffer count, with an outer timeout. Reconnect attempts are
limited to **0–2**, replace the *entire* partially captured attempt, and
publish only a complete final result. The `gst-launch-1.0` process is supervised
and reaped; on POSIX a timed-out owned process group is killed, and on Windows
the child is explicitly terminated. There is no discovery, multicast, network
listener, background daemon or implicit forwarding.

## CI-generated live demo and verification

[FrameRudiments M15.2 gallery](https://kieransimkin.github.io/FrameRudiments/m15-2/)
contains an actual VFR-generated MP4, its decoded-frame contact sheet, a
scrubbable eight-frame GStreamer live synthetic capture, independent PTS and
SHA-256 ledgers, a pinned generated 48 kHz WAV clock and the CI qualification
receipt. It is **real GStreamer output**, with synthetic test media, not a
network/device demo and not perceptual approval.

The existing [M15.1 GStreamer workflow](../.github/workflows/gstreamer-m15.yml)
now runs native M15.2 unit and fixture tests on Linux/Python 3.11 and 3.13. The
[visualisation Pages workflow](../.github/workflows/visualisation-gallery.yml)
builds and publishes the same verified sample under `/m15-2/`.

## Explicit limitations and next phase

- Source video with separate audio pads in the same container may fail the
  video-only `decodebin` link; the **independently pinned audio-source** option
  is the qualified clock boundary. Multi-stream demux/audio mixing is future work.
- No device, RTSP, physical GPU, continuous NDI, NDI|HX, Spout, Syphon, WebRTC,
  multicast or network stream has passed this synthetic-fixture acceptance.
- Each capture is finite; continuous recording, session restart state and
  persistent stream clocks need a dedicated transport supervisor in M15.5.
- Nominal/source PTS, encoder delay, measured sync and source-audio offset are
  different concepts. The adapter never silently normalises one into another.

Next: [M15.3 OpenFX plugin-host boundary](m15-integration-plan.md#m153--openfx-plugin-host-boundary).
