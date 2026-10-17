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
            envelope = min(1, t / 0.02, (4 - t) / 0.02)
            value = round(9000 * envelope * math.sin(2 * math.pi * 220 * t))
            data.extend(struct.pack("<hh", value, value))
        handle.writeframes(data)
    sprite = Image.new("RGBA", (160, 160))
    draw = ImageDraw.Draw(sprite)
    draw.ellipse((20, 20, 140, 140), fill="#76d4cf", outline="#f9edca", width=5)
    draw.line((50, 80, 70, 105, 115, 55), fill="#101b2e", width=12)
    sprite.save(directory / "sprite.png")
    (directory / "lyrics.lrc").write_text(
        "[00:00.00]Signals into motion\n[00:01.25]Words into light\n"
        "[00:02.50]Keep the clock in sight\n[00:03.75]\n",
        encoding="utf-8",
    )
    (directory / "sections.cue").write_text(
        'FILE "tone.wav" WAVE\nTRACK 01 AUDIO\nTITLE "opening"\n'
        'INDEX 01 00:00:00\nTRACK 02 AUDIO\nTITLE "closing"\nINDEX 01 00:02:00\n',
        encoding="utf-8",
    )
    clock = RecordingClock(120, "synthetic demo oscillator", "confirmed", 0, "supplied", 0)
    recording = Recording(
        "demo_bands",
        ("low", "mid", "high", "air"),
        tuple(tuple(math.sin(i / 24 + j) for j in range(4)) for i in range(512)),
        clock,
    )
    write_recording(directory / "recording.json", recording)

    def ref(name):
        path = directory / name
        return dict(path=name, sha256=sha256(path))

    def box(a, b, c, d):
        return [round(a * width), round(b * height), round(c * width), round(d * height)]

    text_size = max(8, round(min(width * 0.078, height * 0.045)))
    data = dict(
        schema="framerudiments.project.v1",
        title="FrameRudiments",
        artist="Synthetic demonstration",
        purpose="short",
        width=width,
        height=height,
        fps=60,
        audio=dict(
            source=ref("tone.wav"), sample_rate=rate, start_sample=0, end_sample=rate * 4, fade_seconds=0.12
        ),
        clock=dict(bpm=120, authority="synthetic oscillator", status="synthetic", phase_status="supplied"),
        font=dict(path=str(font), sha256=sha256(font)),
        lyrics=ref("lyrics.lrc"),
        cues=ref("sections.cue"),
        background=dict(colour="#101b2e"),
        identity=[
            dict(
                text="FrameRudiments",
                style=dict(
                    box=box(0.1, 0.13, 0.9, 0.23),
                    font_size=text_size,
                    min_font_size=max(6, text_size // 2),
                    stroke=0,
                    line_spacing=2,
                ),
            )
        ],
        captions=dict(
            box=box(0.12, 0.28, 0.88, 0.48),
            font_size=text_size,
            min_font_size=max(6, text_size // 2),
            stroke=0,
            line_spacing=2,
        ),
        layers=[
            dict(
                id="native",
                asset=ref("sprite.png"),
                center=[width * 0.5, height * 0.65],
                width=round(min(width * 0.33, height * 0.22)),
                bounds=box(0.1, 0.5, 0.9, 0.82),
                motion=dict(kind="named", pattern="bounce"),
                mapping=dict(dy=height * 0.035, y_axis=0),
            ),
            dict(
                id="bands",
                type="bars",
                bounds=box(0.15, 0.84, 0.85, 0.94),
                motion=dict(kind="recording", file=ref("recording.json")),
            ),
        ],
        notes="Synthetic audio and motion; no actual sung lyric or creative approval implied.",
    )
    write_json(directory / "project.json", data)
    return directory / "project.json"


def create_rudiment_demo(directory, font, *, width=600, height=960):
    """Four bounded views of native motion and a synthetic N-D recording."""
    path = create_demo(directory, font, width=width, height=height)
    from .io import read_json

    data = read_json(path)

    def box(a, b, c, d):
        return [round(a * width), round(b * height), round(c * width), round(d * height)]

    data["identity"][0]["style"]["box"] = box(0.08, 0.025, 0.92, 0.09)
    data["captions"]["box"] = box(0.08, 0.11, 0.92, 0.22)
    panels = [
        ("particles", (0.04, 0.30, 0.48, 0.58)),
        ("bars", (0.52, 0.30, 0.96, 0.58)),
        ("waveform", (0.04, 0.67, 0.48, 0.95)),
        ("path", (0.52, 0.67, 0.96, 0.95)),
    ]
    data["layers"] = []
    for name, bounds in panels:
        motion = (
            dict(
                kind="recording",
                file=dict(path="recording.json", sha256=sha256(path.parent / "recording.json")),
            )
            if name in {"bars", "waveform"}
            else dict(kind="named", pattern="circle")
        )
        data["layers"].append(
            dict(
                id=name,
                type="rudiment",
                renderer=name,
                motion=motion,
                bounds=box(*bounds),
                history_frames=120,
                style=dict(background="#192a40", padding=5),
            )
        )
        label_box = box(bounds[0], bounds[1] - 0.055, bounds[2], bounds[1] - 0.01)
        data["identity"].append(
            dict(
                text=name.upper(),
                style=dict(box=label_box, font_size=max(7, round(width * 0.025)), stroke=0, line_spacing=0),
            )
        )
    data["notes"] = "Synthetic rudiment visualisation; waveforms trace motion values, not PCM audio."
    write_json(path, data)
    return path


def create_spectrum_demo(directory, font, *, width=600, height=960):
    """Optional StemLab 1.6+ integration: actual synthetic Fourier sweep."""
    try:
        from stemlab.analysis.rudiment_recordings import fourier_axes, extract_recording
    except ImportError as exc:
        raise RuntimeError(
            "Spectrum demo needs StemLab 1.6+ in this Python environment; saved Fourier recordings need no StemLab import"
        ) from exc
    from .io import read_json

    path = create_demo(directory, font, width=width, height=height)
    rate, duration = 48000, 4
    growth = math.log(6000 / 80) / duration
    with wave.open(str(path.parent / "tone.wav"), "wb") as handle:
        handle.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        pcm = bytearray()
        for i in range(rate * duration):
            t = i / rate
            phase = 2 * math.pi * 80 / growth * (math.exp(growth * t) - 1)
            envelope = min(1, t / 0.02, (duration - t) / 0.02)
            value = round(18000 * envelope * math.sin(phase))
            pcm.extend(struct.pack("<hh", value, -value))
        handle.writeframes(pcm)
    audio = path.parent / "tone.wav"
    recording = extract_recording(
        fourier_axes(audio, 64, fft_size=4096, maximum=8000),
        name="synthetic_sweep_fourier",
        normalization="global",
        bpm=120,
        bpm_source="synthetic score",
        bpm_status="confirmed",
        beat_zero_seconds=0,
        phase_status="supplied",
    )
    write_recording(path.parent / "fourier.json", recording)
    data = read_json(path)

    def box(a, b, c, d):
        return [round(a * width), round(b * height), round(c * width), round(d * height)]

    data["audio"]["source"]["sha256"] = sha256(audio)
    data["identity"][0]["style"]["box"] = box(0.08, 0.03, 0.92, 0.10)
    data["captions"]["box"] = box(0.08, 0.13, 0.92, 0.23)
    motion = dict(
        kind="recording", file=dict(path="fourier.json", sha256=sha256(path.parent / "fourier.json"))
    )
    data["layers"] = [
        dict(
            id="spectrum",
            type="rudiment",
            renderer="spectrum",
            motion=motion,
            bounds=box(0.04, 0.30, 0.96, 0.72),
            style=dict(background="#192a40", min_peak_contrast_db=1),
        ),
        dict(
            id="band-traces",
            type="rudiment",
            renderer="waveform",
            motion=motion,
            axes=["band_000", "band_008", "band_024"],
            history_frames=180,
            bounds=box(0.04, 0.79, 0.96, 0.96),
            style=dict(background="#192a40"),
        ),
    ]
    data["identity"].append(
        dict(
            text="64 FOURIER BANDS / PEAK HOLD",
            style=dict(box=box(0.08, 0.25, 0.92, 0.29), font_size=max(8, round(width * 0.025)), stroke=0),
        )
    )
    data["identity"].append(
        dict(
            text="SELECTED BAND TRACES",
            style=dict(box=box(0.08, 0.74, 0.92, 0.78), font_size=max(8, round(width * 0.025)), stroke=0),
        )
    )
    data["notes"] = (
        "Actual StemLab Fourier analysis of a synthetic antiphase stereo sweep. Peaks are frequency bands, not pitch or audio true peaks."
    )
    write_json(path, data)
    return path


def add_demo_effects(path, engines, preset="feedback"):
    """Add both full-frame engines and a native moving animated atlas."""
    from .io import read_json

    path = Path(path)
    data = read_json(path)
    width, height = data["width"], data["height"]
    atlas = Image.new("RGBA", (256, 64))
    draw = ImageDraw.Draw(atlas)
    for index, colour in enumerate(("#76d4cf", "#edb56d", "#c9a4ff", "#f9edca")):
        left = index * 64
        draw.polygon([(left + 32, 6), (left + 58, 48 - index * 3), (left + 6, 48 - index * 3)], fill=colour)
        draw.ellipse((left + 25, 22, left + 39, 36), fill="#101b2e")
    atlas.save(path.parent / "atlas.png")
    data["layers"].append(
        dict(
            id="native-animated-atlas",
            type="sprite",
            asset=dict(path="atlas.png", sha256=sha256(path.parent / "atlas.png")),
            center=[width * 0.5, height * 0.55],
            width=round(width * 0.16),
            bounds=[0, 0, width, height],
            atlas=dict(columns=4, rows=1, count=4, cycle_ticks=16),
            motion=dict(kind="named", pattern="circle"),
            mapping=dict(dx=width * 0.12, dy=height * 0.05, rotation=12, x_axis=0, y_axis=1),
        )
    )
    data["effects"] = [
        dict(id=f"{engine}-{i}", engine=engine, preset=preset, input_mix=0.7)
        for i, engine in enumerate(engines)
    ]
    write_json(path, data)
    return path
