from types import SimpleNamespace

import pytest

from framerudiments.encoding import X264_PRESETS, command


def project():
    return SimpleNamespace(
        width=1920,
        height=1080,
        fps=60,
        audio_ref={"path": "source.wav"},
        start_sample=0,
        end_sample=44100,
        sample_rate=44100,
        repeats=1,
        fade=0,
        duration=1,
    )


@pytest.mark.parametrize("preset", X264_PRESETS)
def test_command_accepts_every_documented_x264_preset(preset):
    args = command(project(), "candidate.mp4", "ffmpeg", preset=preset, crf=12)
    index = args.index("-preset")
    assert args[index + 1] == preset
    assert args[args.index("-crf") + 1] == "12"


def test_command_rejects_unknown_encoder_preset():
    with pytest.raises(ValueError, match="Unsupported encoder preset"):
        command(project(), "candidate.mp4", "ffmpeg", preset="maximum", crf=12)
