"""M8.2 native Project/Compositor integration; no stub DanceRudiment driver."""
from copy import deepcopy
import os
from pathlib import Path

from PIL import Image
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.effect_graph_surface_transport import (
    TransportContract, pack_image, unpack_image,
)


@pytest.fixture
def project(tmp_path):
    preferred = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    fonts = ([Path(preferred)] if preferred else [Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                                               Path('C:/Windows/Fonts/arial.ttf')])
    font = next((p for p in fonts if p.is_file()), None)
    if font is None:
        if preferred:
            pytest.fail('FRAMERUDIMENTS_TEST_FONT does not exist')
        pytest.skip('Native font required; installed by CI/PowerShell test runner')
    path = create_demo(tmp_path / 'transport-demo', str(font), width=144, height=240)
    original = Project.load(path)
    data = deepcopy(original.data)
    data['render_quality'] = {'enabled': False}
    data['effect_graph'] = {'nodes': [
        {'id': 'frame', 'type': 'source'},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['frame'],
         'key_color': '#10ff00', 'tolerance': .1, 'softness': .1},
    ], 'output': 'key'}
    data['effect_graph_controls'] = [dict(
        node='key', property='tolerance',
        motion=dict(kind='named', pattern='circle'), axis='x', range=[.01, .35])]
    return Project(data, original.base)


@pytest.mark.parametrize('frame', [0, 7, 19, 37])
def test_native_compositor_preserves_output_with_transport_preflight(project, frame):
    authored = deepcopy(project.data)
    comp = Compositor(project)
    try:
        before = comp.frame(frame, apply_quality=False)[0].tobytes()
        ctx = comp.context(frame)
        values = comp.effect_graph_controls.values(ctx, comp.effect_graph)
        plan = comp.effect_graph.backend_plan({'frame': (project.width, project.height)},
                                              parameter_values=values, context=ctx)
        assert plan['eligible']
        assert plan['nodes']['key']['transport']['pixel_format'] == 'rgba8'
        assert plan['nodes']['key']['transport']['alpha'] == 'straight'
        # The host-level transport is a snapshot, never a compositor mutation.
        pixels = Image.new('RGBA', (project.width, project.height), (17, 38, 49, 127))
        packed = pack_image(pixels, TransportContract(project.width, project.height,
                                row_order='bottom-first', alignment=8))
        assert unpack_image(packed).tobytes() == pixels.tobytes()
        assert comp.frame(frame, apply_quality=False)[0].tobytes() == before
        assert project.data == authored
    finally:
        comp.close()


def test_native_temporary_supersampling_uses_no_new_clock_or_conversion(project):
    data = deepcopy(project.data)
    data['render_quality'] = dict(enabled=True, spatial_scale=1, temporal_samples=2)
    comp = Compositor(Project(data, project.base))
    try:
        first = comp.frame(7, apply_quality=False)[0].tobytes()
        virtual = comp._temporal_virtual
        assert virtual is not None
        ctx = virtual.context(14)
        values = virtual.effect_graph_controls.values(ctx, virtual.effect_graph)
        plan = virtual.effect_graph.backend_plan({'frame': (project.width, project.height)},
                                                  parameter_values=values, context=ctx)
        assert plan['registry']['surface_transport']['gpu_conversion_shaders'] is False
        assert comp.frame(7, apply_quality=False)[0].tobytes() == first
    finally:
        comp.close()
