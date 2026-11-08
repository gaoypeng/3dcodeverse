# `src/shader.frag` — the names in use (lints clean, builds, moves; what it draws is not a suggestion)

```glsl
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 p = (fragCoord - 0.5 * u_resolution) / u_resolution.y;    // centred, y up, circles stay round
    float n = texture(u_noise, p * 0.5 + 0.02 * u_time).r;          // the noise texture as a hash
    float d = length(p - vec2(0.3 * cos(u_time), 0.0));
    vec3 col = vec3(0.2, 0.3, 0.5) * (0.5 + 0.5 * n) + vec3(1.0, 0.8, 0.5) * exp(-8.0 * d);
    fragColor = vec4(col, 1.0);
}
```
