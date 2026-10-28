"""Optional ModernGL CPU<->GPU bridge for M3.3 matte and colour-key nodes.

The existing graph remains the authority for validation, controls and alpha
lineage. This backend accelerates *individual* RGBA nodes. It deliberately
exposes upload/readback costs; GPU-resident DAG execution is a later milestone.

Only instantiate in a graphics-enabled environment. There is no ModernGL
import, context creation, or shader compilation on the CPU-only code path.
Unsupported expensive configurations are rejected in strict GPU mode, or
executed by the exact CPU reference in the graph's opt-in ``auto`` mode.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from PIL import Image, ImageColor

# The CPU renderer allows much larger radii, but the fragment implementation
# intentionally caps work per pixel. No silent clipping of user controls.
MAX_GPU_MORPH_RADIUS = 16
MAX_GPU_GAUSSIAN_RADIUS = 12
MAX_GPU_KEY_COLOURS = 8
MAX_GPU_INPUT_MATTES = 8


class GPUUnsupported(ValueError):
    """Valid CPU effect that exceeds this GPU backend's capabilities."""


@dataclass(frozen=True)
class GPUCapabilities:
    context_version: int
    backend: str
    renderer: str
    max_morph_radius: int = MAX_GPU_MORPH_RADIUS
    max_gaussian_radius: int = MAX_GPU_GAUSSIAN_RADIUS
    cpu_to_gpu_bridge: bool = True


