/**
 * Custom shaders that survive THIS renderer.
 *
 * Writing GLSL here has three ways to fail with no error at all — the
 * effect is simply absent from the frame, which reads as "the idea was
 * wrong" and ends in a retreat to a flat material. All three are
 * boilerplate, so they live here instead of in your shader:
 *
 *   1. a renderer with `logarithmicDepthBuffer: true` DISCARDS a custom
 *      shader without the four depth chunks behind opaque geometry
 *      (`depthWrite: false` does not save it).  THIS harness's renderer
 *      (runtime_js/lib/browser/renderer.js) leaves log depth OFF by
 *      default — the chunks then compile to nothing — but `--log-depth`
 *      is a host option for km-scale scenes, so they are always injected;
 *   1b. every scene here sets `scene.fog`, and fog reaches a shader ONLY
 *      through its chunks — without them the effect keeps full contrast
 *      while the world around it recedes, which is the sticker look;
 *   1c. scene renders go through a post chain whose OutputPass does the
 *      tone mapping (ACES, exposure 1.0) and the sRGB encode, but a pass
 *      drawn straight to the canvas (`post: false`, the raw coverage
 *      passes) does both in the fragment tail, so there a shader without
 *      three's two closing chunks renders dark and untonemapped next to
 *      every built-in — they are appended for you;
 *   2. an `#include` sharing a line with anything else fails to compile;
 *   3. GLSL 3.00 syntax in a material three compiles as GLSL ES 1.00.
 *
 * Write the BODY of main() and let this file assemble the rest:
 *
 *   const mat = makeShaderMaterial({
 *     uniforms: { uColor: { value: new THREE.Color(0xff8844) } },
 *     varyings: 'varying vec2 vUv;',
 *     vertexMain: 'vUv = uv;',                    // position is done for you
 *     fragmentMain: 'gl_FragColor = vec4(uColor * vUv.y, 1.0);',
 *   });
 *
 * Verify before you render: the harness's `check_shaders` tool (CLI:
 * `node runtime_js/check_shaders.mjs --ws <workspace>`) compiles every
 * material on the headless GPU and maps errors back to file:line.
 */

import * as THREE from 'three';
import { lehmer } from './noise.js';

/**
 * Shared GLSL helpers. Include with `${GLSL_UTIL}` in a head string.
 *
 * These are value noise over a hash, with no seed: they are NOT the
 * Perlin field `noise.js` displaces geometry with, so a shader pattern
 * and a CPU-built heightfield never line up. When both have to agree,
 * build the field on the CPU and hand it over as an attribute.
 */
export const GLSL_UTIL = [
    // Cofactors implement inverse transpose in GLSL ES 1.00 too. Dividing
    // by a determinant is unnecessary after normalization, but its SIGN
    // matters for mirrored objects. Unlike column rescaling, this also
    // handles shear from nested rotations and nonuniform parent scales.
    'vec3 astraNormalTransform(mat3 m, vec3 n) {',
    '  float magnitude = max(length(m[0]), max(length(m[1]), length(m[2])));',
    '  m /= max(magnitude, 1e-30);',
    '  vec3 a = cross(m[1], m[2]);',
    '  vec3 b = cross(m[2], m[0]);',
    '  vec3 c = cross(m[0], m[1]);',
    '  vec3 v = mat3(a, b, c) * n;',
    '  float len2 = dot(v, v);',
    '  float determinant = dot(m[0], a);',
    '  if (len2 > 1e-20) return v * inversesqrt(len2)',
    '      * (determinant < 0.0 ? -1.0 : 1.0);',
    '  return dot(n, n) > 1e-20 ? normalize(n) : vec3(0.0, 1.0, 0.0);',
    '}',
    'float astraHash11(float p) {',
    '  p = fract(p * 0.1031);',
    '  p *= p + 33.33;',
    '  return fract(p * (p + p));',
    '}',
    'float astraHash21(vec2 p) {',
    '  vec3 p3 = fract(vec3(p.xyx) * 0.1031);',
    '  p3 += dot(p3, p3.yzx + 33.33);',
    '  return fract((p3.x + p3.y) * p3.z);',
    '}',
    'float astraHash31(vec3 p) {',
    '  p = fract(p * 0.1031);',
    '  p += dot(p, p.yzx + 33.33);',
    '  return fract((p.x + p.y) * p.z);',
    '}',
    'float astraNoise2(vec2 p) {',
    '  vec2 i = floor(p), f = fract(p);',
    '  f = f * f * (3.0 - 2.0 * f);',
    '  float a = astraHash21(i), b = astraHash21(i + vec2(1.0, 0.0));',
    '  float c = astraHash21(i + vec2(0.0, 1.0));',
    '  float d = astraHash21(i + vec2(1.0, 1.0));',
    '  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);',
    '}',
    'float astraFbm2(vec2 p, int octaves) {',
    '  float s = 0.0, a = 0.5;',
    '  for (int i = 0; i < 6; i++) {',
    '    if (i >= octaves) break;',
    '    s += a * astraNoise2(p);',
    '    p *= 2.02; a *= 0.5;',
    '  }',
    '  return s;',
    '}',
    'float astraFresnel(vec3 n, vec3 v, float power) {',
    '  return pow(1.0 - clamp(dot(normalize(n), normalize(v)), 0.0, 1.0),',
    '             power);',
    '}',
    // Contact transitions: nothing in a photograph meets anything else
    // with a hard edge. `h` is height above the contact plane.
    'float astraContact(float h, float band) {',
    '  return 1.0 - smoothstep(0.0, max(band, 1e-4), h);',
    '}',
    // BROKEN COLOUR. Not a hue rotation: rotating about the grey axis
    // returns white unchanged, and the swing daylight actually makes is
    // warm where the sun lands and cool where only the sky reaches. The
    // field is centred on 0.375 because astraFbm2 at 2 octaves runs
    // 0..0.75 about that mean — subtracting the half a caller expects
    // both skews the swing cool and halves it (measured: a meadow's mat
    // moved 8.2 degrees of local hue instead of the 25-45 a photograph
    // carries). `swing` around 0.3 is stone, 0.6 is foliage.
    'vec3 astraHueBreak(vec3 c, vec2 p, float scale, float swing) {',
    '  float h = (astraFbm2(p * scale, 2) - 0.375) * swing;',
    '  return c * vec3(1.0 + h, 1.0 + h * 0.15, 1.0 - h);',
    '}',
    'vec3 astraHueShift(vec3 c, float a) {',
    '  const vec3 k = vec3(0.57735);',
    '  float ca = cos(a);',
    '  return c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca);',
    '}',
    // One stroke per period of v, half-width w, antialiased by its own
    // screen gradient — and gone once a pixel spans more than the stroke
    // itself, or a grazing surface turns solid instead of striped.
    // The vanishing only works for a THIN stroke: aa is clamped at 0.30
    // while the kill runs w*1.5 to w*5, so past w ~= 0.06 it never fully
    // kills and past 0.20 it never starts — a fat stroke greys out to a
    // flat mid tone instead of disappearing.
    // fwidth is a FRAGMENT-stage function and this block ships in both,
    // so the guard is not decoration: without it every vertex shader in
    // the engine fails to compile.
    '#ifdef ASTRA_FRAG',
    'float astraStroke(float v, float w) {',
    '  float g = abs(fract(v) - 0.5);',
    '  float aa = clamp(fwidth(v) * 0.5, 0.0004, 0.30);',
    '  float m = 1.0 - smoothstep(max(w - aa, 0.0), w + aa, g);',
    '  return m * (1.0 - smoothstep(w * 1.5, w * 5.0, aa));',
    '}',
    // Surface-gradient bump (Mikkelsen) in view space: perturb the lit
    // normal n by a height h (astraBump), or by h's screen derivatives dh
    // (astraBumpSlope, for an analytic gradient). Existing
    // normal/bump maps have already run, so their detail is kept. A
    // degenerate pixel footprint (det 0) returns n, never NaN.
    'vec3 astraBumpSlope(vec3 eye, vec3 n, vec2 dh) {',
    '  vec3 dx = dFdx(eye), dy = dFdy(eye);',
    '  vec3 r1 = cross(dy, n), r2 = cross(n, dx);',
    '  float det = dot(dx, r1);',
    '  if (abs(det) < 1e-12) return n;',
    '  return normalize(abs(det) * n - sign(det) * (dh.x * r1 + dh.y * r2));',
    '}',
    'vec3 astraBump(vec3 eye, vec3 n, float h) {',
    '  return astraBumpSlope(eye, n, vec2(dFdx(h), dFdy(h)));',
    '}',
    '#endif',
    // Bounded ray marches (fire, smoke, clouds). Entry and exit distances of
    // origin + t * direction through an axis-aligned box; an axis-parallel
    // ray keeps a finite slab. Miss when y <= x.
    'vec2 astraRayBox(vec3 origin, vec3 direction, vec3 boxMin, vec3 boxMax) {',
    '  vec3 safe = mix(vec3(-1.0), vec3(1.0), step(vec3(0.0), direction))',
    '    * max(abs(direction), vec3(1e-7));',
    '  vec3 a = (boxMin - origin) / safe, b = (boxMax - origin) / safe;',
    '  vec3 lo = min(a, b), hi = max(a, b);',
    '  return vec2(max(lo.x, max(lo.y, lo.z)), min(hi.x, min(hi.y, hi.z)));',
    '}',
    // The depth a volume writes for its first visible density, so opaque
    // geometry in front still hides it and its empty proxy box does not:
    // `gl_FragDepth = astraClipDepth(uLocalToClip, firstHit);` (standard
    // depth only — guard it with #ifndef USE_LOGDEPTHBUF).
    'float astraClipDepth(mat4 localToClip, vec3 p) {',
    '  vec4 clip = localToClip * vec4(p, 1.0);',
    '  return clamp(clip.z / clip.w * 0.5 + 0.5, 0.0, 1.0);',
    '}',
    // 0 edge-on, 1 facing. A double-sided shell piles its front and back
    // into the same pixels at the silhouette and stacks into a bright
    // rib; fade by this.
    'float astraFacing(vec3 n, vec3 v) {',
    '  return abs(dot(normalize(n), normalize(v)));',
    '}',
    // Decorrelate neighbouring filaments. Offsets under 2*PI leave them
    // correlated and their fronts weave a herringbone across the sheet.
    'float astraStagger(float x) {',
    '  return (sin(x * 311.0) * 1.70 + sin(x * 137.0 + 1.1) * 1.15',
    '        + sin(x * 57.0 + 2.3) * 0.60) * 4.3;',
    '}',
].join('\n');

