"""M8.3 bounded GLSL 3.30 kernels on the existing per-node RGBA8 bridge.

Inputs are validated graph-controlled values, never shader source. Source upload
and output readback still occur for every executed node; M8.4 will pool graph
surfaces. All resources created by one call are released on success or failure.
"""
from __future__ import annotations

from PIL import Image

TRANSITION = """#version 330
uniform sampler2D outgoing_tex;
uniform sampler2D incoming_tex;
uniform int style;
uniform int direction;
uniform float progress;
uniform float feather;
uniform vec2 center;
out vec4 out_color;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec2 sz = vec2(textureSize(outgoing_tex, 0));
    float weight = progress;
    if (style != 0) {
        float coordinate;
        if (style == 1) {
            if (direction == 0) coordinate = (float(p.x) + 0.5) / sz.x;
            else if (direction == 1) coordinate = 1.0 - (float(p.x) + 0.5) / sz.x;
            else if (direction == 2) coordinate = (float(p.y) + 0.5) / sz.y;
            else coordinate = 1.0 - (float(p.y) + 0.5) / sz.y;
        } else {
            vec2 uv = (vec2(p) + 0.5) / sz;
            float radius = max(max(length(vec2(0.0,0.0)-center), length(vec2(0.0,1.0)-center)),
                               max(length(vec2(1.0,0.0)-center), length(vec2(1.0,1.0)-center)));
            coordinate = length(uv-center) / max(radius, 1e-8);
        }
        if (feather > 0.0) {
            float t = clamp((progress-coordinate)/feather+0.5, 0.0, 1.0);
            weight = t*t*(3.0-2.0*t);
        } else weight = coordinate <= progress ? 1.0 : 0.0;
    }
    vec4 a = texelFetch(outgoing_tex, p, 0);
    vec4 b = texelFetch(incoming_tex, p, 0);
    float coverage = mix(a.a, b.a, weight);
    vec3 premul = a.rgb*a.a*(1.0-weight) + b.rgb*b.a*weight;
    out_color = vec4(coverage > 1e-8 ? premul/coverage : vec3(0.0), coverage);
}
"""

CHROMATIC = """#version 330
uniform sampler2D source_tex;
uniform vec2 red_offset;
uniform vec2 green_offset;
uniform vec2 blue_offset;
out vec4 out_color;
float sample_channel(vec2 sample_at, int channel) {
    ivec2 base = ivec2(floor(sample_at));
    vec2 f = sample_at - vec2(base);
    ivec2 sz = textureSize(source_tex, 0);
    float value = 0.0;
    for (int j = 0; j < 2; ++j) {
        for (int i = 0; i < 2; ++i) {
            ivec2 q = base + ivec2(i,j);
            if (any(lessThan(q, ivec2(0))) || any(greaterThanEqual(q, sz))) continue;
            float w = (i == 0 ? 1.0-f.x : f.x)*(j == 0 ? 1.0-f.y : f.y);
            value += texelFetch(source_tex,q,0)[channel]*w;
        }
    }
    return value;
}
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec2 xy = vec2(p);
    out_color = vec4(sample_channel(xy-red_offset,0), sample_channel(xy-green_offset,1),
                     sample_channel(xy-blue_offset,2), texelFetch(source_tex,p,0).a);
}
"""

BLOOM_SELECT = """#version 330
uniform sampler2D source_tex;
uniform float threshold;
out vec4 out_color;
void main() {
    vec4 src = texelFetch(source_tex, ivec2(gl_FragCoord.xy), 0);
    float brightness = max(src.r,max(src.g,src.b));
    float weight = clamp((brightness-threshold)/(1.0-threshold),0.0,1.0);
    out_color = vec4(src.rgb*src.a*weight, src.a*weight);
}
"""

GAUSSIAN = """#version 330
uniform sampler2D source_tex;
uniform ivec2 direction;
uniform float radius;
uniform int source_straight;
uniform int border_mode;
out vec4 out_color;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    ivec2 size = textureSize(source_tex, 0);
    vec4 sum_colour = vec4(0.0);
    float sum_weight = 0.0;
    int r = int(ceil(3.0*radius));
    for (int j = -12; j <= 12; ++j) {
        if (abs(j) > r) continue;
        float weight = radius == 0.0 ? float(j == 0) :
                       exp(-0.5 * pow(float(j)/radius,2.0));
        ivec2 q = p + direction*j;
        bool valid = all(greaterThanEqual(q,ivec2(0))) && all(lessThan(q,size));
        if (valid || border_mode == 0) {
            vec4 v = texelFetch(source_tex,clamp(q,ivec2(0),size-1),0);
            if (source_straight == 1) v.rgb *= v.a;
            sum_colour += v*weight;
        }
        sum_weight += weight;
    }
    out_color = clamp(sum_colour / max(sum_weight, 1e-8), 0.0, 1.0);
}
"""

