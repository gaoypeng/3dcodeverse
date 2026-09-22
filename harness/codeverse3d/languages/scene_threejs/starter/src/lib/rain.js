/**
 * Rain: falling streaks, impact splashes on the surfaces the rain
 * actually hits, a wet-material conversion and a rippling puddle.
 * DOM-free (DataTexture only), seeded through mulberry32, no RTT and
 * no post pass. Delivery frames are STILLS, so every effect must read
 * FROZEN: streaks are drawn long, splash phases are spread across the
 * field so all radii show at once, and puddle ripples displace real
 * geometry. World-space — add the returned objects
 * at the scene ROOT and drive them from `tick`: `obj.userData.update(t)`.
 */

import * as THREE from 'three';
import { mulberry32, fbm2, noiseDataTexture } from './noise.js';
import { makeShaderMaterial, keepOutOfDepthPasses } from './shader.js';

const _UP = new THREE.Vector3(0, 1, 0);

/**
 * The scene's own air, read where every shader here already has it: the
 * fog uniforms three refreshes each frame. Water is colourless — streak,
 * ring and crown all show you the sky — so a hardcoded tint puts
 * steel-blue rain in a golden dusk. `rainAirHue` takes the air's HUE at
 * the caller's own luminance; `rainAirLit` its brightness, on a fourth
 * root (a night scene is ~140x darker in linear light, and a plain
 * multiply would delete the effect rather than dim it). `fogColor`
 * exists only under USE_FOG: no fog, no change.
 */
const _AIR_GLSL = [
  'vec3 rainAirHue(vec3 c, float k) {',
  '#ifdef USE_FOG',
  '  float l = max(dot(fogColor, vec3(0.2126, 0.7152, 0.0722)), 1e-4);',
  '  return mix(c, c * (fogColor / l), k);',
  '#else',
  '  return c;',
  '#endif',
  '}',
  'float rainAirLit(float dim) {',
  '#ifdef USE_FOG',
  '  float a = max(dot(fogColor, vec3(0.2126, 0.7152, 0.0722)), 0.0);',
  '  return mix(dim, 1.0, clamp(pow(a, 0.25), 0.0, 1.0));',
  '#else',
  '  return 1.0;',
  '#endif',
  '}',
].join('\n');

/**
 * Build the instanced quad geometry every effect here draws with.
 *
 * `position` is deliberately ZERO and the corners travel in `aCorner`:
 * the render chain's GTAO pass re-draws the scene with an override
 * material that ignores these shaders, and a real unit quad then
 * printed a black AO slab at the world origin.
 *
 * @param {number} count Instances.
 * @returns {THREE.InstancedBufferGeometry} Degenerate quad with
 *   `aCorner` + `uv`, instanceCount set.
 */
function _quads(count) {
  const geo = new THREE.PlaneGeometry(1, 1);
  const verts = geo.attributes.position.count;
  const inst = new THREE.InstancedBufferGeometry();
  inst.index = geo.index;
  inst.setAttribute('position',
      new THREE.BufferAttribute(new Float32Array(verts * 3), 3));
  inst.setAttribute('aCorner', geo.attributes.position);
  inst.attributes.uv = geo.attributes.uv;
  inst.instanceCount = count;
  return inst;
}

/**
 * Falling rain as ONE instanced mesh of camera-facing streaks.
 *
 * Each instance is a quad stretched along the fall vector and rolled
 * to face the camera, which is what makes rain legible in a still: a
 * frozen drop is a point, a frozen STREAK is rain. The field wraps
 * modulo a box that follows the camera, so any authored or orbit
 * camera sits inside the weather without the scene knowing where it is.
 *
 * @param {object} [opts]
 *   `seed` (default 5) mulberry32 seed;
 *   `count` (default 7000) streaks — one draw call regardless. The
 *     box is small on purpose: density where the camera is, not a
 *     thin sprinkle across a stadium;
 *   `radius` (default 16) half-extent in X/Z of the wrap box in
 *     metres — streaks fade out over its last 45%;
 *   `height` (default 18) its Y extent;
 *   `speed` (default 14) fall speed in m/s (drives lean and drift);
 *   `length` (default 0.6) streak length in metres — this is the
 *     "exposure", the single strongest still-frame knob;
 *   `width` (default 0.014) streak width in metres;
 *   `wind` (default [1.4, 0]) horizontal drift [x, z] in m/s, which
 *     also tilts the streaks off vertical;
 *   `color` (default 0xdbe6f2) streak tint at the HEAD of the streak;
 *   `rimColor` (default 0x0b111a) the tail tint — that dark trail is
 *     the only thing that prints rain on a sky the tone map has already
 *     pushed to 0.94;
 *   `air` (default 0.55) how far both tints follow the scene's fog hue
 *     (0 keeps `color` exactly): a golden dusk gets golden rain;
 *   `opacity` (default 0.42) peak alpha before distance fade;
 *   `follow` (default true) wrap the box around the camera; false pins
 *     it to `center`;
 *   `center` (default [0, height / 2, 0]) box centre when not following.
 * @returns {THREE.Mesh} Mesh named 'Rainfall', transparent and
 *   non-shadowing, with `userData.update(t)` advancing the fall.
 */
