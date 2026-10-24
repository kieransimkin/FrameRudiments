from types import SimpleNamespace

import pytest

from framerudiments.encoding import X264_PRESETS, command, mux_chunks_command, video_chunk_command


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


def test_chunk_command_is_video_only_and_forces_joinable_gops():
    args = video_chunk_command(project(), "chunk.mp4", "ffmpeg", preset="slow", crf=14)
    assert "-an" in args
    assert args[args.index("-g") + 1] == "120"
    assert args[args.index("-sc_threshold") + 1] == "0"


def test_chunk_assembly_copies_video_and_muxes_canonical_audio_once():
    args = mux_chunks_command(project(), "chunks.txt", "video.mp4", "ffmpeg")
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args.count("source.wav") == 1
