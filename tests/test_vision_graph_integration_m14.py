"""M14.2 opt-in effect graph source validation and byte-stable legacy routing."""
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.effect_graph_integration import evaluate_effect_graph, validate_graph_sources


class DummyGraph:
    def __init__(self, names):
        self.source_ids = frozenset(names)
        self.layer_style_ids = frozenset()
        self.last_inputs = None

    def render(self, images, **kwargs):
        self.last_inputs = images
        return images[sorted(self.source_ids)[0]].copy()


def test_opt_in_graph_estimate_is_supplied_as_real_rgba():
    graph = DummyGraph({"vision:foreground"})
    ink = Image.new("RGBA", (16, 16), (255, 255, 255, 150))
    provider = SimpleNamespace(source_ids={"vision:foreground"}, source=lambda key, frame: ink.copy())
    validate_graph_sources(graph, set(), extra_sources=provider.source_ids)
    result = evaluate_effect_graph(graph, None, SimpleNamespace(frame=4),
                                   Image.new("RGBA", (16, 16)), {}, vision_sources=provider)
    assert result.tobytes() == ink.tobytes()
    assert result is not ink
    assert graph.last_inputs["vision:foreground"] is not ink


def test_unknown_vision_source_does_not_silently_become_transparent():
    graph = DummyGraph({"vision:unknown"})
    with pytest.raises(ValueError, match="Unknown effect graph source"):
        validate_graph_sources(graph, set())
    with pytest.raises(ValueError, match="Unknown vision graph source"):
        evaluate_effect_graph(graph, None, SimpleNamespace(frame=2),
                              Image.new("RGBA", (16, 16)), {}, vision_sources=None)


def test_legacy_graph_evaluation_is_unchanged_when_not_opted_in():
    graph = DummyGraph({"frame"})
    original = Image.new("RGBA", (16, 16), (23, 40, 80, 255))
    result = evaluate_effect_graph(graph, None, SimpleNamespace(frame=0), original, {})
    assert result.tobytes() == original.tobytes()
    assert original.getpixel((0, 0)) == (23, 40, 80, 255)


def test_missing_valid_layer_remains_transparent():
    graph = DummyGraph({"layer:inactive"})
    validate_graph_sources(graph, {"layer:inactive"})
    result = evaluate_effect_graph(graph, None, SimpleNamespace(frame=1),
                                   Image.new("RGBA", (16, 16)), {})
    assert result.getbbox() is None


def test_legacy_graph_does_not_require_a_prediction_clock():
    graph = DummyGraph({"frame"})
    original = Image.new("RGBA", (16, 16), (23, 40, 80, 255))
    assert evaluate_effect_graph(graph, None, None, original, {}).tobytes() == original.tobytes()


def test_vision_source_without_context_fails_before_prediction():
    graph = DummyGraph({"vision:foreground"})
    provider = SimpleNamespace(source_ids={"vision:foreground"},
                               source=lambda *_: pytest.fail("Prediction started without a source clock"))
    with pytest.raises(ValueError, match="encoded-frame context"):
        evaluate_effect_graph(graph, None, None, Image.new("RGBA", (16, 16)), {},
                              vision_sources=provider)
