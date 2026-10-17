# FrameRudiments Visual Authoring

The local-only Phase 5 editor uses the official `react-timeline-sequence@0.4.0`
React control. Run `npm install && npm run build`, then launch
`python ../scripts/authoring_preview.py /path/to/project.json` from this folder.

Full documentation: [docs/authoring-workspace.md](../docs/authoring-workspace.md).

Source assets stay hash-pinned and are never written from the browser. Use
**Export project & sidecars** to create a new portable project bundle, then
validate the result with the existing FrameRudiments `plan` / `preflight`
commands. The preview intentionally does not replay GPU feedback engines.
