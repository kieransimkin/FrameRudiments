"""GLSL inverse coordinates and premultiplied sampling for M8.9 spatial warps."""
from __future__ import annotations

from math import cos, exp, radians, remainder, sin, tau
from struct import pack, unpack

from PIL import Image

from .effect_graph_backend_contract import gpu_requirements
from .effect_graph_gpu_kernels import _execute
from .effect_graph_ripples import MAX_RIPPLE_SOURCES, RIPPLE_VARIANT_ORDER, prepare_ripple_field
from .effect_graph_spatial import (
    GPU_WARP_TYPES, MAX_DIMENSION, PRECISE_SPATIAL_EXTENSION, WARP_DEFAULTS,
    spatial_map_is_transparent, spatial_requires_precise_gpu,
    validate_spatial_images,
)


SPATIAL_SHADER_NAME = 'm89_spatial_warp'
_SPATIAL_CONSTANTS = '\n'.join([
    *(f'const int WARP_{name.upper()} = {index};' for index, name in enumerate(GPU_WARP_TYPES)),
    *(f'const int RIPPLE_{name.upper()} = {index};' for index, name in enumerate(RIPPLE_VARIANT_ORDER)),
    f'const int MAX_RIPPLE_SOURCES = {MAX_RIPPLE_SOURCES};',
    f'const int POLAR_SEAM_BLOCKS = {(MAX_DIMENSION + 127) // 128};',
])
SPATIAL_WARP = "#version 330\n" + _SPATIAL_CONSTANTS + """
uniform sampler2D source_tex;
uniform sampler2D map_tex;
uniform ivec2 map_channels;
uniform float map_neutral;
uniform int invert_map;
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
uniform float wave_turns;
uniform uvec2 cycles_low;
uniform uvec2 cycles_mid;
uniform uvec2 cycles_high;
uniform uvec2 cycles_whole;
uniform ivec2 noise_cell;
uniform uvec2 noise_fraction;
uniform ivec2 shifted_noise_cell;
uniform uvec2 shifted_noise_fraction;
uniform vec2 wrap_radius_scale;
uniform vec2 wrap_angle_turns;
uniform int wrap_seam_axis;
uniform float wrap_seam_slope;
uniform uvec4 wrap_seam_mask[POLAR_SEAM_BLOCKS];
uniform int octaves;
uniform int seed;
uniform int segments;
uniform ivec2 mirror_axis;
uniform ivec2 origin_pixel;
uniform vec2 origin_fraction;
uniform vec2 origin_fraction_low;
uniform int ripple_variant;
uniform int ripple_count;
uniform vec4 ripple_sources[MAX_RIPPLE_SOURCES];
uniform vec2 ripple_centre_fractions[MAX_RIPPLE_SOURCES];
uniform vec2 ripple_centre_fractions_low[MAX_RIPPLE_SOURCES];
uniform float ripple_front_low[MAX_RIPPLE_SOURCES];
uniform float ripple_radius_low[MAX_RIPPLE_SOURCES];
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

// Multiply the integer pixel index by the host's 48-bit fixed-point reciprocal.
// The three 16-bit products and carries fit in uint at the supported canvas sizes.
// Keep the integer cell and the leading 32 fractional bits separate.
void pixel_cycles(uvec2 pixel, out ivec2 cell, out uvec2 fraction) {
    uvec2 low = pixel * cycles_low;
    uvec2 mid = pixel * cycles_mid + (low >> 16u);
    uvec2 high = pixel * cycles_high + (mid >> 16u);
    cell = ivec2(pixel * cycles_whole + (high >> 16u));
    fraction = (high << 16u) | (mid & 65535u);
}

vec2 unit_fraction(uvec2 fraction) {
    return vec2(fraction) * (1.0 / 4294967296.0);
}

float noise_at(ivec2 cell, uvec2 fraction, ivec2 shift_cell, uvec2 shift_fraction, int noise_seed) {
    uvec2 shifted = fraction + shift_fraction;
    ivec2 corner = cell + shift_cell + ivec2(lessThan(shifted, fraction));
    vec2 f = unit_fraction(shifted);
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
    return alpha * delta / (delta >= 0.0 ? 255.0 - map_neutral : map_neutral);
}

float polar_angle(vec2 delta) {
    if (delta.x != 0.0) return atan(delta.y, delta.x);
    return sign(delta.y) * PI * 0.5;
}

// Evaluate the angle in turns. The half-angle identity bounds the Taylor
// series argument by sqrt(2)-1, where the final term is below 1e-8 radians.
float direction_turns(vec2 delta) {
    vec2 axes = abs(delta);
    float major = max(axes.x, axes.y);
    if (major == 0.0) return 0.0;
    float ratio = min(axes.x, axes.y) / major;
    float t = ratio / (1.0 + sqrt(1.0 + ratio * ratio));
    float t2 = t * t;
    float arc = 1.0 / 19.0;
    arc = 1.0 / 17.0 - t2 * arc;
    arc = 1.0 / 15.0 - t2 * arc;
    arc = 1.0 / 13.0 - t2 * arc;
    arc = 1.0 / 11.0 - t2 * arc;
    arc = 1.0 / 9.0 - t2 * arc;
    arc = 1.0 / 7.0 - t2 * arc;
    arc = 1.0 / 5.0 - t2 * arc;
    arc = 1.0 / 3.0 - t2 * arc;
    float turns = (2.0 / TAU) * (t - t * t2 * arc);
    if (axes.y > axes.x) turns = 0.25 - turns;
    if (delta.x < 0.0) turns = 0.5 - turns;
    return delta.y < 0.0 ? -turns : turns;
}

vec2 sum_with_error(float a, float b);

float polar_column(ivec2 pixel, vec2 delta, float width) {
    vec2 offset = sum_with_error(direction_turns(delta), -wrap_angle_turns.x);
    offset.y -= wrap_angle_turns.y;
    float wrapped = offset.x - floor(offset.x);
    if (wrapped == 0.0 && offset.y < 0.0) wrapped = 1.0;
    float column = wrapped * width + offset.y * width;
    if (border != 2) {
        // Only the pixel nearest the forward ray can cross the seam through
        // angle rounding. Its exact side comes from the CPU angle convention.
        vec2 axes = wrap_seam_axis == 0 ? delta : delta.yx;
        if (abs(axes.y - wrap_seam_slope * axes.x) < 0.25) {
            float signed_turns = wrapped > 0.5 ? (wrapped - 1.0) + offset.y : wrapped + offset.y;
            if (abs(signed_turns) < 1e-5) {
                int scan = pixel[wrap_seam_axis];
                uint mask = wrap_seam_mask[scan / 128][(scan / 32) % 4];
                bool last_side = ((mask >> uint(scan % 32)) & 1u) != 0u;
                column = last_side ? width + min(0.0, signed_turns * width)
                                   : max(0.0, signed_turns * width);
            }
        }
    }
    return column - 0.5;
}

// Two floats retain the residual from a large radius or front calculation.
// Split operands at 12 significant bits so products are exact in binary32.
vec2 sum_with_error(float a, float b) {
    PRECISE float sum = a + b;
    PRECISE float b_virtual = sum - a;
    PRECISE float a_virtual = sum - b_virtual;
    return vec2(sum, (a - a_virtual) + (b - b_virtual));
}

vec2 split_float(float value) {
    PRECISE float shifted = 4097.0 * value;
    PRECISE float high = shifted - (shifted - value);
    return vec2(high, value - high);
}

vec2 product_with_error(float a, float b) {
    vec2 x = split_float(a);
    vec2 y = split_float(b);
    PRECISE float product = a * b;
    PRECISE float error = ((x.x * y.x - product) + x.x * y.y + x.y * y.x) + x.y * y.y;
    return vec2(product, error);
}

vec2 pixel_distance(ivec2 pixel_delta, vec2 fraction, vec2 fraction_low) {
    vec2 x = sum_with_error(float(pixel_delta.x), -fraction.x);
    vec2 y = sum_with_error(float(pixel_delta.y), -fraction.y);
    x.y -= fraction_low.x;
    y.y -= fraction_low.y;
    vec2 xx = product_with_error(x.x, x.x);
    vec2 yy = product_with_error(y.x, y.x);
    vec2 squared = sum_with_error(xx.x, yy.x);
    squared.y += xx.y + yy.y + 2.0 * (x.x * x.y + y.x * y.y);
    // The high component can cancel at the pixel nearest the centre.
    squared = sum_with_error(squared.x, squared.y);
    float root = sqrt(squared.x);
    if (root == 0.0) return vec2(0.0);
    vec2 square = product_with_error(root, root);
    return vec2(root, ((squared.x - square.x) + (squared.y - square.y)) / (2.0 * root));
}

float ripple_lens(vec2 distance, float radius, float radius_low) {
    float remaining = ((radius - distance.x) + (radius_low - distance.y)) / radius;
    return remaining > 0.0 ? (distance.x / radius) * sqrt(remaining) : 0.0;
}

vec2 ripple_offset(ivec2 pixel) {
    vec2 offset = vec2(0.0);
    for (int index = 0; index < ripple_count; ++index) {
        vec4 drop = ripple_sources[index];
        // Keep directions stable even when a drop falls almost on a pixel.
        ivec2 pixel_delta = pixel - ivec2(drop.xy);
        vec2 fraction = ripple_centre_fractions[index];
        vec2 delta = vec2(pixel_delta) - fraction;
#ifdef COMPENSATE
        vec2 distance_parts = pixel_distance(pixel_delta, fraction, ripple_centre_fractions_low[index]);
#else
        vec2 distance_parts = vec2(length(delta), 0.0);
#endif
        float distance = distance_parts.x;
        if (distance <= 1e-12) continue;
        float magnitude;
        if (ripple_variant == RIPPLE_DROPLETS) {
            magnitude = ripple_lens(distance_parts, drop.z, ripple_radius_low[index]) * drop.w;
        } else {
            float relative = (distance - drop.w) + (distance_parts.y - ripple_front_low[index]);
            float r = relative / ripple_sigma;
            // Beyond eight sigmas the packet contributes less than 1e-13
            // output pixels even at the maximum authored displacement.
            magnitude = abs(r) > 8.0 ? 0.0 : sin(TAU * (relative / wavelength.x)) * exp(-0.5 * r * r);
            if (ripple_variant == RIPPLE_STORM) {
                magnitude += 0.35 * ripple_lens(distance_parts, drop.z, ripple_radius_low[index]);
            }
        }
        offset += delta / distance * magnitude;
    }
    return clamp(offset / ripple_normalizer, -1.0, 1.0);
}

vec2 inverse_coordinate(ivec2 pixel, vec2 size) {
    vec2 p = vec2(pixel);
    if (kind == WARP_RAINDROP_RIPPLES) {
        return p - amplitude * ripple_offset(pixel);
    }
    if (kind == WARP_DISPLACEMENT) {
        vec4 control = roundEven(texelFetch(map_tex, pixel, 0) * 255.0);
        float coverage = control.a / 255.0;
        if (invert_map != 0) control = 255.0 - control;
        vec2 displacement = vec2(map_offset(map_value(control, map_channels.x), coverage),
                                 map_offset(map_value(control, map_channels.y), coverage));
        return p - amplitude * (invert_map == 0 ? displacement : -displacement);
    }
    if (kind == WARP_WAVE) {
        ivec2 cell;
        uvec2 fraction;
        pixel_cycles(uvec2(pixel), cell, fraction);
        // Stay inside the range where the driver sine needs no large-angle reduction.
        vec2 turns = unit_fraction(fraction.yx) + wave_turns;
        return p - amplitude * sin(TAU * (turns - roundEven(turns)));
    }
    if (kind == WARP_TURBULENCE) {
        vec2 offset = vec2(0.0);
        float total = 0.0;
        ivec2 cell;
        uvec2 fraction;
        pixel_cycles(uvec2(pixel), cell, fraction);
        for (int octave = 0; octave < octaves; ++octave) {
            float weight = 1.0 / float(1 << octave);
            int noise_seed = seed + octave * 17;
            offset += weight * vec2(noise_at(cell, fraction, noise_cell, noise_fraction, noise_seed),
                                    noise_at(cell, fraction, shifted_noise_cell, shifted_noise_fraction,
                                             noise_seed + 101));
            total += weight;
            cell = cell * 2 + ivec2(fraction >> 31u);
            fraction <<= 1u;
        }
        return p - amplitude * offset / total;
    }
    if (kind == WARP_POLAR_WRAP) {
        // Subtract the integer origin before its fractional part, preserving
        // the direction at a pixel arbitrarily close to the polar centre.
        vec2 delta = vec2(pixel - origin_pixel) - origin_fraction;
#ifdef COMPENSATE
        vec2 distance = pixel_distance(pixel - origin_pixel, origin_fraction, origin_fraction_low);
        vec2 scaled = product_with_error(distance.x, wrap_radius_scale.x);
        scaled.y += distance.x * wrap_radius_scale.y + distance.y * wrap_radius_scale.x;
#else
        vec2 scaled = vec2(length(delta) * wrap_radius_scale.x, 0.0);
#endif
        // Reduce the large radial coordinate before adding the subpixel part.
        // Repeat and mirror only need the coordinate inside their border period.
        float row = scaled.x;
        if (border == 2 || border == 3) {
            int period = int(size.y) * (border == 2 ? 1 : 2);
            row = float(int(row) % period) + fract(row);
        }
        return vec2(polar_column(pixel, delta, size.x), row + (scaled.y - 0.5));
    }
    if (kind == WARP_RADIAL) {
        // An offset smaller than a float pixel can still sample far from the
        // centre when a negative amount gives the inverse map a steep slope.
        vec2 delta = vec2(pixel - origin_pixel) - origin_fraction;
        float radius = length(delta);
        if (radius == 0.0 || radius >= radius_limit) return p;
        float mapped = radius_limit * pow(radius / radius_limit, radial_power);
        return p + (delta / radius) * (mapped - radius);
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
    vec2 at = inverse_coordinate(pixel, vec2(size));
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
SPATIAL_WARP_PRECISE = SPATIAL_WARP.replace(
    '#version 330\n',
    f'#version 330\n#extension {PRECISE_SPATIAL_EXTENSION} : require\n'
    '#define COMPENSATE\n#define PRECISE precise\n',
    1,
)
SPATIAL_WARP = SPATIAL_WARP.replace('#version 330\n', '#version 330\n#define PRECISE\n', 1)


def spatial_shader(config):
    if spatial_requires_precise_gpu(config):
        return SPATIAL_SHADER_NAME + '_precise', SPATIAL_WARP_PRECISE
    return SPATIAL_SHADER_NAME, SPATIAL_WARP


def require_precise_spatial_shader(backend):
    """Compile the optional variant before starting a frame's texture work."""
    from .effect_graph_gpu import GPUUnsupported

    try:
        backend._get_shader(SPATIAL_SHADER_NAME + '_precise', SPATIAL_WARP_PRECISE)
    except backend._moderngl.Error as exc:
        raise GPUUnsupported('precise spatial warp shader failed to compile or link') from exc


