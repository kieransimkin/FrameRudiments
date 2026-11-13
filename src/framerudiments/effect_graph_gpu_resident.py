"""Bounded GPU-resident effect DAG execution.

Supported graphs keep shared, immutable RGBA8 source textures on the GPU and
read back only their final surface. A dry execution uses the *same* lifetime
scheduler as real rendering, so resource preflight accounts for multi-pass
filters, composite temporaries and aliasing before uploading any pixels.

Unsupported operations retain the established node bridge. Matte/key refinement beyond
the established M3.3 shader radius and edge-decontamination limits stays on CPU.
The GPU backend owns shaders/context; the scheduler only owns frame resources.
"""
from __future__ import annotations

from collections import Counter
from math import ceil, floor
from struct import pack

from PIL import ImageColor

from .effect_graph_gpu_kernels import (
    BLOOM_SELECT, CHROMATIC, FILTER_FINAL, GAUSSIAN, TRANSITION,
)
from .effect_graph_filter import MAX_FILTER_DIMENSION, MAX_FILTER_PIXELS
from .effect_graph_backend_contract import gpu_requirements
from .effect_graph_gpu import (
    BLUR as MASK_BLUR, DECONTAM_BLUR, DECONTAM_FINAL, DECONTAM_PACK,
    KEY_FINAL, KEY_INIT, MASK_INIT, MATTE_FINAL,
    MORPH_HORIZONTAL, MORPH_VERTICAL,
)
from .effect_graph_gpu_spectral import (
    BASE as SPECTRAL_BASE,
    COPY as SPECTRAL_COPY,
    BAND as SPECTRAL_BAND,
    FINAL as SPECTRAL_FINAL,
    SPACE_INDEX as SPECTRAL_SPACE_INDEX,
    _bind_band as bind_spectral_band,
)
from .effect_graph_gpu_spatial import SPATIAL_SHADER_NAME, SPATIAL_WARP, spatial_draw_inputs, spatial_uniforms
from .effect_graph_spatial import spatial_is_identity, spatial_map_is_transparent, validate_spatial_images
from .effect_graph_spectral import validate_spectral

# The M3.3 bridge uploads an L original-alpha image as RGBA and reads .r.
# Resident lineage points to a *shared RGBA graph surface*, so alpha is .a.
# Use dedicated shader names: never change the per-node bridge's ABI.
_ALPHA_SAMPLE = 'texelFetch(original_tex,pos,0).r'
if _ALPHA_SAMPLE not in MATTE_FINAL or _ALPHA_SAMPLE not in KEY_FINAL:
    raise RuntimeError('M3.3 alpha shader contract changed; resident kernel requires review')
MATTE_FINAL_RESIDENT = MATTE_FINAL.replace(_ALPHA_SAMPLE, 'texelFetch(original_tex,pos,0).a')
KEY_FINAL_RESIDENT = KEY_FINAL.replace(_ALPHA_SAMPLE, 'texelFetch(original_tex,pos,0).a')

MATTE_COMBINE_ORDER = ('multiply', 'min', 'max', 'add', 'subtract', 'difference', 'screen')

OPACITY = '''#version 330
uniform sampler2D source_tex;
uniform float strength;
out vec4 out_color;
void main() {
    vec4 c = texelFetch(source_tex, ivec2(gl_FragCoord.xy), 0);
    out_color = vec4(c.rgb, c.a * strength);
}
'''
OVER = '''#version 330
uniform sampler2D base_tex;
uniform sampler2D upper_tex;
out vec4 out_color;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec4 a = texelFetch(base_tex, p, 0);
    vec4 b = texelFetch(upper_tex, p, 0);
    float alpha = b.a + a.a * (1.0 - b.a);
    vec3 premul = b.rgb*b.a + a.rgb*a.a*(1.0-b.a);
    out_color = vec4(alpha > 1e-8 ? premul/alpha : vec3(0.0), alpha);
}
'''


