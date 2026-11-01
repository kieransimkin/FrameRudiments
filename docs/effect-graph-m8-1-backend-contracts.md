# M8.1 — backend capabilities and surface/resource preflight

Implementation increment **v31**, based on `90df2b6b55d0551d0cc63742ddc8ede99e640abc`.
This is a development patch, not a package release. No tags, deployment settings,
shader algorithms, source clocks or package dependencies change.

## Deliverables

The module `effect_graph_backend_contract.py` provides a built-in backend registry,
immutable surface contracts, explicit per-sample resource plans and diagnostic
failures. `EffectGraph.backend_plan()` exposes it on the existing DAG. The renderer
and planner use the same extracted `_resolved_configs()` implementation for native
parameter overrides and beat-timed transitions. There is no alternate executor,
new animation oscillator or shadow copy of motion/DSP logic.

This increment implements M8.1 and the **descriptive foundation** of M8.2. It does
not implement GPU-resident graph scheduling, texture pooling, additional GPU
kernels, automatic device selection, shader import, HDR, benchmarks on new hardware,
or persistent image caches. Those remain separately gated milestones.

The existing Pillow/NumPy graph and native DanceRudiment adapter suffice. Saved
StemLab recordings can drive the same existing control path. Other DanceFlow
renderers supply ordinary isolated RGBA surfaces; they need no new transport or
renderer API. Model inference, PixelCue/PrismMoves analysis and external shader
hosts are unnecessary to this allocation-free planning operation.

## Discovery without side effects

```python
from framerudiments.effect_graph_backend_contract import backend_registry
catalogue = backend_registry()
```

The registry covers every built-in node kind. It reports package metadata and
module discoverability separately from executable GPU availability. It never
imports ModernGL, creates a context, compiles a shader or downloads a dependency.
An installed module is not proof that its native binary can import or that a
graphics context can be created.

Supply an existing `GLMatteBackend` to inspect its OpenGL version, renderer, vendor,
texture limit and configured texture-payload budget. The registry does not own,
close or mutate that backend. A closed backend is unavailable. An attached context
is explicitly **not** a shader compilation or image-parity qualification. Only the
known GL bridge is accepted; arbitrary plugin objects are not silently certified.
A missing reported texture limit is labelled as such; the bridge's existing 16384
fallback is retained rather than represented as a measured device limit.

`create_gpu_backend(mode, report=some_dict)` optionally retains initialization
state and the exception text/type when auto mode falls back. Existing callers
retain the original return value and behavior. The optional report is cleared on
each attempt, so old diagnostics cannot look like a current successful probe.
Only the explicit factory may open a device; `backend_registry` never calls it.

## Surface ABI

`SurfaceContract(width, height)` is an immutable, checked host RGBA8 descriptor:

| Property | Current contract |
| --- | --- |
| Format | Four unsigned normalized 8-bit channels |
| Alpha | Straight/unassociated RGBA at graph boundaries |
| Colour | Display-referred; no implicit scene-linear conversion |
| Rows | Logical top-first, tightly packed; upload/readback use alignment 1 |
| Residency | Host at every public graph image port |
| Dimensions | Positive integers, each at most 16384; at most 67,108,864 pixels |
| Alpha lineage | Separate single-channel R8 coverage/data descriptor |

The descriptor can represent device RGBA8 payloads for resource reporting, but
current graph inputs still reject device-resident surfaces. Unsupported float,
premultiplied, bottom-first or HDR declarations fail rather than implying a
conversion. The narrower 8K pixel ceiling of spatial/raster/procedural kernels is
checked separately. Unknown byte padding and driver metadata are not guessed.

All normal multi-input nodes require equal dimensions. The existing two-input
texture-pattern layer style can consume a differently sized pattern; its output
has the primary input's dimensions. Planning validates only reachable source
contracts. Unreachable nodes are reported, not allocated or rendered.

## Planning one sample

