"""Driver contracts; no native renderer or downloaded neural model is required."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('preset_behaviour_pipeline', SCRIPTS/'preset_behaviour_pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def arguments():
    return SimpleNamespace(render_python=Path('render-python'), analysis_python=Path('analysis-python'),
        seconds=3, capture_fps=15, retry_failed=True, semantic_budget=24, semantic_edge=384,
        models=['clip','blip'],device='cpu')


def test_dense_frames_and_distinct_semantic_budget():
    points = pipeline.dense_intervals()
    assert len(points) == 46 and points[0] == 0 and points[-1] == 3
    assert len(points)*8 == 368
    assert max(b-a for a,b in zip(points,points[1:])) < .067


@pytest.mark.parametrize('seconds,fps', [(float('nan'),15),(-1,15),(60,60),(3,0),(3,15.5)])
def test_dense_capture_bounds(seconds,fps):
    with pytest.raises(ValueError):
        pipeline.dense_intervals(seconds,fps)


def test_capture_command_has_all_conditions_no_semantic_models(tmp_path):
    args = arguments()
    command = pipeline.capture_command(args,Path('suite'),Path('catalogue'),{'id':'butterchurn:fixture'},tmp_path)
    assert '--models' not in command and '--prism-python' not in command
    assert command.count('--image') == 4 and command.count('--audio-mode') == 2
    assert '--resume' not in command
    (tmp_path/'run').mkdir()
    (tmp_path/'run'/'experiment.json').write_text('{}')
    command = pipeline.capture_command(args,Path('suite'),Path('catalogue'),{'id':'butterchurn:fixture'},tmp_path)
    assert '--resume' in command and '--retry-failed' in command


def test_analysis_command_selects_two_models_and_budget(tmp_path):
    command = pipeline.analysis_command(arguments(),tmp_path/'captures.json',tmp_path/'report')
    assert command[command.index('--budget')+1] == '24'
    assert command[-2:] == ['clip','blip']
    assert '--allow-download' not in command and '--keyword-models' not in command


def test_public_report_filters_private_paths():
    value = {'checkpoint':'Z:/private/model', 'name':'preset',
             'items':[{'path':'Z:/private/image.png','sha256':'abc'}]}
    assert pipeline._public(value) == {'name':'preset','items':[{'sha256':'abc'}]}


def test_existing_capture_api_supports_dense_cadence():
    from framerudiments.preset_profiling import capture_frames
    assert len(capture_frames(pipeline.dense_intervals(),60,240)) == 46
    with pytest.raises(ValueError,match='512'):
        capture_frames([i/60 for i in range(513)],60,600)


def test_higher_resolution_synthetic_suite_reuses_source_clock(tmp_path):
    font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    import os
    font = Path(os.environ.get('FRAMERUDIMENTS_TEST_FONT', font))
    suite = pipeline.input_suite(tmp_path,font,render_edge=384)
    doc = pipeline.legacy.read_json(suite)
    project = pipeline.legacy.read_json(Path(doc['projects'][0]['path']))
    assert project['width'] == project['height'] == 384
    assert project['fps'] == 60 and project['audio']['end_sample'] == 48000*4
    assert pipeline.input_suite(tmp_path,font,render_edge=384) == suite


def test_local_runtime_bundle_noop_on_non_windows(tmp_path):
    import ensure_projectm_runtime
    assert ensure_projectm_runtime.ensure(tmp_path,platform='posix') == 0
