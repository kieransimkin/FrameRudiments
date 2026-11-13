"""GLSL inverse coordinates and premultiplied sampling for M8.9 spatial warps."""
from __future__ import annotations

from math import exp, radians, remainder, tau

from PIL import Image

from .effect_graph_backend_contract import gpu_requirements
from .effect_graph_gpu_kernels import _execute
from .effect_graph_ripples import MAX_RIPPLE_SOURCES, RIPPLE_VARIANT_ORDER, prepare_ripple_field
from .effect_graph_spatial import (
    GPU_WARP_TYPES, WARP_DEFAULTS, spatial_map_is_transparent, validate_spatial_images,
)


SPATIAL_SHADER_NAME = 'm89_spatial_warp'
_SPATIAL_CONSTANTS = '\n'.join([
    *(f'const int WARP_{name.upper()} = {index};' for index, name in enumerate(GPU_WARP_TYPES)),
    *(f'const int RIPPLE_{name.upper()} = {index};' for index, name in enumerate(RIPPLE_VARIANT_ORDER)),
    f'const int MAX_RIPPLE_SOURCES = {MAX_RIPPLE_SOURCES};',
])
SPATIAL_WARP = "#version 330\n" + _SPATIAL_CONSTANTS + """
uniform sampler2D source_tex;
uniform sampler2D map_tex;
uniform ivec2 map_channels;
uniform float map_neutral;
uniform int kind;
uniform int border;
uniform int alpha_only;
uniform float amount;
uniform vec2 amplitude;
uniform vec2 wavelength;
uniform vec2 centre;
uniform float angle;
uniform float radius_limit;
uniform float radial_power;
uniform float phase;
uniform int octaves;
uniform int seed;
uniform int segments;
uniform ivec2 mirror_axis;
uniform ivec2 polar_origin_pixel;
uniform vec2 polar_origin_fraction;
uniform int ripple_variant;
uniform int ripple_count;
uniform vec4 ripple_sources[MAX_RIPPLE_SOURCES];
uniform vec2 ripple_centre_fractions[MAX_RIPPLE_SOURCES];
uniform float ripple_sigma;
uniform float ripple_normalizer;
out vec4 out_color;

const float PI = 3.141592653589793;
const float TAU = 2.0 * PI;

float lattice(ivec2 position, int noise_seed) {
    uint n = uint(position.x) * 0x1f123bb5u;
    n ^= uint(position.y) * 0x5f356495u;
    n ^= uint(noise_seed * 7919);
    n ^= n >> 16u;
    n *= 0x7feb352du;
    n ^= n >> 15u;
    n *= 0x846ca68bu;
    n ^= n >> 16u;
    return (float(n) / 4294967295.0) * 2.0 - 1.0;
}

float noise_at(vec2 p, int noise_seed) {
    ivec2 corner = ivec2(floor(p));
    vec2 f = fract(p);
    f = f*f*f * (f * (f*6.0 - 15.0) + 10.0);
    float low = mix(lattice(corner, noise_seed), lattice(corner+ivec2(1,0), noise_seed), f.x);
    float high = mix(lattice(corner+ivec2(0,1), noise_seed), lattice(corner+ivec2(1,1), noise_seed), f.x);
    return mix(low, high, f.y);
}

float map_value(vec4 control, int channel) {
    if (channel == 4) return dot(control.rgb, vec3(0.2126, 0.7152, 0.0722));
    return control[channel];
}

float map_offset(float value, float alpha) {
    float delta = value - map_neutral;
    return alpha * delta / (delta >= 0.0 ? 1.0 - map_neutral : map_neutral);
}

float polar_angle(vec2 delta) {
    if (delta.x != 0.0) return atan(delta.y, delta.x);
    return sign(delta.y) * PI * 0.5;
}

float ripple_lens(float distance, float radius) {
    float q = distance / radius;
    return q < 1.0 ? q * sqrt(max(0.0, 1.0 - q)) : 0.0;
}

vec2 ripple_offset(vec2 p) {
    vec2 offset = vec2(0.0);
    for (int index = 0; index < ripple_count; ++index) {
        vec4 drop = ripple_sources[index];
        // Keep directions stable even when a drop falls almost on a pixel.
        vec2 delta = (p - drop.xy) - ripple_centre_fractions[index];
        float distance = length(delta);
        if (distance <= 1e-12) continue;
        float magnitude;
        if (ripple_variant == RIPPLE_DROPLETS) {
            magnitude = ripple_lens(distance, drop.z) * drop.w;
        } else {
            float relative = distance - drop.w;
            float r = relative / ripple_sigma;
            magnitude = sin(TAU * relative / wavelength.x) * exp(-0.5 * r * r);
            if (ripple_variant == RIPPLE_STORM) magnitude += 0.35 * ripple_lens(distance, drop.z);
        }
        offset += delta / distance * magnitude;
    }
    return clamp(offset / ripple_normalizer, -1.0, 1.0);
}

vec2 inverse_coordinate(vec2 p, vec2 size) {
    if (kind == WARP_RAINDROP_RIPPLES) {
        return p - amplitude * ripple_offset(p);
    }
    if (kind == WARP_DISPLACEMENT) {
        vec4 control = texelFetch(map_tex, ivec2(p), 0);
        vec2 displacement = vec2(map_offset(map_value(control, map_channels.x), control.a),
                                 map_offset(map_value(control, map_channels.y), control.a));
        return p - amplitude * displacement;
    }
    if (kind == WARP_WAVE) {
        // Reduce the periodic argument before multiplying by TAU at large pixel indices.
        return p - amplitude * sin(TAU * mod(p.yx, wavelength.yx) / wavelength.yx + phase);
    }
    if (kind == WARP_TURBULENCE) {
        vec2 offset = vec2(0.0);
        float total = 0.0;
        for (int octave = 0; octave < octaves; ++octave) {
            float frequency = float(1 << octave);
            float weight = 1.0 / frequency;
            vec2 at = p / wavelength * frequency + phase * vec2(1.0, 0.61803398875);
            int noise_seed = seed + octave * 17;
            offset += weight * vec2(noise_at(at, noise_seed),
                                    noise_at(at + vec2(31.7, -19.3), noise_seed + 101));
            total += weight;
        }
        return p - amplitude * offset / total;
    }
    if (kind == WARP_POLAR_WRAP) {
        // Subtract the integer origin before its fractional part, preserving
        // the direction at a pixel arbitrarily close to the polar centre.
        vec2 delta = vec2(ivec2(p) - polar_origin_pixel) - polar_origin_fraction;
        float wrapped = mod(polar_angle(delta) - angle, TAU);
        return vec2(wrapped / TAU * size.x, length(delta) / radius_limit * size.y) - 0.5;
    }
    vec2 origin = centre * size - 0.5;
    vec2 delta = p - origin;
    if (kind == WARP_MIRROR) {
        return mix(p, origin - abs(delta), vec2(mirror_axis));
    }
    float radius = length(delta);
    if (kind == WARP_SWIRL) {
        if (radius >= radius_limit) return p;
        float falloff = 1.0 - radius / radius_limit;
        float turn = -angle * falloff * falloff;
        float cs = cos(turn), sn = sin(turn);
        return origin + vec2(delta.x * cs - delta.y * sn, delta.x * sn + delta.y * cs);
    }
    if (kind == WARP_RADIAL) {
        if (radius == 0.0 || radius >= radius_limit) return p;
        float mapped = radius_limit * pow(radius / radius_limit, radial_power);
        return origin + delta * (mapped / radius);
    }
    if (kind == WARP_KALEIDOSCOPE) {
        if (radius == 0.0) return p;
        float sector = TAU / float(segments);
        float theta = polar_angle(delta);
        float folded = abs(mod(theta - angle + sector * 0.5, sector) - sector * 0.5) + angle;
        return origin + radius * vec2(cos(folded), sin(folded));
    }
    // WARP_POLAR_UNWRAP is the only remaining validated kind.
    float theta = (p.x + 0.5) / size.x * TAU + angle;
    float r = (p.y + 0.5) / size.y * radius_limit;
    return origin + r * vec2(cos(theta), sin(theta));
}

vec4 premultiply(vec4 colour) {
    return vec4(colour.rgb * colour.a, colour.a);
}

vec4 tap(ivec2 p, ivec2 size) {
    if (border == 0 && (any(lessThan(p, ivec2(0))) || any(greaterThanEqual(p, size)))) {
        return vec4(0.0);
    }
    if (border == 2) p = ivec2(mod(vec2(p), vec2(size)));
    if (border == 3) {
        p = ivec2(mod(vec2(p), vec2(2 * size)));
        p = min(p, 2 * size - 1 - p);
    }
    return premultiply(texelFetch(source_tex, clamp(p, ivec2(0), size - 1), 0));
}

void main() {
    ivec2 pixel = ivec2(gl_FragCoord.xy);
    ivec2 size = textureSize(source_tex, 0);
    vec2 at = inverse_coordinate(vec2(pixel), vec2(size));
    ivec2 corner = ivec2(floor(at));
    vec2 fraction = fract(at);
    vec4 low = mix(tap(corner, size), tap(corner+ivec2(1,0), size), fraction.x);
    vec4 high = mix(tap(corner+ivec2(0,1), size), tap(corner+ivec2(1,1), size), fraction.x);
    vec4 sampled = mix(low, high, fraction.y);
    vec4 result = mix(premultiply(texelFetch(source_tex, pixel, 0)), sampled, amount);
    float alpha = roundEven(result.a * 255.0) / 255.0;
    vec3 rgb = alpha_only == 0 && alpha > 0.0 ? result.rgb / result.a : vec3(0.0);
    out_color = vec4(rgb, alpha);
}
"""


