"""Generate an entirely synthetic styled-lyric and native word-indicator test gallery."""

import argparse
import html
import json
import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from framerudiments.compositor import TextBlock
from framerudiments.lyric_indicator import WordIndicator
from framerudiments.timeline import EventTrack
from framerudiments.word_timing import WordTrack

SCENES = (
    (
        "gold-foil",
        "Gold foil · multistop gradient & bevel",
        {
            "gradient": {
                "stops": [
                    "#75471e",
                    {"at": 0.36, "colour": "#ffde85"},
                    {"at": 0.65, "colour": "#9b682d"},
                    "#fff3c1",
                ],
                "angle": 90,
            },
            "bevel": {"depth": 3, "strength": 0.8},
        },
        None,
    ),
    (
        "neon-glass",
        "Glass neon · inner + outer glow",
        {
            "gradient": {"stops": ["#198bff", "#d9f4ff", "#8b40ff"], "angle": 18},
            "inner_glow": {"colour": "#ffffff", "radius": 3, "strength": 0.85},
            "outer_glow": {"colour": "#178eff", "radius": 10, "strength": 1},
        },
        None,
    ),
    (
        "metal-emboss",
        "Metal engraving · emboss & outline",
        {
            "gradient": {"stops": ["#434955", "#dee4ed", "#505b77"]},
            "emboss": {"depth": 3, "strength": 0.85, "angle": 125},
            "stroke": {"width": 2, "colour": "#060c1a"},
        },
        None,
    ),
    (
        "cinematic-shadow",
        "Cinematic · heavy stroke & soft shadow",
        {
            "stroke": {"width": 4, "colour": "#111325"},
            "drop_shadow": {"dx": 6, "dy": 7, "blur": 9, "opacity": 0.75},
        },
        None,
    ),
    (
        "flame-type",
        "Heat gradient · outline, shadow & glow",
        {
            "gradient": {
                "stops": [
                    "#611c24",
                    {"at": 0.27, "colour": "#ff3e18"},
                    {"at": 0.7, "colour": "#ffa526"},
                    "#ffffc1",
                ]
            },
            "outer_glow": {"radius": 7, "colour": "#fc5735", "strength": 0.75},
            "stroke": {"width": 2, "colour": "#6b1d1e"},
        },
        None,
    ),
    (
        "bounce-spark",
        "Word-following sparkle · native bounce",
        {"gradient": {"stops": ["#6cf1e8", "#dbecff", "#bc7ce8"]}},
        {
            "glyph": "spark",
            "colour": "#ffed95",
            "size": 21,
            "bounce": {"motion": {"kind": "named", "pattern": "bounce"}, "axis": "x", "amplitude": 18},
        },
    ),
    (
        "word-bursts",
        "Continuous sparks plus per-word bursts",
        {"gradient": {"stops": ["#d1edff", "#57cbea", "#83aaf9"]}, "outer_glow": {"radius": 4}},
        {
            "glyph": "orb",
            "size": 20,
            "bounce": {"motion": {"kind": "named", "pattern": "bounce"}, "axis": "x", "amplitude": 14},
            "particles": {
                "mode": "hybrid",
                "kind": "sparks",
                "rate": 26,
                "burst_count": 16,
                "lifetime_frames": 36,
                "speed": 2,
                "on_word": {
                    "colour": "#ffb341",
                    "burst_count": 22,
                    "rate_multiplier": 1.8,
                    "glow": 4,
                    "duration_frames": 17,
                },
            },
        },
    ),
    (
        "cue-reactive-smoke",
        "Constant smoke, cue-reactive colour and bursts",
        {"gradient": {"stops": ["#fff0bc", "#fea383", "#97e7ff"]}, "bevel": {"depth": 2}},
        {
            "glyph": "diamond",
            "size": 21,
            "bounce": {"motion": {"kind": "named", "pattern": "circle"}, "axis": "y", "amplitude": 22},
            "particles": {
                "mode": "hybrid",
                "kind": "smoke",
                "rate": 34,
                "burst_count": 0,
                "lifetime_frames": 55,
                "speed": 0.75,
                "cue_labels": ["*DROP*"],
                "on_word": {
                    "burst_count": 0,
                    "rate_multiplier": 1.2,
                    "colour": "#a1f4ff",
                    "duration_frames": 10,
                },
                "on_cue": {
                    "burst_count": 36,
                    "rate_multiplier": 2.5,
                    "colour": "#ff6f69",
                    "indicator_colour": "#ff4e8d",
                    "indicator_scale": 1.5,
                    "glow": 7,
                    "duration_frames": 30,
                },
            },
        },
    ),
)
FRAMES = (18, 36, 70, 105)


class DemoProject:
    def __init__(self, font):
        self.width, self.height, self.fps = 600, 260, 60
        self.frame_count = 150
        self.start_sample, self.end_sample, self.sample_rate, self.repeats = 0, 120000, 48000, 1
        self.base = Path(".")
        self.inputs = []
        self.audio_ref = {"sha256": "0" * 64}
        self.font_ref = {"path": str(font), "sha256": "synthetic CI font for visual testing"}
        self.clock = SimpleNamespace(beat_at=lambda t: t * 2)
        self.lyrics = [{"frame": 0, "text": "Lights follow every word", "cycle": 0, "source_order": 0}]
        self.words = [
            {
                "frame": n,
                "text": value,
                "cycle": 0,
                "word_index": j,
                "line_frame": 0,
                "line_order": 0,
                "source_order": j,
            }
            for j, (n, value) in enumerate(zip((5, 35, 65, 95), ("Lights", "follow", "every", "word")))
        ]
        self.cues = [{"frame": 70, "text": "[DROP: CHORUS]", "cycle": 0}]
        self.data = {}
        self._word_track = WordTrack(self.words)
        self._line_track = EventTrack(self.lyrics)
        self._cue_track = EventTrack(self.cues)

    def ref(self, value):
        raise ValueError("Public demo has no external sprite/font imports")

    def context(self, frame):
        line = self._line_track.at(frame)
        return SimpleNamespace(
            frame=frame,
            source_seconds=frame / 60,
            output_seconds=frame / 60,
            beat=frame / 30,
            lyric=line,
            word=self._word_track.at(frame, line),
            cue=self._cue_track.at(frame),
        )


