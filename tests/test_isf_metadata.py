from hashlib import sha256
import json
from pathlib import Path

import pytest

from framerudiments.cli import main
from framerudiments.isf_metadata import MAX_SHADER_BYTES, inspect_isf

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/isf-tint.fs"


def shader(tmp_path, metadata, body="void main() {}"):
    path = tmp_path / "fixture.fs"
    path.write_bytes(("/*" + json.dumps(metadata) + "*/\n" + body).encode("utf-8"))
    return path


def test_inspects_pinned_source_and_preserves_control_order():
    source = EXAMPLE.read_bytes()
    digest = sha256(source).hexdigest()
    report = inspect_isf(EXAMPLE, expected_sha256=digest.upper())
    assert report["schema"] == "framerudiments.isf-metadata.v1"
    assert report["scope"] == "metadata_only"
    assert report["source"] == {"sha256": digest, "bytes": len(source)}
    assert report["kind"] == "filter" and report["passes"] == 1
    assert report["inputs"] == [
        {"name": "inputImage", "type": "image"},
        {"name": "amount", "type": "float", "label": "Tint amount",
         "minimum": 0.0, "maximum": 1.0, "default": 0.25, "identity": 0.0},
        {"name": "tint", "type": "color", "default": [0.2, 0.6, 1.0, 1.0]},
        {"name": "enabled", "type": "bool", "default": True},
    ]
    report["inputs"][2]["default"][0] = 0
    assert inspect_isf(EXAMPLE)["inputs"][2]["default"][0] == 0.2


def test_typed_generator_and_integer_choices(tmp_path):
    path = shader(tmp_path, {"ISFVSN": "2.0", "PASSES": [{}], "IMPORTED": {}, "INPUTS": [
        {"NAME": "origin", "TYPE": "point2D", "MIN": [-10, -8], "DEFAULT": [4, 2]},
        {"NAME": "shape", "TYPE": "long", "MIN": -1, "MAX": 2, "DEFAULT": -1,
         "VALUES": [-1, 2, 2], "LABELS": ["Square", "Circle", "Ring"]},
        {"NAME": "enabled", "TYPE": "bool", "DEFAULT": 0.0},
    ]})
    report = inspect_isf(path)
    assert report["kind"] == "generator"
    assert report["inputs"][0]["default"] == [4.0, 2.0]
    assert "maximum" not in report["inputs"][0]
    assert report["inputs"][1]["choices"] == [
        {"value": -1, "label": "Square"}, {"value": 2, "label": "Circle"}, {"value": 2, "label": "Ring"},
    ]
    assert report["inputs"][2]["default"] is False


@pytest.mark.parametrize("progress", [
    {"NAME": "progress", "TYPE": "float", "MIN": 0, "MAX": 1, "DEFAULT": 0.5},
    {"NAME": "progress", "TYPE": "float"},
])
def test_transition_metadata_with_normalized_progress(tmp_path, progress):
    path = shader(tmp_path, {"ISFVSN": "2", "INPUTS": [
        {"NAME": "startImage", "TYPE": "image"}, {"NAME": "endImage", "TYPE": "image"}, progress,
    ]})
    assert inspect_isf(path)["kind"] == "transition"


@pytest.mark.parametrize(("entry", "message"), [
    ({"NAME": "TIME", "TYPE": "float"}, "NAME"),
    ({"NAME": "gl_color", "TYPE": "color"}, "NAME"),
    ({"NAME": "inputImage", "TYPE": "float"}, "NAME"),
    ({"NAME": "leading space", "TYPE": "float"}, "NAME"),
    ({"NAME": "sound", "TYPE": "audioFFT"}, "TYPE"),
    ({"NAME": "sound", "TYPE": "audio"}, "TYPE"),
    ({"NAME": "hit", "TYPE": "event"}, "TYPE"),
    ({"NAME": "image", "TYPE": "image", "DEFAULT": "local.png"}, "Unknown"),
    ({"NAME": "value", "TYPE": "float", "MAX": True}, "finite"),
    ({"NAME": "value", "TYPE": "float", "MAX": 10**400}, "finite shader"),
    ({"NAME": "value", "TYPE": "float", "DEFAULT": float("inf")}, "Non-finite"),
    ({"NAME": "value", "TYPE": "float", "MIN": 0, "DEFAULT": -1}, "outside"),
    ({"NAME": "origin", "TYPE": "point2D", "MIN": [0, 1], "MAX": [1, 0]}, "MIN exceeds MAX"),
    ({"NAME": "tint", "TYPE": "color", "DEFAULT": [0, 0.5, 1]}, "4 components"),
    ({"NAME": "tint", "TYPE": "color", "DEFAULT": [0, 0.5, 1, 2]}, "outside"),
    ({"NAME": "enabled", "TYPE": "bool", "DEFAULT": "false"}, "boolean"),
    ({"NAME": "choice", "TYPE": "long", "DEFAULT": 2**31}, "integer"),
    ({"NAME": "choice", "TYPE": "long", "VALUES": [0], "LABELS": []}, "equally sized"),
    ({"NAME": "choice", "TYPE": "long", "VALUES": [0], "LABELS": ["A"], "DEFAULT": 1}, "in VALUES"),
])
def test_rejects_invalid_control_metadata(tmp_path, entry, message):
    with pytest.raises(ValueError, match=message):
        inspect_isf(shader(tmp_path, {"ISFVSN": "2", "INPUTS": [entry]}))


