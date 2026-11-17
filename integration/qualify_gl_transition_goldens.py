"""M9.5: compare pinned reference pixels with real GL; emit renderer provenance.

Requires GPU extra and existing frozen PNGs. Never regenerates references.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from framerudiments.gl_shader_guard import POLICY_VERSION  # noqa: E402
from framerudiments.gl_transition_goldens import (  # noqa: E402
    shader_config, synthetic_sources, verify_manifest,
)


def qualify(*, backend="egl", expected_renderer=None):
    manifest = verify_manifest(ROOT)  # Preflight source and golden bytes before GL.
    from framerudiments.effect_graph_gpu import GLMatteBackend
    from framerudiments.effect_graph_gpu_memory import is_software_renderer
    from framerudiments.gl_transitions import render_gl_transition, shader_pass_plan
    outgoing, incoming = synthetic_sources()
    original = outgoing.tobytes(), incoming.tobytes()
    cases = []
    with GLMatteBackend(backend=backend) as device:
        info = device.ctx.info
        renderer = str(info.get("GL_RENDERER", "unknown"))
        if expected_renderer and expected_renderer.lower() not in renderer.lower():
            raise ValueError(f"Renderer {renderer!r} does not match {expected_renderer!r}")
        identity = {
            "backend": backend or "platform", "vendor": str(info.get("GL_VENDOR", "unknown")),
            "renderer": renderer, "version": str(info.get("GL_VERSION", "unknown")),
            "context_version": device.ctx.version_code,
            "software_renderer": bool(is_software_renderer(renderer)),
            "physical_adapter_confirmed": False,
        }
        from PIL import Image
        for row in manifest["cases"]:
            config = shader_config(row, ROOT)
            device.begin_transfer_sample()
            result = render_gl_transition(device, outgoing, incoming, config)
            with Image.open(ROOT / row["golden"]) as png:
                expected = png.convert("RGBA").tobytes()
            pixels = result.tobytes()
            if pixels != expected or sha256(pixels).hexdigest() != row["rgba_sha256"]:
                raise AssertionError(f"M9.5 GPU golden mismatch: {row['id']} on {renderer}")
            transfer = device.last_transport
            plan = shader_pass_plan(config, result.size)
            if (transfer.get("uploads") != 2 or transfer.get("readbacks") != 1
                    or transfer.get("upload_bytes") != 2 * len(pixels)
                    or transfer.get("readback_bytes") != len(pixels)
                    or transfer.get("external_shader_passes") != plan["passes"]
                    or transfer.get("external_shader_peak_payload_bytes") != plan["working_rgba8_bytes"]):
                raise AssertionError(f"M9.5 transfer or resource mismatch: {row['id']}")
            cases.append({
                "id": row["id"], "state": "gpu_pixels_match_reference",
                "observed_rgba_sha256": sha256(pixels).hexdigest(),
                "expected_rgba_sha256": row["rgba_sha256"],
                "shader_pins": row["shaders"],
                "uniforms": {"progress": row["progress"], "TIME": row["time_seconds"],
                             "TIMEDELTA": row["time_delta_seconds"], "FRAMEINDEX": row["frame_index"]},
                "surface": list(result.size),
                "transport": {"uploads": transfer["uploads"], "readbacks": transfer["readbacks"],
                              "upload_bytes": transfer["upload_bytes"], "readback_bytes": transfer["readback_bytes"],
                              "shader_passes": transfer["external_shader_passes"],
                              "peak_rgba8_bytes": transfer["external_shader_peak_payload_bytes"]},
            })
        if (outgoing.tobytes(), incoming.tobytes()) != original:
            raise AssertionError("M9.5 source mutation")
    return {
        "schema": "framerudiments.gl-golden-qualification.v1",
        "scope": "actual OpenGL sample; pixel parity and tracked input/program pins",
        "reference_schema": manifest["schema"], "shader_policy": POLICY_VERSION,
        "reference_manifest_sha256": sha256((ROOT / "docs/media/gl-goldens/manifest.json").read_bytes()).hexdigest(),
        "renderer": identity, "cases": cases,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="egl", choices=("egl", "default"))
    parser.add_argument("--expect-renderer", help="Case-insensitive GL_RENDERER substring")
    parser.add_argument("--output", required=True, help="New receipt path (never overwrite)")
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.exists():
        raise ValueError(f"Receipt already exists: {output}")
    receipt = qualify(backend=None if args.backend == "default" else args.backend,
                      expected_renderer=args.expect_renderer)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"M9.5: {len(receipt['cases'])} real GPU golden comparisons passed: {output}")


if __name__ == "__main__":
    main()