// ---------------------------------------------------------------------
// Library-internal helpers the effect modules share. None of them is in
// the catalog: they exist so one body is written once, not fifteen times.

/** Take a THREE.Color, a hex or nothing, never sharing the instance. */
export function toColor(value, fallback) {
    return new THREE.Color(
        value === undefined || value === null ? fallback : value);
}

/** Clamp to 0..1. */
export function unit(value) {
    return Math.max(0, Math.min(1, value));
}

/** fract(), which JS's % gets wrong for a negative seed. */
export function frac(x) {
    return x - Math.floor(x);
}

/** astraHash11 from GLSL_UTIL, so CPU and shader agree on a seed. */
export function hash11(x) {
    let p = frac(x * 0.1031);
    p *= p + 33.33;
    return frac(p * (p + p));
}

/**
 * Turn a seed into a noise-space offset, so two materials differ.
 *
 * The offset is a UNIFORM: a seed baked into the GLSL would be fixed for
 * every material sharing the cache key, because the first one to compile
 * a key decides the source for all of them. `s` is the seed (plus the
 * patch's own salt); `a`, `b`, `c` and the scale `k` are per LIBRARY, or
 * one seed would lay one library's field along another's blotches.
 */
export function seedVec3(s, a, b, c, k) {
    return new THREE.Vector3(hash11(s + a), hash11(s + b), hash11(s + c))
        .multiplyScalar(k);
}

/**
 * The library's ONE azimuth/elevation -> direction rule: azimuth in degrees
 * from +X toward +Z (0 = +X, 90 = +Z), elevation in degrees above the
 * horizon.  sunRig, makeSky, the moon, establishingShot's eye and every
 * default sun use it, so an angle means the same thing to all of them.
 */
export function sunVector(azDeg, elDeg) {
    const az = azDeg * Math.PI / 180;
    const el = elDeg * Math.PI / 180;
    return new THREE.Vector3(Math.cos(el) * Math.cos(az), Math.sin(el),
                             Math.cos(el) * Math.sin(az));
}

/**
 * Read "the scene's wind" in every spelling the library documents, so ONE
 * wind can be handed to every effect: a number (strength, along the
 * fallback's direction); `[x, z]`; `[x, y, z]` or a THREE.Vector3 / `{x, y, z}`
 * (y is ignored); a THREE.Vector2 / `{x, y}` (y is world z); or `{ dir |
 * direction, strength, speed }` with `dir` in any of those forms.
 * `null`/missing reads `fallback`, which takes the same spellings; anything
 * else throws a RangeError naming `label`.
 *
 * Returns `{ x, z, dir, strength, speed }`: (x, z) is the horizontal
 * vector — an array or vector IS it, an object is dir * strength; `dir`
 * its unit Vector2 over world XZ ((1, 0) when it has no direction);
 * `speed` defaults to 1.  Each effect keeps its own units for strength.
 */
export function readWind(value, fallback, label = 'wind') {
    const bad = () => new RangeError(`${label}: a wind is a number, [x, z], [x, y, z], `
        + 'a THREE vector or { dir, strength, speed } of finite numbers');
    // [x, z] of an array or a vector, or null when it is neither
    const xz = (v) => {
        const r = Array.isArray(v) ? (v.length === 2 ? [v[0], v[1]] : v.length === 3 ? [v[0], v[2]] : null)
            : v && typeof v === 'object' && v.x !== undefined ? [v.x, v.z ?? v.y] : null;
        if (r && !r.every(Number.isFinite)) throw bad();
        return r;
    };
    const unitXZ = ([x, z]) => {
        const l = Math.hypot(x, z);
        return l > 1e-9 ? new THREE.Vector2(x / l, z / l) : new THREE.Vector2(1, 0);
    };
    const w = value ?? fallback;
    const vec = xz(w);
    if (vec) return { x: vec[0], z: vec[1], dir: unitXZ(vec), strength: Math.hypot(...vec), speed: 1 };
    if (typeof w !== 'number' && !(w && typeof w === 'object')) throw bad();
    const o = typeof w === 'number' ? { strength: w } : w;
    const d = o.dir ?? o.direction;
    let dir;
    if (d !== undefined && d !== null) {
        const v = xz(d);
        if (!v) throw bad();
        dir = unitXZ(v);
    } else {
        dir = w !== fallback && fallback !== undefined && fallback !== null
            ? readWind(fallback, null, label).dir : new THREE.Vector2(1, 0);
    }
    const strength = o.strength ?? 1;
    const speed = o.speed ?? 1;
    if (!Number.isFinite(strength) || !Number.isFinite(speed)) throw bad();
    return { x: dir.x * strength, z: dir.y * strength, dir, strength, speed };
}

/**
 * A seed as a far-apart lattice coordinate, not a one-cell shift: the
 * integer seed (1 when undefined) mod 9973, times the multiplier `k`,
 * mod `m`.  Seeds 7 and 8 offsetting a hash lattice by 1 would merely
 * TRANSLATE the pattern by one cell, which reads as the same object twice.
 */
export function seedLattice(seed, k = 16807, m = 9973) {
    const s = Math.abs(Math.round(seed === undefined ? 1 : seed)) % 9973;
    return (s * k) % m;
}

/**
 * A point as a new Vector3, from a THREE.Vector3, an `[x, y, z]` or an
 * `{x, y, z}`.  `null`/missing reads `(fx, fy, fz)`; a coordinate a
 * given point leaves out is 0, so `[x, y]` lies at z = 0.
 */
export function readVec3(p, fx = 0, fy = 0, fz = 0) {
    if (p === undefined || p === null) return new THREE.Vector3(fx, fy, fz);
    if (Array.isArray(p)) return new THREE.Vector3(p[0] ?? 0, p[1] ?? 0, p[2] ?? 0);
    return new THREE.Vector3(p.x ?? 0, p.y ?? 0, p.z ?? 0);
}

/**
 * The library's ONE option rule.  `null` or missing reads `fallback`;
 * anything else that is not a finite number in `[min, max]` throws a
 * RangeError naming `label` — never a silent fallback or clamp (fire
 * clamps on purpose, after this check).  A seed is any finite number:
 * `option(opts.seed, 7, 'makeX: seed')`.
 */
export function option(value, fallback, label, min = -Infinity, max = Infinity) {
    const v = value ?? fallback;
    if (!Number.isFinite(v) || v < min || v > max) {
        const range = min === -Infinity ? (max === Infinity ? '' : ` <= ${max}`)
            : max === Infinity ? ` >= ${min}` : ` in [${min}, ${max}]`;
        throw new RangeError(`${label} must be a finite number${range}, got ${String(value)}`);
    }
    return v;
}

/** `option` for a count: an integer in `[min, max]`. */
export function intOption(value, fallback, label, min = -Infinity, max = Infinity) {
    const v = option(value, fallback, label, min, max);
    if (!Number.isInteger(v)) throw new RangeError(`${label} must be an integer, got ${String(value)}`);
    return v;
}

/** `option` for a size or a speed: a finite number above 0 (and <= `max`). */
export function positive(value, fallback, label, max = Infinity) {
    const v = option(value, fallback, label, -Infinity, max);
    if (!(v > 0)) throw new RangeError(`${label} must be above 0, got ${String(value)}`);
    return v;
}

/**
 * `option` for a vector: a THREE vector or an array of `length` finite
 * numbers, returned as a new array.  `null`/missing reads `fallback`.
 */
export function vector(value, fallback, length, label) {
    const w = value ?? fallback;
    const v = w?.toArray ? w.toArray() : w;
    if (!Array.isArray(v) || v.length !== length || !v.every(Number.isFinite)) {
        throw new RangeError(`${label} must be ${length} finite numbers, got ${String(value)}`);
    }
    return v.slice();
}

/**
 * The library's ONE `userData.sampleHeight(x, z, ...)` contract, for a
 * `width` x `depth` patch centred on its local origin: `fn`'s height
 * inside the patch, and `null` outside it, for a non-finite argument or
 * where `fn` has no finite height.  It never throws, so a prop just off
 * the patch reads `null` instead of the edge's height.
 */
export function boundedSampler(width, depth, fn) {
    return (x, z, ...rest) => {
        if (!(Math.abs(x) <= width / 2 && Math.abs(z) <= depth / 2)) return null;
        if (!rest.every((v) => v === undefined || Number.isFinite(v))) return null;
        const y = fn(x, z, ...rest);
        return Number.isFinite(y) ? y : null;
    };
}

