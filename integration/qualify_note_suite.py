"""Native arrangement-first note suite, independent caches, pinned score and exports."""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import wave

ROOT = Path(__file__).resolve().parents[1]
if "--installed" not in sys.argv:
    sys.path.insert(0, str(ROOT / "src"))
from framerudiments import Project, __version__  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments.encoding import binaries, probe  # noqa: E402
from framerudiments.layer_frame_cache import LayerFrameCache  # noqa: E402
from framerudiments.midi_notes import import_midi_notes  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from framerudiments.render import render  # noqa: E402
from framerudiments.work_cache import cache_status  # noqa: E402
from qualify_layer_cache import counted  # noqa: E402


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def fixture(directory):
    import mido

    directory.mkdir()
    with wave.open(str(directory / "source.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b"\0\0" * 192000)
    font = Path(os.environ["FRAMERUDIMENTS_TEST_FONT"])
    shutil.copyfile(font, directory / "font.ttf")

    def pin(name):
        return dict(path=name, sha256=sha256((directory / name).read_bytes()).hexdigest())

    data = dict(
        schema="framerudiments.project.v1",
        purpose="visualiser",
        title="Authored five-part arrangement",
        width=960,
        height=540,
        fps=60,
        audio=dict(
            source=pin("source.wav"),
            sample_rate=48000,
            start_sample=0,
            end_sample=192000,
            repeats=1,
            fade_seconds=0,
        ),
        font=pin("font.ttf"),
        clock=dict(bpm=120, authority="Explicitly authored synthetic MIDI", status="synthetic"),
        background=dict(colour="#0c1929"),
        layers=[],
        render_quality=dict(enabled=True, spatial_scale=1, temporal_samples=1, antialias=0),
    )
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    tempo = mido.MidiTrack()
    tempo.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    midi.tracks.append(tempo)
    parts = [
        ("Bass", 0, [(i, k, 0.8, 110) for i, k in enumerate([36, 43, 41, 43])]),
        ("Chords", 1, [(i, k, 1.8, 82) for i, keys in [(0, [60, 64, 67]), (2, [57, 60, 65])] for k in keys]),
        ("Melody", 2, [(i * 0.5, k, 0.45, 110) for i, k in enumerate([72, 74, 76, 79, 77, 76, 74, 72])]),
        (
            "Drums",
            9,
            [(i * 0.5, 36 if i % 2 == 0 else 38, 0.05, 105) for i in range(8)]
            + [(i * 0.25, 42, 0.04, 65) for i in range(16)],
        ),
        ("Guitar", 3, [(i * 0.5, k, 0.4, 100) for i, k in enumerate([64, 67, 69, 72, 71, 69, 67, 64])]),
    ]
    for name, channel, events in parts:
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        midi.tracks.append(track)
        messages = []
        for start, key, duration, velocity in events:
            messages.extend(
                [(round(start * 960), 1, key, velocity), (round((start + duration) * 960), 0, key, 0)]
            )
        previous = 0
        for tick, on, key, velocity in sorted(messages):
            track.append(
                mido.Message(
                    "note_on" if on else "note_off",
                    channel=channel,
                    note=key,
                    velocity=velocity,
                    time=tick - previous,
                )
            )
            previous = tick
    midi.save(directory / "authored-parts.mid")
    take = import_midi_notes(Project(data, directory), pin("authored-parts.mid"), offset_seconds=0)
    note_pin = take.write(directory / "notes.json")
    note_pin = dict(path="notes.json", sha256=note_pin["sha256"])
    assert len(take.notes) == 50
    return data, take, note_pin


def projects(directory, data, take, pin):
    score_dir = ROOT / "integration/fixtures/note-suite-score"
    layout = json.loads((score_dir / "layout.json").read_text(encoding="utf-8"))
    pages, glyphs = [], []
    for index, page in enumerate(layout["pages"]):
        shutil.copyfile(score_dir / page["image"], directory / page["image"])
        assert sha256((directory / page["image"]).read_bytes()).hexdigest() == page["sha256"]
        pages.append(
            dict(
                image=dict(path=page["image"], sha256=page["sha256"]),
                start_seconds=page["start_seconds"],
                end_seconds=page["end_seconds"],
                label=f"Authored bar {index + 1}",
            )
        )
        for glyph in page["glyphs"]:
            match = [
                i
                for i, n in enumerate(take.notes)
                if n.track == glyph["authored"]["track"]
                and n.key == glyph["authored"]["key"]
                and n.start_seconds == glyph["authored"]["start_seconds"]
            ]
            assert len(match) == 1
            glyphs.append(dict(page=index, note_indices=match, bounds=glyph["bounds"]))
    fingering = dict(
        notes_sha256=pin["sha256"],
        tuning=[64, 59, 55, 50, 45, 40],
        fret_count=12,
        positions=[
            dict(note_index=i, string=1, fret=n.key - 64) for i, n in enumerate(take.notes) if n.track == 5
        ],
    )
    drums = dict(
        pads=[
            dict(id="kick", label="Kick", keys=[36], shape="circle", colour="#f6ad54"),
            dict(id="snare", label="Snare", keys=[38], shape="square", colour="#ef829f"),
            dict(id="hat", label="Hi-hat", keys=[42], shape="cross", colour="#52d6cf"),
        ]
    )
    views = [
        ("arrangement-scroll", dict(view="arrangement")),
        ("arrangement-overview", dict(view="arrangement", arrangement=dict(time_mode="overview"))),
        ("drum-lanes", dict(view="drum-lanes", tracks=[4], drums=drums)),
        ("pad-grid", dict(view="pad-grid", tracks=[4], drums=drums)),
        ("pitch-wheel", dict(view="pitch-wheel", tracks=[1, 2, 3, 5])),
        ("fretboard", dict(view="fretboard", tracks=[5], fretboard=fingering)),
        ("tablature", dict(view="tablature", tracks=[5], fretboard=fingering)),
        (
            "score",
            dict(
                view="score",
                tracks=[3],
                labels=True,
                score=dict(
                    notes_sha256=pin["sha256"],
                    reviewed=True,
                    provenance="Explicit two-bar C-major fixture, VexFlow 5.11.0 / Bravura; authored quarter-note spelling and glyph boxes",
                    pages=pages,
                    glyphs=glyphs,
                ),
            ),
        ),
        ("piano-roll", dict(view="piano-roll", tracks=[1, 2, 3, 5], note_range=[36, 84])),
        (
            "rising-notes",
            dict(view="piano-roll", direction="rising", tracks=[1, 2, 3, 5], note_range=[36, 84]),
        ),
        (
            "note-highway",
            dict(view="note-highway", tracks=[5], note_map={"64": 0, "67": 1, "69": 2, "71": 3, "72": 4}),
        ),
    ]
    result = {}
    for name, cfg in views:
        item = deepcopy(data)
        item["layers"] = [
            dict(
                id="notes",
                type="notes",
                file=pin,
                bounds=[0, 0, 960, 540],
                history_seconds=1,
                lookahead_seconds=3,
                colour_by="track",
                **deepcopy(cfg),
            )
        ]
        result[name] = item
    impacts = deepcopy(result["piano-roll"])
    impacts["layers"].append(
        dict(
            id="impacts",
            type="notes",
            view="note-impacts",
            tracks=[1, 2, 3, 5],
            note_range=[36, 84],
            file=pin,
            bounds=[0, 0, 960, 540],
            colour_by="track",
            impacts=dict(particles_per_note=18, style=dict(lifetime=0.9, size=4, seed=42)),
        )
    )
    result["note-impacts"] = impacts
    for name, item in result.items():
        write(directory / f"{name}.json", item)
    return result


def decode(video, ffmpeg, ffprobe):
    stream = next(s for s in probe(video, ffprobe, count=True)["streams"] if s["codec_type"] == "video")
    assert stream["r_frame_rate"] == stream["avg_frame_rate"] == "60/1"
    rows = subprocess.check_output(
        [ffmpeg, "-v", "error", "-i", str(video), "-map", "0:v:0", "-f", "framehash", "-hash", "sha256", "-"],
        encoding="utf-8",
    )
    hashes = [
        line.rsplit(",", 1)[1].strip() for line in rows.splitlines() if line and not line.startswith("#")
    ]
    assert len(hashes) == int(stream["nb_read_frames"]) and len(set(hashes)) > 10
    return hashes, dict(
        frames=len(hashes),
        fps="60/1",
        unique_frames=len(set(hashes)),
        adjacent_duplicates=sum(a == b for a, b in zip(hashes, hashes[1:])),
        sha256=sha256(video.read_bytes()).hexdigest(),
    )


def qualify(output):
    temporary_preflight(output.parent)
    output.mkdir()
    ffmpeg, ffprobe = binaries()
    data, take, pin = fixture(output / "inputs")
    candidates = projects(output / "inputs", data, take, pin)
    with TemporaryDirectory(prefix="note-suite-cache-", dir=output.parent) as directory:
        cache = LayerFrameCache(directory)
        try:
            comparisons = 0
            sample = perf_counter()
            for name, cfg in candidates.items():
                cold = Compositor(Project(cfg, output / "inputs"), layer_cache=cache)
                fresh = Compositor(Project(cfg, output / "inputs"), layer_cache=False)
                try:
                    for frame in (0, 75, 135, 75):
                        assert cold.frame(frame)[0].tobytes() == fresh.frame(frame)[0].tobytes()
                        comparisons += 1
                    cold.frame(75)[0].convert("RGB").save(output / (name + "-poster.png"))
                finally:
                    cold.close()
                    fresh.close()
            estimate = (perf_counter() - sample) / comparisons * (240 * 14 + 1200) * 2
            ownership = dict(
                owner="active invocation",
                estimated_seconds=estimate,
                sample_comparisons=comparisons,
                reason="Short synthetic demonstrations; includes uncached references and safety factor",
            )
            write(output / "render-ownership.json", ownership)
            assert estimate < 900, ownership
            paired = deepcopy(candidates["arrangement-overview"])
            paired["layers"][0]["bounds"] = [0, 0, 960, 360]
            pads = deepcopy(candidates["pad-grid"]["layers"][0])
            pads["id"] = "pads"
            pads["bounds"] = [0, 360, 960, 540]
            paired["layers"].append(pads)
            owner = Compositor(Project(paired, output / "inputs"), layer_cache=cache)
            try:
                for frame in range(60, 76):
                    owner.frame(frame)
            finally:
                owner.close()
            edited = deepcopy(paired)
            edited["layers"][0]["arrangement"]["group_by"] = "channel"
            changed = Compositor(Project(edited, output / "inputs"), layer_cache=cache)
            reference = Compositor(Project(edited, output / "inputs"), layer_cache=False)
            calls = counted(changed)
            try:
                for frame in range(60, 76):
                    assert changed.frame(frame)[0].tobytes() == reference.frame(frame)[0].tobytes()
                    comparisons += 1
                assert calls == {"notes": 16, "pads": 0}, calls
                changed.frame(75)[0].convert("RGB").save(output / "comparison.png")
            finally:
                changed.close()
                reference.close()
            for name, cfg in candidates.items():
                print("Native suite export: " + name, flush=True)
                render(
                    Project(cfg, output / "inputs"),
                    output / name,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    preset="veryfast",
                    crf=16,
                    layer_cache=cache,
                )
            render(
                Project(candidates["arrangement-scroll"], output / "inputs"),
                output / "reference",
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                preset="veryfast",
                crf=16,
                layer_cache=False,
            )
            print("Native pinned score working-cache export", flush=True)
            render(
                Project(candidates["score"], output / "inputs"),
                output / "score-staged",
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                preset="veryfast",
                crf=16,
                layer_cache=cache,
                work_cache=output / "working-cache",
                reserve_bytes=64 * 1024**2,
                min_free_bytes=0,
            )
            assert cache_status(output / "working-cache")["empty"]
            stats = cache.stats()
        finally:
            cache.close()
        reopened = LayerFrameCache(directory)
        owner = Compositor(Project(edited, output / "inputs"), layer_cache=reopened)
        reopening = counted(owner)
        try:
            owner.frame(75)
            assert reopening == {"notes": 0, "pads": 0}, reopening
        finally:
            owner.close()
            reopened.close()
    videos = {}
    decoded = {}
    for name in [*candidates, "reference", "score-staged"]:
        decoded[name], videos[name] = decode(output / name / "video.mp4", ffmpeg, ffprobe)
        assert videos[name]["frames"] == 240
    assert decoded["arrangement-scroll"] == decoded["reference"]
    assert decoded["score"] == decoded["score-staged"]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT))
    report = dict(
        schema="framerudiments.note-suite-qualification.v1",
        status="passed",
        version=__version__,
        source_revision=revision,
        working_tree_dirty=dirty,
        synthetic=True,
        notes=len(take.notes),
        views=list(candidates),
        width=960,
        height=540,
        native_pixel_comparisons=comparisons,
        edited_draws=calls,
        reopened_draws=reopening,
        encoded_arrangement_equals_uncached=True,
        pinned_score_staged_equals_original=True,
        working_cache_empty=True,
        cache_temporary_removed=not Path(directory).exists(),
        render_ownership=ownership,
        videos=videos,
        cache=stats,
        score_engraver="VexFlow 5.11.0; explicit authored notation",
        device_activity=False,
    )
    write(output / "qualification.json", report)
    captions = {
        "arrangement-scroll": (
            "Scrolling arrangement",
            "Five named parts, pitch and duration bars, bounded history/lookahead and source playhead.",
        ),
        "arrangement-overview": (
            "Whole arrangement overview",
            "The complete four-second pattern stays in place while the playhead and sounding outlines move.",
        ),
        "drum-lanes": (
            "Drum lanes",
            "Explicit kick, snare and hi-hat identities; zero-length hits remain visible.",
        ),
        "pad-grid": ("Pad matrix", "Source note-ons illuminate authored pads with bounded decay."),
        "pitch-wheel": (
            "Pitch-class wheel",
            "Active pitch classes retain exact octave/track/channel voicing; chord labels are not inferred.",
        ),
        "fretboard": (
            "Authored fretboard",
            "Standard tuning and explicit string/fret positions for the guitar part.",
        ),
        "tablature": ("Scrolling tablature", "Authored fret numbers, duration tails and source playhead."),
        "score": (
            "Reviewed score follower",
            "Actual VexFlow engraving with authored spellings and source-bound glyph mappings. Progress is shown separately from notation spacing.",
        ),
        "note-impacts": (
            "Note-triggered particles",
            "Source attacks drive the existing native particle engine over the keyboard.",
        ),
        "piano-roll": ("Falling keyboard", "Existing bottom keyboard and note-duration view."),
        "rising-notes": ("Rising note history", "Existing causal note history."),
        "note-highway": (
            "Perspective note highway",
            "Existing rhythm-game presentation with an explicit five-lane map.",
        ),
    }
    figures = "".join(
        f'<figure class="{"wide" if name.startswith("arrangement") else ""}"><h2>{captions[name][0]}</h2><div class="player"><video id="v-{name}" controls muted playsinline preload="none" poster="{name}-poster.png" src="{name}/video.mp4"></video><button class="picture" data-video="v-{name}" aria-label="Play or pause {captions[name][0]}"></button></div><figcaption>{captions[name][1]}</figcaption></figure>'
        for name in candidates
    )
    html = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FrameRudiments arrangement and note views</title><style>body{margin:auto;padding:24px;max-width:1100px;background:#0b1423;color:#e5ebf5;font:17px/1.5 system-ui}a{color:#64ded7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:24px}.wide{grid-column:1/-1}figure{margin:0}img,video{display:block;max-width:100%;width:100%;height:auto}.player{position:relative;container-type:inline-size;background:#000}video{height:calc(100cqw * 9 / 16 + 64px);object-fit:contain;object-position:center top}.picture{position:absolute;inset:0 0 64px;width:100%;border:0;background:transparent;cursor:pointer}.picture:focus-visible{outline:3px solid #ffd078}figcaption{font-size:15px}h1{line-height:1.15}small{color:#b6c5d9}</style><h1>An arrangement, twelve ways to see its notes</h1><p>Actual FrameRudiments VERSION native output from one original authored five-part MIDI pattern: 50 notes in four seconds. Every video is encoded and fully decoded at 60 fps. Natural held/still frames are counted in the proof. The soundtrack is silent.</p><div class="grid">FIGURES</div><h2>Independent layers</h2><img src="comparison.png" alt="Actual arrangement overview above its independently cached pad matrix"><p>Editing the arrangement redraws its 16 sampled frames and reuses the pad layer. Reopening the cache reuses both. The complete arrangement export matches its uncached reference; staging both pinned score pages preserves decoded output.</p><p><a href="qualification.json">Native checks, cadence and media hashes</a> · <a href="https://github.com/kieransimkin/FrameRudiments/blob/vVERSION/docs/note-suite.md">Input contracts and examples</a> · <a href="../note-visualisers/">Original keyboard and highway demonstration</a></p><small>Original synthetic material. No transcription, inferred fingering/chords, live-controller or physical-phone acceptance is implied. Score engraved using VexFlow 5.11.0 (MIT), with bundled Bravura (SIL OFL 1.1).</small><script>document.querySelectorAll('.picture').forEach(b=>b.addEventListener('click',()=>{const v=document.getElementById(b.dataset.video);if(v.paused){v.play()}else{v.pause()}}));</script></html>""".replace(
        "VERSION", __version__
    ).replace("FIGURES", figures)
    (output / "index.html").write_text(html + "\n", encoding="utf-8")
    print(
        json.dumps(
            dict(
                status="passed",
                views=list(candidates),
                notes=len(take.notes),
                version=__version__,
                comparisons=comparisons,
            )
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    qualify(args.output.resolve())
