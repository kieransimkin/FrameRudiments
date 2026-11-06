"""Real B-frame regressions for sub-microsecond concat duration rounding."""
from fractions import Fraction
import os
import subprocess
from types import SimpleNamespace
import wave

import pytest

from framerudiments import encoding


def decoded_hashes(ffmpeg, path):
    output = subprocess.check_output(
        [ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
         "-f", "framemd5", "-"], text=True,
    )
    return [line.rsplit(",", 1)[1].strip() for line in output.splitlines()
            if line and not line.startswith("#")]


@pytest.mark.parametrize("fps", [24, 30, 60])
def test_fractional_chunk_durations_keep_exact_cfr_and_all_b_frames(tmp_path, fps):
    if not os.environ.get("FRAMERUDIMENTS_FFMPEG"):
        pytest.skip("FFmpeg integration: set FRAMERUDIMENTS_FFMPEG")
    ffmpeg, ffprobe = encoding.binaries()
    count, number = 11, 13
    total = count * number
    samples = total * 48000 // fps
    audio = tmp_path / "synthetic.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0" * samples * 4)
    project = SimpleNamespace(width=64, height=36, fps=fps, frame_count=total,
                              audio_ref={"path": str(audio)}, start_sample=0,
                              end_sample=samples, sample_rate=48000, repeats=1,
                              fade=0, duration=Fraction(total, fps))
    paths, expected = [], []
    for index in range(number):
        path = tmp_path / f"chunk-{index:02d}.mp4"
        # Changing pixels exercise reordered H.264 frames, rather than a still.
        pixels = b"".join(bytes(((frame * 11 + index * 17) % 256, frame * 7 % 256, index * 19 % 256))
                          * (64 * 36) for frame in range(count))
        subprocess.run(encoding.video_chunk_command(project, path, ffmpeg, preset="fast", crf=20),
                       input=pixels, check=True, capture_output=True)
        paths.append(path)
        expected.extend(decoded_hashes(ffmpeg, path))
    first = encoding.probe(paths[0], ffprobe)["streams"][0]
    assert first["has_b_frames"] > 0
    concat = tmp_path / "chunks.txt"
    concat.write_text("".join(f"file '{path.name}'\n" for path in paths), encoding="utf-8")
    output = tmp_path / "assembled.mp4"
    command = encoding.mux_chunks_command(project, concat, output, ffmpeg)
    subprocess.run(command, check=True, capture_output=True)
    verified = encoding.verify(output, project, ffmpeg, ffprobe)
    assert verified["full_decode"] == "passed"
    assert decoded_hashes(ffmpeg, output) == expected
    # Demonstrate the former failure at 60 fps with the same inputs and muxer.
    if fps == 60:
        legacy = tmp_path / "legacy.mp4"
        original = command.copy()
        position = original.index("-bsf:v")
        del original[position:position + 2]
        original[-1] = str(legacy)
        subprocess.run(original, check=True, capture_output=True)
        assert decoded_hashes(ffmpeg, legacy) == expected
        legacy_video = encoding.probe(legacy, ffprobe)["streams"][0]
        # Muxer versions differ in duration rounding; keep the fixed-output
        # invariant unconditional, without requiring every muxer to share a bug.
        if Fraction(legacy_video["avg_frame_rate"]) != fps:
            with pytest.raises(ValueError, match="prepared frame contract"):
                encoding.verify(legacy, project, ffmpeg, ffprobe)
