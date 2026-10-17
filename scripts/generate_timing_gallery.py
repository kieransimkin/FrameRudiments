"""CI Pages example: actual rendered synthetic motion, variable BPM and dual LRC captions."""

import argparse
from fractions import Fraction
import html
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import wave

from PIL import Image, ImageDraw, ImageFont

from framerudiments.compositor import Compositor
from framerudiments.io import sha256, write_json
from framerudiments.project import Project

FRAMES = (10, 50, 95, 156)
SCENES = (
    ("fixed-line", "Fixed BPM + original line subtitles", False, "off", False),
    ("variable-line", "StemLab variable BPM + source-time lines", True, "off", False),
    ("variable-karaoke", "Variable BPM + live word highlighting", True, "progressive", False),
    ("variable-dual", "Variable BPM + line & separate word subtitle", True, "highlight", True),
)


def _font():
    candidates = (
        os.environ.get("FRAMERUDIMENTS_TEST_FONT"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    found = next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)
    if found is None:
        raise RuntimeError("Set FRAMERUDIMENTS_TEST_FONT for the synthetic word timing gallery")
    return Path(found)


def _inputs(base):
    rate = 8000
    master = base / "synthetic-master.wav"
    with wave.open(str(master), "wb") as handle:
        handle.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        pcm = bytearray()
        for n in range(rate * 3):
            t = n / rate
            value = round(7000 * math.sin(2 * math.pi * 220 * t))
            pcm += struct.pack("<h", value)
        handle.writeframes(pcm)
    sprite = base / "ring.png"
    image = Image.new("RGBA", (144, 144))
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, 130, 130), outline="#42c7ef", width=11)
    draw.ellipse((40, 40, 104, 104), outline="#eda572", width=5)
    image.save(sprite)
    (base / "lines.lrc").write_text(
        "[00:00.00]Signals into motion\n[00:01.00]Words into light\n[00:02.00]Change the tempo\n",
        encoding="utf-8",
    )
    (base / "words.lrc").write_text(
        "[00:00.12]Signals\n[00:00.39]into\n[00:00.66]motion\n"
        "[00:01.12]Words\n[00:01.40]into\n[00:01.74]light\n"
        "[00:02.12]Change\n[00:02.39]the\n[00:02.63]tempo\n",
        encoding="utf-8",
    )
    points = [1000, 5000, 9000, 14160, 18080]
    anchors = [(i, frame) for i, frame in enumerate(points)]
    offset = Fraction(-64 * 1000, 4000)
    mp = dict(
        schema="stemlab.beat-timeline.v1",
        pips_per_beat=64,
        source_audio=dict(sha256=sha256(master), sample_rate=rate, num_frames=rate * 3, num_channels=1),
        consensus_sha256="b" * 64,
        origin_beat=0,
        review_status="estimated",
        beats=[
            dict(beat=beat, frame=frame, source="consensus", reference_frame=frame) for beat, frame in anchors
        ],
        sync=dict(
            beat_1_index=0,
            beat_1_source_frame=1000,
            beat_1_start_seconds=0.125,
            beat_1_start_pips=float(-offset),
            sync_phase_offset_pips=float(offset),
            exact_phase_offset_pips=str(offset),
            canonical_first_beat_pip=0,
            pips_per_beat=64,
        ),
    )
    write_json(base / "beat-timeline.json", mp)
    return master


