"""Original click fixture and the real FrameRudiments source-bound clock adapter."""
import argparse
from copy import deepcopy
from pathlib import Path
import math
import struct
import wave

from framerudiments.demo import create_demo
from framerudiments.io import read_json, sha256, write_json


def generate(output, font):
    path = create_demo(output, font, width=360, height=640)
    data = read_json(path)
    rate = 48000
    with wave.open(str(path.parent / "tone.wav"), "wb") as stream:
        stream.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        pcm = bytearray()
        for index in range(rate * 4):
            time = index / rate
            phase = (time - .125) % .5
            amplitude = math.exp(-phase * 160) if time >= .125 and phase < .04 else 0
            value = round(16000 * amplitude * math.sin(2 * math.pi * 1000 * phase))
            pcm.extend(struct.pack("<hh", value, value))
        stream.writeframes(pcm)
    data["audio"]["source"]["sha256"] = sha256(path.parent / "tone.wav")
    reading = dict(schema="stemlab.beat-alignment.v1", source_sha256=data["audio"]["source"]["sha256"],
                   source_audio_verified=True, source_audio=dict(sample_rate=rate, num_frames=rate * 4),
                   bpm=120, exact_bpm="120", bpm_basis="combined_fit", bpm_status="estimated",
                   phase_status="synthetic", analysis_id="consensus", evidence_sha256="b" * 64,
                   report_sha256="c" * 64, first_beat_seconds=.125, first_beat_exact_seconds="1/8",
                   offset_pips=-16, exact_offset_pips="-16", pips_per_beat=64, canonical_first_beat_pip=0)
    write_json(path.parent / "beat-alignment.json", reading)
    data["clock"] = dict(authority="Original synthetic click score; not an inferred real song",
                         status="estimated", phase_status="synthetic",
                         beat_alignment=dict(path="beat-alignment.json", sha256=sha256(path.parent / "beat-alignment.json")))
    data["identity"][0]["text"] = "Beat alignment / -16 pips"
    data["identity"][0]["style"]["box"] = [20, 30, 340, 95]
    data["captions"]["box"] = [35, 105, 325, 220]
    native = data["layers"][0]
    native.update(id="aligned", center=[260, 400], width=90, bounds=[190, 315, 335, 500])
    original = deepcopy(native)
    original.update(id="original", center=[100, 400], bounds=[25, 315, 170, 500])
    original["motion"]["phase_beats"] = .25
    data["layers"].insert(0, original)
    for text, box in (("Original pip 0 at 0s", [15, 265, 175, 315]),
                      ("Aligned pip 0 at .125s", [185, 265, 345, 315]),
                      ("Recording: original source clock", [25, 500, 335, 535])):
        data["identity"].append(dict(text=text, style=dict(box=box, font_size=16, min_font_size=10, stroke=0)))
    data["notes"] = "Synthetic 120 BPM clicks begin at 0.125 s. The left movement adds 0.25 beat to show the original grid. The right uses the validated -16 pip reading. Lyrics/cues and recording retain source timestamps. No detector accuracy or song approval is implied."
    write_json(path, data)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path, required=True)
    args = parser.parse_args()
    print(generate(args.output, args.font))
