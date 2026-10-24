# Effect graph M2 — compositor integration verification

**Status:** v13 candidate (requires full checkout CI).

This stage wires the forkable effect graph into FrameRudiments' isolated
compositor, preserving native DanceRudiment source time. Its purpose is to
establish trustworthy end-to-end rendering before extending the effects catalogue.

## Observable contracts

1. An `effect_graph` project uses only verified, reachable provider names.
   Unknown names are rejected by `Compositor(project)`.
2. `background` is the unmodified isolated RGBA background; `frame` includes
   all visible layers and final-frame legacy effects.
3. `scene` is available when a custom scene hook exists and a graph source
   requests it; the hook is rendered to its own transparent RGBA buffer.
4. Inactive valid layer sources (including text/lyric artwork outside their
   visible interval) are transparent, not errors and not stale cached images.
5. Graph inputs are immutable, and transition progress/control evaluation
   is determined solely by the project clock and motion source at that frame.
6. Projects without the graph retain the previous render path and outputs.
7. Project evidence records graph and control declarations without recording
   private image data or duplicating native musical analysis.

## Acceptance and test ownership

- `tests/test_effect_graph_integration.py`: source validation, transparent
  fallback, independent background image and frame-local control adaptation.
- `tests/test_effect_graph_project_integration.py`: synthetic pinned project,
  source identity, native motion transition at two frames, random-access
  repeatability, scene source and early error checking.
- `tests/test_effect_graph_transitions.py`: algebraic transition endpoints,
  feathering, radial and directional wipes, transparent-edge behaviour.
- Full `tests/` suite: non-graph output regression and installation.

## Dependency and rollout controls

Python/Pillow/DanceRudiments remain the base dependencies. The CPU chromatic,
chroma and transition kernels currently rely on NumPy, exposed via the optional
`effect_graph` extra; `gpu` or `dev` also supplies it. Render-time use without
NumPy should produce an actionable error rather than silently substitute
another algorithm (a separate usability task).

No live feedback, arbitrary shader execution or GPU graph backend is enabled
by this milestone. Those remain in the consolidated roadmap.
