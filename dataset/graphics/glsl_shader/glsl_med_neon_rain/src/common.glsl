// src/common.glsl — Cyberpunk rain window helper functions & constants
#define PI 3.14159265359
#define TAU 6.28318530718

// Cyberpunk Noir Palette Constants
const vec3 BASE_OBSIDIAN = vec3(0.02, 0.03, 0.07);
const vec3 NEON_CYAN     = vec3(0.05, 0.90, 1.00);
const vec3 NEON_MAGENTA  = vec3(1.00, 0.08, 0.55);
const vec3 NEON_AMBER    = vec3(1.00, 0.65, 0.15);
const vec3 NEON_VIOLET   = vec3(0.45, 0.12, 0.95);
const vec3 LIGHTNING_COL = vec3(0.85, 0.95, 1.00);

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

vec3 hash32(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yxz + 33.33);
    return fract((p3.xxy + p3.yzz) * p3.zyx);
}

// 2D Value noise
float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1.0, 0.0)), u.x),
               mix(hash12(i + vec2(0.0, 1.0)), hash12(i + vec2(1.0, 1.0)), u.x), u.y);
}

// 4-octave FBM
float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    mat2 rot = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 4; i++) {
        v += a * noise(p);
        p = rot * p * 2.02 + 0.15;
        a *= 0.5;
    }
    return v;
}

// ACES Tone Mapping
vec3 tonemapACES(vec3 x) {
    const float a = 2.51;
    const float b = 0.03;
    const float c = 2.43;
    const float d = 0.59;
    const float e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

// Gamma correction
vec3 gamma(vec3 col) {
    return pow(max(col, vec3(0.0)), vec3(1.0 / 2.2));
}
