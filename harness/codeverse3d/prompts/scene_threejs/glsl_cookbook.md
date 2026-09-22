# GLSL cookbook — custom shaders that SURVIVE this renderer (three r182, WebGL2)

Three ways a shader "fails" with no error: missing log-depth chunks (discarded behind
opaque geometry when the renderer uses a logarithmic depth buffer), missing fog chunks
(the effect keeps full contrast while the world recedes — the sticker look), missing
tonemap/colorspace output (dark, wrong colours).  The boilerplate below carries all of
them.  Every factory here is `export function make<Name>Material(THREE, opts)` and the
harness test suite compiles each one in headless Chrome, on a Mesh AND an InstancedMesh.

Conventions: uniforms `uTime` (seconds) driven via `mat.userData.update(t, dt)`;
`varying` spelling for interstage declarations (three defines it for both GLSL dialects);
never write `#version` or `precision` lines; every `#include <...>` alone on its line.

## Shared GLSL library (hash, noise, fbm, fresnel)

```js
// src/shaders/lib.js — prepend GLSL_LIB into any shader head that needs it.
export const GLSL_LIB = /* glsl */ `
float c3vHash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; return fract(p * (p + p)); }
float c3vHash21(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float c3vNoise2(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = c3vHash21(i), b = c3vHash21(i + vec2(1.0, 0.0));
  float c = c3vHash21(i + vec2(0.0, 1.0)), d = c3vHash21(i + vec2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float c3vFbm2(vec2 p) {                       // 4 octaves, range ~0..1
  float s = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { s += a * c3vNoise2(p); p *= 2.03; a *= 0.5; }
  return s;
}
float c3vFresnel(vec3 n, vec3 viewDir, float power) {
  return pow(1.0 - clamp(dot(normalize(n), normalize(viewDir)), 0.0, 1.0), power);
}
`;
```

Why: value noise over a hash — no textures, no extensions; `c3vFbm2` is the workhorse for
water, fog, dirt, wood grain.  Loop bounds are constant (GLSL requires it).

## The boilerplate: makeShaderMaterial (fog + log-depth + uTime + tonemap, copy whole)

```js
// src/shaders/base.js — the ONE way to build a ShaderMaterial here.  Write only the
// bodies of main(); the wrapper owns the chunks that make it a good citizen.
function uniformDecl(THREE, name, u) {          // custom uniforms are NOT auto-declared in GLSL
  const v = u.value;
  const t = v && v.isColor ? 'vec3' : v && v.isVector2 ? 'vec2' : v && v.isVector3 ? 'vec3'
    : v && v.isVector4 ? 'vec4' : v && v.isMatrix4 ? 'mat4' : v && v.isTexture ? 'sampler2D'
    : typeof v === 'number' ? 'float' : null;
  return t ? `uniform ${t} ${name};` : '';
}

export function makeShaderMaterial(THREE, opts = {}) {
  const { uniforms = {}, varyings = '', vertexHead = '', fragmentHead = '',
          vertexMain = '', fragmentMain = 'gl_FragColor = vec4(1.0, 0.0, 1.0, 1.0);',
          fog = true, additive = false, name = 'C3vShader', ...rest } = opts;
  const decls = ['uniform float uTime;',
                 ...Object.entries(uniforms).map(([k, u]) => uniformDecl(THREE, k, u))].join('\n');
  const vs = [
    '#include <common>',
    '#include <logdepthbuf_pars_vertex>',
    fog ? '#include <fog_pars_vertex>' : '',
    decls, varyings, vertexHead,
    'void main() {',
    '  vec3 transformed = position;',
    vertexMain,
    '  vec4 mvPosition = modelViewMatrix * vec4(transformed, 1.0);',
    '#ifdef USE_INSTANCING',
    '  mvPosition = modelViewMatrix * instanceMatrix * vec4(transformed, 1.0);',
    '#endif',
    '  gl_Position = projectionMatrix * mvPosition;',
    '#include <logdepthbuf_vertex>',
    fog ? '#include <fog_vertex>' : '',
    '}',
  ].filter(Boolean).join('\n');
  const fs = [
    '#include <common>',
    '#include <logdepthbuf_pars_fragment>',
    fog ? '#include <fog_pars_fragment>' : '',
    decls, varyings, fragmentHead,
    'void main() {',
    '#include <logdepthbuf_fragment>',
    fragmentMain,
    '#include <tonemapping_fragment>',
    '#include <colorspace_fragment>',
    // fog LAST, matching three's own materials; additive light must FADE, not tint:
    fog ? (additive ? 'gl_FragColor.a *= 1.0 - smoothstep(fogNear, fogFar, vFogDepth);'
                    : '#include <fog_fragment>') : '',
    '}',
  ].filter(Boolean).join('\n');
  const mat = new THREE.ShaderMaterial({
    // fog uniforms MUST exist in the map or the scene fog has nowhere to land:
    uniforms: THREE.UniformsUtils.merge([
      fog ? THREE.UniformsLib.fog : {}, { uTime: { value: 0 } }, uniforms]),
    vertexShader: vs, fragmentShader: fs, fog, ...(additive
      ? { transparent: true, depthWrite: false, blending: THREE.AdditiveBlending } : {}), ...rest });
  mat.name = name;
  mat.userData.update = (t) => { mat.uniforms.uTime.value = t; };
  return mat;
}
```

Wire-up in `scene.js` (once, at build time — never traverse inside `update`):

```js
export function collectAnimatedMaterials(scene) {
  const mats = [];
  scene.traverse((o) => {
    const list = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
    for (const m of list) if (m.userData && typeof m.userData.update === 'function' && !mats.includes(m)) mats.push(m);
  });
  return mats;   // in update(t, dt):  for (const m of mats) m.userData.update(t, dt);
}
```

## Scrolling water (plane at the water level)

```js
export function makeWaterMaterial(THREE, opts = {}) {
  const shallow = new THREE.Color(opts.shallow ?? 0x3fa8b8), deep = new THREE.Color(opts.deep ?? 0x0b3446);
  return makeShaderMaterial(THREE, {
    name: 'Water', transparent: true, depthWrite: false, side: THREE.DoubleSide,
    uniforms: { uShallow: { value: shallow }, uDeep: { value: deep }, uOpacity: { value: opts.opacity ?? 0.85 } },
    varyings: 'varying vec2 vWuv; varying vec3 vN; varying vec3 vV;',
    vertexHead: GLSL_LIB,
    vertexMain: `
      vWuv = (modelMatrix * vec4(transformed, 1.0)).xz * 0.35;
      // small ripple displacement so the sun break-up reads at grazing angles
      transformed.y += 0.03 * (c3vNoise2(vWuv * 2.0 + uTime * 0.35) - 0.5);
      vN = normalize(normalMatrix * normal);
      vV = -(modelViewMatrix * vec4(transformed, 1.0)).xyz;`,
    fragmentHead: GLSL_LIB,
    fragmentMain: `
      // two noise fields scrolling AGAINST each other never look like a conveyor belt
      float n1 = c3vFbm2(vWuv * 3.0 + vec2(uTime * 0.06, uTime * 0.028));
      float n2 = c3vFbm2(vWuv * 5.0 - vec2(uTime * 0.043, uTime * 0.07) + 17.0);
      float ripple = n1 * 0.6 + n2 * 0.4;
      vec3 col = mix(uDeep, uShallow, smoothstep(0.35, 0.75, ripple));
      float f = c3vFresnel(vN, vV, 3.0);
      col += vec3(0.9, 0.95, 1.0) * f * 0.35;                          // sky glint at grazing angles
      col += vec3(1.0) * smoothstep(0.72, 0.78, ripple) * 0.25;        // sparse crest sparkle
      gl_FragColor = vec4(col, uOpacity);`,
  });
}
```

Numbers: scroll speeds 0.03–0.08 (m-ish units per second), two layers at scales 3 and 5,
opacity 0.8–0.9, `depthWrite: false` so shorelines don't z-fight.

## Waterfall (falling water accelerates: ride features on sqrt(drop))

```js
export function makeWaterfallMaterial(THREE, opts = {}) {
  return makeShaderMaterial(THREE, {
    name: 'Waterfall', transparent: true, depthWrite: false, side: THREE.DoubleSide,
    uniforms: { uColor: { value: new THREE.Color(opts.color ?? 0xcfe8f2) }, uHeight: { value: opts.height ?? 6.0 } },
    varyings: 'varying vec2 vUvw;',
    vertexMain: 'vUvw = uv;',
    fragmentHead: GLSL_LIB,
    fragmentMain: `
      float drop = 1.0 - vUvw.y;                       // 0 at the lip, 1 at the pool
      float age = sqrt(max(drop, 1e-4));               // free fall: distance ~ time^2
      float lane = vUvw.x * 14.0;
      float streak = c3vNoise2(vec2(lane, age * 6.0 - uTime * 1.6 + c3vHash11(floor(lane)) * 7.0));
      float body = smoothstep(0.35, 0.75, streak) * (0.55 + 0.45 * drop);
      float foam = smoothstep(0.8, 1.0, drop) * c3vFbm2(vec2(lane, uTime * 2.0));
      float a = clamp(body * 0.75 + foam * 0.9, 0.0, 1.0) * 0.9;
      gl_FragColor = vec4(uColor, a);`,
  });
}
```

Why: scrolling `uv.y` linearly reads as a conveyor; sampling on `sqrt(drop)` makes the
sheet visibly accelerate.  Per-lane hash offsets stop the herringbone weave.

## Glow / pulse emissive (lanterns, runes, portals, coals)

```js
export function makeGlowMaterial(THREE, opts = {}) {
  return makeShaderMaterial(THREE, {
    name: 'Glow', additive: true, side: THREE.DoubleSide,
    uniforms: { uColor: { value: new THREE.Color(opts.color ?? 0xffa64d) },
                uStrength: { value: opts.strength ?? 1.2 }, uSpeed: { value: opts.speed ?? 2.0 } },
    varyings: 'varying vec3 vN; varying vec3 vV;',
    vertexMain: `
      vN = normalize(normalMatrix * normal);
      vV = -(modelViewMatrix * vec4(transformed, 1.0)).xyz;`,
    fragmentHead: GLSL_LIB,
    fragmentMain: `
      float pulse = 0.75 + 0.25 * sin(uTime * uSpeed);
      float rim = c3vFresnel(vN, vV, 2.0);
      float core = 1.0 - rim;
      gl_FragColor = vec4(uColor * uStrength * pulse * (0.35 + 0.65 * core), core * 0.9 + 0.1);`,
  });
}
```

Why: `additive: true` sets AdditiveBlending + `depthWrite:false` + distance fade through
ALPHA (ordinary fog would make added light brighter with distance).  Strength ≤ 1.5
without bloom.

## God-ray billboard (light shafts through trees / windows)

```js
export function makeGodRayMaterial(THREE, opts = {}) {
  return makeShaderMaterial(THREE, {
    name: 'GodRay', additive: true, side: THREE.DoubleSide,
    uniforms: { uColor: { value: new THREE.Color(opts.color ?? 0xfff2cc) }, uStrength: { value: opts.strength ?? 0.35 } },
    varyings: 'varying vec2 vRuv;',
    vertexMain: 'vRuv = uv;',
    fragmentHead: GLSL_LIB,
    fragmentMain: `
      float across = 1.0 - abs(vRuv.x - 0.5) * 2.0;              // soft at both side edges
      float along = smoothstep(0.0, 0.25, vRuv.y) * (1.0 - smoothstep(0.55, 1.0, vRuv.y));
      float shimmer = 0.8 + 0.2 * c3vNoise2(vec2(vRuv.x * 6.0, uTime * 0.15));
      gl_FragColor = vec4(uColor, uStrength * across * across * along * shimmer);`,
  });
}
// Use on tall thin planes (e.g. 0.6 × 8 m), tilted to the sun azimuth, uv.y = down the shaft.
// 2–4 shafts maximum; they ADD light, so keep uStrength 0.2–0.5.
```

## Height fog curtain (valley mist, dawn haze — no postprocessing needed)

```js
export function makeHeightFogMaterial(THREE, opts = {}) {
  return makeShaderMaterial(THREE, {
    name: 'HeightFog', transparent: true, depthWrite: false, side: THREE.DoubleSide, fog: false,
    uniforms: { uColor: { value: new THREE.Color(opts.color ?? 0xcdd8e4) },
                uTop: { value: opts.top ?? 3.0 }, uDensity: { value: opts.density ?? 0.45 } },
    varyings: 'varying vec3 vW;',
    vertexMain: 'vW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
    fragmentHead: GLSL_LIB,
    fragmentMain: `
      float h = clamp(1.0 - vW.y / max(uTop, 0.01), 0.0, 1.0);          // thick low, gone at uTop
      float drift = c3vFbm2(vW.xz * 0.08 + vec2(uTime * 0.02, uTime * 0.013));
      gl_FragColor = vec4(uColor, h * h * uDensity * (0.55 + 0.45 * drift));`,
  });
}
// Mount on 2-3 huge vertical planes (or a cylinder shell) between the camera and the far zone.
```

## Wind sway on a built-in material (onBeforeCompile patch, instancing-safe)

```js
// Patch a MeshStandardMaterial so lighting/shadows/fog stay; inject sway into begin_vertex.
export function makeSwayMaterial(THREE, opts = {}) {
  const mat = new THREE.MeshStandardMaterial({ color: opts.color ?? 0x3f7a2f, roughness: 0.9 });
  const u = { uTime: { value: 0 }, uSway: { value: opts.sway ?? 0.12 }, uSwayFreq: { value: opts.freq ?? 1.4 } };
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nuniform float uTime; uniform float uSway; uniform float uSwayFreq;')
      .replace('#include <begin_vertex>', `
        #include <begin_vertex>
        {
          // phase differs per instance so a field never waves in lockstep
          float c3vPhase = 0.0;
          #ifdef USE_INSTANCING
            c3vPhase = instanceMatrix[3].x * 1.7 + instanceMatrix[3].z * 2.3;
          #endif
          float c3vBend = pow(clamp(transformed.y / 2.0, 0.0, 1.0), 1.5);   // roots stay planted
          transformed.x += sin(uTime * uSwayFreq + c3vPhase) * uSway * c3vBend;
          transformed.z += cos(uTime * uSwayFreq * 0.83 + c3vPhase) * uSway * 0.6 * c3vBend;
        }`);
  };
  // three caches programs by material type: two differently-patched Standards would share
  // one program without a distinct key.
  mat.customProgramCacheKey = () => 'c3v_sway_v1';
  mat.userData.update = (t) => { u.uTime.value = t; };
  return mat;
}
```

Why: patching keeps PBR lighting and shadows; the ONLY safe injection points are chunk
markers (`begin_vertex` for positions, `color_fragment` for albedo).  Shadows are cast
from the unswayed mesh (the depth material is not patched) — invisible at sway ≤ 0.15 m.

## Triplanar procedural surface (cliffs, terrain — no UVs needed)

```js
export function makeTriplanarMaterial(THREE, opts = {}) {
  const a = new THREE.Color(opts.rock ?? 0x6b6258), b = new THREE.Color(opts.moss ?? 0x4a5d33);
  return makeShaderMaterial(THREE, {
    name: 'Triplanar',
    uniforms: { uRock: { value: a }, uMoss: { value: b }, uScale: { value: opts.scale ?? 0.6 } },
    varyings: 'varying vec3 vWp; varying vec3 vWn; varying vec3 vNv; varying vec3 vVv;',
    vertexMain: `
      vWp = (modelMatrix * vec4(transformed, 1.0)).xyz;
      vWn = normalize(mat3(modelMatrix) * normal);
      vNv = normalize(normalMatrix * normal);
      vVv = -(modelViewMatrix * vec4(transformed, 1.0)).xyz;`,
    fragmentHead: GLSL_LIB,
    fragmentMain: `
      vec3 w = abs(vWn); w = w / (w.x + w.y + w.z + 1e-5);              // blend weights
      float tx = c3vFbm2(vWp.zy * uScale), ty = c3vFbm2(vWp.xz * uScale), tz = c3vFbm2(vWp.xy * uScale);
      float g = tx * w.x + ty * w.y + tz * w.z;                          // seam-free grain
      vec3 col = mix(uRock, uRock * (0.55 + 0.9 * g), 1.0);              // value break-up
      col = mix(col, uMoss, smoothstep(0.55, 0.9, vWn.y) * smoothstep(0.35, 0.7, g)); // moss on up-faces
      float lambert = 0.35 + 0.65 * clamp(dot(vNv, normalize(vec3(0.4, 0.8, 0.3))), 0.0, 1.0);
      gl_FragColor = vec4(col * lambert, 1.0);`,
  });
}
```

Why: three fbm samples blended by |normal| kill UV stretching on steep faces; moss grows
on up-facing surfaces (`vWn.y`).  This material fakes its own lambert — use it on set
dressing, not on hero objects (or patch a Standard material instead).

## Toon + outline

```js
export function makeToonMaterial(THREE, opts = {}) {
  return makeShaderMaterial(THREE, {
    name: 'Toon',
    uniforms: { uColor: { value: new THREE.Color(opts.color ?? 0xcc5533) },
                uSun: { value: (opts.sun ?? new THREE.Vector3(0.4, 0.8, 0.3)).clone().normalize() } },
    varyings: 'varying vec3 vTn;',
    vertexMain: 'vTn = normalize(mat3(modelMatrix) * normal);',
    fragmentMain: `
      float d = clamp(dot(normalize(vTn), uSun), 0.0, 1.0);
      float band = d > 0.66 ? 1.0 : (d > 0.33 ? 0.62 : 0.34);            // 3 hard bands
      gl_FragColor = vec4(uColor * band, 1.0);`,
  });
}
// Outline: add a slightly inflated BackSide copy — no shader needed:
export function addOutline(THREE, mesh, thickness = 0.02, color = 0x101014) {
  const out = new THREE.Mesh(mesh.geometry,
    new THREE.MeshBasicMaterial({ color, side: THREE.BackSide }));
  out.scale.setScalar(1 + thickness); out.name = mesh.name + 'Outline';
  mesh.add(out); return out;
}
```

## Pitfalls (symptom → cause → fix)

1. **Effect invisible behind objects, no error** → missing `logdepthbuf_*` chunks while the
   renderer runs a logarithmic depth buffer.  Use `makeShaderMaterial` (chunks included).
2. **Effect stays full-contrast while the world fogs out** → missing fog chunks / `fog:
   true` / fog uniforms.  All three are required; the boilerplate does them.
3. **"redefinition of 'vUv'"** → the varying is declared in `varyings` AND in your head
   string, or you redeclared a built-in (`position`, `normal`, `uv`, `projectionMatrix`,
   `modelViewMatrix`, `normalMatrix`, `cameraPosition`).  Declare each varying once; never
   redeclare built-ins.
4. **"'#version' must occur first" / "unsupported shader version"** → you wrote `#version`
   or `precision` in a ShaderMaterial; three prepends its own header.  Delete them.
5. **`out vec4` + `gl_FragColor` conflict** → GLSL3 syntax in a GLSL1 material.  Use
   `varying` + `gl_FragColor` + `texture2D` everywhere; do not set `glslVersion`.
6. **`#include` sharing a line** (`{ #include <fog_fragment> }`) → silent compile failure.
   Every include alone on its own line.
7. **`pow(x, 2)` / `mod(i, 2)` / `vec3(v2)`** → no int↔float promotion: `2.0`, `float(i)`,
   matching constructors.  Loops need constant bounds (`for (int i = 0; i < 4; i++)`).
8. **"'uName' : undeclared identifier"** → ShaderMaterial does NOT auto-declare custom
   uniforms in GLSL; `makeShaderMaterial` generates the declarations from the uniforms
   map — do not redeclare them (or uTime) in your head strings.  A uniform that never
   moves: you replaced the uniforms object instead of mutating `uniforms.uTime.value`;
   drive time from `update(t, dt)` via `mat.userData.update(t)` — never `Date.now()`.
9. **Instanced mesh: all copies at the origin / no sway variation** → ShaderMaterial must
   apply `instanceMatrix` under `#ifdef USE_INSTANCING` (the boilerplate does);
   `onBeforeCompile` code that needs world position must read `instanceMatrix[3]`.
10. **Two patched Standard materials render identically** → shared program cache; give each
    patch flavour its own `customProgramCacheKey`.
11. **Transparent water z-fights the shore / hides particles** → `transparent: true` needs
    `depthWrite: false`; large transparent planes render in draw order, so keep water
    slightly below the bank top.
12. **Glow gets BRIGHTER with distance** → additive material with the normal fog mix; fade
    through alpha (the boilerplate's `additive: true` path).
13. **Black output with tone mapping** → colours built in [0,1] then multiplied down twice;
    remember output passes through ACES + sRGB: author linear colours ~[0, 1.5] and check
    a mid-grey renders mid-grey.
14. **`fwidth`/`dFdx` "extension" errors** → they are core in WebGL2; delete any
    `#extension GL_OES_standard_derivatives` line.
15. **`Material.clone()` loses the patch** → `onBeforeCompile` is not cloned; build a fresh
    material from the factory instead of cloning patched ones.
16. **Shadow acne / wrong shadows on displaced vertices** → the depth pass is not patched;
    keep vertex displacement small (≤ 0.15 m) or set `castShadow = false` on the mesh.