/**
 * Read an `up`-style option as a unit vector, never as NaN.
 *
 * A degenerate vector would divide by zero in the shader and paint the
 * object black, so a zero-length one falls back to world up.
 */
export function upVector(value) {
    const v = new THREE.Vector3(0, 1, 0);
    if (value) v.fromArray(value.toArray ? value.toArray() : value);
    if (!(v.lengthSq() > 1e-9)) v.set(0, 1, 0);
    return v.normalize();
}

// GLSL several libraries each carry under their OWN name. A chained
// material wears several libraries at once and patchStandard throws when
// two patches give one function name two different bodies, so every
// library keeps its prefix (astraAcc..., astraWear...) and these write
// the body once. Each returns its lines joined by '\n'.

/** `vec3 name(vec3 n)`: triplanar weights, the cubed normal normalised. */
export function glslAxes(name) {
    return [
        `vec3 ${name}(vec3 n) {`,
        '  vec3 w = abs(n * n * n);',
        '  return w / max(w.x + w.y + w.z, 1e-4);',
        '}',
    ].join('\n');
}

/**
 * `float name(vec3 p, vec3 w)`: astraNoise2 on the three world planes,
 * blended by the weights `w`; `a`, `b`, `c` offset the planes apart.
 */
export function glslTriNoise(name, a, b, c) {
    return [
        `float ${name}(vec3 p, vec3 w) {`,
        `  return astraNoise2(p.yz + ${a}) * w.x`,
        `       + astraNoise2(p.zx + ${b}) * w.y`,
        `       + astraNoise2(p.xy + ${c}) * w.z;`,
        '}',
    ].join('\n');
}

/**
 * `float name(vec3 n, vec3 p)`: curvature in 1/m — how far the normal
 * turns per metre of surface under one pixel. Positive is convex,
 * negative the concave lee; ZERO across a hard, unwelded edge.
 * Fragment stage only (screen derivatives).
 */
export function glslCurv(name) {
    return [
        `float ${name}(vec3 n, vec3 p) {`,
        '  vec3 dx = dFdx(p), dy = dFdy(p);',
        '  float d = dot(dx, dx) + dot(dy, dy);',
        '  return (dot(dFdx(n), dx) + dot(dFdy(n), dy)) / max(d, 1e-12);',
        '}',
    ].join('\n');
}

/**
 * `vec3 name(vec3 w)`: a world direction as the LOCAL offset that moves
 * this surface one metre along it. Uses the reciprocal basis, including
 * shear produced by nested rotations/nonuniform scales. `instanced` folds
 * in instanceMatrix, which Three applies after the vertex hook. A singular
 * transform has no inverse and returns a zero offset instead of NaNs.
 */
export function glslLocalDir(name, instanced = false) {
    return [
        `vec3 ${name}(vec3 w) {`,
        '  mat3 m = mat3(modelMatrix);',
        ...(instanced ? ['#ifdef USE_INSTANCING',
                         '  m = m * mat3(instanceMatrix);', '#endif'] : []),
        '  vec3 a = cross(m[1], m[2]);',
        '  vec3 b = cross(m[2], m[0]);',
        '  vec3 c = cross(m[0], m[1]);',
        '  float determinant = dot(m[0], a);',
        '  float magnitude = length(m[0]) * length(m[1]) * length(m[2]);',
        '  if (abs(determinant) <= max(magnitude * 1e-7, 1e-30)) return vec3(0.0);',
        '  return vec3(dot(w, a), dot(w, b), dot(w, c)) / determinant;',
        '}',
    ].join('\n');
}

/**
 * Two octaves of sway with a fixed envelope, so the slide is bounded by
 * 1 and a field's stated volume is exactly the volume it occupies.
 * `wobX`/`wobZ` on the CPU; `glslWob(prefix)` is the same pair in GLSL,
 * spelled the same, named `<prefix>X` / `<prefix>Z`.
 */
export function wobX(f, p, t) {
    return Math.sin(f * t + p) * 0.75 + Math.sin(f * 1.9 * t + p * 2.1) * 0.25;
}

export function wobZ(f, p, t) {
    return Math.cos(f * t + p) * 0.75 + Math.cos(f * 2.3 * t + p * 1.6) * 0.25;
}

export function glslWob(prefix) {
    return [
        `float ${prefix}X(float f, float p, float t) {`,
        '  return sin(f * t + p) * 0.75 + sin(f * 1.9 * t + p * 2.1) * 0.25;',
        '}',
        `float ${prefix}Z(float f, float p, float t) {`,
        '  return cos(f * t + p) * 0.75 + cos(f * 2.3 * t + p * 1.6) * 0.25;',
        '}',
    ].join('\n');
}

const FOG_PARS_V = '#include <fog_pars_vertex>';
const FOG_V = '#include <fog_vertex>';
const FOG_PARS_F = '#include <fog_pars_fragment>';
const FOG_F = '#include <fog_fragment>';
// What three appends to the end of every one of its OWN fragment
// shaders. Both resolve per render target: identity into the
// linear buffer the post chain reads (scene renders; its OutputPass
// tone-maps), tone map + sRGB straight to the canvas (`post: false`
// and the raw passes: ACES, exposure 1.0) — where, without them, a
// custom shader is dark and untonemapped next to every built-in.
const OUT_F = [
    '  #include <tonemapping_fragment>',
    '  #include <colorspace_fragment>',
].join('\n');
const FOG_F_ADDITIVE = [
    '#ifdef USE_FOG',
    '  #ifdef FOG_EXP2',
    '    float astraFogF = 1.0 - exp(-fogDensity * fogDensity',
    '        * vFogDepth * vFogDepth);',
    '  #else',
    '    float astraFogF = smoothstep(fogNear, fogFar, vFogDepth);',
    '  #endif',
    '  gl_FragColor.a *= 1.0 - clamp(astraFogF, 0.0, 1.0);',
    '#endif',
].join('\n');
const LOG_PARS_V = '#include <logdepthbuf_pars_vertex>';
const LOG_V = '#include <logdepthbuf_vertex>';
const LOG_PARS_F = '#include <logdepthbuf_pars_fragment>';
const LOG_F = '#include <logdepthbuf_fragment>';

/**
 * Normalise interstage declarations to `varying`.
 *
 * Measured in three 0.184 (`WebGLProgram`): every non-raw material is
 * compiled as `#version 300 es` regardless of `glslVersion`, with
 * `#define varying out` in the vertex stage and `#define varying in` in
 * the fragment stage — so `varying` is the ONE spelling that is correct
 * in both. A shared declaration written `out vec3 vN;` becomes a second
 * fragment OUTPUT, which knocks out three's `pc_fragColor` and takes
 * `gl_FragColor` with it. Asking three for the GLSL3 dialect would not
 * help: its only effect is that three stops defining `gl_FragColor` at
 * all. So the author may write either dialect and this rewrites it.
 * (The dialect option is named without its literal `key: value` here:
 * the harness's GLSL audit sets a FILE-level GLSL3 flag from that token
 * and then errors on every `gl_FragColor` in the file — a doc comment
 * is not an exemption, measured on shader_preflight 2026-09-01.)
 */
function asVarying(decls) {
    return decls.replace(
        /^(\s*)(in|out)(\s+(?:lowp |mediump |highp )?(?:vec[234]|float|int|uint|mat[234])\s+\w+\s*;)/gm,
        '$1varying$3');
}

/**
 * Drop a repeated `varying <type> <name>;`, keeping the first.
 *
 * `varyings` is injected into BOTH stages, and an author who also
 * declares it in their own head string gets 'redefinition' — measured
 * on the first probe run, from a model that did exactly that.
 */
function dedupeVaryings(src) {
    const seen = new Set();
    return src.split('\n').filter((line) => {
        const m = line.match(
            /^\s*varying\s+(?:lowp |mediump |highp )?[a-z0-9]+\s+(\w+)\s*;/i);
        if (!m) return true;
        if (seen.has(m[1])) return false;
        seen.add(m[1]);
        return true;
    }).join('\n');
}


/**
 * Drop a repeated `uniform <type> <name>;`, keeping the first.
 *
 * Chained patches each declare what they need, and two of them wanting
 * the same uniform — uTime above all — is a redefinition the author
 * cannot see coming from inside one patch.
 */
function dedupeUniforms(src) {
    const seen = new Set();
    return src.split('\n').filter((line) => {
        const m = line.match(
            // The [n] is not optional decoration: without it
            // `uniform vec3 uPos[8];` never matched, was never
            // deduped, and two patches wanting one array hit the
            // exact redefinition this function exists to prevent.
            /^\s*uniform\s+(?:lowp |mediump |highp )?[a-z0-9]+\s+(\w+)\s*(?:\[[^\]]*\])?\s*;/i);
        if (!m) return true;
        if (seen.has(m[1])) return false;
        seen.add(m[1]);
        return true;
    }).join('\n');
}


/** Declare `uniform float uTime;` where it is used but not declared. */
function withTime(src) {
    if (!/\buTime\b/.test(src)) return src;
    if (/\buniform\s+float\s+uTime\s*;/.test(src)) return src;
    return 'uniform float uTime;\n' + src;
}

/** Insert `line` as the first statement of main(), on its own line. */
function intoMainTop(src, line) {
    if (src.includes(line)) return src;
    const i = src.search(/void\s+main\s*\([^)]*\)\s*\{/);
    if (i < 0) {
        throw new Error(
            'shader.js: no void main() to inject ' + line + ' into');
    }
    const open = src.indexOf('{', i) + 1;
    return src.slice(0, open) + '\n  ' + line + '\n' + src.slice(open);
}

