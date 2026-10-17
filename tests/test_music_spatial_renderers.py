"""Evidence-preserving native music-driven landscape and mesh tests."""
from types import SimpleNamespace
import math

import pytest

from framerudiments.music_spatial_renderers import MusicSpatialRenderer
from framerudiments.terrain_field import terrain_field, fbm


class Band:
    def __init__(self, index):
        self.name = f"fourier-{index}"
        self.low_hz = 40 * 2**index
        self.high_hz = 40 * 2**(index+1)
        self.source_sha256 = "a"*64
        self.sample_rate = 48000
        self.fft_size = 2048

    def rms(self, value):
        return max(0, (value + 1) / 2)


def sample(at, values, bands=()):
    return SimpleNamespace(context=SimpleNamespace(frame=at, output_seconds=at/60,
                                                   source_seconds=at/60, beat=at/30),
                           values=values, spectrum_bands=bands)


def spectrum_frame(at=23, phase=0):
    bands=tuple(Band(j) for j in range(5))
    hist=[sample(i, tuple(-.5+1.45*((j+i+phase)%5)/4 for j in range(5)))
          for i in range(at-12,at+1)]
    last=hist[-1]
    return SimpleNamespace(context=last.context,values=last.values,
                           history=tuple(hist),spectrum_bands=bands)


def test_fft_row_history_creates_chronological_spectral_landscape():
    f=spectrum_frame(23)
    a=terrain_field(f,8,16,source="fft",amplitude=1.0)
    b=terrain_field(f,8,16,source="fft",amplitude=1.0)
    assert a==b
    assert a.source_frames==tuple(range(16,24))
    assert a.source_sha256=="a"*64
    assert any(a.heights[i]!=a.heights[i+1] for i in range(7))
    assert len(a.heights[0])==16
    other=terrain_field(spectrum_frame(24,phase=1),8,16,source="fft")
    assert a.heights != other.heights


def test_fft_heightfield_rejects_unproven_or_wrong_band_data():
    f=spectrum_frame()
    f.spectrum_bands=()
    with pytest.raises(ValueError,match="calibrated"):
        terrain_field(f,8,8,source="fft")
    f=spectrum_frame()
    f.spectrum_bands=f.spectrum_bands[:-1]
    with pytest.raises(ValueError,match="calibrated"):
        terrain_field(f,8,8,source="fft")


def test_geomorphology_changes_shape_without_random_state():
    f=sample(20,(.4,-.6,.2))
    source=terrain_field(f,12,16)
    eroded=terrain_field(f,12,16,weathering=.8,hydraulic_erosion=.8,
                         thermal_erosion=.75,glaciation=.85,erosion_iterations=5)
    assert source!=eroded
    assert eroded==terrain_field(f,12,16,weathering=.8,hydraulic_erosion=.8,
                          thermal_erosion=.75,glaciation=.85,erosion_iterations=5)
    assert all(math.isfinite(v) and v>=0 for row in eroded.heights for v in row)
    assert fbm(.2,.3,seed=7)==fbm(.2,.3,seed=7)


@pytest.mark.parametrize("kind",("torus","sphere","terrain"))
def test_cpu_mesh_renders_and_reacts_to_native_motion(kind):
    r=MusicSpatialRenderer((140,92),{"geometry":kind,"rings":7,"segments":12,
                                      "fog":0,"glow":0,"backend":"cpu"})
    f=sample(30,(.6,-.2,.3))
    a=r.render(f)
    assert a.mode=="RGBA" and a.size==(140,92)
    assert r.frame_report["triangles"]==168
    assert r.frame_report["backend"]=="cpu"
    assert a.tobytes()==r.render(f).tobytes()
    b=r.render(sample(30,(-.6,.8,-.4)))
    assert a.tobytes()!=b.tobytes()
    r.render(sample(12,(0,0,0)))
    assert a.tobytes()==r.render(f).tobytes()


def test_calibrated_fft_scene_covers_all_rows_and_changes_with_music():
    style={"geometry":"terrain","terrain_source":"fft","rings":8,"segments":12,
           "fog":0,"glow":0,"backend":"cpu","erosion_iterations":1}
    r=MusicSpatialRenderer((146,96),style)
    assert r.requires_spectrum
    a=r.render(spectrum_frame(23))
    assert r.frame_report["source_sha256"]=="a"*64
    assert len(r.frame_report["source_frames"])==9
    b=r.render(spectrum_frame(24,phase=2))
    assert a.tobytes()!=b.tobytes()
    assert a.tobytes()==r.render(spectrum_frame(23)).tobytes()