def spatial_uniforms(config, size):
    def value(name):
        return config.get(name, WARP_DEFAULTS[name])

    kind = value('warp_type')
    phase = value('warp_phase')
    axis = value('warp_axis')
    channels = ('r', 'g', 'b', 'a', 'luminance')
    uniforms = {
        'kind': GPU_WARP_TYPES.index(kind),
        'border': ('transparent', 'clamp', 'repeat', 'mirror').index(value('warp_border')),
        'amount': value('warp_mix'),
        'amplitude': tuple(value('warp_amplitude')),
        'wavelength': tuple(value('warp_scale')),
        'centre': tuple(value('warp_center')),
        'angle': radians(value('warp_angle')),
        'radius_limit': value('warp_radius'),
        'radial_power': exp(value('warp_amount')),
        'phase': remainder(phase, tau) if kind == 'wave' else phase,
        'octaves': value('warp_octaves'),
        'seed': value('warp_seed'),
        'segments': value('warp_segments'),
        'mirror_axis': (int('x' in axis), int('y' in axis)),
        'map_channels': (channels.index(value('warp_map_x')), channels.index(value('warp_map_y'))),
        'map_neutral': value('warp_neutral'),
    }
    if kind == 'polar_wrap' and size is not None:
        centre = tuple(value * extent - .5 for value, extent in zip(value('warp_center'), size))
        pixels = tuple(round(coordinate) for coordinate in centre)
        uniforms.update(polar_origin_pixel=pixels, polar_origin_fraction=tuple(
            coordinate - pixel for coordinate, pixel in zip(centre, pixels)))
    if kind == 'raindrop_ripples' and size is not None:
        variant = value('warp_ripple_variant')
        field = prepare_ripple_field(
            size, variant=variant, centre=value('warp_center'), radius=value('warp_radius'),
            wavelength=value('warp_scale')[0], phase=phase, count=value('warp_segments'),
            seed=value('warp_seed'),
        )
        sources, fractions = [], []
        for source in field.sources:
            x, y = round(source.x), round(source.y)
            sources.append((x, y, source.radius, source.pulse if variant == 'droplets' else source.front))
            fractions.append((source.x - x, source.y - y))
        unused = MAX_RIPPLE_SOURCES - len(sources)
        uniforms.update(
            ripple_variant=RIPPLE_VARIANT_ORDER.index(variant),
            ripple_count=len(sources),
            ripple_sources=tuple(sources) + ((0, 0, 0, 0),) * unused,
            ripple_centre_fractions=tuple(fractions) + ((0, 0),) * unused,
            ripple_sigma=field.sigma,
            ripple_normalizer=field.normalizer if len(sources) > 1 else 1,
        )
    return uniforms


