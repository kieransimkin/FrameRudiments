"""Real Project/Compositor/DanceRudiments acceptance; no substitute motion driver."""
from copy import deepcopy
import os
from pathlib import Path

from PIL import ImageDraw
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.effect_graph_spatial import GPU_WARP_TYPES


@pytest.fixture
def project(tmp_path):
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        font = next((str(p) for p in (Path('C:/Windows/Fonts/arial.ttf'),
                     Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')) if p.is_file()), None)
    if not font:
        pytest.skip('An installed TTF font is required for actual project acceptance')
    path = create_demo(tmp_path / 'spatial-project', font, width=180, height=320)
    loaded = Project.load(path)
    data = deepcopy(loaded.data)
    data['render_quality'] = {'enabled': False}
    return Project(data, loaded.base)


def scene(image, context):
    # Fixed release-owned scene isolates control movement from source animation.
    draw = ImageDraw.Draw(image)
    for y in range(16, 300, 24):
        draw.rectangle((20, y, 152, y + 12), fill=(240, (y * 7) % 255, 80, 180))
    draw.ellipse((45, 45, 130, 140), fill=(0, 220, 255, 255))
    draw.rectangle((65, 65, 98, 99), fill=(0, 0, 0, 0))
    return image


CASES = [
    ('wave', 'warp_phase', [0, 3]), ('turbulence', 'warp_phase', [0, 3]),
    ('swirl', 'warp_angle', [0, 240]), ('radial', 'warp_amount', [-1, 1]),
    ('mirror', 'warp_center.x', [.3, .7]), ('kaleidoscope', 'warp_angle', [0, 120]),
    ('displacement', 'warp_amplitude.x', [0, 20]),
    ('polar_unwrap', 'warp_angle', [0, 180]), ('polar_wrap', 'warp_angle', [0, 180]),
]


@pytest.mark.parametrize('kind,prop,interval', CASES)
def test_real_native_controls_change_frames_and_reproduce_seeks(project, kind, prop, interval):
    data = deepcopy(project.data)
    inputs = ['scene', 'background'] if kind == 'displacement' else ['scene']
    data['effect_graph'] = {'nodes': [
        {'id': 'scene', 'type': 'source'}, {'id': 'background', 'type': 'source'},
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': inputs, 'warp_type': kind,
         'warp_amplitude': [12, 8], 'warp_scale': [32, 40], 'warp_radius': 90},
    ], 'output': 'warp'}
    data['effect_graph_controls'] = [{
        'id': 'move', 'node': 'warp', 'property': prop,
        'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': interval,
    }]
    authored = deepcopy(data)
    comp = Compositor(Project(data, project.base), scene=scene)
    try:
        # Deliberately sample sub-beat instants, not repeated zero crossings.
        frames = (0, 1, 3, 7, 11, 19, 23, 31)
        values = [comp.effect_graph_controls.values(comp.context(f), comp.effect_graph)[('warp', prop)]
                  for f in frames]
        assert max(values) - min(values) > 1e-6
        images = {f: comp.frame(f, apply_quality=False)[0].tobytes() for f in frames}
        assert len(set(images.values())) > 1
        for f in reversed(frames):
            assert comp.frame(f, apply_quality=False)[0].tobytes() == images[f]
        assert data == authored
        assert comp.effect_graph.spatial_manifest()['warp']['gpu_native'] == (kind in GPU_WARP_TYPES)
    finally:
        comp.close()


def test_chain_warp_and_downstream_original_alpha_style_in_real_compositor(project):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'scene', 'type': 'source'}, {'id': 'background', 'type': 'source'},
        {'id': 'chain', 'type': 'transition_chain', 'inputs': ['scene', 'background'],
         'input_effects': [[{'id': 'wave', 'type': 'spatial_warp', 'warp_amplitude': [8, 2]}], []],
         'stages': [{'id': 'fade', 'progress': 0}]},
        {'id': 'outline', 'type': 'layer_style', 'inputs': ['chain'], 'style_type': 'outline',
         'style_color': '#00ffff', 'style_width': 3, 'alpha_source': 'original'},
    ], 'output': 'outline'}
    comp = Compositor(Project(data, project.base), scene=scene)
    try:
        first = comp.frame(7, apply_quality=False)[0]
        comp.frame(17, apply_quality=False)
        assert first.tobytes() == comp.frame(7, apply_quality=False)[0].tobytes()
        assert first.getbbox() is not None
        assert 'chain::input::0::wave' in comp.effect_graph.spatial_manifest()
    finally:
        comp.close()
