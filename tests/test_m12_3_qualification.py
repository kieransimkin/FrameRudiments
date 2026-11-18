"""Validate the local qualification command without claiming physical GPU parity."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_gpu_aces_m12 import build, SCHEMA, BUILTIN_ACES_CG  # noqa: E402


def test_unavailable_native_colour_is_not_silent_success(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == 'PyOpenColorIO':
            raise ImportError('native binding absent')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', unavailable)
    target = tmp_path / 'no-runtime'
    with pytest.raises(ImportError, match='native binding absent'):
        build(target)
    assert not (target / 'report.json').exists()


def test_aces_qualifier_checks_pinned_declared_spaces(tmp_path, monkeypatch):
    import sys
    fake = SimpleNamespace(Config=SimpleNamespace(CreateFromBuiltinConfig=lambda name:
        SimpleNamespace(getColorSpace=lambda name: None)))
    monkeypatch.setitem(sys.modules, 'PyOpenColorIO', fake)
    with pytest.raises(RuntimeError, match='ACEScg'):
        build(tmp_path / 'bad-aces-config')
    assert BUILTIN_ACES_CG.startswith('cg-config-')
    assert SCHEMA.endswith('v1')