def test_physical_material_and_modulated_style_controls_impact_pixels():
    f=sample(9,(.3,-.5,.7))
    normal=MusicSpatialRenderer((140,90),{"geometry":"terrain","fog":0,"glow":0,
                                          "rings":7,"segments":10})
    weathered=MusicSpatialRenderer((140,90),{"geometry":"terrain","fog":0,"glow":0,
                     "rings":7,"segments":10,"glaciation":.95,"weathering":.95,
                     "ice_coverage":.9,"normal_strength":2,"audio_displacement":.8})
    assert normal.render(f).tobytes()!=weathered.render(f).tobytes()


@pytest.mark.parametrize("wrong",[
    {"backend":"vulkan"},{"terrain_source":"raw_pcm"},
    {"geometry":"sphere","terrain_source":"fft"},
    {"volumetric_steps":0},{"shadow_map_size":32},
    {"glaciation":float("nan")},{"mesh_gain":-1},
    {"normal_strength":5},{"texture_octaves":9},
    {"weathering":2},{"unknown_native_shader":"exec"},
])
def test_invalid_scene_options_rejected(wrong):
    with pytest.raises(ValueError):
        MusicSpatialRenderer((140,90),dict({"geometry":"terrain"},**wrong))


def test_explicit_gpu_does_not_silently_fallback(monkeypatch):
    import framerudiments.gpu_spatial_backend as gpu
    def fail(*_args):
        raise RuntimeError("no GPU available")
    monkeypatch.setattr(gpu,"GLMeshRenderer",fail)
    r=MusicSpatialRenderer((120,80),{"backend":"gpu","rings":5,"segments":8})
    with pytest.raises(RuntimeError,match="native GPU"):
        r.render(sample(2,(.4,.2,.1)))


def test_auto_gpu_fallback_is_explicit_in_evidence(monkeypatch):
    import framerudiments.gpu_spatial_backend as gpu
    def fail(*_args):
        raise RuntimeError("no display")
    monkeypatch.setattr(gpu,"GLMeshRenderer",fail)
    r=MusicSpatialRenderer((120,80),{"backend":"auto","rings":5,"segments":8,
                                      "fog":0,"glow":0})
    r.render(sample(2,(.4,.2,.1)))
    assert r.frame_report["backend"]=="cpu-fallback"
    assert "no display" in r.frame_report["gpu_fallback_reason"]


def test_gpu_shader_contains_shadow_and_atmospheric_equations():
    from framerudiments.gpu_spatial_backend import MESH_FRAGMENT,VOLUME_FRAGMENT
    assert "u_shadow" in MESH_FRAGMENT and "float visibility" in MESH_FRAGMENT
    assert "exp(-sigmaT*ds)" in VOLUME_FRAGMENT
    assert "u_anisotropy" in VOLUME_FRAGMENT
    assert "u_inverse_vp" in VOLUME_FRAGMENT


def test_fft_driven_spherical_mesh_uses_calibrated_spectrum():
    f=spectrum_frame()
    renderer=MusicSpatialRenderer((128,82),{"geometry":"sphere", "spectral_deform":True,
                          "rings":7,"segments":9,"fog":0,"glow":0})
    assert renderer.requires_spectrum
    first=renderer.render(f)
    later=renderer.render(spectrum_frame(23,phase=2))
    assert first.tobytes()!=later.tobytes()


def test_modulatable_light_and_camera_controls_have_effect():
    f=sample(50,(.2,-.6,.7))
    a=MusicSpatialRenderer((120,88),{"geometry":"terrain", "light_azimuth":-110,
                                 "light_elevation":45,"camera_pitch":0,"rings":7,"segments":9})
    b=MusicSpatialRenderer((120,88),{"geometry":"terrain", "light_azimuth":80,
                                 "light_elevation":10,"camera_pitch":22,"rings":7,"segments":9})
    assert a.render(f).tobytes()!=b.render(f).tobytes()
