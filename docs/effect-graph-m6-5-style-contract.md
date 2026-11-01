# M6.5 — Layer-style control and capability contract

**Increment:** v27, 1 November 2026. **Baseline:** `b7568edeab7420f71041963214204f2424c76b1b` (v26).
**Status:** CPU implementation and focused regression evidence prepared. Native
Project/Compositor acceptance and complete repository CI remain required gates.

This milestone consolidates the M6.1–M6.4 layer styles into a discoverable contract,
connects their conservative bounds to graph execution, and exposes what actually
ran. It does not add another image-processing engine, move effects to the GPU,
change native movement definitions, analyse audio, resize canvases, or change the
existing raster algorithms. Descriptor/report generation never feeds image or
motion evaluation.

## Deliverables and ownership

| Deliverable | Implementation | Gate |
| --- | --- | --- |
| All fourteen style families and texture variant | `effect_graph_style_contract.style_catalogue()` | Complete inventory; explicit CPU-only execution |
| Topology-safe descriptors | `EffectGraph.control_descriptors(node_id)` | Every address accepted by real graph validation |
| Whole-graph style discovery | `EffectGraph.style_manifest()` | Stable IDs, chain preprocessing, reachable/dead nodes |
| Conservative and actual bounds | `layer_style_bounds()` and per-node render reports | Actual alpha contained by estimate; no pixel changes |
| Sample-context integration | `evaluate_effect_graph()` / `compositor_style_report()` | No stale successful graph report after adapter failure |
| Full registered style numeric ranges | Existing native driver plus extended-range mapping | Real-driver tests, no replacement oscillator or motion |
| Cross-provider acceptance | Native Project/Compositor test module | Sprite, recording bars, text, scene hook, composite group, chain |
| Reproducible diagnostics | Visual generator and real-project export script | Fresh output, deterministic pixels and JSON |

FrameRudiments remains the presentation/compositing owner. DanceRudiments remains
the native motion authority; StemLab owns saved analysis and beat provenance.
There are no new runtime dependencies. The existing `effect_graph` extra supplies
NumPy; Pillow and the native motion dependency remain as defined by the project.
No model, private song, font binary, renderer plugin or GPU device is required for
static discovery. No release/version/tag/publication action is included.

## 1. Static catalogue and graph discovery

```python
import json
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_style_contract import style_catalogue

catalogue = style_catalogue()
graph = EffectGraph({
    "nodes": [
        {"id": "source", "type": "source"},
        {"id": "edge", "type": "layer_style", "inputs": ["source"],
         "style_type": "outline", "style_width": 4, "style_color": "#20ddff"}
    ],
    "output": "edge"
})
manifest = graph.style_manifest(active_only=True)
print(json.dumps(manifest, indent=2, allow_nan=False))
```

The catalogue has schema `framerudiments.layer-styles.v1`. It lists drop/inner
shadows, outer/inner glows, outline, bevel, emboss, edge light, satin, extrusion,
long shadow and colour/gradient/pattern overlays. The pattern texture variant
explicitly requires two graph inputs. Other style variants are unary.

The manifest has schema `framerudiments.layer-style-controls.v1`. Each style entry
contains input IDs, capabilities, reachable state, optional chain origin, and
control descriptors. Control addresses are `[node_id, property_path]` arrays;
node names are never split at punctuation. For example:

```text
["sequence::input::0::edge", "style_width"]
["paint", "gradient_stops.hot.color.g"]
```

`include_unreachable=False` is the default: dead style nodes are not offered as
executing effects. Set it to true to inspect authored but unreachable nodes; they
are labelled `reachable: false`. This does not render or require their sources.
Chain preprocessing keeps its already-established stable generated IDs. This API
currently describes **layer styles**, not every unrelated renderer or graph node.

### Descriptor semantics

Each descriptor keeps `default` compatible with v26: it is the configured base
value, not necessarily a factory default. New fields make the distinction explicit:

| Field | Meaning |
| --- | --- |
| `value` | Configured base; alias of historical `default` |
| `default_semantics` | `configured_base` |
| `factory_default` | Registry default, or null for a custom gradient stop |
| `active` | The chosen style/output/pattern actually uses the parameter |
| `choices`, `can_vary` | Topology-compatible alternatives; possibly one choice |
| `range`, `units`, `interpolation` | Existing numeric/enum contract |
| `constraints` | Joint validation rules, such as ordered gradient stops |
| `address` | Added by graph manifest: stable node/property tuple as JSON array |

All returned dictionaries/lists are detached snapshots. Editing a manifest cannot
change the graph, module defaults, colours, offsets or authored gradient stops.
Inactive parameters remain accepted for compatibility and future categorical
switching; `active_only=True` filters them from a current-style editor. "Active"
is structural applicability, not a promise that a parameter changes every input
image (for example a fully transparent image or a zero-strength effect).

