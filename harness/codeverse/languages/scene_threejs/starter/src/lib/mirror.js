/**
 * Reflections: ONE real planar mirror, everything else by environment.
 * A Reflector re-renders the whole scene, and a second RTT surface the
 * first can SEE nests inside that pass — measured on SwiftShader at
 * 1024x576, a mirror floor costs +48 ms/frame and adding one wall
 * mirror to it +148 ms. The budget is enforced HERE, not left to the
 * caller: over budget `makeMirror` degrades to an envMap mirror.
 * Glass facades never get an RTT — `bakeSkylineEnvironment` +
 * `glazeFacade` give a whole city reflective for one bake.
 */

import * as THREE from 'three';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import * as MAT from './materials.js';
import { mulberry32 } from './noise.js';

// The Reflector shader OVERLAY-blends its `color` over the reflection,
// and overlay's identity is linear 0.5 = 0xbc in sRGB. Its own default
// 0x7f7f7f (linear 0.21) multiplies darks by 0.42 — dim, dirty glass.
const NEUTRAL = 0xbcbcbc;

// The reflection target holds a SCREEN-space image of the mirrored
// scene, so its aspect must match the FRAME, not the mirror's shape —
// a square one oversamples vertically and costs 1.8x the pixels.
const FRAME_ASPECT = 16 / 9;

// Same axis convention as sunRig()/makeOcean: azimuth 35, elevation 48.
const _AZ = 35 * Math.PI / 180;
const _EL = 48 * Math.PI / 180;
const _DAY_SUN = new THREE.Vector3(
    Math.cos(_EL) * Math.cos(_AZ), Math.sin(_EL),
    Math.cos(_EL) * Math.sin(_AZ));

let _limit = 1;
let _used = 0;
const _ours = new WeakSet();

/**
 * Set how many REAL reflection surfaces this scene may build.
 *
 * Default 1. Set 0 when the scene already spends its one RTT on
 * `makeOcean()` (lib/water.js) or `makeMirrorFloor()` (lib/wetground.js)
 * and you did not hand `makeMirror` the scene to count them itself.
 *
 * @param {number} n Allowed RTT surfaces (clamped to >= 0).
 * @returns {number} The limit now in force.
 */
export function setReflectionBudget(n) {
  _limit = Math.max(0, Math.floor(n));
  _used = 0;
  return _limit;
}

/**
 * Report the RTT reflection budget.
 *
 * @param {THREE.Scene} [scene] Counted for RTT surfaces this module did
 *   not build (an ocean, a mirror floor) so they charge the budget too.
 * @returns {{limit: number, used: number, left: number}} Counts of
 *   whole-scene reflection passes allowed, spent and remaining.
 */
export function reflectionBudget(scene) {
  const used = _used + _foreignRtt(scene);
  return { limit: _limit, used, left: Math.max(0, _limit - used) };
}

/** Count RTT surfaces in `scene` that this module did not build. */
function _foreignRtt(scene) {
  if (!scene || !scene.traverse) return 0;
  let n = 0;
  scene.traverse((o) => {
    const u = o.material && o.material.uniforms;
    if (u && (u.mirrorSampler || u.tDiffuse) && !_ours.has(o)) n++;
  });
  return n;
}

/**
 * A mirror-bright material that costs NOTHING per frame: it reflects
 * `scene.environment` (or `envMap`) instead of re-rendering the scene.
 * The honest trade — it cannot show the objects in front of it, so use
 * it for chrome props, distant glass and every mirror past the first.
 *
 * @param {object} [opts]
 *   `envMap` texture to reflect (default: whatever `scene.environment`
 *   holds at render time); `scene` to take that environment from now;
 *   `tint` hex (default 0xf0f3f5, near-white chrome);
 *   `roughness` (default 0.05 — never 0: a perfect mirror with a low
 *   resolution environment reads as a flat colour patch);
 *   `envMapIntensity` (default 1.15).
 * @returns {THREE.MeshPhysicalMaterial} Metallic, opaque, no RTT.
 */