export function makeRain(opts = {}) {
  const seed = opts.seed === undefined ? 5 : opts.seed;
  const count = opts.count === undefined ? 7000 : opts.count;
  const radius = opts.radius === undefined ? 16 : opts.radius;
  const height = opts.height === undefined ? 18 : opts.height;
  const speed = opts.speed === undefined ? 14 : opts.speed;
  const length = opts.length === undefined ? 0.6 : opts.length;
  const width = opts.width === undefined ? 0.014 : opts.width;
  const wind = opts.wind || [1.4, 0];
  const opacity = opts.opacity === undefined ? 0.42 : opts.opacity;
  const follow = opts.follow === false ? 0 : 1;
  const center = opts.center || [0, height * 0.5, 0];

  const rand = mulberry32(seed);
  const inst = _quads(count);
  const off = new Float32Array(count * 3);
  const ext = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    off[i * 3] = (rand() - 0.5) * 2 * radius;
    off[i * 3 + 1] = rand() * height;
    off[i * 3 + 2] = (rand() - 0.5) * 2 * radius;
    ext[i * 3] = 0.55 + rand() * 0.6;       // length scale
    ext[i * 3 + 1] = 0.45 + rand() * 0.55;  // brightness
    // Hue jitter: a field of one tint is a hatch pattern.
    ext[i * 3 + 2] = rand();
  }
  inst.setAttribute('iOff', new THREE.InstancedBufferAttribute(off, 3));
  inst.setAttribute('iExtra', new THREE.InstancedBufferAttribute(ext, 3));

  const mat = makeShaderMaterial({
    name: 'Rainfall',
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    uniforms: {
      // Declared and bound EXPLICITLY here and in both splash shaders:
      // shader.js injects both, but glsl_audit.mjs reads the MODULE's
      // source, and uTime undeclared there is two hard ERRORS in the
      // scene build's preflight — every scene importing this file.
      uTime: { value: 0 },
      uVel: { value: new THREE.Vector3(wind[0], -speed, wind[1]) },
      uBox: { value: new THREE.Vector3(radius * 2, height, radius * 2) },
      uCenter: { value: new THREE.Vector3(...center) },
      uFollow: { value: follow },
      uLen: { value: length },
      uWidth: { value: width },
      uFar: { value: radius },
      uColor: {
        value: new THREE.Color(
            opts.color === undefined ? 0xdbe6f2 : opts.color),
      },
      uRim: {
        value: new THREE.Color(
            opts.rimColor === undefined ? 0x0b111a : opts.rimColor),
      },
      uAir: { value: opts.air === undefined ? 0.55 : opts.air },
      uOpacity: { value: opacity },
    },
    varyings: 'varying vec2 vUv; varying float vFade; varying float vJit;',
    vertexHead: [
      'attribute vec3 aCorner;',
      'attribute vec3 iOff; attribute vec3 iExtra;',
      'uniform float uTime;',
      'uniform vec3 uVel; uniform vec3 uBox;',
      'uniform vec3 uCenter; uniform float uFollow;',
      'uniform float uLen; uniform float uWidth; uniform float uFar;',
    ].join('\n'),
    vertexMain: [
      '  vUv = uv; vJit = iExtra.z;',
      '  vec3 anchor = mix(uCenter, cameraPosition, uFollow);',
      // Wrap the whole field into a box centred on the anchor: rain is
      // everywhere the camera is, for a fixed instance budget.
      '  vec3 p = mod(iOff + uVel * uTime - anchor + uBox * 0.5, uBox)',
      '      - uBox * 0.5 + anchor;',
      '  vec3 axis = normalize(uVel);',
      '  vec3 toCam = p - cameraPosition;',
      '  float dist = length(toCam);',
      '  vec3 side = cross(axis, toCam / max(dist, 1e-4));',
      '  float sl = length(side);',
      '  side = sl > 1e-4 ? side / sl : vec3(1.0, 0.0, 0.0);',
      '  transformed = p + axis * (aCorner.y * uLen * iExtra.x)',
      '      + side * (aCorner.x * uWidth);',
      '  vFade = iExtra.y * smoothstep(1.0, 6.0, dist)',
      '      * (1.0 - smoothstep(uFar * 0.55, uFar, dist));',
    ].join('\n'),
    fragmentHead: [
      'uniform vec3 uColor; uniform vec3 uRim;',
      'uniform float uOpacity; uniform float uAir;',
      _AIR_GLSL,
    ].join('\n'),
    fragmentMain: [
      '  float core = smoothstep(0.0, 0.55,',
      '      1.0 - abs(vUv.x - 0.5) * 2.0);',
      // v = 1 is the leading end (the axis points down): brightest
      // there, tapered at both tips so the quad never reads as a bar.
      '  float ends = smoothstep(0.0, 0.28, vUv.y)',
      '      * (1.0 - smoothstep(0.70, 1.0, vUv.y));',
      // HEAD BRIGHT, TAIL DARK — along the LENGTH: a streak is ~2 px
      // wide, so a split across its width averages back to one tone in
      // a single pixel. The head prints on anything dark, the tail is
      // all that prints on a 0.94 sky (3/255 pale, 5-86 dark).
      '  vec3 tint = uColor * mix(vec3(0.90, 0.97, 1.12),',
      '      vec3(1.10, 1.00, 0.88), vJit);',
      '  vec3 c = mix(rainAirHue(uRim, uAir), rainAirHue(tint, uAir),',
      '      smoothstep(0.20, 0.92, vUv.y));',
      '  float a = core * ends * (0.62 + 0.38 * vUv.y)',
      '      * vFade * uOpacity;',
      // A 0.6 m ramp bands into flat segments on a clean sky; dither.
      '  a *= 0.97 + 0.06 * astraHash21(gl_FragCoord.xy);',
      '  if (a < 0.004) discard;',
      '  gl_FragColor = vec4(c, a);',
    ].join('\n'),
  });

  const mesh = new THREE.Mesh(inst, mat);
  mesh.name = 'Rainfall';
  mesh.frustumCulled = false;  // positions live in the shader
  mesh.renderOrder = 4;        // in front of splashes and puddles
  mesh.userData.update = (t) => { mat.uniforms.uTime.value = t; };
  return keepOutOfDepthPasses(mesh);
}

