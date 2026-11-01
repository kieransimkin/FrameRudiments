"""M6.5 full-package acceptance: actual compositor, providers and native motion.

No native Motion replacements. Run with the installed-font CI environment.
These are deliberately separate from the pure graph-surface contract tests.
"""
from copy import deepcopy
import json
import os
from pathlib import Path

from PIL import ImageDraw
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def project(tmp_path):
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        font = next((str(path) for path in (Path('C:/Windows/Fonts/arial.ttf'),
                     Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')) if path.is_file()), None)
    if not font or not Path(font).is_file():
        pytest.skip('An installed TTF is required for native style acceptance')
    original = Project.load(create_demo(tmp_path / 'contract', font, width=180, height=320))
    data = deepcopy(original.data)
    data['render_quality'] = {'enabled': False}
    return Project(data, original.base)


def configured(project, *, source='layer:native', style='outline', controls=()):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': source, 'type': 'source'},
        {'id': 'styled', 'type': 'layer_style', 'inputs': [source], 'style_type': style,
         'style_color': '#00ddff', 'style_width': 2},
    ], 'output': 'styled'}
    data['effect_graph_controls'] = list(controls)
    return Project(data, project.base)


@pytest.mark.parametrize('source', ['layer:native', 'layer:bands', 'identity:0', 'caption'])
@pytest.mark.parametrize('style', ['outline', 'bevel', 'gradient_overlay'])
def test_real_sprite_recording_bars_and_text_share_contract(project, source, style):
    authored = configured(project, source=source, style=style)
    saved = deepcopy(authored.data)
    comp = Compositor(authored)
    try:
        image = comp.frame(45, apply_quality=False)[0]
        graph = comp.effect_graph
        report = graph.style_render_report()
        assert report['sample_context']['frame'] == 45
        assert report['nodes']['styled']['inputs'] == [source]
        assert report['nodes']['styled']['bounds']['actual_output_alpha'] is not None
        assert graph.style_manifest()['nodes']['styled']['capabilities']['style_type'] == style
        json.dumps(report, allow_nan=False)
        comp.frame(12, apply_quality=False)
        assert comp.frame(45, apply_quality=False)[0].tobytes() == image.tobytes()
        assert graph.style_render_report() == report
        assert authored.data == saved
    finally:
        comp.close()


def test_public_scene_hook_capture_uses_same_style_contract(project):
    def scene(image, ctx):
        ImageDraw.Draw(image).polygon([(5, 8), (60, 25), (20, 55)], fill=(210, 80, 20, 160))
        return image
    comp = Compositor(configured(project, source='scene', style='extrusion'), scene=scene)
    try:
        comp.frame(8, apply_quality=False)
        bounds = comp.effect_graph.style_render_report()['nodes']['styled']['bounds']
        assert bounds['input_alpha'] == [5, 8, 61, 56]
        assert bounds['actual_output_alpha'] is not None
    finally:
        comp.close()


def test_actual_native_style_values_are_reported_after_modulation(project):
    controls = [dict(node='styled', property='style_width', range=[1, 7],
                     motion={'kind': 'named', 'pattern': 'circle'}, axis='x')]
    comp = Compositor(configured(project, controls=controls))
    try:
        values = []
        for frame in (0, 1, 4, 9, 13, 22, 37, 53):
            expected = comp.effect_graph_controls.values(comp.context(frame), comp.effect_graph)
            comp.frame(frame, apply_quality=False)
            report = comp.effect_graph.style_render_report()['nodes']['styled']
            assert report['resolved_controls']['style_width'] == expected[('styled', 'style_width')]
            values.append(report['resolved_controls']['style_width'])
        assert max(values) > min(values)
        assert comp.effect_graph.nodes['styled'].config['style_width'] == 2
    finally:
        comp.close()


@pytest.mark.parametrize('property_name,interval', [
    ('gradient_angle', [12000, 24000]), ('pattern_offset.x', [-65536, 65536]),
])
def test_extended_range_uses_real_native_driver_and_remains_seekable(project, property_name, interval):
    style = 'gradient_overlay' if property_name == 'gradient_angle' else 'pattern_overlay'
    controls = [dict(node='styled', property=property_name, range=interval,
                     motion={'kind': 'named', 'pattern': 'circle'}, axis='x')]
    comp = Compositor(configured(project, style=style, controls=controls))
    try:
        values = []
        for frame in (0, 1, 4, 9, 13, 22, 37, 53):
            resolved = comp.effect_graph_controls.values(comp.context(frame), comp.effect_graph)
            value = resolved[('styled', property_name)]
            assert interval[0] <= value <= interval[1]
            values.append(value)
        assert max(values) > min(values)
        assert comp.effect_graph_controls.range_evidence[0]['native_range'] == [0, 1]
        image = comp.frame(45, apply_quality=False)[0]
        comp.frame(12, apply_quality=False)
        assert comp.frame(45, apply_quality=False)[0].tobytes() == image.tobytes()
    finally:
        comp.close()


def test_composited_group_and_chain_preprocessing_produce_separate_reports(project):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'layer:native', 'type': 'source'}, {'id': 'identity:0', 'type': 'source'},
        {'id': 'background', 'type': 'source'},
        {'id': 'group', 'type': 'composite', 'inputs': ['layer:native', 'identity:0']},
        {'id': 'styled-group', 'type': 'layer_style', 'inputs': ['group'], 'style_type': 'outline'},
        {'id': 'chain', 'type': 'transition_chain', 'inputs': ['styled-group', 'background'],
         'input_effects': [[], [{'id': 'paint', 'type': 'layer_style', 'style_type': 'gradient_overlay'}]],
         'stages': [{'id': 'reveal', 'progress': .5}]},
    ], 'output': 'chain'}
    comp = Compositor(Project(data, project.base))
    try:
        comp.frame(45, apply_quality=False)
        manifest = comp.effect_graph.style_manifest()
        report = comp.effect_graph.style_render_report()
        assert set(manifest['nodes']) == set(report['nodes'])
        assert len(report['nodes']) == 2
        assert report['nodes']['styled-group']['inputs'] == ['group']
        assert report['final_graph_alpha'] == [0, 0, 180, 320]
    finally:
        comp.close()


