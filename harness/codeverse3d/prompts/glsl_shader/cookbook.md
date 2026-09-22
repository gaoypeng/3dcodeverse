# glsl_shader cookbook — copyable snippets (GLSL 330 core, harness uniforms u_time / u_resolution / u_prev / u_noise)

All snippets assume `vec2 p = (fragCoord - 0.5*u_resolution) / u_resolution.y;` (centred, aspect-correct,
y up) and `vec2 uv = fragCoord / u_resolution;` (0..1) unless stated.  Put helpers in `src/common.glsl`.

## Hash / noise / fbm
```glsl
float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash12(vec2 p)  { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
vec2  hash22(vec2 p)  { vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.xx + p3.yz) * p3.zy); }
vec3  hash33(vec3 p)  { p = fract(p * vec3(0.1031, 0.1030, 0.0973)); p += dot(p, p.yxz + 33.33); return fract((p.xxy + p.yxx) * p.zyx); }
float noise(vec2 p) {                       // value noise 0..1, smooth
    vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1, 0)), u.x), mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), u.x), u.y);
}
float noise3(vec3 p) {                      // 3D value noise (volumes, time as z)
    vec3 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
    float n000 = hash33(i).x, n100 = hash33(i + vec3(1,0,0)).x, n010 = hash33(i + vec3(0,1,0)).x, n110 = hash33(i + vec3(1,1,0)).x;
    float n001 = hash33(i + vec3(0,0,1)).x, n101 = hash33(i + vec3(1,0,1)).x, n011 = hash33(i + vec3(0,1,1)).x, n111 = hash33(i + vec3(1,1,1)).x;
    return mix(mix(mix(n000, n100, f.x), mix(n010, n110, f.x), f.y), mix(mix(n001, n101, f.x), mix(n011, n111, f.x), f.y), f.z);
}
float fbm(vec2 p) {                         // 6 octaves, constant bound, rotated per octave
    float v = 0.0, a = 0.5; mat2 m = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 6; i++) { v += a * noise(p); p = m * p * 2.03 + 0.31; a *= 0.5; }
    return v;
}
float ridged(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * (1.0 - abs(2.0 * noise(p) - 1.0)); p *= 2.1; a *= 0.5; } return v; }
// cheap texture noise (GPU-fast): float n = texture(u_noise, p * 0.37 + u_time * 0.01).r;
```

## Domain warping (billowy clouds, smoke, marble)
```glsl
float warped(vec2 p, float t) {
    vec2 q = vec2(fbm(p + vec2(0.0, t * 0.05)), fbm(p + vec2(5.2, 1.3)));
    vec2 r = vec2(fbm(p + 4.0 * q + vec2(1.7, 9.2) + 0.15 * t), fbm(p + 4.0 * q + vec2(8.3, 2.8)));
    return fbm(p + 4.0 * r);
}
```

## Palettes, tonemapping, grading
```glsl
vec3 palette(float t, vec3 a, vec3 b, vec3 c, vec3 d) { return a + b * cos(6.28318 * (c * t + d)); }
// e.g. neon:  palette(t, vec3(0.5), vec3(0.5), vec3(1.0), vec3(0.0, 0.33, 0.67))
// sunset:     palette(t, vec3(0.5,0.4,0.3), vec3(0.5,0.4,0.3), vec3(1.0), vec3(0.0,0.1,0.2))
vec3 tonemapACES(vec3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }
vec3 gamma(vec3 c) { return pow(max(c, 0.0), vec3(1.0 / 2.2)); }
// final line for HDR-ish accumulations:  fragColor = vec4(gamma(tonemapACES(col)), 1.0);
// vignette:  col *= 1.0 - 0.35 * dot(p, p);      // dithering against banding:  col += (hash12(fragCoord) - 0.5) / 255.0;
```

## SDF 2D shapes + anti-aliased edges
```glsl
float sdCircle(vec2 p, float r) { return length(p) - r; }
float sdBox(vec2 p, vec2 b) { vec2 d = abs(p) - b; return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0); }
float sdSegment(vec2 p, vec2 a, vec2 b) { vec2 pa = p - a, ba = b - a; float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0); return length(pa - ba * h); }
float sdStar(vec2 p, float r, float n) { float a = atan(p.y, p.x), s = 6.28318 / n; a = mod(a + s * 0.5, s) - s * 0.5; return length(p) * cos(a) - r; }
float fill(float d) { return 1.0 - smoothstep(0.0, 1.5 / u_resolution.y, d); }      // 1 px AA edge
float stroke(float d, float w) { return 1.0 - smoothstep(w, w + 1.5 / u_resolution.y, abs(d)); }
float glow(float d, float k) { return exp(-k * max(d, 0.0)); }
mat2 rot2(float a) { float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }
```

