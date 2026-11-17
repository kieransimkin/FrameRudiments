"""M9.5 mandatory actual EGL golden comparisons, no skips or CPU substitutes."""
from hashlib import sha256
from pathlib import Path

from framerudiments.effect_graph_gpu import GLMatteBackend
from framerudiments.gl_transition_goldens import shader_config, synthetic_sources, verify_manifest
from framerudiments.gl_transitions import render_gl_transition, shader_pass_plan

ROOT = Path(__file__).resolve().parents[1]


def test_real_egl_matches_every_approved_rgba8_golden_in_scrambled_frame_order():
    manifest = verify_manifest(ROOT)
    a, b = synthetic_sources()
    originals = a.tobytes(), b.tobytes()
    cases = manifest['cases']
    # Scrambled and repeated evaluation proves no untracked frame-history input.
    with GLMatteBackend(backend='egl') as gpu:
        for index in (4, 1, 2, 0, 3, 4):
            entry = cases[index]
            config = shader_config(entry, ROOT)
            gpu.begin_transfer_sample()
            output = render_gl_transition(gpu, a, b, config)
            assert sha256(output.tobytes()).hexdigest() == entry['rgba_sha256'], entry['id']
            plan = shader_pass_plan(config, output.size)
            assert gpu.last_transport['uploads'] == 2
            assert gpu.last_transport['readbacks'] == 1
            assert gpu.last_transport['external_shader_passes'] == plan['passes']
            assert gpu.last_transport['external_shader_peak_payload_bytes'] == plan['working_rgba8_bytes']
    assert (a.tobytes(), b.tobytes()) == originals