export function envMirrorMaterial(opts = {}) {
  const env = opts.envMap ||
      (opts.scene && opts.scene.environment) || null;
  return new THREE.MeshPhysicalMaterial({
    color: opts.tint === undefined ? 0xf0f3f5 : opts.tint,
    metalness: 1,
    roughness: opts.roughness === undefined ? 0.05 : opts.roughness,
    envMap: env,
    envMapIntensity:
        opts.envMapIntensity === undefined ? 1.15 : opts.envMapIntensity,
  });
}

/**
 * The scene's planar mirror: wall mirror, mirrored panel, mirror wall.
 * Built as a real `Reflector` while the budget holds and as an envMap
 * mirror after that — check `userData.mode` if it matters.
 *
 * @param {number} w Width in metres.
 * @param {number} h Height in metres.
 * @param {object} [opts]
 *   `scene` the scene it will join — passing it lets the budget count
 *   an ocean / mirror floor already in there (STRONGLY recommended);
 *   `mode` `'auto'` (default, degrade when over budget), `'rtt'` (force
 *   a real reflection) or `'env'` (force the free one);
 *   `rttSize` reflection width in px (default 768; height follows
 *   `aspect`); `aspect` frame aspect (default 16/9);
 *   `multisample` MSAA samples in the reflection (default 0: the
 *   addon's 4 costs 28 ms/frame on SwiftShader for a difference
 *   invisible at 1:1, since the reflection is minified onto the
 *   mirror and the composer antialiases the frame anyway);
 *   `tint` hex over the reflection (default 0xbcbcbc = untinted;
 *   BELOW that darkens, above brightens — overlay blend, not multiply);
 *   `standoff` metres the glass stands proud of the mount plane
 *   (default 0.05 — see @returns, do not set 0);
 *   `frame` false for a frameless mirrored panel (default true);
 *   `frameColor` hex (default 0x64492f, walnut), `frameWidth` metres
 *   (default 5% of the short side), `frameDepth` metres (default
 *   standoff + 0.8 * frameWidth); `backColor` hex of the backing plate
 *   (default 0x39332c); `envMap` / `roughness` forwarded to the fallback;
 *   `name`.
 * @returns {THREE.Group} Centred on its own origin, which is the MOUNT
 *   PLANE (put it ON the wall): the glass stands `standoff` in front,
 *   facing +Z (`userData.forward`). Geometry coplanar with the glass
 *   leaks through the reflection's oblique clip plane and fills the
 *   mirror with a black slab at oblique angles — the standoff is what
 *   buys the clearance. `userData.mode` is `'rtt'` or `'env'`,
 *   `userData.surface` the reflective mesh.
 */