/**
 * Bake the splash crown alpha map — a starburst of thin rays rising
 * from the bottom-centre impact point, with droplets at their tips.
 *
 * @param {number} [size] Cell width and height in px (default 64).
 * @param {number} [variants] Grid side of an ATLAS of DIFFERENT crowns
 *   (default 1 — one crown, the whole texture). 2 bakes a 2x2 sheet of
 *   four, each with its own ray count, angles and rim shape;
 *   `makeSplashes` picks one per impact, which is what stops a field of
 *   500 splashes reading as one stamp repeated 500 times. Every cell is
 *   transparent at its own border, so mips do not bleed across cells.
 * @returns {THREE.DataTexture} White RGB, crowns in alpha,
 *   `size * variants` square.
 */
export function splashTexture(size = 64, variants = 1) {
  const n = Math.max(1, Math.round(variants));
  const W = size * n;
  const data = new Uint8Array(W * W * 4);
  for (let cell = 0; cell < n * n; cell++) {
    const rand = mulberry32(31 + cell * 977);
    const rays = [];
    // 6..9 rays: the count is half of what makes two crowns look unlike.
    const nRays = 6 + Math.floor(rand() * 4);
    for (let k = 0; k < nRays; k++) {
      const a = Math.PI * (0.2 + 0.6 * (k + rand() * 0.6) / nRays);
      rays.push([Math.cos(a), Math.sin(a), 0.34 + rand() * 0.32]);
    }
    const rw = 0.30 + rand() * 0.09;   // rim half-width
    const rh = 0.16 + rand() * 0.06;   // rim height
    const cx0 = (cell % n) * size;
    const cy0 = Math.floor(cell / n) * size;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const px = (x / size - 0.5) * 2;
        const py = y / size;
        // The crown itself: an elliptical rim of water thrown up around
        // the impact. Without it the rays read as a firework, not a
        // drop.
        const e = Math.hypot(px / rw, py / rh);
        let a = 0.85 * Math.exp(-((e - 1) ** 2) / 0.05)
            * Math.min(1, py / 0.03);
        for (const [dx, dy, len] of rays) {
          const t = px * dx + py * dy;
          if (t <= 0 || t >= len) continue;
          const d = Math.abs(px * dy - py * dx);
          const w = 0.032 * (1 - 0.5 * t / len);
          const ray = 0.85 * Math.exp(-(d * d) / (w * w)) * (1 - t / len);
          const tip = 0.8 * Math.exp(
              -((px - dx * len) ** 2 + (py - dy * len) ** 2) / 0.0009);
          a = Math.max(a, ray + tip);
        }
        // The whole crown rises OUT of the surface, rays included: only
        // the rim faded in at the contact line, so the bottom pixel row
        // held alpha 34 under the impact — a hard stub, and on an atlas
        // a mip-level leak into the cell below.
        const i = ((cy0 + y) * W + cx0 + x) * 4;
        data[i] = data[i + 1] = data[i + 2] = 255;
        data[i + 3] = Math.round(
            255 * Math.max(0, Math.min(1, a * Math.min(1, py / 0.05))));
      }
    }
  }
  const tex = new THREE.DataTexture(data, W, W, THREE.RGBAFormat);
  // DataTexture defaults to NearestFilter with no mipmaps; a 64px crown
  // seen 20 m away then stair-steps into a cluster of hard blocks.
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Sample impact points: raycast straight down onto real surfaces and
 * fall back to the ground plane where nothing is in the way.
 *
 * @param {object} opts Same surface/area options as `makeSplashes`.
 * @param {() => number} rand Seeded PRNG.
 * @returns {Array<{p: THREE.Vector3, n: THREE.Vector3}>} World-space
 *   points already lifted off their surface, with world normals.
 */