@pytest.mark.parametrize(("data", "message"), [
    ({}, "ISFVSN"),
    ({"ISFVSN": "2.1"}, "ISFVSN"),
    ({"ISFVSN": "2", "IMPORTED": {"still": {"PATH": "missing.png"}}}, "imported resources"),
    ({"ISFVSN": "2", "PASSES": [{"TARGET": "buffer"}, {}]}, "single pass"),
    ({"ISFVSN": "2", "PASSES": [{"PERSISTENT": True}]}, "single pass"),
    ({"ISFVSN": "2", "PASSES": [{"WIDTH": "$WIDTH / 2"}]}, "single pass"),
    ({"ISFVSN": "2", "INPUTS": {}}, "INPUTS must be an array"),
    ({"ISFVSN": "2", "INPUTS": [{}] * 65}, "up to 64"),
    ({"ISFVSN": "2", "CATEGORIES": "test"}, "CATEGORIES"),
    ({"ISFVSN": "2", "INPUTS": [{"NAME": "amount", "TYPE": "float"}] * 2}, "Duplicate"),
    ({"ISFVSN": "2", "INPUTS": [{"NAME": "startImage", "TYPE": "image"}]}, "image inputs"),
    ({"ISFVSN": "2", "INPUTS": [
        {"NAME": "startImage", "TYPE": "image"}, {"NAME": "endImage", "TYPE": "image"},
        {"NAME": "progress", "TYPE": "float", "DEFAULT": 1.1},
    ]}, "outside"),
])
def test_rejects_unsupported_metadata(tmp_path, data, message):
    with pytest.raises(ValueError, match=message):
        inspect_isf(shader(tmp_path, data))


@pytest.mark.parametrize(("source", "message"), [
    (b"/*{\"ISFVSN\":\"2\",\"ISFVSN\":\"2\"}*/void main() {}", "Duplicate JSON key"),
    (b"/*{\"ISFVSN\":\"2\",\"INPUTS\": [}*/void main() {}", "Expecting value"),
    (b"// leading comment\n/*{\"ISFVSN\":\"2\"}*/void main() {}", "leading JSON"),
    (b"/*{\"ISFVSN\":\"2\"}*/ ", "fragment body"),
    (b"/*{\"ISFVSN\":\"2\",\"INPUTS\":" + b"[" * 3000 + b"]" * 3000 + b"}*/void main() {}",
     r"nested too deeply|INPUTS\[0\] must be an object"),
    (b"/*{\"ISFVSN\":\"2\"}*/ " + b"a" * MAX_SHADER_BYTES, "exceeds"),
    (b"/*{\"ISFVSN\":\"2\"}*/ \xff", "utf-8"),
])
def test_invalid_or_unbounded_file(tmp_path, source, message):
    path = tmp_path / "bad.fs"
    path.write_bytes(source)
    with pytest.raises(ValueError, match=message):
        inspect_isf(path)


def test_body_is_opaque_to_discovery(tmp_path):
    path = shader(tmp_path, {"ISFVSN": "2", "DESCRIPTION": "Résumé"},
                  '#include "missing.fs"\nThis is intentionally invalid GLSL.')
    raw = b"\xef\xbb\xbf\n" + path.read_bytes()
    path.write_bytes(raw)
    report = inspect_isf(path)
    assert report["description"] == "Résumé"
    assert report["scope"] == "metadata_only"
    assert report["source"]["sha256"] == sha256(raw).hexdigest()


def test_cli_inspects_without_mutating_source_and_fails_on_hash_mismatch(capsys):
    raw = EXAMPLE.read_bytes()
    pin = sha256(raw).hexdigest()
    assert main(["inspect-isf", str(EXAMPLE), "--expect-sha256", pin]) == 0
    result = capsys.readouterr()
    assert not result.err and json.loads(result.out)["source"]["sha256"] == pin
    assert main(["inspect-isf", str(EXAMPLE), "--expect-sha256", "0" * 64]) == 2
    result = capsys.readouterr()
    assert not result.out and "SHA-256 mismatch" in result.err
    assert EXAMPLE.read_bytes() == raw
