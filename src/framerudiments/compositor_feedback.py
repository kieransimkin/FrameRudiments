"""Explicit encoded-frame feedback with native clocks and owned checkpoints.

The recurrence follows native subframe/history-shutter composition and precedes
final output quality and sequential external visualisers. Checkpoints contain
only this adapter's RGB recurrence state, never an opaque engine or GPU state.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from pathlib import PurePosixPath
import platform

from PIL import Image

from .io import fields, integer, number
from .preview_stream_contract import canonical_digest
from .quality_effects import linear_shutter_composite

SCHEMA = 'framerudiments.native-feedback.v1'
MAX_NATIVE_DELAY = 64
MAX_CHECKPOINT_INTERVAL = 3600
MAX_CHECKPOINTS = 64
MAX_REPLAY_FRAMES = 36000
MAX_STATE_BYTES = 512 * 1024 * 1024


class FeedbackSettings:
    """Validated declarative recurrence; native modulators drive its mix only."""

    def __init__(self, project, config, context_at):
        fields(config, {'delay_frames', 'mix', 'reset', 'checkpoint_interval',
                        'max_checkpoints', 'max_replay_frames', 'max_state_bytes',
                        'modulations'}, 'post_effects.feedback')
        if 'delay_frames' not in config:
            raise ValueError('post_effects.feedback requires an explicit positive delay_frames')
        self.base = {
            'delay_frames': integer(config['delay_frames'], 'feedback delay_frames',
                                    minimum=1, maximum=MAX_NATIVE_DELAY),
            'mix': number(config.get('mix', .5), 'feedback mix', minimum=0, maximum=1),
            'reset': config.get('reset', 'repeat'),
            'checkpoint_interval': integer(config.get('checkpoint_interval', 30),
                'feedback checkpoint_interval', minimum=1, maximum=MAX_CHECKPOINT_INTERVAL),
            'max_checkpoints': integer(config.get('max_checkpoints', 8),
                'feedback max_checkpoints', minimum=1, maximum=MAX_CHECKPOINTS),
            'max_replay_frames': integer(config.get('max_replay_frames', 3600),
                'feedback max_replay_frames', minimum=1, maximum=MAX_REPLAY_FRAMES),
            'max_state_bytes': integer(config.get('max_state_bytes', 256 * 1024 * 1024),
                'feedback max_state_bytes', minimum=1, maximum=MAX_STATE_BYTES),
        }
        if self.base['reset'] not in ('repeat', 'programme'):
            raise ValueError('feedback reset must be repeat or programme')
        rows = config.get('modulations', [])
        if not isinstance(rows, list) or len(rows) > 1:
            raise ValueError('feedback modulations must contain at most one mix driver')
        from .modulation import _Modulator

        self.driver = None
        if rows:
            row = rows[0]
            if not isinstance(row, dict) or row.get('target') != 'feedback.mix':
                raise ValueError('feedback modulation target must be feedback.mix')
            proxy = dict(row, target='visual.opacity')
            proxy.setdefault('range', [0, 1])
            self.driver = _Modulator(proxy, project, context_at)
        self.files = list(self.driver.motion.files) if self.driver is not None else []
        self.evidence = {
            'config': dict(self.base),
            'modulations': [dict(self.driver.evidence, target='feedback.mix')] if self.driver else [],
            'initial_history': 'current_native_frame_when_delayed_state_is_missing',
            'blend': 'linear_light_rgb8_current_and_delayed_feedback_output',
            'stage': 'after_native_subframes_and_shutter_before_final_quality_and_external_effects',
        }

    def parameters(self, context):
        value, reports = self.base['mix'], {}
        if self.driver is not None:
            result = self.driver.output(context)
            if result is not None:
                from .modulation import _combine

                selected, source = result
                value = max(0, min(1, _combine(value, selected, self.driver.combine, self.driver.weight)))
                reports['feedback.mix'] = {'value': value, 'native_value': source}
        return value, reports


def _backend_identity(compositor):
    backend = compositor.effect_graph_gpu
    if backend is None:
        actual = ('uninitialized' if compositor.effect_graph_backend_mode != 'cpu'
                  and not compositor._owns_effect_graph_gpu else 'cpu')
        return {'requested': compositor.effect_graph_backend_mode, 'actual': actual}
    context = getattr(backend, 'ctx', None)
    info = getattr(context, 'info', {})
    return {
        'requested': compositor.effect_graph_backend_mode, 'actual': 'gpu',
        'capabilities': dict(vars(backend.capabilities)),
        'driver': {key: info.get(key) for key in (
            'GL_VENDOR', 'GL_RENDERER', 'GL_VERSION', 'GL_SHADING_LANGUAGE_VERSION')},
    }


def _configuration(compositor):
    project = compositor.project
    references = [*(project.canonical_inputs or []), *project.inputs]
    return {
        'schema': SCHEMA, 'document': _pin_roles(project.data),
        'clock': {name: getattr(project, name) for name in (
            'fps', 'frame_count', 'sample_rate', 'start_sample', 'end_sample', 'repeats')},
        'canvas': [project.width, project.height], 'quality': compositor.output_quality.evidence,
        'pins': sorted({(ref['sha256'], _suffix(ref['path'])) for ref in references}),
        'feedback': _pin_roles(compositor.post_pipeline.feedback.evidence),
        'backend': _backend_identity(compositor),
    }


def _suffix(path):
    return PurePosixPath(str(path).replace('\\', '/')).suffix.lower()


def _pin_roles(value):
    """Bind content at its document role, independently of owned file placement.

    Extensions retain parser distinctions such as CUE versus LRC. Unpinned
    fields remain exact configuration. Actual filesystem change fences remain
    attached to the separately admitted aliases, not these portable identities.
    """
    if isinstance(value, dict):
        result = {key: _pin_roles(item) for key, item in value.items()}
        for path, digest in (('path', 'sha256'), ('shader_path', 'shader_sha256')):
            if path in value and digest in value:
                result[path] = {'pinned_content_format_suffix': _suffix(value[path])}
        return result
    if isinstance(value, (list, tuple)):
        return [_pin_roles(item) for item in value]
    return value


class NativeFeedback:
    """One compositor's identity-bound feedback history and reconstruction."""

    def __init__(self, compositor):
        from .feedback_history import DelayedFeedback
        from .preview_dependencies import implementation_fingerprint
        from .preview_quality import PinnedInputGuard

        self.compositor = compositor
        self.settings = compositor.post_pipeline.feedback
        self.configuration = canonical_digest(_configuration(compositor))
        self.implementation = implementation_fingerprint()
        self.runtime = {'python': platform.python_version(),
            'implementation': platform.python_implementation(), 'system': platform.system(),
            'machine': platform.machine(), 'python_build': list(platform.python_build())}
        self.identity = canonical_digest({'schema': SCHEMA, 'configuration': self.configuration,
            'implementation': self.implementation, 'runtime': self.runtime})
        project = compositor.project
        self.guard = PinnedInputGuard(project.base, project.data,
            [*(project.canonical_inputs or []), *project.inputs])
        self.revoked = False
        self.rendering = False
        self.cancelled = None
        self.last_report = None
        self.target_frame = self.target_output = None
        self.apply_quality = False
        config = self.settings.base
        self.engine = DelayedFeedback(self._evaluate, self.identity,
            frame_count=project.frame_count, result_bytes=project.width * project.height * 3,
            delays=(config['delay_frames'],),
            repeat_span=Fraction((project.end_sample - project.start_sample) * project.fps,
                                 project.sample_rate), reset=config['reset'],
            checkpoint_interval=config['checkpoint_interval'], max_checkpoints=config['max_checkpoints'],
            max_request_frames=config['max_replay_frames'], max_state_bytes=config['max_state_bytes'])

    def verify(self):
        if self.revoked:
            raise ValueError('feedback identity was revoked; create a new compositor')
        try:
            if canonical_digest(_configuration(self.compositor)) != self.configuration:
                raise ValueError('feedback configuration changed since admission')
            self.guard()
        except (ValueError, OSError):
            self.revoked = True
            if not self.rendering:
                self.engine.close()
            self.last_report = None
            raise

    def contract(self):
        project = self.compositor.project
        span = self.engine.repeat_span
        return {
            'schema': SCHEMA, 'identity': self.identity,
            'configuration_sha256': self.configuration,
            'implementation_sha256': self.implementation,
            'runtime': deepcopy(self.runtime),
            'config': dict(self.settings.base),
            'modulation_targets': ['feedback.mix'] if self.settings.driver is not None else [],
            **{key: self.settings.evidence[key] for key in ('initial_history', 'blend', 'stage')},
            'source_clock': {name: getattr(project, name) for name in (
                'fps', 'frame_count', 'sample_rate', 'start_sample', 'end_sample', 'repeats')},
            'repeat_span_frames': {
                'numerator': span.numerator, 'denominator': span.denominator},
            'backend': _backend_identity(self.compositor),
            'scope': {'native_compositor': 'pre_external_effects', 'checkpointed_state': 'owned_rgb8_feedback_history',
                'sequential_external_engine_checkpoints': False, 'gpu_state_serialization': False,
                'trusted_checkpoint_payload_with_separately_pinned_digest': True,
                'state_budget_excludes_native_sampling_rasters_renderer_caches_and_callback_scratch': True},
        }

    def snapshot(self):
        state = self.engine.snapshot()
        last = state.get('last_execution') or {}
        return {**state, 'mode': 'checkpoint_replay', 'enabled': True,
            'live_next_frame': state.get('next_frame', 0),
            'current_request_frames': state.get('current_request_frames', last.get('executed_steps', 0)),
            'replayed_frames': state.get('executed_steps', 0),
            'checkpoint_restore': True, 'native_scope': 'pre_external_effects',
            'last_native_parameters': deepcopy(self.last_report)}

    def plan(self, frame):
        self.verify()
        return self.engine.plan(frame)

    def _evaluate(self, frame, history):
        from .compositor_temporal_fx import render_temporal_fx_frame

        image = prior = pixels = None
        try:
            self.verify()
            image, context = render_temporal_fx_frame(self.compositor, frame,
                apply_quality=False, cancelled=self.cancelled)
            mix, modulations = self.settings.parameters(context)
            previous = history[0]
            if previous is not None:
                prior = Image.frombytes('RGB', image.size, previous)
                image = linear_shutter_composite([image, prior], [1 - mix, mix], strength=1)
            self.last_report = {'frame': frame, 'delay_frames': self.settings.base['delay_frames'],
                'history_frame': frame - self.settings.base['delay_frames'] if previous is not None else None,
                'mix': mix, 'modulations': modulations, 'source_seconds': context.source_seconds}
            self.compositor.post_pipeline.last_report['feedback'] = deepcopy(self.last_report)
            pixels = image.tobytes()
            if frame == self.target_frame:
                # Finish the requested output inside the transaction. Even AA
                # or a source guard failure must precede state publication.
                self.target_output = self.compositor._final_output(image, context, self.apply_quality)
            self.verify()
            return pixels
        finally:
            image = prior = pixels = None
            del image, prior, pixels

    def render(self, frame, *, apply_quality=True, cancelled=None, progress=None):
        from .compositor_temporal import _metadata, _restore, temporal_plan
        from .feedback_history import FeedbackCancelled, FeedbackUnavailable

        if self.rendering:
            raise FeedbackUnavailable('invalid_feedback_state')
        if self.revoked:
            self.verify()
        owner = self.compositor
        owner._last_temporal_schedule = None
        self.last_report = None
        baseline = _metadata(owner)
        self.rendering = True
        admitted = False

        def observe(receipt):
            if progress is not None:
                progress({**self.snapshot(), 'phase': receipt['phase'],
                    'restored_from_frame': receipt['start_frame'],
                    'current_request_frames': receipt['executed_steps'],
                    'origin': receipt['origin']})
            # The kernel calls this before publishing the completed request.
            # A changed pin or observer failure cannot certify new state.
            self.verify()

        try:
            self.engine.clear_request(frame)
            if type(apply_quality) is not bool:
                raise ValueError('feedback apply_quality must be boolean')
            if cancelled is not None and not callable(cancelled) or progress is not None and not callable(progress):
                raise ValueError('feedback cancellation/progress callbacks must be callable')
            check_cancelled = cancelled or (lambda: False)
            self.verify()
            plan = self.engine.plan(frame)
            # Admit every nested source schedule, including bounded temporal-FX
            # fan-out, before advancing even one committed feedback history frame.
            if owner.post_pipeline.temporal_fx is not None:
                from .compositor_temporal_fx import preflight_feedback_tap_work
                preflight_feedback_tap_work(owner, plan.start_frame, plan.target_frame,
                    check_cancelled)
            else:
                for index in range(plan.start_frame, plan.target_frame + 1):
                    if check_cancelled():
                        raise FeedbackCancelled('superseded native feedback request')
                    temporal_plan(owner, index)
            self.cancelled = check_cancelled
            self.target_frame, self.apply_quality = frame, apply_quality
            self.target_output = None
            admitted = True
            self.engine.render(frame, cancelled=check_cancelled, progress=observe)
            # State always stores pre-AA pixels; the detached target result
            # was already finished once before the kernel committed success.
            return self.target_output
        except BaseException as exc:
            _restore(owner, baseline)
            owner._last_temporal_schedule = None
            self.last_report = None
            if self.revoked:
                self.engine.close()
            elif not admitted:
                reason = ('superseded' if isinstance(exc, FeedbackCancelled) else
                    exc.reason_code if isinstance(exc, FeedbackUnavailable) else 'invalid_request')
                self.engine.clear_request(frame, reason_code=reason)
            virtual = getattr(owner, '_temporal_virtual', None)
            if virtual is not None:
                try:
                    virtual.close()
                finally:
                    owner.__dict__.pop('_temporal_virtual', None)
            raise
        finally:
            self.rendering = False
            self.cancelled = None
            self.target_frame = self.target_output = None
            self.apply_quality = False

    def checkpoint(self):
        self.verify()
        return self.engine.checkpoint()

    def restore(self, checkpoint, *, expected_sha256):
        self.verify()
        self.engine.restore(checkpoint, expected_sha256=expected_sha256)
        self.last_report = None
        self.compositor._last_temporal_schedule = None

    def close(self):
        self.engine.close()
        self.last_report = None


def feedback_session(compositor):
    """Create metadata/history ownership lazily; never draw at admission."""
    if compositor.post_pipeline.feedback is None:
        return None
    session = getattr(compositor, '_feedback_session', None)
    if session is None:
        session = NativeFeedback(compositor)
        compositor._feedback_session = session
    return session


def feedback_contract(compositor):
    session = feedback_session(compositor)
    return session.contract() if session is not None else None


def feedback_snapshot(compositor):
    session = feedback_session(compositor)
    return session.snapshot() if session is not None else None


def feedback_plan(compositor, frame):
    session = feedback_session(compositor)
    return session.plan(frame).document() if session is not None else None


def render_feedback_frame(compositor, frame, **kwargs):
    compositor._last_temporal_schedule = None
    return feedback_session(compositor).render(frame, **kwargs)
