"""Generate reproducible original synthetic audio for the public React timeline.

No source media, project files, recordings or rendering-backend API are needed.
The URL is inside the static /timeline/ subtree and cannot leak local paths.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import wave

SAMPLE_RATE = 16000
DURATION_SECONDS = 16
BPM = 120
SCHEMA = "framerudiments.public-timeline-fixture.v1"


def generate(output):
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    sound = folder / "synthetic-click.wav"
    manifest = folder / "catalogue.json"
    if sound.exists() or manifest.exists():
        raise ValueError("Timeline demo artifacts already exist")
    pcm = bytearray()
    for i in range(SAMPLE_RATE * DURATION_SECONDS):
        t = i / SAMPLE_RATE
        beat = t % (60 / BPM)
        click = 0.46 * math.exp(-50 * beat) * math.sin(2 * math.pi * 660 * beat)
        note = (0.09 * math.sin(2 * math.pi * 110 * t)
                + 0.035 * math.sin(2 * math.pi * 165 * t))
        pulse = 0.5 + 0.5 * math.cos(2 * math.pi * BPM / 60 * t)
        value = max(-0.99, min(0.99, click + note * pulse))
        pcm.extend(struct.pack('<h', round(value * 32767)))
    try:
        with wave.open(str(sound), 'wb') as output_file:
            output_file.setnchannels(1)
            output_file.setsampwidth(2)
            output_file.setframerate(SAMPLE_RATE)
            output_file.writeframes(pcm)
        doc = {
            "schema": SCHEMA,
            "source": "generated_original_synthetic_audio",
            "sample_rate": SAMPLE_RATE,
            "duration_seconds": DURATION_SECONDS,
            "bpm": BPM,
            "audio": "synthetic-click.wav",
            "sha256": hashlib.sha256(sound.read_bytes()).hexdigest(),
            "timeline": "react-timeline-sequence@0.4.0",
            "offline_only": True,
            "no_native_preview_api": True,
            "license_note": "Synthetic fixture, not a real musical performance or analysis",
        }
        manifest.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except BaseException:
        sound.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        raise
    return doc


def verify(folder):
    folder = Path(folder)
    manifest = folder / 'catalogue.json'
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if (set(data) != {"schema", "source", "sample_rate", "duration_seconds", "bpm",
                      "audio", "sha256", "timeline", "offline_only", "no_native_preview_api", "license_note"}
            or data["schema"] != SCHEMA or not data["offline_only"]
            or not data["no_native_preview_api"] or data["audio"] != 'synthetic-click.wav'):
        raise ValueError('Unexpected public timeline manifest')
    sound = folder / data['audio']
    if hashlib.sha256(sound.read_bytes()).hexdigest() != data['sha256']:
        raise ValueError('Public demo audio SHA-256 mismatch')
    with wave.open(str(sound), 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) != (
                1, 2, SAMPLE_RATE, SAMPLE_RATE * DURATION_SECONDS):
            raise ValueError('Public demo audio timebase changed')
    page = (folder / 'index.html').read_text(encoding='utf-8')
    if ('<div id="root"></div>' not in page or '/FrameRudiments/timeline/assets/' not in page):
        raise ValueError('The gallery timeline was not built with the Pages base path')
    return data


def main(argv=None):
    p = argparse.ArgumentParser(description='Generate or verify original playable timeline audio')
    p.add_argument('--output', required=True)
    p.add_argument('--verify', action='store_true')
    args = p.parse_args(argv)
    result = verify(args.output) if args.verify else generate(args.output)
    print(json.dumps({"schema": result['schema'], "sha256": result['sha256']}))


if __name__ == '__main__':
    main()
