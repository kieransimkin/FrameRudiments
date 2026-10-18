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
Submit focused improvements to https://github.com/kieransimkin/FrameRudiments/pulls.
Validate exact-head CI and immutable matching releases; do not rewrite tags or
assets. GitHub and PyPI publication are separately verified states. Website:
https://kieransimkin.co.uk/danceflow/.

Future v* tags publish automatically after CI. Keep the pypi environment free
of human reviewer gates; preserve tests, source/tag identity, checksum validation
and immutable assets. New publication destinations still require user authority.

## Full ecosystem and release preflight

Before every new workflow run, check the latest stable official release and observe the actual runtime used by the job. Follow [the shared preflight](https://github.com/kieransimkin/DanceFlow/blob/main/docs/workflows/release-preflight.md) and this project's [people-and-agent guide](docs/ECOSYSTEM.md). Review the complete relevant capability inventory rather than only a familiar command. Consider every DanceFlow component against the actual task; use and compose all applicable supported capabilities, and improve a concrete reusable gap in its canonical repository. Record why existing capabilities suffice when no change is justified. No catalogue checklist expands the task, authorises model downloads/training, or proves integration. Preserve exact source/package identity, clocks, model-local storage, privacy, costs, immutable releases and separate deployment gates.