## Raymarching template (3D sdf scene with lighting, fog, sky)
```glsl
float sdSphere(vec3 p, float r) { return length(p) - r; }
float sdBox3(vec3 p, vec3 b) { vec3 q = abs(p) - b; return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0); }
float sdTorus(vec3 p, vec2 t) { vec2 q = vec2(length(p.xz) - t.x, p.y); return length(q) - t.y; }
float smin(float a, float b, float k) { float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0); return mix(b, a, h) - k * h * (1.0 - h); }
float map(vec3 p) {                                   // the scene: ground + bobbing blobs
    float ground = p.y + 1.0;
    vec3 q = p; q.xz = mod(q.xz + 2.0, 4.0) - 2.0;     // repetition
    float blob = sdSphere(q - vec3(0.0, 0.3 * sin(u_time + p.x), 0.0), 0.7);
    return smin(ground, blob, 0.5);
}
vec3 calcNormal(vec3 p) { vec2 e = vec2(1e-3, 0.0); return normalize(vec3(map(p + e.xyy) - map(p - e.xyy), map(p + e.yxy) - map(p - e.yxy), map(p + e.yyx) - map(p - e.yyx))); }
float softShadow(vec3 ro, vec3 rd) { float res = 1.0, t = 0.05; for (int i = 0; i < 32; i++) { float h = map(ro + rd * t); res = min(res, 10.0 * h / t); t += clamp(h, 0.02, 0.5); if (h < 1e-3 || t > 20.0) break; } return clamp(res, 0.0, 1.0); }
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 p = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;
    vec3 ro = vec3(3.0 * cos(0.2 * u_time), 1.0, 3.0 * sin(0.2 * u_time));     // orbiting camera
    vec3 ta = vec3(0.0, 0.0, 0.0);
    vec3 ww = normalize(ta - ro), uu = normalize(cross(ww, vec3(0, 1, 0))), vv = cross(uu, ww);
    vec3 rd = normalize(p.x * uu + p.y * vv + 1.6 * ww);
    float t = 0.0; bool hit = false;
    for (int i = 0; i < 96; i++) { float d = map(ro + rd * t); if (d < 1e-3) { hit = true; break; } t += d; if (t > 40.0) break; }
    vec3 sky = mix(vec3(0.9, 0.8, 0.7), vec3(0.2, 0.4, 0.8), smoothstep(-0.1, 0.5, rd.y));
    vec3 col = sky;
    if (hit) {
        vec3 pos = ro + rd * t, n = calcNormal(pos), lig = normalize(vec3(0.6, 0.8, 0.3));
        float dif = max(dot(n, lig), 0.0) * softShadow(pos + n * 0.01, lig), amb = 0.5 + 0.5 * n.y;
        vec3 mat = vec3(0.8, 0.5, 0.3);
        col = mat * (dif * vec3(1.0, 0.9, 0.7) + amb * vec3(0.2, 0.3, 0.45));
        col = mix(col, sky, 1.0 - exp(-0.02 * t * t));    // fog
    }
    fragColor = vec4(pow(col, vec3(0.4545)), 1.0);
}
```

## Gradient sky, sun, stars, water
```glsl
vec3 skyGrad(vec2 p) { return mix(vec3(0.95, 0.6, 0.35), vec3(0.1, 0.25, 0.6), smoothstep(-0.3, 0.6, p.y)); }
float sun(vec2 p, vec2 c, float r) { float d = length(p - c); return smoothstep(r, r * 0.8, d) + 0.3 * exp(-8.0 * d); }
// DENSE STARS: thousands of sub-pixel points, a few brighter; keep = fraction of cells that hold a star (0.2-0.3).
// Two layers (density 160 and 70) read as a real sky; twenty twinkling sparkles do not (that version cost a run).
float stars(vec2 p, float density, float keep) {
    vec2 g = floor(p * density), f = fract(p * density);
    float h = hash12(g); vec2 o = hash22(g) * 0.7 + 0.15;
    float d = length(f - o);                                  // cell units
    float size = 0.07 + 0.10 * step(0.97, h);                 // a few are bigger / brighter
    return step(1.0 - keep, h) * exp(-d * d / (size * size)) * (0.35 + 0.65 * hash12(g + 9.0));
}
// usage: col += vec3(0.9, 0.95, 1.0) * (stars(p, 160.0, 0.22) * 0.55 + stars(p + 3.7, 70.0, 0.25) * 0.9);
float waterHeight(vec2 xz, float t) { return 0.05 * sin(xz.x * 4.0 + t * 1.5) + 0.03 * sin(xz.y * 6.0 - t * 1.1) + 0.04 * noise(xz * 3.0 + t * 0.4); }
// water colour: mix(deep, shallow, fresnel) + specular: pow(max(dot(reflect(-lig, n), -rd), 0.0), 64.0)
```