export function makeMirror(w, h, opts = {}) {
  const mode = opts.mode || 'auto';
  const left = reflectionBudget(opts.scene).left;
  const rtt = mode === 'rtt' || (mode !== 'env' && left > 0);
  if (mode === 'auto' && !rtt) {
    console.warn(
        'makeMirror: reflection budget spent (limit ' + _limit + ') — ' +
        'this mirror falls back to an envMap reflection. A real ' +
        'mirror re-renders the WHOLE scene, and a second one it can ' +
        'see nests inside that pass.');
  }
  if (mode === 'rtt' && left <= 0) {
    console.warn(
        'makeMirror: forced mode:"rtt" past the budget — this frame ' +
        'now costs another full scene pass, nested if the surfaces ' +
        'can see each other.');
  }

  const group = new THREE.Group();
  group.name = opts.name || 'Mirror';

  let surface;
  if (rtt) {
    const tw = opts.rttSize === undefined ? 768 : opts.rttSize;
    const th = Math.max(
        64, Math.round(tw / (opts.aspect || FRAME_ASPECT)));
    surface = new Reflector(new THREE.PlaneGeometry(w, h), {
      clipBias: 0.003,
      textureWidth: tw,
      textureHeight: th,
      color: opts.tint === undefined ? NEUTRAL : opts.tint,
      multisample:
          opts.multisample === undefined ? 0 : opts.multisample,
    });
    _used++;
    _ours.add(surface);
  } else {
    surface = new THREE.Mesh(
        new THREE.PlaneGeometry(w, h), envMirrorMaterial(opts));
  }
  const stand = opts.standoff === undefined ? 0.05 : opts.standoff;
  surface.name = 'MirrorSurface';
  surface.position.z = stand;
  // An RTT plane must never cast: it would shadow the very scene it
  // resamples. Receiving would paint shadows onto the reflection.
  surface.castShadow = false;
  surface.receiveShadow = false;
  group.add(surface);

  // A mirror is not a hole in the wall. The plate faces away, and it
  // sits on the MOUNT plane, not against the glass: measured, anything
  // within 1 cm behind the glass leaks past the oblique clip plane and
  // fills the mirror with a dark slab from 20 degrees off-axis.
  const back = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      MAT.paintedIron({
        // 0x2a2724 is linear 0.024 — at the very floor of a legal albedo,
        // and under ACES at exposure 1.0 the plate came back as a hole in
        // the wall wherever the frame did not cover it. This still reads
        // as dark painted steel and still has somewhere to fall in shade.
        color: opts.backColor === undefined ? 0x39332c : opts.backColor,
      }));
  back.rotation.y = Math.PI;
  back.position.z = 0.004;
  back.name = 'MirrorBack';
  back.castShadow = true;
  back.receiveShadow = true;
  group.add(back);

  if (opts.frame !== false) {
    const fw = opts.frameWidth === undefined
        ? Math.max(0.03, Math.min(w, h) * 0.05) : opts.frameWidth;
    const fd = opts.frameDepth === undefined
        ? stand + Math.max(0.03, fw * 0.8) : opts.frameDepth;
    const zc = fd / 2;
    const bars = [
      _bx(w + 2 * fw, fw, fd, 0, h / 2 + fw / 2, zc),
      _bx(w + 2 * fw, fw, fd, 0, -h / 2 - fw / 2, zc),
      _bx(fw, h, fd, -w / 2 - fw / 2, 0, zc),
      _bx(fw, h, fd, w / 2 + fw / 2, 0, zc),
    ];
    const frame = new THREE.Mesh(
        mergeGeometries(bars, false),
        MAT.paintedWood({
          // The frame is the only lit thing next to a mirror full of the
          // scene's own brightness, so it decides whether the mirror reads
          // as an object or as a hole cut in the wall. 0x4a3a2a is linear
          // 0.068: measured on our host it rendered as a black border with
          // no grain in it at all. Walnut sits at 0.13/0.07/0.03 — still a
          // dark frame, but one with a wood hue and a visible streak.
          color: opts.frameColor === undefined ? 0x64492f : opts.frameColor,
        }));
    frame.name = 'MirrorFrame';
    frame.castShadow = true;
    frame.receiveShadow = true;
    group.add(frame);
  }

  group.userData.forward = '+Z';
  group.userData.mode = rtt ? 'rtt' : 'env';
  group.userData.surface = surface;
  return group;
}

/** A box as geometry, positioned by its centre. */
function _bx(w, h, d, x, y, z) {
  const g = new THREE.BoxGeometry(w, h, d);
  g.translate(x, y, z);
  return g;
}

/** Deterministic 0..1 hash — the per-window lit draw, no PRNG order. */
function _hash(a, b, seed) {
  const t = Math.sin(a * 127.1 + b * 311.7 + seed * 74.7) * 43758.5453;
  return t - Math.floor(t);
}

// THE BAKE'S TRANSFER FUNCTION. Every colour below is mixed in the LINEAR
// working space a THREE.Color holds (`new THREE.Color(0x5d8fd6)` stores
// 0.110/0.275/0.672, not 0.365/0.561/0.839), while the DataTexture is
// flagged SRGBColorSpace and is therefore DECODED as sRGB when sampled.
// Writing the linear numbers straight into the bytes ran the transfer
// backwards: the intended zenith 0x5d8fd6 shipped as byte (28,70,171) and
// came back as linear (0.011,0.061,0.415) — a sixth of the light, and blue
// pulled far ahead of red because the error is a power curve, not a scale.
// Measured on our host BEFORE this line existed: the baked horizon band sat
// at byte 98 with a hue spread of 0.002, and every facade `glazeFacade`
// touched rendered as a flat navy slab (mean_lum 0.145, saturation 0.72).
function _srgb(v) {
  if (!(v > 0)) return 0;
  return v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
}

/** Radians to degrees. */
function _deg(r) {
  return r * 180 / Math.PI;
}