FILTER_FINAL = """#version 330
uniform sampler2D source_tex;
uniform sampler2D blurred_tex;
uniform int bloom;
uniform float amount;
uniform float strength;
out vec4 out_color;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec4 src = texelFetch(source_tex, p, 0);
    vec4 base = vec4(src.rgb*src.a,src.a);
    vec4 effect = texelFetch(blurred_tex,p,0);
    if (bloom == 1) {
        vec4 glow = effect*strength;
        float alpha = clamp(base.a + glow.a*(1.0-base.a),0.0,1.0);
        effect = vec4(min(vec3(alpha),base.rgb+glow.rgb),alpha);
    }
    vec4 mixed = mix(base,effect,amount);
    out_color = vec4(mixed.a > 1e-8 ? mixed.rgb/mixed.a : vec3(0.0),mixed.a);
}
"""


def _execute(backend, inputs, steps, size, *, budget):
    """Perform bounded passes, recording transfer at the real bridge boundary."""
    backend._check_budget(size, budget)
    allocations = []
    try:
        textures = []
        for image in inputs:
            tex = backend._input(image)
            allocations.append(tex)
            textures.append(tex)
        result_fb = None
        for name, shader, sources, uniforms in steps(textures):
            bound = {key: textures[index] for key, index in sources.items()}
            tex, result_fb = backend._draw(name, shader, bound, size, uniforms, allocations)
            textures.append(tex)
        return backend._readback_rgba(result_fb,size)
    finally:
        for item in reversed(allocations):
            item.release()


def render_transition(backend, outgoing: Image.Image, incoming: Image.Image, config):
    """Dissolve, wipe, radial. Matte/spectral/geometric remain unsupported."""
    from .effect_graph_gpu import GPUUnsupported

    style = config.get('transition_style','dissolve')
    if style not in {'dissolve','wipe','radial'}:
        raise GPUUnsupported('transition shader is not implemented for '+str(style))
    if outgoing.mode != 'RGBA' or incoming.mode != 'RGBA' or outgoing.size != incoming.size:
        raise ValueError('GPU transition requires equally sized RGBA images')
    progress = config.get('progress',0)
    if progress <= 0:
        return outgoing.copy()
    if progress >= 1:
        return incoming.copy()
    if style == 'dissolve':
        feather, center, direction = 0, [.5,.5], 'right'
    else:
        feather = config.get('feather',0)
        center = config.get('center',[.5,.5])
        direction = config.get('direction','right')
    directions = {'right':0,'left':1,'down':2,'up':3}
    uniforms = dict(style={'dissolve':0,'wipe':1,'radial':2}[style], direction=directions[direction],
                    progress=progress,feather=feather,center=tuple(center))
    return _execute(backend,[outgoing,incoming],
                    lambda _: [('m83_transition',TRANSITION,{'outgoing_tex':0,'incoming_tex':1},uniforms)],
                    outgoing.size,budget=3)


def render_chromatic(backend, image: Image.Image, config):
    """Legacy RGB-channel semantics preserved (straight RGB under alpha)."""
    if image.mode != 'RGBA':
        raise ValueError('GPU chromatic displacement needs RGBA')
    if all(not any(config.get(f'{channel}_offset',[0,0])) for channel in ('red','green','blue')):
        return image.copy()
    uniforms = {f'{channel}_offset':tuple(config.get(f'{channel}_offset',[0,0]))
                for channel in ('red','green','blue')}
    return _execute(backend,[image],
                    lambda _: [('m83_chromatic',CHROMATIC,{'source_tex':0},uniforms)],image.size,budget=2)


def render_filter(backend,image: Image.Image,config):
    from .effect_graph_filter import validate_filter, MAX_FILTER_PIXELS, MAX_FILTER_DIMENSION

    validate_filter(config,1)
    if image.mode != 'RGBA' or image.width*image.height > MAX_FILTER_PIXELS or max(image.size) > MAX_FILTER_DIMENSION:
        raise ValueError('GPU filter requires RGBA canvas within pixel limits')
    kind = config.get('filter_type','blur')
    radius = config.get('filter_radius',1.5)
    mix = config.get('filter_mix',1)
    strength = config.get('filter_strength',1)
    if mix == 0 or (kind == 'blur' and radius == 0) or (kind == 'bloom' and strength == 0):
        return image.copy()
    border = int(config.get('filter_border','clamp') == 'transparent')

    def passes(textures):
        if kind == 'bloom':
            # Input index 0, brightpass output index 1.
            yield ('m83_bright', BLOOM_SELECT, {'source_tex':0},
                   dict(threshold=config.get('filter_threshold',.7)))
            first = 1
        else:
            first = 0
        # The first blur pass converts straight source to premultiplied.
        # A brightpass is already premultiplied.
        yield ('m83_gaussian',GAUSSIAN,{'source_tex':first},
               dict(direction=(1,0),radius=radius,source_straight=int(kind == 'blur'),border_mode=border))
        horizontal = first+1
        yield ('m83_gaussian',GAUSSIAN,{'source_tex':horizontal},
               dict(direction=(0,1),radius=radius,source_straight=0,border_mode=border))
        vertical = horizontal+1
        yield ('m83_filter_final',FILTER_FINAL,{'source_tex':0,'blurred_tex':vertical},
               dict(bloom=int(kind == 'bloom'),amount=mix,strength=strength))
    return _execute(backend,[image],passes,image.size,budget=5 if kind=='bloom' else 4)