function _impactPoints(opts, rand) {
  // A number is truthy, so `area: 12` used to read x/w as
  // undefined and every impact landed at NaN — 520 of 780
  // offsets non-finite, with 260 instances still drawn.
  const area = typeof opts.area === 'number'
      ? { x: 0, z: 0, w: opts.area, d: opts.area }
      : (opts.area || { x: 0, z: 0, w: 40, d: 40 });
  const y = opts.y === undefined ? 0 : opts.y;
  const ground = opts.ground !== false;
  const surfaces = opts.surfaces || [];
  const count = opts.count === undefined ? 260 : opts.count;
  const lift = opts.lift === undefined ? 0.012 : opts.lift;
  const bias = opts.surfaceBias === undefined ? 0.5 : opts.surfaceBias;
  const minNy = Math.cos(THREE.MathUtils.degToRad(
      opts.maxSlopeDeg === undefined ? 65 : opts.maxSlopeDeg));

  const box = new THREE.Box3();
  for (const s of surfaces) {
    s.updateWorldMatrix(true, true);
    box.expandByObject(s);
  }
  const hasBox = surfaces.length > 0 && !box.isEmpty();
  const top = hasBox ? Math.max(box.max.y, y) + 1 : y + 1;

  const ray = new THREE.Raycaster(
      new THREE.Vector3(), new THREE.Vector3(0, -1, 0));
  const nm = new THREE.Matrix3();
  const out = [];
  // Rejections (steep faces, undersides) re-sample; the budget stops a
  // scene whose only "surface" is a wall from looping forever.
  const attempts = Math.max(1, count) * 8;
  for (let a = 0; a < attempts && out.length < count; a++) {
    let x, z;
    // Aim a share of the samples INSIDE the surfaces' footprint, or a
    // car in a 40 m plaza collects three drops and reads as dry.
    if (hasBox && rand() < bias) {
      x = box.min.x + rand() * (box.max.x - box.min.x);
      z = box.min.z + rand() * (box.max.z - box.min.z);
    } else {
      x = area.x + (rand() - 0.5) * area.w;
      z = area.z + (rand() - 0.5) * area.d;
    }
    let hit = null;
    if (surfaces.length) {
      ray.ray.origin.set(x, top, z);
      for (const h of ray.intersectObjects(surfaces, true)) {
        if (h.face) { hit = h; break; }
      }
    }
    if (hit) {
      nm.getNormalMatrix(hit.object.matrixWorld);
      const n = hit.face.normal.clone().applyMatrix3(nm).normalize();
      if (n.y < minNy) continue;
      out.push({ p: hit.point.clone().addScaledVector(n, lift), n });
    } else if (ground) {
      out.push({ p: new THREE.Vector3(x, y + lift, z), n: _UP.clone() });
    }
  }
  return out;
}

/**
 * Splashes where the rain lands: an expanding ring flat on each
 * surface plus a crown of spray standing up off it.
 *
 * Pass `surfaces` and the impacts are RAYCAST onto them, so drops land
 * on the car roof, the bonnet and the umbrella at their real heights
 * and tilts; everything the rays miss falls through to the `y` ground
 * plane. Per-instance phases are spread over the whole cycle, so one
 * frozen frame shows rings at every radius — that is what reads as
 * continuous rainfall rather than one synchronized pulse.
 *
 * @param {object} [opts]
 *   `seed` (default 11) mulberry32 seed;
 *   `count` (default 260) impacts;
 *   `surfaces` (default none) meshes/groups to raycast onto;
 *   `surfaceBias` (default 0.5) share of samples aimed inside the
 *     surfaces' footprint instead of `area`;
 *   `area` (default `{x: 0, z: 0, w: 40, d: 40}`) ground sampling rect;
 *   `y` (default 0) ground plane height;
 *   `ground` (default true) let misses land on that plane;
 *   `maxSlopeDeg` (default 65) reject faces tilted past this — rain
 *     does not splash on walls or undersides;
 *   `size` (default 0.26) ring diameter in metres at full expansion;
 *   `rate` (default 2.2) splash cycles per second;
 *   `color` (default 0xe6eef7) ring and crown tint — thrown water is
 *     colourless, so it takes the scene's fog hue and dims with it;
 *   `air` (default 0.6) how far the tint follows that hue;
 *   `dim` (default 0.4) what it keeps in a black scene, on a fourth
 *     root — half, not 1/140th (it had white-hot crowns before);
 *   `variants` (default 2) grid side of the crown atlas (4 crowns),
 *     mirrored per instance for 8 apparent stamps;
 *   `opacity` (default 0.6) peak ring alpha;
 *   `crowns` (default true) add the upright spray billboards;
 *   `crownScale` (default 1) multiplies crown size relative to `size`;
 *   `lift` (default 0.012) metres pushed along the normal (z-fighting).
 * @returns {THREE.Group} Group named 'RainSplashes' holding
 *   'SplashRings' (+ 'SplashCrowns'), world-space — add it at the
 *   scene ROOT — with `userData.update(t)` driving both.
 */
