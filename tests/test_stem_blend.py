"""Cross-stem visual modulation preserves native Motion clocks and validation."""

from types import SimpleNamespace
import pytest
from framerudiments.stem_blend import StemBlend


class FakeMotion:
    def __init__(self, config, base, clock):
        self.value_dimensions = tuple(config["dimensions"])
        self._values = config["values"]
        self.files = [config["name"]]
        self.evidence = {"source": config["name"]}

    def values(self, seconds, *, continuous_beat=None):
        return tuple(self._values)


def project():
    return SimpleNamespace(
        base=".",
        clock=object(),
        stem_sources={
            "bass": {"name": "bass.json", "dimensions": ["x", "y"], "values": [1, -1]},
            "drums": {"name": "drums.json", "dimensions": ["x", "y"], "values": [-1, 1]},
            "mismatched": {"name": "wrong.json", "dimensions": ["z"], "values": [1]},
        },
    )


def test_weighted_stem_motion_is_normalized_and_deterministic(monkeypatch):
    monkeypatch.setattr("framerudiments.stem_blend.Motion", FakeMotion)
    mix = StemBlend([{"stem_source": "bass", "weight": 3}, {"stem_source": "drums", "weight": 1}], project())
    assert mix.value_dimensions == ("x", "y")
    assert mix.values(0) == (0.5, -0.5)
    assert mix.values(35.25, continuous_beat=99) == (0.5, -0.5)
    assert mix.files == ["bass.json", "drums.json"]
    assert mix.evidence["inputs"][0]["stem_source"] == "bass"


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"stem_source": "bass"}],
        [{"stem_source": "bass"}, {"stem_source": "bass"}],
        [{"stem_source": "bass"}, {"stem_source": "unknown"}],
        [{"stem_source": "bass", "weight": -1}, {"stem_source": "drums"}],
        [{"stem_source": "bass", "weight": float("nan")}, {"stem_source": "drums"}],
        [{"stem_source": "bass"}, {"stem_source": "mismatched"}],
        [{"stem_source": "bass", "kind": "unsafe"}, {"stem_source": "drums"}],
    ],
)
def test_invalid_blends_fail_closed(monkeypatch, items):
    monkeypatch.setattr("framerudiments.stem_blend.Motion", FakeMotion)
    with pytest.raises(ValueError):
        StemBlend(items, project())
