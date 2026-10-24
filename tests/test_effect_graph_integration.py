"""CPU compositor/graph handoff contracts; no project assets required."""
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_integration import (
    evaluate_effect_graph,
    validate_graph_sources,
)


def graph(nodes, output):
    return EffectGraph({"nodes": nodes, "output": output})


def test_reachable_sources_validated_before_render():
    g = graph([
        {"id": "layer:visible", "type": "source"},
        {"id": "missing", "type": "source"},
        {"id": "t", "type": "transition", "inputs": ["layer:visible", "missing"]},
    ], "t")
    with pytest.raises(ValueError, match="Unknown effect graph source: missing"):
        validate_graph_sources(g, {"layer:visible"})


def test_frame_source_is_always_valid_and_unreachable_source_not_required():
    g = graph([
        {"id": "frame", "type": "source"},
        {"id": "unused_missing", "type": "source"},
    ], "frame")
    validate_graph_sources(g, set())
    assert g.source_ids == frozenset({"frame"})


def test_inactive_valid_layer_is_transparent_not_stale():
    g = graph([
        {"id": "frame", "type": "source"},
        {"id": "layer:inactive", "type": "source"},
        {"id": "t", "type": "transition", "inputs": ["frame", "layer:inactive"],
         "progress": .5},
    ], "t")
    validate_graph_sources(g, {"layer:inactive"})
    opaque = Image.new("RGBA", (2, 2), (255, 70, 20, 255))
    out = evaluate_effect_graph(g, None, None, opaque, {})
    assert out.getpixel((0, 0)) == (255, 70, 20, 128)
    assert opaque.getpixel((0, 0)) == (255, 70, 20, 255)


def test_independent_provider_not_modified_by_merge():
    g = graph([
        {"id": "background", "type": "source"},
        {"id": "frame", "type": "source"},
        {"id": "out", "type": "transition", "inputs": ["background", "frame"],
         "progress": .5},
    ], "out")
    background = Image.new("RGBA", (2, 2), (20, 40, 60, 255))
    frame = Image.new("RGBA", (2, 2), (220, 140, 20, 255))
    output = evaluate_effect_graph(g, None, None, frame, {"background": background})
    assert output.getpixel((0, 0)) == (120, 90, 40, 255)
    assert background.getpixel((0, 0)) == (20, 40, 60, 255)
    assert frame.getpixel((0, 0)) == (220, 140, 20, 255)


def test_controls_are_resolved_per_frame_without_graph_mutation():
    g = graph([
        {"id": "a", "type": "source"},
        {"id": "b", "type": "source"},
        {"id": "t", "type": "transition", "inputs": ["a", "b"], "progress": 0},
    ], "t")
    class Controls:
        def values(self, ctx, current):
            assert current is g
            return {("t", "progress"): ctx.frame / 10}
    a = Image.new("RGBA", (1, 1), "red")
    b = Image.new("RGBA", (1, 1), "blue")
    begin = evaluate_effect_graph(g, Controls(), SimpleNamespace(frame=0), a, {"a": a, "b": b})
    end = evaluate_effect_graph(g, Controls(), SimpleNamespace(frame=10), a, {"a": a, "b": b})
    assert begin.getpixel((0, 0)) == (255, 0, 0, 255)
    assert end.getpixel((0, 0)) == (0, 0, 255, 255)
    assert g.nodes["t"].config["progress"] == 0


def test_invalid_frame_mode_rejected():
    g = graph([{"id": "frame", "type": "source"}], "frame")
    with pytest.raises(ValueError, match="RGBA"):
        evaluate_effect_graph(g, None, None, Image.new("RGB", (1, 1)), {})