Choices cannot silently change input arity. A texture-pattern node offers only
`pattern_kind=texture` and `style_type=pattern_overlay`. A unary node excludes the
texture choice. A one-choice enum is discoverable but has `can_vary=false`; do not
create a native categorical binding with fewer than two distinct choices.
Gradient-stop positions retain independent `[0,1]` bounds, but must remain strictly
increasing together after all controls resolve. The descriptor states this joint
constraint; it does not disguise dependent bounds as independent sliders.

## 2. Capability metadata is not a hardware probe

Metadata reports the actual implementation contract: RGBA8, straight alpha,
display-referred sRGB working values, unchanged canvas size, and deterministic
random access without image history. A gradient's interpolation space is reported
separately; choosing OKLab or linear-sRGB does not make the whole graph HDR or
scene-linear.

All layer styles remain CPU-only. Strict `gpu` rejects them, including no-op
configurations. `auto` may dispatch other nodes to a GPU, but style nodes run the
CPU reference. No GL context is created by discovery. Reports say `execution=cpu`
and distinguish a present GPU backend/CPU fallback from ordinary CPU execution.
They do not invent a requested backend when the adapter only knows the actual
backend object available at execution.

Declared limits are taken from existing constants, not performance estimates:
67,108,864 canvas pixels; 1,048,576 distance-field workspace pixels; 4,194,304 sweep
workspace pixels, 257 samples and 134,217,728 sweep pixel visits; 16,777,216 other
alpha-workspace/texture pixels where applicable; 16 gradient stops. These are
implementation limits, not guaranteed available RAM or throughput. No HD/4K/8K
performance improvement or new GPU parity is asserted by this milestone.

## 3. Conservative bounds and actual rendered bounds

`layer_style_bounds(image, config, original_alpha=...)` returns JSON-compatible
canvas-local bounds. Coordinates use left/top inclusive and right/bottom exclusive.

* `input_alpha` is the current input's nonzero-alpha rectangle.
* `selected_alpha` is the selected current or inherited original-alpha rectangle.
* `conservative_unclipped` estimates possible output support before canvas clipping.
* `conservative_canvas` intersects that estimate with the fixed output canvas.
* `padding_ltrb` gives conservative required margin beyond each canvas edge.
* `may_clip` flags a possible clipping candidate, **not proven lost pixels**.

The estimate includes retained source fill, handles zero effect/output opacity,
empty inputs, zero-width contours and zero-length extrusions, and preserves the
current-versus-original coverage distinction. It may be deliberately loose.
Original alpha can support effect-only output even if the current branch was
keyed/dimmed away. Interior combined effects do not add coverage outside current
ink. Unknown or incompatible alpha images are rejected, not guessed.

A completed style report adds `actual_output_alpha`, measured after that node's
universal output opacity, plus `output_touches_canvas`. Neither touching a border
nor an estimated overshoot proves pixels were clipped. These are local-node
bounds: later masks, transforms, transitions and composites may change support.
The graph report separately records `final_graph_alpha` for its returned RGBA.
No original content already cropped by an upstream source is recovered. The
canvas is not resized and legacy sprite-bounds validation is not relaxed.

## 4. Graph and compositor reporting

Direct graph callers own their report dictionary:

```python
report = {}
rgba = graph.render({"source": source_rgba}, style_report=report)
# rgba pixels match render() without style_report.
# report['nodes']['edge']['bounds']['actual_output_alpha'] is measured.
```

The report schema is `framerudiments.layer-style-report.v1`. The caller dictionary
is cleared before validation and populated **only after the whole graph succeeds**.
A late downstream error leaves no partial success report. Direct `render()` does
not overwrite the separate compositor-adapter snapshot. Without `style_report`,
direct rendering does not calculate the new diagnostics.

The compositor's existing `evaluate_effect_graph()` handoff captures reports when
reachable styles exist. `graph.style_render_report()` returns a detached snapshot.
The adapter clears its previous snapshot before attempting its next evaluation,
including control-resolution failures. Errors elsewhere in a frame, before the
adapter is called, may leave the previous graph snapshot: always inspect its
`sample_context`. `state=complete` means this graph evaluation completed, not that
an encoded frame, whole render, export or publication succeeded.

The clock context includes only finite numeric frame/time/beat fields. No image
buffers, lyrics, audio, font/asset paths or recording samples are retained in the
report. Valid authored graph IDs and style parameters remain visible. One report
per graph instance is retained, not an accumulating per-frame history. Concurrent
workers must use their own graph/compositor instances or caller-owned reports.

### Temporal supersampling

```python
from framerudiments.effect_graph_integration import compositor_style_report
image, context = compositor.frame(frame_number)
report = compositor_style_report(compositor)
```

