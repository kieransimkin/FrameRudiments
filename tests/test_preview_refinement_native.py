"""S4 native spawn/Project/Compositor acceptance in the complete CPU CI checkout.

No test-double renderer, network assets, GPU or skips. Separately exercising the
child's native entrypoint prevents synthetic spawn tests from claiming parity.
"""
from hashlib import sha256
from io import BytesIO
import json

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.preview_refinement_process import (
    RefinementSpec, RefinementWorkerError, SpawnQ5Renderer,
)
from framerudiments.project import Project
from test_preview_native_project import fixture


@pytest.mark.parametrize('graph', [False, True])
def test_spawned_native_q5_matches_authored_compositor_at_arbitrary_frames(tmp_path, graph):
    path, data = fixture(tmp_path, graph=graph)
    before = {name: (tmp_path/name).read_bytes() for name in ('project.json', 'source.wav', 'source.png')}
    snapshot = RefinementSpec.from_snapshot(path, data, data, {}, tmp_path/'refinement')
    renderer = SpawnQ5Renderer(snapshot)
    reference = Compositor(Project.load(path))
    try:
        pid = None
        for frame in (31, 1, 119, 61):
            with Image.open(BytesIO(renderer(frame, lambda: False))) as decoded:
                decoded.load()
                expected, _ = reference.frame(frame, apply_quality=True)
                assert decoded.mode == 'RGBA' and decoded.size == (64, 48)
                assert decoded.tobytes() == expected.convert('RGBA').tobytes()
            if pid is None:
                pid = renderer.last_pid
            assert renderer.last_pid == pid
    finally:
        renderer.close()
        reference.close()
    assert {name: (tmp_path/name).read_bytes() for name in before} == before
    assert not list((tmp_path/'refinement').iterdir())


def test_spawned_native_refinement_preserves_pinned_sidecars_and_rejects_edits(tmp_path):
    path, data = fixture(tmp_path)
    cue = tmp_path/'cues.lrc'
    cue.write_text('[00:00.250][SECTION: ORIGINAL]\n', encoding='utf-8')
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    path.write_text(json.dumps(data), encoding='utf-8')
    before = cue.read_bytes()
    snapshot = RefinementSpec.from_snapshot(path, data, data,
        {'cues': [{'start': .5, 'label': '[SECTION: DRAFT]'}]}, tmp_path/'refinement')
    renderer = SpawnQ5Renderer(snapshot)
    try:
        assert renderer(31, lambda: False).startswith(b'\x89PNG')
        assert cue.read_bytes() == before
        native_files = list((tmp_path/'refinement').glob('refinement-*/native/session-*/cues.lrc'))
        assert len(native_files) == 1
        assert native_files[0].read_text(encoding='utf-8') == '[00:00.500][SECTION: DRAFT]\n'
        # A new on-disk project revision must never be rendered under the old ticket.
        changed = dict(data, background={'colour': '#ff0000'})
        path.write_text(json.dumps(changed), encoding='utf-8')
        with pytest.raises(RefinementWorkerError):
            renderer(32, lambda: False)
        assert renderer.process is None
    finally:
        renderer.close()
    assert cue.read_bytes() == before
    assert not list((tmp_path/'refinement').iterdir())
