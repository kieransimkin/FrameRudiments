"""CPU-only dispatch tests that run on all CI operating systems."""

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_spectral import spectral_displacement


def graph(*, mix=1):
    return EffectGraph({"nodes": [
        {"id": "source", "type": "source"},
        {"id": "effect", "type": "spectral_displacement", "inputs": ["source"],
         "mix": mix, "bands": [{"id": "red", "source_color": "#ff0000",
                                "space": "rgb", "offset": [1.25, 0],
                                "tolerance": 0.02, "softness": 0}]},
    ], "output": "effect"})


def test_fake_backend_receives_resolved_frame_controls():
    class Spy:
        last_dispatch = None

        def spectral_displacement(self, source, config):
            assert config["bands"][0]["offset"] == [2.0, 0]
            return spectral_displacement(source, config)

    backend = Spy()
    ink = Image.new("RGBA", (4, 1))
    ink.putpixel((0, 0), (255, 0, 0, 255))
    result = graph().render({"source": ink}, gpu_backend=backend, gpu_strict=True,
                            parameter_values={("effect", "bands.red.offset.x"): 2.0})
    assert result.getpixel((2, 0)) == (255, 0, 0, 255)
    assert backend.last_dispatch == {"gpu_nodes": 1, "cpu_fallbacks": 0}
    assert ink.getpixel((0, 0)) == (255, 0, 0, 255)


def test_auto_fallback_only_for_explicit_unsupported():
    class Unsupported:
        last_dispatch = None

        def spectral_displacement(self, source, config):
            raise GPUUnsupported("insufficient GPU memory")

    ink = Image.new("RGBA", (4, 1), "red")
    backend = Unsupported()
    with pytest.raises(GPUUnsupported):
        graph().render({"source": ink}, gpu_backend=backend, gpu_strict=True)
    assert backend.last_dispatch["cpu_fallbacks"] == 0
    observed = graph().render({"source": ink}, gpu_backend=backend, gpu_strict=False)
    assert observed.tobytes() == graph().render({"source": ink}).tobytes()
    assert backend.last_dispatch == {"gpu_nodes": 0, "cpu_fallbacks": 1}


def test_genuine_runtime_error_must_not_silently_fallback():
    class Faulty:
        last_dispatch = None

        def spectral_displacement(self, source, config):
            raise RuntimeError("GLSL shader compilation failed")

    with pytest.raises(RuntimeError, match="compilation"):
        graph().render({"source": Image.new("RGBA", (2, 2))},
                       gpu_backend=Faulty(), gpu_strict=False)


def test_zero_mix_has_no_gpu_dispatch_and_is_byte_exact():
    class NoCalls:
        last_dispatch = None

        def spectral_displacement(self, source, config):
            raise AssertionError("zero mix must bypass GPU")

    image = Image.new("RGBA", (1, 1), (40, 50, 60, 80))
    backend = NoCalls()
    observed = graph(mix=0).render({"source": image}, gpu_backend=backend,
                                   gpu_strict=True)
    assert observed.tobytes() == image.tobytes()
    assert backend.last_dispatch == {"gpu_nodes": 0, "cpu_fallbacks": 0}
