// Original FrameRudiments M9.3 fixture (MIT). Reblend previous pass toward B.
uniform float progress;
vec4 transition(vec2 uv) {
    return mix(getFromColor(uv), getToColor(uv), progress);
}
