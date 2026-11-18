# CI-generated interactive timeline viewer

The GitHub Pages gallery includes the real, published `react-timeline-sequence@0.4.0`
React timeline component built with Vite at `/FrameRudiments/timeline/`.
It uses original synthetic 16-second 120 BPM WAV created and SHA-receipted in CI.

It demonstrates transport, scrubbing, zoom, beat grid, lyric/word/cue timing,
layer blocks and keyframes. The word markers can be dragged: changes remain in
browser memory and Reset restores the fixture. No private project/audio is loaded;
there are no `/api/` endpoints, cookies, native preview servers or persisted edits.

## Build / release

```bash
npm --prefix authoring install --no-audit --no-fund
npm --prefix authoring run build:gallery
mkdir -p _site/timeline
cp authoring/dist-timeline/timeline-gallery.html _site/timeline/index.html
cp -R authoring/dist-timeline/assets _site/timeline/assets
python scripts/generate_timeline_gallery_audio.py --output _site/timeline
python scripts/generate_timeline_gallery_audio.py --output _site/timeline --verify
```

The existing `visualisation-gallery.yml` job builds this page alongside its other
galleries and publishes one Pages artifact. Vite uses the `/FrameRudiments/timeline/`
base, rather than the local authoring Vite config (`/api` proxy), so JS/CSS load
from the correct project subpath. The full private authoring workspace and native
preview remain local-only; exposing those endpoints on public Pages is unsafe and
would not work on a static host. `docs/authoring-workspace.md` covers the editor.

This is a working public timeline **viewer demonstration**, not a live project
management service or a claim that any synthetic beat/audio data came from StemLab.
