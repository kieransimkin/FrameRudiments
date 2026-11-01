"""Bounded GPU-resident effect DAG execution (M8.4–M8.5).

Supported graphs keep shared, immutable RGBA8 source textures on the GPU and
read back only their final surface. A dry execution uses the *same* lifetime
scheduler as real rendering, so resource preflight accounts for multi-pass
filters, composite temporaries and aliasing before uploading any pixels.

This is deliberately an all-or-nothing resident island: unsupported mattes,
keys, spectral, spatial or custom nodes retain the established node bridge.
The GPU backend owns shaders/context; the scheduler only owns frame resources.
"""
from __future__ import annotations

from collections import Counter

from .effect_graph_gpu_kernels import (
    BLOOM_SELECT, CHROMATIC, FILTER_FINAL, GAUSSIAN, TRANSITION,
)
from .effect_graph_filter import MAX_FILTER_DIMENSION, MAX_FILTER_PIXELS

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
    if kind == 'filter':
        style = config.get('filter_type', 'blur')
        return (config.get('filter_mix', 1) == 0
                or style == 'blur' and config.get('filter_radius', 1.5) == 0
                or style == 'bloom' and config.get('filter_strength', 1) == 0)
    return False


def supported(graph, configs):
    """Only closed DAGs whose every operation has a resident GLSL equivalent."""
    kinds = {'source', 'copy', 'opacity', 'transition', 'composite',
             'chromatic_displacement', 'filter'}
    for key in graph.order:
        node = graph.nodes[key]
        cfg = configs.get(key, node.config)
        if node.kind not in kinds:
            return False
        if node.kind == 'transition' and (cfg.get('transition_style', 'dissolve') not in
                                          {'dissolve', 'wipe', 'radial'} or len(node.inputs) != 2):
            return False
        if node.kind == 'composite' and cfg.get('mode', 'over') != 'over':
            return False
        if node.kind == 'filter' and cfg.get('filter_type', 'blur') not in {'blur', 'bloom'}:
            return False
    return True


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

    def _draw(self, name, shader_code, bindings, uniforms):
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

    def _render_node(self, key):
        node = self.graph.nodes[key]
        cfg = self.configs.get(key, node.config)
        kind = node.kind
        owned = False
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
            if owned or kind == 'source':
                self._recycle(pair)
            pair = new

        self.texture_of[key] = pair
        for name in node.inputs:
            self.inputs_left[name] -= 1
        # A texture can have many graph aliases. A target becomes recyclable only
        # after the LAST alias or consuming edge, never merely the first copy.
        pinned = {id(tex[0]) for name, tex in self.texture_of.items()
                  if name == self.graph.output or self.inputs_left[name] > 0}
        for name, tex in list(self.texture_of.items()):
            if name != self.graph.output and self.inputs_left[name] == 0:
                if id(tex[0]) not in pinned:
                    self._recycle(tex)
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


def render_resident(backend, graph, sources, configs):
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
    size = sources[next(iter(graph.source_ids))].size
    if any(sources[key].size != size for key in graph.source_ids):
        raise ValueError('resident graph requires equal-sized RGBA sources')
    if any(graph.nodes[key].kind == 'filter' for key in graph.order):
        if size[0] * size[1] > MAX_FILTER_PIXELS or max(size) > MAX_FILTER_DIMENSION:
            raise ValueError('GPU filter canvas exceeds pixel budget')
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
        report = getattr(backend, 'last_transport', None)
        if report is not None:
            report.update(plan)
            report['gpu_resident_graph'] = True
            report['resident_estimated_texture_bytes'] = (
                plan['resident_peak_allocated_surfaces'] * size[0] * size[1] * 4)
        backend.last_dispatch['gpu_nodes'] = plan['resident_draws']
        return image
    finally:
        run.close()