export function makeSplashes(opts = {}) {
  const rand = mulberry32(opts.seed === undefined ? 11 : opts.seed);
  const size = opts.size === undefined ? 0.26 : opts.size;
  const rate = opts.rate === undefined ? 2.2 : opts.rate;
  const opacity = opts.opacity === undefined ? 0.6 : opts.opacity;
  const color = new THREE.Color(
      opts.color === undefined ? 0xe6eef7 : opts.color);
  const group = new THREE.Group();
  group.name = 'RainSplashes';

  const pts = _impactPoints(opts, rand);
  if (!pts.length) {
    group.userData.update = () => {};
    return group;
  }

  const n = pts.length;
  const variants = Math.max(1, Math.round(
      opts.variants === undefined ? 2 : opts.variants));
  const off = new Float32Array(n * 3);
  const nrm = new Float32Array(n * 3);
  const tan = new Float32Array(n * 3);
  const ext = new Float32Array(n * 4);
  const t = new THREE.Vector3();
  pts.forEach((q, i) => {
    off[i * 3] = q.p.x; off[i * 3 + 1] = q.p.y; off[i * 3 + 2] = q.p.z;
    nrm[i * 3] = q.n.x; nrm[i * 3 + 1] = q.n.y; nrm[i * 3 + 2] = q.n.z;
    t.set(0, 0, 1).cross(q.n);
    if (t.lengthSq() < 1e-8) t.set(1, 0, 0);
    t.normalize();
    tan[i * 3] = t.x; tan[i * 3 + 1] = t.y; tan[i * 3 + 2] = t.z;
    ext[i * 4] = size * (0.65 + rand() * 0.7);
    ext[i * 4 + 1] = rand();  // phase: spread so a still shows all radii
    // Hue and the crown's mirror flip; the cell is drawn separately so
    // the two never correlate into a pattern.
    ext[i * 4 + 2] = rand();
    ext[i * 4 + 3] = Math.floor(rand() * variants * variants);
  });
  const attrs = (geom) => {
    geom.setAttribute('iOff', new THREE.InstancedBufferAttribute(off, 3));
    geom.setAttribute('iNrm', new THREE.InstancedBufferAttribute(nrm, 3));
    geom.setAttribute('iTan', new THREE.InstancedBufferAttribute(tan, 3));
    geom.setAttribute('iExtra',
        new THREE.InstancedBufferAttribute(ext, 4));
    return geom;
  };
  const air = opts.air === undefined ? 0.6 : opts.air;
  const dim = opts.dim === undefined ? 0.4 : opts.dim;

  const ringMat = makeShaderMaterial({
    name: 'SplashRings',
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    uniforms: {
      uTime: { value: 0 },   // see makeRain: the audit reads this file
      uRate: { value: rate },
      uColor: { value: color },
      uOpacity: { value: opacity },
      uAir: { value: air },
      uDim: { value: dim },
    },
    varyings: [
      'varying vec2 vUv; varying float vPhase; varying float vJit;',
    ].join('\n'),
    vertexHead: [
      'attribute vec3 aCorner;',
      'attribute vec3 iOff; attribute vec3 iNrm; attribute vec3 iTan;',
      'attribute vec4 iExtra;',
    ].join('\n'),
    vertexMain: [
      '  vUv = uv; vPhase = iExtra.y; vJit = iExtra.z;',
      '  vec3 b = cross(iNrm, iTan);',
      '  transformed = iOff + iTan * (aCorner.x * iExtra.x)',
      '      + b * (aCorner.y * iExtra.x);',
    ].join('\n'),
    fragmentHead: [
      'uniform float uTime; uniform float uRate;',
      'uniform vec3 uColor; uniform float uOpacity;',
      'uniform float uAir; uniform float uDim;',
      _AIR_GLSL,
    ].join('\n'),
    fragmentMain: [
      '  float r = length(vUv * 2.0 - 1.0);',
      '  if (r > 1.0) discard;',
      '  float k = fract(vPhase + uTime * uRate);',
      // Two crests, the trailing one fainter: one lone circle reads as
      // a decal, a pair reads as a wave leaving the impact.
      '  float w = 0.09 + 0.15 * k;',
      '  float f = (r - k) / w;',
      '  float g = (r - k * 0.55) / (w * 0.8);',
      '  float a = (exp(-f * f) + 0.45 * exp(-g * g))',
      '      * (1.0 - k) * (1.0 - smoothstep(0.72, 1.0, r))',
      '      * uOpacity;',
      // Two gaussians on flat road band; a fraction of a step hides it.
      '  a *= 0.96 + 0.08 * astraHash21(gl_FragCoord.xy);',
      '  if (a < 0.004) discard;',
      // A sheet of water lifting off the ground shows you the sky.
      '  vec3 c = rainAirHue(uColor, uAir) * rainAirLit(uDim)',
      '      * (0.88 + 0.24 * vJit);',
      '  gl_FragColor = vec4(c, a);',
    ].join('\n'),
  });
  const rings = new THREE.Mesh(attrs(_quads(n)), ringMat);
  rings.name = 'SplashRings';
  rings.frustumCulled = false;
  rings.renderOrder = 2;
  group.add(rings);

  let crownMat = null;
  if (opts.crowns !== false) {
    crownMat = makeShaderMaterial({
      name: 'SplashCrowns',
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      uniforms: {
        uTime: { value: 0 },   // see makeRain
        uRate: { value: rate },
        uColor: { value: color },
        uScale: {
          value: (opts.crownScale === undefined ? 1 : opts.crownScale)
              * 0.7,
        },
        uAtlas: { value: variants },
        uAir: { value: air },
        uDim: { value: dim },
        map: { value: splashTexture(64, variants) },
      },
      varyings: [
        'varying vec2 vUv; varying float vK;',
        'varying float vJit; varying float vCell;',
      ].join('\n'),
      vertexHead: [
        'attribute vec3 aCorner;',
        'attribute vec3 iOff; attribute vec3 iNrm;',
        'attribute vec4 iExtra;',
        'uniform float uTime; uniform float uRate; uniform float uScale;',
      ].join('\n'),
      vertexMain: [
        '  vUv = uv; vJit = iExtra.z; vCell = iExtra.w;',
        '  float k = fract(iExtra.y + uTime * uRate);',
        '  vK = k;',
        // Crowns pop fast and settle: sqrt growth, so a frozen frame
        // catches most of them already open rather than as specks.
        '  float s = iExtra.x * uScale * (0.3 + 0.9 * sqrt(k));',
        '  vec3 up = iNrm;',
        '  vec3 view = normalize(iOff - cameraPosition);',
        '  vec3 right = cross(up, view);',
        '  float rl = length(right);',
        '  right = rl > 1e-4 ? right / rl : vec3(1.0, 0.0, 0.0);',
        '  transformed = iOff + right * (aCorner.x * s)',
        '      + up * ((aCorner.y + 0.5) * s);',
      ].join('\n'),
      fragmentHead: [
        'uniform sampler2D map; uniform vec3 uColor;',
        'uniform float uAtlas; uniform float uAir; uniform float uDim;',
        _AIR_GLSL,
      ].join('\n'),
      fragmentMain: [
        // ONE stamp repeated 500 times is the tell of an instanced
        // field, and a crown is a shape the eye knows. Mirror per
        // instance, then pick an atlas crown: 4 x 2 handednesses.
        '  vec2 cuv = vec2(mix(vUv.x, 1.0 - vUv.x, step(0.5, vJit)),',
        '      vUv.y);',
        '  float cell = floor(vCell + 0.5);',
        '  vec2 g = vec2(mod(cell, uAtlas), floor(cell / uAtlas));',
        '  float a = texture2D(map, (cuv + g) / uAtlas).a',
        '      * pow(1.0 - vK, 1.6) * smoothstep(0.0, 0.06, vK);',
        '  if (a < 0.01) discard;',
        // Spray is water in the air: the sky's hue, the sky's light —
        // left white it was the brightest thing in a night frame.
        '  vec3 c = rainAirHue(uColor, uAir) * rainAirLit(uDim)',
        '      * (0.86 + 0.28 * fract(vJit * 7.3));',
        '  gl_FragColor = vec4(c, a);',
      ].join('\n'),
    });
    const crowns = new THREE.Mesh(attrs(_quads(n)), crownMat);
    crowns.name = 'SplashCrowns';
    crowns.frustumCulled = false;
    crowns.renderOrder = 3;
    group.add(crowns);
  }

  group.userData.update = (t2) => {
    ringMat.uniforms.uTime.value = t2;
    if (crownMat) crownMat.uniforms.uTime.value = t2;
  };
  return keepOutOfDepthPasses(group);
}