```python
from framerudiments.effect_graph_backend_contract import PlanLimits

ctx = compositor.context(frame_number)
values = compositor.effect_graph_controls.values(ctx, compositor.effect_graph)
sizes = {key: (project.width, project.height)
         for key in compositor.effect_graph.source_ids}
plan = compositor.effect_graph.backend_plan(
    sizes, mode="auto", gpu_backend=compositor.effect_graph_gpu,
    parameter_values=values, context=ctx,
    limits=PlanLimits(max_retained_host_bytes=512 * 1024**2),
    require=True,
)
```

Use the actual source dimensions for standalone graphs with different-sized
patterns. Native controls are evaluated by the original adapter, not the planner.
Explicit progress bindings still take priority over automatic transition timing.
The planner revalidates the resolved graph before computing resources. A later
change of controls, topology or dimensions requires a new plan; there is no plan
cache or historical state. This example evaluates one sample, not an entire video.

The report has schema `framerudiments.backend-plan.v1`. It includes deterministic
execution order, resolved node configurations, selected backend and reason,
surface descriptors, alpha-lineage ownership and resource estimates. It is a
detached JSON-safe object. Editing it cannot modify a graph or subsequent report.
`require=True` raises `BackendPlanError` on reported eligibility/budget errors;
the exception retains a detached `.report`. Invalid schema or dimensions raise
`ValueError` directly, before any resource allocation.

A plan is a structural/backend eligibility check, **not proof that a render will
succeed**. It does not inspect image content, compile shaders, decode media,
certify assets or account for every data-dependent temporary allocation.

## Backend decisions and compatibility

The current GPU kernels are `matte`, `chroma_key` and `spectral_displacement`.
Matte/key morphology is limited to 16 pixels and Gaussian sigma to 12 pixels.
Key edge decontamination has no GPU implementation. Unsupported configurations
fall back to the exact CPU reference in `auto`, or are rejected in `gpu` mode.
The CPU matte implementation uses Pillow; colour-key and most other effects use
NumPy. Some style identities may avoid NumPy internally, but the planner
conservatively requires the NumPy effect ABI for the style family.

**Existing `gpu` mode is not a promise of an all-GPU graph.** Host source copies,
opacity, compositing, legacy chromatic displacement, dissolve/wipe/radial/matte
transitions may still run on CPU under that historical policy. The planner
preserves this behavior and labels every such node as CPU. It rejects CPU-only
layer styles, spatial/raster/procedural effects and advanced transitions where
the existing strict executor also rejects them. No CPU operation is relabelled
as a successful GPU kernel. `gpu_resident_graph` is always false in v31.

Spectral `mix=0` is the existing exact host identity: no upload or GPU target is
needed, even in GPU mode. All nonidentity accelerated nodes still return a host
image before the following graph operation. Attaching a GPU does not override a
requested CPU plan; supplying both is rejected as an ambiguous request.

## Texture-accounting correction

The existing matte/key wrapper retains every target until the call's `finally`.
It does not free a morphology intermediate before allocating the blur pass.
The old fixed allowance undercounted one texture when both operations were active.
The shared `gpu_requirements()` now drives the actual wrapper budget checks and
the planner using:

```
passes = 2 + 2 * bool(morphology) + 2 * bool(blur)
uploads = input_count + int(original_alpha_is_present)
live_textures = uploads + passes
payload_bytes = width * height * 4 * live_textures
```

The two base passes are initial mask/key generation and final composition.
Original alpha is currently uploaded as RGBA8 even when the configuration selects
current alpha. Graph execution supplies that lineage, so it must be counted.
For one foreground, one matte and lineage, combined morphology+blur retains
**nine** textures. For one keyed source and lineage it retains **eight**.

Spectral processing has four textures regardless of colour-band count: uploaded
source, two ping-pong intermediates and final target. It performs one upload and
one final readback per node. A chain of GPU nodes still repeats those transfers;
shared graph inputs do not imply shared GPU uploads. New tests record actual
wrapper allocation/release calls with fake resources and check the peak count.
They test the Python wrapper, **not GPU pixels or actual VRAM consumption**.

GPU size/count validation now rejects nonpositive or noninteger values and closed
backends before allocation. Invalid constructor budgets are rejected before
context creation. A newly owned context with an insufficient OpenGL version is
released before the constructor raises; caller-owned contexts remain untouched.

