# Contributing to FrameRudiments

Read README.md, AGENTS.md and docs/project-format.md. Inspect existing APIs before
adding a second clock, motion sampler, parser or encoder. Reusable audio analysis
belongs in StemLab; movement definitions and recording playback belong in
DanceRudiments. Release scenes and private assets remain outside this package.

Use a feature branch, current README and focused regression tests. Install
`.[dev]`, set `FRAMERUDIMENTS_TEST_FONT` and `FRAMERUDIMENTS_FFMPEG`, run `pytest`,
`ruff check .`, `python -m build` and `python -m twine check --strict dist/*`.
CI requires real encode/decode integration; skipped render checks do not prove
rendering. Submit a PR to https://github.com/kieransimkin/FrameRudiments/pulls.

Maintainers publish new immutable matching versions after exact-head validation.
Keep source, package, registry, technical QA and creative/listening approval
distinct. No private songs, portraits, credentials or campaign evidence in commits.

## Before every new workflow

Check the latest stable official release and the actual installed/imported version before starting; do not infer currency from a directory name or a development checkout. Read the current release notes, tagged capability documentation and relevant Potential problems. Use the [DanceFlow release preflight](https://github.com/kieransimkin/DanceFlow/blob/main/docs/workflows/release-preflight.md) and [complete capability references](https://github.com/kieransimkin/DanceFlow/blob/main/docs/capabilities/index.md).

People and agents should consider the full ecosystem, use all capabilities that materially serve the task, compose supported files/APIs, and improve a demonstrated reusable gap in its canonical tool. Record why no worthwhile change is justified when that is the result. Keep independent tool identities, compatibility, source clocks, privacy, model/download costs and publication/deployment permissions intact. Store weights and model-specific caches in the owning project's ignored `models/`.

Read [the people-and-agent ecosystem guide](docs/ECOSYSTEM.md) before selecting or extending a tool.
