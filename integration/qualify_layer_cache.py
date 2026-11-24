"""Actual native per-layer reuse, edit-local redraws and encoded pixel parity."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import wave

ROOT = Path(__file__).resolve().parents[1]
if '--installed' not in sys.argv:
    sys.path.insert(0, str(ROOT/'src'))
from PIL import Image, ImageDraw  # noqa: E402
from framerudiments import Project, __version__  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments.encoding import binaries, probe  # noqa: E402
from framerudiments.layer_frame_cache import LayerFrameCache  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from framerudiments.render import preflight, render  # noqa: E402


def source(directory):
    directory.mkdir()
    audio = directory/'source.wav'
    with wave.open(str(audio), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b'\x00\x00'*48000*4)
    sprite = Image.new('RGBA', (96, 96))
    draw = ImageDraw.Draw(sprite)
    points = []
    for n in range(20):
        a = math.pi*n/10
        r = 43 if n % 2 == 0 else 24
        points.append((48+r*math.cos(a), 48+r*math.sin(a)))
    draw.polygon(points, fill='#ffb266')
    draw.ellipse((35, 35, 61, 61), fill='#7e3b68')
    sprite.save(directory/'star.png')
    def ref(name):
        return {'path': name, 'sha256': sha256((directory/name).read_bytes()).hexdigest()}
    data = dict(schema='framerudiments.project.v1', width=480, height=270, fps=60,
        purpose='visualiser', title='Native per-layer cache qualification',
        audio=dict(source=ref('source.wav'), sample_rate=48000,
                   start_sample=0, end_sample=192000, repeats=1, fade_seconds=0),
        clock=dict(bpm=120, origin=0, authority='synthetic cache qualification', status='synthetic'),
        background=dict(colour='#112632'),
        layers=[
            dict(id='trace', type='rudiment', renderer='path',
                 motion=dict(kind='named', pattern='circle'),
                 bounds=[15, 15, 465, 255], history_frames=180,
                 style=dict(padding=8, radius=3, line_width=2)),
            dict(id='star', asset=ref('star.png'), center=[180, 135], width=56,
                 motion=dict(kind='named', pattern='circle', interpolate=True),
                 mapping=dict(dx=80, dy=45)),
            dict(id='sparks', type='emitter', kind='particles', bounds=[0, 0, 480, 270],
                 emission=dict(mode='continuous'),
                 style=dict(seed=42, rate=70, size=5, lifetime=1.2))],
        render_quality=dict(enabled=True, spatial_scale=1, temporal_samples=1, antialias=0))
    path = directory/'project.json'
    path.write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    return path, data


def counted(compositor):
    calls = {layer.id: 0 for layer in compositor.layers}
    for layer in compositor.layers:
        original, name = layer.draw, layer.id
        def draw(image, context, original=original, name=name):
            calls[name] += 1
            return original(image, context)
        layer.draw = draw
    return calls


def decoded(video, ffmpeg, ffprobe):
    stream = next(s for s in probe(video, ffprobe, count=True)['streams'] if s['codec_type'] == 'video')
    assert stream['r_frame_rate'] == stream['avg_frame_rate'] == '60/1'
    assert int(stream['nb_read_frames']) == 240
    rows = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(video),
        '-map', '0:v:0', '-f', 'framehash', '-hash', 'sha256', '-'], encoding='utf-8')
    hashes = [line.rsplit(',', 1)[1].strip() for line in rows.splitlines() if line and not line.startswith('#')]
    assert len(hashes) == 240 and len(set(hashes)) >= 180
    assert all(a != b for a, b in zip(hashes, hashes[1:]))
    return rows, dict(frames=240, fps='60/1', unique_frames=len(set(hashes)),
                     adjacent_duplicates=0, sha256=sha256(video.read_bytes()).hexdigest())


def qualify(output):
    output = Path(output)
    temporary_preflight(output.parent)
    output.mkdir(exist_ok=False)
    path, data = source(output/'inputs')
    project = Project.load(path)
    ffmpeg, ffprobe = binaries()
    preflight(project, output/'preflight', max_frames=8, layer_cache=False,
              ffmpeg=ffmpeg, ffprobe=ffprobe)
    with TemporaryDirectory(prefix='layer-cost-', dir=output.parent) as cost_directory:
        peak_cache = LayerFrameCache(cost_directory)
        peak = Compositor(project, layer_cache=peak_cache)
        try:
            start = perf_counter()
            for frame in range(180, 188):
                peak.frame(frame)
            measured = perf_counter()-start
        finally:
            peak.close()
            peak_cache.close()
    ownership = dict(sample_frames=list(range(180,188)), sample_seconds=measured, persistence_included=True,
                     estimated_three_exports_seconds=measured/8*240*3,
                     owner='active invocation', reason='Four-second synthetic clips; measured peak below 15 minutes')
    (output/'render-ownership.json').write_text(json.dumps(ownership, indent=2), encoding='utf-8')
    assert ownership['estimated_three_exports_seconds'] < 900
    samples = list(range(60, 92))
    timings = {'fresh_ms': [], 'warm_ms': []}
    images = {}
    with TemporaryDirectory(prefix='layer-cache-', dir=output.parent) as cache_directory:
        cache = LayerFrameCache(cache_directory)
        try:
            render(Project.load(path), output/'base', layer_cache=cache, preset='veryfast', crf=16,
                   ffmpeg=ffmpeg, ffprobe=ffprobe)
            warm = Compositor(Project.load(path), layer_cache=cache)
            fresh = Compositor(Project.load(path), layer_cache=False)
            warm_calls = counted(warm)
            try:
                for frame in samples:
                    start = perf_counter()
                    expected = fresh.frame(frame)[0]
                    timings['fresh_ms'].append((perf_counter()-start)*1000)
                    start = perf_counter()
                    actual = warm.frame(frame)[0]
                    timings['warm_ms'].append((perf_counter()-start)*1000)
                    assert actual.tobytes() == expected.tobytes()
                    if frame == samples[0]:
                        images['base'] = actual.copy()
                assert all(value == 0 for value in warm_calls.values())
            finally:
                warm.close()
                fresh.close()
            edited = deepcopy(data)
            edited['layers'][1].update(center=[220, 135], width=72, opacity=.45)
            edited_path = output/'inputs/edited-project.json'
            edited_path.write_text(json.dumps(edited, indent=2)+'\n', encoding='utf-8')
            actual = Compositor(Project.load(edited_path), layer_cache=cache)
            fresh = Compositor(Project.load(edited_path), layer_cache=False)
            edited_calls = counted(actual)
            try:
                for frame in samples:
                    cached_pixels = actual.frame(frame)[0]
                    assert cached_pixels.tobytes() == fresh.frame(frame)[0].tobytes()
                    if frame == samples[0]:
                        images['edited'] = cached_pixels.copy()
                assert edited_calls == {'trace': 0, 'star': len(samples), 'sparks': 0}
            finally:
                actual.close()
                fresh.close()
            render(Project.load(edited_path), output/'edited', layer_cache=cache, preset='veryfast', crf=16,
                   ffmpeg=ffmpeg, ffprobe=ffprobe)
            stats = cache.stats()
        finally:
            cache.close()
        reopened = LayerFrameCache(cache_directory)
        owner = Compositor(Project.load(edited_path), layer_cache=reopened)
        reopen_calls = counted(owner)
        try:
            assert owner.frame(samples[0])[0].tobytes() == images['edited'].tobytes()
            assert all(value == 0 for value in reopen_calls.values())
        finally:
            owner.close()
            reopened.close()
    # A separately rendered uncached export must decode to exactly the same pixels.
    render(Project.load(edited_path), output/'reference', layer_cache=False, preset='veryfast', crf=16,
           ffmpeg=ffmpeg, ffprobe=ffprobe)
    base_rows, base_video = decoded(output/'base/video.mp4', ffmpeg, ffprobe)
    edited_rows, edited_video = decoded(output/'edited/video.mp4', ffmpeg, ffprobe)
    fresh_rows, fresh_video = decoded(output/'reference/video.mp4', ffmpeg, ffprobe)
    assert edited_rows == fresh_rows
    for name in ('base', 'edited'):
        subprocess.run([ffmpeg, '-v', 'error', '-i', str(output/name/'video.mp4'),
                        '-vf', r'select=eq(n\,60)', '-frames:v', '1', str(output/(name+'-poster.png'))],
                       check=True)
    comparison = Image.new('RGB', (960, 330), '#102030')
    ink = ImageDraw.Draw(comparison)
    comparison.paste(images['base'], (0, 32))
    comparison.paste(images['edited'], (480, 32))
    ink.text((12, 10), 'Native original / warmed layers', fill='white')
    ink.text((492, 10), 'Only the star layer edited / trace and sparks reused', fill='white')
    ink.text((12, 309), 'Actual native redraws over 32 edited frames: star 32, trace 0, sparks 0. Encoded pixels match uncached export.', fill='white')
    comparison.save(output/'comparison.png')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT))
    report = dict(schema='framerudiments.layer-cache-qualification.v1', status='passed',
                  version=__version__, source_revision=revision, working_tree_dirty=dirty,
                  synthetic=True, width=480, height=270, source_clock_unchanged=True,
                  original_warm_draws=warm_calls, edited_draws=edited_calls,
                  reopened_draws=reopen_calls, native_pixel_comparisons=len(samples)*2,
                  encoded_edited_matches_uncached=True, base=base_video, edited=edited_video,
                  reference=fresh_video, cache=stats, render_ownership=ownership,
                  cache_temporary_removed=not Path(cache_directory).exists(),
                  timing=dict(sample_frames=samples, samples=len(samples),
                    uncached_median_ms=median(timings['fresh_ms']),
                    warm_median_ms=median(timings['warm_ms']),
                    measured_ratio=median(timings['fresh_ms'])/median(timings['warm_ms']),
                    scope='Measured on this host only; excludes compositor setup; disk cache includes source verification',
                    raw=timings))
    (output/'qualification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    (output/'edited-framehash.txt').write_text(edited_rows, encoding='utf-8')
    (output/'index.html').write_text(PAGE, encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','version','original_warm_draws','edited_draws','reopened_draws')}))
    return report


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FrameRudiments: reuse unchanged composited layers</title>
<style>
body{background:#102030;color:#f5ede2;font:18px system-ui;max-width:1100px;margin:auto;padding:24px}
h1{font-size:clamp(1.8rem,5vw,2.8rem)}a{color:#8fe4da}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}
.player{position:relative}video,img{display:block;width:100%;height:auto}button.picture{position:absolute;inset:0 0 52px;width:100%;border:0;background:transparent;cursor:pointer}
button:focus-visible{outline:3px solid #ffb266}figcaption{margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:700px){.pair{grid-template-columns:1fr}}
</style>
<h1>Reuse unchanged composited layers</h1>
<p>Actual native FrameRudiments output from synthetic silent test data at 60 fps.
Only the star's size, position and opacity change. The trace and particle layers retain their cached native pixels.</p>
<div class="pair">
<figure><div class="player"><video id="base" controls muted playsinline preload="metadata" poster="base-poster.png" src="base/video.mp4"></video><button class="picture" aria-label="Play or pause original scene" data-video="base"></button></div><figcaption>Original scene. All layers are warmed in the persistent cache.</figcaption></figure>
<figure><div class="player"><video id="edited" controls muted playsinline preload="metadata" poster="edited-poster.png" src="edited/video.mp4"></video><button class="picture" aria-label="Play or pause edited scene" data-video="edited"></button></div><figcaption>Edited star layer; the other native layers are reused. The complete decoded export matches a fresh uncached render.</figcaption></figure>
</div>
<img src="comparison.png" alt="Actual original and edited compositor frames with measured per-layer native redraw counts">
<p>During 32 checked edited frames, native redraws were star 32, trace 0 and sparks 0. A fresh cache owner then reused all three layers. These counters come from actual native calls, not a UI simulation.</p>
<p>The independent final-frame cache and all source/quality/generation checks remain in place. Masks, animations and graph effects recompose from the cached raw layers. Timings are specific to the measured host, not a universal frame-rate claim.</p>
<p><a href="qualification.json">Native calls, pixel proofs and timing measurements</a> · <a href="edited-framehash.txt">Full decoded edited frame hashes</a> · <a href="https://github.com/kieransimkin/FrameRudiments/blob/main/docs/layer-frame-cache.md">Cache guide</a> · <a href="../">Gallery</a></p>
<script>
document.querySelectorAll('button.picture').forEach(function(button){
button.addEventListener('click',function(){var video=document.getElementById(button.dataset.video);if(video.paused){video.play()}else{video.pause()}});
});
</script>
</html>"""


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--installed', action='store_true')
    qualify(parser.parse_args().output)