This accessor follows the existing high-fps `_temporal_virtual` compositor when
appropriate. It labels the sample owner, sample fps and encoded fps; the original
subframe index and clock values are not rewritten. It is the **last completed
subframe**, not a union of all shutter samples or a measurement of final
post-antialias/encoded output. `complete_shutter_bounds` is explicitly false.
It never triggers rendering. Aggregated shutter bounds are future work.

The existing `Compositor.evidence()` output is not expanded with large manifests
in this increment. Use the accessor or explicit export below. This avoids
silently enlarging every existing render manifest.

## 5. Extended numeric style ranges use the real native driver

The native `_Modulator` accepts authored endpoints only within `[-10000,10000]`.
Some existing style parameters allow wider values, notably gradient/pattern angles
and pattern offsets. M6.5 maps **only** those extended style ranges onto a native
`[0,1]` transfer and maps the result back before the existing set/add/multiply/
min/max combination. All ordinary intervals keep their previous path.

Source sampling, beat/variable-BPM authority, curves, deadzone, power, inversion,
lag and past-frame FIR smoothing remain entirely native. No additional oscillator,
random walk, future sampling or mutable smoothing state is introduced. The
extended interval is validated before motion loading and recorded in
`EffectGraphControls.range_evidence`. Its absolute limit is 65,536, covering the
current style registry; graph validation still rejects values invalid for the
actual target after combination. No clamping is introduced.

## 6. Inherited v26 native test correction

Upstream CI at `b7568ede` passed 2,029 tests and failed five overlay native-motion
assertions. Their frames were multiples of 15 at 60 fps / 120 BPM; the resolved
native circle X samples in the log were identical (including 127.5 on `[0,255]`).
The tests therefore sampled the same zero-crossing phase repeatedly.

The patch uses sub-beat sample frames `(0,1,4,9,13,22,37,53)` and checks variation
of the actual resolved control values before asserting image variation. It keeps
the same real circle driver, X axis and ranges, does not weaken pixel/immutability
assertions, and does not modify native movement. This correction still requires
execution with the real runtime on the target checkout/CI.

## 7. Acceptance commands and evidence

Windows acceptance uses the project's existing managed runner:

```powershell
python -m pip install -e ".[dev,effect_graph]"
powershell -ExecutionPolicy Bypass -File .\scripts\Invoke-Tests.ps1 `
  -q tests/test_effect_graph_style_contract.py `
  tests/test_effect_graph_style_reports.py `
  tests/test_effect_graph_style_contract_visuals.py `
  tests/test_effect_graph_style_contract_project.py `
  tests/test_effect_graph_overlay_project.py
```

The supplied installer checks exact baseline blobs and patch checksum, runs Ruff,
requires real native acceptance with no skipped cases, and then runs the same
full CPU/font/FFmpeg selection as CI. Actual GL-only parity modules remain in the
separate qualified EGL CI workflow, not silently accepted as skipped CPU tests.

New focused tests cover every style, descriptor-to-validator agreement,
configuration/topology filtering, custom stops, detached metadata, reachability,
chain IDs, translucent coverage, original alpha, bounds containment, no-op cases,
report transactionality, contexts, fallback reporting, out-of-order rendering and
supersample ownership. Real-package tests cover sprite/recording-bars/text/scene
providers, composited groups, native controls, extended ranges, chain preprocessing
and actual temporal supersampling. The local focused snapshot cannot run these
real-package tests without the full repository and native dependency.

Generate reproducible synthetic evidence:

```sh
python integration/build_effect_graph_m6_5_visuals.py --output qa/m6-5-new
```

This emits twelve actual graph renders, bounds annotations, per-case control and
report JSON, a catalogue and pixel hashes. The fixtures are synthetic Pillow
RGBA/text, **not** a rigged mesh or GPU-rendered scene.

Export a real project frame and diagnostics without modifying the project:

```sh
python integration/report_effect_graph_styles.py project.json --frame 45 --output qa/project-styles-new
```

The output directory must be fresh. Temporary-directory create/read/rename/delete
preflight precedes rendering. Project inputs remain subject to the existing hash
checks. Local frame images can contain the user's artwork and should not be
published automatically. The export helper is included in real-package acceptance.

## 8. Remaining gates and next work

M6.5 implementation does not by itself close the complete M6 release gate. Require
Ruff, full real Project/Compositor/DanceRudiment tests, all supported CPU CI jobs,
and continued existing GPU tests. Rigged-mesh integration, shutter-wide bounds,
actual native style GPU kernels, cross-vendor parity and high-resolution
performance remain separately gated. No release artifacts are published here.

The next development family is M7: reusable spatial/procedural effect nodes,
building on the spectral selectors/displacement already implemented in M4. Keep
using the same graph, native controls, alpha association and capability contracts.
