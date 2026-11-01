"""M5.5 deterministic beat-authoritative transition timing and control contract."""
from types import SimpleNamespace

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_transition_timing import (
    transition_timing_progress, validate_transition_timing,
)


def graph(timing=None):
    transition = {"id": "t", "type": "transition", "inputs": ["a", "b"], "progress": 0.2}
    if timing is not None:
        transition["timing"] = timing
    return EffectGraph({"nodes": [
        {"id": "a", "type": "source"}, {"id": "b", "type": "source"}, transition,
    ], "output": "t"})


def sources():
    return {"a": Image.new("RGBA", (2, 2), "red"),
            "b": Image.new("RGBA", (2, 2), "blue")}


def at(beat, source_beat=None):
    return SimpleNamespace(beat=beat, source_beat=source_beat)


def pixel(g, beat, *, source_beat=None, controls=None):
    return g.render(sources(), context=at(beat, source_beat),
                    parameter_values=controls).getpixel((0, 0))


def test_static_projects_remain_unchanged():
    g = graph()
    assert pixel(g, 0) == pixel(g, 4)
    assert g.render(sources()).tobytes() == g.render(sources(), context=at(50)).tobytes()


def test_beat_duration_and_exact_endpoints():
    g = graph({"start_beat": 8, "duration_beats": 4})
    assert pixel(g, -2) == (255, 0, 0, 255)
    assert pixel(g, 8) == (255, 0, 0, 255)
    assert pixel(g, 10) == (128, 0, 128, 255)
    assert pixel(g, 12) == (0, 0, 255, 255)
    assert pixel(g, 24) == (0, 0, 255, 255)
    assert g.nodes["t"].config["progress"] == .2


def test_variable_bpm_clock_is_supplied_as_absolute_beat_phase():
    g = graph({"start_beat": 1, "duration_beats": 2})
    # Beat 2 can occur at 0.75 or 1.5 seconds depending on tempo map.
    t1, t2 = at(2), at(2)
    assert g.render(sources(), context=t1).tobytes() == g.render(sources(), context=t2).tobytes()
    assert pixel(g, 2) == (128, 0, 128, 255)


def test_loop_gap_holds_final_and_resets_on_next_period():
    g = graph({"duration_beats": 2, "repeat": "loop", "period_beats": 4})
    assert pixel(g, 2) == (0, 0, 255, 255)
    assert pixel(g, 3.5) == (0, 0, 255, 255)
    assert pixel(g, 4) == (255, 0, 0, 255)
    assert pixel(g, 5) == (128, 0, 128, 255)


def test_pingpong_loops_reverse_without_boundary_discontinuity():
    g = graph({"duration_beats": 2, "repeat": "pingpong"})
    assert pixel(g, 0) == (255, 0, 0, 255)
    assert pixel(g, 2) == (0, 0, 255, 255)
    assert pixel(g, 3) == (128, 0, 128, 255)
    assert pixel(g, 4) == (255, 0, 0, 255)
    assert pixel(g, 5) == (128, 0, 128, 255)


def test_continuous_vs_source_clock_repeated_audio():
    continuous = graph({"start_beat": 4, "duration_beats": 4})
    source = graph({"clock": "source", "start_beat": 4, "duration_beats": 4})
    # The second audio cycle has a continuous beat ordinal of 12,
    # but source-relative beat 2, exactly as the project's clock supplies.
    assert pixel(continuous, 12, source_beat=2) == (0, 0, 255, 255)
    assert pixel(source, 12, source_beat=2) == (255, 0, 0, 255)
    assert pixel(source, 16, source_beat=6) == (128, 0, 128, 255)


def test_source_clock_requires_source_beat_not_fake_bpm():
    g = graph({"clock": "source"})
    with pytest.raises(ValueError, match="source beat"):
        pixel(g, 1)


@pytest.mark.parametrize("config,expected", [
    ({"preset": "pulse", "duration_beats": 4}, (255, 0, 0, 255)),
    ({"preset": "triangle", "duration_beats": 4}, (255, 0, 0, 255)),
    ({"reverse": True, "duration_beats": 4}, (255, 0, 0, 255)),
])
def test_reversible_or_nonmonotonic_preset_at_end(config, expected):
    assert pixel(graph(config), 4) == expected


def test_keyframes_can_move_backwards_and_repeat_without_history():
    cfg = {"duration_beats": 4, "keys": [
        {"at": 0, "value": 0}, {"at": .25, "value": 1},
        {"at": .5, "value": .25}, {"at": 1, "value": 1},
    ]}
    g = graph(cfg)
    assert pixel(g, 1) == (0, 0, 255, 255)
    assert pixel(g, 2) == (191, 0, 64, 255)
    a = pixel(g, 3)
    pixel(g, 0)
    assert pixel(g, 3) == a