/** Insert `line` as the last statement of main(), on its own line. */
function intoMainEnd(src, line) {
    if (src.includes(line)) return src;
    const close = src.lastIndexOf('}');
    if (close < 0) {
        throw new Error(
            'shader.js: no closing brace to inject ' + line + ' before');
    }
    // A newline BEFORE the line too: a one-line raw source
    // (`void main() { gl_FragColor = ...; }`) otherwise gets the
    // #include on the same line as its last statement — the exact
    // trap rule 2 at the top of this file describes, measured on the
    // GPU compile of the raw-source path (2026-09-01).
    return src.slice(0, close) + '\n  ' + line + '\n' + src.slice(close);
}

/**
 * Build a `ShaderMaterial` that is not silently discarded.
 *
 * Supply either the two mains (preferred — the boilerplate is written
 * for you) or complete `vertexShader`/`fragmentShader` sources, in
 * which case the depth chunks are injected into them.
 *
 * @param {object} opts
 *   `uniforms` THREE uniform map (uTime is added and driven by
 *   `tickShaders`); `varyings` declarations shared by both stages;
 *   `vertexHead`/`fragmentHead` code before main (functions,
 *   uniforms) — injected ABOVE three's own `<common>` and
 *   `<lights_pars_begin>`, so a head function cannot call
 *   `getDistanceAttenuation`, `saturate`, `pow2` or read
 *   `directionalLights`/`pointLights`; only a BODY can;
 *   `vertexMain`/`fragmentMain` the body of main; `vertexShader`/
 *   `fragmentShader` complete sources instead of the mains; `util`
 *   include GLSL_UTIL (default true); `fog` take part in scene fog
 *   (default true — false only for screen-space passes); `additive`
 *   this material ADDS light (shafts, neon, glints), which sets
 *   additive blending and fades it into distance through ALPHA — the
 *   ordinary fog chunk mixes toward the fog colour, so an added light
 *   would get BRIGHTER as it recedes; `name`
 *   material name; plus any
 *   THREE.ShaderMaterial option (transparent, side, depthWrite...).
 * @returns {THREE.ShaderMaterial} With `userData.astraShader = true`.
 */
export function makeShaderMaterial(opts = {}) {
    const {
        uniforms = {}, varyings = '', vertexHead = '', fragmentHead = '',
        vertexMain = '', fragmentMain = '', vertexShader, fragmentShader,
        util = true, fog = true, additive = false,
        name = 'AstraShader', ...rest
    } = opts;

    const util_ = util ? GLSL_UTIL : '';
    const shared = asVarying(varyings);
    let vs = vertexShader;
    let fs = fragmentShader;
    if (!vs) {
        vs = [
            '#include <common>', LOG_PARS_V, fog ? FOG_PARS_V : '',
            shared, util_, vertexHead,
            'void main() {',
            '  vec3 transformed = position;',
            vertexMain,
            // fog_vertex reads a variable named mvPosition, so the
            // position has to be built in two steps whether or not this
            // material ends up fogged.
            '  vec4 mvPosition = modelViewMatrix * vec4(transformed, 1.0);',
            '  gl_Position = projectionMatrix * mvPosition;',
            LOG_V,
            fog ? FOG_V : '',
            '}',
        ].filter(Boolean).join('\n');
    }
    if (!fs) {
        fs = [
            '#define ASTRA_FRAG',
            // The checker's own advice is "pass fog:false to opt
            // out", and it reads a source marker — so passing the
            // option has to write one, or the advice does nothing.
            // `3dcode: no-fog` is the marker THIS harness's GLSL audit
            // (runtime_js/lib/glsl_audit.mjs) accepts.
            fog ? '' : '// 3dcode: no-fog',
            '#include <common>', LOG_PARS_F, fog ? FOG_PARS_F : '',
            shared, util_, fragmentHead,
            'void main() {',
            '  ' + LOG_F,
            fragmentMain,
            // Fog is the LAST thing that happens to a fragment. Mixing
            // toward the fog colour is right for an opaque surface and
            // backwards for added light, which must simply arrive less.
            fog ? (additive ? FOG_F_ADDITIVE : '  ' + FOG_F) : '',
            OUT_F,
            '}',
        ].filter(Boolean).join('\n');
    }
    // Raw sources still get the chunks: the trap does not care who wrote
    // the shader.
    if (!vs.includes(LOG_PARS_V)) vs = '#include <common>\n' + LOG_PARS_V + '\n' + vs;
    if (!vs.includes(LOG_V)) vs = intoMainEnd(vs, LOG_V);
    // Raw sources opt out the same way: the trap does not care who
    // wrote the shader, and neither does the checker.
    if (!fog && !/(3dcode|astra3d): no-fog/.test(fs)) {
        fs = '// 3dcode: no-fog\n' + fs;
    }
    if (!fs.includes(LOG_PARS_F)) fs = '#include <common>\n' + LOG_PARS_F + '\n' + fs;
    if (!fs.includes(LOG_F)) fs = intoMainTop(fs, LOG_F);
    if (!fs.includes('colorspace_fragment')) {
        fs = intoMainEnd(fs, OUT_F);
    }
    if (/^\s*#version/m.test(vs + '\n' + fs)) {
        throw new Error(
            'shader.js: remove the #version directive — three prepends ' +
            'its own and a second one is a compile error');
    }
    // uTime always exists as a uniform, so a shader that reads it must
    // also DECLARE it; forgetting that is a compile error whose message
    // names a line in three\'s assembled source, not in yours.
    vs = dedupeUniforms(dedupeVaryings(withTime(vs)));
    fs = dedupeUniforms(dedupeVaryings(withTime(fs)));

    if (additive) {
        rest.blending = rest.blending === undefined
            ? THREE.AdditiveBlending : rest.blending;
        rest.transparent = rest.transparent === undefined
            ? true : rest.transparent;
        rest.depthWrite = rest.depthWrite === undefined
            ? false : rest.depthWrite;
    }
    const mat = new THREE.ShaderMaterial({
        // fogColor / fogDensity / fogNear / fogFar must EXIST in the map
        // or the renderer has nowhere to write the scene's fog into, and
        // the material silently renders unfogged.
        uniforms: Object.assign({ uTime: { value: 0 } },
                                fog ? THREE.UniformsLib.fog : {}, uniforms),
        vertexShader: vs,
        fragmentShader: fs,
        fog: !!fog,
        ...rest,
    });
    mat.name = name;
    mat.userData.astraShader = true;
    return mat;
}

/**
 * Patch a built-in material (lighting, shadows and depth chunks stay).
 *
 * Built-ins already carry the depth chunks, so the trap here is a
 * different one: three caches programs by material type and parameters,
 * so two differently-patched MeshStandardMaterials would SHARE one
 * compiled program. `customProgramCacheKey` is set from `name`.
 *
 * @param {THREE.Material} material The material to patch, in place.
 * Patches CHAIN: call it again on the same material and both run, in
 * call order, sharing one uniform map and one cache key.
 *
 * It patches IN PLACE, and `materials.js` hands the same instance to
 * every caller that asked for the same options — so patching a shared
 * material patches everything wearing it. Clone first when that is not
 * what you want, and clone BEFORE patching: `Material.clone()` copies
 * userData but not `onBeforeCompile`, so a clone of a patched material
 * is a dead chain that still claims the cache key.
 *
 * `vertexBody` runs after `<begin_vertex>`, which is BEFORE
 * `<project_vertex>` — so `transformed` there has not had
 * `instanceMatrix`, skinning or morphing applied. On an InstancedMesh
 * (which is most of what this engine builds) a world position computed
 * there is the mesh origin's for every copy unless you apply
 * `instanceMatrix` yourself under `#ifdef USE_INSTANCING`.
 *
 * @param {object} opts
 *   `name` names this patch in the shared cache key — every distinct
 *   GLSL needs its own, and options must vary by UNIFORM rather than by
 *   baked-in source, because the first material to compile a key
 *   decides the source for all of them; `uniforms` extra uniforms
 *   (uTime added, driven by `tickShaders`); `vertexHead`/`fragmentHead`
 *   code before main; `vertexBody` runs after `<begin_vertex>` (edit
 *   `transformed`); `fragmentBody` runs after `<color_fragment>` (edit
 *   `diffuseColor`); `alphaBody` runs before `<alphatest_fragment>` on
 *   both surface and depth/distance materials (coverage/discard logic);
 *   `normalBody` runs after `<normal_fragment_maps>` on the view-space
 *   shading normal; `roughnessBody` / `metalnessBody` / `outputBody`
 *   run after `<roughnessmap_fragment>` / `<metalnessmap_fragment>` /
 *   `<opaque_fragment>` (per-pixel `roughnessFactor`, `metalnessFactor`,
 *   the lit `gl_FragColor`); `transmissionBody` edits a Physical material's
 *   `material.thickness` / `material.transmission` after texture sampling
 *   and before volume refraction (only with USE_TRANSMISSION enabled);
 *   `util` include GLSL_UTIL.
 * @returns {THREE.Material} The same material.
 */
