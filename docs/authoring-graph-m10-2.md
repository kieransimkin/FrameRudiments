# M10.2 — Effect-graph connection authoring

M10.2 extends the existing React timeline workspace with editable connections,
independent branches, ordered composites, mattes and transitions. The editor
continues to export the native `effect_graph` field in
`framerudiments.project.v1`; it does not add a persistent layout format, a second
effect executor or a new clock. This is an implementation candidate. Successful
schema validation and focused automated tests do not establish physical-device
or perceptual acceptance.

## Editing a graph

Select **Effect DAG**. A project without an executable graph offers an explicit
creation action starting from the final `frame` source. An existing graph opens
with deterministic node placement and its current output marked.

- Click an output, then the intended input; selecting the input first also works.
  Dragging from an output provides the same connection operation. Escape cancels
  an unfinished wire. Compatibility cues explain rejected targets.
- The selected node's input controls expose each source in native order. These
  controls support reconnection without dragging and preserve every other node
  setting. Composite and matte inputs can be added, removed and reordered within
  their native limits.
- Fork a selected source or effect to create a copy branch while retaining the
  existing output. Select explicit inputs for a new merge, matte or transition;
  the entire node is added atomically. An optional output choice controls whether
  a newly added template becomes the graph output.
- Register an available project source and connect it by its actual provider ID.
  Choosing a graph output is an explicit operation. Existing undo/redo applies
  to complete graph edits and remains bounded by the workspace's history limit.

The templates are deliberately small, native-valid starting points:

| Template | Ordered inputs | Initial native settings |
|---|---|---|
| Copy | Image | `type: copy` |
| Opacity | Image | `type: opacity`, opacity `1` |
| Blur | Image | `type: filter`, blur radius `1.5` |
| Composite | 2–32 layers in compositing order | `mode: over` |
| Matte | Image, then 1–8 masks | Alpha channel, multiply mask combination |
| Transition | From image, to image | Dissolve, progress `0.5` |
| Matte transition | From image, to image, mask | Native `transition_style: matte`, progress `0.5` |

The editor does not persist incomplete multi-input nodes while a user makes
choices. Repeated use of the same upstream image is permitted where native
validation permits it. Deleting a required input, connecting a node to itself,
introducing a cycle or selecting a missing source rejects the whole edit and
retains the previous draft.

## Source identities and port roles

The ordinary authoring compositor provides `frame`, `background`, each
`layer:<id>`, `identity:<index>` and `lyric-art:<index>` present in the project.
Caption, word-caption and word-indicator providers appear only when their
corresponding native project configuration makes them available. This route
has no external scene callback. Arbitrary source aliases are not accepted.

All executable image connections carry **RGBA8**. A mask, displacement map or
texture is an interpretation of those pixels by the receiving effect. Those
roles do not create incompatible synthetic pixel formats or implicit casts.

The inspector also understands the arity of existing specialised nodes:

| Native node | Input contract |
|---|---|
| Procedural field | One canvas/coverage reference |
| Spatial warp | Image plus displacement map for `displacement`; one image otherwise |
| Layer style | Image plus texture only for a texture-backed `pattern_overlay`; one image otherwise |
| Transition | Two images; a third mask only for the matte style |
| Transition chain | 2–8 ordered images, preserving authored stages and preprocessing |

Existing transition-chain stage masks are shown as additional read-only
dependencies. Their references participate in topology validation, but this
milestone does not provide nested stage or preprocessing editors. Connections
target authored node IDs; generated internal chain IDs are not exposed as
editing endpoints. Existing specialised parameters and native control bindings
are retained when rewiring top-level inputs.

## Atomic edits and authoritative validation

The TypeScript authoring model rejects missing inputs, duplicate IDs, unknown
node kinds, unavailable providers, self-connections, cycles and invalid native
input counts. It checks disconnected branches too, including nested chain mask
dependencies, generated-ID collisions and the native expanded-node limit.
Ordinary graphs are bounded to 256 nodes and 32 inputs per node; tighter
effect-specific limits still apply. Graph edits preserve source/audio clocks,
asset hashes, recorded motion, existing controls and unchanged effect settings.

Python remains the authority for the complete effect-specific schema. The
same-origin `POST /api/graph/validate` accepts the current project and permitted
edited sidecars under the existing bounded local-authoring request contract.
It prepares the native project with its pinned inputs, constructs `EffectGraph`
and `EffectGraphControls`, and checks every expanded dependency and provider.
Control construction validates native targets and motion admission without
evaluating a frame or sampling a motion clock.

The bounded `framerudiments.authoring-graph-validation.v1` report contains
request/graph hashes, source and native-port metadata, and a fixed diagnostic
category on failure. Its scope explicitly records `rendered: false` and
`gpu_context_opened: false`. No submitted project or original sidecar is written
back to disk. Temporary edited sidecars are owned and cleaned up by the route.

For a project with a graph, the UI waits for a successful native check of the
current draft before one-shot preview, adaptive preview or project export.
Editing, undo/redo or changing sidecar contents invalidates the prior result.
Aborted and stale responses cannot approve a newer snapshot. An unavailable
check can be retried while the draft remains editable. Projects without a graph
retain their existing preview/export path.

Validation does not render text or layers, exercise a GPU, certify a plugin
runtime, or approve production output. Continue to run native `plan`,
`preflight` and `render` on the exported bundle as described in the
[workspace guide](authoring-workspace.md#export-and-verification).

## Qualification and next milestone

Focused Node coverage exercises atomic reconnection, forks, complete templates,
native arities, source availability, immutable preservation, nested dependencies,
interaction state and stale validation fencing. Native tests exercise canonical
parameters and bindings, disconnected invalid branches, endpoint admission,
pin protection and temporary-file cleanup. These checks are separate from
browser interaction evidence and native rendered-pixel comparisons.

With Node 24 and the ordinary Python package installed, run this explicit
cross-language qualifier from the repository root with a new output directory:

```sh
python integration/qualify_graph_authoring_m10_2.py --output graph-authoring-evidence
```

It calls `authoring/integration/graphRoundtrip.ts` to perform real model edits
and build the export bundle, then loads the resulting project through native
Python and compares rendered pixels with independently hand-authored reference
graphs. Synthetic pinned inputs exercise fork/merge order, rewiring, single and
multiple mattes, transition endpoints, a matte transition, preserved native
controls and repeat-clock behavior. It writes
`graph-authoring-qualification.json` and `graph-authoring-contact-sheet.png`,
and removes its owned input workspace. The authoring CI workflow runs the same
qualifier. This CPU command requires no browser, GPU, FFmpeg or private media;
it does not substitute for interactive browser or device qualification.

The subsequent **M10.3 generated parameter and DanceNotion control editors**
are now an [implementation candidate](authoring-controls-m10-3.md) in the
[unified roadmap](unified-effects-transitions-rendering-roadmap.md#m10--authoring-interface).
M10.4 transition preview work and M10.5 broader round-trip/undo-redo qualification
remain separately scoped. The older granular plan uses different milestone
numbers and is preserved as a historical dependency plan.
