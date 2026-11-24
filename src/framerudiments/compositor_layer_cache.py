"""Native layer cache adapter; compositing and effect order remain unchanged."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from os import PathLike

from PIL import Image

from .layer_frame_cache import LayerFrameCache


def _pins(value):
    """Content identity survives working-cache copies and temporary sidecars."""
    if isinstance(value, dict):
        result = {k: _pins(v) for k, v in value.items()}
        for path, digest in (('path', 'sha256'), ('shader_path', 'shader_sha256')):
            if digest in result and path in result:
                result.pop(path)
        return result
    if isinstance(value, (list, tuple)):
        return [_pins(v) for v in value]
    return value


class LayerSurfaces:
    def __init__(self, compositor, cache, *, native_renderers=True):
        self.owner, self.project = compositor, compositor.project
        self.owns_cache = cache is None or isinstance(cache, (str, PathLike))
        self.cache = (LayerFrameCache(cache) if isinstance(cache, (str, PathLike)) else
                      LayerFrameCache() if cache is None else cache)
        if not isinstance(self.cache, LayerFrameCache):
            raise TypeError('layer_cache must be a LayerFrameCache, False or None')
        self.enabled = compositor.scene is None
        self.native_renderers = native_renderers
        self.guard = None
        self.dependencies = {}
        p, data = self.project, self.project.data
        geometry = dict(width=p.width, height=p.height,
                        vector_scale=p.output_quality.vector_scale)
        timing = dict(fps=p.fps, frame_count=p.frame_count,
                      audio={k: v for k, v in data['audio'].items() if k != 'source'},
                      clock=data.get('clock'))
        tracks = dict(lyrics=p.lyrics, words=p.words, cues=p.cues)
        self.dependencies['background'] = _pins(dict(geometry=geometry, config=data.get('background', {})))
        self.static = {'background'}
        self.reporters = {}
        self._background_seen = False
        for layer in [*compositor.layers, *compositor.lyric_art.values()]:
            key = ('lyric-art:'+layer.id.rsplit('-', 1)[-1]
                   if layer in compositor.lyric_art.values() else 'layer:'+layer.id)
            cfg = layer.cfg if hasattr(layer, 'cfg') else next(
                row for row in data['layers'] if row['id'] == layer.id)
            dep = dict(geometry=geometry, config=cfg)
            kind = cfg.get('type', 'sprite')
            if kind in ('sprite', 'bars'):
                dynamic = any(k in cfg for k in ('motion', 'atlas', 'start_seconds', 'end_seconds', 'cue_labels'))
                if dynamic:
                    dep['timing'] = timing
                    if 'cue_labels' in cfg:
                        dep['cues'] = p.cues
                else:
                    self.static.add(key)
            else:
                dep.update(timing=timing, tracks=tracks,
                           audio=data['audio']['source'], font=data.get('font'))
                def names(value):
                    if isinstance(value, str):
                        return {value}
                    if isinstance(value, dict):
                        return set().union(*(names(v) for v in value.values()))
                    if isinstance(value, (list, tuple)):
                        return set().union(*(names(v) for v in value))
                    return set()
                referenced = names(cfg)
                dep['stems'] = {name: spec for name, spec in data.get('stem_sources', {}).items()
                                if name in referenced}
            motion = getattr(layer, 'motion', None)
            if motion is not None:
                dep['motion_files'] = getattr(motion, 'files', ())
            if hasattr(layer, 'evidence'):
                dep['native'] = layer.evidence
            self.dependencies[key] = _pins(dep)
            self.reporters[key] = layer
        for index, item in enumerate(data.get('identity', [])):
            key = f'identity:{index}'
            self.dependencies[key] = _pins(dict(geometry=geometry, config=item, font=data.get('font')))
            self.static.add(key)
        for key, field in (('caption', 'captions'), ('word-caption', 'word_captions'),
                           ('word-indicator', 'word_indicator')):
            self.dependencies[key] = _pins(dict(geometry=geometry, config=data.get(field),
                font=data.get('font'), tracks=tracks, timing=timing,
                caption=data.get('captions') if key == 'word-indicator' else None))
        if compositor.word_indicator is not None:
            self.reporters['word-indicator'] = compositor.word_indicator
        if self.cache.root is not None:
            from .preview_quality import PinnedInputGuard
            # Raw native drawing consumes saved motion/assets/timing, not
            # soundtrack PCM. The ordinary renderer owns full audio pin checks;
            # avoid rehashing a long WAV for every cached image sample.
            audio_path = Path(p.audio_ref['path']).resolve()
            references = [ref for ref in p.inputs if
                          Path(ref['path']).resolve() != audio_path]
            self.guard = PinnedInputGuard(p.base, {}, references)

    def begin(self):
        outer = self.cache._begin()
        try:
            if outer and self.guard is not None:
                self.guard()
        except BaseException:
            self.cache._finish(False)
            raise
        return outer

    def finish(self, success, outer):
        try:
            if success and outer and self.guard is not None:
                self.guard()
        except BaseException:
            self.cache._finish(False)
            raise
        self.cache._finish(success)

    def _surface(self, key, ctx, render, backdrop):
        reporter = self.reporters.get(key)
        if key == 'background' and self._background_seen:
            # The immutable background is already its own resident layer bitmap.
            with self.cache._lock:
                self.cache._count(key, 'hits')
            return self.owner.background.copy(), dict(bounds=None, report=None,
                crop=(0, 0, *self.owner.background.size), size=self.owner.background.size)
        cacheable = self.enabled and key in self.dependencies
        if not self.native_renderers and key.startswith('layer:'):
            cacheable = False
        material = getattr(reporter, 'material', None)
        if getattr(material, 'method', None) == 'fluid_2d':
            cacheable = False
        mode = 'canvas' if backdrop is not None else 'isolated'
        stamp = 'static' if key in self.static else asdict(ctx)
        if key.startswith('layer:') and reporter is not None:
            cfg = getattr(reporter, 'cfg', {})
            if cfg.get('type', 'sprite') in ('sprite', 'bars'):
                stamp = ('static' if key in self.static else
                         dict(frame=ctx.frame, source_seconds=ctx.source_seconds, beat=ctx.beat,
                              cue=ctx.cue if 'cue_labels' in cfg else None))
        identity = self.cache.key(dict(layer=self.dependencies.get(key), sample=stamp, mode=mode,
            backdrop=sha256(backdrop.tobytes()).hexdigest() if backdrop is not None else None))
        if cacheable:
            hit = self.cache.get(key, identity)
            if hit is not None:
                tile, metadata = hit
                if reporter is not None and hasattr(reporter, 'frame_report'):
                    reporter.frame_report = deepcopy(metadata['report'])
                if key == 'background':
                    self._background_seen = True
                return tile, metadata
        image = backdrop.copy() if backdrop is not None else Image.new('RGBA', self.owner.background.size)
        bounds = render(image)
        # Include hidden RGB under zero alpha: later effects may reveal it.
        crop = image.getbbox(alpha_only=False)
        tile = (image if crop == (0, 0, *image.size) else image.crop(crop)) if crop else Image.new('RGBA', (1, 1))
        metadata = dict(bounds=bounds, report=deepcopy(getattr(reporter, 'frame_report', None)),
                        crop=crop, size=image.size)
        if cacheable:
            self.cache.put(key, identity, tile, metadata)
        if key == 'background':
            self._background_seen = True
        return tile, metadata

    @staticmethod
    def _canvas(tile, metadata):
        if metadata['crop'] == (0, 0, *metadata['size']):
            return tile  # Already a detached image, safe for downstream effects.
        image = Image.new('RGBA', metadata['size'])
        if metadata['crop'] is not None:
            image.paste(tile, metadata['crop'][:2])
        return image

    def draw(self, key, ctx, render, *, backdrop=None):
        tile, metadata = self._surface(key, ctx, render, backdrop)
        return self._canvas(tile, metadata), metadata['bounds']

    def direct(self, image, key, ctx, render, *, canvas_dependent=False):
        tile, metadata = self._surface(key, ctx, render, image if canvas_dependent else None)
        if canvas_dependent:
            image.paste(self._canvas(tile, metadata))
        elif metadata['crop'] is not None:
            # Match the original single alpha-over, only over the retained tile.
            image.alpha_composite(tile, metadata['crop'][:2])
        return metadata['bounds']

    def close(self):
        if self.owns_cache:
            self.cache.close()