/**
 * Fail loudly when two chained patches define the same GLSL function.
 *
 * Varyings and uniforms are DECLARATIONS — two identical ones dedupe
 * safely. A function has a body, so the same name from two libraries is
 * either the same helper twice (harmless, dropped) or two different
 * helpers (a redefinition error whose message names neither patch).
 *
 * @param {string[]} heads One head string per patch, in call order.
 * @param {string[]} names The patch names, parallel to `heads`.
 * @param {string} stage 'vertex' or 'fragment', for the message.
 * @returns {string[]} The heads, with exact repeats blanked out.
 * @throws {Error} When one name carries two different bodies.
 */
function dedupeFunctions(heads, names, stage) {
    const seen = new Map();
    // Every GLSL ES 3.0 return type, not just the common four:
    // a helper returning ivec3 or mat2x3 slipped the check
    // entirely and collided at compile time instead.
    const TYPE = '(?:void|float|u?int|bool|[biu]?vec[234]'
        + '|mat[234](?:x[234])?)';
    const re = new RegExp(
        '^\\s*(?:lowp |mediump |highp )?' + TYPE
        + '\\s+(\\w+)\\s*\\([^)]*\\)\\s*\\{', 'gm');
    return heads.map((head, i) => {
        let out = head;
        for (const m of head.matchAll(re)) {
            const fn = m[1];
            // From the signature's first character, not the line start:
            // the same helper indented two spaces in one patch and flush
            // in another is the same helper, not a redefinition.
            const body = fnBody(
                head, m.index + (m[0].length - m[0].trimStart().length));
            const prev = seen.get(fn);
            if (!prev) { seen.set(fn, { body, from: names[i] }); continue; }
            if (prev.body === body) {
                out = out.replace(body, '');
                continue;
            }
            throw new Error(
                'shader.js: patches "' + prev.from + '" and "' + names[i]
                + '" both define ' + fn + '() in the ' + stage
                + ' stage with different bodies. Prefix your helpers '
                + '(astra<Lib>Name) — GLSL has one global namespace and '
                + 'the compile error names neither patch.');
        }
        return out;
    });
}

/** The source of one function, from its signature to its closing brace. */
function fnBody(src, start) {
    let depth = 0;
    for (let i = src.indexOf('{', start); i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}' && --depth === 0) {
            return src.slice(start, i + 1);
        }
    }
    return src.slice(start);
}

export function patchStandard(material, opts = {}) {
    const {
        name = 'AstraPatch', uniforms = {}, vertexHead = '',
        fragmentHead = '', vertexBody = '', fragmentBody = '', util = true,
        roughnessBody = '', metalnessBody = '', outputBody = '', alphaBody = '', normalBody = '',
        transmissionBody = '',
    } = opts;
    // Patches CHAIN. Assigning onBeforeCompile outright — which this did
    // — silently dropped every earlier patch and its uniforms, and
    // instanceVariation() patches from inside, so a varied instanced
    // material plus any second patch lost one of them with no error.
    if (material.userData.shared && !material.userData.astraShared) {
        material.userData.astraShared = true;
        console.warn(
            'patchStandard: ' + (material.name || material.type)
            + ' is a SHARED material from materials.js — every mesh using '
            + 'it takes this patch. Clone it first if that is not what '
            + 'you want (and clone before patching, not after).');
    }
    const shared = material.userData.uniforms || { uTime: { value: 0 } };
    Object.assign(shared, uniforms);
    material.userData.uniforms = shared;
    material.userData.astraShader = true;
    const patches = material.userData.astraPatches || [];
    const at = patches.findIndex((p) => p.name === name);
    const patch = { name, vertexHead, fragmentHead, vertexBody, fragmentBody,
                    roughnessBody, metalnessBody, outputBody, alphaBody, normalBody,
                    transmissionBody, util };
    // Re-applying a patch retunes it (its uniforms are already merged);
    // pushing it twice would declare its varyings twice.
    if (at >= 0) patches[at] = patch;
    else patches.push(patch);
    material.userData.astraPatches = patches;
    // The FIRST material to compile a given key decides the GLSL every
    // material with that key gets, so the key must name every patch in
    // the chain, in order. Vary behaviour by uniform, never by baking a
    // value into the source under a fixed name.
    material.customProgramCacheKey =
        () => 'astra:' + patches.map((p) => p.name).join('+');
    material.onBeforeCompile = (shader) => {
        Object.assign(shader.uniforms, shared);
        const util_ = patches.some((p) => p.util) ? GLSL_UTIL : '';
        const live = patches.filter((p) => p.vertexHead || p.fragmentHead);
        const vHead = dedupeFunctions(
            live.map((p) => p.vertexHead || ''),
            live.map((p) => p.name), 'vertex').filter(Boolean);
        const fHead = dedupeFunctions(
            live.map((p) => p.fragmentHead || ''),
            live.map((p) => p.name), 'fragment').filter(Boolean);
        // One replace per hook, bodies joined in CALL order: replacing
        // the same #include per patch inserts each new body ABOVE the
        // last, which runs the chain backwards.
        const vBody = patches.map((p) => p.vertexBody).filter(Boolean)
            .join('\n');
        const fBody = patches.map((p) => p.fragmentBody).filter(Boolean)
            .join('\n');
        let vs = util_ + '\n' + asVarying(vHead.join('\n')) + '\n'
            + shader.vertexShader;
        let fs = '#define ASTRA_FRAG\n' + util_ + '\n'
            + asVarying(fHead.join('\n')) + '\n' + shader.fragmentShader;
        if (vBody) {
            vs = vs.replace('#include <begin_vertex>',
                            '#include <begin_vertex>\n' + vBody);
        }
        if (fBody) {
            fs = fs.replace('#include <color_fragment>',
                            '#include <color_fragment>\n' + fBody);
        }
        // Later hooks. `<color_fragment>` is the albedo, and for
        // years it was the only one — which is why a rust crust could only
        // be sold by retuning the WHOLE material's roughness (a 30 % crust
        // demattes 100 % of the surface), and why aerial perspective could
        // only lift reflectance instead of adding airlight. Each is opt-in
        // and empty by default, so no existing patch changes.
        const join = (key) => patches.map((p) => p[key]).filter(Boolean)
            .join('\n');
        const aBody = join('alphaBody');
        if (aBody) {
            fs = fs.replace('#include <alphatest_fragment>',
                            aBody + '\n#include <alphatest_fragment>');
        }
        const nBody = join('normalBody');
        if (nBody) {
            fs = fs.replace('#include <normal_fragment_maps>',
                            '#include <normal_fragment_maps>\n' + nBody);
        }
        // after <roughnessmap_fragment>: `roughnessFactor` is in scope, and
        // every fragmentBody local above it still is.
        const rBody = join('roughnessBody');
        if (rBody) {
            fs = fs.replace('#include <roughnessmap_fragment>',
                            '#include <roughnessmap_fragment>\n' + rBody);
        }
        // after <metalnessmap_fragment>: `metalnessFactor` in scope.
        const mBody = join('metalnessBody');
        if (mBody) {
            fs = fs.replace('#include <metalnessmap_fragment>',
                            '#include <metalnessmap_fragment>\n' + mBody);
        }
        // Thickness and transmission maps are sampled INSIDE this chunk. A
        // hook before it would be overwritten; one after it would miss the
        // refraction calculation. Expand only this opt-in chunk, retaining
        // Three's USE_TRANSMISSION guard and its native optical model.
        const tBody = join('transmissionBody');
        if (tBody && fs.includes('#include <transmission_fragment>')) {
            const chunk = THREE.ShaderChunk.transmission_fragment;
            const anchor = 'vec3 pos = vWorldPosition;';
            if (!chunk?.includes(anchor)) {
                throw new Error('shader.js: Three transmission chunk changed; review transmissionBody insertion');
            }
            fs = fs.replace('#include <transmission_fragment>',
                            chunk.replace(anchor, tBody + '\n\t' + anchor));
        }
        // after <opaque_fragment>: `gl_FragColor` holds the LIT result, still
        // linear — tonemapping and the colour-space convert come after this,
        // and fog after those. This is the only hook that can add light
        // rather than reflectance (airlight, emission veils).
        const oBody = join('outputBody');
        if (oBody) {
            fs = fs.replace('#include <opaque_fragment>',
                            '#include <opaque_fragment>\n' + oBody);
        }
        // AFTER the bodies are in: uTime is read by the bodies far more
        // often than by the heads, and declaring before they exist left
        // every animated patch failing on an undeclared identifier.
        shader.vertexShader = dedupeUniforms(dedupeVaryings(withTime(vs)));
        shader.fragmentShader =
            dedupeUniforms(dedupeVaryings(withTime(fs)));
    };
    material.needsUpdate = true;
    return material;
}

/** Clone a material and rebuild its registered Astra patch chain.
 * Three's ordinary clone JSON-copies userData but drops onBeforeCompile, losing
 * both the shader and typed uniform values. Built-in patches and ShaderMaterial
 * uniforms follow the same rules: uniform values here are independent
 * (including vectors, matrices and arrays); textures remain borrowed references,
 * as they do for ordinary material map slots. Only registered Astra patches can
 * be rebuilt: additional external wrappers around onBeforeCompile must be
 * reapplied by their owner. An unpatched material's hook is kept by reference.
 * Set shareUniforms:true when collapsing a static batch that must retain the
 * source effect's externally driven uniform map rather than an independent one.
 */
