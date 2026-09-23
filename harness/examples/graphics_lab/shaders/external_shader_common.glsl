// Reusable source fragment, explicitly joined before external_shader.frag.
// A compact direct GGX lobe and authored studio environment. The colour phase
// is an artistic thin-film accent, not a multilayer optical solver.
const float ALLOY_PI = 3.141592653589793;

float alloyHash(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.yzx + 33.33);
    return fract((p.x + p.y) * p.z);
}

float alloyNoise(vec3 p) {
    vec3 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(alloyHash(i), alloyHash(i + vec3(1, 0, 0)), f.x),
                   mix(alloyHash(i + vec3(0, 1, 0)), alloyHash(i + vec3(1, 1, 0)), f.x), f.y),
               mix(mix(alloyHash(i + vec3(0, 0, 1)), alloyHash(i + vec3(1, 0, 1)), f.x),
                   mix(alloyHash(i + vec3(0, 1, 1)), alloyHash(i + vec3(1)), f.x), f.y), f.z);
}

vec3 alloyFresnel(vec3 f0, float cosine) {
    float x = clamp(1.0 - cosine, 0.0, 1.0);
    float x2 = x * x;
    return f0 + (1.0 - f0) * x2 * x2 * x;
}

vec3 alloyGgx(vec3 n, vec3 v, vec3 l, vec3 f0, float roughness, vec3 radiance) {
    vec3 halfVector = v + l;
    vec3 h = halfVector / max(length(halfVector), 0.0001);
    float nv = max(dot(n, v), 0.0001), nl = max(dot(n, l), 0.0);
    float nh = max(dot(n, h), 0.0), vh = max(dot(v, h), 0.0);
    float alpha = roughness * roughness, a2 = alpha * alpha;
    float denominator = nh * nh * (a2 - 1.0) + 1.0;
    float distribution = a2 / max(ALLOY_PI * denominator * denominator, 0.00001);
    float visibility = 0.5 / max(nl * sqrt(nv * nv * (1.0 - a2) + a2)
                               + nv * sqrt(nl * nl * (1.0 - a2) + a2), 0.0001);
    return radiance * nl * distribution * visibility * alloyFresnel(f0, vh);
}

float alloySoftbox(vec3 r, vec3 center, vec3 across, vec2 halfSize, float blur) {
    vec3 axis = normalize(center);
    vec3 right = normalize(cross(axis, across));
    vec3 up = cross(right, axis);
    float facing = dot(r, axis);
    vec2 uv = vec2(dot(r, right), dot(r, up)) / max(facing, 0.001);
    vec2 edge = abs(uv) - halfSize;
    float border = max(edge.x, edge.y);
    return (1.0 - smoothstep(-blur, blur, border)) * smoothstep(0.0, 0.2, facing);
}

vec3 alloyStudio(vec3 r, float roughness) {
    vec3 colour = mix(vec3(0.11, 0.12, 0.14), vec3(0.34, 0.39, 0.46),
                      smoothstep(-0.2, 0.8, r.y));
    float blur = 0.065 + roughness * roughness * 1.2;
    colour += vec3(2.8, 2.4, 1.9) * alloySoftbox(r, vec3(-3, 4, 3),
                      vec3(0, 1, 0), vec2(0.22, 0.78), blur);
    colour += vec3(1.0, 1.55, 2.4) * alloySoftbox(r, vec3(4, 2, -3),
                      vec3(0, 1, 0), vec2(0.12, 0.9), blur);
    colour += vec3(0.9) * alloySoftbox(r, vec3(0, 5, -1),
                      vec3(1, 0, 0), vec2(0.8, 0.14), blur);
    return colour;
}
