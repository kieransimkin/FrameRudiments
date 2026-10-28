"""M3.2: alpha provenance, explicit graph ordering, and deterministic controls."""

from types import SimpleNamespace

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_mattes import apply_matte, chroma_key


def graph(nodes, output="out"):
    return EffectGraph({"nodes": nodes, "output": output})


def rgba(colour=(200, 40, 10), alpha=240):
    return Image.new("RGBA", (1, 1), (*colour, alpha))


def test_alpha_lineage_inherited_through_unary_nodes_and_forks():
    g = graph([
        {"id": "source", "type": "source", "opacity": .8},
        {"id": "blurred", "type": "copy", "inputs": ["source"], "opacity": .5},
        {"id": "mask", "type": "source"},
        {"id": "processed", "type": "matte", "inputs": ["blurred", "mask"]},
        {"id": "out", "type": "matte", "inputs": ["blurred", "mask"], "alpha_source": "original"},
    ])
    sources = {"source": rgba(), "mask": rgba(alpha=128)}
    start = g.render(sources)
    # Source opacity 0.8 is part of this branch's original-alpha contract.
    assert abs(start.getpixel((0, 0))[3] - round(round(240 * .8) * 128 / 255)) <= 1
    processed = graph([
        {"id": "source", "type": "source", "opacity": .8},
        {"id": "blurred", "type": "copy", "inputs": ["source"], "opacity": .5},
        {"id": "mask", "type": "source"},
        {"id": "out", "type": "matte", "inputs": ["blurred", "mask"]},
    ]).render(sources)
    assert abs(processed.getpixel((0, 0))[3] * 2 - start.getpixel((0, 0))[3]) <= 1
    assert sources["source"].getpixel((0, 0))[3] == 240
    assert g.nodes["out"].config["alpha_source"] == "original"
    assert g.render(sources).tobytes() == start.tobytes()


def test_original_alpha_restores_matted_area_only_with_explicit_opt_in():
    g = graph([
        {"id": "s", "type": "source"},
        {"id": "m", "type": "source"},
        {"id": "a", "type": "matte", "inputs": ["s", "m"]},
        {"id": "out", "type": "matte", "inputs": ["a", "m"], "alpha_source": "original"},
    ])
    sources = {"s": rgba(alpha=200), "m": rgba(alpha=128)}
    restored = g.render(sources).getpixel((0, 0))[3]
    assert abs(restored - 100) <= 1
    current = g.render(sources, parameter_values={("out", "alpha_source"): "current"}).getpixel((0, 0))[3]
    assert abs(current - 50) <= 1
    assert g.render(sources).getpixel((0, 0))[3] == restored


def test_chroma_key_can_reference_current_or_original_alpha():
    g = graph([
        {"id": "s", "type": "source"},
        {"id": "reduced", "type": "copy", "inputs": ["s"], "opacity": .25},
        {"id": "out", "type": "chroma_key", "inputs": ["reduced"],
         "key_color": "#00ff00", "tolerance": 0, "softness": 0},
    ])
    source = {"s": rgba((230, 0, 0), 200)}
    assert g.render(source).getpixel((0, 0))[3] == 50
    assert g.render(source, parameter_values={("out", "alpha_source"): "original"}).getpixel((0, 0))[3] == 200
    assert g.render(source).getpixel((0, 0))[3] == 50


