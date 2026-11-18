"""Native M15.2 acceptance and standalone GitHub Pages media gallery fixture.

GStreamer must really decode VFR and capture bounded synthetic live buffers.
FFprobe remains the independent presentation clock. No network/device input.
"""
import argparse
from hashlib import sha256
import json
import shutil
from pathlib import Path
import subprocess
import wave

from PIL import Image, ImageOps

from framerudiments.gstreamer_ingest_m15 import (
    LIVE_SCHEMA, SCHEMA, capture_live, ingest_video,
)


def _hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def qualify(output, *, gst_launch=None, ffmpeg='ffmpeg', ffprobe='ffprobe'):
    ffmpeg = shutil.which(ffmpeg) or ffmpeg
    ffprobe = shutil.which(ffprobe) or ffprobe
    directory = Path(output).absolute()
    if directory.exists() or directory.is_symlink() or not directory.parent.is_dir():
        raise ValueError('M15.2 gallery requires a new output directory')
    directory.mkdir()
    source = directory / 'source-vfr.mp4'
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
           '-i', 'testsrc2=size=160x96:rate=30:duration=1',
           '-vf', 'select=eq(n\\,0)+eq(n\\,1)+eq(n\\,3)+eq(n\\,6)+eq(n\\,8)+eq(n\\,10)+eq(n\\,13)+eq(n\\,16)',
           '-fps_mode', 'vfr', '-c:v', 'libx264', '-preset', 'ultrafast',
           '-pix_fmt', 'yuv420p', '-an', str(source)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    audio = directory / 'synthetic-audio.wav'
    with wave.open(str(audio), 'wb') as output_wav:
        output_wav.setparams((1, 2, 48000, 48000, 'NONE', 'not compressed'))
        output_wav.writeframes(b'\0\0' * 48000)
    decoded = ingest_video(source, directory / 'ingest', sha256=_hash(source),
                           audio_source=audio, audio_sha256=_hash(audio),
                           gst_launch=gst_launch, ffmpeg=ffmpeg, ffprobe=ffprobe)
    if decoded['schema'] != SCHEMA or len(decoded['frames']) != 8 or not decoded['variable_frame_rate']:
        raise RuntimeError('VFR fixture did not preserve eight variably timed decoded frames')
    if decoded['audio_clock']['sample_rate'] != 48000 or decoded['audio_clock']['evidence']['source_sha256'] != _hash(audio):
        raise RuntimeError('Audio sample-clock provenance mismatch')
    live = capture_live(directory / 'live', source='test', limit=8,
                        width=160, height=96, fps='30', timeout=45, gst_launch=gst_launch)
    if live['schema'] != LIVE_SCHEMA or len(live['frames']) != 8:
        raise RuntimeError('Finite live GStreamer capture did not reach eight frames')
    # Genuine visual evidence; generate two small contact sheets from delivered PNGs.
    for name, source_dir in (('vfr', 'ingest'), ('live', 'live')):
        sheet = Image.new('RGB', (640, 192), '#0b1626')
        for i in range(8):
            with Image.open(directory / source_dir / f'frame-{i:06d}.png') as im:
                image = ImageOps.contain(im.convert('RGB'), (160, 96))
                sheet.paste(image, ((i % 4) * 160, (i // 4) * 96))
        sheet.save(directory / f'{name}-contact.png', optimize=True)
    summary = {
        'schema': 'framerudiments.gstreamer-m15-2-qualification.v1',
        'offline': {'source_sha256': _hash(source), 'frame_count': 8,
                    'vfr': True, 'ledger_sha256': _hash(directory / 'ingest' / 'index.json'),
                    'audio_clock_source_sha256': _hash(audio)},
        'live': {'source': 'videotestsrc', 'frame_count': 8,
                 'ledger_sha256': _hash(directory / 'live' / 'index.json'),
                 'reconnects': live['reconnections'], 'backpressure': live['backpressure']},
        'source_ownership': 'synthetic/generated-in-ci', 'network_input': False,
        'device_input': False, 'third_party_media': False,
        'source_pts_independently_checked': True, 'perceptual_approval': False,
    }
    (directory / 'qualification.json').write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n',
                                                  encoding='utf-8')
    page = '''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GStreamer VFR and Live Capture · FrameRudiments</title>
<style>:root{color-scheme:dark;font:16px/1.5 system-ui,sans-serif;background:#0b1220;color:#e9f1ff}
*{box-sizing:border-box}body{max-width:1150px;margin:auto;padding:35px 20px}
a{color:#78e9d6}h1{font-size:clamp(2.3rem,5vw,4rem);line-height:1.08}
p,small{color:#b7c8e3}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(315px,1fr));gap:25px}
article{background:#14223b;border:1px solid #31476a;border-radius:16px;overflow:hidden}
article>div{padding:16px 20px 22px}article img,video{display:block;width:100%;height:auto;background:#081221}
.links{display:flex;gap:16px;flex-wrap:wrap}label{display:flex;gap:8px;align-items:center}
input[type=range]{width:100%;accent-color:#5cdbce}output{min-width:44px}
.notice{border-left:4px solid #eab57f;padding:14px 20px;background:#18253b}
</style></head><body><small>FrameRudiments / M15.2 · Generated in GitHub Actions</small>
<h1>Frames, exactly when they happened.</h1>
<p>These are genuine decoded PNGs and a real GStreamer live test-source capture. No external cameras,
network streams or real users' audio were accessed. The source video has variable presentation intervals.</p>
<div class="grid"><article><video controls muted playsinline preload="metadata" poster="vfr-contact.png" src="source-vfr.mp4"></video>
<div><h2>Variable frame-rate ingest</h2><p>Actual GStreamer decode matched independently probed FFmpeg
presentation timestamps to one nanosecond. The audio-sample ledger uses a pinned synthetic 48 kHz WAV.</p>
<div class="links"><a href="ingest/index.json">PTS + SHA-256 ledger</a><a href="vfr-contact.png">Frame sheet</a></div></div></article>
<article><img id="live" alt="GStreamer synthetic live capture frame" src="live/frame-000000.png" width="160" height="96">
<div><h2>Bounded live media graph</h2><p>Eight real GStreamer buffers captured with a four-buffer leaky queue
and recorded source PTS. Disconnect/reconnect and timeout handling are separately tested.</p>
<label>Frame <input id="seek" aria-label="Scrub captured frames" type="range" min="0" max="7" value="0">
<output id="position">0 / 7</output></label>
<div class="links"><a href="live/index.json">Live PTS ledger</a><a href="live-contact.png">Frame sheet</a></div></div></article></div>
<p class="notice"><strong>Qualification scope:</strong> This is an offline, bounded capture demonstration.
It is not a live network broadcaster, an audio mixer, a device-permission approval or a hardware GPU test.
The displayed stills use synthetic source media.</p>
<p class="links"><a href="qualification.json">CI qualification receipt</a>
<a href="../">Full renderer atlas</a><a href="../timeline/">Interactive timeline viewer</a>
<a href="https://github.com/kieransimkin/FrameRudiments">Source</a></p>
<script>const seek=document.getElementById('seek');const frame=document.getElementById('live');
seek.addEventListener('input',()=>{const n=Number(seek.value);if(!Number.isInteger(n)||n<0||n>7)return;
frame.src='live/frame-'+String(n).padStart(6,'0')+'.png';document.getElementById('position').textContent=n+' / 7';});</script>
</body></html>'''
    (directory / 'index.html').write_text(page, encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--gst-launch')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    args = parser.parse_args()
    print(json.dumps(qualify(args.output, gst_launch=args.gst_launch,
                             ffmpeg=args.ffmpeg, ffprobe=args.ffprobe), indent=2, sort_keys=True))
