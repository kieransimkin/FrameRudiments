"""M8.4 opt-in GPU-resident *closed* DAG islands.

A fully supported source/copy/opacity/common-transition/composite-over graph
has exactly one upload per distinct source and one final readback. No CPU
fallback or shader substitution inside the island. Other DAGs use the v33 bridge.
GPU textures are never modified while still referenced by another branch.
"""
from collections import Counter

from .effect_graph_gpu_kernels import TRANSITION

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


def supported(graph, configs):
    """Conservative static predicate; never silently interpret unsupported nodes."""
    kinds = {'source', 'copy', 'opacity', 'transition', 'composite'}
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
    return True


def render_resident(backend, graph, sources, configs):
    """Run a supported DAG without intermediate host transfers.

    A memory budget covers every live texture and the temporary output (worst-case
    total source/intermediate count); a pool reuses released targets. Only final
    output is read to the host. On failure all handles release in reverse order.
    """
    from .effect_graph_gpu import GPUUnsupported
    if not supported(graph, configs):
        raise GPUUnsupported('graph contains nodes unsupported by resident execution')
    size = sources[graph.source_ids.__iter__().__next__()].size
    if any(sources[key].mode != 'RGBA' or sources[key].size != size for key in graph.source_ids):
        raise ValueError('resident graph requires equal-sized RGBA sources')
    # Conservative preflight before any new textures are allocated.
    max_live = len(graph.order) + 1
    backend._check_budget(size, max_live)
    inputs_left = Counter(i for key in graph.order for i in graph.nodes[key].inputs)
    texture_of = {}
    pool = []
    all_resources = []
    draw_count = 0
    peak_live = 0
    try:
        def target():
            if pool:
                return pool.pop()
            tex, fb = backend._target(size)
            all_resources.extend((fb, tex))
            return tex, fb

        def draw(shader_name, fragment, binding, uniforms):
            nonlocal draw_count
            shader, vao = backend._get_shader(shader_name, fragment)
            tex, fb = target()
            fb.use()
            backend.ctx.viewport = (0, 0, *size)
            backend.ctx.disable(backend._moderngl.BLEND)
            for unit, (name, source) in enumerate(binding.items()):
                source.use(location=unit)
                if name in shader:
                    shader[name].value = unit
            for name, value in uniforms.items():
                if name in shader:
                    shader[name].value = value
            vao.render(mode=backend._moderngl.TRIANGLES, vertices=3)
            draw_count += 1
            return tex, fb

        for key in graph.order:
            node = graph.nodes[key]
            cfg = configs.get(key, node.config)
            if node.kind == 'source':
                tex = backend._input(sources[key]); all_resources.append(tex)
                texture_of[key] = (tex, None)
            elif node.kind == 'copy':
                texture_of[key] = texture_of[node.inputs[0]]
            elif node.kind == 'opacity':
                texture_of[key] = texture_of[node.inputs[0]]
            elif node.kind == 'transition':
                a, b = (texture_of[n][0] for n in node.inputs)
                progress = cfg.get('progress', 0)
                if progress <= 0:
                    texture_of[key] = texture_of[node.inputs[0]]
                elif progress >= 1:
                    texture_of[key] = texture_of[node.inputs[1]]
                else:
                    style = cfg.get('transition_style', 'dissolve')
                    uniforms = dict(style={'dissolve':0,'wipe':1,'radial':2}[style],
                                    direction={'right':0,'left':1,'down':2,'up':3}[cfg.get('direction','right')],
                                    progress=progress, feather=cfg.get('feather',0),
                                    center=tuple(cfg.get('center', [.5,.5])))
                    texture_of[key] = draw('m84_transition', TRANSITION,
                                           {'outgoing_tex':a, 'incoming_tex':b}, uniforms)
            else:  # over composite, sequential in declared input order
                current = texture_of[node.inputs[0]]
                for i in node.inputs[1:]:
                    current = draw('m84_over', OVER,
                                   {'base_tex':current[0], 'upper_tex':texture_of[i][0]}, {})
                texture_of[key] = current

            opacity = cfg.get('opacity', 1)
            if opacity != 1:
                original = texture_of[key][0]
                texture_of[key] = draw('m84_opacity', OPACITY,
                                       {'source_tex':original}, {'strength':opacity})
            # Release only if no *graph key* references texture after this step.
            for i in node.inputs:
                inputs_left[i] -= 1
            if key != graph.output:
                inputs_left.setdefault(key, 0)
            remaining_ids = [graph.output] + [name for name, count in inputs_left.items() if count > 0]
            active_textures = {id(texture_of[name][0]) for name in remaining_ids if name in texture_of}
            for name, pair in list(texture_of.items()):
                if name != graph.output and inputs_left[name] == 0 and id(pair[0]) not in active_textures:
                    if pair[1] is not None and pair not in pool:
                        pool.append(pair)
                    del texture_of[name]
            peak_live = max(peak_live, len(active_textures))
        final_tex, final_fb = texture_of[graph.output]
        if final_fb is None:
            # Read from a framebuffer wrapping the existing source/aliased texture.
            final_fb = backend.ctx.framebuffer(color_attachments=[final_tex]); all_resources.append(final_fb)
        output = backend._readback_rgba(final_fb, size)
        backend.last_transport['gpu_resident_graph'] = True
        backend.last_transport['resident_draws'] = draw_count
        backend.last_transport['resident_peak_live_surfaces'] = peak_live
        backend.last_dispatch['gpu_nodes'] = draw_count
        return output
    finally:
        for resource in reversed(all_resources):
            resource.release()
