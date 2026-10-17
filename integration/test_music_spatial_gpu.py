"""Real offscreen OpenGL 3.3 mesh, shadows and medium transport acceptance.

This suite belongs to the visualiser-acceptance CI job, which installs the
optional GPU dependencies. It must NOT skip on that runner: absent OpenGL is a
configuration failure rather than a silent CPU fallback.
"""
from types import SimpleNamespace

import pytest

from framerudiments.music_spatial_renderers import MusicSpatialRenderer


def _frame(step, values):
    return SimpleNamespace(
        values=values,
        context=SimpleNamespace(frame=step, output_seconds=step / 60,
                                source_seconds=step / 60, beat=step / 30),
        history=(), spectrum_bands=(),
    )


@pytest.mark.parametrize("shape", ("torus", "sphere", "terrain"))
def test_native_opengl_shadowed_scattering_and_random_access(shape):
    renderer=MusicSpatialRenderer((128,96), {
        "geometry":shape,"backend":"gpu","rings":7,"segments":11,
        "shadow_map_size":128,"volumetric_steps":4,"fog":.55,
        "scattering_density":.3,"absorption_density":.1,
        "texture_octaves":3,"weathering":.3,"glaciation":.45,
    })
    try:
        a=renderer.render(_frame(30,(.5,-.2,.3)))
        assert a.mode=="RGBA" and a.size==(128,96)
        assert renderer.frame_report["backend"]=="opengl-3.3"
        assert renderer.frame_report["shadows"].startswith("directional shadow")
        b=renderer.render(_frame(38,(-.3,.6,-.5)))
        assert a.tobytes()!=b.tobytes()
        assert renderer.render(_frame(30,(.5,-.2,.3))).tobytes()==a.tobytes()
    finally:
        renderer.close()


def test_physical_volume_scattering_and_material_changes_pixels():
    f=_frame(10,(.2,.3,.5))
    a=MusicSpatialRenderer((128,96),{"geometry":"terrain","backend":"gpu",
        "fog":0,"rings":6,"segments":9,"shadow_map_size":128})
    b=MusicSpatialRenderer((128,96),{"geometry":"terrain","backend":"gpu",
        "fog":.8,"scattering_density":.8,"anisotropy":.5,
        "glaciation":1,"ice_coverage":1,"normal_strength":2,
        "rings":6,"segments":9,"shadow_map_size":128})
    try:
        assert a.render(f).tobytes()!=b.render(f).tobytes()
    finally:
        a.close()
        b.close()