def font_path():
    value = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    alternatives = [
        value,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    for name in alternatives:
        if name and Path(name).is_file():
            return Path(name)
    raise RuntimeError("Install a test font or set FRAMERUDIMENTS_TEST_FONT")


def render(scene, frame, project):
    ident, label, effects, indicator_config = scene
    background = Image.new("RGBA", (project.width, project.height), "#101629")
    gradient = Image.new("RGB", (1, project.height))
    pix = gradient.load()
    for j in range(project.height):
        pix[0, j] = (15 + j // 40, 27 + j // 11, 52 + j // 15)
    background.alpha_composite(gradient.resize(background.size).convert("RGBA"))
    dr = ImageDraw.Draw(background)
    for i in range(10):
        x = 25 + i * 56
        h = round(13 + (i * 31 + frame * 7) % 55)
        dr.rounded_rectangle((x, 252 - h, x + 21, 252), 3, fill=(39, 113 + i * 5, 185, 125))
    block = TextBlock(
        project,
        dict(
            box=[14, 85, 586, 184],
            font_size=43,
            min_font_size=31,
            colour="#e9e9ff",
            stroke=0,
            word_mode="progressive",
            effects=effects,
        ),
        label=ident,
    )
    state = project._word_track.state(frame, project.lyrics[0])
    if block.rich:
        block.rich.draw(
            background, block, project.lyrics[0]["text"], active=state["active"], completed=state["completed"]
        )
    indicator = None
    if indicator_config is not None:
        indicator = WordIndicator(project, indicator_config, project.context, block)
        indicator.draw(background, project.context(frame))
    dr = ImageDraw.Draw(background)
    dr.text((16, 14), label, fill="#e1f8ff")
    dr.text(
        (16, 38), f"frame {frame} · rendered native frame-addressed words · synthetic test", fill="#8ba9cb"
    )
    return background, indicator


def build_site(output):
    output = Path(output)
    path = output / "rich-lyrics"
    path.mkdir(parents=True, exist_ok=False)
    project = DemoProject(font_path())
    records = []
    for scene in SCENES:
        root = path / scene[0]
        root.mkdir()
        reports = []
        for j, frame in enumerate(FRAMES):
            image, indicator = render(scene, frame, project)
            image.convert("RGB").save(root / f"{j}.png")
            reports.append(indicator.frame_report if indicator else None)
        records.append(
            dict(name=scene[0], title=scene[1], effects=scene[2], indicator=scene[3], reports=reports)
        )
    manifest = dict(
        schema="framerudiments.synthetic-rich-lyric-gallery.v1",
        synthetic=True,
        private_media=False,
        measured_audio=False,
        frames=list(FRAMES),
        scenes=records,
    )
    (path / "catalogue.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    cards = "".join(
        f'<article data-scene="{html.escape(row["name"])}">'
        f'<img src="{html.escape(row["name"])}/0.png" alt="{html.escape(row["title"])}" '
        f'loading="lazy"><h2>{html.escape(row["title"])}</h2></article>'
        for row in records
    )
    page = (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FrameRudiments — Rich Lyric Effects</title><style>
:root{color-scheme:dark}body{background:#071222;color:#e8f3ff;font:16px system-ui;max-width:1300px;padding:26px;margin:auto}a{color:#78e7dc}h1{font-size:2rem;margin:12px 0 6px}p{color:#b7cede;max-width:1000px;line-height:1.5}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:16px}article{background:#10233a;border-radius:12px;overflow:hidden;border:1px solid #375574}article img{width:100%;display:block}h2{font-size:1rem;padding:5px 18px}input{accent-color:#7ff1d3}</style></head><body>
<a href="../index.html">← Visualisation Atlas</a><h1>Rich subtitle typography and bouncing word sprites</h1><p>Eight deterministic, synthetic typography/word-indicator scenes show multi-stop gradients, stroke, bevel, emboss, shadow, glows, native DanceRudiment bounce and continuous particles with separate word or musical-cue bursts, colour changes and density modulation. No analysed music, song lyrics or private artwork is used.</p>
<label>Sample moment <input id="frame" type="range" min="0" max="3" step="1" value="0"></label><span id="moment">1 / 4</span><p><a href="catalogue.json">Inspect demonstration configurations and per-frame evidence</a></p><section class="cards">"""
        + cards
        + """</section><script>document.querySelector('#frame').addEventListener('input',e=>{let n=e.target.value;document.querySelector('#moment').textContent=(+n+1)+' / 4';document.querySelectorAll('article').forEach(card=>{card.querySelector('img').src=card.dataset.scene+'/'+n+'.png'})})</script></body></html>"""
    )
    (path / "index.html").write_text(page, encoding="utf-8")
    homepage = output / "index.html"
    if homepage.exists():
        current = homepage.read_text(encoding="utf-8")
        link = '<p><a href="rich-lyrics/index.html">Rich lyric typography &amp; word sprites →</a></p>'
        if link not in current:
            current = current.replace("</header>", link + "</header>")
            homepage.write_text(current, encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = build_site(args.output)
    print(f"Rich lyrics: {len(output['scenes'])} scenes × {len(output['frames'])} frames")
