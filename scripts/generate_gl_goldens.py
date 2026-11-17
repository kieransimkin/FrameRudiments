"""Explicit M9.5 reference-image authoring. No GPU or network access.

Run from a source checkout with: python scripts/generate_gl_goldens.py --output <new-directory>
Review the generated PNGs/manifest; copy them into docs/media/gl-goldens only
when intentionally updating golden ABI. CI never overwrites the approved files.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from framerudiments.gl_transition_goldens import (  # noqa: E402
    CASES, SCHEMA, SIZE, SOURCE_SCHEMA, reference_image, synthetic_sources,
)
from framerudiments.gl_shader_guard import POLICY_VERSION  # noqa: E402


def generate(directory):
    directory = Path(directory).resolve()
    # Never rewrite tracked golden PNGs without an explicit review and copy.
    if directory.exists():
        raise ValueError(f"Output directory already exists: {directory}")
    if directory.parent.is_dir() is False:
        raise ValueError("Output parent directory does not exist")
    directory.mkdir()
    outgoing, incoming = synthetic_sources()
    manifest = {
        "schema": SCHEMA,
        "reference_kind": "deterministic_host_reference_not_gpu_capture",
        "source_schema": SOURCE_SCHEMA,
        "size": list(SIZE),
        "policy": POLICY_VERSION,
        "sources": {
            "outgoing_rgba_sha256": sha256(outgoing.tobytes()).hexdigest(),
            "incoming_rgba_sha256": sha256(incoming.tobytes()).hexdigest(),
        },
        "cases": [],
    }
    for case_id, shaders, progress, time_seconds, frame_index in CASES:
        pins = []
        for shader in shaders:
            src = ROOT / "examples" / shader
            if not src.is_file():
                raise ValueError(f"Missing committed shader fixture: {src}")
            pins.append({"path": f"examples/{shader}", "sha256": sha256(src.read_bytes()).hexdigest()})
        image = reference_image(case_id)
        path = directory / f"{case_id}.png"
        image.save(path, format="PNG", optimize=False)
        manifest["cases"].append({
            "id": case_id,
            "shaders": pins,
            "progress": progress,
            "time_seconds": time_seconds,
            "time_delta_seconds": 1 / 24,
            "frame_index": frame_index,
            "golden": f"docs/media/gl-goldens/{case_id}.png",
            "png_sha256": sha256(path.read_bytes()).hexdigest(),
            "rgba_sha256": sha256(image.tobytes()).hexdigest(),
        })
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New, non-existing directory for proposed reference bytes")
    args = parser.parse_args(argv)
    result = generate(args.output)
    print(f"Prepared {len(result['cases'])} independent host references. GPU verification not performed.")


if __name__ == "__main__":
    main()
