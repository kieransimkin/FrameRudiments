"""Bounded GLSL 3.30 spectral-displacement passes for the ModernGL graph bridge.

The source is uploaded once and two intermediate textures are ping-ponged for
up to eight independently selected bands. No feedback loops or GPU-side state
are retained between frame evaluations. All shader inputs originate in the
strictly validated CPU graph configuration; no user-supplied shader code.
"""
from __future__ import annotations

from PIL import Image, ImageColor

from .effect_graph_spectral import validate_spectral


# RGB, HSV and OKLab match the M4 CPU reference: selection always happens on
# straight source RGB, not on the recoloured or previously composited output.
COLOUR_FUNCTIONS = """
float linearise(float c) {
    return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4);
}
vec3 oklab(vec3 rgb) {
    vec3 v = vec3(linearise(rgb.r), linearise(rgb.g), linearise(rgb.b));
    vec3 lms = vec3(
        dot(v, vec3(0.4122214708, 0.5363325363, 0.0514459929)),
        dot(v, vec3(0.2119034982, 0.6806995451, 0.1073969566)),
        dot(v, vec3(0.0883024619, 0.2817188376, 0.6299787005)));
    lms = pow(lms, vec3(1.0 / 3.0));
    return vec3(
        dot(lms, vec3(0.2104542553, 0.7936177850, -0.0040720468)),
        dot(lms, vec3(1.9779984951, -2.4285922050, 0.4505937099)),
        dot(lms, vec3(0.0259040371, 0.7827717662, -0.8086757660)));
}
vec3 hsv(vec3 c) {
    float high = max(c.r, max(c.g, c.b));
    float low = min(c.r, min(c.g, c.b));
    float delta = high - low;
    float saturation = high > 1e-8 ? delta / high : 0.0;
    float denom = delta > 1e-8 ? delta : 1.0;
    float hue = 0.0;
    if (delta > 1e-8) {
        if (high == c.r) hue = mod((c.g - c.b) / denom, 6.0);
        if (high == c.g) hue = (c.b - c.r) / denom + 2.0;
        if (high == c.b) hue = (c.r - c.g) / denom + 4.0;
    }
    return vec3(hue / 6.0, saturation, high);
}
float coverage(vec3 rgb, vec3 target, int space, float tolerance, float softness) {
    float distance;
    if (space == 0) {
        vec3 delta = rgb - target;
        distance = sqrt(dot(delta, delta) / 3.0);
    } else if (space == 1) {
        vec3 delta = oklab(rgb) - oklab(target);
        distance = length(delta);
    } else {
        vec3 a = hsv(rgb);
        vec3 b = hsv(target);
        float dh = abs(a.x - b.x);
        dh = min(dh, 1.0 - dh) * 2.0 * min(a.y, b.y);
        vec3 delta = vec3(dh, a.y - b.y, a.z - b.z);
        distance = sqrt(dot(delta, delta) / 3.0);
    }
    if (softness == 0.0) return float(distance <= tolerance);
    float t = clamp((distance - tolerance) / softness, 0.0, 1.0);
    return 1.0 - t * t * (3.0 - 2.0 * t);
}
"""

BAND_UNIFORMS = """
uniform vec3 source_colour;
uniform vec3 target_colour;
uniform vec2 offset;
uniform int selection_space;
uniform float tolerance;
uniform float softness;
uniform float band_opacity;
uniform float recolor;
"""

# The same sampler texture is evaluated for all bands. The removal pass
# accumulates maximum selected opacity without constructing an 8x-frame map.
BASE = ("""#version 330
uniform sampler2D original_tex;
uniform int band_count;
uniform vec4 source_colours[8];
uniform vec4 controls[8]; // tolerance, softness, opacity, space enum
out vec4 out_color;
""" + COLOUR_FUNCTIONS + """
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec4 src = texelFetch(original_tex, p, 0);
    float removal = 0.0;
    for (int i = 0; i < band_count; ++i) {
        vec4 ctl = controls[i];
        float m = coverage(src.rgb, source_colours[i].rgb,
                           int(ctl.w + 0.5), ctl.x, ctl.y) * ctl.z;
        removal = max(removal, m);
    }
    out_color = vec4(src.rgb, src.a * (1.0 - removal));
}
""")

