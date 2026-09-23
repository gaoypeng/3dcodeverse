# glsl_shader authoring contract (Shadertoy-style fragment shader, GLSL 330 core)

You write ONE fragment shader body; the harness wraps it, compiles it with moderngl (OpenGL 3.3 core,
headless), renders frames at the plan's resolution for t = 0, 1, 2.5, 4, 6 s (plus a preview GIF) and
measures / judges the frames.  The code is the deliverable.

## Files
```
src/shader.frag     REQUIRED  the image pass (your code; no #version, no uniform/out declarations)
src/common.glsl     optional  helper functions / constants, pasted ABOVE shader.frag (and buffer_a.frag) automatically
src/buffer_a.frag   optional  ONE feedback buffer pass (Shadertoy "Buffer A"): rendered every frame before the image pass
src/recipes.glsl    HARNESS-OWNED, READ-ONLY  verified cookbook recipes the harness seeded for this brief, pasted ABOVE
                    common.glsl: call its functions, never edit, redefine or copy them (a write is reverted and fails the session)
```
Nothing else is read.  No textures, no files, no includes.

## What the harness prepends (DO NOT write these lines yourself — redeclaring them is a lint ERROR)
```glsl
#version 330 core
uniform float u_time;         // seconds (frames sampled at 0, 1, 2.5, 4, 6 s; preview up to duration_s)
uniform vec2  u_resolution;   // pixels, e.g. 1280x720
uniform vec2  u_mouse;        // always (0,0) headless — do not depend on it
uniform int   u_frame;        // frame counter
uniform sampler2D u_prev;     // previous frame of THIS pass (feedback / trails); black at frame 0
uniform sampler2D u_noise;    // 256x256 RGBA white noise, repeat, linear (texture(u_noise, uv*k).r)
uniform sampler2D u_buffer_a; // output of src/buffer_a.frag (only when that file exists)
#define iTime u_time          // Shadertoy aliases: iTime iResolution iFrame iMouse iChannel0(=u_prev) iChannel1(=u_noise)
out vec4 fragColor;           // the ONLY output
```
NOT available: iChannel2/3, iDate, iSampleRate, iChannelResolution, textures/images/videos/sound.

## Entry point (one of the two; the harness detects which)
```glsl
void mainImage(out vec4 fragColor, in vec2 fragCoord) { ...; fragColor = vec4(col, 1.0); }   // preferred
// or:  void main() { ...; fragColor = vec4(col, 1.0); }   using gl_FragCoord.xy
```
`fragCoord` / `gl_FragCoord.xy` are PIXEL coordinates with origin BOTTOM-LEFT (y up).  Use
`vec2 p = (fragCoord - 0.5*u_resolution) / u_resolution.y;` for aspect-correct centred coords (|y| ≤ 0.5).
`uv = fragCoord / u_resolution` for 0..1 (also the coordinate to sample u_prev / u_buffer_a).

## Rules (lint + gates enforce them)
* GLSL 330 core only: `texture()` not `texture2D`, `in/out` not `varying`, `fragColor` not `gl_FragColor`,
  `mod(x,y)` not `%` on floats, constant loop bounds, `float` literals with a decimal point when in doubt.
* Do not declare uniforms, `out` variables, `#version`, `precision`, `#include`.
* Everything must MOVE with `u_time` (unless the brief says "static"): frames at t=0 and t=1 must differ.
  A static result caps the score at 0.5; NaN/Inf pixels cap it at 0; all-black / blown-out frames cap at 0.5.
* Guard the maths: `x / max(d, 1e-4)`, `sqrt(max(x, 0.0))`, `normalize()` only of non-zero vectors,
  `clamp()` acos/asin inputs, `pow(max(x,0.0), k)`.
* Keep loop bounds constant and small.
* Feedback (`u_prev`): sample at `uv` (optionally offset / scaled), mix with the new frame, and DECAY it
  (`prev * 0.9`) — unbounded accumulation blows out.  buffer_a.frag reads its own previous frame as `u_prev`;
  shader.frag reads the buffer as `u_buffer_a` and its own previous frame as `u_prev`.
* Deterministic: no randomness that changes per frame other than as a function of u_time / u_frame.

## Build errors come back as `src/shader.frag:LINE: error: …` (line numbers refer to YOUR file).
Common messages: "`x' undeclared" (typo / missing helper), "no matching function" (int vs float args →
add `.0`), "too few components" (vec4(col) where col is vec3 → vec4(col, 1.0)), "redeclaration" (you declared
a harness uniform — delete it).

## COMPLETE VERIFIED EXAMPLE — `src/shader.frag` (fbm clouds, drifting, sun on an arc; renders clean)
```glsl
// src/shader.frag — "Afternoon cumulus": fbm clouds drifting over a gradient sky with a warm sun.
// Harness provides: u_time, u_resolution, u_mouse, u_frame, u_prev, u_noise, u_buffer_a, out vec4 fragColor.
const vec3 SKY_TOP    = vec3(0.10, 0.32, 0.78);
const vec3 SKY_HORIZ  = vec3(0.62, 0.78, 0.95);
const vec3 SUN_COL    = vec3(1.00, 0.92, 0.75);
const vec3 CLOUD_LIT  = vec3(1.00, 0.98, 0.95);
const vec3 CLOUD_SHAD = vec3(0.42, 0.50, 0.68);

float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1, 0)), u.x), mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {                       // 6 octaves, constant bound
    float v = 0.0, a = 0.5;
    mat2 m = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 6; i++) { v += a * noise(p); p = m * p * 2.03 + 0.31; a *= 0.5; }
    return v;
}
// cloud density in 0..1 at screen point p (y up), layer k selects parallax speed / scale
float cloudLayer(vec2 p, float k, float t) {
    vec2 q = p * (2.2 + 0.8 * k) + vec2(t * (0.04 + 0.03 * k), k * 7.3);
    float d = fbm(q + 0.35 * fbm(q * 1.7 - t * 0.02));      // domain warp for billowy edges
    return smoothstep(0.50, 0.70, d);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 p = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;   // aspect-correct, centred, y up
    float t = u_time;
    // sky gradient
    vec3 col = mix(SKY_HORIZ, SKY_TOP, smoothstep(-0.35, 0.55, p.y));
    // sun: slow arc; disc + soft glow
    vec2 sunPos = vec2(0.45 * cos(0.12 * t + 2.6), 0.18 + 0.12 * sin(0.12 * t + 2.6));
    float sd = length(p - sunPos);
    col += SUN_COL * (smoothstep(0.05, 0.035, sd) + 0.35 * exp(-8.0 * sd) + 0.08 * exp(-2.0 * sd));
    // two cloud layers, far (dim, slow) then near (bright, fast), lit from the sun side
    for (int k = 0; k < 2; k++) {
        float fk = float(k);
        float dens = cloudLayer(p, fk, t);
        float lit = cloudLayer(p + 0.03 * normalize(sunPos - p + 1e-4), fk, t);     // thinner towards the sun = lit
        vec3 cloudCol = mix(CLOUD_SHAD, CLOUD_LIT, clamp(0.9 - 2.5 * (lit - dens) - 0.25 * fk, 0.0, 1.0));
        col = mix(col, cloudCol, dens * (0.55 + 0.45 * fk));
    }
    // haze near the horizon + gentle vignette (colours above are already display-space: just clamp)
    col = mix(col, SKY_HORIZ, 0.25 * smoothstep(0.1, -0.5, p.y));
    col *= 1.0 - 0.20 * dot(p, p);
    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
```