def _identity(kind, config):
    if kind == 'chromatic_displacement':
        return not any(any(config.get(f'{c}_offset', (0, 0)))
                       for c in ('red', 'green', 'blue'))
    if kind == 'spectral_displacement':
        return config.get('mix', 1) == 0
    if kind == 'filter':
        style = config.get('filter_type', 'blur')
        return (config.get('filter_mix', 1) == 0
                or style == 'blur' and config.get('filter_radius', 1.5) == 0
                or style == 'bloom' and config.get('filter_strength', 1) == 0)
    return False


def node_supported(node, config):
    """Return whether one resolved node has an exact resident GLSL equivalent."""
    kinds = {'source', 'copy', 'opacity', 'transition', 'composite',
             'chromatic_displacement', 'filter', 'spectral_displacement',
             'matte', 'chroma_key', 'spatial_warp'}
    if node.kind not in kinds:
        return False
    if node.kind == 'transition' and (config.get('transition_style', 'dissolve') not in
                                      {'dissolve', 'wipe', 'radial'} or len(node.inputs) != 2):
        return False
    if node.kind == 'composite' and config.get('mode', 'over') != 'over':
        return False
    if node.kind == 'filter' and config.get('filter_type', 'blur') not in {'blur', 'bloom'}:
        return False
    if node.kind == 'spectral_displacement':
        validate_spectral(config)
    if node.kind in {'matte', 'chroma_key', 'spatial_warp'}:
        return gpu_requirements(node.kind, config, len(node.inputs)).supported
    return True


def resident_rejection(graph, configs):
    """Return the first resolved operation that prevents closed residency."""
    for key in graph.order:
        node = graph.nodes[key]
        cfg = configs.get(key, node.config)
        if not node_supported(node, cfg):
            return 'kernel_not_implemented'
        if node.kind == 'spatial_warp' and len(node.inputs) == 2 and not spatial_is_identity(cfg):
            control_map = graph.nodes[node.inputs[1]]
            map_config = configs.get(control_map.id, control_map.config)
            if control_map.kind == 'source' and map_config.get('opacity', 1) not in (0, 1):
                # An alpha quantization tie can produce a full-pixel change in
                # map offsets. Prepare these source pixels on the host first.
                return 'displacement_map_source_opacity'
            # The per-node bridge can establish whether a processed map is
            # entirely transparent without guessing from its GPU texture.
            if control_map.kind != 'source':
                return 'processed_displacement_map'
    return None


def supported(graph, configs):
    return resident_rejection(graph, configs) is None


def _resolve_transparent_maps(graph, sources, configs):
    """Resolve source-dependent identities before scheduling or allocating textures."""
    effective = dict(configs)
    for key in graph.order:
        node = graph.nodes[key]
        if node.kind != 'spatial_warp' or len(node.inputs) != 2:
            continue
        cfg = configs.get(key, node.config)
        if spatial_is_identity(cfg):
            continue
        map_key = node.inputs[1]
        map_node = graph.nodes[map_key]
        if map_node.kind != 'source':
            continue
        opacity = configs.get(map_key, map_node.config).get('opacity', 1)
        if opacity == 0 or spatial_map_is_transparent(sources[map_key]):
            effective[key] = {**cfg, 'warp_mix': 0}
    return effective