COPY = """#version 330
uniform sampler2D original_tex;
out vec4 out_color;
void main() {
    out_color = texelFetch(original_tex, ivec2(gl_FragCoord.xy), 0);
}
"""

BAND = ("""#version 330
uniform sampler2D original_tex;
uniform sampler2D previous_tex;
""" + BAND_UNIFORMS + """
out vec4 out_color;
""" + COLOUR_FUNCTIONS + """
void main() {
    ivec2 pos = ivec2(gl_FragCoord.xy);
    ivec2 size = textureSize(original_tex, 0);
    vec2 coordinate = vec2(pos) - offset;
    ivec2 low = ivec2(floor(coordinate));
    vec2 fraction = coordinate - vec2(low);
    float weighted_alpha = 0.0;
    vec3 weighted_rgb = vec3(0.0);
    // Bilinear sampling of selected, *premultiplied* contributions.
    for (int y = 0; y < 2; ++y) {
        for (int x = 0; x < 2; ++x) {
            ivec2 location = low + ivec2(x, y);
            if (any(lessThan(location, ivec2(0))) ||
                any(greaterThanEqual(location, size))) continue;
            float weight_x = x == 0 ? (1.0 - fraction.x) : fraction.x;
            float weight_y = y == 0 ? (1.0 - fraction.y) : fraction.y;
            vec4 pixel = texelFetch(original_tex, location, 0);
            float a = pixel.a * coverage(pixel.rgb, source_colour,
                                         selection_space, tolerance, softness);
            a *= weight_x * weight_y;
            weighted_alpha += a;
            weighted_rgb += pixel.rgb * a;
        }
    }
    vec3 selected = weighted_alpha > 1e-7
        ? weighted_rgb / weighted_alpha : vec3(0.0);
    selected = mix(selected, target_colour, recolor);
    float selected_alpha = clamp(weighted_alpha * band_opacity, 0.0, 1.0);
    vec4 previous = texelFetch(previous_tex, pos, 0);
    float alpha = selected_alpha + previous.a * (1.0 - selected_alpha);
    vec3 rgb = alpha > 1e-8
        ? (selected * selected_alpha +
           previous.rgb * previous.a * (1.0 - selected_alpha)) / alpha
        : vec3(0.0);
    out_color = vec4(rgb, alpha);
}
""")

FINAL = """#version 330
uniform sampler2D original_tex;
uniform sampler2D processed_tex;
uniform int clip_to_source;
uniform float amount;
out vec4 out_color;
void main() {
    ivec2 pos = ivec2(gl_FragCoord.xy);
    vec4 source = texelFetch(original_tex, pos, 0);
    vec4 processed = texelFetch(processed_tex, pos, 0);
    if (clip_to_source == 1) {
        processed.a = min(processed.a, source.a);
        if (processed.a == 0.0) processed.rgb = vec3(0.0);
    }
    if (amount == 1.0) {
        out_color = processed;
        return;
    }
    float alpha = mix(source.a, processed.a, amount);
    vec3 premultiplied = mix(source.rgb * source.a,
                             processed.rgb * processed.a, amount);
    vec3 rgb = alpha > 1e-8 ? premultiplied / alpha : vec3(0.0);
    out_color = vec4(rgb, alpha);
}
"""

SPACE_INDEX = {"rgb": 0, "oklab": 1, "hsv": 2}


