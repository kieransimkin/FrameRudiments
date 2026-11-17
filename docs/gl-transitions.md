# M9.2 — Pinned GL Transitions on the effect graph

**Status:** M9.4 restricted-shader source gate implemented; exact-head CI and cross-vendor acceptance pending. This is **not ISF execution**. `inspect-isf` remains metadata-only.

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

## M9.3 — Controlled multiple GL passes (implementation candidate)

A `gl_transition` may now add `shader_passes`, an ordered array of **zero to
three additional** `{ "shader_path": ..., "shader_sha256": ... }` records.
The main `shader_path` remains pass 1; all passes are individually pinned and
validated before any execution, including at exact progress endpoints. The
single outer `trusted_shader: true` opt-in covers every reviewed shader; an
additional pass cannot request its own arbitrary uniforms, input textures,
imports or time source. Individual shader files remain limited to 64 KiB.

```json
{
  "transition_style": "gl_transition",
  "shader_path": "first.glsl",
  "shader_sha256": "<sha256 of first.glsl>",
  "shader_passes": [
    {"shader_path": "second.glsl", "shader_sha256": "<sha256 of second.glsl>"}
  ],
  "trusted_shader": true,
  "progress": 0.5,
  "max_pass_bytes": 134217728
}
```

Each pass receives the **prior pass output** as `getFromColor(uv)` and the
**original incoming image B** as `getToColor(uv)`. The first pass uses original
A and B. Progress and explicit host time/coordinate uniforms are unchanged
between passes. Each intermediate is materialized as RGBA8 on the device; its
straight-alpha and display-referred semantics are unchanged. No history or
implicit feedback is created. Progress endpoints still copy A or B exactly.

**Resource contract:** 1–4 passes, 2 input texture uploads, 1 output readback,
`N` intermediate RGBA8 output textures, and at most `N + 2` simultaneously live
RGBA8 texture payloads. Before texture allocation, the bridge checks
`width * height * 4 * (N + 2)` against both its existing per-backend GPU budget
and `max_pass_bytes` (default 128 MiB, maximum 256 MiB). The graph backend
planner reports the same payload size, transfer count and draw-pass count.
Program objects from external shaders are retained in a **16-entry LRU** on
each GPU backend and released when evicted. The budget measures texture
payloads, not CPU images, driver shader compilation memory, VRAM fragmentation,
execution time or the safety of arbitrary GLSL. M9.4 now admits only a restricted, explicitly reviewed shader subset; do **not** run unreviewed shader sources.

Project-relative paths for every additional pass are resolved and registered
via `Project.ref` alongside the first shader for input provenance. Authored
JSON is unchanged. The transport sample records `external_shader_passes` and
`external_shader_peak_payload_bytes` after successful execution. Existing
single-pass projects without `shader_passes` retain the one-pass path.

Targeted CPU acceptance is in `tests/test_gl_multipass.py`; required real EGL
parity, transfer, budget and shader-program-cache tests are in
`tests/test_gl_multipass_egl.py`. Full exact-head CI and independent hardware
vendor performance measurements remain pending.

## M9.4 — Restricted GLSL source admission (candidate)

The shader now passes a deterministic, **fail-closed source lexer and restricted
statement validator** *after* its full-file SHA-256 pin and `trusted_shader:
true` opt-in are checked and *before* GLSL compilation or GPU allocation. Every
shader in a `shader_passes` chain must pass, including when progress is exactly
zero or one. The active policy is `framerudiments.gl-transition-restricted.v1`.
The original local file and its recorded digest are never rewritten. Comment
text is removed from the compiled fragment, and matching source redeclarations of host uniforms
are removed after checking their types (duplicates in the source are rejected).

Only this **GL Transitions subset** is admitted:

- A single `vec4 transition(vec2 uv)` body containing initialized scalar/vector
  locals, assignments to existing mutable locals and a final `return`. Constant
  global declarations and approved host-uniform redeclarations may precede it.
- Straight-line expressions and bounded ternary `?:` expressions using GLSL
  3.30 arithmetic/comparison operations, swizzles, approved pure built-ins and
  only `getFromColor(vec2)` / `getToColor(vec2)` for image access.
- At most 768 lexical tokens, 48 statements, 64 approved function calls and 12
  nested expression parentheses **per pass**. Source remains capped at 64 KiB;
  non-finite or greater-than-1e9 numeric literals are refused. The original
  M9.3 pass count and RGBA8 texture payload limits remain in force.

The validator refuses all `#` directives/includes, escaped line splicing,
strings, unknown identifiers, extra/user-defined functions, recursive calls,
loops, flow-control blocks, mutation of host values, extra textures/samplers,
array indexing, direct `texture()` / `texelFetch()`, image/buffer reads or
writes, atomics, barriers and unsupported language constructs. `gl_*` and
other host symbol access is also disallowed. Commented-out examples of such
code are inert. This is intentionally narrower than general GL Transitions;
previously reviewed third-party shaders with arbitrary helpers, branches or
texture fetches now require a restricted rewrite, not automatic acceptance.

**Important:** This does **not** turn GLSL into a safe sandbox. Static analysis
cannot prove maximum GPU driver/compiler time, that memory allocations outside
RGBA8 payloads stay bounded, or immunity to driver bugs. The explicit
`trusted_shader` flag is still compulsory; never submit untrusted source to a
GPU device shared with other workloads. Untrusted render services require a
separate, OS-isolated GPU execution boundary with hard timeouts and resource
controls. No automatic download, execution of arbitrary ISF, or shader-library
imports are enabled.

Regression coverage: `tests/test_gl_shader_guard.py` exercises both permitted
and refused language features, comment/directive handling, source immutability,
limits, secondary-pass admission and endpoint protection without opening GL.
`tests/test_gl_multipass_egl.py` runs a real, mandatory EGL compile/render
and verifies refused source never uploads or enters the program cache. Full
repository CI plus multi-vendor driver checks are separate acceptance gates.
M9.5 retains pinned reference images and independent provenance evidence.

## M9.5 — Golden image and provenance acceptance (candidate)

Five pinned host-reference PNGs, their exact shader/full-file hashes, a frozen
RGBA8 fixture-generation contract, mandatory EGL pixel parity and actual-GL
qualification receipts are specified in [M9.5 golden evidence](gl-goldens.md).
They are not GPU captures until executed against a real driver. Source signing,
physical-device inventory and perceptual approvals remain separately gated.