def _fixed_noise_origin(x, y):
    values = tuple(round(value * (1 << 32)) for value in (x, y))
    return (tuple(value >> 32 for value in values),
            tuple(value & 0xffffffff for value in values))


def _float_parts(value):
    high = unpack('f', pack('f', value))[0]
    return high, value - high


def _polar_seam(size, centre, angle):
    """Pack the CPU side of the polar cut for one pixel per scanline."""
    import numpy as np

    direction = (cos(angle), sin(angle))
    axis = int(abs(direction[1]) > abs(direction[0]))
    other = 1 - axis
    slope = direction[other] / direction[axis]
    major = np.arange(size[axis], dtype=np.float64) - centre[axis]
    minor = np.rint(centre[other] + slope * major) - centre[other]
    dx, dy = (major, minor) if axis == 0 else (minor, major)
    last_side = np.mod(np.arctan2(dy, dx) - angle, tau) >= tau * .5
    # One bit per scanline at the 16,384-pixel canvas limit. Fill uvec4s in
    # logical low-to-high bit order regardless of the machine's endianness.
    packed = np.packbits(last_side, bitorder='little').tobytes().ljust((MAX_DIMENSION + 127) // 128 * 16, b'\0')
    mask = [unpack('<4I', packed[i:i + 16]) for i in range(0, len(packed), 16)]
    return {'wrap_seam_axis': axis, 'wrap_seam_slope': slope, 'wrap_seam_mask': mask}


def spatial_uniforms(config, size):
    def value(name):
        return config.get(name, WARP_DEFAULTS[name])

    kind = value('warp_type')
    compensate = spatial_requires_precise_gpu(config)
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
        'octaves': value('warp_octaves'),
        'seed': value('warp_seed'),
        'segments': value('warp_segments'),
        'mirror_axis': (int('x' in axis), int('y' in axis)),
        'map_channels': (channels.index(value('warp_map_x')), channels.index(value('warp_map_y'))),
    }
    if kind in {'wave', 'turbulence'}:
        steps = tuple(round((1 << 48) / scale) for scale in value('warp_scale'))
        for index, name in enumerate(('cycles_low', 'cycles_mid', 'cycles_high', 'cycles_whole')):
            uniforms[name] = tuple((step >> (index * 16)) & 0xffff for step in steps)
        if kind == 'turbulence':
            y_phase = phase * .61803398875
            uniforms['noise_cell'], uniforms['noise_fraction'] = _fixed_noise_origin(phase, y_phase)
            uniforms['shifted_noise_cell'], uniforms['shifted_noise_fraction'] = _fixed_noise_origin(
                phase + 31.7, y_phase - 19.3)
        else:
            uniforms['wave_turns'] = remainder(phase, tau) / tau
    if kind == 'displacement':
        neutral = value('warp_neutral')
        # Work from exact channel bytes. Near white, measure distance from
        # white before subtracting the neutral threshold or dividing by it.
        uniforms['invert_map'] = int(neutral > .5)
        uniforms['map_neutral'] = min(neutral, 1 - neutral) * 255
    if kind in {'polar_wrap', 'radial'} and size is not None:
        centre = tuple(coordinate * extent - .5 for coordinate, extent in zip(value('warp_center'), size))
        pixels = tuple(round(coordinate) for coordinate in centre)
        fractions = [_float_parts(coordinate - pixel) for coordinate, pixel in zip(centre, pixels)]
        high, low = zip(*fractions)
        uniforms.update(origin_pixel=pixels, origin_fraction=high, origin_fraction_low=low)
        if kind == 'polar_wrap':
            uniforms['wrap_radius_scale'] = _float_parts(size[1] / value('warp_radius'))
            angle = radians(value('warp_angle'))
            turns = remainder(angle, tau) / tau
            uniforms['wrap_angle_turns'] = _float_parts(turns)
            if value('warp_border') != 'repeat':
                uniforms.update(_polar_seam(size, centre, angle))
    if kind == 'raindrop_ripples' and size is not None:
        variant = value('warp_ripple_variant')
        field = prepare_ripple_field(
            size, variant=variant, centre=value('warp_center'), radius=value('warp_radius'),
            wavelength=value('warp_scale')[0], phase=phase, count=value('warp_segments'),
            seed=value('warp_seed'),
        )
        sources, fractions, fractions_low, fronts_low, radii_low = [], [], [], [], []
        for source in field.sources:
            x, y = round(source.x), round(source.y)
            front, front_low = _float_parts(source.front) if compensate else (source.front, 0)
            radius, radius_low = _float_parts(source.radius) if compensate else (source.radius, 0)
            sources.append((x, y, radius, source.pulse if variant == 'droplets' else front))
            x_high, x_low = _float_parts(source.x - x) if compensate else (source.x - x, 0)
            y_high, y_low = _float_parts(source.y - y) if compensate else (source.y - y, 0)
            fractions.append((x_high, y_high))
            fractions_low.append((x_low, y_low))
            fronts_low.append(front_low)
            radii_low.append(radius_low)
        unused = MAX_RIPPLE_SOURCES - len(sources)
        uniforms.update(
            ripple_variant=RIPPLE_VARIANT_ORDER.index(variant),
            ripple_count=len(sources),
            ripple_sources=tuple(sources) + ((0, 0, 0, 0),) * unused,
            ripple_centre_fractions=tuple(fractions) + ((0, 0),) * unused,
            ripple_centre_fractions_low=tuple(fractions_low) + ((0, 0),) * unused,
            ripple_front_low=tuple(fronts_low) + (0,) * unused,
            ripple_radius_low=tuple(radii_low) + (0,) * unused,
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

    req = gpu_requirements('spatial_warp', config, len(images), original_alpha=original_alpha is not None,
                           precise_gpu=backend.supports_precise_spatial_warps)
    if not req.supported:
        raise GPUUnsupported(f"spatial_warp {config.get('warp_type', 'wave')}: {req.reason}")
    image = images[0]
    control_map = images[1] if len(images) == 2 else None
    validate_spatial_images(image, map_image=control_map, original_alpha=original_alpha)
    if req.reason == 'exact_host_identity' or spatial_map_is_transparent(control_map):
        return image.copy(), original_alpha.copy() if original_alpha is not None else None

    if spatial_requires_precise_gpu(config):
        require_precise_spatial_shader(backend)
    uniforms = spatial_uniforms(config, image.size)
    shader = spatial_shader(config)

    def warp(source, *, alpha_only):
        def steps(_textures):
            yield *shader, *spatial_draw_inputs(
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
