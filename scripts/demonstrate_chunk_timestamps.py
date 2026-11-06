"""Plot actual synthetic concat timing before/after the packet-grid correction.

Requires the development environment plus matplotlib. No private input is used.
Run from the repository root with FRAMERUDIMENTS_FFMPEG configured.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import wave

import matplotlib.pyplot as plt

from framerudiments import encoding


def decoded_hashes(ffmpeg, path):
    text = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0",
                                    "-fps_mode", "passthrough", "-f", "framemd5", "-"], text=True)
    return [line.rsplit(",", 1)[1].strip() for line in text.splitlines()
            if line and not line.startswith("#")]


def timestamps(ffprobe, path):
    stream = next(item for item in encoding.probe(path, ffprobe)["streams"] if item["codec_type"] == "video")
    data = json.loads(subprocess.check_output(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "frame=best_effort_timestamp", "-of", "json", str(path)], text=True,
    ))
    scale = Fraction(stream["time_base"])
    return [int(row["best_effort_timestamp"]) * scale for row in data["frames"]], stream["avg_frame_rate"]


def main():
    plt.switch_backend("Agg")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/media/chunk-timestamp-drift.png"))
    args = parser.parse_args()
    ffmpeg, ffprobe = encoding.binaries()
    parent = Path("local/chunk-timestamp-demo")
    parent.mkdir(parents=True, exist_ok=True)
    # Disposable create/read/atomic-rename/delete probe before media processing.
    with tempfile.TemporaryDirectory(prefix="probe-", dir=parent) as directory:
        before = Path(directory) / "before"
        before.write_bytes(b"probe")
        assert before.read_bytes() == b"probe"
        before.rename(Path(directory) / "after")
        (Path(directory) / "after").unlink()
    work = Path(tempfile.mkdtemp(prefix="run-", dir=parent)).resolve()
    audio = work / "synthetic.wav"
    total = 143
    with wave.open(str(audio), "wb") as handle:
        handle.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0" * total * 800 * 4)
    project = SimpleNamespace(width=64, height=36, fps=60, frame_count=total,
                              audio_ref={"path": str(audio)}, start_sample=0, end_sample=total * 800,
                              sample_rate=48000, repeats=1, fade=0, duration=Fraction(total, 60))
    paths, original_pictures = [], []
    for index in range(13):
        path = work / f"chunk-{index:02d}.mp4"
        pixels = b"".join(bytes(((frame * 11 + index * 17) % 256, frame * 7 % 256, index * 19 % 256))
                          * (64 * 36) for frame in range(11))
        subprocess.run(encoding.video_chunk_command(project, path, ffmpeg, preset="fast", crf=20),
                       input=pixels, check=True, capture_output=True)
        paths.append(path)
        original_pictures.extend(decoded_hashes(ffmpeg, path))
    concat = work / "chunks.txt"
    concat.write_text("".join(f"file '{path.name}'\n" for path in paths), encoding="utf-8")
    receipt = dict(synthetic=True, chunks=13, frames=total, fps=60,
                   ffmpeg=subprocess.check_output([ffmpeg, "-version"], text=True).splitlines()[0])
    errors = {}
    for label in ("former", "corrected"):
        output = work / f"{label}.mp4"
        command = encoding.mux_chunks_command(project, concat, output, ffmpeg)
        if label == "former":
            position = command.index("-bsf:v")
            del command[position:position + 2]
        subprocess.run(command, check=True, capture_output=True)
        actual, average = timestamps(ffprobe, output)
        assert len(actual) == total
        errors[label] = [float((value - Fraction(index, 60)) * 1_000_000)
                         for index, value in enumerate(actual)]
        unchanged = decoded_hashes(ffmpeg, output) == original_pictures
        assert unchanged
        receipt[label] = dict(avg_frame_rate=average, decoded_pictures_identical=True,
                              error_microseconds=errors[label])
        if label == "corrected":
            encoding.verify(output, project, ffmpeg, ffprobe)
            assert all(value == 0 for value in errors[label])
    with plt.style.context("dark_background"):
        fig, ax = plt.subplots(figsize=(12, 5.1), dpi=160)
        fig.patch.set_facecolor("#101923")
        ax.set_facecolor("#101923")
        ax.step(range(total), errors["former"], where="post", color="#ff927c", linewidth=2.2,
                label="Former assembly")
        ax.step(range(total), errors["corrected"], where="post", color="#55e5cb", linewidth=2.2,
                label="Corrected: exact 60 fps")
        for boundary in range(11, total, 11):
            ax.axvline(boundary, color="#708495", linewidth=.6, alpha=.25)
        ax.set_title("Tiny join errors can break an exact frame clock", loc="left", pad=16, fontsize=17)
        ax.set_xlabel("Decoded frame (13 chunks × 11 frames)")
        ax.set_ylabel("Timestamp error from frame / 60 (microseconds)")
        ax.legend(loc="lower left", frameon=False)
        ax.grid(axis="y", alpha=.15)
        for edge in ("top", "right"):
            ax.spines[edge].set_visible(False)
        fig.text(.085, .015, "Actual synthetic H.264 measurements · B-frame ordering retained · All 143 decoded pictures unchanged",
                 color="#bdd0dd", fontsize=9)
        fig.tight_layout(rect=(0, .04, 1, 1))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.output, facecolor=fig.get_facecolor())
        plt.close(fig)
    receipt["plot_sha256"] = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(".json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"plot": str(args.output), "former": receipt["former"]["avg_frame_rate"],
                      "corrected": receipt["corrected"]["avg_frame_rate"], "all_pictures_unchanged": True}))


if __name__ == "__main__":
    main()
