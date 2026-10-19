from pathlib import Path
import importlib

import pytest

renderer = importlib.import_module("framerudiments.render")


def test_many_checkpoints_preserved_in_bounded_expressions(tmp_path, monkeypatch):
    calls = []

    def fake(command, **kwargs):
        calls.append(command)
        start = int(command[command.index("-start_number") + 1])
        count = int(command[command.index("-frames:v") + 1])
        assert len(command[command.index("-vf") + 1].split("+")) <= 32
        for n in range(start, start + count):
            Path(command[-1].replace("%08d", f"{n:08}")).write_bytes(b"encoded-frame")

    monkeypatch.setattr(renderer.subprocess, "run", fake)
    selected = list(range(0, 732, 3))
    renderer.extract_encoded_checkpoints("ffmpeg", "input.mp4", tmp_path, selected)
    assert len(calls) == 8
    assert len(list(tmp_path.glob("encoded-*.png"))) == 244
    assert (tmp_path / "encoded-00000729.png").exists()
    assert not list(tmp_path.glob("checkpoint-*.png"))


@pytest.mark.parametrize("size", [0, 33, 1.5, True])
def test_bad_batch_sizes_fail(size, tmp_path):
    with pytest.raises(ValueError):
        renderer.extract_encoded_checkpoints("ffmpeg", "v", tmp_path, [0], batch_size=size)
