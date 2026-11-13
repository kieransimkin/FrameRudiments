// FrameRudiments original, distributed under the repository MIT licence.
vec4 transition(vec2 uv) {
    vec2 halfExtent = vec2(ratio, 1.0) * 0.5;
    float distanceFromCentre = length((uv - 0.5) * vec2(ratio, 1.0));
    float feather = 0.012;
    float radius = mix(-feather, length(halfExtent) + feather, progress);
    float coverage = 1.0 - smoothstep(radius - feather, radius + feather, distanceFromCentre);
    vec4 fromColour = getFromColor(uv);
    vec4 toColour = getToColor(uv);
    if (coverage <= 0.0) return fromColour;
    if (coverage >= 1.0) return toColour;
    float alpha = mix(fromColour.a, toColour.a, coverage);
    vec3 premultiplied = mix(fromColour.rgb * fromColour.a, toColour.rgb * toColour.a, coverage);
    return vec4(alpha > 0.0 ? premultiplied / alpha : vec3(0.0), alpha);
}
