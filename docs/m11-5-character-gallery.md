# M11.5 — Kandi Boom ⇄ visualiser gallery and CI evidence

**Milestone:** CI-published, inspectable comparison gallery after M11.4 rational video adapters.

The gallery runs the **real FrameRudiments `EffectGraph` and native Rudiment `spectrum` renderer**, producing three 48-frame (24 fps) panels: the pinned character input, native synthetic-spectrum visualiser, and the output of the dissolve, alpha-matte or spectral transition. It publishes accessible HTML, inline MP4 playback, actual frame posters, a JSON provenance receipt and verified MP4 timestamps.

## Character authenticity and scope

Two real static render captures of **Kandi Boom, from Bass Ben & Friends**, are sourced from `kieransimkin/DanceRudiments` at commit `718388ccdb992c0b97dc0a6d27b4d3043e20b5fa`:

- `docs/images/kandi-boom-rest.png` (`Git blob 206d1ef6e4459eb47a0be54da39c95cd2d6e33b4`).
- `docs/images/kandi-boom-arms-raised.png` (`Git blob 1d4d362ed62b8149cbd9ef14e8858f690a6a1eab`).

The generator refuses a missing/mismatched source and records the byte-level SHA-256 in the catalogue. A SHA-pinned static screenshot **is not live FBX skinning or motion capture**. The original rig demonstration remains owned by DanceRudiments/Unity. The source images are letterboxed without replacement, inpainting or pretending to be freshly rendered by FrameRudiments.

The test visualiser uses deterministic **synthetic frequency-channel values** and the native `spectrum` renderer. It does not analyse music or claim verified choreography. Clips are silent; actual soundtrack, beat-grid and live character synchronization require a separately approved integration. Downstream M12/M13 or DanceRudiments mesh integration may add those capabilities, without changing this provenance.

## Gallery invariants

- The output consists of three labelled 480×270 RGB panels, with a 36-pixel title rail: 1440×306.
- Exactly 48 frames at 24/1 fps are rendered for each transition; the graph progresses from 0 to 1 on the output frame ruler.
- Each frame is rendered sequentially and again in **reverse request order**. Every raw RGB SHA-256 must match before encoding.
- M11.4's `encode_manifest` validates ordered SHA-pinned RGB PNG inputs and checks decoded presentation timestamps and the full MP4 decode. Intermediate PNG sequences are removed from the published site after successful encoding; the frame SHA list remains in `catalogue.json`.
- All published posters, video files and upstream sources are hash-referenced. `--verify` checks file identity, scene order and frame-count receipts without rerendering.
- Synthetic offline unit-test images are accepted by a **Python-only test fixture** and are explicitly labelled as non-Kandi; the production CLI always fetches and verifies the authentic pinned source assets.
- Existing gallery scripts, renderers, external GPU effects and M11.4 FPS semantics are unchanged.

## Run and review

```bash
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_m11_5_gallery.py
ruff check scripts/generate_m11_5_gallery.py tests/test_m11_5_gallery.py

# Extend a CI-generated site, with internet access for immutable source assets:
python scripts/generate_visualisation_gallery.py --output _site
python scripts/generate_m11_5_gallery.py --output _site
python scripts/generate_m11_5_gallery.py --verify _site/m11-5
```

The [`visualisation-gallery.yml`](../.github/workflows/visualisation-gallery.yml) workflow runs the M11.5 tests, renders the showcase and checks the generated site. The existing artifact and GitHub Pages deployment then publish `m11-5/index.html`; no second competing Pages deployment is created.

A locally passing suite is **not** equivalent to successful exact-commit Windows/Linux CI, published GitHub Pages, browser playback or perceptual review. Verify those separately before approving M11.5 for release.
