"""Real S6 native worker entrypoint: unavailable engines and stale admission.

These tests spawn the actual production worker, construct real pinned native
projects and decode synthetic audio. No browser, native GPU or engine double is
substituted. Missing optional engines must return explicit diagnostics and
retire their complete owned worker scope.
"""
import json
import multiprocessing

import pytest

from framerudiments.preview_refinement_process import RefinementSpec
from framerudiments.preview_replay import StatefulSeekUnavailable
from framerudiments.preview_stateful_process import SpawnStatefulRenderer
from test_preview_native_project import fixture


@pytest.mark.parametrize('engine', ['butterchurn', 'projectm'])
def test_actual_native_worker_reports_missing_engine_and_retires_scope(tmp_path, monkeypatch, engine):
    for name in ('FRAMERUDIMENTS_BROWSER', 'FRAMERUDIMENTS_BUTTERCHURN_RUNTIME',
                 'FRAMERUDIMENTS_PROJECTM_LIBRARY', 'FRAMERUDIMENTS_PROJECTM_DLL_DIRS',
                 'FRAMERUDIMENTS_TEXTURES_DIR'):
        monkeypatch.delenv(name, raising=False)
    path, data = fixture(tmp_path)
    data['effects'] = [dict(id='feedback', engine=engine, preset='feedback', seed=7)]
    path.write_text(json.dumps(data), encoding='utf-8')
    spec = RefinementSpec.from_snapshot(path, data, data, {}, tmp_path/'workers')
    renderer = SpawnStatefulRenderer(spec, timeout_seconds=15)
    try:
        with pytest.raises(StatefulSeekUnavailable) as failure:
            renderer(0)
        assert failure.value.reason_code == 'native_runtime_unavailable'
        assert renderer.last_pid is not None  # The real child/handshake ran.
        assert renderer.stateful_status['phase'] == 'unavailable'
        assert renderer.stateful_status['production_equivalent'] is False
        assert renderer.process is None and renderer.scope is None
        assert renderer.connection is None and renderer.directory is None
        assert not renderer.cleanup_error
        assert renderer.last_pid not in {child.pid for child in multiprocessing.active_children()}
        assert not list((tmp_path/'workers').iterdir())
    finally:
        renderer.close()


def test_actual_native_worker_rejects_changed_original_before_native_setup(tmp_path):
    path, data = fixture(tmp_path)
    data['effects'] = [dict(id='feedback', engine='butterchurn', preset='feedback', seed=7)]
    path.write_text(json.dumps(data), encoding='utf-8')
    spec = RefinementSpec.from_snapshot(path, data, data, {}, tmp_path/'workers')
    changed = {**data, 'background': {'colour': '#abcdef'}}
    path.write_text(json.dumps(changed), encoding='utf-8')
    renderer = SpawnStatefulRenderer(spec, timeout_seconds=15)
    try:
        with pytest.raises(StatefulSeekUnavailable) as failure:
            renderer(0)
        assert failure.value.reason_code == 'input_identity_changed'
        assert renderer.last_pid is not None
        first_pid = renderer.last_pid
        with pytest.raises(StatefulSeekUnavailable) as failure:
            renderer(1)
        assert failure.value.reason_code == 'input_identity_changed'
        assert renderer.last_pid == first_pid  # No silent reset on a stale admission.
        assert renderer.process is None and not renderer.cleanup_error
        assert not list((tmp_path/'workers').iterdir())
    finally:
        renderer.close()