// NO DITHER HERE, AND THAT IS A MEASUREMENT, NOT AN OVERSIGHT. A smooth
// 8-bit ramp usually needs +-0.5 LSB of ordered noise or it lays contour
// bands across every mirror-smooth surface that reflects it. This one does
// not: encoded to sRGB the sky half crosses only 9 byte levels over 80
// rows, so each step is 1 LSB (0.4%) spread over ~9 texels — under ACES at
// exposure 1.0 that is below the visible floor. A 4x4 Bayer pattern was
// built, shipped into the render and measured against no dither: after a
// 2x2 / 4x4 / 8x8 minification blur (what PMREM and the reflection itself
// do) the two column profiles were IDENTICAL to four decimals, max step
// 0.25 either way, while the un-blurred second difference went 0.21 -> 1.60.
// It bought high-frequency noise and nothing else, so it was deleted.

/**
 * Bake the equirect a reflective city needs: sky gradient, the rig's
 * sun glow, a seeded band of NEIGHBOURING TOWERS with window grids,
 * and ground. Assign it to `scene.environment` (it is a superset of
 * `sunRig().envTex`) or pass it as `envMap` per material.
 *
 * @param {object} [opts]
 *   `seed` (default 7) shapes the skyline; `size` equirect width in px
 *   (default 512, height is half); `sunDir` normalized THREE.Vector3 —
 *   pass the SAME vector the key light uses (default: the day rig's);
 *   `zenith` / `horizon` / `ground` / `sunColor` hex — defaults follow
 *   `sunDir`: the lib/environment.js DAY mood while the sun is up, and
 *   the NIGHT mood (deep-blue zenith over a lighter horizon band, moon
 *   for the glow) the moment it sets, so an evening scene does not
 *   reflect a noon sky off every window; `skylineColor` hex (day
 *   0x39424e); `skylineHeightDeg` tallest neighbour in degrees of
 *   elevation (default 30 — a street canyon; 8 is a distant skyline);
 *   `lit` 0..1 fraction of reflected windows glowing (default 0 by day,
 *   0.3 once the sun is down — a dark city with every window out is a
 *   ruin); `haze` 0..1 of aerial perspective mixed into the neighbours,
 *   heaviest at the street (default 0.18 — a city block is never the
 *   flat swatch its own albedo would make it, and this is what keeps
 *   the reflected band off the black floor); `hueVar` per-building hue
 *   jitter in turns (default 0.035 — concrete, brick and glass
 *   neighbours, not one stamped tone); `skyline` false for open sky.
 * @returns {THREE.DataTexture} Equirect reflection mapping, sRGB,
 *   LINEAR filtered (a DataTexture defaults to Nearest, which prints
 *   the bake's pixel grid across every reflective facade).
 */