## Resource lifetimes and bounded checks

Each RGBA result gets a birth step, consumer-edge list and last-consumer step.
Repeated edges count separately. Forks share their input and inherited alpha
metadata, so lineage is charged once per owner, not once per alias. Spatial and
raster transformations transport alpha into a distinct owned surface; procedural
fields, composites and transitions establish a new source silhouette.

The executor currently retains every result and lineage until return. The report
charges that current behavior, plus caller source roles and the final output copy.
It does **not** claim that last-use release has already been implemented. Multiple
source roles may physically alias one image; accounting charges the logical roles
separately. These values therefore are logical payload estimates, not an exact
physical-memory measurement.

`PlanLimits.max_retained_host_bytes` checks those retained image payloads.
`max_transfer_bytes` checks per-sample upload+readback payload. The actual GL
wrapper enforces its configured texture-payload limit with the same pass counts.
Planning budgets are opt-in checks; they do not impose a whole-process memory cap.

Explicitly excluded: CPU kernel scratch arrays, interpreter/Pillow/NumPy overhead,
allocator alignment, driver/program/framebuffer metadata, shader compilation and
compositor/decoder/encoder buffers. Data-dependent style halos and sweep work are
still enforced by their existing kernels, not fully predicted here.

## Inspection command

```powershell
python integration/report_effect_graph_backend.py `
  examples/effect-graph-backend-plan.json --mode auto --output backend-plan.json

# Explicitly allow a context probe; still no rendering or shader qualification.
python integration/report_effect_graph_backend.py `
  project.json --mode auto --probe-gpu --output backend-plan-probed.json
```

The command accepts a graph or a project containing `effect_graph`. A bare graph
needs `--size WIDTH HEIGHT`. It assumes one supplied canvas for all providers;
use the Python API for differently sized patterns. Native bindings are reported
as present but are **not evaluated** by this static inspection command. Media
assets are not validated. Output is JSON on stdout unless a file is explicitly
requested. File creation is exclusive; it never overwrites an existing report.
It returns exit status 2 for an ineligible plan. A probe failure remains in the
JSON; `--mode auto` may still return a valid CPU plan.

## Test gates and dependencies

No new runtime dependency: Python 3.11+, existing Pillow and optional NumPy/ModernGL
extras. CPU discovery does not require ModernGL or a graphics context. Native
motion remains the released DanceRudiments API, never a replacement stub.

Implemented coverage includes immutable/invalid surfaces, exact byte accounting,
all built-in node registrations, dependency discovery, no image allocation during
planning, no implicit GL import/probe, closed/unsupported devices, strict/auto
policies, matte/key boundaries, combined-pass budget regression, fork/lineage
ownership, duplicate edges, mixed texture sizes, detached reports, native-resolved
overrides, beat timing, source immutability and exclusive report output.

Seven native Project/Compositor cases exercise real named and recording motion,
frame seeking and the temporal virtual graph. They require the full repository,
released native dependencies and an installed font. No skipped native test counts
as acceptance. Full CPU/font/FFmpeg, Ruff and the existing EGL parity CI remain
required before milestone acceptance. The changed shader algorithms count is zero.

## Next milestones

M8.2 extends explicit surface formats/association and transport APIs with parity
fixtures before adding new storage types. M8.3 adds GPU kernels. M8.4 implements
actual last-use release, pooling and shared GPU fork surfaces. M8.5 measures
performance and cross-device parity. None is marked complete by this planner.

## Primary references

The implementation was checked against the current bridge source and its exact
allocation flow, rather than treating installed libraries as runtime evidence.
ModernGL documents context-bound object creation, texture formats and explicit
readback to system memory:

- https://moderngl.readthedocs.io/en/stable/reference/context.html
- https://moderngl.readthedocs.io/en/stable/reference/texture.html
- https://moderngl.readthedocs.io/en/stable/topics/texture_formats.html

These explain the API boundary; they do not qualify this project's GPU kernels.