class _ResidentRun:
    """One graph evaluation; dry=True computes exactly the same RGBA8 allocations.

    Target textures are recycled *after* the last user; source texture handles
    are immutable and retained until finally. Therefore the payload budget is
    the number of all distinct allocations, including pooled and source textures,
    not merely the instantaneous set of active graph nodes.
    """

    def __init__(self, backend, graph, configs, size, *, dry=False):
        self.backend = backend
        self.graph = graph
        self.configs = configs
        self.size = size
        self.dry = dry
        self.pool = []
        self.all_resources = []
        self.texture_of = {}
        # A lineage handle aliases the original alpha-bearing RGBA texture.
        # It follows spatial warps, is inherited through other unary effects,
        # and resets after a source, transition or composite.
        self.alpha_of = {}
        self.inputs_left = Counter(i for key in graph.order for i in graph.nodes[key].inputs)
        self.live = set()
        self.allocated = 0
        self.peak_live = 0
        self.peak_pool = 0
        self.draw_count = 0

    def _track(self, texture):
        self.live.add(id(texture))
        self.peak_live = max(self.peak_live, len(self.live))

    def _source(self, image):
        if self.dry:
            texture = object()
        else:
            texture = self.backend._input(image)
            self.all_resources.append(texture)
        self.allocated += 1
        self._track(texture)
        return texture, None

    def _recycle(self, pair):
        """Drop one dead reference and reuse its target; never recycle sources."""
        texture, framebuffer = pair
        if id(texture) not in self.live:
            return
        self.live.remove(id(texture))
        if framebuffer is not None:
            self.pool.append(pair)
            self.peak_pool = max(self.peak_pool, len(self.pool))

    def _draw(self, name, shader_code, bindings, uniforms, *, prepare_shader=None):
        if self.pool:
            result = self.pool.pop()
        else:
            if self.dry:
                result = object(), object()
            else:
                tex, framebuffer = self.backend._target(self.size)
                self.all_resources.extend((framebuffer, tex))
                result = tex, framebuffer
            self.allocated += 1
        texture, framebuffer = result
        self._track(texture)
        if not self.dry:
            shader, vao = self.backend._get_shader(name, shader_code)
            # M8.6 spectral passes need fixed-size vec4 uniform arrays and
            # per-band metadata. Never mutate shader state during dry preflight.
            if prepare_shader is not None:
                prepare_shader(shader)
            framebuffer.use()
            self.backend.ctx.viewport = (0, 0, *self.size)
            self.backend.ctx.disable(self.backend._moderngl.BLEND)
            for unit, (uniform, bound) in enumerate(bindings.items()):
                bound.use(location=unit)
                if uniform in shader:
                    shader[uniform].value = unit
            for uniform, value in uniforms.items():
                if uniform in shader:
                    shader[uniform].value = value
            vao.render(mode=self.backend._moderngl.TRIANGLES, vertices=3)
        self.draw_count += 1
        return result

    def _filter(self, source, cfg):
        kind = cfg.get('filter_type', 'blur')
        radius = cfg.get('filter_radius', 1.5)
        border = int(cfg.get('filter_border', 'clamp') == 'transparent')
        if kind == 'bloom':
            bright = self._draw('m85_bright', BLOOM_SELECT, {'source_tex': source[0]},
                                {'threshold': cfg.get('filter_threshold', .7)})
            working = bright
        else:
            bright = None
            working = source
        horizontal = self._draw('m85_gaussian', GAUSSIAN, {'source_tex': working[0]},
                                {'direction': (1, 0), 'radius': radius,
                                 'source_straight': int(kind == 'blur'), 'border_mode': border})
        if bright is not None:
            self._recycle(bright)
        vertical = self._draw('m85_gaussian', GAUSSIAN, {'source_tex': horizontal[0]},
                              {'direction': (0, 1), 'radius': radius,
                               'source_straight': 0, 'border_mode': border})
        self._recycle(horizontal)
        output = self._draw('m85_filter_final', FILTER_FINAL,
                            {'source_tex': source[0], 'blurred_tex': vertical[0]},
                            {'bloom': int(kind == 'bloom'), 'amount': cfg.get('filter_mix', 1),
                             'strength': cfg.get('filter_strength', 1)})
        self._recycle(vertical)
        return output

    def _spectral(self, source, config):
        """Render all spectral bands on GPU, recycling two ping-pong targets.

        Reuse the same validated GLSL and per-band bindings as the M4.2 GPU
        bridge. Only *owned* temporaries are recycled: the original is a
        shared graph input whose texture may be referenced by other branches.
        Band passes always read the immutable original, never the previous
        band's transformed output as their colour-selection source.
        """
        validate_spectral(config)
        bands = config['bands']
        mode = config.get('spectral_mode', 'overlay')

        if mode == 'replace':
            def bind_base(shader):
                colours = [(*[v / 255 for v in ImageColor.getrgb(b['source_color'])], 1.0)
                           for b in bands]
                controls = [(b.get('tolerance', .08), b.get('softness', .08),
                             b.get('opacity', 1.0), SPECTRAL_SPACE_INDEX[b.get('space', 'oklab')])
                            for b in bands]
                padding = (0.0, 0.0, 0.0, 0.0)
                shader['source_colours'].write(pack(
                    '<32f', *(v for row in (colours + [padding] * (8 - len(bands))) for v in row)))
                shader['controls'].write(pack(
                    '<32f', *(v for row in (controls + [padding] * (8 - len(bands))) for v in row)))

            previous = self._draw(
                'm86_spectral_base', SPECTRAL_BASE, {'original_tex': source[0]},
                {'band_count': len(bands)}, prepare_shader=bind_base)
        else:
            previous = self._draw('m86_spectral_copy', SPECTRAL_COPY,
                                  {'original_tex': source[0]}, {})

        for band in bands:
            next_pair = self._draw(
                'm86_spectral_band', SPECTRAL_BAND,
                {'original_tex': source[0], 'previous_tex': previous[0]}, {},
                prepare_shader=lambda shader, band=band: bind_spectral_band(shader, band))
            self._recycle(previous)
            previous = next_pair

        result = self._draw(
            'm86_spectral_final', SPECTRAL_FINAL,
            {'original_tex': source[0], 'processed_tex': previous[0]},
            {'clip_to_source': int(config.get('alpha_mode', 'expanded') == 'source'),
             'amount': config.get('mix', 1.0)})
        self._recycle(previous)
        return result

    def _refine_mask(self, mask, *, expand=0, feather=0):
        """M3.3 bounded morphology and Gaussian on resident mask surfaces.

        The mask G channel carries the nearest key index for despill through
        morphology/blur. Targets are recycled only *after* their consuming draw.
        """
        if expand:
            radius = abs(expand)
            low, high = floor(radius), ceil(radius)
            horizontal = self._draw(
                'm87_mask_morph_h', MORPH_HORIZONTAL, {'source_tex': mask[0]},
                {'inner_radius': low, 'outer_radius': high,
                 'dilation': int(expand > 0)})
            self._recycle(mask)
            vertical = self._draw(
                'm87_mask_morph_v', MORPH_VERTICAL,
                {'source_tex': horizontal[0]},
                {'inner_radius': low, 'outer_radius': high,
                 'dilation': int(expand > 0), 'fraction': radius - low})
            self._recycle(horizontal)
            mask = vertical
        if feather:
            for direction in ((1, 0), (0, 1)):
                blurred = self._draw(
                    'm87_mask_blur', MASK_BLUR, {'source_tex': mask[0]},
                    {'direction': direction, 'sigma': feather})
                self._recycle(mask)
                mask = blurred
        return mask

    def _matte(self, inputs, cfg, original):
        """One foreground plus 1–8 alpha/luminance mask graph inputs."""
        mask_count = len(inputs) - 1

        def bind_masks(shader):
            # GLSL 3.30 sampler arrays need explicit fixed-size bindings.
            shader['masks'].value = tuple(range(mask_count)) + (0,) * (8 - mask_count)

        mask = self._draw(
            'm87_mask_init', MASK_INIT,
            {f'mask_slot_{i}': pair[0] for i, pair in enumerate(inputs[1:])},
            {'nmask': mask_count,
             'channel_mode': int(cfg.get('matte_channel', 'alpha') == 'luminance'),
             'combine_mode': MATTE_COMBINE_ORDER.index(cfg.get('mask_combine', 'multiply'))},
            prepare_shader=bind_masks)
        mask = self._refine_mask(
            mask, expand=cfg.get('expand', 0), feather=cfg.get('feather', 0))
        result = self._draw(
            'm87_matte_final', MATTE_FINAL_RESIDENT,
            {'source_tex': inputs[0][0], 'mask_tex': mask[0],
             'original_tex': original[0]},
            {'original_mode': int(cfg.get('alpha_source', 'current') == 'original'),
             'inverted': int(cfg.get('invert', False)),
             'mask_opacity': cfg.get('mask_opacity', 1)})
        self._recycle(mask)
        return result

    def _key(self, image, cfg, original):
        """Bounded RGB/OKLab multi-key GPU processing without host transfers."""
        keys = cfg.get('key_colors') or [cfg.get('key_color', '#00ff00')]
        if 'key_colors' in cfg and 'key_color' in cfg:
            keys = [cfg['key_color'], *cfg['key_colors']]
        colours = [(*[channel / 255 for channel in ImageColor.getrgb(key)], 1.0)
                   for key in keys]
        pad = (0.0, 0.0, 0.0, 1.0)
        packed = pack('<32f', *(v for colour in
                               (colours + [pad] * (8 - len(colours))) for v in colour))

        def bind_colours(shader):
            shader['keys'].write(packed)

        mask = self._draw(
            'm87_key_init', KEY_INIT, {'source_tex': image[0]},
            {'nkey': len(colours),
             'space_mode': int(cfg.get('key_space', 'rgb') == 'oklab'),
             'tolerance': cfg.get('tolerance', .1),
             'softness': cfg.get('softness', .1)},
            prepare_shader=bind_colours)
        mask = self._refine_mask(
            mask, expand=cfg.get('edge_expand', 0),
            feather=cfg.get('edge_feather', 0))
        result = self._draw(
            'm87_key_final', KEY_FINAL_RESIDENT,
            {'source_tex': image[0], 'mask_tex': mask[0],
             'original_tex': original[0]},
            {'original_mode': int(cfg.get('alpha_source', 'current') == 'original'),
             'despill': cfg.get('despill', 0), 'nkey': len(colours)},
            prepare_shader=bind_colours)
        if cfg.get('edge_decontaminate', 0):
            packed = self._draw(
                'm88_decontam_pack', DECONTAM_PACK,
                {'source_tex': image[0], 'mask_tex': mask[0]}, {})
            working = packed
            for direction in ((1, 0), (0, 1)):
                blurred = self._draw(
                    'm88_decontam_blur', DECONTAM_BLUR,
                    {'source_tex': working[0]},
                    {'direction': direction, 'sigma': cfg.get('edge_radius', 3)})
                self._recycle(working)
                working = blurred
            decontaminated = self._draw(
                'm88_decontam_final', DECONTAM_FINAL,
                {'keyed_tex': result[0], 'mask_tex': mask[0],
                 'blurred_tex': working[0]},
                {'strength': cfg['edge_decontaminate']})
            self._recycle(result)
            self._recycle(working)
            result = decontaminated
        self._recycle(mask)
        return result

    def _render_node(self, key):
        node = self.graph.nodes[key]
        cfg = self.configs.get(key, node.config)
        kind = node.kind
        owned = False
        warped_alpha = None
        if kind == 'source':
            pair = self._source(self.sources[key])
        elif kind in {'copy', 'opacity'}:
            pair = self.texture_of[node.inputs[0]]
        elif kind == 'transition':
            a, b = (self.texture_of[i] for i in node.inputs)
            progress = cfg.get('progress', 0)
            if progress <= 0:
                pair = a
            elif progress >= 1:
                pair = b
            else:
                style = cfg.get('transition_style', 'dissolve')
                pair = self._draw('m85_transition', TRANSITION,
                                  {'outgoing_tex': a[0], 'incoming_tex': b[0]},
                                  {'style': {'dissolve': 0, 'wipe': 1, 'radial': 2}[style],
                                   'direction': {'right': 0, 'left': 1, 'down': 2, 'up': 3}[
                                       cfg.get('direction', 'right')],
                                   'progress': progress, 'feather': cfg.get('feather', 0),
                                   'center': tuple(cfg.get('center', (.5, .5)))})
                owned = True
        elif kind == 'chromatic_displacement':
            pair = self.texture_of[node.inputs[0]]
            if not _identity(kind, cfg):
                uniforms = {f'{c}_offset': tuple(cfg.get(f'{c}_offset', (0, 0)))
                            for c in ('red', 'green', 'blue')}
                pair = self._draw('m85_chromatic', CHROMATIC,
                                  {'source_tex': pair[0]}, uniforms)
                owned = True
        elif kind == 'spectral_displacement':
            pair = self.texture_of[node.inputs[0]]
            if not _identity(kind, cfg):
                pair = self._spectral(pair, cfg)
                owned = True
        elif kind == 'spatial_warp':
            pair = self.texture_of[node.inputs[0]]
            original = self.alpha_of[node.inputs[0]]
            warped_alpha = original
            if not spatial_is_identity(cfg):
                uniforms = spatial_uniforms(cfg, self.size)
                control_map = self.texture_of[node.inputs[1]][0] if len(node.inputs) == 2 else None
                warped = self._draw(SPATIAL_SHADER_NAME, SPATIAL_WARP, *spatial_draw_inputs(
                    pair[0], control_map, uniforms, alpha_only=False,
                ))
                warped_alpha = warped if original is pair else self._draw(
                    SPATIAL_SHADER_NAME, SPATIAL_WARP, *spatial_draw_inputs(
                        original[0], control_map, uniforms, alpha_only=True,
                    ))
                pair = warped
                owned = True
        elif kind == 'matte':
            pair = self._matte(
                [self.texture_of[name] for name in node.inputs], cfg,
                self.alpha_of[node.inputs[0]])
            owned = True
        elif kind == 'chroma_key':
            pair = self._key(self.texture_of[node.inputs[0]], cfg,
                             self.alpha_of[node.inputs[0]])
            owned = True
        elif kind == 'filter':
            pair = self.texture_of[node.inputs[0]]
            if not _identity(kind, cfg):
                pair = self._filter(pair, cfg)
                owned = True
        else:  # sequential ordered Porter-Duff source-over
            pair = self.texture_of[node.inputs[0]]
            for name in node.inputs[1:]:
                new = self._draw('m85_over', OVER,
                                 {'base_tex': pair[0], 'upper_tex': self.texture_of[name][0]}, {})
                if owned:
                    self._recycle(pair)
                pair = new
                owned = True

        opacity = cfg.get('opacity', 1)
        if opacity != 1:
            new = self._draw('m85_opacity', OPACITY,
                             {'source_tex': pair[0]}, {'strength': opacity})
            if (owned or kind == 'source') and pair is not warped_alpha:
                self._recycle(pair)
            pair = new

        self.texture_of[key] = pair
        if kind in {'source', 'transition', 'composite'}:
            # CPU graph resets lineage to output alpha after these nodes.
            self.alpha_of[key] = pair
        elif warped_alpha is not None:
            self.alpha_of[key] = warped_alpha
        else:
            self.alpha_of[key] = self.alpha_of[node.inputs[0]]
        for name in node.inputs:
            self.inputs_left[name] -= 1
        # Image and *original-alpha* references are independently pinned.
        # In particular, a source's processed image may become unreachable
        # before a downstream 'alpha_source=original' mask has consumed it.
        retained = [name for name in self.texture_of
                    if name == self.graph.output or self.inputs_left[name] > 0]
        pinned = {id(pair[0]) for name in retained
                  for pair in (self.texture_of[name], self.alpha_of[name])}
        for name, tex in list(self.texture_of.items()):
            if name != self.graph.output and self.inputs_left[name] == 0:
                lineage = self.alpha_of.pop(name)
                # Recycle orphaned lineage targets as soon as the *last*
                # descendant releases them; the source uploads themselves
                # are retained for final cleanup, never pooled for drawing.
                for pair in (tex, lineage):
                    if id(pair[0]) not in pinned:
                        self._recycle(pair)
                del self.texture_of[name]

    def run(self, sources):
        self.sources = sources
        for key in self.graph.order:
            self._render_node(key)
        return self.texture_of[self.graph.output]

    def stats(self):
        return {'resident_draws': self.draw_count,
                'resident_peak_allocated_surfaces': self.allocated,
                'resident_peak_live_surfaces': self.peak_live,
                'resident_peak_pool_surfaces': self.peak_pool}

    def close(self):
        for resource in reversed(self.all_resources):
            resource.release()
        self.all_resources.clear()


