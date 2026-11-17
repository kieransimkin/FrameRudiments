"""M9.2 GL Transitions: pinned bytes, native graph binding, optional real EGL."""

from hashlib import sha256
from pathlib import Path

from PIL import Image
import pytest

from framerudiments.gl_transitions import (
    MAX_SHADER_BYTES, _local_fragment, render_gl_transition, validate_gl_transition,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/gl-transition-crossfade.glsl"


def config(path=EXAMPLE, **overrides):
    result = {
        "transition_style": "gl_transition", "shader_path": str(path),
        "shader_sha256": sha256(path.read_bytes()).hexdigest(), "trusted_shader": True,
        "progress": 0.5, "time_seconds": 1.25, "time_delta_seconds": 1 / 24,
        "frame_index": 30,
    }
    result.update(overrides)
    return result


def images():
    a = Image.new("RGBA", (4, 3), (255, 0, 0, 120))
    b = Image.new("RGBA", (4, 3), (0, 40, 220, 255))
    return a, b


def test_bounded_source_and_uniform_wrapper_are_deterministic():
    cfg = config()
    validate_gl_transition(cfg)
    digest, fragment = _local_fragment(cfg)
    assert digest == cfg["shader_sha256"]
    assert fragment.startswith("#version 330")
    assert "vec2 uv = gl_FragCoord.xy / resolution" in fragment
    for uniform in ("progress", "TIME", "TIMEDELTA", "FRAMEINDEX", "RENDERSIZE", "ratio"):
        assert fragment.count(f"uniform {'int' if uniform == 'FRAMEINDEX' else 'vec2' if uniform == 'RENDERSIZE' else 'float'} {uniform};") == 1
    assert _local_fragment(cfg) == (digest, fragment)


@pytest.mark.parametrize("change,message", [
    ({"trusted_shader": False}, "trusted_shader"),
    ({"shader_sha256": "0" * 64}, "SHA-256 mismatch"),
    ({"shader_sha256": "bad"}, "64-hex"),
    ({"progress": -0.1}, "progress"),
    ({"progress": float("nan")}, "progress"),
    ({"time_seconds": float("inf")}, "time_seconds"),
    ({"time_delta_seconds": -0.1}, "time_delta_seconds"),
    ({"frame_index": 2.5}, "frame_index"),
    ({"feather": 0.2}, "native matte/geometry"),
])
def test_invalid_spec_rejected_before_gl(change, message):
    with pytest.raises(ValueError, match=message):
        validate_gl_transition(config(**change))


def test_wrong_shader_kind_rejects_gl_only_properties():
    with pytest.raises(ValueError, match="require gl_transition"):
        validate_gl_transition({"transition_style": "wipe", "time_seconds": 1})


@pytest.mark.parametrize("body,message", [
    (b"#include \"other.glsl\"\nvec4 transition(vec2 uv){return vec4(1.);}", "preprocessor"),
    (b"void main() {}\nvec4 transition(vec2 uv){return vec4(1.);}", "not main"),
    (b"vec4 other(vec2 uv){return vec4(1.);}", "transition\\(vec2"),
    (b"uniform vec3 TIME;\nvec4 transition(vec2 uv){return vec4(1.);}", "host uniform TIME"),
    (b"uniform sampler2D external;\nvec4 transition(vec2 uv){return vec4(1.);}", "unsupported shader uniform"),
    (b"vec4 transition(vec2 uv){return vec4(1.);}" + b" " * MAX_SHADER_BYTES, "source bytes"),
    (b"\xff\nvec4 transition(vec2 uv){return vec4(1.);}", "UTF-8"),
])
def test_shader_subset_rejects_unavailable_features(tmp_path, body, message):
    path = tmp_path / "other.glsl"
    path.write_bytes(body)
    with pytest.raises(ValueError, match=message):
        validate_gl_transition(config(path))


def test_endpoints_are_exact_and_validate_pin_even_without_gpu(tmp_path):
    a, b = images()
    first = render_gl_transition(None, a, b, config(progress=0))
    last = render_gl_transition(None, a, b, config(progress=1))
    assert first.tobytes() == a.tobytes() and first is not a
    assert last.tobytes() == b.tobytes() and last is not b
    with pytest.raises(RuntimeError, match="no CPU fallback"):
        render_gl_transition(None, a, b, config())
    cfg = config(progress=0)
    cfg["shader_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        render_gl_transition(None, a, b, cfg)
    with pytest.raises(ValueError, match="equal-sized"):
        render_gl_transition(None, a, b.resize((5, 3)), config(progress=0))


def test_gpu_bridge_receives_exact_explicit_uniform_values(monkeypatch):
    from framerudiments.effect_graph_gpu import GLMatteBackend
    from framerudiments import effect_graph_gpu_kernels

    backend = object.__new__(GLMatteBackend)
    backend.closed = False
    captured = {}
    a, b = images()
    def fake_execute(passed, inputs, steps, size, *, budget):
        captured.update(passed=passed, inputs=inputs, size=size, budget=budget)
        name, fragment, sources, uniforms = steps([])[0]
        captured.update(name=name, fragment=fragment, sources=sources, uniforms=uniforms)
        return Image.new("RGBA", size, (0, 0, 0, 0))
    monkeypatch.setattr(effect_graph_gpu_kernels, "_execute", fake_execute)
    render_gl_transition(backend, a, b, config())
    assert captured["passed"] is backend
    assert captured["inputs"] == [a, b]
    assert captured["size"] == (4, 3) and captured["budget"] == 3
    assert captured["sources"] == {"from_tex": 0, "to_tex": 1}
    assert captured["name"].endswith(config()["shader_sha256"])
    assert captured["uniforms"] == {
        "progress": .5, "ratio": 4 / 3, "resolution": (4.0, 3.0),
        "RENDERSIZE": (4.0, 3.0), "TIME": 1.25,
        "TIMEDELTA": 1 / 24, "FRAMEINDEX": 30,
    }


def test_graph_requires_trusted_shader_and_can_resolve_native_progress(monkeypatch):
    from framerudiments.effect_graph import EffectGraph
    from framerudiments import gl_transitions
    cfg = config(progress=0.1)
    graph = EffectGraph({"nodes": [
        {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
        {"id": "mix", "type": "transition", "inputs": ["a", "b"], **cfg},
    ], "output": "mix"})
    with pytest.raises(ValueError, match="transition node"):
        EffectGraph({"nodes": [{"id": "a", "type": "source", "shader_path": cfg["shader_path"]}],
                     "output": "a"})
    graph.validate_control_target("mix", "progress")
    graph.validate_control_target("mix", "time_seconds")
    resolved = graph._resolved_configs({("mix", "progress"): 0.75,
                                       ("mix", "time_seconds"): 2.0})
    assert resolved["mix"]["progress"] == 0.75
    assert resolved["mix"]["time_seconds"] == 2.0
    assert graph.nodes["mix"].config["progress"] == 0.1
    seen = []
    def fake_render(backend, a, b, config):
        seen.append(config.copy())
        return b.copy()
    monkeypatch.setattr(gl_transitions, "render_gl_transition", fake_render)
    class Backend:
        last_dispatch = None
    a, b = images()
    result = graph.render({"a": a, "b": b}, gpu_backend=Backend(),
                          parameter_values={("mix", "progress"): .7})
    assert result.tobytes() == b.tobytes()
    assert seen[0]["progress"] == .7
    assert graph.nodes["mix"].config["progress"] == .1


def test_project_relative_source_registration_is_nonmutating(tmp_path):
    from framerudiments.gl_transitions import prepare_project_gl_transitions
    file = tmp_path / "crossfade.glsl"
    file.write_bytes(EXAMPLE.read_bytes())
    shader = config(file)
    shader["shader_path"] = file.name
    project_graph = {"nodes": [
        {"id": "a", "type": "source"},
        {"id": "b", "type": "source"},
        {"id": "cross", "type": "transition", "inputs": ["a", "b"], **shader},
    ], "output": "cross"}
    class Project:
        base = tmp_path
        data = {"effect_graph": project_graph}
        inputs = []
        def ref(self, reference):
            path = (self.base / reference["path"]).resolve()
            actual = sha256(path.read_bytes()).hexdigest()
            if actual != reference["sha256"]:
                raise ValueError("Input hash mismatch")
            result = {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}
            self.inputs.append(result)
            return result
    project = Project()
    effective = prepare_project_gl_transitions(project)
    assert effective is not project_graph
    assert effective["nodes"][-1]["shader_path"] == str(file.resolve())
    assert project_graph["nodes"][-1]["shader_path"] == file.name
    assert project.inputs[0]["sha256"] == sha256(file.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Input hash mismatch"):
        file.write_text("changed", encoding="utf-8")
        prepare_project_gl_transitions(Project())
