# Shader recipes

Companion to `c3d-glsl-craft`. Every snippet below was composed with the harness's own
`codeverse3d.languages.glsl_shader.compose`, compiled on moderngl 5.12 / OpenGL 3.3 core
and linted with `lint_text` on 2026-08-25: **compiles clean, zero lint findings**. Drop the
helpers into `src/common.glsl` and call them from `src/shader.frag`.

Assumed present (write them once in `common.glsl`):

```glsl
float hash12(vec2 p){ vec3 p3=fract(vec3(p.xyx)*0.1031); p3+=dot(p3,p3.yzx+33.33); return fract((p3.x+p3.y)*p3.z); }
float vnoise(vec2 p){ vec2 i=floor(p), f=fract(p); vec2 u=f*f*(3.0-2.0*f);
  return mix(mix(hash12(i), hash12(i+vec2(1,0)), u.x),
             mix(hash12(i+vec2(0,1)), hash12(i+vec2(1,1)), u.x), u.y); }
```

Cheaper alternative for the base noise: `texture(u_noise, p * 0.004).r`. The harness's
`u_noise` is a 256x256 repeating RGBA texture with linear filtering, so one fetch buys you a
bilinear-interpolated hash and lets you afford two more octaves.

## 1. fbm whose fine octaves fade with distance

The fix for "uniform high-frequency noise causing severe aliasing". Pass the ray distance
(raymarch) or the screen-space footprint (2D); far pixels get the low octaves only.

```glsl
float fbmFade(vec2 p, float dist){
    float v = 0.0, a = 0.5, w = 1.0;
    for (int i = 0; i < 6; i++) {
        float fade = 1.0 / (1.0 + 0.35 * dist * float(i));
        v += a * fade * vnoise(p); w += a * fade;
        p = p * 2.03 + 0.31; a *= 0.5;
    }
    return v / max(w, 1e-4);
}
```

Normalising by the accumulated weight keeps the mean brightness constant as octaves drop
out, so the horizon does not darken.

## 2. Structure across the flow

```glsl
float acrossFlow(vec2 p, vec2 flow, float t){
    vec2 across = vec2(-flow.y, flow.x);
    mat2 basis = mat2(across, flow);      // column 0 = across, column 1 = along
    return fbmFade((p * vec2(2.0, 9.0)) * basis - flow * t * 0.6, 0.0);
}
```

`vec2(2.0, 9.0)` is the anisotropy: fine detail across, stretched along. Swap the two and
the same code reads as a sliding texture instead of a moving surface. Wood grain, brushed
metal, water and cloth are all this function with a different pair of numbers.

## 3. Instances with their own phase and lifetime

```glsl
float sparks(vec2 p, float t){
    float acc = 0.0;
    for (int i = 0; i < 24; i++) {
        float id = float(i);
        float ph   = hash12(vec2(id, 7.0)) * 6.2831;
        float life = fract(t * 0.4 + hash12(vec2(id, 3.0)));   // each starts at its own point
        vec2  c    = vec2(cos(ph), sin(ph)) * (0.15 + 0.35 * life);
        acc += smoothstep(0.02, 0.0, length(p - c)) * (1.0 - life);
    }
    return acc;
}
```

Two things make it read: the per-id phase (so t = 0 is not a symmetric ring) and the
`(1.0 - life)` fade, which hides the `fract` wrap. A wrap you can see is the `flicker`
warning waiting to happen.

## 4. A light shaft is a marched integral

```glsl
float shaft(vec3 ro, vec3 rd, vec3 lightDir){
    float s = 0.0, t = 0.0;
    for (int i = 0; i < 48; i++) {
        vec3 pos = ro + rd * t;
        float vis   = step(0.0, sdScene(pos + lightDir * 0.35));   // is this point lit?
        float phase = 0.6 + 0.4 * pow(max(dot(rd, lightDir), 0.0), 8.0);
        s += vis * phase * exp(-0.08 * t);
        t += 0.12;
    }
    return s;
}
```

`phase` is what makes the shaft directional — it peaks when you look toward the light — and
`vis` is what makes it a *shaft* rather than a haze, because the beam is bright only where
nothing occludes it. Replace `sdScene` with your own distance field, or with a shadow test
against whatever is casting.

The lit-cloud variant of the same idea: sample your density once at the point and once a
short step toward the light, and light the difference.

```glsl
float dens = cloud(pos);
float lit  = cloud(pos + lightDir * 0.08);
vec3  col  = mix(SHADOW_COL, LIT_COL, clamp(0.9 - 3.0 * (lit - dens), 0.0, 1.0));
```

Thin toward the light means bright: that is the physical rule, and it is why a cloud lit
this way has a silver edge and a flat `exp()` glow does not.

## 5. The artifact that cost the worst run in this corpus

The lowest-scoring glsl run (0.30 of 1.0) was graded with a **critical** bug: *severe
raymarching artifacts (bright cyan/white pixels) along the edges of all geometry,
particularly at grazing angles*. That is the march overshooting a surface it hits almost
tangentially, so the central-difference normal is taken across the discontinuity and the
shading term explodes. Three changes remove it:

```glsl
float eps = max(0.0004, 0.0015 * t);       // hit tolerance grows with distance
t += max(d * 0.85, eps);                   // relax the step; never step by the raw distance
vec3 n = normalize(vec3(sdScene(pos + e.xyy) - sdScene(pos - e.xyy),
                        sdScene(pos + e.yxy) - sdScene(pos - e.yxy),
                        sdScene(pos + e.yyx) - sdScene(pos - e.yyx)));   // e = vec2(eps, 0)
```

Use the *same* `eps` for the hit test and for the normal offset, and clamp the final colour.
The same run also scored the lowest colourfulness of the nine (0.050) and was described as
"washed-out, monochromatic greyish" — the two defects together are most of the 0.30.

## 6. Two one-liners that move `technical_cleanliness`

```glsl
float w = 1.5 / u_resolution.y;                        // one pixel, in the centred p space
float edge = smoothstep(-w, w, d);                     // never step(0.0, d)
col += (hash12(fragCoord) - 0.5) / 255.0;              // dither, kills gradient banding
```

Tone-map once, at the very end (`col = col / (1.0 + col)`), and never twice. Accumulated
glow and bokeh sums blow past 1.0 quickly, and a blown frame is an ERROR.

## 7. When to reach for `buffer_a.frag`

Use the second pass when the effect **is** a simulation whose state must persist: advection,
reaction-diffusion, a wave height field, a particle field with collisions. `buffer_a.frag`
reads its own previous frame as `u_prev` and its four neighbours at `uv +- 1.0/u_resolution`;
`shader.frag` then reads the result as `u_buffer_a` and only colours it.

Zero of the 9 graded glsl runs in this corpus used it. In the sibling `opengl_python` runs,
which have the same feedback mechanism, all five recorded critical effect issues are
simulations that produced "expanding rings with empty interiors" or "high-frequency noise,
black speckles and aliasing instead of smooth fluid filaments" — a simulation whose state
does not persist between frames cannot advect anything. If the brief says "swirling ink",
"advection" or "reaction diffusion", the state belongs in `buffer_a.frag`.
