import io
import json
import time

import pytest

from framerudiments.progress import ProgressReporter


def read_progress(path):
    return json.loads((path / "render-progress.json").read_text(encoding="utf-8"))


def test_heartbeat_updates_while_one_frame_remains_active(tmp_path):
    stream = io.StringIO()
    reporter = ProgressReporter(
        tmp_path,
        4,
        mode="single-pass",
        interval_seconds=0.1,
        stream=stream,
    ).start("rendering frames")
    reporter.active(0)
    first = read_progress(tmp_path)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        current = read_progress(tmp_path)
        if current["updated_at"] != first["updated_at"]:
            break
        time.sleep(0.02)
    else:
        pytest.fail("progress heartbeat did not update while the active frame was unchanged")
    assert current["state"] == "running"
    assert current["active_frame"] == 0
    assert current["completed_frames"] == 0
    assert stream.getvalue().count("FrameRudiments progress:") >= 2
    reporter.advance(1)
    reporter.finish()
    final = read_progress(tmp_path)
    assert final["state"] == "completed"
    assert final["completed_frames"] == 4
    assert final["percent"] == 100
    assert final["active_frame"] is None


def test_progress_failure_is_durable(tmp_path):
    reporter = ProgressReporter(
        tmp_path,
        9,
        mode="chunked-sequential",
        interval_seconds=10,
        stream=io.StringIO(),
    ).start("rendering chunks", finalized_frames=3)
    reporter.active(4)
    reporter.advance(4)
    reporter.fail(RuntimeError("synthetic failure"), phase="rendering chunks", frame=4)
    value = read_progress(tmp_path)
    assert value["schema"] == "framerudiments.render-progress.v1"
    assert value["state"] == "failed"
    assert value["mode"] == "chunked-sequential"
    assert value["completed_frames"] == 4
    assert value["finalized_frames"] == 3
    assert value["active_frame"] == 4
    assert value["error"] == "synthetic failure"


@pytest.mark.parametrize("interval", [0, 0.09, 3600.1, "10", None, True])
def test_progress_interval_is_bounded(tmp_path, interval):
    with pytest.raises(ValueError, match="Progress interval"):
        ProgressReporter(tmp_path, 1, mode="preflight", interval_seconds=interval)