export function bakeSkylineEnvironment(opts = {}) {
  const w = opts.size || 512;
  const h = w >> 1;
  const rand = mulberry32(opts.seed === undefined ? 7 : opts.seed);
  const seed = opts.seed === undefined ? 7 : opts.seed;
  const sun = (opts.sunDir ? opts.sunDir.clone()
                           : _DAY_SUN.clone()).normalize();
  // THE HOUR IS NOT A SEPARATE ARGUMENT. `sunDir` already carries it, and
  // a scene that hands its night sun vector in while the bake keeps noon
  // hexes gets a noon sky mirrored off every pane at midnight — the one
  // "hardcoded light colour" failure this module could still commit.
  // Below the horizon the palette flips to the night mood the rest of the
  // library uses: a deep-blue zenith over a LIGHTER horizon band, moonlight
  // for the glow, and the lights on in the reflected city.
  const dark = sun.y <= 0;
  // The night zenith is the scene's own (sky.js measures [26,36,64] there).
  // The other two are NOT the sky at all and must not be scaled down with
  // it: the horizon carries a city's skyglow and the ground carries what
  // its own street lamps throw back. Left at a fraction of the zenith, the
  // lower half of every night facade reflected a black floor — measured
  // 11.1% of the close frame under the harness's crushed-pixel threshold.
  const M = dark
      ? { zen: 0x1a2440, hor: 0x384765, gnd: 0x2c2620, wall: 0x212734,
          glow: 0xb6c6e2, lit: 0.3 }
      : { zen: 0x5d8fd6, hor: 0xdce9f2, gnd: 0x8a7f6a, wall: 0x39424e,
          glow: 0xfff6e0, lit: 0 };
  const zenith = new THREE.Color(
      opts.zenith === undefined ? M.zen : opts.zenith);
  const horizon = new THREE.Color(
      opts.horizon === undefined ? M.hor : opts.horizon);
  const ground = new THREE.Color(
      opts.ground === undefined ? M.gnd : opts.ground);
  const wallC = new THREE.Color(
      opts.skylineColor === undefined ? M.wall : opts.skylineColor);
  const sunC = new THREE.Color(
      opts.sunColor === undefined ? M.glow : opts.sunColor);
  // Interior light is never one bulb: a night city is tungsten rooms,
  // warm-white rooms and the cold fluorescent floor nobody went home from.
  const litC = [
    new THREE.Color(0xffb45f), new THREE.Color(0xffd7a0),
    new THREE.Color(0xf2e2c0), new THREE.Color(0xcfe0dc),
  ];
  const lit = opts.lit === undefined ? M.lit : opts.lit;
  // A night city is FARTHER through the same air, not less hazy: the haze
  // just carries the horizon's own colour, which is now blue.
  const haze = opts.haze === undefined ? (dark ? 0.26 : 0.18) : opts.haze;
  const hueVar = opts.hueVar === undefined ? 0.035 : opts.hueVar;
  const maxRad = (opts.skylineHeightDeg === undefined
      ? 30 : opts.skylineHeightDeg) * Math.PI / 180;
  // WHAT ACTUALLY LIGHTS THIS BAKE. By day it is the sun. Once the sun is
  // under the horizon the key light is the MOON, and lib/environment.js
  // puts it opposite the sun, 30-55 deg up (deeper sun, higher moon) — the
  // same construction, so the reflected sky's glow sits where the scene's
  // own moon is instead of leaving a sun-shaped blot in the ground half.
  const keyDir = dark
      ? (() => {
          const az = Math.atan2(sun.z, sun.x) + Math.PI;
          const el = Math.max(30, Math.min(55, 25 + _deg(Math.asin(-sun.y))))
              * Math.PI / 180;
          return new THREE.Vector3(Math.cos(el) * Math.cos(az), Math.sin(el),
                                   Math.cos(el) * Math.sin(az));
        })()
      : sun;
  // A moon's disc is as bright as a sun's but its halo is a tenth of it —
  // the wide term is what would otherwise wash the night sky back to grey.
  const wideGlow = dark ? 0.05 : 0.22;
  // The key light's azimuth, so a facade's shading agrees with the light
  // instead of being a second random number.
  const sunAz = Math.atan2(keyDir.z, keyDir.x);

  // Skyline profile, one entry per column: roof elevation, tone, the
  // window row pitch, and a per-building hue offset. Buildings span whole
  // runs of columns, so every array below is constant across a facade.
  const top = new Float32Array(w);
  const tone = new Float32Array(w);
  const pitch = new Float32Array(w);
  const hue = new Float32Array(w);
  const wsat = new Float32Array(w);
  if (opts.skyline !== false) {
    let x = 0;
    while (x < w) {
      const bw = 4 + Math.floor(rand() * Math.max(4, w / 24));
      const bh = (0.18 + 0.82 * Math.pow(rand(), 1.7)) * maxRad;
      const bt = 0.75 + rand() * 0.5;
      const bp = 3 + Math.floor(rand() * 4);
      // Hue AND saturation move together: the pale end reads as concrete
      // and the warm end as brick, which is what a mixed block looks like.
      const bhue = (rand() - 0.5) * 2 * hueVar;
      const bsat = 0.7 + rand() * 0.7;
      for (let i = 0; i < bw && x < w; i++, x++) {
        top[x] = bh;
        tone[x] = bt;
        pitch[x] = bp;
        hue[x] = bhue;
        wsat[x] = bsat;
      }
    }
  }
  const below = maxRad * 0.25;   // the street the towers stand in
  const wall = new THREE.Color();

  const data = new Uint8Array(w * h * 4);
  const c = new THREE.Color();
  const px = new THREE.Vector3();
  for (let row = 0; row < h; row++) {
    const lat = ((row + 0.5) / h - 0.5) * Math.PI;
    const y = Math.sin(lat);
    for (let col = 0; col < w; col++) {
      const lon = ((col + 0.5) / w - 0.5) * Math.PI * 2;
      if (y >= 0) {
        c.copy(horizon).lerp(zenith, Math.pow(y, 0.62));
      } else {
        c.copy(horizon).lerp(ground, Math.min(1, -y * 3));
      }
      if (lat < top[col] && lat > -below) {
        // Facade: darker at the base, window grid on top of it, and the
        // side the sun is on brighter than the side it is not — the same
        // `sunDir` the key light uses, so a reflected city cannot be lit
        // from a direction nothing else in the scene is.
        const f = (lat + below) / (top[col] + below);
        const face = 0.72 + 0.42 * Math.max(0, Math.cos(lon - sunAz));
        wall.copy(wallC).offsetHSL(hue[col], (wsat[col] - 1) * 0.35, 0);
        c.copy(wall).multiplyScalar(tone[col] * face * (0.62 + 0.38 * f));
        const p = pitch[col];
        if (col % 3 < 2 && row % p < p - 1 && lat > -below * 0.5) {
          const winRow = Math.floor(row / p);
          if (lit && _hash(col / 3 | 0, winRow, seed) < lit) {
            const k = _hash(winRow, col / 3 | 0, seed + 19);
            c.lerp(litC[Math.min(3, (k * 4) | 0)], 0.62 + 0.3 * k);
          } else {
            // An unlit pane is a slice of the sky it faces, and no two
            // panes hold the same slice: blinds, a curtain, an empty
            // room. One flat lerp is what makes a bake read as wallpaper.
            const v = _hash(col / 3 | 0, winRow, seed + 5);
            c.lerp(horizon, 0.16 + 0.30 * v);
          }
        }
        // Aerial perspective: heaviest at the street, gone by the roofline.
        // Without it the band's darkest facades bottom out near black and
        // every mirror that reflects the horizon reads as a hole.
        c.lerp(horizon, haze * (1 - 0.72 * f));
      } else {
        px.set(Math.cos(lat) * Math.cos(lon), y,
               Math.cos(lat) * Math.sin(lon));
        const ang = Math.acos(
            Math.max(-1, Math.min(1, px.dot(keyDir))));
        const glow = 1.1 * Math.exp(-(ang * ang) / 0.0128) +
            wideGlow * Math.exp(-(ang * ang) / 0.245);
        c.r += sunC.r * glow;
        c.g += sunC.g * glow;
        c.b += sunC.b * glow;
      }
      const i = (row * w + col) * 4;
      data[i] = Math.min(255, Math.round(_srgb(c.r) * 255));
      data[i + 1] = Math.min(255, Math.round(_srgb(c.g) * 255));
      data[i + 2] = Math.min(255, Math.round(_srgb(c.b) * 255));
      data[i + 3] = 255;
    }
  }

  const tex = new THREE.DataTexture(data, w, h, THREE.RGBAFormat);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  // Nearest is the DataTexture default and prints the bake's texel grid
  // straight onto every mirror-smooth facade; PMREM blurs only by
  // roughness, so it cannot rescue an unfiltered source.
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;
  tex.wrapS = THREE.RepeatWrapping;
  tex.needsUpdate = true;
  return tex;
}

