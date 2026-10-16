# FrameRudiments development

Read README.md, docs/project-format.md and docs/extraction.md first. Preserve
source audio/clock identity and separation of technical QA from perceptual
approval. Use released DanceRudiments APIs and saved StemLab outputs; do not
copy movement definitions or DSP. Keep creative scene decisions in adapters.

Update README and relevant docs with feature changes. Add focused regression
coverage, run ruff and pytest with FFmpeg/font integration enabled, build wheel
and sdist, and smoke-test the installed wheel before a release. Keep credentials,
private media, song text, local paths and campaign evidence out of distributions.
Respect immutable tags, public deployment gates and explicit name ownership.
No source repository or public registry release is implied by a local package.