VERTEX = """#version 330
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

# Coordinates deliberately use gl_FragCoord/texelFetch, not filtered UVs.
# OpenGL upload and framebuffer readback both use bottom-row-first ordering;
# using their unchanged byte orders yields the same Pillow row sequence.
MASK_INIT = """#version 330
uniform sampler2D masks[8];
uniform int nmask;
uniform int channel_mode;
uniform int combine_mode;
out vec4 out_color;
float mask_value(int i, ivec2 p) {
    // GLSL 3.30 requires sampler array indexing by constant expressions.
    vec4 v;
    if      (i == 0) v = texelFetch(masks[0], p, 0);
    else if (i == 1) v = texelFetch(masks[1], p, 0);
    else if (i == 2) v = texelFetch(masks[2], p, 0);
    else if (i == 3) v = texelFetch(masks[3], p, 0);
    else if (i == 4) v = texelFetch(masks[4], p, 0);
    else if (i == 5) v = texelFetch(masks[5], p, 0);
    else if (i == 6) v = texelFetch(masks[6], p, 0);
    else v = texelFetch(masks[7], p, 0);
    if (channel_mode == 0) return v.a;
    float l = dot(v.rgb, vec3(0.299, 0.587, 0.114));
    return l * v.a;
}
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    float a = mask_value(0, p);
    for (int i = 1; i < nmask; ++i) {
        float b = mask_value(i, p);
        if (combine_mode == 0) a *= b;
        else if (combine_mode == 1) a = min(a, b);
        else if (combine_mode == 2) a = max(a, b);
        else if (combine_mode == 3) a = min(1.0, a + b);
        else if (combine_mode == 4) a = max(0.0, a - b);
        else if (combine_mode == 5) a = abs(a - b);
        else a = 1.0 - (1.0 - a) * (1.0 - b);
    }
    out_color = vec4(a, 0.0, 0.0, 1.0);
}
"""

KEY_INIT = """#version 330
uniform sampler2D source_tex;
uniform vec4 keys[8];
uniform int nkey;
uniform int space_mode;
uniform float tolerance;
uniform float softness;
out vec4 out_color;
float srgb_linear(float c) { return c <= 0.04045 ? c/12.92 : pow((c+0.055)/1.055, 2.4); }
vec3 oklab(vec3 rgb) {
    vec3 c = vec3(srgb_linear(rgb.r), srgb_linear(rgb.g), srgb_linear(rgb.b));
    vec3 lms = vec3(
        dot(c, vec3(0.4122214708,0.5363325363,0.0514459929)),
        dot(c, vec3(0.2119034982,0.6806995451,0.1073969566)),
        dot(c, vec3(0.0883024619,0.2817188376,0.6299787005))
    );
    lms = sign(lms) * pow(abs(lms), vec3(1.0/3.0));
    return vec3(
        dot(lms,vec3(0.2104542553,0.7936177850,-0.0040720468)),
        dot(lms,vec3(1.9779984951,-2.4285922050,0.4505937099)),
        dot(lms,vec3(0.0259040371,0.7827717662,-0.8086757660))
    );
}
void main() {
    vec3 rgb = texelFetch(source_tex, ivec2(gl_FragCoord.xy), 0).rgb;
    vec3 v = space_mode == 1 ? oklab(rgb) : rgb;
    float dmin = 1e10;
    float best = 0.0;
    for (int i=0; i<nkey; ++i) {
        vec3 k = space_mode == 1 ? oklab(keys[i].rgb) : keys[i].rgb;
        vec3 d = v-k;
        float dist = dot(d,d) / (space_mode == 1 ? 1.0 : 3.0);
        if (dist < dmin) { dmin = dist; best = float(i); }
    }
    float dist = sqrt(dmin);
    float keep = softness == 0.0 ? float(dist > tolerance) : smoothstep(0.0,1.0,clamp((dist-tolerance)/softness,0.0,1.0));
    // G holds nearest key index / 255, sufficient for 8 chroma keys.
    out_color = vec4(keep, best / 255.0, 0.0, 1.0);
}
"""

# Two sequential passes implement the square MaxFilter/MinFilter footprint.
# For fractional radii, channel R contains the floor-radius result and G
# contains the ceil-radius result until the final vertical pass.
MORPH_HORIZONTAL = """#version 330
uniform sampler2D source_tex;
uniform int inner_radius;
uniform int outer_radius;
uniform int dilation;
out vec4 out_color;
void main() {
    ivec2 pos=ivec2(gl_FragCoord.xy);
    ivec2 size=textureSize(source_tex,0)-ivec2(1);
    float a=dilation==1?0.0:1.0;
    float b=a;
    for (int i=-16; i<=16; ++i) {
        if (abs(i)>outer_radius) continue;
        float x=texelFetch(source_tex, clamp(pos+ivec2(i,0), ivec2(0),size),0).r;
        b=dilation==1?max(b,x):min(b,x);
        if (abs(i)<=inner_radius) a=dilation==1?max(a,x):min(a,x);
    }
    out_color=vec4(a,b,texelFetch(source_tex,pos,0).g,1.0);
}
"""
MORPH_VERTICAL = """#version 330
uniform sampler2D source_tex;
uniform int inner_radius;
uniform int outer_radius;
uniform int dilation;
uniform float fraction;
out vec4 out_color;
void main() {
    ivec2 pos=ivec2(gl_FragCoord.xy);
    ivec2 size=textureSize(source_tex,0)-ivec2(1);
    float a=dilation==1?0.0:1.0;
    float b=a;
    for (int i=-16; i<=16; ++i) {
        if (abs(i)>outer_radius) continue;
        vec2 x=texelFetch(source_tex,clamp(pos+ivec2(0,i),ivec2(0),size),0).rg;
        b=dilation==1?max(b,x.g):min(b,x.g);
        if (abs(i)<=inner_radius) a=dilation==1?max(a,x.r):min(a,x.r);
    }
    vec4 initial=texelFetch(source_tex,pos,0);
    out_color=vec4(mix(a,b,fraction),initial.b,0.0,1.0);
}
"""

BLUR = """#version 330
uniform sampler2D source_tex;
uniform ivec2 direction;
uniform float sigma;
out vec4 out_color;
void main() {
    ivec2 pos=ivec2(gl_FragCoord.xy);
    ivec2 size=textureSize(source_tex,0)-ivec2(1);
    float weighted=0.0, total=0.0;
    int radius=int(ceil(3.0*sigma));
    for (int i=-36;i<=36;++i) {
        if (abs(i)>radius) continue;
        float w=exp(-float(i*i)/(2.0*sigma*sigma));
        weighted += texelFetch(source_tex,clamp(pos+direction*i,ivec2(0),size),0).r*w;
        total += w;
    }
    out_color=vec4(weighted/total, texelFetch(source_tex,pos,0).g,0.0,1.0);
}
"""

MATTE_FINAL = """#version 330
uniform sampler2D source_tex;
uniform sampler2D mask_tex;
uniform sampler2D original_tex;
uniform int original_mode;
uniform int inverted;
uniform float mask_opacity;
out vec4 out_color;
void main() {
    ivec2 pos=ivec2(gl_FragCoord.xy);
    vec4 src=texelFetch(source_tex,pos,0);
    float m=texelFetch(mask_tex,pos,0).r;
    if(inverted==1) m=1.0-m;
    m=mix(1.0,m,mask_opacity);
    float base=original_mode==1 ? texelFetch(original_tex,pos,0).r : src.a;
    out_color=vec4(src.rgb,base*m);
}
"""

KEY_FINAL = """#version 330
uniform sampler2D source_tex;
uniform sampler2D mask_tex;
uniform sampler2D original_tex;
uniform int original_mode;
uniform float despill;
uniform vec4 keys[8];
uniform int nkey;
out vec4 out_color;
void main() {
    ivec2 pos=ivec2(gl_FragCoord.xy);
    vec4 src=texelFetch(source_tex,pos,0);
    vec4 mask=texelFetch(mask_tex,pos,0);
    float keep=mask.r;
    float alpha=(original_mode==1 ? texelFetch(original_tex,pos,0).r : src.a)*keep;
    vec3 rgb=src.rgb;
    if(despill>0.0) {
        int which=clamp(int(round(mask.g*255.0)),0,nkey-1);
        vec3 key=keys[which].rgb;
        int dominant = key.r >= key.g && key.r >= key.b ? 0 : (key.g >= key.b ? 1 : 2);
        float other=max(rgb[(dominant+1)%3],rgb[(dominant+2)%3]);
        rgb[dominant] -= max(0.0,rgb[dominant]-other)*despill*(1.0-keep);
    }
    out_color=vec4(rgb,alpha);
}
"""


class GLMatteBackend:
    """Reusable per-node ModernGL executor; input and output are Pillow RGBA.

    Resources are released after every call, while programs/VAOs are cached for
    the context lifetime. `close` owns the context only if it created it.
    """
    def __init__(self, ctx=None, *, backend=None, max_working_set_bytes=768 * 1024**2):
        try:
            import moderngl
        except ImportError as exc:
            raise RuntimeError('GPU matte effects need the optional moderngl GPU extra') from exc
        self._moderngl = moderngl
        self._owns_context = ctx is None
        self.ctx = ctx or self._make_context(moderngl, backend)
        self.max_working_set_bytes = max_working_set_bytes
        if self.ctx.version_code < 330:
            raise RuntimeError('GPU matte/keying requires OpenGL 3.3 or later')
        self._programs = {}
        self._vaos = {}
        self.backend = backend or 'platform/egl'
        self.capabilities = GPUCapabilities(
            context_version=self.ctx.version_code,
            backend=str(backend or 'platform/egl'),
            renderer=str(self.ctx.info.get('GL_RENDERER', 'unknown')),
        )
        self.last_dispatch = {'gpu_nodes': 0, 'cpu_fallbacks': 0}
        self.closed = False

    @staticmethod
    def _make_context(moderngl, backend):
        if backend is not None:
            return moderngl.create_standalone_context(require=330, backend=backend)
        try:
            return moderngl.create_standalone_context(require=330)
        except Exception as first:
            try:
                return moderngl.create_standalone_context(require=330, backend='egl')
            except Exception as second:
                raise RuntimeError(f'No standalone OpenGL 3.3 context (platform: {first}; EGL: {second})') from second

    def _check_budget(self, size, texture_count):
        if size[0] > self.ctx.info.get('GL_MAX_TEXTURE_SIZE', 16384) or size[1] > self.ctx.info.get('GL_MAX_TEXTURE_SIZE', 16384):
            raise GPUUnsupported('Input dimensions exceed GPU texture limit')
        estimate = size[0] * size[1] * 4 * texture_count
        if estimate > self.max_working_set_bytes:
            raise GPUUnsupported(f'GPU working-set estimate {estimate} exceeds budget {self.max_working_set_bytes}')

    def _get_shader(self, name, fragment):
        if name not in self._programs:
            program = self.ctx.program(vertex_shader=VERTEX, fragment_shader=fragment)
            self._programs[name] = program
            self._vaos[name] = self.ctx.vertex_array(program, [])
        return self._programs[name], self._vaos[name]

    def _input(self, image):
        if image.mode != 'RGBA':
            raise ValueError('GPU input must be RGBA')
        texture = self.ctx.texture(image.size, 4, image.tobytes(), alignment=1)
        texture.filter = (self._moderngl.NEAREST, self._moderngl.NEAREST)
        texture.repeat_x = False
        texture.repeat_y = False
        return texture

    def _target(self, size):
        texture = self.ctx.texture(size, 4)
        texture.filter = (self._moderngl.NEAREST, self._moderngl.NEAREST)
        framebuffer = self.ctx.framebuffer(color_attachments=[texture])
        return texture, framebuffer

    def _draw(self, shader, fragment, sources, size, uniforms, allocations):
        program, vao = self._get_shader(shader, fragment)
        output, fb = self._target(size)
        allocations.extend([fb, output])
        fb.use()
        self.ctx.viewport = (0, 0, *size)
        self.ctx.disable(self._moderngl.BLEND)
        for unit, (name, texture) in enumerate(sources.items()):
            texture.use(location=unit)
            # Array uniforms are configured separately to avoid sampler name
            # interpretation differences across ModernGL versions.
            if name != 'masks' and name in program:
                program[name].value = unit
        if 'masks' in sources:
            raise RuntimeError('internal: masks must be configured through explicit uniforms')
        for name, value in uniforms.items():
            if name in program:
                program[name].value = value
        vao.render(mode=self._moderngl.TRIANGLES, vertices=3)
        return output, fb

    def _blur_or_morph(self, mask, size, *, morph=0, blur=0, allocations):
        if morph:
            radius = abs(morph)
            low, high = math.floor(radius), math.ceil(radius)
            h, _ = self._draw('morph_h', MORPH_HORIZONTAL, {'source_tex': mask}, size,
                              dict(inner_radius=low, outer_radius=high, dilation=int(morph > 0)), allocations)
            mask, _ = self._draw('morph_v', MORPH_VERTICAL, {'source_tex': h}, size,
                                 dict(inner_radius=low, outer_radius=high, dilation=int(morph > 0),
                                      fraction=radius-low), allocations)
        if blur:
            for direction in ((1, 0), (0, 1)):
                mask, _ = self._draw('blur', BLUR, {'source_tex': mask}, size,
                                     dict(direction=direction, sigma=blur), allocations)
        return mask

    @staticmethod
    def _require_supported(config, kind):
        if kind == 'matte':
            morph, blur = config.get('expand', 0), config.get('feather', 0)
        elif kind == 'chroma_key':
            morph, blur = config.get('edge_expand', 0), config.get('edge_feather', 0)
            if config.get('edge_decontaminate', 0):
                raise GPUUnsupported('GPU edge decontamination is not implemented; use CPU or auto backend')
        else:
            raise ValueError('invalid GPU node kind')
        if abs(morph) > MAX_GPU_MORPH_RADIUS or blur > MAX_GPU_GAUSSIAN_RADIUS:
            raise GPUUnsupported('GPU radius limit exceeded; use CPU or auto backend')

    def apply_matte(self, images, config, *, original_alpha=None):
        from .effect_graph_mattes import validate_matte
        validate_matte(config, len(images))
        self._require_supported(config, 'matte')
        size = images[0].size
        if any(im.size != size for im in images):
            raise ValueError('GPU matte inputs need equal size')
        self._check_budget(size, len(images) + 5 + int(original_alpha is not None))
        allocations = []
        try:
            fg = self._input(images[0]); allocations.append(fg)
            mask_inputs = []
            for im in images[1:]:
                tex = self._input(im)
                allocations.append(tex)
                mask_inputs.append(tex)
            origin = self._input(original_alpha.convert('RGBA')) if original_alpha is not None else fg
            if origin is not fg: allocations.append(origin)
            # Separate shader sampler array assignments: bind an additional
            # dummy image to unused slots only if the driver requires it.
            program, vao = self._get_shader('mask_init', MASK_INIT)
            mask, fb = self._target(size)
            allocations.extend((mask, fb))
            fb.use()
            self.ctx.viewport = (0, 0, *size)
            self.ctx.disable(self._moderngl.BLEND)
            for i, tex in enumerate(mask_inputs): tex.use(location=i)
            program['masks'].value = tuple(range(len(mask_inputs))) + (0,) * (8-len(mask_inputs))
            program['nmask'].value = len(mask_inputs)
            program['channel_mode'].value = int(config.get('matte_channel', 'alpha') == 'luminance')
            program['combine_mode'].value = ['multiply','min','max','add','subtract','difference','screen'].index(config.get('mask_combine','multiply'))
            vao.render(mode=self._moderngl.TRIANGLES, vertices=3)
            mask = self._blur_or_morph(mask, size, morph=config.get('expand', 0),
                                       blur=config.get('feather', 0), allocations=allocations)
            _, fb = self._draw('matte_final', MATTE_FINAL,
                               {'source_tex': fg, 'mask_tex': mask, 'original_tex': origin}, size,
                               dict(original_mode=int(config.get('alpha_source','current')=='original'),
                                    inverted=int(config.get('invert',False)),
                                    mask_opacity=config.get('mask_opacity',1)), allocations)
            return Image.frombytes('RGBA', size, fb.read(components=4, alignment=1))
        finally:
            for resource in reversed(allocations):
                resource.release()

    def chroma_key(self, image, config, *, original_alpha=None):
        from .effect_graph_mattes import validate_key
        validate_key(config)
        self._require_supported(config, 'chroma_key')
        size = image.size
        self._check_budget(size, 6 + int(original_alpha is not None))
        keys = config.get('key_colors') or [config.get('key_color','#00ff00')]
        if 'key_colors' in config and 'key_color' in config:
            keys = [config['key_color'], *config['key_colors']]
        colors = [tuple(x / 255 for x in ImageColor.getrgb(k)) + (1,) for k in keys]
        allocations = []
        try:
            fg = self._input(image); allocations.append(fg)
            origin = self._input(original_alpha.convert('RGBA')) if original_alpha is not None else fg
            if origin is not fg: allocations.append(origin)
            shader, vao = self._get_shader('key_init', KEY_INIT)
            mask, fb = self._target(size)
            allocations.extend((mask, fb))
            fb.use()
            self.ctx.viewport = (0,0,*size)
            self.ctx.disable(self._moderngl.BLEND)
            fg.use(location=0)
            shader['source_tex'].value = 0
            import struct
            packed = struct.pack('<32f', *(x for color in (colors + [(0.,0.,0.,1.)]*(8-len(colors))) for x in color))
            shader['keys'].write(packed)
            shader['nkey'].value = len(colors)
            shader['space_mode'].value = int(config.get('key_space','rgb') == 'oklab')
            shader['tolerance'].value = config.get('tolerance', 0.1)
            shader['softness'].value = config.get('softness', 0.1)
            vao.render(mode=self._moderngl.TRIANGLES, vertices=3)
            mask = self._blur_or_morph(mask, size, morph=config.get('edge_expand',0),
                                       blur=config.get('edge_feather',0), allocations=allocations)
            shader, _ = self._get_shader('key_final', KEY_FINAL)
            shader['keys'].write(packed)
            _, fb = self._draw('key_final', KEY_FINAL,
                               {'source_tex': fg, 'mask_tex': mask, 'original_tex': origin}, size,
                               dict(original_mode=int(config.get('alpha_source','current')=='original'),
                                    despill=config.get('despill',0), nkey=len(colors)), allocations)
            return Image.frombytes('RGBA', size, fb.read(components=4, alignment=1))
        finally:
            for resource in reversed(allocations):
                resource.release()

    def close(self):
        if self.closed:
            return
        for vao in self._vaos.values(): vao.release()
        for program in self._programs.values(): program.release()
        if self._owns_context: self.ctx.release()
        self.closed = True

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
