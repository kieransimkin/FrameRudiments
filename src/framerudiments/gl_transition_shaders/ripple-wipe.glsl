// FrameRudiments original, distributed under the repository MIT licence.
// time is the FrameRudiments output-seconds uniform.
vec4 transition(vec2 uv) {
    float displacement = 0.12 * sin(6.283185307 * (uv.y + fract(0.75 * time)));
    float front = mix(-0.14, 1.14, progress) + displacement;
    float coverage = 1.0 - smoothstep(front - 0.012, front + 0.012, uv.x);
    vec4 fromColour = getFromColor(uv);
    vec4 toColour = getToColor(uv);
    if (coverage <= 0.0) return fromColour;
    if (coverage >= 1.0) return toColour;
    float alpha = mix(fromColour.a, toColour.a, coverage);
    vec3 premultiplied = mix(fromColour.rgb * fromColour.a, toColour.rgb * toColour.a, coverage);
    return vec4(alpha > 0.0 ? premultiplied / alpha : vec3(0.0), alpha);
}
