// Original FrameRudiments M9.5 synthetic fixture, MIT. Host-only time and pixel coordinates.
uniform float TIME;
uniform int FRAMEINDEX;
vec4 transition(vec2 uv) {
    float reveal = step(0.5, uv.x) * step(1.0, TIME) * step(1.0, float(FRAMEINDEX));
    return mix(getFromColor(uv), getToColor(uv), reveal);
}
