"""Native S7 evidence must be honest, reusable, private and safely cleaned."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from time import sleep

import pytest

from framerudiments.preview_stream_contract import canonical_digest
from framerudiments import preview_stream_profile as profile


def test_actual_controller_cost_matrix_is_labelled_and_respects_hysteresis():
    matrix = profile.controller_matrix()
    assert not matrix['host_render_performance']
    assert matrix['measurement'] == 'injected_costs_and_virtual_controller_clock'
    assert len(matrix['rows']) == 16
    assert {(row['target_fps'], row['injected_render_ms']) for row in matrix['rows']} == {
        (target, cost) for target in (24, 30, 60, 120) for cost in (5, 12, 30, 70)}
    for row in matrix['rows']:
        pressure = [event for event in row['transitions'] if event['phase'] == 'pressure']
        recovery = [event for event in row['transitions'] if event['phase'] == 'recovery']
        assert bool(pressure) == row['expected_pressure']
        assert bool(recovery) == row['expected_pressure']
        for change in pressure:
            assert int(change['to_profile'][1]) == int(change['from_profile'][1])-1
        for change in recovery:
            assert int(change['to_profile'][1]) == int(change['from_profile'][1])+1


@pytest.mark.parametrize('slow_first_frame', [False, True])
def test_native_profile_exercises_stream_refinement_edit_quota_and_private_artifacts(
        tmp_path, monkeypatch, slow_first_frame):
    original_fixture = profile.fixture
    marker = 'PRIVATE_PROFILE_METADATA_CANARY'
    def private_metadata_fixture(*args, **kwargs):
        path, data = original_fixture(*args, **kwargs)
        data['title'] = marker
        path.write_text(json.dumps(data), encoding='utf-8')
        return path, data
    monkeypatch.setattr(profile, 'fixture', private_metadata_fixture)
    if slow_first_frame:
        original_render = profile.MeasuredNativeRenderer.render_cancellable
        def delayed_first_frame(renderer, *args, **kwargs):
            result = original_render(renderer, *args, **kwargs)
            if not getattr(renderer, '_test_first_frame_delayed', False):
                renderer._test_first_frame_delayed = True
                sleep(.3)
            return result
        monkeypatch.setattr(profile.MeasuredNativeRenderer, 'render_cancellable', delayed_first_frame)
    output = tmp_path/'qualification'
    report = profile.run_profile(output, preset='smoke', max_seconds=120)
    assert report['status'] == 'passed'
    assert report['cleanup']['owned_working_tree_removed']
    assert report['native']['native_contexts_retired']
    assert report['native']['source_inputs_unchanged']
    assert len(report['native']['q5_parity']) == 5
    assert all(row['exact_match'] and row['rgba8_tolerance'] == 0 for row in report['native']['q5_parity'])
    cases = {case['name']: case for case in report['native']['cases']}
    assert cases['native_seek_and_edit']['independent_q5_preserved']
    assert cases['native_seek_and_edit']['latest_frame_only_accepted']
    if slow_first_frame:
        baseline = cases['native_seek_and_edit']['before_edit']
        assert any(row['reason'] == 'catastrophic_service_time' for row in baseline['transitions'])
        assert baseline['counters']['background_completed'] > 0
    assert cases['injected_service_pressure_and_native_refinement']['anchor_q5_exact']
    assert cases['injected_service_pressure_and_native_refinement']['inflight_latest_frame_only_accepted']
    assert cases['injected_service_pressure_and_native_refinement']['obsolete_native_results_rejected'] > 0
    assert cases['injected_slow_writes_and_tight_cache']['evictions_observed'] > 0
    assert cases['injected_allocation_pressure']['recovered_profile'] == 'Q4'
    for case in cases.values():
        receipt = case.get('receipt', case.get('after_edit'))
        assert receipt['schema'] == 'framerudiments.preview-stream-qualification.v1'
        assert receipt['rates']['acknowledgement_source'] == 'harness_png_decode'
        assert case['gpu'] == []
    saved = json.loads((output/'qualification.json').read_text(encoding='utf-8'))
    assert saved == report
    unsigned = deepcopy(saved)
    digest = unsigned.pop('receipt_sha256')
    assert digest == canonical_digest(unsigned)
    evidence = (output/'qualification.json').read_text()+(output/'index.html').read_text()
    assert marker not in evidence and str(tmp_path) not in evidence
    assert 'data:image/png;base64,' in evidence
    assert 'browser paint or physical-display rate' in evidence
    assert {path.name for path in output.iterdir()} == {'qualification.json', 'index.html'}
    assert not any(thread.name.startswith('framerudiments-') and thread.is_alive()
                   for thread in threading.enumerate())


def test_output_reuse_cannot_overwrite_existing_evidence(tmp_path):
    output = tmp_path/'existing'
    output.mkdir()
    original = output/'qualification.json'
    original.write_bytes(b'preserve caller bytes')
    with pytest.raises(ValueError, match='new directory'):
        profile.run_profile(output, preset='smoke')
    assert original.read_bytes() == b'preserve caller bytes'


def test_failed_native_start_records_safe_failure_and_retires_actual_worker(tmp_path, monkeypatch):
    marker = 'sensitive-error-path-and-title'
    def fail(*_args, **_kwargs):
        raise RuntimeError(marker)
    monkeypatch.setattr(profile.NativeRun, 'receive', fail)
    output = tmp_path/'failed'
    with pytest.raises(profile.ProfileFailure, match='qualification_failed'):
        profile.run_profile(output, preset='smoke')
    raw = (output/'qualification.json').read_text(encoding='utf-8')
    receipt = json.loads(raw)
    assert receipt['status'] == 'failed'
    assert receipt['failure'] == {'code': 'qualification_failed', 'error_type': 'RuntimeError'}
    assert receipt['cleanup']['owned_working_tree_removed']
    assert marker not in raw and str(tmp_path) not in raw
    assert not (output/'index.html').exists()
    assert list(output.iterdir()) == [output/'qualification.json']
    assert not any(thread.name.startswith('framerudiments-') and thread.is_alive()
                   for thread in threading.enumerate())


def test_wrong_revision_is_rejected_before_output_is_created(tmp_path):
    with pytest.raises(ValueError, match='source revision'):
        profile.run_profile(tmp_path/'new', revision='0'*40, preset='smoke')
    assert not (tmp_path/'new').exists()


def test_report_rejects_private_or_unbounded_optional_version_metadata(monkeypatch):
    marker = 'PRIVATE_PACKAGE_PATH/metadata'
    monkeypatch.setattr(profile.metadata, 'version', lambda _name: marker)
    identity = profile.source_identity()
    assert identity['versions']['numpy'] is None
    assert identity['versions']['moderngl'] is None
    assert marker not in json.dumps(identity)


@pytest.mark.parametrize('settings', [dict(preset='unbounded'), dict(size='huge'),
    dict(backend='auto'), dict(max_seconds=float('nan')), dict(max_seconds=1801)])
def test_unbounded_or_ambiguous_qualification_modes_are_rejected(tmp_path, settings):
    with pytest.raises(ValueError):
        profile.run_profile(tmp_path/'output', **settings)
    assert not (tmp_path/'output').exists()


def test_fixture_validator_preserves_the_native_source_clock(tmp_path):
    _path, data = profile.fixture(tmp_path/'fixture', (160, 90), 'cpu')
    accepted = deepcopy(data)
    accepted['layers'][0]['opacity'] = .2
    profile._validate_fixture(data, accepted)
    for field, value in [('fps', 30), ('audio', {**data['audio'], 'repeats': 1})]:
        altered = deepcopy(accepted)
        altered[field] = value
        with pytest.raises(ValueError, match='declared opacity'):
            profile._validate_fixture(data, altered)
    assert not any(path.is_symlink() for path in Path(tmp_path/'fixture').iterdir())