/**
 * Wet a material (or every material under an object) IN PLACE: darker
 * albedo, near-mirror roughness, stronger environment reflection.
 *
 * That triple is what "it has been raining" looks like — a wet surface
 * absorbs more light AND reflects the sky harder at once, so darkening
 * alone just reads as a repaint. Mutates in place like
 * `materials.weather()`, so a material SHARED with dry objects wets
 * them too — clone first when that matters.
 *
 * @param {THREE.Material|THREE.Object3D} target Material, or an object
 *   whose meshes' materials are all wetted (each one once).
 * @param {number} [amount] 0..1 soak, default 0.7.
 * @param {object} [opts] `minRoughness` (default 0.1) the wettest a
 *   surface may get; `darken` (default 0.45) peak albedo loss;
 *   `envGain` (default 0.6) peak envMapIntensity boost — three scales
 *   the DIFFUSE irradiance with it too, so a big gain LIGHTS the object
 *   up: at 1.2 the wet car roof measured 0.613 against a 0.568 dry
 *   control and read as white plastic. Keep `(1 - darken) * (1 +
 *   envGain)` under 1; `roughFactor` (default 0.25) share of its own
 *   roughness a surface keeps — a soaked brick is a damp sheen, and one
 *   flat floor made brick, dirt and paint identical; `floor` (default
 *   0.02) darkest albedo a soak may leave, in linear light (a wet tyre
 *   sat at 0.0006 of a night frame: a hole, not a surface).
 * @returns {THREE.Material|THREE.Object3D} The same target.
 */
