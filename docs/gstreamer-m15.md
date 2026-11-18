# M15.1 — opt-in, real GStreamer H.264/MP4 export

**Status:** integration candidate. Native GStreamer CI must pass on the exact merged
commit before declaring the external encoder qualified. This is a **working,
closed-pipeline export adapter**, not an OpenFX host, NDI publisher, WebGPU
renderer or GStreamer live-streaming server.

## Contract

`framerudiments-gstreamer encode-video` consumes the exact versioned
`framerudiments.video-frames.v1` manifest already supported by M11.4. Each
frame is an RGB8 PNG with a SHA-256 pin; width/height are even; FPS is an
**exact rational** such as `24000/1001`, `30000/1001` or `60`.

1. Reject absolute paths, path traversal, external symlink targets, changed
   pins, non-RGB PNGs, invalid dimensions and out-of-budget media **before**
   launching the encoder. Limits: **8192 frames**, **512 MiB** raw input and
   **1 GiB** output. Use a new MP4 filename; overwriting is prohibited.
2. Stage tightly pinned PNGs into RGB8 rawvideo. GStreamer's native RGB row
   stride is **four-byte aligned** (`RU4(width*3)`); zero padding is explicitly
   inserted on each row. For example, a 130-pixel row is 392 bytes, not 390.
3. Discover trusted installed `gst-launch-1.0` and `gst-inspect-1.0`. Require
   `filesrc`, `rawvideoparse`, `videoconvert`, `x264enc`, `h264parse`, `mp4mux`,
   and `filesink`. A plugin *probe* does not qualify timing or visual quality.
4. Launch a **fixed allowlisted pipeline**, with no external URLs, plugin name
   injection, user pipeline strings, model downloads, audio stream or fallback
   to FFmpeg encoding. The muxer track timescale is the frame-rate numerator.
5. Independently inspect **every decoded presentation timestamp** and completely
   decode the output through FFprobe/FFmpeg. The exact required timestamp is
   `n * fps.denominator / fps.numerator`; any drift, missing frame, failed
   decode, mutated source or failed encoder rejects the result.
6. Publish MP4 plus `.mp4.receipt.json` via no-overwrite hard links only *after*
   successful verification. Failure or timeout removes staging and rolls back
   any partial publication. Receipts pin input frames, raw bytes, output and
   GStreamer element versions, and explicitly state that this is video-only.

GStreamer native plugins are executable code, not isolated scripts. **Only use
trusted local installations.** `gst-inspect` and `gst-launch` do not provide a
security boundary around malicious plugins. Check licence and redistributability
of your platform's chosen GStreamer/x264 packages separately; this adapter does
not bundle or download those components.

## Usage

```shell
python -m pip install -e '.[dev]'
framerudiments-gstreamer inspect
framerudiments-gstreamer encode-video ./frames.json --output ./gstreamer.mp4 \
  --bitrate-kbps 4000 --preset fast
```

Optional explicit trusted binary paths: `--gst-launch`, `--gst-inspect`,
`--ffmpeg`, `--ffprobe`. FFmpeg is **only** the independent decoder/PTS
checker; its M11.4 producer remains the legacy application encoder.

The output receipt exposes `source_manifest_sha256`, every frame pin,
`raw_sha256`, GStreamer/plugin version IDs, `video_sha256`, the independently
verified rational timestamps and a complete decoded-frame proof.

## Acceptance and follow-ons

`tests/test_gstreamer_m15.py` is dependency-light and runs on regular Python
without GStreamer. It covers malformed input, raw stride/byte identity, plugin
admission, rational FPS, no-overwrite and rollback on corrupt output.

`.github/workflows/gstreamer-m15.yml` installs the native GStreamer plugins and
FFmpeg on Ubuntu/Python 3.11 and 3.13, then runs the actual
`integration/qualify_gstreamer_m15.py` fixture. It exports 24000/1001,
30000/1001 and 60 FPS MP4 samples plus poster frames and a machine-readable
receipt to GitHub Actions artifacts. **A skipped native encode fails CI.**

Not supported yet: ingestion through GStreamer, live source/sink graphs,
network transports, audio muxing, physical video devices, GPU/hardware encode,
zero-copy interop, OpenFX binary plugins, OpenUSD/Hydra, WebGPU/Vulkan, or
OSC/MIDI. Those require separate capability and integration/CI gates under M15.
The main FrameRudiments compositor, source clock, legacy encoder and Pages
timeline remain untouched.

See official GStreamer element references: [rawvideoparse](https://gstreamer.freedesktop.org/documentation/rawparse/rawvideoparse.html),
[x264enc](https://gstreamer.freedesktop.org/documentation/x264/index.html), and
[mp4mux](https://gstreamer.freedesktop.org/documentation/isomp4/mp4mux.html).