/**
 * The curtain-wall material: a coated glass that mirrors the sky and
 * its neighbours off `envMap` for zero per-frame cost. Untinted glass
 * with only fresnel reflects ~4% head-on and renders as a black hole;
 * `coating` is what real reflective glazing adds on top of that.
 *
 * @param {object} [opts]
 *   `envMap` the equirect to reflect — pass `bakeSkylineEnvironment()`
 *   or leave null to inherit `scene.environment`; `scene` to read that
 *   environment now; `color` glass tint hex (default 0x2f4250, the
 *   blue-green of a real curtain wall); `roughness` (default 0.06 —
 *   raise to 0.15+ for older, wavier glazing); `metalness` (default
 *   0.22, the coating's tinted mirror component); `coating` 0..1 of
 *   dielectric reflectance, ior 1.5 to 2.33 (default 0.9);
 *   `clearcoat` 0..1 of the UNTINTED outer lacquer (default 0.9) and
 *   `clearcoatRoughness` (default 0.05); `envMapIntensity` (default
 *   1.5); `opacity` < 1 to see the reveals behind the glass (default 1,
 *   opaque — it writes depth and casts); `extra` merged last.
 * @returns {THREE.MeshPhysicalMaterial} Ready for a facade's glazing.
 */
export function glassFacadeMaterial(opts = {}) {
  const env = opts.envMap ||
      (opts.scene && opts.scene.environment) || null;
  const op = opts.opacity === undefined ? 1 : opts.opacity;
  const m = new THREE.MeshPhysicalMaterial({
    color: opts.color === undefined ? 0x2f4250 : opts.color,
    roughness: opts.roughness === undefined ? 0.06 : opts.roughness,
    // METALNESS TINTS THE MIRROR AND DIMS IT. A metal's F0 IS its colour,
    // and this colour is linear (0.031, 0.058, 0.088) — at 0.35 metalness
    // over a third of the reflection was being multiplied by a near-black
    // blue. Measured on our host: a glazed block's panes rendered at
    // mean_lum 0.145 with saturation 0.72, a flat navy hole. The mirror a
    // curtain wall actually shows lives in the COATING, not the metal.
    metalness: opts.metalness === undefined ? 0.22 : opts.metalness,
    // The lacquer over the tint: an untinted dielectric layer with its own
    // fresnel, so the sky arrives on the glass in the sky's own colour and
    // brightens toward grazing exactly where a real facade does.
    clearcoat: opts.clearcoat === undefined ? 0.9 : opts.clearcoat,
    clearcoatRoughness:
        opts.clearcoatRoughness === undefined ? 0.05
                                              : opts.clearcoatRoughness,
    envMap: env,
    envMapIntensity:
        opts.envMapIntensity === undefined ? 1.5 : opts.envMapIntensity,
    transparent: op < 1,
    opacity: op,
  });
  // reflectivity drives ior (0.5 -> 1.5, 1 -> 2.33): the coating that
  // lifts head-on reflectance from 4% toward 16%.
  m.reflectivity = opts.coating === undefined ? 0.9 : opts.coating;
  return Object.assign(m, opts.extra || {});
}

