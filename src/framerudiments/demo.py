"""Self-contained synthetic demonstration: no private songs or third-party artwork."""
import math
from pathlib import Path
import struct
import wave

from PIL import Image, ImageDraw
from dancerudiments_authoring.recording import Recording, RecordingClock, write_recording

from .io import sha256, write_json


def create_demo(directory, font, *, width=360, height=640):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    font = Path(font).resolve()
    if not font.is_file():
        raise ValueError("Pass an installed font with --font")
    rate = 48000
    with wave.open(str(directory / "tone.wav"), "wb") as handle:
        handle.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        data = bytearray()
        for i in range(rate * 4):
            t = i / rate
            envelope = min(1, t / .02, (4 - t) / .02)
            value = round(9000 * envelope * math.sin(2 * math.pi * 220 * t))
            data.extend(struct.pack("<hh", value, value))
        handle.writeframes(data)
    sprite = Image.new("RGBA", (160, 160))
    draw = ImageDraw.Draw(sprite)
    draw.ellipse((20, 20, 140, 140), fill="#76d4cf", outline="#f9edca", width=5)
    draw.line((50, 80, 70, 105, 115, 55), fill="#101b2e", width=12)
    sprite.save(directory / "sprite.png")
    (directory / "lyrics.lrc").write_text("[00:00.00]Signals into motion\n[00:01.25]Words into light\n"
                                         "[00:02.50]Keep the clock in sight\n[00:03.75]\n", encoding="utf-8")
    (directory / "sections.cue").write_text('FILE "tone.wav" WAVE\nTRACK 01 AUDIO\nTITLE "opening"\n'
                                           'INDEX 01 00:00:00\nTRACK 02 AUDIO\nTITLE "closing"\nINDEX 01 00:02:00\n',
                                           encoding="utf-8")
    clock = RecordingClock(120, "synthetic demo oscillator", "confirmed", 0, "supplied", 0)
    recording = Recording("demo_bands", ("low", "mid", "high", "air"),
                          tuple(tuple(math.sin(i / 24 + j) for j in range(4)) for i in range(512)), clock)
    write_recording(directory / "recording.json", recording)
    def ref(name):
        path = directory / name
        return dict(path=name, sha256=sha256(path))
    def box(a, b, c, d):
        return [round(a * width), round(b * height), round(c * width), round(d * height)]
    text_size = max(8, round(min(width * .078, height * .045)))
    data = dict(schema="framerudiments.project.v1", title="FrameRudiments", artist="Synthetic demonstration",
                purpose="short", width=width, height=height, fps=60,
                audio=dict(source=ref("tone.wav"), sample_rate=rate, start_sample=0, end_sample=rate * 4,
                           fade_seconds=.12),
                clock=dict(bpm=120, authority="synthetic oscillator", status="synthetic", phase_status="supplied"),
                font=dict(path=str(font), sha256=sha256(font)), lyrics=ref("lyrics.lrc"), cues=ref("sections.cue"),
                background=dict(colour="#101b2e"),
                identity=[dict(text="FrameRudiments", style=dict(box=box(.1, .13, .9, .23), font_size=text_size,
                                                   min_font_size=max(6, text_size // 2), stroke=0, line_spacing=2))],
                captions=dict(box=box(.12, .28, .88, .48), font_size=text_size,
                              min_font_size=max(6, text_size // 2), stroke=0, line_spacing=2),
                layers=[dict(id="native", asset=ref("sprite.png"), center=[width * .5, height * .65],
                             width=round(min(width * .33, height * .22)), bounds=box(.1, .5, .9, .82),
                             motion=dict(kind="named", pattern="bounce"),
                             mapping=dict(dy=height * .035, y_axis=0)),
                        dict(id="bands", type="bars", bounds=box(.15, .84, .85, .94),
                             motion=dict(kind="recording", file=ref("recording.json")))],
                notes="Synthetic audio and motion; no actual sung lyric or creative approval implied.")
    write_json(directory / "project.json", data)
    return directory / "project.json"
