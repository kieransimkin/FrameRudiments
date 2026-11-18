"""Native S7 preview qualification, with explicitly labelled injected pressure.

Only generated, pinned fixtures are accepted. The harness drives the shipping
stream/cache/native renderer; it never replaces those algorithms with a model.
Its PNG-decode acknowledgements are not browser or physical-display evidence.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
import html
from importlib import metadata
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import tempfile
from time import monotonic, process_time, sleep
import wave

from PIL import Image, ImageDraw

from .compositor import Compositor
from .effect_graph_gpu_memory import is_software_renderer
from .preview_adaptive import AdaptiveController
from .preview_dependencies import implementation_fingerprint
from .preview_native_renderer import NativePreviewRenderer
from .preview_qualification import _VERSIONS, runtime_versions
from .preview_stream_contract import canonical_digest, frame_clock
from .preview_stream_s2 import PreviewStreamAPI
from .project import Project

SCHEMA = 'framerudiments.preview-stream-profile.v1'
PRESETS = {'smoke': 12, 'standard': 64, 'stress': 1000}
SIZES = {'small': (320, 180), 'hd': (1920, 1080), 'uhd': (3840, 2160)}
_TARGETS = (24, 30, 60, 120)
_COSTS = (5, 12, 30, 70)
_MAX_GALLERY_FRAMES = 6


class ProfileFailure(RuntimeError):
    """A fixed diagnostic code, never source text or a private pathname."""


def temporary_preflight(parent):
    """Prove create/write/read/atomic rename/removal with the executing runtime."""
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    probe = Path(tempfile.mkdtemp(prefix='preview-profile-probe-', dir=parent))
    try:
        first, renamed = probe/'probe', probe/'renamed'
        first.write_bytes(b'preview-profile-preflight')
        if first.read_bytes() != b'preview-profile-preflight':
            raise ProfileFailure('temporary_readback_failed')
        os.replace(first, renamed)
        renamed.unlink()
    finally:
        probe.rmdir()


def _percentiles(values):
    ordered = sorted(values)
    if not ordered:
        return {'samples': 0, 'p50': None, 'p95': None}
    from math import ceil
    return {'samples': len(ordered), 'p50': ordered[ceil(len(ordered)*.5)-1],
            'p95': ordered[ceil(len(ordered)*.95)-1]}


def controller_matrix():
    """Run the real controller under virtual costs, never report host FPS."""
    results = []
    for target in _TARGETS:
        for cost in _COSTS:
            now = [0.0]
            controller = AdaptiveController(target, clock=lambda: now[0])
            transitions = []
            for phase, count, duration in (('pressure', 600, cost), ('recovery', 1200, 2)):
                for _ in range(count):
                    now[0] += duration/1000
                    decision = controller.observe(duration, epoch=controller.epoch,
                                                  profile_id=controller.profile_id)
                    if decision:
                        transitions.append({'at_ms': round(now[0]*1000, 3), 'phase': phase, **decision})
            if any(abs(int(row['from_profile'][1])-int(row['to_profile'][1])) != 1
                   for row in transitions):
                raise ProfileFailure('controller_rank_jump')
            if any(b['at_ms']-a['at_ms'] < 999.99 for a, b in zip(transitions, transitions[1:])):
                raise ProfileFailure('controller_cooldown_violation')
            pressured = cost > 900/target
            if pressured and not any(row['phase'] == 'pressure' for row in transitions):
                raise ProfileFailure('controller_pressure_unhandled')
            if not pressured and any(row['phase'] == 'pressure' for row in transitions):
                raise ProfileFailure('controller_unnecessary_downgrade')
            if pressured and not any(row['phase'] == 'recovery' for row in transitions):
                raise ProfileFailure('controller_recovery_missing')
            results.append({'target_fps': target, 'injected_render_ms': cost,
                            'pressure_samples': 600, 'recovery_samples': 1200,
                            'recovery_render_ms': 2, 'transitions': transitions,
                            'final_profile': controller.profile_id,
                            'one_rank_changes': True, 'cooldown_respected': True,
                            'expected_pressure': pressured})
    now = [0.0]
    controller = AdaptiveController(60, clock=lambda: now[0])
    first = controller.allocation_failure(epoch=controller.epoch, profile_id=controller.profile_id)
    repeated = controller.allocation_failure(epoch=controller.epoch, profile_id=controller.profile_id)
    if first is None or first['to_profile'] != 'Q4' or repeated is not None:
        raise ProfileFailure('controller_allocation_recovery_invalid')
    return {'measurement': 'injected_costs_and_virtual_controller_clock',
            'host_render_performance': False, 'rows': results,
            'allocation_failure': {'one_rank_downgrade': True, 'cooldown_respected': True}}


def fixture(directory, size, backend):
    """Pinned native two-section/repeated-clock fixture with no private inputs."""
    directory = Path(directory)
    directory.mkdir()
    audio = directory/'synthetic.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b'\x00\x00' * 48000)
    sprite = directory/'synthetic.png'
    image = Image.new('RGBA', (128, 64), '#15243c')
    draw = ImageDraw.Draw(image)
    rng = random.Random(613)
    for x in range(0, 128, 4):
        for y in range(0, 64, 4):
            draw.rectangle((x, y, x+3, y+3), fill=(rng.randrange(70, 240),
                rng.randrange(60, 220), rng.randrange(80, 250), 230))
    draw.ellipse((39, 7, 89, 57), outline='white', width=3)
    image.save(sprite, 'PNG')
    cue = directory/'synthetic.lrc'
    cue.write_text('[00:00.000][SECTION: A]\n[00:00.500][SECTION: B]\n', encoding='utf-8')
    def pin(path):
        return {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    width, height = size
    data = {'schema': 'framerudiments.project.v1', 'purpose': 'visualiser',
        'width': width, 'height': height, 'fps': 60,
        'audio': {'source': pin(audio), 'sample_rate': 48000, 'start_sample': 0,
                  'end_sample': 48000, 'repeats': 2, 'fade_seconds': 0},
        'clock': {'bpm': 120, 'authority': 'synthetic S7 fixture', 'status': 'synthetic'},
        'cues': pin(cue), 'background': {'colour': '#102137'},
        'layers': [
            {'id': 'local', 'type': 'sprite', 'asset': pin(sprite),
             'center': [width*.33, height*.5], 'width': round(width*.52),
             'bounds': [0, 0, width, height], 'start_seconds': 0, 'end_seconds': .5},
            {'id': 'continuous', 'type': 'sprite', 'asset': pin(sprite),
             'center': [width*.73, height*.5], 'width': round(width*.3),
             'bounds': [0, 0, width, height]}],
        'layer_animations': {'layer:continuous': {'opacity': {'keys': [
            {'time': 0, 'value': .2}, {'time': .5, 'value': 1}, {'time': .99, 'value': .3}]}}},
        'render_quality': {'spatial_scale': 2, 'temporal_samples': 2},
        'effect_graph_backend': backend,
        'effect_graph': {'nodes': [{'id': 'frame', 'type': 'source'},
            {'id': 'blur', 'type': 'filter', 'inputs': ['frame'], 'filter_radius': 1}], 'output': 'blur'}}
    path = directory/'project.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


def _validate_fixture(original, draft):
    # No user projects are admitted by this harness. The only edit being
    # qualified is the first time-gated synthetic layer's scalar opacity.
    allowed = deepcopy(original)
    try:
        opacity = draft['layers'][0].get('opacity', 1)
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError('invalid qualification fixture') from exc
    if type(opacity) not in (int, float) or not 0 <= opacity <= 1:
        raise ValueError('invalid qualification opacity')
    if 'opacity' in draft['layers'][0]:
        allowed['layers'][0]['opacity'] = opacity
    if draft != allowed:
        raise ValueError('qualification fixture permits only its declared opacity edit')


class MeasuredNativeRenderer(NativePreviewRenderer):
    """Shipping native renderer with declared service pressure and GPU probes."""

    def __init__(self, *args, delay_ms=0, allocation_failures=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_ms = delay_ms
        self.allocation_failures = allocation_failures
        self.gpu_observations = []

    def __call__(self, frame, profile_id):
        if self.allocation_failures:
            self.allocation_failures -= 1
            raise MemoryError('injected qualification allocation pressure')
        if self.delay_ms:
            sleep(self.delay_ms/1000)
        png = super().__call__(frame, profile_id)
        gpu = self.compositor.effect_graph_gpu
        if gpu is not None:
            transport = {k: v for k, v in gpu.last_transport.items()
                         if type(v) in (int, bool) and (type(v) is bool or v >= 0)}
            if gpu.last_dispatch.get('cpu_fallbacks'):
                raise ProfileFailure('strict_gpu_fell_back')
            observation = {'frame': frame, 'profile': profile_id,
                'renderer_sha256': sha256(gpu.capabilities.renderer.encode('utf-8')).hexdigest(),
                'software_renderer': is_software_renderer(gpu.capabilities.renderer),
                'context_version': gpu.capabilities.context_version,
                'dispatch': dict(gpu.last_dispatch), 'transport': transport,
                'driver_memory': gpu.sample_driver_memory()}
            if len(self.gpu_observations) == 128:
                self.gpu_observations.pop(0)
            self.gpu_observations.append(observation)
        return png


def _decode(png):
    with Image.open(BytesIO(png)) as image:
        image.load()
        if image.format != 'PNG' or image.mode != 'RGBA':
            raise ProfileFailure('invalid_native_png')
        return image.size, image.tobytes()


def _wait(stream, predicate, deadline):
    while monotonic() < deadline:
        with stream.condition:
            if predicate():
                return
            if stream.foreground_cleanup_error:
                raise ProfileFailure('native_cleanup_failed')
            if stream.refinement and stream.refinement.state in ('failed', 'cleanup_failed'):
                raise ProfileFailure('refinement_failed')
            stream.condition.wait(min(.05, max(0, deadline-monotonic())))
    raise ProfileFailure('qualification_deadline_exceeded')


def _retire(api):
    stream = api.active
    if stream is None:
        return
    stream.close()
    _wait(stream, lambda: stream.retired, monotonic()+8)
    if not stream.retired:
        raise ProfileFailure('worker_cleanup_failed')


class NativeRun:
    def __init__(self, path, directory, *, delay_ms=0, allocation_failures=0, refine=False,
                 deadline, backend='cpu'):
        self.path, self.deadline, self.backend = path, deadline, backend
        self.samples, self.rate_samples, self.request_sequence = [], [], 0
        self.cleanup_ok = True
        def factory(*args, **kwargs):
            return MeasuredNativeRenderer(*args, delay_ms=delay_ms,
                allocation_failures=allocation_failures, **kwargs)
        self.api = PreviewStreamAPI(path, _validate_fixture, None, cache_root=directory,
            adaptive_renderer_factory=factory, acknowledgement_source='harness_png_decode',
            cache_limits={'ram_bytes': 0})
        data = json.loads(path.read_text(encoding='utf-8'))
        started = monotonic()
        try:
            reply = self.api.open({'project': data, 'adaptive': True, 'refine': refine,
                                  'frame': 0, 'target_fps': 120})
            self.stream = self.api.get(reply['session'])
            self.current_data = data
            self.initial = self.receive(0, 0, started, 'initial')
        except BaseException:
            try:
                _retire(self.api)
                self.api.shutdown()
            except BaseException as cleanup:
                raise ProfileFailure('worker_cleanup_failed') from cleanup
            raise

    def receive(self, sequence, frame, started, command):
        stream = self.stream
        latest = 0
        while monotonic() < self.deadline:
            for event in stream.since(latest, timeout=.05):
                latest = max(latest, event['id'])
                if event.get('cursor_seq') != sequence or event.get('frame') != frame:
                    continue
                if event['type'] in ('render-error', 'overload', 'stateful-seek-unavailable'):
                    raise ProfileFailure('native_stream_request_failed')
                if event['type'] != 'frame-ready':
                    continue
                png = stream.image(frame, event['generation'], event['image_sha256'])
                if png is None:
                    raise ProfileFailure('delivered_frame_not_current')
                size, pixels = _decode(png)
                if not stream.acknowledge(sequence, frame, event['image_sha256']):
                    raise ProfileFailure('decode_acknowledgement_rejected')
                sample = {'command': command, 'frame': frame, 'profile': event['profile_id'],
                          'width': size[0], 'height': size[1], 'cache_hit': event['cache_hit'],
                          'origin': event['origin'], 'first_frame_ms': (monotonic()-started)*1000,
                          'pixel_sha256': sha256(pixels).hexdigest()}
                self.samples.append(sample)
                self.rate_samples.append(dict(self.receipt()['rates']))
                return sample, png
        raise ProfileFailure('qualification_deadline_exceeded')

    def request(self, name, frame, *, target_fps=None):
        if monotonic() >= self.deadline:
            raise ProfileFailure('qualification_deadline_exceeded')
        self.request_sequence += 1
        started = monotonic()
        self.stream.command(name, sequence=self.request_sequence, frame=frame, target_fps=target_fps)
        return self.receive(self.request_sequence, frame, started, name)

    def burst(self, frames):
        started = monotonic()
        for frame in frames:
            self.request_sequence += 1
            self.stream.command('seek', sequence=self.request_sequence, frame=frame)
        return self.receive(self.request_sequence, frames[-1], started, 'latest_intent_burst')

    def inflight_burst(self, frames):
        """Supersede a confirmed active native render, then its pending cursor."""
        self.request_sequence += 1
        started = monotonic()
        self.stream.command('seek', sequence=self.request_sequence, frame=frames[0])
        _wait(self.stream, lambda: self.stream.foreground_active, self.deadline)
        for frame in frames[1:]:
            self.request_sequence += 1
            self.stream.command('seek', sequence=self.request_sequence, frame=frame)
        return self.receive(self.request_sequence, frames[-1], started, 'inflight_latest_intent_burst')

    def paused_q5(self, frame):
        """Use actual stopped-anchor refinement when hardware cannot hold Q5."""
        delivered = self.request('pause', frame)
        if delivered[0]['profile'] == 'Q5':
            return delivered
        sequence = self.request_sequence
        _wait(self.stream, lambda: any(row['type'] == 'frame-ready'
              and row.get('cursor_seq') == sequence and row.get('frame') == frame
              and row.get('origin') == 'refinement' and row.get('quality_rank') == 5
              for row in self.stream.events), self.deadline)
        return self.request('seek', frame)

    def edited(self):
        _retire(self.api)
        draft = deepcopy(self.current_data)
        draft['layers'][0]['opacity'] = .2
        before = monotonic()
        reply = self.api.open({'project': draft, 'adaptive': True, 'refine': False,
                              'frame': 1, 'target_fps': 60})
        self.stream = self.api.get(reply['session'])
        self.request_sequence = 0
        self.current_data = draft
        delivered = self.receive(0, 1, before, 'edited_draft')
        return reply['invalidation'], delivered

    def receipt(self):
        with self.stream.condition:
            receipt = self.stream.qualification()
        if receipt['rates']['acknowledgement_source'] != 'harness_png_decode':
            raise ProfileFailure('acknowledgement_scope_missing')
        return receipt

    def measurements(self):
        return {'request_count': len(self.samples),
                'request_first_frame_ms': _percentiles([row['first_frame_ms'] for row in self.samples]),
                'sampled_fresh_fps': _percentiles([row['fresh_fps'] for row in self.rate_samples
                                                 if row['fresh_fps'] is not None]),
                'sampled_decode_fps': _percentiles([row['decoded_fps'] for row in self.rate_samples
                                                  if row['decoded_fps'] is not None]),
                'rate_scope': 'sampled_server_window_rates_and_harness_decode_acknowledgements'}

    def close(self):
        try:
            _retire(self.api)
            self.api.shutdown()
        except BaseException:
            self.cleanup_ok = False
            raise


def _native_reference(path, data, frame):
    project = Project(data, path.parent)
    compositor = Compositor(project)
    try:
        image, _ = compositor.frame(frame, apply_quality=True)
        return image.convert('RGBA').tobytes(), frame_clock(project, frame)
    finally:
        compositor.close()


def _exact_reference(path, data, delivered):
    row, png = delivered
    if row['profile'] != 'Q5':
        raise ProfileFailure('q5_reference_received_lower_rank')
    expected, clock = _native_reference(path, data, row['frame'])
    _, actual = _decode(png)
    if actual != expected:
        raise ProfileFailure('native_q5_pixel_mismatch')
    return {'frame': row['frame'], 'profile': 'Q5', 'rgba8_tolerance': 0,
            'pixel_sha256': sha256(actual).hexdigest(), 'exact_match': True,
            'source_seconds': clock.source_seconds, 'continuous_beat': clock.continuous_beat,
            'output_time': [clock.output_time_numerator, clock.output_time_denominator]}


def _disk_bytes(directory):
    # The directory is exclusively owned by this run and contains synthetic data.
    return sum(p.stat().st_size for p in directory.rglob('*') if p.is_file() and not p.is_symlink())


def native_profile(working, *, size, seeks, backend, deadline):
    path, data = fixture(working/'fixture', size, backend)
    originals = {p.name: sha256(p.read_bytes()).hexdigest() for p in path.parent.iterdir() if p.is_file()}
    cases, gallery, parity = [], [], []
    owners = []
    try:
        baseline = NativeRun(path, working/'baseline', refine=True, deadline=deadline, backend=backend)
        owners.append(baseline)
        parity.append(_exact_reference(path, data, baseline.initial))
        gallery.append(('Native Q5 · frame 0', baseline.initial[1]))
        warm = baseline.request('seek', 0)
        if not warm[0]['cache_hit']:
            raise ProfileFailure('warm_seek_missed_cache')
        rng = random.Random(613)
        sequence = [rng.randrange(120) for _ in range(seeks)]
        for frame in sequence:
            baseline.request('seek', frame)
        burst_frames = [rng.randrange(120) for _ in range(max(24, seeks))]
        latest, _ = baseline.burst(burst_frames)
        if latest['frame'] != burst_frames[-1]:
            raise ProfileFailure('latest_intent_presented_obsolete_frame')
        # HD/software-GPU work may legitimately lower foreground quality. The
        # stopped anchor must reach native Q5 through the shipping refiner;
        # arbitrary unthrottled seek speed is not a pixel-correctness gate.
        preserved = baseline.paused_q5(91)
        parity.append(_exact_reference(path, data, preserved))
        for target in (15, 24, 30, 60, 120):
            baseline.request('target_fps', 91, target_fps=target)
            if baseline.stream.target_fps != target:
                raise ProfileFailure('target_control_not_applied')
        original_receipt = baseline.receipt()
        invalidation, changed = baseline.edited()
        retained = baseline.request('seek', 91)
        if not retained[0]['cache_hit'] or retained[1] != preserved[1]:
            raise ProfileFailure('independent_q5_not_preserved')
        if not invalidation['invalidated_frames'] or not invalidation['retained_sections']:
            raise ProfileFailure('edit_invalidation_not_observed')
        parity.append(_exact_reference(path, baseline.current_data, changed))
        parity.append(_exact_reference(path, baseline.current_data, retained))
        gallery.append(('Edited local layer · native Q5', changed[1]))
        gallery.append(('Unaffected section retained · native Q5', retained[1]))
        cases.append({'name': 'native_seek_and_edit', 'measurement': 'native_unthrottled',
            'random_seeks': seeks, 'latest_intent_burst_commands': len(burst_frames),
            'latest_frame_only_accepted': True, 'independent_q5_preserved': True,
            'observations': baseline.measurements(),
            'warm_cached_first_frame_ms': warm[0]['first_frame_ms'],
            'before_edit': original_receipt, 'after_edit': baseline.receipt(),
            'gpu': list(baseline.stream.renderer.gpu_observations)})
        baseline.close()

        # The delay is an explicitly injected service cost around the unchanged
        # native renderer. It makes adaptive downgrade/refinement reproducible.
        pressure = NativeRun(path, working/'pressure', delay_ms=70, refine=True,
                             deadline=deadline, backend=backend)
        owners.append(pressure)
        latest = pressure.inflight_burst(list(range(20, 44)))
        if latest[0]['frame'] != 43:
            raise ProfileFailure('inflight_seek_presented_obsolete_frame')
        burst_receipt = pressure.receipt()
        if not burst_receipt['counters']['stale_results']:
            raise ProfileFailure('inflight_obsolete_work_not_observed')
        for frame in range(1, 17):
            pressure.request('play' if frame == 1 else 'advance', frame)
        if pressure.stream.controller.rank >= 5:
            raise ProfileFailure('injected_service_pressure_not_detected')
        pressure.stream.renderer.delay_ms = 0
        refined = pressure.paused_q5(82)
        parity.append(_exact_reference(path, data, refined))
        gallery.append(('Paused anchor upgraded by native Q5 worker', refined[1]))
        cases.append({'name': 'injected_service_pressure_and_native_refinement',
            'measurement': 'native_pixels_with_injected_70ms_service_delay',
            'injected_delay_ms': 70, 'refinement_anchor': 82,
            'inflight_burst_commands': 24, 'inflight_latest_frame_only_accepted': True,
            'obsolete_native_results_rejected': burst_receipt['counters']['stale_results'],
            'anchor_q5_exact': True, 'receipt': pressure.receipt(), 'observations': pressure.measurements(),
            'gpu': list(pressure.stream.renderer.gpu_observations)})
        pressure.close()

        low_cache = NativeRun(path, working/'cache-pressure', deadline=deadline, backend=backend)
        owners.append(low_cache)
        cache = low_cache.stream.cache
        # Bound by observed PNG bytes, so 1080p/4K gets the same small retained
        # frame count instead of rejecting its first legitimate full-size PNG.
        with low_cache.stream.condition:
            cache.max_bytes = max(4096, len(low_cache.initial[1])*3)
            original_write = cache._write_blob
            def delayed_write(*args, **kwargs):
                sleep(.012)
                return original_write(*args, **kwargs)
            cache._write_blob = delayed_write
        for frame in range(1, 25):
            low_cache.request('play' if frame == 1 else 'advance', frame)
        stats = cache.stats()
        if stats['ram_bytes'] != 0 or stats['disk_bytes'] > stats['max_bytes'] or not stats['evictions']:
            raise ProfileFailure('cache_pressure_not_bounded')
        cases.append({'name': 'injected_slow_writes_and_tight_cache',
            'measurement': 'native_pixels_with_injected_cache_io_delay',
            'injected_write_delay_ms': 12, 'png_ram_budget_bytes': 0,
            'png_disk_budget_bytes': cache.max_bytes, 'evictions_observed': stats['evictions'],
            'receipt': low_cache.receipt(), 'observations': low_cache.measurements(),
            'gpu': list(low_cache.stream.renderer.gpu_observations)})
        low_cache.close()

        allocation = NativeRun(path, working/'allocation-pressure', allocation_failures=1,
                               deadline=deadline, backend=backend)
        owners.append(allocation)
        if allocation.initial[0]['profile'] != 'Q4':
            raise ProfileFailure('allocation_pressure_did_not_recover')
        cases.append({'name': 'injected_allocation_pressure',
            'measurement': 'one_explicit_memory_error_then_native_render',
            'injected_failures': 1, 'recovered_profile': 'Q4', 'receipt': allocation.receipt(),
            'observations': allocation.measurements(),
            'gpu': list(allocation.stream.renderer.gpu_observations)})
        allocation.close()
        if originals != {p.name: sha256(p.read_bytes()).hexdigest()
                         for p in path.parent.iterdir() if p.is_file()}:
            raise ProfileFailure('source_fixture_was_modified')
        return {'cases': cases, 'q5_parity': parity, 'source_inputs_unchanged': True,
                'owned_working_file_bytes_before_cleanup': _disk_bytes(working),
                'native_contexts_retired': True}, gallery
    finally:
        cleanup_failed = False
        for owner in owners:
            try:
                owner.close()
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise ProfileFailure('worker_cleanup_failed')


def source_identity(revision=None):
    package = Path(__file__).resolve().parent
    checkout = package.parents[1]
    try:
        actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout,
                                         stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=checkout,
                                             stderr=subprocess.DEVNULL, text=True, timeout=5).strip())
    except (OSError, subprocess.SubprocessError):
        actual, dirty = None, None
    if revision is not None and (not isinstance(revision, str) or len(revision) != 40
            or any(c not in '0123456789abcdef' for c in revision) or revision != actual):
        raise ValueError('expected source revision must match the actual checkout')
    if not isinstance(actual, str) or len(actual) != 40 or any(c not in '0123456789abcdef' for c in actual):
        actual = None
    versions = runtime_versions()
    for name in ('numpy', 'moderngl'):
        try:
            found = metadata.version(name)
        except metadata.PackageNotFoundError:
            found = None
        versions[name] = found if isinstance(found, str) and _VERSIONS.fullmatch(found) else None
    operating_system = platform.system()
    return {'commit': actual, 'working_tree_dirty': dirty,
            'implementation_sha256': implementation_fingerprint(),
            'harness_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'python_version': versions['python'],
            'platform': operating_system if operating_system in ('Linux', 'Windows', 'Darwin') else 'Other',
            'versions': versions}


def _atomic_json(path, data):
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=True, allow_nan=False)
        stream.write('\n')
    os.replace(temporary, path)


def gallery_html(report, frames):
    """A standalone artifact with actual native PNGs and an embedded JSON download."""
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False).encode('utf-8')
    download = 'data:application/json;base64,'+base64.b64encode(payload).decode('ascii')
    cards = ''.join('<figure><img alt="'+html.escape(label, quote=True)+'" src="data:image/png;base64,'+
        base64.b64encode(png).decode('ascii')+'"><figcaption>'+html.escape(label)+'</figcaption></figure>'
        for label, png in frames[:_MAX_GALLERY_FRAMES])
    rows = []
    for case in report.get('native', {}).get('cases', []):
        receipt = case.get('receipt', case.get('after_edit'))
        cache = receipt['cache']
        latencies = receipt['latency_ms']
        def cell(value):
            return 'unavailable' if value is None else f'{value:.2f}' if isinstance(value, float) else str(value)
        rows.append('<tr>'+''.join('<td>'+html.escape(cell(value))+'</td>' for value in (
            case['name'].replace('_', ' '), latencies['render']['p50'], latencies['render']['p95'],
            latencies['queue']['p95'], cache['frames'], cache['q5_frames'], cache['bytes'], cache['evictions']))+'</tr>')
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>FrameRudiments S7 preview qualification</title><style>
body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#101826;color:#e8edf6}main{max-width:1200px;margin:auto;padding:32px}
h1{font-size:2.1rem;margin:0 0 8px}h2{margin-top:34px}.note{color:#bdcbe0;max-width:90ch}a{color:#91dfcd}code{word-break:break-all}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}figure{margin:0;padding:14px;background:#1a283e;border-radius:10px}
img{display:block;width:100%;height:auto;background:#080d15}figcaption{padding-top:10px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}
th,td{text-align:left;border-bottom:1px solid #3b4960;padding:10px;white-space:nowrap}th{color:#acd9ff}.pill{display:inline-block;background:#254d40;padding:5px 12px;border-radius:20px}
</style><main><h1>Native preview qualification</h1><p class="pill">'''+html.escape(report['status'])+'''</p>
<p class="note">Actual FrameRudiments native output from generated, pinned fixtures. Preview correctness and measured throughput are separate from release or perceptual approval.
Decode acknowledgements come from this Python harness; no browser paint or physical-display rate is measured.</p><p><a download="qualification.json" href="'''+download+'''">Download complete qualification JSON</a></p>
<p>Source commit: <code>'''+html.escape(str(report['source']['commit']))+'''</code><br>Implementation: <code>'''+report['source']['implementation_sha256']+'''</code></p>
<h2>Native frames with exact Q5 comparison</h2><div class="cards">'''+cards+'''</div>
<h2>Observed session metrics</h2><p class="note">Service and queue times are milliseconds. Cache bytes are indexed PNG payloads. Pressure cases explicitly inject delays or constrained cache budgets and must not be interpreted as unthrottled machine benchmarks.</p>
<div class="scroll"><table><thead><tr><th>Case</th><th>Render p50</th><th>Render p95</th><th>Queue p95</th><th>Cached frames</th><th>Q5 frames</th><th>PNG bytes</th><th>Evictions</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div>
<h2>Qualification scope</h2><p class="note">The report also contains the real controller's sixteen virtual-cost cases (5/12/30/70 ms at 24/30/60/120 FPS), target changes, random and latest-intent seeks, native spawned refinement, local-edit cache retention, zero-RAM-cache/tight-disk pressure and one injected allocation failure.
GPU mode records the actual native graph route and available driver memory hints; unavailable values remain unavailable. Software EGL does not qualify a physical adapter. External feedback-engine, encoded-output and human perceptual acceptance remain separate gates.</p></main></html>'''


def run_profile(output, *, preset='standard', size='small', backend='cpu', revision=None,
                max_seconds=180):
    if preset not in PRESETS or size not in SIZES or backend not in ('cpu', 'gpu'):
        raise ValueError('unsupported qualification configuration')
    if type(max_seconds) not in (int, float) or not 10 <= max_seconds <= 1800:
        raise ValueError('qualification deadline must be 10..1800 seconds')
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('qualification output must be a new directory')
    temporary_preflight(output.parent)
    identity = source_identity(revision)
    output.mkdir()
    working = Path(tempfile.mkdtemp(prefix='owned-working-', dir=output))
    started, cpu_started = monotonic(), process_time()
    report = {'schema': SCHEMA, 'status': 'running', 'synthetic_inputs': True,
              'source': identity, 'preset': preset, 'resolution': list(SIZES[size]), 'backend_requested': backend,
              'acknowledgement_source': 'harness_png_decode', 'physical_display_measured': False,
              'performance_thresholds_are_observations': True,
              'deadline_seconds': max_seconds, 'deadline_scope': 'between operations; CI owns blocked-native job timeout',
              'release_gates': {'exact_change_ci': False, 'external_feedback_engines': False,
                                'physical_gpu': False, 'encoded_output': False, 'perceptual_approval': False}}
    frames = []
    error = None
    try:
        report['controller_matrix'] = controller_matrix()
        report['native'], frames = native_profile(working, size=SIZES[size], seeks=PRESETS[preset],
                                                   backend=backend, deadline=started+max_seconds)
        if implementation_fingerprint() != identity['implementation_sha256']:
            raise ProfileFailure('implementation_changed_during_qualification')
        report['status'] = 'passed'
    except BaseException as exc:
        error = exc
        report['status'] = 'failed'
        report['failure'] = {'code': str(exc) if isinstance(exc, ProfileFailure) else 'qualification_failed',
                             'error_type': type(exc).__name__}
        if implementation_fingerprint() != identity['implementation_sha256']:
            report['failure']['code'] = 'implementation_changed_during_qualification'
    finally:
        clean = not isinstance(error, ProfileFailure) or str(error) != 'worker_cleanup_failed'
        if clean:
            try:
                shutil.rmtree(working)
            except OSError:
                clean = False
        if not clean:
            report['status'] = 'failed'
            report['failure'] = {'code': 'worker_cleanup_failed', 'error_type': 'ProfileFailure'}
            error = ProfileFailure('worker_cleanup_failed')
        report['cleanup'] = {'owned_working_tree_removed': clean}
        report['resources'] = {'wall_seconds': monotonic()-started,
                               'parent_cpu_seconds': process_time()-cpu_started,
                               'cpu_scope': 'harness_parent_including_foreground_not_spawned_refinement',
                               'gpu_scope': 'owner_thread_native_graph_samples_when_gpu_requested',
                               'rss_and_driver_memory': 'see per-session resources and gpu samples; unavailable is not zero'}
        report['receipt_sha256'] = canonical_digest(report)
        _atomic_json(output/'qualification.json', report)
        if report['status'] == 'passed':
            temporary = output/'index.html.tmp'
            temporary.write_text(gallery_html(report, frames), encoding='utf-8')
            os.replace(temporary, output/'index.html')
    if error:
        raise ProfileFailure(report['failure']['code']) from error
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New artifact directory; never overwritten')
    parser.add_argument('--preset', choices=PRESETS, default='standard')
    parser.add_argument('--size', choices=SIZES, default='small')
    parser.add_argument('--backend', choices=('cpu', 'gpu'), default='cpu', help='GPU is strict, with no fallback')
    parser.add_argument('--revision', help='Require this exact source checkout SHA')
    parser.add_argument('--max-seconds', type=int, default=180)
    args = parser.parse_args(argv)
    try:
        report = run_profile(args.output, preset=args.preset, size=args.size, backend=args.backend,
                             revision=args.revision, max_seconds=args.max_seconds)
    except (ProfileFailure, ValueError, OSError) as exc:
        code = str(exc) if isinstance(exc, ProfileFailure) else type(exc).__name__
        parser.exit(1, f'PREVIEW QUALIFICATION FAILED: {code}\n')
    print(json.dumps({'status': report['status'], 'schema': report['schema'],
                      'receipt_sha256': report['receipt_sha256'], 'random_seeks': PRESETS[args.preset],
                      'native_cases': len(report['native']['cases']),
                      'controller_cases': len(report['controller_matrix']['rows']),
                      'outputs': ['qualification.json', 'index.html']}))


if __name__ == '__main__':
    main()
