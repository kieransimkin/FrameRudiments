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

## Preflight temporary-directory access

Before any long, costly, model-backed, media-rendering, build, test, packaging or release command that creates private or atomic temporary directories, run a disposable preflight with the exact runtime, user or sandbox, and intended output parent. The probe must create a unique directory, write and read a file, atomically rename it, then remove the file and directory.

Keep temporary data and caches under the owning project on `Z:`; model weights and model-specific caches remain under that project's ignored `models/`. If the probe returns `WinError 5`, access denied, a rename failure or a cleanup failure, stop before the expensive command. Preserve the exact error and use the documented narrowly approved unrestricted recovery from the outset only when the task requires it. Use a fresh output directory and do not repeat the already-failing full job in the restricted shell.

After any unrestricted recovery, verify that the final output is readable and writable from the normal managed environment. If it is not, copy the completed bytes into a pre-created project-local directory that passed the probe, verify hashes, and retain the original failure and recovery evidence. Do not edit global ACLs, switch caches to `C:`, terminate unrelated processes or claim the underlying permission problem is fixed. Repositories that own temporary-directory creation should provide and test a reusable preflight helper rather than relying only on this instruction.