export function clonePatchedMaterial(material, { shareUniforms = false } = {}) {
    // Leave uniforms out of Three's JSON copy: a render-target sampler can carry
    // a large object graph, and JSON cannot preserve its GPU identity anyway.
    const source = Object.create(material);
    source.userData = { ...material.userData };
    delete source.userData.uniforms;
    delete source.userData.astraPatches;
    delete source.userData.astraShader;
    delete source.userData.astraShared;
    delete source.userData.shared;
    // ShaderMaterial.copy uses UniformsUtils.clone, which duplicates ordinary
    // texture samplers and replaces render-target samplers with null. Borrow
    // them through the same typed copy used for registered surface patches.
    if (material.isShaderMaterial) source.uniforms = {};
    const clone = material.clone.call(source);
    const copyValue = (value) => {
        if (!value || typeof value !== 'object' || value.isTexture) return value;
        if (ArrayBuffer.isView(value)) return value.slice();
        if (Array.isArray(value)) return value.map(copyValue);
        if (typeof value.clone === 'function') return value.clone();
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copyValue(item)]));
    };
    const uniformMaps = new Map();
    const copyUniforms = (uniforms) => {
        if (shareUniforms) return uniforms;
        if (!uniformMaps.has(uniforms)) uniformMaps.set(uniforms, Object.fromEntries(
            Object.entries(uniforms).map(([key, binding]) =>
                [key, { ...binding, value: copyValue(binding.value) }])));
        return uniformMaps.get(uniforms);
    };
    if (material.isShaderMaterial) clone.uniforms = copyUniforms(material.uniforms);
    if (material.userData.uniforms) clone.userData.uniforms = copyUniforms(material.userData.uniforms);
    const patches = material.userData.astraPatches;
    if (patches?.length) {
        for (const patch of patches) patchStandard(clone, { ...patch });
    } else {
        clone.onBeforeCompile = material.onBeforeCompile;
        clone.customProgramCacheKey = material.customProgramCacheKey;
    }
    return clone;
}

/**
 * The world position and normal the surface patches shade from.
 *
 * Named once for every library that reads them, so a material wearing
 * several declares ONE pair (patchStandard drops a repeated varying);
 * each library writes them from its OWN vertex locals, because two
 * bodies declaring one local is a compile error.
 */
export const WORLD_VARYINGS = [
    'varying vec3 vAstraWorld;',
    'varying vec3 vAstraWorldN;',
].join('\n');

/**
 * The vertex body that writes WORLD_VARYINGS from locals `p` and `n`.
 *
 * `transformed` is still object-space after <begin_vertex>, so the
 * instance transform is folded in by hand, or every scattered copy is
 * shaded as though it stood at the world origin.
 */
export function worldBody(p, n) {
    return [
        `  vec4 ${p} = vec4(transformed, 1.0);`,
        `  vec3 ${n} = normal;`,
        '#ifdef USE_INSTANCING',
        `  ${p} = instanceMatrix * ${p};`,
        `  ${n} = astraNormalTransform(mat3(instanceMatrix), ${n});`,
        '#endif',
        `  vAstraWorld = (modelMatrix * ${p}).xyz;`,
        `  vAstraWorldN = astraNormalTransform(mat3(modelMatrix), ${n});`,
    ].join('\n');
}

/**
 * A library's base patch: the world varyings and their vertex body, and
 * `head` — the helpers all of that library's patches read — in the
 * fragment stage. Apply it before each of them: patchStandard replaces a
 * patch of the SAME name in place, so the base costs one body however
 * many of the library's patches a material wears.
 */
export function worldBase(name, p, n, head) {
    return {
        name,
        vertexHead: WORLD_VARYINGS,
        vertexBody: worldBody(p, n),
        fragmentHead: head ? [WORLD_VARYINGS, head].join('\n')
            : WORLD_VARYINGS,
    };
}

/** Apply a library's `base`, then the patch itself. */
export function withBase(material, base, part) {
    // A raw ShaderMaterial (the addon Water, anything from
    // makeShaderMaterial) has neither hook, so the patch is a silent
    // no-op — the one failure mode nothing else here would report.
    if (material && material.isShaderMaterial) {
        console.warn(
            part.name + ': ' + (material.name || 'material') + ' is a raw ' +
            'ShaderMaterial with no <color_fragment> hook, so this patch ' +
            'does nothing. Patch a standard-material surface instead.');
    }
    patchStandard(material, base);
    return patchStandard(material, part);
}


/**
 * Compose a roughness multiplier from several patches, in one place.
 *
 * This is the WHOLE-MATERIAL lever, and it stays the right one for a
 * look that is uniform over the surface (a wet slab, a polished floor).
 * For a look that covers only PART of it — a rust crust, a dust film —
 * `patchStandard({ roughnessBody })` now reaches per-pixel roughness
 * after `<roughnessmap_fragment>`; prefer that, because retuning the
 * material demattes the clean 70 % along with the corroded 30 %.
 *
 * Two libraries each keeping
 * their own "original" value cannot see one another: whichever runs
 * second takes the first's result as the base, and re-applying either
 * one re-bases it again. This keeps the true base and one factor per
 * named patch, so any order and any number of re-applications land on
 * the same number.
 *
 * @param {THREE.Material} material The material to retune, in place.
 * @param {string} key The patch's name, one factor per key.
 * @param {number} factor Multiplier on the material's ORIGINAL
 *   roughness; 1 removes this patch's contribution.
 * @param {number} [floor] Lower clamp (default 0.04 — a perfect mirror
 *   is never what any of these effects mean).
 * @returns {number} The resulting roughness, or NaN for a material that
 *   has none (MeshBasicMaterial and friends).
 */
export function composeRoughness(material, key, factor, floor = 0.04) {
    if (typeof material.roughness !== 'number') return NaN;
    const store = material.userData.astraRoughness
        || { base: material.roughness, factors: {} };
    store.factors[key] = factor;
    material.userData.astraRoughness = store;
    let r = store.base;
    for (const k of Object.keys(store.factors).sort()) {
        r *= store.factors[k];
    }
    // three does not clamp roughness, and every dirt/rust/dust patch
    // needs a factor above 1 — two matte patches on an already-rough
    // material compose straight past the top of the range.
    material.roughness = Math.min(1, Math.max(floor, r));
    return material.roughness;
}

/** The roughness the material was AUTHORED with, before any cover. */
export function baseRoughness(material) {
    const store = material.userData.astraRoughness;
    return store ? store.base : material.roughness;
}

/**
 * A roughening factor one patch cannot push past fully rough.
 *
 * Roughness only means anything up to 1, and the material may start
 * near it.
 */
export function matteFactor(material, add) {
    const base = baseRoughness(material);
    return base > 0 ? Math.min(1 + add, 1 / base) : 1;
}

/**
 * Move the material's gloss toward a cover's, never past it.
 *
 * Roughness is per MATERIAL here, so a cover can only say how much of
 * the surface is no longer the material: `blend` 0 leaves it alone, 1
 * hands the whole material to the cover's own roughness.
 */
export function towardRoughness(material, target, blend) {
    const base = baseRoughness(material);
    if (!(base > 0)) return 1;
    return 1 + (target / base - 1) * unit(blend);
}


/**
 * Drive every astra shader under `root` from one call in `update`.
 *
 * @param {THREE.Object3D} root Scene or group to walk.
 * @param {number} t Scene time in seconds.
 * @returns {number} How many materials were advanced.
 */
export function tickShaders(root, t) {
    let n = 0;
    root.traverse((o) => {
        for (const m of [].concat(o.material || [])) {
            if (!m) continue;
            const u = (m.uniforms && m.uniforms.uTime)
                ? m.uniforms
                : (m.userData && m.userData.uniforms);
            if (u && u.uTime) { u.uTime.value = t; n++; }
        }
    });
    return n;
}

/**
 * Give a vertex-displaced mesh a shadow that moves with it.
 *
 * Three draws directional/spot shadows with MeshDepthMaterial and point
 * shadows with MeshDistanceMaterial. Neither sees `patchStandard`: a
 * displaced surface casts the shadow of
 * its undisplaced geometry — and for the `position`-at-zero instanced
 * lattices these libraries build, that is no shadow at all. This
 * patches a depth material with the SAME head and body and shares the
 * surface's uniform map, so one `tickShaders` advances both.
 *
 * The depth stage defines FLAT_SHADED, so a body whose vNormal write
 * sits under `#ifndef FLAT_SHADED` (as every lib here does) can be
 * passed WHOLE — a depth shader has no vNormal to write.
 *
 * @param {THREE.Mesh} mesh The mesh, its surface patched already.
 * @param {string} name Cache-key name for these shadow patches.
 * @param {string} head The vertex head the body needs.
 * @param {string} body The vertex body: the displacement, or the whole
 *   body when its normal half is FLAT_SHADED-guarded.
 * @param {object} [opts] `fragmentHead` and `fragmentBody` provide the same
 *   silhouette test/discard as the surface. The body runs before the alpha
 *   test in both shadow passes. Neither owns the surface's shared textures.
 * @returns {THREE.Mesh} The same mesh, now casting.
 */
export function shadowLike(mesh, name, head, body, opts = {}) {
    // A depth pass that matches the surface EXACTLY self-shadows into
    // moire, and shadow bias belongs to a light this library cannot
    // see: the offset is the surface's own, so it travels with it.
    const settings = {
        side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: 3, polygonOffsetUnits: 8,
    };
    const depth = new THREE.MeshDepthMaterial({
        ...settings, depthPacking: THREE.RGBADepthPacking,
    });
    const distance = new THREE.MeshDistanceMaterial(settings);
    for (const material of [depth, distance]) {
        material.userData.uniforms = mesh.material.userData.uniforms;
        patchStandard(material, {
            name,
            vertexHead: '#define FLAT_SHADED 1\n' + head,
            vertexBody: body,
            fragmentHead: opts.fragmentHead || '',
            alphaBody: opts.fragmentBody || '',
        });
    }
    mesh.customDepthMaterial = depth;
    mesh.customDistanceMaterial = distance;
    mesh.castShadow = true;
    return mesh;
}