def resident_plan(graph, configs=None):
    """Device-free exact RGBA8 *allocated texture count* for a supported graph.

    No source images, GPU context or third-party numerical packages are needed.
    Returned surface counts exclude driver allocations and host readback bytes.
    """
    from .effect_graph_gpu import GPUUnsupported

    configs = {} if configs is None else configs
    if not supported(graph, configs):
        raise GPUUnsupported('graph contains nodes unsupported by resident execution')
    run = _ResidentRun(None, graph, configs, None, dry=True)
    run.run({key: None for key in graph.source_ids})
    return run.stats()


def render_resident(backend, graph, sources, configs, *, island_stats=None):
    """Upload each graph source once, render on device, read one RGBA8 output.

    Preflight runs before any allocation. Resource ownership is invocation-local,
    and all textures/framebuffers are released even when a draw or readback fails.
    """
    from .effect_graph_gpu import GPUUnsupported

    if not supported(graph, configs):
        raise GPUUnsupported('graph contains nodes unsupported by resident execution')
    for key in graph.source_ids:
        if key not in sources or sources[key].mode != 'RGBA':
            raise ValueError(f'missing RGBA source: {key}')
    first_source = sources[next(iter(graph.source_ids))]
    size = first_source.size
    if any(sources[key].size != size for key in graph.source_ids):
        raise ValueError('resident graph requires equal-sized RGBA sources')
    if any(graph.nodes[key].kind == 'filter' for key in graph.order):
        if size[0] * size[1] > MAX_FILTER_PIXELS or max(size) > MAX_FILTER_DIMENSION:
            raise ValueError('GPU filter canvas exceeds pixel budget')
    if any(graph.nodes[key].kind == 'spatial_warp' for key in graph.order):
        validate_spatial_images(first_source)
    configs = _resolve_transparent_maps(graph, sources, configs)
    plan = resident_plan(graph, configs)
    backend._check_budget(size, plan['resident_peak_allocated_surfaces'])
    run = _ResidentRun(backend, graph, configs, size)
    try:
        texture, framebuffer = run.run(sources)
        if run.stats() != plan:
            raise RuntimeError('resident resource scheduling diverged from preflight')
        if framebuffer is None:
            framebuffer = backend.ctx.framebuffer(color_attachments=[texture])
            run.all_resources.append(framebuffer)
        image = backend._readback_rgba(framebuffer, size)
        if island_stats is not None:
            island_stats.update(plan)
        report = getattr(backend, 'last_transport', None)
        if report is not None:
            if island_stats is None:
                report.update(plan)
                report['gpu_resident_graph'] = True
                report['resident_estimated_texture_bytes'] = (
                    plan['resident_peak_allocated_surfaces'] * size[0] * size[1] * 4)
        if island_stats is None:
            backend.last_dispatch['gpu_nodes'] = plan['resident_draws']
        else:
            backend.last_dispatch['gpu_nodes'] += plan['resident_draws']
        return image
    finally:
        run.close()