def test_real_temporal_supersampling_exposes_last_graph_sample_honestly(project):
    from framerudiments.effect_graph_integration import compositor_style_report
    data = deepcopy(configured(project).data)
    data['render_quality'] = {'enabled': True, 'spatial_scale': 1, 'temporal_samples': 2, 'antialias': 0}
    comp = Compositor(Project(data, project.base))
    try:
        comp.frame(10, apply_quality=False)
        report = compositor_style_report(comp)
        assert report['sample_context']['frame'] == 21
        assert report['sampling']['sample_fps'] == 120
        assert report['sampling']['encoded_fps'] == 60
        assert not report['sampling']['complete_shutter_bounds']
    finally:
        virtual = getattr(comp, '_temporal_virtual', None)
        if virtual is not None:
            virtual.close()
        comp.close()


def test_real_project_style_report_export_is_readable_and_refuses_existing_output(project, tmp_path):
    import importlib.util
    from framerudiments.io import write_json
    script = Path(__file__).resolve().parents[1] / 'integration' / 'report_effect_graph_styles.py'
    spec = importlib.util.spec_from_file_location('project_style_report', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    authored = configured(project)
    # Preserve relative hash-pinned input paths by writing beside the generated project.
    path = project.base / 'style-report-project.json'
    write_json(path, authored.data)
    output = tmp_path / 'actual-style-report'
    evidence = module.export_report(path, output, 45)
    assert evidence['sample_report']['nodes']['styled']['bounds']['actual_output_alpha']
    assert evidence['encoded_frame'] == 45
    assert json.loads((output / 'styles.json').read_text(encoding='utf-8')) == evidence
    assert (output / 'frame.png').is_file()
    with pytest.raises(FileExistsError):
        module.export_report(path, output, 45)
