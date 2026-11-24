"""Real RtMidi/JACK capture, owned dummy server and native 60 fps export proof."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qualify_osc_controls_m15 import create_source  # noqa: E402
from framerudiments import Project, __version__  # noqa: E402
from framerudiments.compositor import Compositor  # noqa: E402
from framerudiments.encoding import binaries, probe  # noqa: E402
from framerudiments.midi_controls_m15 import MIDIControlCapture  # noqa: E402
from framerudiments.midi_protocol_m15 import input_ports  # noqa: E402
from framerudiments.preview_stream_profile import temporary_preflight  # noqa: E402
from framerudiments.render import preflight, render  # noqa: E402

BINDINGS = [
    {'message': 'cc', 'channel': 1, 'controller': 7, 'node': 'gain',
     'property': 'opacity', 'minimum': 0, 'maximum': 1},
    {'message': 'pitchbend', 'channel': 16, 'node': 'frame',
     'property': 'opacity', 'minimum': .5, 'maximum': 1},
]
CHANGES = {0: (32, 8192), 60: (96, 16383), 120: (51, 4096), 180: (114, 12288)}


@contextmanager
def loopback(output):
    if sys.platform != 'linux' or not (3, 11) <= sys.version_info < (3, 13):
        raise RuntimeError('Native MIDI qualification needs Linux Python 3.11/3.12 and JACK dummy')
    import rtmidi
    jackd = shutil.which('jackd')
    if not jackd or rtmidi.API_UNIX_JACK not in rtmidi.get_compiled_api():
        raise RuntimeError('Required native JACK server/API is unavailable; no skip or fallback')
    name = 'frame-midi-' + uuid4().hex[:12]
    producer_name = 'FrameMIDIProducer-' + uuid4().hex[:8]
    previous = {key: os.environ.get(key) for key in ('JACK_DEFAULT_SERVER', 'JACK_NO_START_SERVER')}
    os.environ.update(JACK_DEFAULT_SERVER=name, JACK_NO_START_SERVER='1')
    server = sender = None
    port_name = None
    cleanup = {'server_name': name, 'rtmidi_apis': rtmidi.get_compiled_api(),
               'server_exited': False, 'producer_deleted': False}
    try:
        with (output / 'jack-server.log').open('wb') as log:
            command = [jackd, '--no-realtime', '--name', name, '-d', 'dummy', '-r', '48000', '-p', '256']
            server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            cleanup.update(pid=server.pid, process_group=os.getpgid(server.pid), command=command)
            deadline = monotonic() + 10
            while sender is None:
                if server.poll() is not None or monotonic() >= deadline:
                    raise RuntimeError('Owned JACK dummy server did not become ready; inspect its retained log')
                try:
                    sender = rtmidi.MidiOut(rtapi=rtmidi.API_UNIX_JACK, name=producer_name)
                except (OSError, RuntimeError):
                    sleep(.1)
            assert sender.get_current_api() == rtmidi.API_UNIX_JACK
            sender.open_virtual_port('controls')
            names = input_ports('jack')['input_names']
            candidates = [port for port in names if producer_name in port and port.endswith(':controls')]
            assert len(candidates) == 1, names
            port_name = candidates[0]
            yield sender, port_name, rtmidi, cleanup
    finally:
        try:
            if sender is not None:
                try:
                    sender.close_port()
                finally:
                    sender.delete()
                cleanup['producer_deleted'] = True
                if port_name is not None:
                    assert port_name not in input_ports('jack')['input_names']
                    cleanup['producer_absent_after_delete'] = True
        finally:
            try:
                if server is not None:
                    if server.poll() is None:
                        server.terminate()
                    try:
                        server.wait(timeout=5)
                        cleanup['shutdown'] = 'terminated and waited'
                    except subprocess.TimeoutExpired:
                        os.killpg(server.pid, signal.SIGKILL)
                        server.wait(timeout=5)
                        cleanup['shutdown'] = 'owned group killed after deadline'
                    cleanup['returncode'] = server.returncode
                    cleanup['server_exited'] = server.poll() is not None
                (output / 'native-ownership.json').write_text(json.dumps(cleanup, indent=2), encoding='utf-8')
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


def source_for(output):
    source = create_source(output / 'inputs')
    data = deepcopy(source.data)
    data['title'] = 'Synthetic native MIDI control capture and replay'
    data['clock']['authority'] = 'synthetic MIDI qualification'
    return Project(data, source.base)


def capture_native(source, output):
    with loopback(output) as (sender, port_name, rtmidi, cleanup):
        destinations_before = sender.get_ports()
        capture = MIDIControlCapture(source, BINDINGS, api='jack', port_name=port_name, lease_frames=45)
        with capture:
            for frame in range(source.frame_count):
                if frame in CHANGES:
                    cc, bend = CHANGES[frame]
                    # Independent producer uses standard MIDI 1.0 status/data
                    # bytes. RtMidi arrival delta never supplies the media time.
                    sender.send_message([0xb0, 7, cc])
                    sender.send_message([0xef, bend & 127, bend >> 7])
                    assert capture.wait_for_input(2, minimum_messages=2), 'MIDI messages did not arrive'
                capture.sample(frame)
        destinations_after = sender.get_ports()
        assert destinations_after == destinations_before, 'Owned receiver port survived close/delete'
        cleanup['receiver_destinations_restored'] = True
        take = capture.freeze()
        assert capture.receiver.closed and capture.receiver.native is None
        assert take.evidence['event_count'] == 8
        for event in take.document()['events']:
            assert event['frame'] in CHANGES
            assert event['source_seconds'] == source.source_time(event['frame'])
            assert event['beat'] == source.continuous_beat(event['frame'])
    ownership = json.loads((output / 'native-ownership.json').read_text())
    assert ownership['server_exited'] and ownership['producer_deleted']
    assert ownership['producer_absent_after_delete']
    return take, rtmidi


def attach(source, take):
    reference = take.write(source.base / 'midi-take.json')
    data = deepcopy(source.data)
    data['midi_control_take'] = {'path': 'midi-take.json', 'sha256': reference['sha256']}
    path = source.base / 'project.json'
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return Project.load(path)


def native_demo(source, take, output):
    project = attach(source, take)
    ffmpeg, ffprobe = binaries()
    baseline, controlled = Compositor(source), Compositor(project)
    try:
        image = Image.new('RGB', (960, 316), '#102030')
        draw = ImageDraw.Draw(image)
        draw.text((18, 10), 'Native movement / unchanged base', fill='white')
        draw.text((498, 10), 'Same source clock / MIDI CC + pitchbend', fill='white')
        image.paste(baseline.frame(30)[0], (0, 32))
        image.paste(controlled.frame(30)[0], (480, 32))
        draw.text((18, 303), 'Synthetic control take / 60 fps / four native temporal samples / device-free replay',
                  fill='white')
        image.save(output / 'comparison.png')
        a, context = controlled.frame(30)
        controlled.frame(150)
        assert controlled.frame(30)[0].tobytes() == a.tobytes()
        assert context.source_seconds == source.source_time(30)
        assert context.beat == source.continuous_beat(30)
        for frame in (46, 59, 106, 166, 226, 239):
            assert controlled.frame(frame)[0].tobytes() == baseline.frame(frame)[0].tobytes()
    finally:
        controlled.close()
        baseline.close()
    start = monotonic()
    preflight(project, output / 'preflight', max_frames=8, ffmpeg=ffmpeg, ffprobe=ffprobe)
    elapsed = monotonic() - start
    decision = {'prefix_frames': 8, 'prefix_seconds': elapsed,
                'estimated_seconds': elapsed/8*project.frame_count, 'owner': 'active invocation',
                'reason': 'Four-second fixture, measured estimate below 15 minutes'}
    (output/'render-ownership.json').write_text(json.dumps(decision, indent=2), encoding='utf-8')
    if decision['estimated_seconds'] > 900:
        raise RuntimeError('Native demo estimate exceeds active render scope')
    result = render(project, output/'render', ffmpeg=ffmpeg, ffprobe=ffprobe, preset='veryfast', crf=16)
    video = output/'render/video.mp4'
    stream = next(row for row in probe(video, ffprobe, count=True)['streams'] if row['codec_type'] == 'video')
    assert stream['r_frame_rate'] == stream['avg_frame_rate'] == '60/1'
    assert int(stream['nb_read_frames']) == project.frame_count == 240
    decoded = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(video), '-map', '0:v:0',
                                      '-f', 'framehash', '-hash', 'sha256', '-'], encoding='utf-8')
    hashes = [line.rsplit(',', 1)[1].strip() for line in decoded.splitlines() if line and not line.startswith('#')]
    assert len(hashes) == 240 and len(set(hashes)) >= 180
    assert all(a != b for a, b in zip(hashes, hashes[1:])), 'Duplicate adjacent decoded motion frames'
    project.verify_inputs()
    report = {'status': 'passed', 'synthetic': True, 'physical_controller_acceptance': False,
              'version': __version__, 'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                                                         text=True).strip(),
              'working_tree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
              'take': take.evidence, 'source_clock_unchanged': True, 'render_ownership': decision,
              'encoded_frames': 240, 'encoded_fps': '60/1', 'unique_decoded_frames': len(set(hashes)),
              'adjacent_duplicate_frames': 0, 'video_sha256': sha256(video.read_bytes()).hexdigest(),
              'composition': result['composition'], 'platform': sys.platform, 'python': sys.version,
              'replay_device_activity': False}
    (output/'qualification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (output/'decoded-framehash.txt').write_text(decoded, encoding='utf-8')
    (output/'index.html').write_text(PAGE, encoding='utf-8')
    return report


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M15.5 MIDI capture and replay</title><style>
body{background:#102030;color:#f5ede2;font:18px system-ui;max-width:1050px;margin:auto;padding:24px}
img,video{display:block;width:100%;height:auto}a{color:#ffcd78}
.player{position:relative}.player button{position:absolute;inset:0 0 52px;background:transparent;
border:0;color:white;font:24px system-ui;cursor:pointer}.player button[hidden]{display:none}</style>
<h1>Keeping MIDI controls on the video clock</h1>
<p>Synthetic qualification: real RtMidi/JACK virtual ports on an owned dummy server, CC and pitchbend
values, closed input and server, then a repeatable native export. Physical-controller acceptance is separate.</p>
<figure><img src="comparison.png" alt="Native movement at base opacity and with captured MIDI CC plus pitchbend">
<figcaption>Actual FrameRudiments frames at output frame 30. Both retain the same source clock.</figcaption></figure>
<div class="player"><video id="demo" controls playsinline preload="metadata" poster="comparison.png"
src="render/video.mp4"></video><button id="start" type="button" aria-label="Play MIDI replay demonstration">Play demonstration</button></div>
<script>const v=document.getElementById('demo'),b=document.getElementById('start');
b.addEventListener('click',()=>{v.play().catch(()=>{b.hidden=false})});
v.addEventListener('playing',()=>{b.hidden=true});v.addEventListener('pause',()=>{b.hidden=false});
v.addEventListener('ended',()=>{b.hidden=false});v.addEventListener('click',e=>{
if(!v.paused&&e.clientY<v.getBoundingClientRect().bottom-52)v.pause()});</script>
<p>Four seconds at 60 fps with four native temporal samples per output frame. Changes occur at frames
0, 60, 120 and 180. Each lease lasts through age 45, then the native base returns. Audio is silent.</p>
<p>The input requires an explicitly selected API and exact current port name. Native capture uses
Python 3.11/3.12 binary packages; replay also works on the normal Python 3.13 renderer without MIDI.</p>
<p><a href="qualification.json">Qualification</a> / <a href="native-ownership.json">Native cleanup</a> /
<a href="inputs/midi-take.json">Frozen take</a> / <a href="decoded-framehash.txt">Decoded frame hashes</a> /
<a href="../m15-5/">OSC demonstration</a></p></html>"""


def qualify(output):
    temporary_preflight(output.parent)
    output.mkdir()
    source = source_for(output)
    take, rtmidi = capture_native(source, output)
    # All native ports and the dummy server have already closed. These guards
    # make an accidental new device or socket an immediate qualification error.
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Replay attempted native MIDI or network activity')
    with patch.object(rtmidi, 'MidiIn', forbidden), patch.object(rtmidi, 'MidiOut', forbidden), \
            patch.object(socket, 'socket', forbidden):
        return native_demo(source, take, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(qualify(arguments.output), indent=2))