/**
 * Give every instance its own hue, scale and phase.
 *
 * Two hundred pixel-identical copies is a large part of what the judge
 * calls "primitives". This adds an `aVar` attribute (hue, scale, phase)
 * and, for built-in materials, applies the hue and scale in the shader.
 *
 * @param {THREE.InstancedMesh} mesh The instanced mesh to vary.
 * @param {object} [opts] `seed` (default 1); `hue` radians of hue swing
 *   (default 0.10); `scale` relative size swing (default 0.12);
 *   `apply` patch the material to consume it (default true).
 * @returns {THREE.InstancedMesh} The same mesh.
 */
export function instanceVariation(mesh, opts = {}) {
    // 0.10 rad is +-5.7 deg: measured across the delivered corpus
    // that reads as one albedo, because hue rotation is invisible
    // until the swing is wide enough to cross a colour name.
    const { seed = 1, hue = 0.34, scale = 0.12, apply = true } = opts;
    const count = mesh.count || 0;
    if (!count) return mesh;
    const rand = lehmer(seed);
    const data = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
        data[i * 3] = (rand() * 2 - 1) * hue;
        data[i * 3 + 1] = 1 + (rand() * 2 - 1) * scale;
        data[i * 3 + 2] = rand() * Math.PI * 2;
    }
    mesh.geometry.setAttribute(
        'aVar', new THREE.InstancedBufferAttribute(data, 3));
    if (apply && mesh.material && !mesh.material.userData.astraVaried) {
        patchStandard(mesh.material, {
            name: 'variation:' + (mesh.material.name || mesh.uuid.slice(0, 8)),
            vertexHead: 'attribute vec3 aVar;\nvarying vec3 vAstraVar;',
            vertexBody: '  vAstraVar = aVar;\n  transformed *= aVar.y;',
            fragmentHead: 'varying vec3 vAstraVar;',
            fragmentBody:
                '  diffuseColor.rgb = astraHueShift(diffuseColor.rgb,' +
                ' vAstraVar.x);',
        });
        mesh.material.userData.astraVaried = true;
    }
    return mesh;
}

/**
 * Sweep a cross-section along a path — one body, not stacked sheets.
 *
 * Parallel planes betray themselves as separate layers the moment the
 * eye is edge-on, and a single plane collapses to a line. A section
 * swept along a curve keeps its body from every angle, and the closed
 * loop naturally reads denser at the silhouette, where the surface
 * turns away.
 *
 * @param {function(number): {x: number, y: number, z: number}} path
 *   Centre of the section at t in [0, 1].
 * @param {function(number, number): {x: number, y: number, z: number}}
 *   section Offset from that centre at angle a in [0, 2pi) and the same
 *   t; return the shape of the cross-section there.
 * @param {object} [opts] `nu` points around the section (default 48),
 *   `nv` steps along the path (default 64), `t0`/`t1` path range
 *   (default 0..1).
 * @returns {THREE.BufferGeometry} With uv (u around, v along) and
 *   computed normals.
 */
export function sweepProfile(path, section, opts = {}) {
    const { nu = 48, nv = 64, t0 = 0, t1 = 1 } = opts;
    const pos = [], uv = [], idx = [];
    for (let j = 0; j <= nv; j++) {
        const t = t0 + (t1 - t0) * (j / nv);
        const c = path(t);
        for (let i = 0; i <= nu; i++) {
            const a = (i / nu) * Math.PI * 2;
            const o = section(a, t);
            pos.push(c.x + o.x, c.y + o.y, c.z + o.z);
            uv.push(i / nu, j / nv);
        }
    }
    for (let j = 0; j < nv; j++) {
        for (let i = 0; i < nu; i++) {
            const a = j * (nu + 1) + i;
            const b = a + 1;
            const c = a + nu + 1;
            idx.push(a, c, b, b, c, c + 1);
        }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
}

/**
 * A billboard geometry that survives the ambient-occlusion pass.
 *
 * `GTAOPass` redraws the scene with an override material that does NOT
 * run a custom vertex shader — it draws raw `position`. A quad kept in
 * `position` and displaced in the shader therefore sits at the world
 * origin during AO and burns a black rectangle there. Corners ride on
 * `aCorner` instead, and `position` stays at zero.
 *
 * @param {number} count Instance count.
 * @param {number} [w] Quad width. @param {number} [h] Quad height.
 * @param {number} [radius] World radius the instances cover, for
 *   CULLING only: `position` is all zeros, so without a stated sphere
 *   three drops the whole field the moment the origin leaves frame.
 *   The bounding BOX is deliberately left alone — it is what measures
 *   the asset, and a stated one would put the asset's floor at
 *   -radius.
 * @returns {THREE.InstancedBufferGeometry} With `aCorner`, `uv`
 *   and a flat `normal` — the last one for any built-in material
 *   put on this geometry, which would otherwise take NaN shadow
 *   coordinates from a zero normal.
 */
export function instancedQuad(count, w = 1, h = 1, radius = 1e4) {
    const base = new THREE.PlaneGeometry(w, h);
    const g = new THREE.InstancedBufferGeometry();
    g.index = base.index;
    g.setAttribute('position', new THREE.BufferAttribute(
        new Float32Array(base.attributes.position.count * 3), 3));
    g.setAttribute('aCorner', base.attributes.position);
    g.setAttribute('uv', base.attributes.uv);
    // A built-in material reads `normal`, and without it objectNormal is
    // zero: <shadowmap_vertex> normalizes vec3(0) and every shadow
    // coordinate on a receiving mesh comes out NaN.
    g.setAttribute('normal', base.attributes.normal);
    g.instanceCount = count;
    // Culling reads the SPHERE, and every measurement of the asset
    // reads the BOX. Stating the sphere stops three culling the field
    // the moment the origin leaves frame; leaving the box alone keeps
    // min_y and the island check honest.
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), radius);
    return g;
}

/**
 * Keep a transparent mesh out of every pass that draws with an OVERRIDE
 * material — which means out of ambient occlusion.
 *
 * GTAOPass rebuilds depth and normals by setting `scene.overrideMaterial`
 * and drawing everything, and an override material is opaque: a stack of
 * fog sheets, a curtain of rain or a shaft of light is a stack of SOLID
 * FLOORS in that buffer, occluding whatever is behind it. Measured on
 * the height fog with the bank made invisible but still present: 50/255
 * of darkening on ground the fog never touched, with the footprint's own
 * disc as a hard rim.
 *
 * The draw range is restored afterwards, so a raycast or a later frame
 * cannot inherit an empty one. Shadow passes also draw with a foreign
 * material, so a mesh guarded here stops casting too — which is right
 * for anything you would call a veil and wrong for anything you would
 * call a wall.
 *
 * @param {THREE.Object3D} obj Mesh, or a group whose meshes to guard.
 * @returns {THREE.Object3D} The same object.
 */
const overrideGuards = new WeakSet();

export function keepOutOfDepthPasses(obj) {
    obj.traverse((o) => {
        if (!o.isMesh || overrideGuards.has(o)) return;
        overrideGuards.add(o);
        o.userData.astraNoOverride = true;
        const before = o.onBeforeRender, after = o.onAfterRender;
        const ranges = [];
        // A shaft of light does not cast a shadow. Three r182 draws
        // the shadow map through its own hook, so the draw-range guard
        // below never sees it — the flag is what keeps it out.
        o.castShadow = false;
        o.onBeforeRender = function (...args) {
            before?.apply(this, args);
            const [, , , geo, material] = args;
            ranges.push({geometry:geo, start:geo.drawRange.start, count:geo.drawRange.count});
            const own = Array.isArray(this.material)
                ? this.material.includes(material) : this.material === material;
            if (!own) geo.setDrawRange(0, 0);
        };
        o.onAfterRender = function (...args) {
            const range = ranges.pop();
            if (range) range.geometry.setDrawRange(range.start, range.count);
            after?.apply(this, args);
        };
    });
    return obj;
}

// A renderer may nest captures (for example a transmission pass inside a
// reflection). Reuse one frame per nesting level without sharing live state.
const rendererStateFrames = new WeakMap();

/**
 * Run a synchronous capture and restore the caller's renderer state, including
 * active target rectangles that differ from the target's authored defaults.
 * This scopes addon render callbacks; it does not coordinate recursive mirrors
 * or recover the contents of an interrupted frame. Object visibility/matrices
 * remain the caller's responsibility. The callback must finish synchronously.
 */
