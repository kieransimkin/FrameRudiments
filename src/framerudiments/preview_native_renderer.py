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
        from .compositor import Compositor

        self._thread()
        if self.closed:
            raise RuntimeError('preview renderer is closed')
        self.last_setup_ms = 0
        if self.project is None:
            start = perf_counter()
            self._prepare()
            self.last_setup_ms += (perf_counter()-start)*1000
        _integer(frame, 'absolute frame', 0, self.project.frame_count - 1)
        (self.verifier or self.project.verify_inputs)()
        if self.input_guard:
            self.input_guard()
        if self.profile != profile_id:
            start = perf_counter()
            view = render_project_view(self.project, profile_id)
            if self.compositor is not None:
                self.compositor.close()
                self.compositor = self.profile = None
            self.compositor = Compositor(view)
            self.profile = profile_id
            self.last_setup_ms += (perf_counter()-start)*1000
        try:
            image, _ = self.compositor.frame(frame, apply_quality=True)
        except BaseException:
            try:
                self.compositor.close()
            finally:
                self.compositor = self.profile = None
            raise
        view = self.compositor.project
        if not isinstance(image, Image.Image) or image.mode not in ('RGB', 'RGBA') or image.size != (view.width, view.height):
            raise RuntimeError('native preview must produce exact RGBA8 profile dimensions')
        (self.verifier or self.project.verify_inputs)()
        if self.input_guard:
            self.input_guard()
        # The native final-frame API applies its own deterministic black matte
        # and can return RGB. Add opaque alpha only for the cache wire format;
        # do not resize, recompose or apply another quality pass here.
        if image.mode == 'RGB':
            image = image.convert('RGBA')
        buffer = BytesIO()
        image.save(buffer, format='PNG', compress_level=1)
        return buffer.getvalue()

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
            if self.temporary is not None:
                self.temporary.cleanup()
                self.temporary = None
