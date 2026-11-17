// Original FrameRudiments test shader, released under the repository's MIT licence.
// GL Transitions single-pass ABI. The host supplies all declared uniforms.
uniform float progress;
uniform float TIME;
uniform float ratio;
vec4 transition(vec2 uv) {
    // Includes all supported host conventions without introducing external resources.
    float phase = sin(TIME * 0.0 + ratio * 0.0);
    return mix(getFromColor(uv), getToColor(uv), progress + phase);
}