def build_site(output):
    output = Path(output)
    directory = output / "timing"
    directory.mkdir(parents=True, exist_ok=False)
    rows = []
    with tempfile.TemporaryDirectory(prefix="framerudiments-timing-") as temp:
        base = Path(temp)
        _inputs(base)
        font = _font()

        def ref(path):
            return dict(path=str(base / path), sha256=sha256(base / path))

        for key, label, variable, word_mode, dual in SCENES:
            folder = directory / key
            folder.mkdir()
            clock = (
                dict(
                    authority="synthetic edited anchors",
                    status="estimated",
                    beat_timeline=ref("beat-timeline.json"),
                )
                if variable
                else dict(authority="synthetic fixed metronome", status="synthetic", bpm=120)
            )
            data = dict(
                schema="framerudiments.project.v1",
                title="Timed subtitles",
                width=600,
                height=340,
                fps=60,
                purpose="visualiser",
                audio=dict(
                    source=ref("synthetic-master.wav"),
                    sample_rate=8000,
                    start_sample=0,
                    end_sample=24000,
                    fade_seconds=0,
                ),
                clock=clock,
                font=dict(path=str(font), sha256=sha256(font)),
                lyrics=ref("lines.lrc"),
                word_lyrics=ref("words.lrc"),
                captions=dict(
                    box=[20, 205, 580, 258],
                    font_size=30,
                    min_font_size=21,
                    stroke=1,
                    word_mode=word_mode,
                    word_colour="#f4b969",
                    completed_colour="#58dee2",
                ),
                background=dict(colour="#0d1b30"),
                layers=[
                    dict(
                        id="moving-ring",
                        type="sprite",
                        asset=ref("ring.png"),
                        center=[300, 110],
                        width=108,
                        bounds=[0, 0, 600, 200],
                        motion=dict(kind="named", pattern="circle"),
                        mapping=dict(dx=160, dy=20, rotation=38),
                    )
                ],
            )
            if dual:
                data["word_captions"] = dict(
                    box=[70, 266, 530, 326], font_size=24, min_font_size=18, colour="#aaf6db", stroke=1
                )
            project = Project(data, base)
            compositor = Compositor(project)
            observations = []
            for index, frame in enumerate(FRAMES):
                picture, ctx = compositor.frame(frame)
                draw = ImageDraw.Draw(picture)
                font_small = ImageFont.truetype(font, 15)
                local_bpm = project.clock.tempo_at(ctx.source_seconds) if variable else 120
                draw.text(
                    (16, 13),
                    f"{key} | {local_bpm:.2f} BPM | beat {ctx.beat:.3f}",
                    font=font_small,
                    fill="#cce0f8",
                )
                picture.save(folder / f"{index}.png")
                observations.append(
                    dict(
                        frame=frame,
                        source_seconds=ctx.source_seconds,
                        integrated_beat=ctx.beat,
                        local_bpm=local_bpm,
                        word=ctx.word["text"] if ctx.word else "",
                    )
                )
            rows.append(
                dict(
                    name=key,
                    label=label,
                    variable_bpm=variable,
                    word_mode=word_mode,
                    dual_captions=dual,
                    samples=observations,
                )
            )
    catalog = dict(
        synthetic=True,
        measured_audio=False,
        beat_timeline_schema="stemlab.beat-timeline.v1",
        word_formats=["plain word LRC", "enhanced word LRC"],
        frames=list(FRAMES),
        scenes=rows,
    )
    (directory / "catalogue.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    cards = "\n".join(
        f'<article><img loading="lazy" data-name="{r["name"]}" src="{r["name"]}/0.png" '
        f'alt="{html.escape(r["label"])}"><h2>{html.escape(r["label"])}</h2></article>'
        for r in rows
    )
    page = (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FrameRudiments · Variable BPM and per-word subtitles</title><style>
:root{color-scheme:dark}body{background:#101927;color:#e9f3ff;font-family:system-ui;max-width:1200px;margin:auto;padding:24px}
a{color:#62dfd0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:20px}
article{background:#14283e;border:1px solid #355571;border-radius:12px;overflow:hidden}
article img{width:100%;display:block}h2{font-size:1rem;padding:0 18px 14px}input{width:230px}</style></head><body>
<a href="../index.html">← Visualisation Atlas</a><h1>Variable tempo and word-accurate subtitles</h1>
<p>Four real FrameRudiments renders made from synthetic music and sample-frame beat anchors; captions use original audio timestamps, while the ring follows the integrated variable-BPM clock. No automatic musical transcription is claimed.</p>
<label>Frame sample <input type="range" min="0" max="3" value="0" id="frame"></label>
<p><a href="catalogue.json">Reproducible clock and word-state data</a></p>
<div class="grid">"""
        + cards
        + """</div><script>document.getElementById('frame').addEventListener('input',e=>{
  for(const image of document.querySelectorAll('img[data-name]'))image.src=image.dataset.name+'/'+e.target.value+'.png'
})</script></body></html>"""
    )
    (directory / "index.html").write_text(page, encoding="utf-8")
    home = output / "index.html"
    content = home.read_text(encoding="utf-8")
    if 'href="timing/index.html"' not in content:
        home.write_text(
            content.replace(
                "</header>",
                '<p><a href="timing/index.html">Variable BPM &amp; word subtitle demo →</a></p></header>',
            ),
            encoding="utf-8",
        )
    return catalog


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = build_site(args.output)
    print(f"Generated {len(result['scenes'])} timing demonstrations")
