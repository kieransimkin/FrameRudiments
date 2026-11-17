# M9.2 — Pinned GL Transitions on the effect graph

**Status:** implementation candidate; exact-head CI and real-device acceptance pending. This is **not ISF execution**. `inspect-isf` remains metadata-only.

A two-image, one-pass [GL Transitions](https://gl-transitions.com/) fragment exposes `vec4 transition(vec2 uv)` and samples `getFromColor(uv)` / `getToColor(uv)`. FrameRudiments composes it using its existing ModernGL 3.3 `GLMatteBackend`, RGBA8 transport, explicit GPU resource budget and cleanup. No extra base-install dependency is added; install `framerudiments[gpu]` to render intermediate frames. With `progress = 0` or `1`, the exact respective input is copied without a GPU upload.

An example graph fragment (replace the SHA with the exact digest of your local fixture):

```json
{
  "nodes": [
    {"id": "a", "type": "source"},
    {"id": "b", "type": "source"},
    {"id": "cross", "type": "transition", "inputs": ["a", "b"],
     "transition_style": "gl_transition", "progress": 0.5,
     "shader_path": "examples/gl-transition-crossfade.glsl",
     "shader_sha256": "b42ec29cc3c9b016bb9b1132d0222d9450b0a909d212eb4c4f9a9768b6688ac3",
     "trusted_shader": true,
     "time_seconds": 1.25, "time_delta_seconds": 0.041666666666666664,
     "frame_index": 30}
  ],
  "output": "cross"
}
```

Render with `EffectGraph(spec).render(sources, gpu_backend=backend)`. The existing graph's native `effect_graph_controls` `progress` target works unchanged. `time_seconds` and `time_delta_seconds` are also finite numeric targets, so the host can pass resolved native values through `parameter_values={("cross", "time_seconds"): 2.0}`. An optional transition `timing` envelope can derive `progress` from the established beat clock. Time values are *explicit*: they are never read from the wall clock and are not automatically inferred from frame order.

## Host ABI

| Name | Type | Definition |
| --- | --- | --- |
| `progress` | float | Native-resolved transition progress in [0, 1] |
| `resolution`, `RENDERSIZE` | vec2 | Canvas width and height in pixels |
| `ratio` | float | Width divided by height |
| `TIME` | float | Explicit nonnegative seconds from `time_seconds` |
| `TIMEDELTA` | float | Explicit nonnegative seconds from `time_delta_seconds` |
| `FRAMEINDEX` | int | Explicit nonnegative `frame_index` |
| `getFromColor(vec2)`, `getToColor(vec2)` | vec4 | Clamped, unfiltered RGBA sampling of source A and B |

`uv` is a normalized, top-first host image coordinate at each pixel centre: `(x + 0.5)/width, (y + 0.5)/height`. Source and result are straight-alpha display-referred RGBA8; the shader defines its own colour/alpha interpolation. Source images are unchanged. There is one upload per source, a single GPU draw, then one readback. The ordinary resident-island optimiser deliberately does **not** treat arbitrary external GLSL as a supported resident kernel.

## Trust and bounds

- `trusted_shader: true` is an explicit **reviewed local code** opt-in. Require a full-file SHA-256 pin; verify it every evaluation, including exact endpoints. Project-relative shader paths are resolved against the project directory and registered through `Project.ref`, so `verify_inputs` and the existing source-provenance ledger verify the pinned shader alongside other inputs. The 64 KiB maximum is a source-size bound, **not** a GPU execution-time or instruction bound.
- Only a fragment exposing `vec4 transition(vec2 uv)` is currently accepted. The wrapper declares host uniforms and samplers. Matching declarations of host uniforms in the source are consumed; unsupported third-party uniforms, `main()`, preprocessor directives and multiple passes are rejected. Local fragments cannot import images or other resources through this ABI.
- **Do not run untrusted shaders.** Arbitrary GLSL can hang a GPU even when file size and texture allocation are limited. This feature does not claim a security sandbox. M9.4 remains the separate hardening gate, and arbitrary third-party shader intake must remain disabled in untrusted workflows.
- There is **no CPU substitute** for intermediate pixels. Missing GPU, failed compilation, expired pin and unsupported uniforms fail explicitly. Source-specific licensing and attribution remain the caller's responsibility. The included example is original and covered by the repository licence.
- Per-node graph GPU planning accounts for three RGBA8 texture payloads: two uploads and one output. Driver-internal shader/pipeline allocations are outside that budget. No automatic caching of *source* bytes is performed; compiled programs are keyed by the verified digest in the existing backend cache.

## Verification

```sh
# Run this repository's targeted tests through its standard managed entry point.
pwsh -File scripts/Invoke-Tests.ps1 tests/test_gl_transitions.py -q
```

The CPU test module covers source and pin validation, exact endpoints, native override forwarding and host uniform packets. The separate `tests/test_gl_transitions_egl.py` is exercised in the required EGL GPU workflow, with no skip-only pass; no CPU fallback masquerades as shader execution. Required exact-head CI must confirm a real EGL context and existing project regressions before calling M9.2 shipped. The final M9 family acceptance requires cross-vendor checks, pinned goldens, provenance reports and M9.3/M9.4 follow-ups.

Project usage: set `effect_graph_backend` to `"gpu"` (strict) and refer to the shader file by a path relative to the project JSON. `Project` records the full-file pin, and the compositor passes the resolved path to `EffectGraph` without modifying the original project JSON. A two-input GL stage in `transition_chain` is supported using the same pinning rule.