def _bind_band(program, band):
    """Bind one validated band's independently animated uniforms."""
    source = tuple(x / 255.0 for x in ImageColor.getrgb(band["source_color"]))
    target = tuple(x / 255.0 for x in ImageColor.getrgb(band.get("target_color", band["source_color"])))
    program["source_colour"].value = source
    program["target_colour"].value = target
    program["offset"].value = tuple(band.get("offset", (0.0, 0.0)))
    program["selection_space"].value = SPACE_INDEX[band.get("space", "oklab")]
    program["tolerance"].value = band.get("tolerance", 0.08)
    program["softness"].value = band.get("softness", 0.08)
    program["band_opacity"].value = band.get("opacity", 1.0)
    program["recolor"].value = band.get("recolor", 0.0)


def render_spectral(backend, image: Image.Image, config: dict) -> Image.Image:
    """Run the entire spectral operation in bounded GPU passes.

    Every pass uses two reusable RGBA8 framebuffers. Total texture count is
    three (one uploaded source, two ping-pong targets), not proportional to
    the number of bands. Download happens only after all bands and final mix.
    """
    from .effect_graph_gpu import GPUUnsupported

    validate_spectral(config)
    if image.mode != "RGBA":
        raise ValueError("spectral GPU input must be RGBA")
    if config.get("mix", 1.0) == 0:
        return image.copy()
    size = image.size
    # One source plus two intermediates and the final output texture.
    backend._check_budget(size, 4)
    allocations = []
    try:
        src = backend._input(image)
        allocations.append(src)
        textures = []
        buffers = []
        for _ in range(2):
            tex, fb = backend._target(size)
            textures.append(tex)
            buffers.append(fb)
            allocations.extend((tex, fb))
        final_tex, final_fb = backend._target(size)
        allocations.extend((final_tex, final_fb))

        def draw(name, fragment, target, image_sources, uniforms=None):
            program, vao = backend._get_shader(name, fragment)
            target.use()
            backend.ctx.viewport = (0, 0, *size)
            backend.ctx.disable(backend._moderngl.BLEND)
            for unit, (param, texture) in enumerate(image_sources.items()):
                texture.use(location=unit)
                program[param].value = unit
            for param, value in (uniforms or {}).items():
                program[param].value = value
            vao.render(mode=backend._moderngl.TRIANGLES, vertices=3)
            return program

        bands = config["bands"]
        if config.get("spectral_mode", "overlay") == "replace":
            shader, _ = backend._get_shader("spectral_base", BASE)
            # GLSL uniform vec4 arrays require the full fixed length. Pad to
            # eight without changing the number of active spectral bands.
            import struct
            colours = [(*[v / 255 for v in ImageColor.getrgb(b["source_color"])], 1.0)
                       for b in bands]
            controls = [(b.get("tolerance", .08), b.get("softness", .08),
                         b.get("opacity", 1), SPACE_INDEX[b.get("space", "oklab")])
                        for b in bands]
            pad = (0.0, 0.0, 0.0, 0.0)
            shader["source_colours"].write(struct.pack("<32f", *(v for row in (colours + [pad] * (8 - len(bands))) for v in row)))
            shader["controls"].write(struct.pack("<32f", *(v for row in (controls + [pad] * (8 - len(bands))) for v in row)))
            draw("spectral_base", BASE, buffers[0], {"original_tex": src}, {"band_count": len(bands)})
        else:
            draw("spectral_copy", COPY, buffers[0], {"original_tex": src})

        last = 0
        for band in bands:
            next_index = 1 - last
            shader, _ = backend._get_shader("spectral_band", BAND)
            _bind_band(shader, band)
            draw("spectral_band", BAND, buffers[next_index],
                 {"original_tex": src, "previous_tex": textures[last]})
            last = next_index
        draw("spectral_final", FINAL, final_fb,
             {"original_tex": src, "processed_tex": textures[last]},
             {"clip_to_source": int(config.get("alpha_mode", "expanded") == "source"),
              "amount": config.get("mix", 1.0)})
        return Image.frombytes("RGBA", size, final_fb.read(components=4, alignment=1))
    except MemoryError as exc:
        raise GPUUnsupported("Spectral GPU allocation exhausted") from exc
    finally:
        for resource in reversed(allocations):
            resource.release()
