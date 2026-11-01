"""Actual Project/Compositor/DanceRudiment acceptance, never a substitute driver."""
from copy import deepcopy
import os
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.effect_graph_raster import RASTER_TYPES


@pytest.fixture
def project(tmp_path):
    supplied = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    candidates = [Path(supplied)] if supplied else [
        Path('C:/Windows/Fonts/arial.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    ]
    font = next((p for p in candidates if p.is_file()), None)
    if font is None:
        if supplied:
            pytest.fail('FRAMERUDIMENTS_TEST_FONT must name an existing font')
        pytest.skip('Installed TTF font required; CI and installer must supply it')
    file = create_demo(tmp_path / 'raster-demo', str(font), width=180, height=320)
    result = Project.load(file)
    data = deepcopy(result.data)
    data['render_quality'] = {'enabled': False}
    return Project(data, result.base)


def scene(image, context):
    y, x = np.indices((image.height, image.width))
    a = np.stack([(x * 31 + y * 13) % 256, (x * 7 + y * 43) % 256,
                  (x * 3 + y * 17 + 127) % 256, np.full(x.shape, 211)], axis=-1).astype(np.uint8)
    a[30:90, 30:90, 3] = 0
    return Image.fromarray(a)


def configured(project, kind, controls):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'scene', 'type': 'source'},
        {'id': 'r', 'type': 'raster_effect', 'inputs': ['scene'], 'raster_type': kind},
    ], 'output': 'r'}
    data['effect_graph_controls'] = controls
    return Project(data, project.base)


@pytest.mark.parametrize('kind', sorted(RASTER_TYPES))
def test_native_modulation_changes_pixels_and_is_seekable(project, kind):
    controls = [dict(id='raster-beat', node='r', property='raster_mix',
                     motion={'kind': 'named', 'pattern': 'circle'}, axis='x', range=[0, 1])]
    p = configured(project, kind, controls)
    saved = deepcopy(p.data)
    comp = Compositor(p, scene=scene)
    try:
        frames = (0, 7, 11, 19, 37, 73)
        values = [comp.effect_graph_controls.values(comp.context(f), comp.effect_graph)[('r', 'raster_mix')] for f in frames]
        assert max(values) - min(values) > .01
        images = {f: comp.frame(f, apply_quality=False)[0].tobytes() for f in frames}
        assert len(set(images.values())) > 1
        for f in reversed(frames):
            assert comp.frame(f, apply_quality=False)[0].tobytes() == images[f]
        assert p.data == saved
    finally:
        comp.close()


def test_native_categorical_dither_binding(project):
    controls = [dict(node='r', property='dither_pattern',
                     motion={'kind': 'named', 'pattern': 'circle'}, axis='x',
                     choices=['none', 'bayer8', 'noise'])]
    comp = Compositor(configured(project, 'dither', controls), scene=scene)
    try:
        frames = (0, 7, 11, 19, 37, 73)
        choices = [comp.effect_graph_controls.values(comp.context(f), comp.effect_graph)[('r', 'dither_pattern')] for f in frames]
        assert len(set(choices)) > 1
        assert all(comp.frame(f, apply_quality=False)[0].size == (180, 320) for f in frames)
    finally:
        comp.close()


def test_native_chain_preprocessing_and_descriptor_addresses(project):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'scene', 'type': 'source'},
        {'id': 'chain', 'type': 'transition_chain', 'inputs': ['scene', 'scene'],
         'input_effects': [[{'id': 'pixels', 'type': 'raster_effect', 'raster_type': 'pixelate'}], []],
         'stages': [{'id': 'fade', 'progress': 0}]},
    ], 'output': 'chain'}
    target = 'chain::input::0::pixels'
    data['effect_graph_controls'] = [dict(node=target, property='pixel_size.x',
        motion={'kind': 'named', 'pattern': 'circle'}, axis='x', range=[2, 20])]
    comp = Compositor(Project(data, project.base), scene=scene)
    try:
        assert comp.effect_graph.control_descriptors(target)['pixel_size.x']['animatable']
        frames = (0, 7, 11, 19, 37, 73)
        images = [comp.frame(f, apply_quality=False)[0].tobytes() for f in frames]
        assert len(set(images)) > 1
    finally:
        comp.close()
