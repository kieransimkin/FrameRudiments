"""Render public-safe native particle fixtures; no audio or private artwork."""
import argparse
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
from framerudiments.particle_engine import ParticleEmitter

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
project = SimpleNamespace(width=3840, height=2160, fps=30, frame_count=30,
                          sample_rate=48000, start_sample=0, end_sample=48000,
                          repeats=1, base=Path('.'), inputs=[],
                          audio_ref={'sha256': 'a'*64},
                          clock=SimpleNamespace(beat_at=lambda t: t*2))
def context(frame):
    return SimpleNamespace(frame=frame, source_seconds=frame/30,
                           output_seconds=frame/30, beat=frame/15, lyric=None, cue=None)
sheet = Image.new('RGB', (1280, 420), '#18142b')
for column, scale in enumerate([1, 8]):
    cfg = dict(id='synthetic-star', type='emitter', kind='particles',
               bounds=[0, 0, 3840, 2160], raster_scale=scale,
               style=dict(seed=42, rate=30, size=96, speed=0, turbulence=0,
                          lifetime=1, shape='star', colour='#ef83c0',
                          secondary_colour='#80cfe6', rotation_randomness=0))
    emitter = ParticleEmitter(project, cfg, context)
    image = Image.new('RGBA', (3840, 2160))
    emitter.draw(image, context(0))
    image = image.resize((640, 360), Image.Resampling.LANCZOS)
    sheet.paste(image, (column*640, 20), image)
    ImageDraw.Draw(sheet).text((column*640+24, 385), f'Native 4K canvas; raster_scale={scale}', fill='white')
    emitter.close()
args.output.parent.mkdir(parents=True, exist_ok=True)
sheet.save(args.output)