def spatial_draw_inputs(source, control_map, uniforms, *, alpha_only):
    """Bind the optional control map identically in the bridge and resident passes."""
    bindings = {'source_tex': source}
    if control_map is not None:
        bindings['map_tex'] = control_map
    return bindings, {**uniforms, 'alpha_only': int(alpha_only)}


def render_spatial_warp(backend, images, config, *, original_alpha=None):
    """Use the per-node bridge when an image and its lineage must return to the host."""
    from .effect_graph_gpu import GPUUnsupported

    req = gpu_requirements('spatial_warp', config, len(images), original_alpha=original_alpha is not None)
    if not req.supported:
        raise GPUUnsupported(f"spatial_warp {config.get('warp_type', 'wave')}: {req.reason}")
    image = images[0]
    control_map = images[1] if len(images) == 2 else None
    validate_spatial_images(image, map_image=control_map, original_alpha=original_alpha)
    if req.reason == 'exact_host_identity' or spatial_map_is_transparent(control_map):
        return image.copy(), original_alpha.copy() if original_alpha is not None else None

    uniforms = spatial_uniforms(config, image.size)

    def warp(source, *, alpha_only):
        def steps(_textures):
            yield SPATIAL_SHADER_NAME, SPATIAL_WARP, *spatial_draw_inputs(
                0, 1 if control_map is not None else None, uniforms, alpha_only=alpha_only,
            )

        inputs = [source] if control_map is None else [source, control_map]
        result = _execute(backend, inputs, steps, image.size, budget=req.texture_count)
        backend.last_spatial_draws += 1
        return result

    result = warp(image, alpha_only=0)
    lineage = None
    if original_alpha is not None:
        alpha_surface = Image.new('RGBA', image.size)
        alpha_surface.putalpha(original_alpha)
        lineage = warp(alpha_surface, alpha_only=1).getchannel('A')
    return result, lineage
