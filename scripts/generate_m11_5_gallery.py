"""M11.5: publish provenance-checked native character / visualiser transition evidence.

The Kandi Boom poses are actual static upstream renderer captures. We never
present them as rigged motion rendered by FrameRudiments or as measured audio.
"""
from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import html
from io import BytesIO
import json
from pathlib import Path
import shutil
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps

UPSTREAM_REPO = "kieransimkin/DanceRudiments"
UPSTREAM_COMMIT = "718388ccdb992c0b97dc0a6d27b4d3043e20b5fa"
CAPTURES = {
    "rest": ("docs/images/kandi-boom-rest.png", "206d1ef6e4459eb47a0be54da39c95cd2d6e33b4"),
    "raised": ("docs/images/kandi-boom-arms-raised.png", "1d4d362ed62b8149cbd9ef14e8858f690a6a1eab"),
}
SCHEMA = "framerudiments.m11-5-character-transition-gallery.v1"
SIZE = (480, 270)
RATE = 24
FRAMES = 48
SCENES = ("dissolve", "matte", "spectral")


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def git_blob_sha(payload):
    return sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def upstream_capture(name):
    """Fetch immutable exact-file Git source; never fall back to unrelated art."""
    path, expected = CAPTURES[name]
    url = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{UPSTREAM_COMMIT}/{path}"
    with urlopen(Request(url, headers={"User-Agent": "FrameRudiments-M11.5"}), timeout=35) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024 or git_blob_sha(data) != expected:
        raise ValueError(f"Kandi Boom reference changed or exceeded budget: {name}")
    with Image.open(BytesIO(data)) as opened:
        opened.load()
        if opened.format != "PNG":
            raise ValueError("Upstream character reference must be PNG")
        image = opened.convert("RGBA")
    if image.width < 64 or image.height < 64:
        raise ValueError("Character reference is too small")
    return image, {"url": url, "git_blob": expected, "sha256": sha256(data).hexdigest()}


