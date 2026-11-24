"""Real UDP capture, ordinary native export and independent 60 fps decode proof."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
from time import monotonic
import wave

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from framerudiments import Project, __version__  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments.encoding import binaries, probe  # noqa: E402
from framerudiments.osc_controls_m15 import OSCControlCapture  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from framerudiments.render import preflight, render  # noqa: E402


def create_source(directory):
    directory.mkdir()
    with wave.open(str(directory / "source.wav"), "wb") as stream:
        stream.setparams((1, 2, 48000, 192000, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 192000)
    sprite = Image.new("RGBA", (80, 80))
    draw = ImageDraw.Draw(sprite)
    draw.polygon([(40, 3), (76, 68), (4, 68)], fill="#f9b84b", outline="#fff1ca", width=3)
    draw.ellipse((32, 32, 48, 48), fill="#e25965")
    sprite.save(directory / "sprite.png")

    def ref(name):
        return {"path": name, "sha256": sha256((directory / name).read_bytes()).hexdigest()}

    data = {
        "schema": "framerudiments.project.v1", "width": 480, "height": 270, "fps": 60,
        "purpose": "visualiser", "title": "Synthetic OSC capture and replay qualification",
        "audio": {"source": ref("source.wav"), "sample_rate": 48000, "start_sample": 24000,
                  "end_sample": 120000, "repeats": 2, "fade_seconds": 0},
        "clock": {"authority": "synthetic OSC qualification", "status": "synthetic", "bpm": 120, "origin": .125},
        "background": {"colour": "#102030"},
        "layers": [{"id": "motion", "asset": ref("sprite.png"), "center": [240, 135], "width": 64,
                    "motion": {"kind": "named", "pattern": "circle", "repeat_clock": "continuous",
                               "interpolate": True}, "mapping": {"dx": 140, "dy": 80}}],
        "effect_graph": {"nodes": [
            {"id": "frame", "type": "source"},
            {"id": "gain", "type": "copy", "inputs": ["frame"], "opacity": 1},
            {"id": "background", "type": "source"},
            {"id": "out", "type": "matte", "inputs": ["gain", "background"]}], "output": "out"},
        "render_quality": {"enabled": True, "spatial_scale": 1, "temporal_samples": 4, "antialias": 0},
    }
    return Project(data, directory)


def qualify(output):
    temporary_preflight(output.parent)
    output.mkdir()  # No overwrite: each source revision owns a fresh gallery.
    ffmpeg, ffprobe = binaries()
    source = create_source(output / "inputs")
    changes = {0: .25, 60: .75, 120: .4, 180: .9}
    bindings = [{"address": "/gain", "node": "gain", "property": "opacity"}]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        capture = OSCControlCapture(source, bindings, peer=sender.getsockname(), lease_frames=45)
        with capture:
            for frame in range(source.frame_count):
                if frame in changes:
                    # Independent OSC 1.0 float32 producer; no encoder roundtrip.
                    sender.sendto(b"/gain\0\0\0,f\0\0" + struct.pack(">f", changes[frame]), capture.endpoint)
                    assert capture.wait_for_input(), "Loopback datagram was not received"
                capture.sample(frame)
        take = capture.freeze()
    assert capture.receiver.closed and capture.receiver.socket is None
    reference = take.write(source.base / "control-take.json")
    data = deepcopy(source.data)
    data["osc_control_take"] = {"path": "control-take.json", "sha256": reference["sha256"]}
    path = source.base / "project.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    project = Project.load(path)
    project.verify_inputs()

    baseline, controlled = Compositor(source), Compositor(project)
    try:
        poster = Image.new("RGB", (960, 316), "#102030")
        draw = ImageDraw.Draw(poster)
        draw.text((18, 10), "Native DanceRudiments motion / base opacity", fill="white")
        draw.text((498, 10), "Same source clock / captured OSC opacity 0.25", fill="white")
        poster.paste(baseline.frame(30)[0], (0, 32))
        poster.paste(controlled.frame(30)[0], (480, 32))
        draw.text((18, 303), "Synthetic fixture / 60 fps / native temporal samples: 4 / no network during replay",
                  fill="white")
        poster.save(output / "comparison.png")
        first, context = controlled.frame(30)
        controlled.frame(150)
        assert controlled.frame(30)[0].tobytes() == first.tobytes()
        assert context.source_seconds == source.source_time(30)
        assert context.beat == source.continuous_beat(30)
        for frame in (46, 59, 106, 166, 226, 239):
            assert controlled.frame(frame)[0].tobytes() == baseline.frame(frame)[0].tobytes()
    finally:
        controlled.close()
        baseline.close()

    started = monotonic()
    preflight(project, output / "preflight", max_frames=8, ffmpeg=ffmpeg, ffprobe=ffprobe)
    prefix_seconds = monotonic() - started
    estimated_seconds = prefix_seconds / 8 * project.frame_count
    decision = {"prefix_frames": 8, "prefix_seconds": prefix_seconds,
                "estimated_seconds": estimated_seconds, "owner": "active session",
                "reason": "Four-second synthetic qualification; measured estimate below 15 minutes"}
    (output / "render-ownership.json").write_text(json.dumps(decision, indent=2), encoding="utf-8")
    if estimated_seconds > 900:
        raise RuntimeError("Qualification estimate exceeds active-session render scope")
    result = render(project, output / "render", ffmpeg=ffmpeg, ffprobe=ffprobe, preset="veryfast", crf=16)
    video = output / "render/video.mp4"
    stream = next(s for s in probe(video, ffprobe, count=True)["streams"] if s["codec_type"] == "video")
    assert stream["r_frame_rate"] == stream["avg_frame_rate"] == "60/1"
    assert int(stream["nb_read_frames"]) == project.frame_count == 240
    decoded = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(video), "-map", "0:v:0",
                                       "-f", "framehash", "-hash", "sha256", "-"], encoding="utf-8")
    hashes = [line.rsplit(",", 1)[1].strip() for line in decoded.splitlines() if line and not line.startswith("#")]
    assert len(hashes) == 240 and len(set(hashes)) >= 180
    assert all(a != b for a, b in zip(hashes, hashes[1:])), "Duplicate adjacent decoded motion frames"
    project.verify_inputs()
    report = {"status": "passed", "synthetic": True, "musical_or_hardware_acceptance": False,
              "version": __version__, "revision": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
              "source_clock_unchanged": True, "take": take.evidence,
              "render_ownership": decision, "encoded_frames": 240, "encoded_fps": "60/1",
              "unique_decoded_frames": len(set(hashes)), "adjacent_duplicate_frames": 0,
              "video_sha256": sha256(video.read_bytes()).hexdigest(),
              "composition": result["composition"], "platform": sys.platform,
              "python": sys.version, "network_replay": "closed; frozen file only"}
    (output / "qualification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "decoded-framehash.txt").write_text(decoded, encoding="utf-8")
    (output / "index.html").write_text("""<!doctype html><html lang="en"><meta charset="utf-8">