export function wetten(target, amount = 0.7, opts = {}) {
  const k = Math.max(0, Math.min(1, amount));
  const minRough = opts.minRoughness === undefined ? 0.1
                                                   : opts.minRoughness;
  const darken = opts.darken === undefined ? 0.45 : opts.darken;
  const envGain = opts.envGain === undefined ? 0.6 : opts.envGain;
  const roughFactor = opts.roughFactor === undefined ? 0.25
                                                     : opts.roughFactor;
  const floor = opts.floor === undefined ? 0.02 : opts.floor;

  const wetOne = (mat) => {
    if (!mat) return;
    if (mat.color) {
      mat.color.multiplyScalar(1 - darken * k);
      // A film makes a colour READ deeper, not just darker: it kills
      // the diffuse haze washing the pigment out. A scalar multiply
      // alone is a neutral-density filter.
      mat.color.offsetHSL(0, 0.06 * k, 0);
      const m = Math.max(mat.color.r, mat.color.g, mat.color.b);
      if (m > 1e-6 && m < floor) mat.color.multiplyScalar(floor / m);
    }
    if (typeof mat.roughness === 'number') {
      const wet = Math.max(minRough, mat.roughness * roughFactor);
      mat.roughness = mat.roughness + (wet - mat.roughness) * k;
    }
    if (typeof mat.envMapIntensity === 'number') {
      mat.envMapIntensity *= 1 + envGain * k;
    }
    // Water fills the micro-relief it is sitting in.
    if (typeof mat.bumpScale === 'number') mat.bumpScale *= 1 - 0.6 * k;
    if (mat.isMeshPhysicalMaterial) {
      mat.clearcoat = Math.max(mat.clearcoat || 0, k);
      mat.clearcoatRoughness = Math.min(
          mat.clearcoatRoughness === undefined ? 1 : mat.clearcoatRoughness,
          0.04 + 0.1 * (1 - k));
    }
    mat.needsUpdate = true;
  };

  if (target && target.isMaterial) {
    wetOne(target);
    return target;
  }
  if (target && target.isObject3D) {
    const seen = new Set();
    target.traverse((o) => {
      if (!o.isMesh || !o.material) return;
      for (const m of (Array.isArray(o.material) ? o.material
                                                 : [o.material])) {
        if (m && !seen.has(m.uuid)) { seen.add(m.uuid); wetOne(m); }
      }
    });
  }
  return target;
}

/**
 * Standing water with rain ripples — concentric wavefronts spreading
 * from drops that keep landing in new places.
 *
 * The ripples are REAL vertex displacement, not a normal map, so the
 * scene's own lights and environment carry them; a still frame catches
 * several rings mid-expansion because the drops are phase-staggered.
 * Crest vertices are also brightened through vertex colours — and
 * TINTED with them, cool on the crests that tip toward the sky, warm in
 * the troughs that show the bed — so the rings stay legible under flat
 * overcast light and the pool never reads as one flat hue.
 *
 * @param {number} w Puddle width (X) in metres.
 * @param {number} d Puddle depth (Z) in metres.
 * @param {object} [opts]
 *   `seed` (default 3) mulberry32 seed;
 *   `segments` (default: derived as 4 samples per wavelength, 24-96)
 *     grid resolution per axis — a ripple the mesh cannot resolve
 *     flattens into an invisible one, so raise `wavelength` rather
 *     than the cap on a large pool;
 *   `drops` (default 8) simultaneous ripple sources;
 *   `amplitude` (default 0.006) crest height in metres;
 *   `wavelength` (default 0.3) metres between crests;
 *   `speed` (default 0.7) wavefront speed in m/s;
 *   `life` (default 2.6) seconds a ripple lives before its drop
 *     relands elsewhere;
 *   `color` (default 0x121820) water tint (dark: depth IS the darkness);
 *   `roughness` (default 0.07) low so the sky reflects;
 *   `mask` (default true) fade the rectangle out with a seeded fBm
 *     alpha so the puddle has an organic shoreline.
 * @returns {THREE.Mesh} Mesh named 'Puddle' lying at y = 0 (position it
 *   just above the road), with `userData.update(t)` driving the ripples;
 *   already stepped to t = 0, so it is never flat.
 */
