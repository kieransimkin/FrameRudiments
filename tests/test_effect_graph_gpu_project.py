"""End-to-end project backend-selection smoke; requires installed test font."""
import json
import os
from pathlib import Path

import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def demo(tmp_path):
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        for candidate in ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
            if Path(candidate).is_file():
                font = candidate
                break
    if not font:
        pytest.skip('Installed TTF test font is required')
    return create_demo(tmp_path/'effect-graph-gpu', font, width=180, height=320)


def test_project_cpu_backend_and_render_evidence(demo):
    data = json.loads(demo.read_text(encoding='utf-8'))
    data['effect_graph'] = {'nodes':[{'id':'frame','type':'source'}], 'output':'frame'}
    data['effect_graph_backend'] = 'cpu'
    data['render_quality'] = {'temporal_samples':1,'spatial_scale':1,'antialias':0}
    project = Project(data, demo.parent)
    compositor = Compositor(project)
    try:
        image, _ = compositor.frame(15)
        assert image.mode == 'RGB'
        assert image.size == (180,320)
        info = compositor.evidence()['effect_graph_backend']
        assert info['requested'] == 'cpu'
        assert info['actual'] == 'cpu'
    finally:
        compositor.close()


@pytest.mark.parametrize('bad', ['none', True, 4, {'name':'gpu'}])
def test_project_invalid_backend_fails_closed(demo,bad):
    data = json.loads(demo.read_text(encoding='utf-8'))
    data['effect_graph'] = {'nodes':[{'id':'frame','type':'source'}], 'output':'frame'}
    data['effect_graph_backend'] = bad
    with pytest.raises(ValueError,match='effect_graph_backend'):
        Project(data,demo.parent)


def test_project_backend_without_graph_rejected(demo):
    data = json.loads(demo.read_text(encoding='utf-8'))
    data['effect_graph_backend'] = 'auto'
    with pytest.raises(ValueError,match='requires effect_graph'):
        Project(data,demo.parent)