def fit_image(image):
    """Letterbox actual images without forging a character pose or cropping."""
    prepared = ImageOps.contain(image.convert("RGBA"), SIZE, method=Image.Resampling.LANCZOS)
    target = Image.new("RGBA", SIZE, (9, 16, 34, 255))
    target.alpha_composite(prepared, ((SIZE[0] - prepared.width) // 2,
                                     (SIZE[1] - prepared.height) // 2))
    return target


def mask_at(index, count):
    """An explicitly authored moving alpha matte, passed to the native graph."""
    out = Image.new("RGBA", SIZE, (255, 255, 255, 0))
    draw = ImageDraw.Draw(out)
    radius = int(36 + (SIZE[0] + 80) * index / max(1, count - 1))
    x, y = SIZE[0] // 2, SIZE[1] // 2
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(255, 255, 255, 255))
    return out


def graph_for(style):
    from framerudiments.effect_graph import EffectGraph

    nodes = [{"id": "character", "type": "source"},
             {"id": "visualiser", "type": "source"}]
    config = {"id": "blend", "type": "transition", "progress": 0,
              "transition_style": style, "inputs": ["character", "visualiser"]}
    if style == "matte":
        nodes.append({"id": "mask", "type": "source"})
        config.update(inputs=["character", "visualiser", "mask"],
                      matte_channel="alpha", feather=0.18)
    elif style == "spectral":
        config.update(bands=[{
            "id": "cyan", "source_color": "#edc18a", "to_color": "#42d6f5",
            "space": "rgb", "tolerance": 0.32, "softness": 0.15,
            "start": 0.05, "end": 0.95, "offset_from": [4, 0], "offset_to": [-4, 0],
        }])
    nodes.append(config)
    return EffectGraph({"nodes": nodes, "output": "blend"})


def side_by_side(character, visualiser, mixed, style):
    """Each panel is an actual source or graph output; caption is outside pixels."""
    width = SIZE[0] * 3
    canvas = Image.new("RGB", (width, SIZE[1] + 36), (8, 15, 30))
    draw = ImageDraw.Draw(canvas)
    labels = ("KANDI BOOM · SOURCE CAPTURE", "NATIVE VISUALISER", style.upper() + " · GRAPH OUTPUT")
    for n, (label, frame) in enumerate(zip(labels, (character, visualiser, mixed))):
        if frame.size != SIZE:
            raise ValueError("M11.5 comparison panels must share a viewport")
        canvas.paste(frame.convert("RGB"), (n * SIZE[0], 36))
        draw.text((n * SIZE[0] + 14, 11), label, font=ImageFont.load_default(), fill=(191, 236, 248))
    return canvas


def build(output, *, poses=None, frame_count=FRAMES, encode=True):
    """Render in increasing and shuffled order; only publish matching pixels."""
    if type(frame_count) is not int or not 4 <= frame_count <= 96:
        raise ValueError("M11.5 frame_count must be 4..96")
    site = Path(output)
    if site.exists() and any(site.iterdir()):
        # Existing gallery site is allowed, but an old M11.5 page is not.
        if (site / "m11-5").exists():
            raise ValueError("M11.5 output already exists")
    root = site / "m11-5"
    root.mkdir(parents=True, exist_ok=False)
    source = {}
    if poses is None:
        poses = {}
        for name in CAPTURES:
            poses[name], source[name] = upstream_capture(name)
        authenticity = "upstream_kandi_boom_captures"
    else:
        if set(poses) != set(CAPTURES):
            raise ValueError("Offline test poses require both rest and raised images")
        authenticity = "test_fixture_not_kandi_boom"
        source = {name: {"sha256": sha256(poses[name].tobytes()).hexdigest(),
                         "note": "user-provided test fixture, not upstream character"}
                  for name in CAPTURES}
    character = {name: fit_image(poses[name]) for name in CAPTURES}

    from framerudiments.rudiment_renderers import default_renderers
    from generate_visualisation_gallery import fixture
    from framerudiments.video_adapters import MANIFEST_SCHEMA, encode_manifest

    registry = default_renderers()
    renderer, _ = registry.create("spectrum", SIZE, {
        "background": "#091022", "padding": 10,
        "colours": ["#4fe5d0", "#ffbd69", "#c39dff", "#6b9dff"], "labels": False,
    })

    def render_visual(index):
        return renderer.render(fixture(round(index * 60 / RATE), spectral=True)).convert("RGBA")

    receipt = {"schema": SCHEMA, "size": list(SIZE), "frame_rate": f"{RATE}/1",
               "frames_per_scene": frame_count, "asset_source": authenticity,
               "upstream_repo": UPSTREAM_REPO if poses is None else None,
               "upstream_commit": UPSTREAM_COMMIT if poses is None else None,
               "sources": source, "audio": "no audio: native synthetic-display fixture",
               "characters_are_static_captures": True, "scenes": []}
    for style in SCENES:
        graph = graph_for(style)
        folder = root / style
        folder.mkdir()

        def frame_image(index):
            current = character["rest" if index < frame_count // 2 else "raised"]
            visual = render_visual(index)
            inputs = {"character": current, "visualiser": visual}
            if style == "matte":
                inputs["mask"] = mask_at(index, frame_count)
            progress = index / (frame_count - 1)
            mixed = graph.render(inputs, parameter_values={("blend", "progress"): progress})
            return side_by_side(current, visual, mixed, style)

        checksums = []
        rows = []
        for index in range(frame_count):
            image = frame_image(index)
            name = f"{index:04d}.png"
            target = folder / name
            image.save(target)
            checksums.append(sha256(image.tobytes()).hexdigest())
            rows.append({"path": name, "sha256": digest(target)})
        # Verify out-of-order requests from the same graph + native renderer.
        for index in reversed(range(frame_count)):
            if sha256(frame_image(index).tobytes()).hexdigest() != checksums[index]:
                raise ValueError(f"Non-deterministic M11.5 {style} frame {index}")
        poster = f"{style}-poster.png"
        shutil.copyfile(folder / f"{frame_count // 2:04d}.png", root / poster)
        if encode:
            manifest = {"schema": MANIFEST_SCHEMA, "width": SIZE[0] * 3,
                        "height": SIZE[1] + 36, "fps": [RATE, 1], "frames": rows}
            manifest_path = folder / "frames.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            video = root / f"{style}.mp4"
            exported = encode_manifest(manifest_path, video)
            if exported["verified"]["frames"] != frame_count:
                raise ValueError("M11.5 output frame count differs")
            video_data = {"path": video.name, "sha256": digest(video),
                          "verified": exported["verified"]}
        else:
            video_data = None
        receipt["scenes"].append({"id": style, "poster": poster,
                                  "poster_sha256": digest(root / poster),
                                  "decoded_rgb_sha256": checksums,
                                  "video": video_data})
        # Raw frame sequences are only evidence to encoder; don't publish 144 extra PNGs.
        if encode:
            shutil.rmtree(folder)
    (root / "catalogue.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "index.html").write_text(site_html(receipt), encoding="utf-8")
    home = site / "index.html"
    if home.exists():
        content = home.read_text(encoding="utf-8")
        link = '<p><a href="m11-5/index.html">M11.5 · Kandi Boom / visualiser transitions →</a></p>'
        if 'href="m11-5/index.html"' not in content:
            home.write_text(content.replace("</header>", link + "</header>", 1), encoding="utf-8")
    return receipt


def site_html(receipt):
    cards = []
    for item in receipt["scenes"]:
        title = {"dissolve": "A clean crossfade", "matte": "A shaped reveal",
                 "spectral": "A colour-aware transition"}[item["id"]]
        url = html.escape(item["poster"], quote=True)
        video = item["video"]
        media = (f'<video controls playsinline preload="metadata" poster="{url}" '
                 f'src="{html.escape(video["path"], quote=True)}"></video>'
                 if video else f'<img loading="lazy" src="{url}" alt="{title}">')
        cards.append(f'<article><h2>{title}</h2>{media}'
                     f'<p>Real source captures and native RGB graph pixels · {item["id"]}.</p></article>')
    disclosure = ("Two SHA-pinned stills of the actual Kandi Boom model from DanceRudiments "
                  "are used as static source imagery, not as animated rig output."
                  if receipt["asset_source"] == "upstream_kandi_boom_captures"
                  else "TEST FIXTURES ONLY — no genuine Kandi Boom artwork shown.")
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FrameRudiments · M11.5 Character ⇄ Visualiser</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;color:#e7f4ff;background:#080f20;font:16px/1.6 system-ui,Segoe UI,sans-serif}
a{color:#8ee9fa}.wrap{max-width:1130px;margin:auto;padding:36px 24px}
header{background:radial-gradient(circle at 20% 10%,#18385d,#080f20 72%);border-bottom:1px solid #254463}
.eyebrow{color:#66e3de;text-transform:uppercase;letter-spacing:.17em;font-size:12px;font-weight:800}
h1{font-size:clamp(2.2rem,6vw,4.2rem);line-height:1.05;margin:10px 0 22px;letter-spacing:-.05em}
.lead{color:#bed2e4;max-width:760px;font-size:1.15rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:24px}
article{border:1px solid #294966;border-radius:20px;overflow:hidden;background:#111f37;box-shadow:0 22px 50px #05091478}
article h2{font-size:1.15rem;margin:22px 22px 12px}article p{color:#c2d3e0;padding:0 22px 16px;font-size:14px}
video,img{display:block;width:100%;aspect-ratio:1440/306;object-fit:contain;background:#060b14}
.notice{padding:15px 20px;border-left:3px solid #eab166;background:#162536;color:#dfeaf2;margin:32px 0}
footer{border-top:1px solid #254463;margin-top:40px;color:#9fb5cc;font-size:14px}
</style></head><body><header><div class="wrap"><p class="eyebrow">M11.5 · Native effect graph evidence</p>
<h1>Kandi Boom <span style="color:#65e5d9">⇄</span> Visualisers</h1>
<p class="lead">Compare pinned character captures, actual native Rudiment visualiser frames,
and three working transitions rendered by the FrameRudiments effect graph.</p>
<p><a href="../index.html">All visualisers</a> · <a href="catalogue.json">Download frame hashes &amp; source provenance</a></p>
</div></header><main class="wrap"><div class="notice">""" + html.escape(disclosure) + """
Synthetic spectral display values are not extracted from a recording. Videos are silent visual tests;
source-audio synchronization and character animation must be qualified separately.</div>
<div class="grid">""" + "".join(cards) + """</div></main>
<footer><div class="wrap">Every frame was recomputed in reverse order and checked against its original
RGB pixel digest. Encoder timestamps are verified independently. <a href="catalogue.json">Inspect receipt</a>.</div></footer>
</body></html>"""


def verify(root):
    root = Path(root)
    report = json.loads((root / "catalogue.json").read_text(encoding="utf-8"))
    if report.get("schema") != SCHEMA or len(report.get("scenes", [])) != len(SCENES):
        raise ValueError("M11.5 gallery receipt incomplete")
    if [scene["id"] for scene in report["scenes"]] != list(SCENES):
        raise ValueError("M11.5 gallery scene order differs")
    if not (root / "index.html").is_file():
        raise ValueError("M11.5 HTML missing")
    for scene in report["scenes"]:
        if digest(root / scene["poster"]) != scene["poster_sha256"]:
            raise ValueError("M11.5 gallery poster checksum mismatch")
        if len(scene["decoded_rgb_sha256"]) != report["frames_per_scene"]:
            raise ValueError("M11.5 frame receipt count differs")
        if scene["video"] is not None and digest(root / scene["video"]["path"]) != scene["video"]["sha256"]:
            raise ValueError("M11.5 gallery video checksum mismatch")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build/verify M11.5 CI character transition gallery")
    parser.add_argument("--output", help="Existing site root to extend")
    parser.add_argument("--verify", help="Previously generated M11.5 directory")
    args = parser.parse_args(argv)
    if bool(args.output) == bool(args.verify):
        parser.error("Select exactly one of --output or --verify")
    if args.verify:
        result = verify(args.verify)
    else:
        result = build(args.output)
    print(json.dumps({"schema": result["schema"], "scenes": len(result["scenes"]),
                      "character_provenance": result["asset_source"]}, sort_keys=True))


if __name__ == "__main__":
    main()