export function makePuddle(w, d, opts = {}) {
  const seed = opts.seed === undefined ? 3 : opts.seed;
  const drops = opts.drops === undefined ? 8 : opts.drops;
  const amp = opts.amplitude === undefined ? 0.006 : opts.amplitude;
  const lambda = opts.wavelength === undefined ? 0.3 : opts.wavelength;
  // Four samples per wavelength or the rings alias flat; capped
  // because every vertex is re-evaluated on the CPU each update.
  const seg = opts.segments === undefined
      ? Math.max(24, Math.min(96, Math.round(Math.max(w, d) * 4 / lambda)))
      : opts.segments;
  const speed = opts.speed === undefined ? 0.7 : opts.speed;
  const life = opts.life === undefined ? 2.6 : opts.life;

  const geom = new THREE.PlaneGeometry(w, d, seg, seg);
  geom.rotateX(-Math.PI / 2);
  const pos = geom.attributes.position;
  const col = new Float32Array(pos.count * 3).fill(1);
  geom.setAttribute('color', new THREE.BufferAttribute(col, 3));

  const mat = new THREE.MeshStandardMaterial({
    color: opts.color === undefined ? 0x121820 : opts.color,
    roughness: opts.roughness === undefined ? 0.07 : opts.roughness,
    metalness: 0,  // water is a dielectric; metalness makes dirty chrome
    vertexColors: true,
    envMapIntensity: 1.6,
  });
  if (opts.mask !== false) {
    mat.transparent = true;
    mat.alphaMap = noiseDataTexture(128, (u, v) => {
      const r = Math.hypot(u - 0.5, v - 0.5) * 2;
      const k = 1 - r + 0.42 * fbm2(u * 3.4, v * 3.4, { seed });
      return Math.max(0, Math.min(1, (k - 0.18) * 3.2));
    });
  }

  // Each source relands every `life` seconds; the cycle index seeds
  // where, so the puddle is not eight fixed drip points forever.
  const offs = [];
  const rand = mulberry32(seed);
  for (let i = 0; i < drops; i++) offs.push(rand());
  const cache = offs.map(() => ({ cyc: NaN, x: 0, z: 0, r: 0, fade: 0 }));
  const sigma = lambda * 1.1;
  const k2 = (Math.PI * 2) / lambda;

  const update = (t) => {
    for (let i = 0; i < drops; i++) {
      const phase = t / life + offs[i];
      const cyc = Math.floor(phase);
      const c = cache[i];
      if (c.cyc !== cyc) {
        const r = mulberry32((seed + i * 7919 + cyc * 104729) | 0);
        c.cyc = cyc;
        c.x = (r() - 0.5) * w;
        c.z = (r() - 0.5) * d;
      }
      c.r = (phase - cyc) * life * speed;   // wavefront radius
      c.fade = 1 - (phase - cyc);
    }
    for (let vi = 0; vi < pos.count; vi++) {
      const x = pos.getX(vi), z = pos.getZ(vi);
      // Wind chop: rain-struck water is never glassy between drops.
      let h = amp * 0.22 * Math.sin(x * 11.3 + t * 3.1) *
          Math.sin(z * 9.7 - t * 2.6);
      for (let i = 0; i < drops; i++) {
        const c = cache[i];
        const dist = Math.hypot(x - c.x, z - c.z);
        const f = dist - c.r;
        if (f > 3 * sigma || f < -3 * sigma) continue;
        h += amp * Math.cos(f * k2) * Math.exp(-(f * f) / (2 * sigma * sigma))
            * c.fade / (1 + dist * 1.4);
      }
      pos.setY(vi, h);
      // Crest and trough are not the same water: a crest tips toward
      // the sky it mirrors and goes cool, a trough shows the warm bed.
      // One achromatic multiply left the pool on a single flat hue.
      const s = h / amp;
      const b = Math.max(0.72, Math.min(1.35, 1 + s * 0.14));
      const w = Math.max(0, Math.min(1, 0.5 + 0.5 * s));
      col[vi * 3] = b * (1.06 - 0.12 * w);
      col[vi * 3 + 1] = b * (1.005 - 0.01 * w);
      col[vi * 3 + 2] = b * (0.94 + 0.12 * w);
    }
    pos.needsUpdate = true;
    geom.attributes.color.needsUpdate = true;
    geom.computeVertexNormals();
  };

  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = 'Puddle';
  mesh.receiveShadow = true;
  mesh.userData.update = update;
  update(0);
  return mesh;
}
