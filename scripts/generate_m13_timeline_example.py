"""Publish one source-pinned OTIO edit decision list for the public timeline.

Uses the previously generated, original synthetic 16-second audio fixture. It
preserves audio and cues, not the procedural visualisers as if they were media.
"""

import argparse
import hashlib
import json
from pathlib import Path

from framerudiments.timeline_otio_m13 import (SCHEMA, export_otio, from_otio,
                                               manifest_file, verify_sources, _decoded)


def example(folder):
    base = Path(folder)
    catalogue = json.loads((base / "catalogue.json").read_text(encoding="utf-8"))
    if (catalogue.get("schema") != "framerudiments.public-timeline-fixture.v1" or
            catalogue.get("sample_rate") != 16000 or
            catalogue.get("duration_seconds") != 16 or
            catalogue.get("bpm") != 120 or catalogue.get("audio") != "synthetic-click.wav"):
        raise ValueError("Expected the official synthetic public timeline fixture")
    source = base / catalogue["audio"]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != catalogue["sha256"]:
        raise ValueError("Synthetic timeline audio source checksum changed")
    return {"schema": SCHEMA, "name": "FrameRudiments public 120 BPM timeline",
            "fps": [60, 1], "tracks": [
                {"id": "audio", "kind": "audio", "items": [
                    {"type": "clip", "id": "click", "name": "Original synthetic 120 BPM click",
                     "source": {"uri": "synthetic-click.wav", "sha256": digest},
                     "source_fps": [16000, 1], "source_start_frame": 0,
                     "duration_frames": 960},
                ]},
                {"id": "unrendered", "kind": "video", "items": [
                    {"type": "gap", "id": "procedural_placeholder", "duration_frames": 960},
                ]},
            ], "markers": [
                {"id": name.lower(), "name": name, "frame": tick * 60,
                 "duration_frames": 0, "color": "YELLOW"}
                for name, tick in (("INTRO", 0), ("VERSE", 4), ("DROP", 8), ("OUTRO", 12))
            ]}


def build(folder):
    base = Path(folder)
    doc = example(base)
    manifest = base / "example.timeline.json"
    if manifest.exists() or manifest.is_symlink():
        raise ValueError("Timeline example already exists")
    manifest.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    try:
        result = export_otio(doc, base / "example.otio", media_root=base)
    except BaseException:
        manifest.unlink(missing_ok=True)
        raise
    return result


def verify(folder):
    base = Path(folder)
    expect = example(base)
    actual = manifest_file(base / "example.timeline.json")
    if actual != expect:
        raise ValueError("Published OTIO example manifest changed")
    verify_sources(actual, base)
    otio_path = base / "example.otio"
    payload = otio_path.read_bytes()
    receipt = json.loads((base / "example.otio.receipt.json").read_text(encoding="utf-8"))
    if receipt.get("output_sha256") != hashlib.sha256(payload).hexdigest():
        raise ValueError("OTIO example checksum mismatch")
    parsed = from_otio(_decoded(payload.decode("utf-8")))
    if parsed != expect:
        raise ValueError("Public OTIO example changed on readback")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reproducible public M13 OTIO export")
    parser.add_argument("--site", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    result = verify(args.site) if args.verify else build(args.site)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