## Rain / drops on glass (grid cells + trails, refracted background)
```glsl
vec2 dropsLayer(vec2 uv, float t, float scale) {          // returns (mask, trail)
    vec2 asp = vec2(2.0, 1.0); vec2 st = uv * scale * asp; vec2 id = floor(st);
    float n = hash12(id); t += n * 6.28;
    st.y += t * 0.25;  id = floor(st);  n = hash12(id);       // scroll cells downward
    vec2 f = fract(st) - 0.5;
    float w = sin(t + n * 6.28) * (0.5 - abs(f.y)) ; f.x += w * 0.3;   // wobble
    vec2 d = f * vec2(1.0, 2.0);
    float drop = smoothstep(0.1, 0.05, length(d));
    float trail = smoothstep(0.1, 0.0, abs(f.x)) * smoothstep(0.5, -0.1, f.y) * step(0.0, f.y) * 0.5;
    return vec2(drop, trail) * step(0.3, n);
}
// usage: vec2 dr = dropsLayer(uv, u_time, 8.0); vec2 off = dr.x * 0.03 * normalize(p + 1e-3); col = background(uv + off);
```

## Bokeh city lights (soft discs ADDED on a dark ground, depth layers, pulsing)
Soft gaussian discs added on a near-black ground with muted warm/cool tints — the hard `smoothstep` discs in
full-saturation `palette()` colours this recipe used to hold read as opaque candy and cost a run.
```glsl
// SOFT BOKEH: gaussian discs ADDED on a dark ground, dim, muted tints — not opaque smoothstep circles.
vec3 bokehSoft(vec2 p, float t) {
    vec3 acc = vec3(0.0);
    for (int layer = 0; layer < 3; layer++) {
        float fl = float(layer), scale = 4.0 + 3.0 * fl;
        vec2 q = p * scale + vec2(t * (0.02 + 0.01 * fl), 0.0);
        vec2 id = floor(q), f = fract(q) - 0.5;
        for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) {
            vec2 o = vec2(i, j); vec2 h = hash22(id + o); vec2 c = o + h - 0.5;
            float r = 0.12 + 0.12 * hash12(id + o + 7.0);
            float d = length(f - c);
            float disc = exp(-d * d / (r * r)) * smoothstep(r * 1.6, r * 0.6, d);   // soft core, soft rim
            disc *= 0.7 + 0.3 * sin(t * (0.5 + h.x) + h.y * 6.28);                     // gentle pulsing
            vec3 tint = mix(vec3(0.9, 0.6, 0.3), vec3(0.3, 0.6, 0.9), h.x) * (0.5 + 0.5 * h.y);   // warm/cool, muted
            acc += disc * tint * (0.28 + 0.18 * fl);                                    // many small adds on a dark ground, never a flat fill
        }
    }
    return acc;
}
```

