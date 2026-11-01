"""Generate deterministic M5.5 beat timing transition comparisons and hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph import EffectGraph  # noqa: E402


PRESETS = {
    'linear': {'duration_beats': 4},
    'smoothstep': {'duration_beats': 4, 'preset': 'smoothstep'},
    'pulse': {'duration_beats': 4, 'preset': 'pulse'},
    'pingpong': {'duration_beats': 2, 'repeat': 'pingpong'},
    'non-monotonic': {'duration_beats': 4, 'keys': [
        {'at': 0, 'value': 0}, {'at': .25, 'value': 1},
        {'at': .5, 'value': .15}, {'at': 1, 'value': 1},
    ]},
}


def _sources(size):
    width, height = size
    a = Image.new('RGBA', size, '#122d48')
    d = ImageDraw.Draw(a)
    d.ellipse((width // 10, height // 6, width * 3 // 5, height * 5 // 6),
              fill='#28d8cd', outline='#f7e7c0', width=3)
    d.line((0, height - 1, width - 1, 0), fill='#fd8e63', width=5)
    b = Image.new('RGBA', size, '#391b42')
    d = ImageDraw.Draw(b)
    d.polygon([(width // 2, 6), (width - 8, height // 2), (width // 2, height - 8),
               (8, height // 2)], fill='#ff7c42', outline='#ffe2ac')
    d.rectangle((width * 2 // 3, height // 5, width - 5, height * 4 // 5),
                fill='#b454ff')
    return {'a': a, 'b': b}


def build(out_path, manifest_path=None):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 220, 124
    beats = [0, 1, 2, 3, 4, 5]
    rowheight = height + 22
    canvas = Image.new('RGB', (len(beats) * width, len(PRESETS) * rowheight), '#090e1b')
    source = _sources((width, height))
    hashes = {}
    for row, (name, timing) in enumerate(PRESETS.items()):
        graph = EffectGraph({'nodes': [
            {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
            {'id': 't', 'type': 'transition', 'inputs': ['a', 'b'], 'timing': timing},
        ], 'output': 't'})
        for column, beat in enumerate(beats):
            image = graph.render(source, context=SimpleNamespace(beat=beat))
            digest = hashlib.sha256(image.tobytes()).hexdigest()
            hashes[f'{name}:{beat}'] = digest
            x, y = column * width, row * rowheight
            canvas.paste(image.convert('RGB'), (x, y))
            ImageDraw.Draw(canvas).text((x + 8, y + height + 3),
                                        f'{name} / beat {beat}', fill='#d7e2f5')
    canvas.save(out_path)
    manifest = {
        'schema': 'framerudiments.effect-graph.m5-5.qa.v1',
        'size': list(canvas.size), 'beats': beats, 'frame_hashes': hashes,
        'sheet_sha256': hashlib.sha256(canvas.tobytes()).hexdigest(),
    }
    if manifest_path:
        manifest_path = Path(manifest_path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.manifest), indent=2))
