// src/common.glsl — Cosmic flythrough math & noise helpers
#define PI  3.14159265359
#define TAU 6.28318530718

// Hash functions
float hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec2 hash22(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.xx + p3.yz) * p3.zy);
}

float hash31(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.yzx + 33.33);
    return fract((p.x + p.y) * p.z);
}

vec3 hash33(vec3 p) {
    p = fract(p * vec3(0.1031, 0.1030, 0.0973));
    p += dot(p, p.yxz + 33.33);
    return fract((p.xxy + p.yxx) * p.zyx);
}

// 2D rotation matrix
mat2 rot2(float a) {
    float c = cos(a), s = sin(a);
    return mat2(c, -s, s, c);
}

// 3D smooth value noise
float noise3D(vec3 p) {
    vec3 i = floor(p);
    vec3 f = fract(p);
    vec3 u = f * f * (3.0 - 2.0 * f);

    float n000 = hash31(i + vec3(0.0, 0.0, 0.0));
    float n100 = hash31(i + vec3(1.0, 0.0, 0.0));
    float n010 = hash31(i + vec3(0.0, 1.0, 0.0));
    float n110 = hash31(i + vec3(1.0, 1.0, 0.0));
    float n001 = hash31(i + vec3(0.0, 0.0, 1.0));
    float n101 = hash31(i + vec3(1.0, 0.0, 1.0));
    float n011 = hash31(i + vec3(0.0, 1.0, 1.0));
    float n111 = hash31(i + vec3(1.0, 1.0, 1.0));

    return mix(
        mix(mix(n000, n100, u.x), mix(n010, n110, u.x), u.y),
        mix(mix(n001, n101, u.x), mix(n011, n111, u.x), u.y),
        u.z
    );
}

// 3D Fractal Brownian Motion with rotation per octave
float fbm3D(vec3 p) {
    float v = 0.0;
    float a = 0.52;
    for (int i = 0; i < 4; i++) {
        v += a * noise3D(p);
        p = p * 2.08;
        p.xy = rot2(0.55) * p.xy;
        p.yz = rot2(0.42) * p.yz;
        p += vec3(0.31, 0.47, 0.19);
        a *= 0.48;
    }
    return v;
}

// Domain-warped 3D density for organic cosmic gas tendrils
float nebulaDensity(vec3 p, float t) {
    vec3 q = p;
    q.xy = rot2(0.06 * t + p.z * 0.12) * q.xy;
    
    // First warp layer
    vec3 warp1 = vec3(
        fbm3D(q * 0.75 + vec3(0.0, 0.0, t * 0.04)),
        fbm3D(q * 0.75 + vec3(4.3, 1.7, -t * 0.03)),
        fbm3D(q * 0.75 + vec3(2.1, 5.8, t * 0.02))
    );
    
    // Second warp layer
    vec3 warp2 = vec3(
        fbm3D(q + 1.2 * warp1 + vec3(1.7, 9.2, 0.05 * t)),
        fbm3D(q + 1.2 * warp1 + vec3(8.3, 2.8, -0.04 * t)),
        fbm3D(q + 1.2 * warp1 + vec3(3.5, 6.1, 0.03 * t))
    );

    float d = fbm3D(q * 0.9 + 1.6 * warp2);
    
    // Carve a central channel so the camera flies cleanly through glowing corridors
    float coreDist = length(p.xy);
    float tunnel = smoothstep(0.1, 1.8, coreDist);
    
    d = smoothstep(0.38, 0.85, d) * tunnel;
    return d;
}

// ACES Filmic Tone Mapping Curve
vec3 tonemapACES(vec3 x) {
    const float a = 2.51;
    const float b = 0.03;
    const float c = 2.43;
    const float d = 0.59;
    const float e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}
