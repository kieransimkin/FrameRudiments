"""S6 native Q5 prefix replay using the production composition/effect order.

External engines are qualified for the observed prefix on this runtime only.
Neither an unseeded projectM instance nor a seeded GPU engine is assumed to be
repeatable: every new prefix must agree byte-for-byte after a fresh reset.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib import metadata
from io import BytesIO
import os
from pathlib import Path
import shutil
import stat
from time import perf_counter

from PIL import Image

from .preview_native_renderer import NativePreviewRenderer
from .preview_quality import PinnedInputGuard, render_project_view
from .preview_replay import ReplayCancelled, StatefulSeekUnavailable, VerifiedPrefixReplay
from .preview_stream_contract import _integer, canonical_digest

STATEFUL_ABI = 'framerudiments.preview-stateful-prefix.v1'
MAX_PCM_BYTES = 256 * 1024**2
_ENVIRONMENT = (
    'FRAMERUDIMENTS_FFMPEG', 'FRAMERUDIMENTS_FFPROBE', 'FRAMERUDIMENTS_NODE',
    'FRAMERUDIMENTS_BROWSER', 'FRAMERUDIMENTS_BUTTERCHURN_RUNTIME',
    'FRAMERUDIMENTS_PROJECTM_LIBRARY', 'FRAMERUDIMENTS_PROJECTM_DLL_DIRS',
    'FRAMERUDIMENTS_TEXTURES_DIR', 'LIBGL_ALWAYS_SOFTWARE', 'MESA_LOADER_DRIVER_OVERRIDE',
)
_MAX_RUNTIME_FILES = 30000
_MAX_RUNTIME_BYTES = 2 * 1024**3


def _signature(alias):
    """Track both a symlink and its target; missing entries are also identities."""
    try:
        link = alias.lstat()
    except FileNotFoundError:
        return None
    resolved = alias.resolve(strict=True)
    target = resolved.stat()
    return (str(resolved), link.st_dev, link.st_ino, link.st_mode,
            link.st_size, link.st_mtime_ns, link.st_ctime_ns,
            target.st_dev, target.st_ino, target.st_mode,
            target.st_size, target.st_mtime_ns, target.st_ctime_ns)


def _matches_signature(alias, signature):
    # Parent directories are also captured for trees. Avoid resolving thousands
    # of ancestor paths on every cache hit; inode/ctime identity and explicit
    # symlink/target stats still reject replacements and retargeting.
    try:
        link = alias.lstat()
    except FileNotFoundError:
        return signature is None
    if signature is None:
        return False
    own = (link.st_dev, link.st_ino, link.st_mode, link.st_size, link.st_mtime_ns, link.st_ctime_ns)
    if own != signature[1:7]:
        return False
    target = alias.stat() if stat.S_ISLNK(link.st_mode) else link
    return (target.st_dev, target.st_ino, target.st_mode, target.st_size,
            target.st_mtime_ns, target.st_ctime_ns) == signature[7:]


def _executables():
    ffmpeg = os.environ.get('FRAMERUDIMENTS_FFMPEG') or shutil.which('ffmpeg')
    ffprobe = os.environ.get('FRAMERUDIMENTS_FFPROBE')
    if not ffprobe and ffmpeg:
        sibling = Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
        ffprobe = str(sibling) if sibling.is_file() else shutil.which('ffprobe')
    return {
        'ffmpeg': ffmpeg,
        'ffprobe': ffprobe,
        'node': os.environ.get('FRAMERUDIMENTS_NODE') or shutil.which('node'),
        'browser': os.environ.get('FRAMERUDIMENTS_BROWSER'),
    }


class ExternalRuntimeGuard:
    """Pure filesystem admission/check guard; never starts a browser or GPU.

    Missing prerequisites are recorded so the worker can return an unavailable
    diagnostic. Adding one later changes this identity and retires cached hits.
    Directory signatures detect additions/removals; file signatures detect byte
    replacement without rehashing the complete locked runtime for every frame.
    Names and paths never appear in the public digest or error messages.
    """

    def __init__(self, document):
        self._environment = {name: os.environ.get(name) for name in _ENVIRONMENT}
        self._commands = _executables()
        self._entries = []
        self._fast_entries = []
        self._visited = set()
        self._bytes = 0
        manifest = []

        def capture(path, label, *, tree=False):
            if path is None:
                manifest.append((label, 'unset'))
                return
            alias = Path(path).absolute()
            signature = _signature(alias)
            self._entries.append((alias, signature))
            if not label.startswith(('npm/node_modules/', 'implementation/')):
                self._fast_entries.append((alias, signature))
            if len(self._entries) > _MAX_RUNTIME_FILES:
                raise StatefulSeekUnavailable('runtime_manifest_limit')
            if signature is None:
                manifest.append((label, 'missing'))
                return
            resolved = Path(signature[0])
            mode = resolved.stat().st_mode
            if stat.S_ISDIR(mode):
                manifest.append((label, 'directory'))
                if tree and resolved not in self._visited:
                    self._visited.add(resolved)
                    for child in sorted(alias.iterdir()):
                        capture(child, label + '/' + child.name, tree=True)
                return
            if not stat.S_ISREG(mode):
                raise StatefulSeekUnavailable('native_runtime_unavailable')
            self._bytes += resolved.stat().st_size
            if self._bytes > _MAX_RUNTIME_BYTES:
                raise StatefulSeekUnavailable('runtime_manifest_limit')
            digest = sha256()
            with alias.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
            if _signature(alias) != signature:
                raise StatefulSeekUnavailable('runtime_identity_changed')
            manifest.append((label, digest.hexdigest()))

        try:
            effects = document.get('effects', [])
            engines = {row.get('engine') for row in effects if isinstance(row, dict)}
            package = Path(__file__).parent
            assets = package / 'visualiser_assets'
            for source in sorted(package.rglob('*')):
                if (source.is_file() and '__pycache__' not in source.parts
                        and source.suffix in {'.py', '.js', '.mjs', '.glsl', '.fs', '.png', '.json', '.svg', '.milk'}):
                    capture(source, 'implementation/' + source.relative_to(package).as_posix())
            for name in ('visualisers.py', 'projectm_backend.py', 'preview_replay.py',
                         'preview_stateful_renderer.py'):
                capture(package / name, 'adapter/' + name)
            for name in ('ffmpeg', 'ffprobe'):
                capture(self._commands[name], name)
            for index, row in enumerate(effects):
                if not isinstance(row, dict):
                    continue
                choices = [row.get('preset', 'feedback')]
                choices.extend(e.get('preset') for e in row.get('preset_timeline', []) if isinstance(e, dict))
                for position, preset in enumerate(choices):
                    if isinstance(preset, str) and preset in {'feedback', 'kaleidoscope', 'julia'}:
                        extension = '.json' if row.get('engine') == 'butterchurn' else '.milk'
                        capture(assets / (preset + extension), f'preset/{index}/{position}')
            if 'butterchurn' in engines:
                for name in ('node', 'browser'):
                    capture(self._commands[name], name)
                browser = self._commands['browser']
                if browser and Path(browser).exists():
                    # Packaged Chrome uses a launcher script beside its actual
                    # executable. Pin both rather than only the shell script.
                    parent = Path(browser).resolve().parent
                    for name in ('chrome', 'chrome.exe', 'chromium', 'chromium-browser'):
                        candidate = parent / name
                        if candidate != Path(browser).resolve():
                            capture(candidate, 'browser-sibling/' + name)
                runtime = self._environment['FRAMERUDIMENTS_BUTTERCHURN_RUNTIME']
                if runtime:
                    for name in ('package.json', 'package-lock.json', 'node_modules'):
                        capture(Path(runtime) / name, 'npm/' + name, tree=name == 'node_modules')
                else:
                    manifest.append(('npm', 'unset'))
                for name in ('package-lock.json', 'butterchurn-page.js', 'butterchurn-worker.mjs'):
                    capture(assets / name, 'bridge/' + name)
            if 'projectm' in engines:
                library = self._environment['FRAMERUDIMENTS_PROJECTM_LIBRARY']
                capture(library, 'projectm/library')
                if library:
                    capture(Path(library).parent / 'framerudiments-projectm.json', 'projectm/receipt')
                for index, folder in enumerate((self._environment['FRAMERUDIMENTS_PROJECTM_DLL_DIRS'] or '').split(os.pathsep)):
                    if folder:
                        capture(folder, f'projectm/dependencies/{index}', tree=True)
            textures = self._environment['FRAMERUDIMENTS_TEXTURES_DIR']
            capture(textures, 'textures', tree=True)
            versions = {}
            for name in ('Pillow', 'numpy', 'glfw', 'PyOpenGL', 'dancerudiments'):
                try:
                    versions[name] = metadata.version(name)
                except metadata.PackageNotFoundError:
                    versions[name] = 'not_installed'
            self.digest = canonical_digest({'abi': STATEFUL_ABI, 'manifest': manifest,
                                            'environment': self._environment, 'commands': self._commands,
                                            'versions': versions})
            self._versions = versions
        except StatefulSeekUnavailable:
            raise
        except (OSError, ValueError, TypeError):
            raise StatefulSeekUnavailable('native_runtime_unavailable') from None

    def _check(self, entries):
        try:
            if self._environment != {name: os.environ.get(name) for name in _ENVIRONMENT}:
                raise ValueError('environment changed')
            if self._commands != _executables():
                raise ValueError('executable selection changed')
            for alias, signature in entries:
                if not _matches_signature(alias, signature):
                    raise ValueError('runtime changed')
        except (OSError, ValueError):
            raise StatefulSeekUnavailable('runtime_identity_changed') from None

    def __call__(self):
        self._check(self._entries)
        for name, expected in self._versions.items():
            try:
                actual = metadata.version(name)
            except metadata.PackageNotFoundError:
                actual = 'not_installed'
            if actual != expected:
                raise StatefulSeekUnavailable('runtime_identity_changed')

    def verify_frame(self):
        """Cheap running-frame guard; complete tree checks bracket each request.

        npm code is loaded at engine construction. The complete tree is checked
        before/after reset and before any verified result is published, so a
        mutation during replay can never be admitted as a cache result.
        """
        self._check(self._fast_entries)


def _pixels_digest(image):
    if not isinstance(image, Image.Image) or image.mode != 'RGBA':
        raise StatefulSeekUnavailable('invalid_replay_result')
    return sha256(image.tobytes()).hexdigest()


class _NativePipeline:
    """A fresh native frame-zero state; resources never cross owning threads."""

    def __init__(self, owner):
        from .compositor import Compositor
        from .encoding import binaries
        from .visualisers import EffectChain

        self.owner = owner
        self.compositor = self.effects = None
        try:
            owner._verify()
            view = render_project_view(owner.project, 'Q5', allow_stateful=True)
            self.compositor = Compositor(view)
            if not self.compositor.effect_specs:
                raise StatefulSeekUnavailable('profile_unsupported')
            ffmpeg, _ = binaries()
            directory = Path(owner.temporary.name) / 'prefix'
            directory.mkdir(exist_ok=True)
            self.effects = EffectChain(
                view, self.compositor.effect_specs, ffmpeg, directory,
                routing=self.compositor.visualiser_routing,
                hooks=self.compositor.visualiser_hooks, context_at=self.compositor.context,
            )
            view.verify_inputs()
            # Compositor and EffectChain register preset/modulation pins lazily.
            # Capture after both constructors, including the exact native PCM.
            references = [*view.inputs,
                          {'path': str(directory / 'effects-audio.f32'),
                           'sha256': self.effects.audio.evidence['sha256']}]
            self.guard = PinnedInputGuard(view.base, view.data, references)
            runtimes = [deepcopy(backend.evidence) for backend in self.effects.backends]
            self.identity = canonical_digest({
                # Owned sidecar paths differ between foreground/refinement
                # processes. Their logical contents and discovered input bytes
                # are authoritative; temporary placement is not render state.
                'abi': STATEFUL_ABI, 'snapshot_sha256': owner._snapshot_digest,
                'canvas': [view.width, view.height],
                'quality': view.output_quality.evidence,
                'clock': {name: getattr(view, name) for name in (
                    'fps', 'frame_count', 'sample_rate', 'start_sample', 'end_sample', 'repeats')},
                'input_hashes': sorted(reference['sha256'] for reference in references),
                'runtime_guard': owner.runtime_guard.digest,
                'runtime_evidence': runtimes, 'pcm': self.effects.audio.evidence,
                'effects': self.compositor.effect_specs,
            })
            owner._verify()
            self.guard()
        except BaseException:
            self.close()
            raise

    def render(self, frame):
        self.owner._verify(full_runtime=False)
        try:
            self.guard()
        except (ValueError, OSError):
            raise StatefulSeekUnavailable('input_identity_changed') from None
        image, context = self.compositor.frame(frame, apply_quality=False)
        image = self.effects.process(
            image, context,
            isolated_layers=self.compositor._route_parts
            if self.compositor.visualiser_routing is not None or self.compositor._isolate_stage_injections
            else None,
        )
        # The same one final AA pass used by production, after all effects.
        image = self.compositor.output_quality.apply(image)
        if (not isinstance(image, Image.Image) or image.mode not in ('RGB', 'RGBA')
                or image.size != (self.compositor.project.width, self.compositor.project.height)):
            raise StatefulSeekUnavailable('invalid_replay_result')
        self.owner._verify(full_runtime=False)
        try:
            self.guard()
        except (ValueError, OSError):
            raise StatefulSeekUnavailable('input_identity_changed') from None
        return image.convert('RGBA')

    def close(self):
        failures = []
        for name in ('effects', 'compositor'):
            resource = getattr(self, name)
            if resource is not None:
                try:
                    resource.close()
                    setattr(self, name, None)
                except Exception as exc:
                    failures.append(exc)
        if failures:
            raise StatefulSeekUnavailable('native_cleanup_failed')


class StatefulPreviewRenderer(NativePreviewRenderer):
    """One thread-owned, Q5-only verified native prefix replay session."""

    def __init__(self, project_file, draft, sidecars, *, validate_draft, temp_parent,
                 verifier=None, max_prefix_frames=1800, max_request_frames=3600,
                 max_pcm_bytes=MAX_PCM_BYTES):
        super().__init__(project_file, draft, sidecars, validate_draft=validate_draft,
                         temp_parent=temp_parent, verifier=verifier)
        for value, maximum in ((max_prefix_frames, 36000), (max_request_frames, 72000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('invalid bounded replay limit')
        self.max_prefix_frames, self.max_request_frames = max_prefix_frames, max_request_frames
        if type(max_pcm_bytes) is not int or not 1 <= max_pcm_bytes <= MAX_PCM_BYTES:
            raise ValueError('PCM budget must be a positive integer no larger than 256 MiB')
        self.max_pcm_bytes = max_pcm_bytes
        self.pcm_bytes = 0
        self.runtime_guard = self.replay = self._pending_pipeline = None
        self._snapshot_digest = canonical_digest({'draft': self.draft, 'sidecars': self.sidecars})
        self._project_guard = None
        self._unavailable_reason = None
        self._requested_frame = None

    @property
    def stateful_status(self):
        if self.replay is not None:
            result = self.replay.snapshot()
            result.update(max_pcm_bytes=self.max_pcm_bytes, pcm_bytes=self.pcm_bytes,
                          engine_identity_sha256=self.replay.identity)
            if self._unavailable_reason:
                result.update(phase='unavailable', last_reason=self._unavailable_reason,
                              requested_frame=self._requested_frame)
            return result
        return {'enabled': True, 'mode': 'verified_prefix_replay',
                'phase': 'unavailable' if self._unavailable_reason else 'idle',
                'requested_frame': self._requested_frame, 'verified_prefix_frames': 0, 'live_next_frame': 0,
                'current_request_frames': 0, 'replayed_frames': 0,
                'max_prefix_frames': self.max_prefix_frames,
                'max_request_frames': self.max_request_frames,
                'max_pcm_bytes': self.max_pcm_bytes, 'pcm_bytes': self.pcm_bytes,
                'engine_identity_sha256': None,
                'last_reason': self._unavailable_reason}

    def verified_receipt(self, frame):
        """Return a content-free proof for parent pixel/provenance admission."""
        self._thread()
        if (self.closed or self._unavailable_reason or self.replay is None
                or self.replay.phase != 'ready' or type(frame) is not int
                or not 0 <= frame < len(self.replay.receipts)):
            raise StatefulSeekUnavailable('unverified_result')
        receipt = self.replay.receipts[frame]
        return {'frame': receipt.frame, 'pixels_sha256': receipt.pixels_sha256,
                'prefix_sha256': receipt.prefix_sha256,
                'engine_identity_sha256': self.replay.identity}

    def _admit_pcm(self):
        # Native EffectAudio prepares the complete programme, even for a short
        # preview prefix. Bound that real allocation without truncating audio or
        # changing native excerpt/repeat/fade semantics.
        self.pcm_bytes = ((self.project.end_sample - self.project.start_sample)
                          * self.project.repeats * 8)
        if self.pcm_bytes > self.max_pcm_bytes:
            raise StatefulSeekUnavailable('pcm_budget_exceeded')
        pcm = Path(self.temporary.name) / 'prefix' / 'effects-audio.f32'
        if not pcm.is_file() and shutil.disk_usage(self.temporary.name).free < self.pcm_bytes + 16 * 1024**2:
            raise StatefulSeekUnavailable('pcm_disk_budget_exceeded')

    def _verify(self, *, full_runtime=True):
        try:
            if self._snapshot_digest != canonical_digest({'draft': self.draft, 'sidecars': self.sidecars}):
                raise ValueError('snapshot changed')
            if self.verifier is not None:
                self.verifier()
            if self.input_guard is not None:
                self.input_guard()
            if self._project_guard is not None:
                self._project_guard()
            if self.runtime_guard is not None:
                if full_runtime:
                    self.runtime_guard()
                else:
                    self.runtime_guard.verify_frame()
        except StatefulSeekUnavailable:
            raise
        except (ValueError, OSError):
            raise StatefulSeekUnavailable('input_identity_changed') from None

    def _pipeline(self):
        if self._pending_pipeline is not None:
            result, self._pending_pipeline = self._pending_pipeline, None
            return result
        start = perf_counter()
        try:
            return _NativePipeline(self)
        finally:
            self.last_setup_ms += (perf_counter() - start) * 1000

    def __call__(self, frame, profile_id='Q5', *, cancelled=lambda: False, progress=None):
        self._thread()
        self.last_setup_ms = 0
        self._requested_frame = frame
        self._unavailable_reason = None
        try:
            if self.closed:
                raise StatefulSeekUnavailable('renderer_closed')
            if profile_id != 'Q5':
                raise StatefulSeekUnavailable('profile_unsupported')
            if cancelled():
                raise ReplayCancelled('superseded replay')
            self._verify()
            if self.project is None:
                start = perf_counter()
                self._project_guard = PinnedInputGuard(self.path.parent, {}, [
                    {'path': str(self.path), 'sha256': sha256(self.path.read_bytes()).hexdigest()}])
                self._prepare()
                self._project_guard()
                self.runtime_guard = ExternalRuntimeGuard(self.draft)
                self.last_setup_ms += (perf_counter() - start) * 1000
            _integer(frame, 'absolute frame', 0, self.project.frame_count - 1)
            if frame >= self.max_prefix_frames:
                raise StatefulSeekUnavailable('prefix_limit_exceeded')
            self._admit_pcm()
            if self.replay is None:
                initial = self._pipeline()
                self._pending_pipeline = initial
                self.replay = VerifiedPrefixReplay(
                    self._pipeline, initial.identity, digest=_pixels_digest,
                    max_prefix_frames=self.max_prefix_frames, max_request_frames=self.max_request_frames,
                )
            image = self.replay.render(frame, cancelled=cancelled, progress=progress)
            self._verify()
            output = BytesIO()
            image.save(output, format='PNG', compress_level=1)
            return output.getvalue()
        except ReplayCancelled:
            self.release_compositor()
            if self.replay is not None:
                self.replay.phase, self.replay.last_reason = 'cancelled', None
            raise
        except StatefulSeekUnavailable as exc:
            self._unavailable_reason = exc.reason_code
            if self.replay is not None and exc.reason_code in {
                'runtime_identity_changed', 'input_identity_changed', 'native_cleanup_failed', 'replay_mismatch',
            }:
                self.replay.invalidate(exc.reason_code)
            self.release_compositor()
            raise
        except Exception:
            self._unavailable_reason = 'native_runtime_unavailable'
            self.release_compositor()
            raise StatefulSeekUnavailable('native_runtime_unavailable') from None

    def release_compositor(self):
        self._thread()
        if self.replay is not None:
            self.replay.release()
        if self._pending_pipeline is not None:
            self._pending_pipeline.close()
            self._pending_pipeline = None

    def close(self):
        self._thread()
        if self.replay is not None:
            self.replay.close()
        self.release_compositor()
        try:
            super().close()
        except Exception:
            raise StatefulSeekUnavailable('native_cleanup_failed') from None
