"""Synthetic protocol, reference isolation and native-bridge contract tests."""
import hashlib
import importlib.util
from pathlib import Path
from PIL import Image
import pytest
from framerudiments import audio_probes as probes
from framerudiments.controlled_profiling import capture_condition, source_image, validate_job

@pytest.fixture(scope='module')
def signals(tmp_path_factory):
    directory = tmp_path_factory.mktemp('probe-audio')
    return {name: probes.make_probe(name, 0, directory) for name in probes.EXTENDED}

def test_frequency_conditions_share_level_envelope_and_duration(signals):
    for name, hz in [('bass-pulses', 80), ('mid-pulses', 1000), ('treble-pulses', 6000)]:
        row = signals[name]
        assert row['frequency_hz'] == hz and row['rms'] == 0.12
        assert row['events'] == signals['bass-pulses']['events']
        assert row['peak'] < 0.98
        assert row['samples_per_channel'] == 12 * 44100
    assert len({signals[n]['sha256'] for n in signals}) == len(signals)

def test_quiet_is_exact_half_amplitude(signals):
    quiet = probes.load_pcm(signals['bass-quiet'])
    loud = probes.load_pcm(signals['bass-pulses'])
    assert all((q == v / 2 for q, v in zip(quiet, loud)))

def test_onsets_vary_between_trials_but_not_between_frequency_conditions():
    assert probes.events_for(0) != probes.events_for(1)
    assert len(probes.events_for(1, fast=True)) == 8
    assert probes.events_for(1) == probes.events_for(1)

def test_physical_frequency_peak_is_expected(signals):
    for name in ['bass-pulses', 'mid-pulses', 'treble-pulses']:
        spec = signals[name]
        mono = probes.load_pcm(spec)[0::2]
        start = spec['events'][0]['on'] + 0.05
        end = start + 0.2
        part = mono[int(start * 44100):int(end * 44100)]
        crossing = sum((a <= 0 < b for a, b in zip(part, part[1:])))
        assert abs(crossing / 0.2 - spec['frequency_hz']) <= 5.01

def test_silence_and_warmup_are_genuine_zero_pcm(signals):
    assert not any(probes.load_pcm(signals['silence']))
    for row in signals.values():
        assert not any(probes.load_pcm(row)[:2 * 44100 * 2])

def test_audio_is_fed_at_render_rate_not_capture_rate(signals):
    pcm = probes.load_pcm(signals['bass-pulses'])
    assert len(probes.pcm_window(pcm, 0)) == 2048
    assert not any(probes.pcm_window(pcm, 0))
    for n in [1, 149, 152, 720]:
        block = probes.pcm_window(pcm, n)
        assert len(block) == 2048
        end = n * 44100 // 60
        start = max(0, end - 1024)
        assert block[-(end - start) * 2:] == pcm[start * 2:end * 2]
    with pytest.raises(ValueError, match='gaps'):
        probes.pcm_window(pcm, 1, 30)

def test_inputs_cannot_carry_reference_colours_shapes_or_text():
    nofeed = source_image('engine-only', 64)
    assert not any(nofeed.tobytes())
    patterns = []
    for role in ('neutral-a', 'neutral-b'):
        image = source_image(role, 64)
        assert image.getchannel('R').tobytes() == image.getchannel('G').tobytes() == image.getchannel('B').tobytes()
        patterns.append(image.tobytes())
        image.close()
    assert patterns[0] != patterns[1]
    with pytest.raises(ValueError):
        source_image('album-art', 64)
    nofeed.close()

def job(tmp_path, signals):
    preset = tmp_path / 'fixture.json'
    preset.write_text('{}')
    return {'id': 'engine-only__bass-pulses__0', 'protocol': probes.PROTOCOL, 'trial': 0, 'seed': 123, 'input_role': 'engine-only', 'input_mix': 0, 'render_fps': 60, 'capture_fps': 5, 'edge': 64, 'preset': {'engine': 'butterchurn', 'preset': {'path': str(preset), 'sha256': hashlib.sha256(preset.read_bytes()).hexdigest()}}, 'stimulus': signals['bass-pulses'], 'output': str(tmp_path / 'runs' / 'fixture')}

def test_real_capture_driver_with_injected_backend(signals, tmp_path):
    calls = []

    class Backend:
        evidence = {'engine': 'synthetic-test', 'renderer': 'fixture'}

        def __init__(self, size, fps, preset, mix, seed, work):
            assert mix == 0 and fps == 60
            self.size = size

        def advance(self, image, context, pcm, capture=False):
            calls.append((context.frame, len(pcm), capture))
            return Image.new('RGBA', self.size, 'white') if capture else None

        def close(self):
            pass
    spec = job(tmp_path, signals)
    result = capture_condition(spec, factory=Backend)
    assert len(calls) == 721 and len(result['captures']) == 61
    assert all((c[1] == 2048 for c in calls))
    assert result['processed_frames'] == 721 and result['state'] == 'captured'
    repeated = capture_condition(spec, factory=lambda *a: pytest.fail('valid receipt should resume'))
    assert repeated['signature'] == result['signature']
    Path(result['captures'][3]['output']['path']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='changed'):
        capture_condition(spec, factory=Backend)

@pytest.mark.parametrize('field,value', [('input_mix', 0.35), ('capture_fps', 14), ('render_fps', 30), ('input_role', 'cover')])
def test_capture_rejects_misdeclared_experiment(signals, tmp_path, field, value):
    spec = job(tmp_path, signals)
    spec[field] = value
    with pytest.raises(ValueError):
        validate_job(spec)

def test_pcm_hash_tamper_is_not_reused(tmp_path):
    row = probes.make_probe('silence', 0, tmp_path)
    Path(row['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):
        probes.load_pcm(row)

def test_driver_alternates_engines_without_relying_on_names():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'controlled_preset_analysis.py'
    spec = importlib.util.spec_from_file_location('controlled_driver', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [{'engine': e, 'id': e + str(i)} for e in ('projectm', 'butterchurn') for i in range(3)]
    order = module.ordered_presets(rows, 71)
    assert [r['engine'] for r in order] == ['projectm', 'butterchurn'] * 3
    assert order == module.ordered_presets(rows, 71)

def test_legacy_release_pins_are_not_changed_by_new_launcher():
    script = (Path(__file__).resolve().parents[1] / 'scripts' / 'Run-ControlledPresetAnalysis.ps1').read_text()
    assert 'Run-HybridPresetAnalysis.ps1' in script
    hybrid = (Path(__file__).resolve().parents[1] / 'scripts' / 'Run-HybridPresetAnalysis.ps1').read_text()
    assert '.venv-hybrid-analysis' in hybrid
    assert 'install_analysis_releases.py' not in hybrid
    assert 'install_analysis_releases.py' not in script
