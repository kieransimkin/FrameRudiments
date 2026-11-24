"""Actual note-layer exports, selective cache reuse and optional native JACK capture."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
if "--installed" not in sys.argv:
    sys.path.insert(0, str(ROOT / "src"))
from framerudiments import Compositor, LayerFrameCache, NoteTimeline, Project, __version__  # noqa: E402
from framerudiments.encoding import binaries  # noqa: E402
from framerudiments.midi_notes import MIDINoteCapture, NoteEvents  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from framerudiments.render import preflight, render  # noqa: E402
from qualify_layer_cache import counted, decoded, source  # noqa: E402

# Independently authored wire sequence; this is a synthetic performance, not a song transcription.
WIRE = {
    0: [[0x90, 60, 106], [0x90, 64, 82], [0x90, 67, 94]],
    15: [[0x91, 61, 100]],
    30: [[0x80, 60, 0], [0x80, 64, 0], [0x80, 67, 0]],
    45: [[0x81, 61, 0], [0x90, 62, 65]],
    60: [[0x80, 62, 0], [0x90, 65, 100], [0x90, 69, 86], [0x90, 72, 110]],
    70: [[0xB0, 64, 127]],
    90: [[0x80, 65, 0], [0x80, 69, 0], [0x80, 72, 0]],
    100: [[0x90, 67, 108]],
    105: [[0x90, 67, 65]],
    120: [[0xB0, 64, 0]],
    135: [[0x80, 67, 0]],
    150: [[0x80, 67, 0], [0x91, 70, 101]],
    180: [[0x81, 70, 0], [0x90, 64, 96], [0x90, 68, 80], [0x90, 71, 116]],
}
MAPPING = {
    str(key): lane
    for lane, keys in enumerate(((60, 65, 70), (61, 67), (62, 69), (64, 68), (71, 72)))
    for key in keys
}


def capture(source_project, output, native):
    if native:
        from qualify_midi_controls_m15 import loopback

        with loopback(output) as (sender, port, _rtmidi, cleanup):
            owner = MIDINoteCapture(source_project, api="jack", port_name=port)
            with owner:
                for frame in range(source_project.frame_count):
                    messages = WIRE.get(frame, [])
                    for raw in messages:
                        sender.send_message(raw)
                    if messages:
                        assert owner.wait_for_input(2, minimum_messages=len(messages))
                    owner.sample(frame)
            take = owner.freeze()
            assert owner.receiver.closed and owner.receiver.native is None
            assert owner.statistics["received_messages"] == sum(map(len, WIRE.values()))
        assert cleanup["producer_deleted"] and cleanup["server_exited"]
        return take, dict(
            kind="real RtMidi/JACK virtual ports",
            closed=True,
            native=cleanup,
            physical_controller_tested=False,
        )
    events = NoteEvents(source_project)
    for frame in sorted(WIRE):
        for raw in WIRE[frame]:
            events.add(frame / source_project.fps, raw)
    return events.finish(provenance={"kind": "authored synthetic wire sequence"}), dict(
        kind="authored synthetic wire sequence; no device capture", physical_controller_tested=False
    )


def qualify(output, native=False):
    temporary_preflight(output.parent)
    output.mkdir()
    ffmpeg, ffprobe = binaries()
    _path, data = source(output / "inputs")
    data["layers"] = []
    data["title"] = "Native keyboard, piano roll and perspective note highway"
    data["render_quality"] = dict(enabled=True, spatial_scale=2, temporal_samples=1, antialias=0)
    programme = Project(data, output / "inputs")
    take, capture_receipt = capture(programme, output, native)
    assert len(take.notes) == 14
    assert take.notes[3].key == 61 and take.notes[3].channel == 2
    assert any(n.release_seconds == 1.5 and n.end_seconds == 2 for n in take.notes)
    assert take.data["provenance"]["held_keys_closed_at_programme_end"] == 3
    pin = take.write(output / "inputs/notes.json")
    relative = dict(path="notes.json", sha256=pin["sha256"])
    projects = {}
    for name, settings in [
        ("piano-roll", dict(view="piano-roll", note_range=[48, 84])),
        ("rising-notes", dict(view="piano-roll", direction="rising", note_range=[48, 84])),
        ("note-highway", dict(view="note-highway", note_map=MAPPING)),
    ]:
        cfg = deepcopy(data)
        cfg["layers"] = [dict(id="notes", type="notes", file=relative, bounds=[0, 0, 480, 270], **settings)]
        path = output / "inputs" / f"{name}.json"
        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        projects[name] = path
    paired = deepcopy(data)
    paired["width"], paired["height"] = 960, 540
    paired["layers"] = [
        dict(
            id="keyboard",
            type="notes",
            file=relative,
            view="piano-roll",
            note_range=[48, 84],
            bounds=[0, 0, 480, 540],
        ),
        dict(
            id="highway",
            type="notes",
            file=relative,
            view="note-highway",
            note_map=MAPPING,
            bounds=[480, 0, 960, 540],
        ),
    ]
    paired_path = output / "inputs/paired.json"
    paired_path.write_text(json.dumps(paired, indent=2) + "\n", encoding="utf-8")
    samples = list(range(60, 92))
    parity = 0
    with TemporaryDirectory(prefix="notes-cache-", dir=output.parent) as directory:
        cache = LayerFrameCache(directory)
        try:
            cold = Compositor(Project.load(paired_path), layer_cache=cache)
            fresh = Compositor(Project.load(paired_path), layer_cache=False)
            ticks = perf_counter()
            try:
                for frame in samples:
                    assert cold.frame(frame)[0].tobytes() == fresh.frame(frame)[0].tobytes()
                    parity += 1
                elapsed = perf_counter() - ticks
                # Includes uncached reference, so this deliberately overestimates ordinary render cost.
                estimate = elapsed / len(samples) * 240 * 9
                assert estimate < 900
                ownership = dict(
                    owner="active invocation",
                    estimated_seconds=estimate,
                    frames_sampled=len(samples),
                    samples_include_reference=True,
                    reason="Four-second synthetic clips; measured estimate below 15 minutes",
                )
                (output / "render-ownership.json").write_text(
                    json.dumps(ownership, indent=2), encoding="utf-8"
                )
                cold.frame(75)[0].convert("RGB").save(output / "comparison.png")
            finally:
                cold.close()
                fresh.close()
            edited = deepcopy(paired)
            edited["layers"][0]["opacity"] = 0.65
            changed = Compositor(Project(edited, output / "inputs"), layer_cache=cache)
            reference = Compositor(Project(edited, output / "inputs"), layer_cache=False)
            calls = counted(changed)
            try:
                for frame in samples:
                    assert changed.frame(frame)[0].tobytes() == reference.frame(frame)[0].tobytes()
                    parity += 1
                assert calls == {"keyboard": 32, "highway": 0}, calls
            finally:
                changed.close()
                reference.close()
            for name, path in projects.items():
                print("Native note export: " + name, flush=True)
                preflight(
                    Project.load(path),
                    output / (name + "-preflight"),
                    max_frames=4,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    layer_cache=cache,
                )
                render(
                    Project.load(path),
                    output / name,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    preset="veryfast",
                    crf=16,
                    layer_cache=cache,
                )
            print("Uncached note reference export", flush=True)
            render(
                Project.load(projects["piano-roll"]),
                output / "reference",
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                preset="veryfast",
                crf=16,
                layer_cache=False,
            )
            print("Two-chunk and resume note export", flush=True)
            # Respect the existing eight-second minimum: two authored ten-second chunks.
            repeated = deepcopy(data)
            repeated["audio"]["repeats"] = 5
            repeated_source = Project(repeated, output / "inputs")
            rows = [
                {
                    **row,
                    **{
                        key: row[key] + cycle * 4
                        for key in ("start_seconds", "release_seconds", "end_seconds")
                    },
                }
                for cycle in range(5)
                for row in take.data["notes"]
            ]
            repeated_pin = NoteTimeline.from_notes(
                repeated_source,
                rows,
                provenance={"kind": "five authored repetitions of the synthetic note pattern"},
            ).write(output / "inputs/chunk-notes.json")
            repeated["layers"] = [
                dict(
                    id="notes",
                    type="notes",
                    file=dict(path="chunk-notes.json", sha256=repeated_pin["sha256"]),
                    view="note-highway",
                    note_map=MAPPING,
                    bounds=[0, 0, 480, 270],
                )
            ]
            chunk_project = Project(repeated, output / "inputs")
            render(
                chunk_project,
                output / "chunked",
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                chunked=True,
                chunk_seconds=10,
                preset="veryfast",
                crf=16,
                layer_cache=cache,
            )
            render(
                chunk_project,
                output / "chunked",
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                chunked=True,
                chunk_seconds=10,
                resume=True,
                preset="veryfast",
                crf=16,
                layer_cache=cache,
            )
            cache_stats = cache.stats()
        finally:
            cache.close()
        reopened = LayerFrameCache(directory)
        owner = Compositor(Project(edited, output / "inputs"), layer_cache=reopened)
        reopening = counted(owner)
        try:
            owner.frame(samples[0])
            assert reopening == {"keyboard": 0, "highway": 0}
        finally:
            owner.close()
            reopened.close()
    videos, rows = {}, {}
    for name in [*projects, "reference", "chunked"]:
        rows[name], videos[name] = decoded(
            output / name / "video.mp4",
            ffmpeg,
            ffprobe,
            frames=1200 if name == "chunked" else 240,
            min_unique=180 if name == "chunked" else 240,
        )
        assert (
            videos[name]["frames"] == (1200 if name == "chunked" else 240) and videos[name]["fps"] == "60/1"
        )
        assert videos[name]["adjacent_duplicates"] == 0, (name, videos[name])
        if name in projects:
            subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-i",
                    str(output / name / "video.mp4"),
                    "-vf",
                    r"select=eq(n\,75)",
                    "-frames:v",
                    "1",
                    str(output / (name + "-poster.png")),
                ],
                check=True,
            )
    assert rows["piano-roll"] == rows["reference"]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT))
    report = dict(
        schema="framerudiments.note-visualisers-qualification.v1",
        status="passed",
        version=__version__,
        source_revision=revision,
        working_tree_dirty=dirty,
        synthetic=True,
        capture=capture_receipt,
        notes=len(take.notes),
        note_semantics=take.data["provenance"],
        width=480,
        height=270,
        native_pixel_comparisons=parity,
        edited_draws=calls,
        reopened_draws=reopening,
        encoded_piano_equals_uncached=True,
        videos=videos,
        chunked_and_resume_passed=True,
        cache_temporary_removed=not Path(directory).exists(),
        render_ownership=ownership,
        cache=cache_stats,
    )
    (output / "qualification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    figures = "".join(
        f'''<figure><h2>{label}</h2><div class="player"><video id="v-{name}" controls muted playsinline preload="metadata" poster="{name}-poster.png" src="{name}/video.mp4"></video><button class="picture" aria-label="Play or pause {label}" data-video="v-{name}"></button></div><figcaption>{caption}</figcaption></figure>'''
        for name, label, caption in [
            (
                "piano-roll",
                "Falling piano roll",
                "Notes approach the bottom keyboard; key lighting follows physical note release, including black keys.",
            ),
            (
                "rising-notes",
                "Rising note history",
                "Causal history: blocks appear only after note-on and rise above the keyboard.",
            ),
            (
                "note-highway",
                "Perspective note highway",
                "Five explicitly mapped lanes with held-note tails and illuminated pads; this is a visualisation, not a scored game or guitar fingering.",
            ),
        ]
    )
    (output / "index.html").write_text(
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FrameRudiments note visualisers</title><style>body{margin:auto;padding:24px;max-width:1100px;background:#0b1423;color:#e5ebf5;font:17px/1.5 system-ui}a{color:#64ded7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:24px}figure{margin:0}img,video{display:block;max-width:100%;width:100%;height:auto}.player{position:relative}.picture{position:absolute;inset:0 0 48px;width:100%;border:0;background:transparent;cursor:pointer}.picture:focus-visible{outline:3px solid #ffd078}code{overflow-wrap:anywhere}figcaption{font-size:15px}h1{line-height:1.15}small{color:#b6c5d9}</style><h1>Notes, keys and a highway</h1><p>Actual synthetic FrameRudiments """
        + __version__
        + """ output. Each four-second clip has 240 distinct decoded frames at 60 fps. The audio is silent; no speech or real song is present.</p><div class="grid">"""
        + figures
        + """</div><h2>Same note timeline, independent layer caches</h2><img src="comparison.png" alt="Actual piano roll and perspective note highway at the same output time"><p>After editing only the keyboard opacity, its layer redraws 32 sampled frames and the highway redraws zero. Reopening the cache draws neither again. The entire piano export decodes to the same pixels as an uncached export.</p><p><a href="qualification.json">Native proof and exact media hashes</a> Ã‚Â· <a href="https://github.com/kieransimkin/FrameRudiments/blob/v"""
        + __version__
        + """/docs/note-visualisers.md">Guide</a></p><p><small>Pitch, duration and velocity are authored MIDI data. A five-lane map is explicitly authored; no real guitar fingering, transcription accuracy, physical-controller or physical-phone acceptance is implied.</small></p><script>document.querySelectorAll('.picture').forEach(b=>b.addEventListener('click',()=>{const v=document.getElementById(b.dataset.video);if(v.paused){v.play()}else{v.pause()}}));</script></html>""",
        encoding="utf-8",
    )
    print(
        json.dumps(
            dict(
                status="passed",
                version=__version__,
                notes=len(take.notes),
                views=list(projects),
                edited_draws=calls,
                encoded_parity=True,
                native_capture=native,
            )
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-midi", action="store_true")
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    qualify(args.output.resolve(), args.native_midi)
