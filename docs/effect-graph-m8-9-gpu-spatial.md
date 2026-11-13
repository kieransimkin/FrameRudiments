# M8.9 GPU spatial warps

Set `effect_graph_backend` to `"auto"` or `"gpu"` on a project using an
existing `spatial_warp` node. Install the `gpu` extra and use an OpenGL 3.3
context. The default project backend is still CPU. Warp coordinates, the
native DanceRudiment control paths and the project format are shared across
backends.

![A test grid and yellow ring before and after the same wave warp on CPU and GPU](media/effect-graph-m8-9-spatial.png)

[Play the two-second, silent 60 fps comparison](media/effect-graph-m8-9-spatial.mp4).
The left panel holds the original blue grid and yellow ring. The middle and
right panels show the same moving deformation as wave phase makes one turn,
with both panels composited onto the same dark background.

## Supported configurations

| GPU warp | Controls and limits |
| --- | --- |
| Wave and turbulence | Up to 128 pixels of amplitude per axis and wavelengths of at least 16 pixels. Turbulence supports the authored seed and up to six octaves. |
| Radial | Nonnegative `warp_amount`. Negative amounts retain the CPU inverse mapping. |
| Swirl, mirror, kaleidoscope and polar unwrap | Existing controls and canvas limits. |

All seven shaders support transparent, clamp, repeat and mirror borders. They
sample at pixel centres, interpolate premultiplied colour, and apply warp mix
before quantizing alpha to RGBA8. Zero mix and neutral transforms preserve
the original pixels exactly. Wave and turbulence settings beyond the GPU
precision limits retain their full CPU ranges. The CPU also handles polar
wrap, two-input map displacement and raindrop ripples. Strict GPU mode reports
the reason for rejecting these configurations. `spatial_manifest()` reports
support and the reason for the configured values. Backend preflight resolves
per-frame control overrides before making the same decision.

## Original alpha and transfers

An original-alpha matte follows the warped geometry even when an earlier node
changed the layer's opacity. The resident executor reuses the warped colour
texture for lineage when both input handles share alpha. When they differ,
it warps the original-alpha texture with the same coordinate map. A closed
resident graph still reads back its final image once. If a mixed graph needs
the warped alpha on the host, the per-node bridge returns that alpha separately
and reports both transfers.

The 1920×1080 wave fixture on an NVIDIA RTX 4060 Ti made one upload and one
readback of 8,294,400 bytes each. Across three samples, the median was 1.349 s on
CPU and 0.057 s on GPU, including transfers. The largest alpha and visible
straight-RGB differences were one 8-bit level each. These timings measure one
effect graph on that driver. They exclude decoding, the rest of the compositor
and encoding. Physical VRAM measurement and persistent cross-frame pooling
remain separate roadmap items.

Reproduce the benchmark and optional comparison media from the source checkout.

```sh
python integration/benchmark_effect_graph_m8_9_gpu.py --size hd --backend auto --output m89.json
python integration/benchmark_effect_graph_m8_9_gpu.py --backend auto --output m89-demo.json --media docs/media
```

## Potential problems

- **A short wavelength combined with a large amplitude changes edge pixels
  on GPU.** At 1080p, a deliberately extreme wave and turbulence fixture exposed
  large differences between the GLSL and CPU sampling coordinates. Keep the CPU
  range intact and use the shared GPU precision check before upload. The HD
  benchmark tests the admitted boundary and compares alpha plus the result
  composited over both black and white.
- **A radial warp with a negative amount and an almost pixel-centred origin
  can shift the centre pixel.** The CPU and GLSL may round the origin
  differently. The same precision check routes negative radial amounts to
  the CPU, including values reached by a live control.
- **The Windows glcontext wheel cannot import `egl`.** Run the spatial suite
  through `scripts/Invoke-Tests.ps1`; the fixture selects Windows' native
  standalone OpenGL context. The required Linux GPU job separately exercises
  Mesa/EGL and fails if its parity tests skip.
