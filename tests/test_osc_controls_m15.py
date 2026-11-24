"""Actual loopback datagrams, native parameter validation and network-free replay."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import struct
import threading
import wave

from PIL import Image, ImageDraw
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.effect_graph import EffectGraph
from framerudiments.osc_control_take_m15 import OSCControlTake, bindings_for
from framerudiments.osc_controls_m15 import OSCControlCapture
from framerudiments.osc_protocol_m15 import LoopbackReceiver, decode_message, endpoint


BINDINGS = [{"address": "/gain", "node": "gain", "property": "opacity"}]


@pytest.fixture
def source(tmp_path):
    with wave.open(str(tmp_path / "source.wav"), "wb") as stream:
        stream.setparams((1, 2, 48000, 48000, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 48000)
    sprite = Image.new("RGBA", (16, 16))
    ImageDraw.Draw(sprite).polygon([(1, 14), (8, 1), (15, 14)], fill="#f9b84b")
    sprite.save(tmp_path / "sprite.png")

    def ref(name):
        return {"path": name, "sha256": sha256((tmp_path / name).read_bytes()).hexdigest()}

    data = {
        "schema": "framerudiments.project.v1", "width": 64, "height": 48, "fps": 60,
        "purpose": "visualiser",
        "audio": {"source": ref("source.wav"), "sample_rate": 48000,
                  "start_sample": 12000, "end_sample": 36000, "repeats": 2, "fade_seconds": 0},
        "clock": {"authority": "synthetic OSC qualification", "status": "synthetic", "bpm": 120, "origin": .125},
        "background": {"colour": "#102030"},
        "layers": [{"id": "motion", "asset": ref("sprite.png"), "center": [32, 24], "width": 16,
                    "motion": {"kind": "named", "pattern": "circle", "repeat_clock": "continuous",
                               "interpolate": True}, "mapping": {"dx": 12, "dy": 9}}],
        "effect_graph": {"nodes": [
            {"id": "frame", "type": "source"},
            {"id": "gain", "type": "copy", "inputs": ["frame"], "opacity": 1},
            {"id": "background", "type": "source"},
            {"id": "out", "type": "matte", "inputs": ["gain", "background"]}], "output": "out"},
        "render_quality": {"enabled": True, "spatial_scale": 1, "temporal_samples": 1, "antialias": 0},
    }
    return Project(data, tmp_path)


def wire(value, address="/gain", tag="f"):
    # Test producer follows the published OSC format independently of the decoder.
    def string(text):
        raw = text.encode("ascii") + b"\0"
        return raw + b"\0" * (-len(raw) % 4)
    return string(address) + string("," + tag) + struct.pack(">" + tag, value)


def capture_take(source, *, lease=18):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        capture = OSCControlCapture(source, BINDINGS, peer=sender.getsockname(), lease_frames=lease)
        assert capture.endpoint is None and capture.receiver.socket is None
        with capture:
            for frame, value in ((0, .25), (30, .75)):
                sender.sendto(wire(value), capture.endpoint)
                assert capture.wait_for_input()
                assert capture.sample(frame)["accepted_addresses"] == ["/gain"]
            capture.sample(source.frame_count - 1)
        assert capture.receiver.closed and capture.receiver.socket is None
    return capture.freeze(), capture


def attached(source, take, *, factor=1):
    path = source.base / f"take-{factor}.json"
    reference = take.write(path)
    data = deepcopy(source.data)
    data["osc_control_take"] = reference
    data["render_quality"]["temporal_samples"] = factor
    return Project(data, source.base)


@pytest.mark.parametrize(("packet", "expected"), [
    (bytes.fromhex("2f6100002c690000fffffffe"), ("/a", -2)),
    (bytes.fromhex("2f6100002c6600003e800000"), ("/a", .25)),
    (wire(2147483647, tag="i"), ("/gain", 2147483647)),
])
def test_independent_wire_vectors(packet, expected):
    assert decode_message(packet) == expected


@pytest.mark.parametrize("packet", [
    b"", b"#bundle\0" + b"\0" * 12, b"/a\0\1,i\0\0" + b"\0" * 4,
    wire(.5, address="/a*"), wire(.5) + b"\0" * 4,
    wire(float("nan")), wire(float("inf")), b"/a\0\0,s\0\0abc\0",
    b"/a\0\0,ff\0" + b"\0" * 8, b"x" * 1028,
    b"/a\0\0,f\0\0\0", b"/a\0\0,f\0\0" + bytes.fromhex("ff800000"),
])
def test_reject_unsupported_or_malformed_packets(packet):
    with pytest.raises(ValueError):
        decode_message(packet)


@pytest.mark.parametrize("value", [
    ("0.0.0.0", 1234), ("192.168.1.2", 1234), ("localhost", 1234),
    ("::1", 1234), ("127.0.0.1", 0), ("127.0.0.1", True), ("127.0.0.1", 65536),
])
def test_only_explicit_loopback_peers(value):
    with pytest.raises(ValueError):
        endpoint(value)


def test_real_peer_filter_rejections_and_close(source):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
        sender.bind(("127.0.0.1", 0))
        stranger.bind(("127.0.0.1", 0))
        receiver = LoopbackReceiver(("127.0.0.1", 0), sender.getsockname())
        with receiver:
            address = receiver.bound
            stranger.sendto(wire(.8), address)
            sender.sendto(b"x" * 2048, address)
            sender.sendto(wire(.5), address)
            assert receiver.wait_ready()
            messages, peers, malformed = receiver.poll()
            assert messages == [("/gain", .5)] and peers == malformed == 1
        assert receiver.closed and receiver.socket is None
        with pytest.raises(RuntimeError):
            receiver.poll()
        with pytest.raises(RuntimeError):
            receiver.__enter__()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rebound:
            rebound.bind(address)


def test_packet_flood_closes_before_partial_publication(source, monkeypatch):
    import framerudiments.osc_protocol_m15 as protocol
    monkeypatch.setattr(protocol, "MAX_POLL_PACKETS", 2)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        with OSCControlCapture(source, BINDINGS, peer=sender.getsockname()) as capture:
            for value in (.25, .5, .75):
                sender.sendto(wire(value), capture.endpoint)
            assert capture.wait_for_input()
            with pytest.raises(RuntimeError, match="budget"):
                capture.sample(0)
            assert capture.receiver.closed and capture._history == []
        with pytest.raises(ValueError, match="final frame"):
            capture.freeze()


@pytest.mark.parametrize("rows", [
    [], [{"address": "/gain", "node": "gain", "property": "missing"}],
    BINDINGS * 2, [{"address": "/gain*", "node": "gain", "property": "opacity"}],
    [{"address": "/gain", "node": "out", "property": "alpha_source"}],
])
def test_bindings_require_unique_native_numeric_targets(source, rows):
    with pytest.raises(ValueError):
        bindings_for(source, EffectGraph(source.effect_graph_spec), rows)


def test_native_binding_collision_rejected_before_socket_open(source):
    source.data["effect_graph_controls"] = [{"node": "gain", "property": "opacity"}]
    with pytest.raises(ValueError, match="unique"):
        OSCControlCapture(source, BINDINGS, peer=("127.0.0.1", 1234))


def test_real_capture_lease_repeat_clock_and_resource_ownership(source):
    threads = set(threading.enumerate())
    take, capture = capture_take(source)
    assert set(threading.enumerate()) == threads
    assert capture.receiver.closed
    assert source.source_time(0) == source.source_time(30)
    events = take.document()["events"]
    assert [e["frame"] for e in events] == [0, 30]
    assert events[1]["beat"] > events[0]["beat"]
    assert take.values_at(0) == take.values_at(18) == {("gain", "opacity"): .25}
    assert take.values_at(19) == {} and take.values_at(29) == {}
    assert take.values_at(30) == {("gain", "opacity"): .75}
    assert take.values_at(49) == {}
    assert take.report_at(49)["expired_addresses"] == ["/gain"]
    with pytest.raises(ValueError, match="increasing"):
        capture.sample(59)  # A closed take cannot accept another sample.
    with pytest.raises(ValueError):
        take.values_at(True)


def test_freeze_requires_closed_complete_extent_and_rejects_invalid_values(source):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        capture = OSCControlCapture(source, BINDINGS, peer=sender.getsockname())
        with capture:
            with pytest.raises(ValueError, match="zero"):
                capture.sample(1)
            sender.sendto(wire(2.0), capture.endpoint)
            sender.sendto(wire(.2, address="/unknown"), capture.endpoint)
            sender.sendto(wire(.5), capture.endpoint)
            assert capture.wait_for_input()
            report = capture.sample(0)
            assert report["statistics"]["rejected_value"] == 1
            assert report["statistics"]["unknown_address"] == 1
            with pytest.raises(ValueError, match="Close"):
                capture.freeze()
        with pytest.raises(ValueError, match="final frame"):
            capture.freeze()


@pytest.mark.parametrize("change", ["clock", "slice", "repeats", "fps", "graph", "audio"])
def test_take_rejects_changed_source_identity(source, change):
    take, _ = capture_take(source)
    data = deepcopy(source.data)
    if change == "clock":
        data["clock"]["bpm"] = 121
    elif change == "slice":
        data["audio"]["start_sample"] += 1
    elif change == "repeats":
        data["audio"]["repeats"] = 3
    elif change == "fps":
        data["fps"] = 120
        data["fps_reason"] = "Higher sampling regression"
    elif change == "graph":
        data["effect_graph"]["nodes"][1]["opacity"] = .9
    else:
        data["audio"]["source"]["sha256"] = "a" * 64
        with pytest.raises(ValueError):
            Project(data, source.base)
        return
    changed = Project(data, source.base)
    with pytest.raises(ValueError, match="differs"):
        OSCControlTake(take.document(), changed, EffectGraph(changed.effect_graph_spec))


@pytest.mark.parametrize("change", ["duplicate", "timing", "bounds", "closed", "extent", "boolean", "order"])
def test_take_rejects_invalid_rows(source, change):
    take, _ = capture_take(source)
    document = take.document()
    if change == "duplicate":
        document["events"].insert(1, deepcopy(document["events"][0]))
    elif change == "timing":
        document["events"][0]["beat"] += .1
    elif change == "bounds":
        document["events"][0]["value"] = 2
    elif change == "closed":
        document["capture"]["receiver_closed"] = False
    elif change == "extent":
        document["capture"]["last_frame"] -= 1
    elif change == "boolean":
        document["events"][0]["frame"] = False
    else:
        document["events"].reverse()
    with pytest.raises(ValueError):
        OSCControlTake(document, source, EffectGraph(source.effect_graph_spec))


def test_atomic_no_overwrite_pin_and_input_ledger(source):
    take, _ = capture_take(source)
    project = attached(source, take)
    path = Path(project.osc_control_take_ref["path"])
    assert project.summary()["osc_control_take"]["sha256"] == sha256(path.read_bytes()).hexdigest()
    assert project.osc_control_take_ref in project.inputs
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        take.write(path)
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".osc-take-*.tmp"))
    path.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="changed"):
        project.verify_inputs()
    with pytest.raises(ValueError, match="pin"):
        OSCControlTake.load(project.osc_control_take_ref, project, EffectGraph(project.effect_graph_spec))


@pytest.mark.parametrize("factor", [1, 4, 8])
def test_native_compositor_replay_is_seekable_and_holds_each_encoded_frame(source, monkeypatch, factor):
    take, _ = capture_take(source)
    project = attached(source, take, factor=factor)
    native_values = []
    from framerudiments.osc_control_take_m15 import RecordedOSCControls
    original = RecordedOSCControls.values

    def observed(owner, context, graph):
        result = original(owner, context, graph)
        native_values.append((context.frame, owner.factor, result.copy()))
        return result

    monkeypatch.setattr(RecordedOSCControls, "values", observed)
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: pytest.fail("Replay opened a network socket"))
    comp = Compositor(project)
    try:
        first, context = comp.frame(0)
        comp.frame(30)
        again, _ = comp.frame(0)
        assert first.tobytes() == again.tobytes()
        assert context.source_seconds == source.source_time(0)
        assert context.beat == source.continuous_beat(0)
        for frame, scale, values in native_values:
            expected = .25 if frame // scale < 19 else .75
            assert values[("gain", "opacity")] == expected
        receipt = comp.evidence()["osc_controls"]
        assert receipt["native_subframes_per_encoded_frame"] == factor
        assert receipt["replay_network_activity"] is False
        assert receipt["last_used"]["encoded_frame"] == 0
        assert comp.frame(0)[0].tobytes() != comp.frame(1)[0].tobytes()  # Actual native movement.
    finally:
        comp.close()


def test_expiry_returns_exact_native_base_pixels_and_legacy_unchanged(source):
    take, _ = capture_take(source)
    controlled = Compositor(attached(source, take))
    baseline = Compositor(source)
    data = deepcopy(source.data)
    data["effect_graph"]["nodes"][1]["opacity"] = .25
    quarter = Compositor(Project(data, source.base))
    try:
        assert "osc_controls" not in baseline.evidence()
        assert controlled.frame(0)[0].tobytes() == quarter.frame(0)[0].tobytes()
        assert controlled.frame(0)[0].tobytes() != baseline.frame(0)[0].tobytes()
        for frame in (19, 29, 49, 59):
            assert controlled.frame(frame)[0].tobytes() == baseline.frame(frame)[0].tobytes()
    finally:
        for comp in (controlled, baseline, quarter):
            comp.close()


def test_cli_inspection_and_pinned_validation_open_no_socket(source, monkeypatch, capsys):
    from framerudiments.osc_m15_cli import main
    take, _ = capture_take(source)
    project = attached(source, take)
    path = source.base / "project.json"
    path.write_text(json.dumps(project.data), encoding="utf-8")
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: pytest.fail("CLI opened a socket"))
    assert main(["inspect"]) == 0
    assert json.loads(capsys.readouterr().out)["network_default"] == "closed"
    assert main(["validate", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "validated" and report["take"]["event_count"] == 2



def test_native_integer_control_requires_actual_int32(source):
    data = deepcopy(source.data)
    data["effect_graph"] = {"nodes": [{"id": "frame", "type": "source"},
        {"id": "key", "type": "chroma_key", "inputs": ["frame"], "key_colors": ["#00ff00"]}],
        "output": "key"}
    project = Project(data, source.base)
    rows = [{"address": "/channel", "node": "key", "property": "key_color.r"}]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        with OSCControlCapture(project, rows, peer=sender.getsockname()) as capture:
            sender.sendto(wire(128, address="/channel", tag="i"), capture.endpoint)
            assert capture.wait_for_input()
            capture.sample(0)
            sender.sendto(wire(128., address="/channel"), capture.endpoint)
            assert capture.wait_for_input()
            report = capture.sample(1)
            assert report["statistics"]["rejected_value"] == 1
            capture.sample(59)
    take = capture.freeze()
    assert take.values_at(0) == {("key", "key_color.r"): 128}
    assert type(take.document()["events"][0]["value"]) is int


def test_event_overflow_closes_without_committing_partial_sample(source, monkeypatch):
    import framerudiments.osc_controls_m15 as module
    monkeypatch.setattr(module, "MAX_EVENTS", 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        with OSCControlCapture(source, BINDINGS, peer=sender.getsockname()) as capture:
            sender.sendto(wire(.25), capture.endpoint)
            assert capture.wait_for_input()
            capture.sample(0)
            sender.sendto(wire(.75), capture.endpoint)
            assert capture.wait_for_input()
            with pytest.raises(RuntimeError, match="event budget"):
                capture.sample(1)
            assert capture.receiver.closed
            assert len(capture._history) == 1
            assert capture._last_frame == 0


def test_duplicate_json_and_oversize_reference_rejected(source):
    raw = b'{"schema":1,"schema":2}'
    path = source.base / "duplicate.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="Duplicate"):
        OSCControlTake.load({"path": str(path), "sha256": sha256(raw).hexdigest()}, source,
                            EffectGraph(source.effect_graph_spec))
    path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    data = deepcopy(source.data)
    data["osc_control_take"] = {"path": path.name, "sha256": "a" * 64}
    with pytest.raises(ValueError, match="budget"):
        Project(data, source.base)


def test_reformatted_take_receipt_and_reexport_preserve_actual_pinned_bytes(source):
    take, _ = capture_take(source)
    raw = json.dumps(take.document(), indent=2).encode("utf-8")
    path = source.base / "formatted-take.json"
    path.write_bytes(raw)
    pin = {"path": str(path), "sha256": sha256(raw).hexdigest()}
    loaded = OSCControlTake.load(pin, source, EffectGraph(source.effect_graph_spec))
    assert loaded.evidence["take_sha256"] == pin["sha256"]
    assert loaded.values_at(30) == {("gain", "opacity"): .75}
    exported = source.base / "exported-take.json"
    assert loaded.write(exported)["sha256"] == pin["sha256"]
    assert exported.read_bytes() == raw
