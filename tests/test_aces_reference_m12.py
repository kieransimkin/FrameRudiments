"""M12.3 non-display ACEScg colour-space conversions through pinned OCIO."""

import hashlib
import os

import numpy as np
import pytest

from framerudiments.aces_reference_m12 import to_acescg_reference
from framerudiments.colour_aov import AOVFrame


def sample():
    rgb = np.asarray([[[1., 1., 1.], [4., .2, .01]],
                      [[0., 0., 0.], [.8, .6, .5]]], dtype=np.float32)
    depth = np.full((2, 2), 2.5, dtype=np.float32)
    normal = np.zeros((2, 2, 3), dtype=np.float32)
    normal[..., 2] = 1
    motion = np.full((2, 2, 2), .5, dtype=np.float32)
    return AOVFrame(rgb, "Linear-sRGB", depth=depth, normals=normal, velocity=motion)


def test_invalid_input_and_config_fail_before_ocio(tmp_path):
    cfg = tmp_path / "reference.ocio"
    cfg.write_text("fake")
    digest = hashlib.sha256(cfg.read_bytes()).hexdigest()
    a = sample()
    with pytest.raises(ValueError):
        to_acescg_reference(AOVFrame(a.rgb, "ACEScg"), config=cfg, config_sha256=digest)
    with pytest.raises(ValueError):
        to_acescg_reference(a, config=cfg, config_sha256=digest, aces_space="ACES")
    with pytest.raises(ValueError):
        to_acescg_reference(a, config=cfg, config_sha256=digest, max_roundtrip_abs=float("nan"))


def require_ocio():
    try:
        import PyOpenColorIO as ocio
    except ImportError as exc:
        if os.environ.get("FRAMERUDIMENTS_REQUIRE_ACES") == "1":
            pytest.fail(f"Required OCIO native binding unavailable: {exc}")
        pytest.skip("PyOpenColorIO not installed")
    return ocio


def test_official_pinned_aces13_cg_conversion_roundtrip_and_aov_identity(tmp_path):
    ocio = require_ocio()
    # Named and versioned Academy Software Foundation ACES CG config,
    # NOT the moving `ocio://latest` alias. Serialize, then SHA-pin on disk.
    config = ocio.Config.CreateFromBuiltinConfig("cg-config-v2.2.0_aces-v1.3_ocio-v2.4")
    assert config.getColorSpace("ACEScg") is not None
    assert config.getColorSpace("Linear Rec.709 (sRGB)") is not None
    path = tmp_path / "aces13.ocio"
    path.write_text(config.serialize(), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    before = sample()
    transformed, receipt = to_acescg_reference(before, config=path, config_sha256=digest)
    assert transformed.colour_space == "ACEScg"
    assert receipt["aovs_unchanged"]
    assert receipt["view_transform"].startswith("none")
    assert receipt["roundtrip_max_abs"] < receipt["allowed_abs"]
    for channel in ("depth", "normals", "velocity"):
        assert np.array_equal(getattr(before, channel), getattr(transformed, channel))
    assert np.array_equal(before.rgb, sample().rgb)
    assert not np.array_equal(before.rgb, transformed.rgb)
    with pytest.raises(ValueError):
        to_acescg_reference(before, config=path, config_sha256="0" * 64)
