"""Validate per-run Windows pytest temp isolation on every CI platform."""

from pathlib import Path
from types import SimpleNamespace

from conftest import _assign_windows_basetemp


class _Config:
    def __init__(self, basetemp=None):
        self.option = SimpleNamespace(basetemp=basetemp)

    def getoption(self, name, default=None):
        return getattr(self.option, name, default)


def test_windows_basetemp_is_unique_per_invocation(tmp_path):
    first = _Config()
    second = _Config()
    a = _assign_windows_basetemp(first, tmp_path)
    b = _assign_windows_basetemp(second, tmp_path)
    root = tmp_path / "FrameRudiments" / "pytest-tmp"
    assert a is not None and b is not None and a != b
    assert a.parent == b.parent == root
    assert Path(first.option.basetemp) == a
    assert Path(second.option.basetemp) == b


def test_explicit_pytest_basetemp_is_never_overridden(tmp_path):
    explicit = str(tmp_path / "chosen-by-caller")
    config = _Config(explicit)
    assert _assign_windows_basetemp(config, tmp_path) is None
    assert config.option.basetemp == explicit
    assert not (tmp_path / "FrameRudiments").exists()