def test_merge_and_transition_reset_ambiguous_original_lineage():
    nodes = [
        {"id": "a", "type": "source"},
        {"id": "b", "type": "source"},
        {"id": "m", "type": "source"},
        {"id": "t", "type": "transition", "inputs": ["a", "b"], "progress": .5},
        {"id": "dimmed", "type": "copy", "inputs": ["t"], "opacity": .5},
        {"id": "out", "type": "matte", "inputs": ["dimmed", "m"], "alpha_source": "original"},
    ]
    g = graph(nodes)
    inputs = {"a": rgba((200, 0, 0), 240), "b": rgba((0, 0, 220), 100), "m": rgba(alpha=128)}
    # Result alpha of the transition is 170; it begins a *new* lineage.
    alpha = g.render(inputs).getpixel((0, 0))[3]
    assert abs(alpha - 85) <= 1
    # A composite uses the same reset rule, but retains its own alpha-over semantics.
    merged = graph([
        {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
        {"id": "m", "type": "source"},
        {"id": "c", "type": "composite", "inputs": ["a", "b"], "opacity": .5},
        {"id": "out", "type": "matte", "inputs": ["c", "m"], "alpha_source": "original"},
    ])
    result = merged.render(inputs)
    assert 0 < result.getpixel((0, 0))[3] < 128
    assert merged.render(inputs).tobytes() == result.tobytes()


def test_explicit_pre_and_post_matte_graphs_differ():
    source_a = rgba((255, 0, 0), 255)
    source_b = rgba((0, 0, 255), 255)
    mask = rgba(alpha=128)
    pre = graph([
        {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
        {"id": "m", "type": "source"},
        {"id": "masked", "type": "matte", "inputs": ["a", "m"]},
        {"id": "out", "type": "transition", "inputs": ["masked", "b"], "progress": .5},
    ])
    post = graph([
        {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
        {"id": "m", "type": "source"},
        {"id": "mixed", "type": "transition", "inputs": ["a", "b"], "progress": .5},
        {"id": "out", "type": "matte", "inputs": ["mixed", "m"]},
    ])
    inputs = {"a": source_a, "b": source_b, "m": mask}
    before, after = pre.render(inputs), post.render(inputs)
    assert before.getpixel((0, 0))[3] > after.getpixel((0, 0))[3]
    assert before.tobytes() != after.tobytes()
    assert source_a.getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("kind", ["matte", "chroma_key"])
@pytest.mark.parametrize("mode", [123, "bogus", True, [], None])
def test_invalid_alpha_modes_fail_early(kind, mode):
    nodes = [{"id": "s", "type": "source"}, {"id": "m", "type": "source"},
             {"id": "out", "type": kind, "inputs": ["s", "m"] if kind == "matte" else ["s"],
              "alpha_source": mode}]
    with pytest.raises(ValueError, match="alpha_source"):
        graph(nodes)


def test_alpha_source_control_options_are_validated_and_ephemeral():
    g = graph([{"id": "s", "type": "source"}, {"id": "m", "type": "source"},
               {"id": "out", "type": "matte", "inputs": ["s", "m"]}])
    g.validate_control_choices("out", "alpha_source", ["current", "original"])
    assert g.base_control_value("out", "alpha_source") == "current"
    assert g.is_categorical_control("out", "alpha_source")
    for invalid in ("bad", 2, True):
        with pytest.raises(ValueError, match="categorical"):
            g.render({"s": rgba(), "m": rgba()}, parameter_values={("out", "alpha_source"): invalid})
    with pytest.raises(ValueError, match="choices"):
        g.validate_control_choices("out", "alpha_source", ["original", "original"])


def test_original_alpha_helper_rejects_bad_image():
    with pytest.raises(ValueError, match="same-sized L"):
        apply_matte([rgba(), rgba()], {"alpha_source": "original"}, original_alpha=Image.new("RGBA", (1, 1)))
    with pytest.raises(ValueError, match="same-sized L"):
        chroma_key(rgba(), {"alpha_source": "original", "tolerance": 0}, original_alpha=Image.new("L", (3, 3)))


def test_categorical_native_dancerudiment_binding_for_alpha_mode(monkeypatch):
    from framerudiments.effect_graph_modulation import EffectGraphControls

    class Driver:
        def __init__(self, config, project, context_at):
            self.combine = "set"
            self.weight = 1
            self.motion = SimpleNamespace(files=[])

        def output(self, context):
            return (context.frame / 10, 0)

    monkeypatch.setattr("framerudiments.effect_graph_modulation._Modulator", Driver)
    g = graph([
        {"id": "s", "type": "source"}, {"id": "m", "type": "source"},
        {"id": "half", "type": "opacity", "inputs": ["s"], "opacity": .5},
        {"id": "out", "type": "matte", "inputs": ["half", "m"]},
    ])
    controls = EffectGraphControls(None, g, [{
        "node": "out", "property": "alpha_source", "choices": ["current", "original"], "motion": {},
    }], None)
    src = {"s": rgba(alpha=200), "m": rgba(alpha=128)}
    begin = g.render(src, parameter_values=controls.values(SimpleNamespace(frame=0), g))
    end = g.render(src, parameter_values=controls.values(SimpleNamespace(frame=10), g))
    assert abs(begin.getpixel((0, 0))[3] - 50) <= 1
    assert abs(end.getpixel((0, 0))[3] - 100) <= 1
    assert g.render(src).tobytes() == begin.tobytes()


def test_alpha_source_rejected_on_nodes_that_do_not_evaluate_a_mask():
    with pytest.raises(ValueError, match="only supported"):
        graph([{"id": "s", "type": "source", "alpha_source": "original"}], output="s")