export function withRendererState(renderer, callback) {
    let stack = rendererStateFrames.get(renderer);
    if (!stack) {
        stack = { depth: 0, frames: [] };
        rendererStateFrames.set(renderer, stack);
    }
    let frame = stack.frames[stack.depth];
    if (!frame) {
        frame = {
            viewport: new THREE.Vector4(), scissor: new THREE.Vector4(),
            logicalViewport: new THREE.Vector4(), logicalScissor: new THREE.Vector4(),
            targetViewport: new THREE.Vector4(), targetScissor: new THREE.Vector4(),
            clearColor: new THREE.Color(),
        };
        stack.frames.push(frame);
    }
    const gl = renderer.getContext();
    frame.target = renderer.getRenderTarget();
    frame.face = renderer.getActiveCubeFace();
    frame.mip = renderer.getActiveMipmapLevel();
    frame.xr = renderer.xr.enabled;
    frame.shadowUpdate = renderer.shadowMap.autoUpdate;
    frame.toneMapping = renderer.toneMapping;
    frame.clearAlpha = renderer.getClearAlpha();
    frame.logicalScissorTest = renderer.getScissorTest();
    frame.actualScissorTest = gl.isEnabled(gl.SCISSOR_TEST);
    renderer.getCurrentViewport(frame.viewport);
    renderer.getViewport(frame.logicalViewport);
    renderer.getScissor(frame.logicalScissor);
    frame.scissor.fromArray(gl.getParameter(gl.SCISSOR_BOX));
    renderer.getClearColor(frame.clearColor);
    stack.depth++;
    try {
        return callback();
    } finally {
        try {
            renderer.xr.enabled = frame.xr;
            renderer.shadowMap.autoUpdate = frame.shadowUpdate;
            renderer.toneMapping = frame.toneMapping;
            renderer.setClearColor(frame.clearColor, frame.clearAlpha);
            renderer.setViewport(frame.logicalViewport);
            renderer.setScissor(frame.logicalScissor);
            renderer.setScissorTest(frame.logicalScissorTest);
            if (frame.target) {
                // Rebinding normally resets active rectangles to target
                // defaults. Use the captured rectangles for this bind, then
                // return its metadata intact so Three's cache and GL agree.
                frame.targetViewport.copy(frame.target.viewport);
                frame.targetScissor.copy(frame.target.scissor);
                const targetScissorTest = frame.target.scissorTest;
                frame.target.viewport.copy(frame.viewport);
                frame.target.scissor.copy(frame.scissor);
                frame.target.scissorTest = frame.actualScissorTest;
                try {
                    renderer.setRenderTarget(frame.target, frame.face, frame.mip);
                } finally {
                    frame.target.viewport.copy(frame.targetViewport);
                    frame.target.scissor.copy(frame.targetScissor);
                    frame.target.scissorTest = targetScissorTest;
                }
            } else {
                renderer.setRenderTarget(null, frame.face, frame.mip);
                renderer.state.viewport(frame.viewport);
                renderer.state.scissor(frame.scissor);
                renderer.state.setScissorTest(frame.actualScissorTest);
            }
        } finally {
            stack.depth--;
            // Do not keep a disposed caller target alive through this pool.
            frame.target = null;
        }
    }
}

const PLANE_AXIS = new THREE.Vector3(0, 0, 1);
const PLANE_ORIGIN = new THREE.Vector3();

/**
 * Install `mesh.onBeforeRender` for a three.js planar reflection addon
 * (Reflector / Water). The capture runs once per COLOUR draw of `material`:
 * never in an override pass (GTAO, depth), a nested capture or a draw of a
 * replaced material, and never when the camera is behind the plane (unless
 * `mirror.forceUpdate`). The addon reads its plane from a rotation of
 * `mirror.matrixWorld`, which is wrong under nonuniform scale or shear, so
 * during `capture` that matrix is a RIGID frame on the true (inverse-transpose)
 * plane, then restored with the caller's descendants untouched. Renderer
 * state is scoped by withRendererState.
 *
 * capture(renderer, scene, camera, toRigid): toRigid = rigid⁻¹ · mesh.matrixWorld,
 * the factor that maps the addon's rigid-frame texture matrix back onto the
 * mesh's actual local positions. `frame`, `normal` and `point` place the
 * plane (frame-local); both default to the mesh itself and its local +Z.
 */
export function planarCapture(mesh, capture, {
    material = mesh.material, mirror = mesh, frame = mesh,
    normal = PLANE_AXIS, point = PLANE_ORIGIN, skip = null,
} = {}) {
    const actual = new THREE.Matrix4(), rigid = new THREE.Matrix4(), toRigid = new THREE.Matrix4();
    const saved = new THREE.Matrix4(), normalMatrix = new THREE.Matrix3();
    const worldNormal = new THREE.Vector3(), worldPoint = new THREE.Vector3(), eye = new THREE.Vector3();
    const rotation = new THREE.Quaternion();
    let capturing = false;
    mesh.onBeforeRender = function (renderer, scene, camera, geometry, drawMaterial) {
        if (capturing || scene.overrideMaterial || mesh.material !== material
            || (drawMaterial && drawMaterial !== material) || skip?.()) return;
        actual.copy(frame.matrixWorld);
        normalMatrix.getNormalMatrix(actual);
        worldNormal.copy(normal).applyMatrix3(normalMatrix).normalize();
        if (worldNormal.lengthSq() < 0.5) return;
        worldPoint.copy(point).applyMatrix4(actual);
        eye.setFromMatrixPosition(camera.matrixWorld);
        if (eye.sub(worldPoint).dot(worldNormal) < 0 && !mirror.forceUpdate) return;
        rotation.setFromUnitVectors(PLANE_AXIS, worldNormal);
        rigid.makeRotationFromQuaternion(rotation).setPosition(worldPoint);
        toRigid.copy(rigid).invert().multiply(mesh.matrixWorld);
        saved.copy(mirror.matrixWorld);
        const autoUpdate = mirror.matrixWorldAutoUpdate, needsUpdate = mirror.matrixWorldNeedsUpdate;
        const visible = mesh.visible, mirrorVisible = mirror.visible;
        // Nested scene updates must not propagate the temporary rigid frame
        // into caller-owned descendants (including cameras and bones).
        const descendants = [];
        for (const child of mirror.children) child.traverse((node) => {
            descendants.push([node, node.matrixWorldAutoUpdate, node.matrixWorldNeedsUpdate]);
            node.matrixWorldAutoUpdate = false;
        });
        capturing = true;
        mirror.matrixWorldAutoUpdate = false;
        mirror.matrixWorld.copy(rigid);
        mesh.visible = false;
        try {
            return withRendererState(renderer, () => capture(renderer, scene, camera, toRigid));
        } finally {
            mirror.matrixWorld.copy(saved);
            mirror.matrixWorldAutoUpdate = autoUpdate;
            mirror.matrixWorldNeedsUpdate = needsUpdate;
            for (const [node, childAutoUpdate, childNeedsUpdate] of descendants) {
                node.matrixWorldAutoUpdate = childAutoUpdate;
                node.matrixWorldNeedsUpdate = childNeedsUpdate;
            }
            mesh.visible = visible;
            mirror.visible = mirrorVisible;
            capturing = false;
        }
    };
    return mesh;
}

/**
 * A reader for the lights an effect shades itself with, reused per draw:
 * `const readLights = makeLightProbe(); const l = readLights(scene, near);`
 * Over the VISIBLE lights with intensity > 0 it returns (one reused object):
 * `sun` the directional light with the greatest linear luminance and
 * `sunDirection` the world unit
 * vector toward it (position minus target; +Y without a sun); `ambient`,
 * `sky` and `ground` the summed ambient colour and hemisphere sky/ground
 * colours, each times intensity (linear); `environment` the scene
 * environment's intensity (0 without one); and, when `near` (a world
 * Vector3) is given, `point` the point light with the greatest attenuated
 * luminance there, using Three's decay and smooth distance cutoff.
 * Black lights and lights beyond their cutoff cannot displace a visible
 * source. Each effect applies its own calibration after selection.
 */
export function makeLightProbe() {
    const probe = {
        sun: null, sunDirection: new THREE.Vector3(0, 1, 0), point: null, environment: 0,
        ambient: new THREE.Color(), sky: new THREE.Color(), ground: new THREE.Color(),
    };
    const at = new THREE.Vector3(), target = new THREE.Vector3(), scratch = new THREE.Color();
    const luminance = (color) => .2126 * color.r + .7152 * color.g + .0722 * color.b;
    return (scene, near = null) => {
        let sunScore = 0, pointScore = 0;
        probe.sun = probe.point = null;
        probe.ambient.setRGB(0, 0, 0); probe.sky.setRGB(0, 0, 0); probe.ground.setRGB(0, 0, 0);
        scene.traverseVisible((light) => {
            if (!light.isLight || !(light.intensity > 0)) return;
            const i = light.intensity;
            if (light.isDirectionalLight) {
                const score = i * luminance(light.color);
                if (score > sunScore) { sunScore = score; probe.sun = light; }
            }
            else if (light.isAmbientLight) probe.ambient.add(scratch.copy(light.color).multiplyScalar(i));
            else if (light.isHemisphereLight) {
                probe.sky.add(scratch.copy(light.color).multiplyScalar(i));
                probe.ground.add(scratch.copy(light.groundColor).multiplyScalar(i));
            } else if (near && light.isPointLight) {
                const distance = light.getWorldPosition(at).distanceTo(near);
                // Same attenuation as Three's getDistanceAttenuation: selection
                // must not prefer a nearby lamp whose range has already ended.
                const cutoff = light.distance > 0
                    ? Math.max(0, 1 - (distance / light.distance) ** 4) ** 2 : 1;
                const score = i * luminance(light.color) * cutoff
                    / Math.max(.01, distance ** light.decay);
                if (score > pointScore) { pointScore = score; probe.point = light; }
            }
        });
        probe.sunDirection.set(0, 1, 0);
        if (probe.sun) {
            probe.sun.getWorldPosition(at); probe.sun.target.getWorldPosition(target);
            if (at.sub(target).lengthSq() > 1e-12) probe.sunDirection.copy(at).normalize();
        }
        probe.environment = scene.environment ? (scene.environmentIntensity ?? 1) : 0;
        return probe;
    };
}
