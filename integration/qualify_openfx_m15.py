"""Actual C ABI OFX reference plugin admission/render/crash/deadline qualification."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import shutil
import subprocess
from tempfile import TemporaryDirectory

from PIL import Image

from framerudiments.openfx_m15 import SCHEMA, describe, render


PLUGIN_ID = "org.framerudiments.reference.invert"


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def compile_reference(root, *, mode=0):
    c_file = Path(__file__).resolve().parents[1] / "tests/fixtures/openfx_m15/reference_filter.c"
    compiler = shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        raise RuntimeError("Native OpenFX qualification requires a C compiler")
    if platform.system() != "Linux":
        raise RuntimeError("The required native reference compiler gate is Linux-only")
    target = root / f"ref-{mode}.so"
    command = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-fPIC", "-shared",
               f"-DREF_MODE={mode}", str(c_file), "-o", str(target)]
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    return target


def create_manifest(root, lib, image, *, frame=25, rate="30000/1001", timeout=5):
    with Image.open(image) as photo:
        mode = photo.mode
    assert mode in ("RGB", "RGBA")
    data = {"schema": SCHEMA,
            "plugin": {"path": lib.name, "sha256": digest(lib), "identifier": PLUGIN_ID,
                       "declared_license": "MIT", "trusted_execution": True},
            "source": {"path": image.name, "sha256": digest(image)},
            "frame": frame, "fps": rate, "timeout_seconds": timeout,
            "pixel_contract": {"mode": mode + "8", "alpha": "straight" if mode == "RGBA" else "opaque",
                               "transfer": "sRGB"}}
    manifest = root / "request.json"
    manifest.write_text(json.dumps(data, sort_keys=True) + "\n", "utf-8")
    return manifest


def expected_image(image):
    with Image.open(image) as source:
        data = source.tobytes()
        result = bytearray(data)
        stride = 4 if source.mode == "RGBA" else 3
        for i in range(0, len(data), stride):
            result[i:i+3] = bytes(255 - v for v in data[i:i+3])
        return Image.frombytes(source.mode, source.size, bytes(result))


def run(output):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("Qualification needs a new directory beneath an existing parent")
    # Probe temporary ownership and rename semantics before compiling/loading C code.
    with TemporaryDirectory(prefix="fr-ofx-qa-probe-", dir=output.parent) as probe:
        path = Path(probe) / "source"
        path.write_bytes(b"openfx-qa-preflight")
        assert path.read_bytes() == b"openfx-qa-preflight"
        renamed = path.with_name("renamed")
        path.replace(renamed)
        assert renamed.read_bytes() == b"openfx-qa-preflight"
        renamed.unlink()
    output.mkdir(parents=True)
    with TemporaryDirectory(prefix="ofx-native-", dir=output) as owned:
        root = Path(owned)
        library = compile_reference(root)
        receipt_rows = []
        for index, (rate, width, height, mode) in enumerate([
                ("24", 48, 36, "RGBA"), ("24000/1001", 65, 37, "RGBA"),
                ("30000/1001", 160, 90, "RGBA"), ("25", 64, 48, "RGB")]):
            img = root / f"source-{index}.png"
            rgba = Image.new(mode, (width, height))
            for y in range(height):
                for x in range(width):
                    colour = ((x*13+y*3)%256, (y*17+x)%256, (x*3+y*11)%256)
                    rgba.putpixel((x, y), colour + ((x*9+y*5)%256,) if mode == "RGBA" else colour)
            rgba.save(img)
            shutil.copyfile(img, output / f"source-{index}.png")
            manifest = create_manifest(root, library, img, rate=rate, frame=index + 15)
            scan = describe(manifest)
            assert not scan["binary_loaded"] and scan["plugin_abi_verified_only_after_execution"]
            dst = output / f"reference-{index}.png"
            verified = render(manifest, dst)
            with Image.open(dst) as actual:
                actual.load()
                assert actual.tobytes() == expected_image(img).tobytes()
            assert digest(dst) == verified["output_sha256"]
            receipt_rows.append({"frame": verified["frame"], "fps": rate,
                                 "mode": mode + "8", "width": width, "height": height,
                                 "output_sha256": verified["output_sha256"],
                                 "source_sha256": verified["source_sha256"]})

        for mode, name, exception in ((1, "failed_status", RuntimeError),
                                       (2, "native_abort", RuntimeError),
                                       (3, "deadline", TimeoutError)):
            library = compile_reference(root, mode=mode)
            manifest = create_manifest(root, library, img, timeout=2)
            dst = output / f"bad-{name}.png"
            try:
                render(manifest, dst)
            except exception:
                pass
            else:
                raise AssertionError(f"OpenFX {name} was incorrectly accepted")
            assert not dst.exists() and not dst.with_name(dst.name + ".receipt.json").exists()
        result = {"schema": "framerudiments.openfx-native-qualification.v1",
                  "sample_frames": receipt_rows, "native_plugin_api": "OpenFX v1",
                  "filter_context": "Source->Output", "golden_pixels": "exact",
                  "crash_and_timeout": "rejected; no output published",
                  "compiler": Path(shutil.which("cc") or shutil.which("gcc")).name,
                  "physical_gpu": False, "arbitrary_plugin_certified": False}
        (output / "qualification.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", "utf-8")
        cards = []
        for i, row in enumerate(receipt_rows):
            cards.append(f"""<article><header><span>Frame {row['frame']} at {row['fps']} FPS</span>
            <strong>{row['width']} × {row['height']}</strong></header>
            <div class='images'><figure><img src='source-{i}.png' alt='Original synthetic source frame'/>
            <figcaption>Original (pinned)</figcaption></figure>
            <figure><img src='reference-{i}.png' alt='Native C OpenFX inverted RGB frame'/>
            <figcaption>OpenFX C filter · original alpha</figcaption></figure></div>
            <p class='digest'>Result SHA-256: {row['output_sha256']}</p></article>""")
        webpage = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>OpenFX M15.3 · Native Filter Demonstration</title>
        <style>:root{color-scheme:dark;background:#0c1322;color:#ebf4ff;font:16px system-ui}
        *{box-sizing:border-box}body{margin:0}a{color:#77e6d5}
        main{width:min(1120px,96%);margin:auto;padding:3rem 1rem}h1{font-size:clamp(2rem,5vw,3.8rem);letter-spacing:-.05em}
        .sub{line-height:1.7;color:#bfcee6;max-width:850px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:1.2rem;margin:2rem 0}
        article{border:1px solid #344663;border-radius:16px;background:#16243a;overflow:hidden}
        article header{display:flex;justify-content:space-between;padding:1.1rem;gap:.5rem;color:#84e9d7}
        .images{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;padding:.8rem}
        figure{margin:0;text-align:center;font-size:.8rem;color:#adc3dd}figure img{width:100%;aspect-ratio:1;object-fit:contain;image-rendering:pixelated;border-radius:8px;background:#0a1121}
        figcaption{padding:.4rem}.digest{font: .68rem ui-monospace,monospace;overflow-wrap:anywhere;margin:1rem;color:#c5cfe6}
        footer{border-top:1px solid #37465a;padding-top:1.2rem;color:#9fb2cb}</style></head>
        <body><main><p><a href='../'>FrameRudiments visualisation atlas</a></p>
        <h1>A real OpenFX filter. No mock pixels.</h1>
        <p class='sub'>A genuine native C OpenFX Image Effect v1 filter ran in a separate owned process.
        It used the standard v1 property and image-effect suites to invert RGB channels while preserving
        straight alpha. Exact output pixels were independently compared against a Python reference.
        The generated frames are synthetic test data, not production artwork.</p>
        <section class='grid'>""" + "".join(cards) + """</section>
        <footer><p>ABI scope: filter context, RGBA8, full-frame CPU, no parameters, no GPU texture sharing.
        Tested failure, abort and deadline paths are not a guarantee that arbitrary plugins are safe.</p>
        <p><a href='qualification.json'>Qualification JSON and checksums</a> ·
        <a href='https://github.com/kieransimkin/FrameRudiments/blob/main/docs/openfx-m15.md'>Contract and source</a></p></footer></main></body></html>"""
        (output / "index.html").write_text(webpage, "utf-8")
        return result


def main():
    arg = argparse.ArgumentParser()
    arg.add_argument("--output", required=True)
    opts = arg.parse_args()
    print(json.dumps(run(opts.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
