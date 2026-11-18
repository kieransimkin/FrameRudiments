"""S3 persistent, thread-owned native compositor and owned temporary sidecars."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import get_ident
from time import perf_counter

from PIL import Image

from .preview_quality import PinnedInputGuard, render_project_view
from .preview_stream_contract import _integer


def _sidecar(rows, *, cues=False):
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError('invalid preview sidecar')
    values = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('label'), str):
            raise ValueError('invalid source timing row')
        second, label = row.get('start'), row['label']
        if type(second) not in (float, int) or not 0 <= second < 360000:
            raise ValueError('invalid source timestamp')
        if '\n' in label or '\r' in label:
            raise ValueError('multiline timing label')
        if cues and not re.fullmatch(r'\[(?:SECTION|ENTRY|DROP|BREAK):\s*[^\]\r\n]{1,120}\]', label):
            raise ValueError('edited cues require SECTION, ENTRY, DROP or BREAK')
        values.append((round(second * 1000), label))
    values.sort(key=lambda row: row[0])
    return ''.join(f'[{ms // 60000:02d}:{(ms // 1000) % 60:02d}.{ms % 1000:03d}]{label}\n'
                   for ms, label in values)


class NativePreviewRenderer:
    """Allocate/close GL and Pillow resources on the foreground worker only.

    At most one quality-specific compositor exists. A quality change closes it
    and constructs a new, correctly sized view; it never rescales final pixels.
    """
    def __init__(self, project_file, draft, sidecars, *, validate_draft, temp_parent, verifier=None):
        self.path = Path(project_file)
        self.draft, self.sidecars = deepcopy(draft), deepcopy(sidecars)
        self.validate, self.verifier = validate_draft, verifier
        self.temp_parent = Path(temp_parent)
        self.owner = self.project = self.compositor = self.profile = self.temporary = None
        self.closed = False
        self.last_setup_ms = 0
        self.input_guard = None
        self._feedback_resume = None
        self._feedback_failure = None
        from .preview_seek_support import seek_status
        post = self.draft.get('post_effects', {})
        self._stateful_status = (seek_status(self.draft)
                                 if isinstance(post, dict) and post.get('feedback') is not None else {})

    @property
    def stateful_status(self):
        # External S6 adapters provide their own read-only replay property.
        # Keep the native status private so base construction respects that API.
        return dict(self._stateful_status)

    def _thread(self):
        current = get_ident()
        if self.owner is None:
            self.owner = current
        elif self.owner != current:
            raise RuntimeError('Native compositor/context must stay on its owning thread')

    def _prepare(self):
        from .project import Project

        original = Project.load(self.path)
        self.validate(original.data, self.draft)
        document = deepcopy(self.draft)
        self.temp_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='probe-', dir=self.temp_parent) as probe:
            path = Path(probe) / 'probe'
            path.write_bytes(b'preview-temp-probe')
            if path.read_bytes() != b'preview-temp-probe':
                raise OSError('preview temp readback failed')
            path.replace(Path(probe) / 'renamed')
        self.temporary = TemporaryDirectory(prefix='session-', dir=self.temp_parent)
        try:
            fields = {'lines': 'lyrics', 'words': 'word_lyrics', 'cues': 'cues'}
            if not isinstance(self.sidecars, dict) or set(self.sidecars) - set(fields):
                raise ValueError('invalid preview sidecars')
            for kind, rows in self.sidecars.items():
                if fields[kind] not in document:
                    raise ValueError('New sidecar input requires explicit pinning')
                data = _sidecar(rows, cues=kind == 'cues').encode('utf-8')
                path = Path(self.temporary.name) / f'{kind}.lrc'
                path.write_bytes(data)
                document[fields[kind]] = {'path': str(path), 'sha256': sha256(data).hexdigest()}
            self.project = Project(document, original.base)
            self.project.verify_inputs()
            self.input_guard = PinnedInputGuard(original.base, document, self.project.inputs)
        except BaseException:
            self.project = self.input_guard = None
            self.temporary.cleanup()
            self.temporary = None
            raise

    def __call__(self, frame, profile_id):
        return self.render_cancellable(frame, profile_id, lambda: False)

    def _retire_feedback(self, frame, progress, reason='input_identity_changed'):
        """Revoke live and suspended state before reporting a changed identity."""
        from .preview_replay import StatefulSeekUnavailable

        self._feedback_resume = None
        owner, self.compositor, self.profile = self.compositor, None, None
        try:
            if owner is not None:
                owner.close()
        except Exception:
            reason = 'native_cleanup_failed'
            self.cleanup_error = True
        self._feedback_failure = reason
        self._stateful_status = {**self._stateful_status, 'phase': 'unavailable',
                                'requested_frame': frame, 'last_reason': reason,
                                'checkpoint_count': 0, 'live_next_frame': 0,
                                'restored_from_frame': None}
        if progress is not None:
            progress(dict(self._stateful_status))
        raise StatefulSeekUnavailable(reason) from None

    def _verify_preview_inputs(self, frame, progress, feedback):
        try:
            (self.verifier or self.project.verify_inputs)()
            if self.input_guard:
                self.input_guard()
        except (ValueError, OSError):
            if feedback:
                self._retire_feedback(frame, progress)
            raise

    def render_cancellable(self, frame, profile_id, cancelled, progress=None):
        """Reconstruct native feedback cooperatively on the owning worker.

        Existing independent/shutter frames keep their ordinary rendering path.
        Only bounded content-free counters leave a native checkpoint owner.
        """
        from .compositor import Compositor
        from .feedback_history import FeedbackCancelled, FeedbackUnavailable
        from .preview_refinement_process import RefinementCancelled
        from .preview_replay import StatefulSeekUnavailable

        self._thread()
        if self.closed:
            raise RuntimeError('preview renderer is closed')
        if not callable(cancelled) or (progress is not None and not callable(progress)):
            raise ValueError('preview cancellation and progress must be callable')
        if self._feedback_failure is not None:
            self._retire_feedback(frame, progress, self._feedback_failure)
        if cancelled():
            raise RefinementCancelled('superseded native preview')
        self.last_setup_ms = 0
        if self.project is None:
            start = perf_counter()
            self._prepare()
            self.last_setup_ms += (perf_counter()-start)*1000
        _integer(frame, 'absolute frame', 0, self.project.frame_count - 1)
        feedback = self.project.data.get('post_effects', {}).get('feedback') is not None
        self._verify_preview_inputs(frame, progress, feedback)
        if self.profile != profile_id:
            start = perf_counter()
            # Native shutters and feedback own their admitted reconstruction.
            # External feedback uses the separate S6 EffectChain dispatcher.
            view = render_project_view(self.project, profile_id,
                                       allow_stateful=not bool(self.project.data.get('effects')))
            if self.compositor is not None:
                self.compositor.close()
                self.compositor = self.profile = None
            self.compositor = Compositor(view)
            self.profile = profile_id
            if self._feedback_resume is not None:
                saved_profile, checkpoint, expected = self._feedback_resume
                self._feedback_resume = None
                if saved_profile == profile_id:
                    try:
                        self.compositor.restore_feedback(checkpoint, expected_sha256=expected)
                    except (ValueError, OSError):
                        self._retire_feedback(frame, progress, 'runtime_identity_changed')
                    except BaseException:
                        self.compositor.close()
                        self.compositor = self.profile = None
                        raise
            self.last_setup_ms += (perf_counter()-start)*1000
        def report(snapshot):
            if not isinstance(snapshot, dict):
                return
            keys = ('phase', 'requested_frame', 'current_request_frames', 'replayed_frames',
                    'live_next_frame', 'max_request_frames', 'checkpoint_count', 'restored_from_frame')
            self._stateful_status = {**self._stateful_status,
                **{key: snapshot[key] for key in keys if key in snapshot},
                'enabled': True, 'mode': 'checkpoint_replay', 'last_reason': None}
            if progress is not None:
                progress(dict(self.stateful_status))

        try:
            if feedback:
                image, _ = self.compositor.frame(frame, apply_quality=True,
                                                  cancelled=cancelled, progress=report)
                report(self.compositor.feedback_snapshot())
            else:
                image, _ = self.compositor.frame(frame, apply_quality=True)
        except FeedbackCancelled as exc:
            report(self.compositor.feedback_snapshot())
            raise RefinementCancelled('superseded native feedback') from exc
        except FeedbackUnavailable as exc:
            self._stateful_status = {**self._stateful_status, 'phase': 'unavailable',
                                    'requested_frame': frame, 'last_reason': exc.reason_code}
            if progress is not None:
                progress(dict(self.stateful_status))
            raise StatefulSeekUnavailable(exc.reason_code) from exc
        except BaseException:
            session = getattr(self.compositor, '_feedback_session', None)
            if feedback and session is not None and session.revoked:
                self._retire_feedback(frame, progress)
            try:
                self.compositor.close()
            finally:
                self.compositor = self.profile = None
            raise
        if cancelled():
            raise RefinementCancelled('superseded native preview')
        view = self.compositor.project
        if not isinstance(image, Image.Image) or image.mode not in ('RGB', 'RGBA') or image.size != (view.width, view.height):
            raise RuntimeError('native preview must produce exact RGBA8 profile dimensions')
        self._verify_preview_inputs(frame, progress, feedback)
        if cancelled():
            raise RefinementCancelled('superseded native preview')
        # The native final-frame API applies its own deterministic black matte
        # and can return RGB. Add opaque alpha only for the cache wire format;
        # do not resize, recompose or apply another quality pass here.
        if image.mode == 'RGB':
            image = image.convert('RGBA')
        buffer = BytesIO()
        image.save(buffer, format='PNG', compress_level=1)
        return buffer.getvalue()

    def release_compositor(self):
        """S4 yield native resources on the foreground's own thread while paused.

        Keep the immutable project/sidecars; a later foreground call rebuilds its
        quality view and records setup time separately from the render service.
        """
        self._thread()
        feedback = self.project is not None and self.project.data.get('post_effects', {}).get('feedback') is not None
        if feedback:
            self._verify_preview_inputs(self._stateful_status.get('requested_frame'), None, True)
        try:
            if self.compositor is not None:
                owner = self.compositor
                try:
                    if feedback:
                        checkpoint = owner.feedback_checkpoint()
                        if checkpoint is not None:
                            # Retain one immutable, bounded native state while the
                            # renderer/context closes; never serialize opaque GPU state.
                            self._feedback_resume = (self.profile, checkpoint, checkpoint.digest)
                except (ValueError, OSError):
                    if feedback:
                        self._retire_feedback(self._stateful_status.get('requested_frame'), None)
                    raise
                finally:
                    owner.close()
        finally:
            self.compositor = self.profile = None

    def close(self):
        self._thread()
        if self.closed:
            return
        self.closed = True
        try:
            if self.compositor is not None:
                self.compositor.close()
        finally:
            self.compositor = None
            self._feedback_resume = None
            if self.temporary is not None:
                self.temporary.cleanup()
                self.temporary = None