<title>M15.5 OSC capture and replay</title><style>
body{background:#102030;color:#f5ede2;font:18px system-ui;max-width:1050px;margin:auto;padding:24px}
img,video{width:100%;height:auto}a{color:#ffcd78}
.player{position:relative}.player button{position:absolute;inset:0 0 52px;background:transparent;
border:0;color:white;font:24px system-ui;cursor:pointer}.player button[hidden]{display:none}</style>
<h1>Native motion with a captured OSC take</h1>
<p>Synthetic qualification: real loopback UDP, source-frame numeric bindings, closed receiver,
repeatable native export. This is not hardware, LAN or musical acceptance.</p>
<figure><img src="comparison.png" alt="Same native movement at base opacity and captured quarter opacity">
<figcaption>Actual FrameRudiments frames at output frame 30. Both use the same native source clock.</figcaption></figure>
<div class="player"><video id="demo" controls playsinline preload="metadata"
poster="comparison.png" src="render/video.mp4"></video>
<button id="start" type="button" aria-label="Play OSC replay demonstration">Play demonstration</button></div>
<script>const v=document.getElementById('demo'),b=document.getElementById('start');
b.addEventListener('click',()=>{v.play().catch(()=>{b.hidden=false})});
v.addEventListener('playing',()=>{b.hidden=true});v.addEventListener('pause',()=>{b.hidden=false});
v.addEventListener('ended',()=>{b.hidden=false});v.addEventListener('click',e=>{
if(!v.paused&&e.clientY<v.getBoundingClientRect().bottom-52)v.pause()});</script>
<p>Four seconds at 60 fps. Opacity changes at frames 0, 60, 120 and 180; each lease lasts through
age 45, then the native base returns. Synthetic audio is silent. Controls are applied once per
encoded frame while the native shutter samples movement four times.</p>
<p><a href="qualification.json">Qualification and hash receipt</a> /
<a href="inputs/control-take.json">Frozen take</a> /
<a href="decoded-framehash.txt">Independent decoded frame hashes</a></p>
</html>""", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "version", "encoded_fps", "encoded_frames",
                                                  "unique_decoded_frames", "adjacent_duplicate_frames")}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    qualify(args.output.resolve())


if __name__ == "__main__":
    main()
