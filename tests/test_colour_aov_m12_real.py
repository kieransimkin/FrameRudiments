"""Real OpenEXR 3.4 single-part ZIP colour and AOV integration gate.

The dedicated M12 workflow installs the optional bindings and fails if they
are unavailable; local default environments may skip this expensive extension.
"""
import hashlib

import numpy as np
import pytest

OpenEXR = pytest.importorskip("OpenEXR")
if not hasattr(OpenEXR, "File"):
    pytest.skip("OpenEXR modern File API is required", allow_module_level=True)

from framerudiments.colour_aov import AOVFrame, read_exr, write_exr


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("with_aux", [False, True])
def test_real_openexr_singlepart_zip_roundtrip(tmp_path, with_aux):
    h, w = 17, 26
    rng = np.random.default_rng(4123)
    rgb = rng.uniform(-.25, 18.0, size=(h, w, 3)).astype("float32")
    a = rng.uniform(0, 1, size=(h, w)).astype("float32") if with_aux else None
    z = rng.uniform(0, 12000, size=(h, w)).astype("float32") if with_aux else None
    n = rng.uniform(-1, 1, size=(h, w, 3)).astype("float32") if with_aux else None
    v = rng.uniform(-40, 40, size=(h, w, 2)).astype("float32") if with_aux else None
    source = AOVFrame(rgb, "ACEScg", a, z, n, v)
    exr_path = tmp_path / "real.exr"
    evidence = write_exr(exr_path, source)
    assert evidence["readback"] == "exact_float32"
    assert evidence["exr_sha256"] == sha256(exr_path)
    assert evidence["channels"] == source.evidence()["channels"]
    read = read_exr(exr_path, sha256=sha256(exr_path))
    for name, array in source.channels().items():
        assert read.channels()[name].dtype == np.float32
        np.testing.assert_array_equal(read.channels()[name], array)
    assert read.colour_space == "ACEScg"
    with OpenEXR.File(str(exr_path), separate_channels=True) as raw:
        assert raw.header()["type"] == OpenEXR.scanlineimage
        assert raw.header()["compression"] == OpenEXR.ZIP_COMPRESSION
        assert len(raw.parts) == 1


def test_real_opencolorio_identity_config_is_pinned_and_preserves_aovs(tmp_path):
    """Exercise the real OCIO Python CPU processor without a bundled ACES LUT."""
    import PyOpenColorIO as ocio
    from framerudiments.colour_aov import transform_ocio

    config = tmp_path / "identity.ocio"
    ocio.Config.CreateRaw().serialize(str(config))
    # OCIO 'raw' is a test identity/data route, NOT an ACES reference transform.
    source = AOVFrame(np.array([[[1.25, -.1, .5]]], dtype="float32"), "raw",
                      depth=np.array([[2]], dtype="float32"))
    out = transform_ocio(source, config=config, config_sha256=sha256(config),
                         source_space="raw", target_space="raw")
    np.testing.assert_array_equal(out.rgb, source.rgb)
    np.testing.assert_array_equal(out.depth, source.depth)
    assert out.colour_space == "raw"