/**
 * Point a `lib/building.js` facade at the sky: swaps the glazing
 * material on every `Glazing` / `Pane` mesh under `root` (what
 * `block()`, `tower()`, `cityFabric()` and `casement()` name theirs).
 * Lit windows live on the `Reveals` mesh and are left glowing.
 *
 * @param {THREE.Object3D} root A building from lib/building.js, or any
 *   subtree whose glazed meshes are named `Glazing` / `Pane`.
 * @param {object} [opts] `material` to apply as-is — one material on every
 *   pane, no variants — otherwise every `glassFacadeMaterial()` option
 *   (`envMap` is the one that matters).
 * @returns {THREE.Object3D} The same `root`, with `userData.glazed`
 *   set to the number of meshes retargeted.
 */
export function glazeFacade(root, opts = {}) {
  const mat = opts.material || glassFacadeMaterial(opts);
  // lib/building.js ALREADY splits its panes into two batches (`Glazing`
  // and `Glazing2`) for exactly this reason — "real glazing is never one
  // value: blinds, curtains, a room behind one pane and none behind the
  // next". Retargeting both to a single material threw that away and
  // handed back the flat glass tower the split exists to prevent. The
  // second batch keeps the same coating and moves only tint and
  // roughness, by a hair; batch 0 is `mat` itself, untouched.
  const variants = [mat];
  if (!opts.material) {
    const alt = mat.clone();
    alt.color = mat.color.clone().offsetHSL(-0.02, 0.06, -0.03);
    alt.roughness = Math.min(1, mat.roughness + 0.05);
    alt.envMapIntensity = mat.envMapIntensity * 0.92;
    variants.push(alt);
  }
  let n = 0;
  root.traverse((o) => {
    if (o.isMesh && /glaz|pane/i.test(o.name || '')) {
      // Deterministic: the batch's own name picks the variant, so a fix
      // round that rebuilds the tower reproduces the same facade.
      const k = /2$/.test(o.name) ? 1 : 0;
      o.material = variants[Math.min(k, variants.length - 1)];
      n++;
    }
  });
  if (n === 0) {
    console.warn(
        'glazeFacade: no Glazing/Pane mesh under "' + (root.name || '?') +
        '" — nothing was made reflective.');
  }
  root.userData.glazed = n;
  return root;
}