## Light phenomena look like LIGHT, not paint (aurora, curtains, glow)
Three habits, each one cost a real run (2026-08-26, frames judged by eye): an aurora drawn as a comb of evenly
spaced vertical bars; bokeh as hard opaque candy discs; a "star field" of twenty sparkles.  Light has soft
falloff, sits on real darks, and is dense at fine scale.  Verified recipe (rendered and looked at):
```glsl
// ORGANIC CURTAIN (aurora, drapery, flame sheets): a ribbon whose LOWER EDGE wanders and folds, sharp below,
// fading upward, with fine vertical rays grouped in bundles and gaps where the ribbon thins out.  Never a comb.
// k = 0 at the lower edge .. 1 at the top (drive the green -> violet gradient with it).
float curtain(vec2 p, float t, float seed, out float k) {
    float xw = p.x + 0.55 * (fbm(vec2(p.x * 0.7 + seed, t * 0.05 + seed * 2.0)) - 0.5);   // horizontal folding (domain warp)
    float base = -0.08 + 0.11 * sin(xw * 2.3 + seed * 1.7 + t * 0.07)
               + 0.16 * fbm(vec2(xw * 2.0 + seed * 3.1, 0.7 + t * 0.06));                  // a long arc + a sinuous lower edge
    float h = 0.22 + 0.25 * fbm(vec2(xw * 0.6 + seed * 5.0, 2.0 + t * 0.03));               // how tall the curtain is here
    float d = p.y - base;  k = clamp(d / h, 0.0, 1.0);
    float rays = 0.45 + 0.55 * noise(vec2(xw * 55.0 + t * 0.9, seed));                      // fine vertical striations ...
    rays *= 0.55 + 0.45 * noise(vec2(xw * 12.0 - t * 0.25, seed + 3.0));                    // ... grouped into bundles
    float lower = smoothstep(-0.02 - 0.05 * rays, 0.012, d);                                 // bright lower edge, ragged by the rays
    float upper = exp(-max(d, 0.0) / h * 1.7);                                               // fades out upward
    float gaps = smoothstep(0.36, 0.66, fbm(vec2(xw * 1.6 + seed * 7.0, t * 0.04)));        // the ribbon thins and breaks
    return lower * upper * rays * gaps;
}
vec3 auroraCol(float k) { return mix(vec3(0.10, 0.95, 0.42), vec3(0.70, 0.20, 0.85), smoothstep(0.18, 0.9, k)); }  // green low, violet crown
vec3 aurora(vec2 p, float t) {                       // three curtains at different depths: nearest brightest
    vec3 acc = vec3(0.0); float k = 0.0;
    acc += auroraCol(k) * curtain(p, t, 0.0, k) * 1.00;
    acc += auroraCol(k) * curtain(p - vec2(0.3, 0.12), t * 0.8, 11.0, k) * 0.55;
    acc += auroraCol(k) * curtain(p - vec2(-0.5, 0.22), t * 0.6, 23.0, k) * 0.30;
    return acc;                                      // then: col += au * 1.7 + au * au * 0.5;  (bloom on the bright edge)
}
```
Tonal discipline for any night / space / dusk subject: start from a near-black ground (`vec3(0.02, 0.035, 0.08)` at
the horizon → `vec3(0, 0, 0.012)` at the zenith), ADD light, tonemap `c / (1 + 0.6 c)`, and check `gl_frames`: mean
luminance 0.06–0.15 is a night scene; 0.3 with no black pixels is a wash.  Haze and bloom go on the light, not on
the frame.  Stars: `stars()` above (two layers, keep 0.2–0.3); city lights: `bokehSoft()` above.  Complete verified
example (aurora over a ridge with a frozen lake, three curtains, 5 k stars, blurred reflection): the harness ships
it as `codeverse3d/prompts/glsl_shader/examples/aurora_ridge.frag`.

## Feedback trails (u_prev) and Buffer A
```glsl
// shader.frag: decayed feedback + new emitter (particles / light streaks)
vec3 prev = texture(u_prev, uv + vec2(0.0, -0.002)).rgb * 0.94;        // drift + decay keeps it bounded
vec3 col = max(prev, newFrameColour);                                    // or prev + newFrameColour * 0.2
fragColor = vec4(col, 1.0);
// buffer_a.frag computes a simulation (e.g. reaction-diffusion, fluid-ish blur) reading texture(u_prev, uv) and its
// 4 neighbours (uv ± 1.0/u_resolution); shader.frag then colours texture(u_buffer_a, uv).
```

## Instancing-like repetition in 2D (many shapes cheaply)
```glsl
// grid of rotating squares: vec2 g = p * 8.0; vec2 id = floor(g); vec2 f = fract(g) - 0.5;
// f = rot2(u_time * (0.5 + hash12(id))) * f; float d = sdBox(f, vec2(0.25)); col += fill(d) * palette(hash12(id), ...);
```

## PITFALLS (each one cost a real run)
* Integer division / int literals: `1/2 == 0`; `float x = 3;` is fine but `pow(2, 3)` / `mod(x, 2)` may fail on strict
  drivers → always `2.0`.  `%` is integer-only → `mod()`.
* Loop bounds must be constant expressions (`for (int i = 0; i < 64; i++)`), break early inside.
* `normalize(vec3(0))` / `0/0` / `sqrt(-x)` / `pow(neg, 0.5)` / `log(0)` → NaN → black speckles → score 0.  Guard them.
* `texture(u_prev, uv)` needs uv in 0..1 (fragCoord / u_resolution), NOT the centred `p`.
* Aspect: divide by `u_resolution.y`, never by `u_resolution.xy` for shapes (ellipses otherwise).
* `smoothstep(a, b, x)` needs a < b; AA width ≈ `1.5 / u_resolution.y`.
* Brightness: accumulations (glow, bokeh sums) blow out → tonemap (`c/(1+c)` or ACES) before gamma; do not gamma twice.
* Motion: use u_time directly (`sin(u_time)`, `fract(u_time*0.2)`); do NOT use u_frame alone (frames are sampled at
  arbitrary times) and never seed per-frame randomness with u_frame (flicker).
* `precision` qualifiers, `#version`, `#extension`, `#include`, `uniform` / `out` declarations: leave them out.
* gl_FragCoord origin is bottom-left; y up.  (The harness flips rows when writing PNGs, so what you compute is what you see.)
* Symmetric patterns at t=0 (everything at the origin) look dead — offset phases with hashes.
* A light phenomenon drawn as evenly spaced bars / hard discs / a flat wash: the judge's likeness criterion scores it
  0.4 — see the Light section.