def test_hold_and_smoothstep_keyframe_easing():
    g = graph({"duration_beats": 4, "keys": [
        {"at": 0, "value": 0, "ease": "hold"},
        {"at": .5, "value": 1, "ease": "smoothstep"},
        {"at": 1, "value": 0},
    ]})
    assert pixel(g, 1) == (255, 0, 0, 255)
    assert pixel(g, 2) == (0, 0, 255, 255)
    assert pixel(g, 4) == (255, 0, 0, 255)


def test_step_quantisation_and_phase_offset():
    g = graph({"duration_beats": 4, "phase_beats": 1, "steps": 3})
    assert pixel(g, 0) == (255, 0, 0, 255)
    assert pixel(g, 1) == (128, 0, 128, 255)
    assert pixel(g, 2) == (0, 0, 255, 255)


def test_explicit_progress_control_wins_over_timing():
    g = graph({"duration_beats": 4})
    assert pixel(g, 0, controls={("t", "progress"): 1}) == (0, 0, 255, 255)
    assert pixel(g, 4, controls={("t", "progress"): 0}) == (255, 0, 0, 255)
    assert pixel(g, 2) == (128, 0, 128, 255)


def test_timing_controls_are_ephemeral_and_validated():
    g = graph({"duration_beats": 4, "repeat": "loop"})
    assert g.base_control_value("t", "timing.duration_beats") == 4
    assert g.is_categorical_control("t", "timing.repeat")
    g.validate_control_choices("t", "timing.repeat", ["loop", "pingpong"])
    assert pixel(g, 1, controls={("t", "timing.duration_beats"): 2}) == (128, 0, 128, 255)
    assert pixel(g, 1) == (191, 0, 64, 255)
    assert pixel(g, 3, controls={("t", "timing.repeat"): "pingpong"}) == (64, 0, 191, 255)
    assert g.nodes["t"].config["timing"] == {"duration_beats": 4, "repeat": "loop"}


def test_chain_internal_stage_uses_same_clock_and_stable_id():
    cfg = {"nodes": [
        {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
        {"id": "c", "type": "transition_chain", "inputs": ["a", "b"],
         "stages": [{"id": "reveal", "timing": {"duration_beats": 2}}]},
    ], "output": "c"}
    g = EffectGraph(cfg)
    stage = g.chain_expansions["c"]["stages"]["reveal"]
    assert stage == "c::stage::reveal"
    assert g.validate_control_target(stage, "timing.duration_beats") is None
    assert g.render(sources(), context=at(1)).getpixel((0, 0)) == (128, 0, 128, 255)


@pytest.mark.parametrize("invalid", [
    {"duration_beats": 0}, {"duration_beats": True},
    {"start_beat": float("nan")}, {"preset": "unknown"},
    {"repeat": "loop", "period_beats": 1},
    {"repeat": "once", "period_beats": 4},
    {"steps": 1}, {"steps": 2.5},
    {"keys": [{"at": 0, "value": 0}, {"at": 1, "value": 2}]},
    {"keys": [{"at": 0.1, "value": 0}, {"at": 1, "value": 1}]},
    {"keys": [{"at": 0, "value": 0}, {"at": 0, "value": 1}]},
    {"keys": [{"at": 0, "value": 0}, {"at": 1, "value": 1}], "preset": "linear"},
    {"keys": [{"at": 0, "value": 0, "garbage": 1}, {"at": 1, "value": 1}]},
    {"reverse": 1}, {"clock": "wall"},
])
def test_reject_invalid_timing_at_graph_preparation(invalid):
    with pytest.raises(ValueError):
        graph(invalid)


def test_timing_rejected_on_nontransition_nodes():
    with pytest.raises(ValueError, match="only supported on transition"):
        EffectGraph({"nodes": [{"id": "a", "type": "source", "timing": {}}], "output": "a"})


def test_bad_runtime_control_rejected_without_mutation():
    g = graph({"duration_beats": 4})
    with pytest.raises(ValueError):
        pixel(g, 1, controls={("t", "timing.duration_beats"): 0})
    with pytest.raises(ValueError):
        pixel(g, 1, controls={("t", "timing.repeat"): "garbage"})
    with pytest.raises(ValueError):
        pixel(g, 1, controls={("t", "timing.steps"): 1.2})
    assert g.nodes["t"].config["timing"] == {"duration_beats": 4}


def test_standalone_preset_values_always_bounded():
    for preset in ('linear', 'smoothstep', 'ease_in', 'ease_out', 'ease_in_out',
                   'triangle', 'pulse', 'bounce'):
        cfg = {"duration_beats": 4, "preset": preset}
        validate_transition_timing(cfg)
        assert all(0 <= transition_timing_progress(cfg, continuous_beat=i / 17) <= 1
                   for i in range(100))
