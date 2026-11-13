"""Prepare two synthetic cards and source-bound 60 fps projects for the GL demos."""
import argparse
from pathlib import Path
import wave

from PIL import Image, ImageDraw

from framerudiments.gl_transitions import shader_catalogue
from framerudiments.io import sha256, write_json

SIZE = (640, 360)
SAMPLE_RATE = 48000
SAMPLE_COUNT = SAMPLE_RATE * 3


def ref(path):
    return {'path': path.name, 'sha256': sha256(path)}


def draw_cards(directory):
    width, height = SIZE
    a = Image.new('RGB', SIZE, '#101d42')
    b = Image.new('RGB', SIZE, '#5e1f35')
    front = ImageDraw.Draw(a)
    back = ImageDraw.Draw(b)
    for y in range(22, height, 29):
        for x in range(18, width, 29):
            front.ellipse((x, y, x + 3, y + 3), fill='#314671')
    for x in range(-height, width, 31):
        back.line((x, 0, x + height, height), fill='#893b4c', width=2)
    front.polygon([(104, 258), (224, 66), (344, 258)], fill='#70ddfa')
    front.line([(134, 241), (224, 98), (314, 241), (134, 241)], fill='#1b3963', width=5)
    front.ellipse((421, 190, 543, 312), fill='#f4cc6e')
    back.rounded_rectangle((303, 65, 574, 250), radius=24, fill='#f5a250')
    back.ellipse((105, 118, 289, 302), fill='#f9cad6')
    back.ellipse((139, 152, 255, 268), fill='#5e1f35')
    a_path, b_path = directory / 'outgoing.png', directory / 'incoming.png'
    a.save(a_path)
    b.save(b_path)
    return a_path, b_path


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=False)
    a, b = draw_cards(directory)
    audio = directory / 'silence.wav'
    with wave.open(str(audio), 'wb') as wav:
        wav.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes(SAMPLE_COUNT * 2))
    width, height = SIZE
    for shader in shader_catalogue():
        project = {
            'schema': 'framerudiments.project.v1',
            'purpose': 'visualiser', 'width': width, 'height': height, 'fps': 60,
            'audio': {'source': ref(audio), 'sample_rate': SAMPLE_RATE, 'end_sample': SAMPLE_COUNT},
            'clock': {'bpm': 120, 'authority': 'synthetic demo', 'status': 'synthetic'},
            'background': {'image': ref(a)},
            'layers': [{'id': 'incoming', 'asset': ref(b), 'center': [width // 2, height // 2],
                        'width': width}],
            'render_quality': {'enabled': False},
            'effect_graph_backend': 'gpu',
            'effect_graph': {'nodes': [
                {'id': 'background', 'type': 'source'}, {'id': 'layer:incoming', 'type': 'source'},
                {'id': 'reveal', 'type': 'gl_transition', 'inputs': ['background', 'layer:incoming'],
                 'shader': shader, 'timing': {'start_beat': 1, 'duration_beats': 4}},
            ], 'output': 'reveal'},
        }
        write_json(directory / (shader['id'] + '.json'), project)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    prepare(parser.parse_args().directory)
