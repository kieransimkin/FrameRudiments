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
