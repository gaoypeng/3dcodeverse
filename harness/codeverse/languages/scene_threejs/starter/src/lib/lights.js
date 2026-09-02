/**
 * Panel lights: a RectAreaLight fused with its visible glowing panel.
 * Closes RectAreaLight's silent traps: renders black without the LTC
 * init, lights only Standard/Physical materials with NO shadows, and
 * emits along local -Z (pre-rotated here so the group's +Z emits).
 */

import * as THREE from 'three';
import { RectAreaLightUniformsLib }
  from 'three/addons/lights/RectAreaLightUniformsLib.js';

let _inited = false;

/**
 * Install the LTC lookup tables RectAreaLight needs, exactly once.
 * Safe to call any number of times; `makePanelLight` calls it for you.
 */
export function initRectAreaLights() {
  if (_inited) return;
  RectAreaLightUniformsLib.init();
  _inited = true;
}

/**
 * Colour of a black-body at `kelvin` — the Tanner Helland fit.
 *
 * The realism of a night street is largely its light classes
 * SEPARATING: sodium amber over the road, tungsten in the homes,
 * fluorescent in the shop, cool LED headlights. One generic warm
 * white over everything is the giveaway of a rendered scene.
 *
 * COLOUR SPACE (the trap this closes): the Helland fit is an 8-bit
 * **sRGB** triple — it is what you would type into a paint program.
 * three's working space is srgb-linear and `new THREE.Color(r,g,b)`
 * writes its arguments there RAW, so handing the fit's numbers to
 * that constructor states a gamma-encoded value as a linear one and
 * every class comes out washed toward white: measured on this
 * renderer, tungsten 2700 K landed at linear (1.00, 0.65, 0.34) and
 * its panel read (158,152,145) on screen — a grey card, not a warm
 * bulb. Decoding through SRGBColorSpace gives (1.00, 0.39, 0.10) and
 * the class split the table exists for. A hex colour (`0xffdca8`)
 * already takes that decode inside three, so this is also the only
 * way the two colour paths into `makePanelLight` agree.
 *
 * @param {number} kelvin Temperature, clamped to 1000-12000 K.
 * @returns {THREE.Color} That temperature in three's working
 *   (linear-sRGB) space, ready for a light or an emissive.
 */
export function kelvinColor(kelvin) {
  const t = THREE.MathUtils.clamp(kelvin, 1000, 12000) / 100;
  let r;
  let g;
  let b;
  if (t <= 66) {
    r = 255;
    g = 99.4708025861 * Math.log(t) - 161.1195681661;
    b = t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  } else {
    r = 329.698727446 * Math.pow(t - 60, -0.1332047592);
    g = 288.1221695283 * Math.pow(t - 60, -0.0755148492);
    b = 255;
  }
  // Floor every channel a hair above zero. Below 1900 K the fit snaps
  // blue to a hard 0 — a discontinuity in the fit, not physics — and a
  // dead channel makes a light pool clip to a pure hue and band.
  const c = (v) => Math.max(2, THREE.MathUtils.clamp(v, 0, 255)) / 255;
  return new THREE.Color().setRGB(c(r), c(g), c(b), THREE.SRGBColorSpace);
}

/**
 * The light classes a night scene is built from, by name.
 *
 * Pick per FIXTURE CLASS, never one hue for the whole scene: roads
 * sodium or led_cool, homes tungsten, shopfronts fluorescent, works
 * floodlights metal_halide. `moon` is perceptual (matches sunRig's
 * night sun), not a black body — moonlight photographs blue.
 * Values: `{ kelvin, color }`, colours precomputed.
 */
export const LIGHT_CLASSES = {
  sodium: { kelvin: 1900, color: kelvinColor(1900) },
  tungsten: { kelvin: 2700, color: kelvinColor(2700) },
  halogen: { kelvin: 3200, color: kelvinColor(3200) },
  fluorescent: { kelvin: 4300, color: kelvinColor(4300) },
  metal_halide: { kelvin: 4800, color: kelvinColor(4800) },
  led_cool: { kelvin: 6300, color: kelvinColor(6300) },
  moon: { kelvin: 4100, color: new THREE.Color(0xb5c7e8) },
};

/** Deterministic 0..1 hash — dither, so an 8-bit ramp cannot band. */
function _hash(i) {
  const s = Math.sin(i * 12.9898 + 78.233) * 43758.5453;
  return s - Math.floor(s);
}

let _diffuserTex = null;
let _haloTex = null;

/**
 * The luminance profile of a real diffuser: a broad flat core with the
 * last fifth of the panel rolling off into its frame. Used as an
 * emissiveMap, so it is a LINEAR multiplier (NoColorSpace, no decode).
 *
 * Without it every panel is one clipped value — flat white regardless
 * of its class, and a solid modal block in the frame statistics. The
 * roll-off also hands the rim a dimmer, hence MORE saturated version
 * of the same colour once ACES has desaturated the core, which is what
 * makes an amber lamp read amber next to a white one.
 */
function diffuserTexture() {
  if (_diffuserTex) return _diffuserTex;
  const N = 64;
  const d = new Uint8Array(N * N * 4);
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const a = Math.abs((x + 0.5) / N * 2 - 1);
      const b = Math.abs((y + 0.5) / N * 2 - 1);
      // superellipse radius: a rounded rectangle, not a circle, so a
      // long sign rolls off along its length as well as its height
      const e = Math.pow(a ** 6 + b ** 6, 1 / 6);
      // never to zero: a rim that reaches 0 uncovers the panel's dark
      // base colour and draws a black bezel around every luminaire.
      // 0.32 leaves the rim a dimmer — hence more saturated, ACES
      // desaturates the core — version of the same light.
      const roll = 0.32 + 0.68 * (1 - THREE.MathUtils.smoothstep(e, 0.72, 1.0));
      // gentle centre lift — a diffuser is brightest over its lamp
      const v = roll * (0.88 + 0.12 * (1 - e * e));
      const dith = (_hash(y * N + x) - 0.5) * (1.6 / 255);
      const u8 = Math.round(THREE.MathUtils.clamp(v + dith, 0, 1) * 255);
      const o = (y * N + x) * 4;
      d[o] = d[o + 1] = d[o + 2] = u8;
      d[o + 3] = 255;
    }
  }
  const t = new THREE.DataTexture(d, N, N, THREE.RGBAFormat);
  t.colorSpace = THREE.NoColorSpace;
  t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = true;
  t.needsUpdate = true;
  _diffuserTex = t;
  return t;
}

/** Alpha falloff for the spill quad: 1 at the panel, 0 at the rim. */
function haloTexture() {
  if (_haloTex) return _haloTex;
  const N = 96;
  const d = new Uint8Array(N * N * 4);
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const a = Math.abs((x + 0.5) / N * 2 - 1);
      const b = Math.abs((y + 0.5) / N * 2 - 1);
      const e = Math.min(1, Math.pow(a ** 4 + b ** 4, 1 / 4));
      const v = Math.pow(1 - e, 2.4);
      const dith = (_hash(y * N + x + 7919) - 0.5) * (2.0 / 255);
      const o = (y * N + x) * 4;
      d[o] = d[o + 1] = d[o + 2] = 255;
      d[o + 3] = Math.round(THREE.MathUtils.clamp(v + dith, 0, 1) * 255);
    }
  }
  const t = new THREE.DataTexture(d, N, N, THREE.RGBAFormat);
  t.colorSpace = THREE.NoColorSpace;
  t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = true;
  t.needsUpdate = true;
  _haloTex = t;
  return t;
}

/**
 * A rectangular area light with its visible emissive panel.
 * The group emits along its LOCAL +Z, so `group.lookAt()` aims the
 * glowing face; pair with the scene's shadow sun for grounding.
 *
 * @param {number} w Panel width in metres.
 * @param {number} h Panel height in metres.
 * @param {number|string} [color] Light + glow colour, or a
 *   LIGHT_CLASSES name — 'sodium', 'tungsten', 'fluorescent'...
 *   (default 0xffffff).
 * @param {number} [intensity] RectAreaLight intensity (default 6).
 *   Re-measured on THIS pipeline (ACES, exposure 1.0, no post chain,
 *   no other light): a 4x2 m white panel washing a matte 0.6-grey
 *   wall, read at the point facing the panel centre —
 *     intensity 6 at 3 m -> 186/255 · 3 at 3 m -> 138/255
 *     intensity 6 at 6 m -> 103/255 · 20 at 6 m -> 188/255
 *   so 6 is a room light at arm's length and a billboard read from
 *   20 m wants tens, not units. A WARM class also carries less
 *   luminance per unit intensity than a cool one (the colour
 *   multiplies the radiance: sodium ~0.38, tungsten ~0.49,
 *   fluorescent ~0.72, led_cool ~0.97 of white) — swapping a shop
 *   from 'led_cool' to 'sodium' needs roughly 2.5x the intensity to
 *   hold the same road brightness.
 * @param {object} [opts] `panel: false` skips the visible mesh (an
 *   invisible area fill); `glow` panel emissiveIntensity (default
 *   2.2); `halo` the soft spill quad behind the panel — `false` to
 *   drop it, or a number for its strength (default 0.6). There is no
 *   bloom pass in this pipeline, so the halo IS the glow around a
 *   luminaire; it hides behind the panel and only shows as spill.
 * @returns {THREE.Group} Group named `PanelLight`; the light, panel
 *   and halo are exposed as `group.userData.light` /
 *   `group.userData.panel` / `group.userData.halo`.
 */
export function makePanelLight(w, h, color, intensity, opts = {}) {
  initRectAreaLights();
  // A LIGHT_CLASSES name is a colour: 'fluorescent' for the shop,
  // 'sodium' for the forecourt — the class split is the realism.
  const col = typeof color === 'string' && LIGHT_CLASSES[color]
      ? LIGHT_CLASSES[color].color
      : (color === undefined ? 0xffffff : color);
  const inten = intensity === undefined ? 6 : intensity;

  const group = new THREE.Group();
  group.name = 'PanelLight';
  // A luminaire hangs where the author hung it. This host runs a
  // deterministic settle pass over every placed asset before the first
  // frame (runtime_js/lib/host_placement.mjs, --no-settle to disable):
  // a group whose meshes float is dropped onto whatever is beneath it.
  // Measured: a 1.5x1.2 window panel authored at y=4.4 on a wall was
  // seated at y=0.60 (its own half-height) and rendered lying at the
  // kerb. `placement: 'free'` is the documented opt-out — the same one
  // a bird or a hanging lantern uses.
  group.userData.placement = 'free';

  const light = new THREE.RectAreaLight(col, inten, w, h);
  // RectAreaLight emits along its local -Z; flip it so the GROUP's +Z
  // is the emitting direction and a plain group.lookAt() aims it.
  light.rotation.y = Math.PI;
  group.add(light);

  let panel = null;
  let halo = null;
  if (opts.panel === undefined || opts.panel) {
    panel = new THREE.Mesh(
        new THREE.PlaneGeometry(w, h),
        new THREE.MeshStandardMaterial({
          // A diffuser is a pale grey object when it is OFF; 0x111111
          // sits under the 0.02 linear floor and reads as a dead hole
          // in daylight, where these panels also have to appear.
          color: 0x2b2b28,
          // not perfectly matte — the sheen off a plastic diffuser is
          // what keeps an unlit panel from being a flat cutout
          roughness: 0.72,
          emissive: new THREE.Color(col),
          emissiveIntensity: opts.glow === undefined ? 2.2 : opts.glow,
          emissiveMap: diffuserTexture(),
        }));
    panel.position.z = -0.01;
    panel.name = 'PanelGlow';
    group.add(panel);

    const hs = opts.halo === undefined || opts.halo === true
        ? 0.6
        : (opts.halo === false ? 0 : opts.halo);
    if (hs > 0) {
      // margin scales with the SHORT side (a thin sign gets a thin
      // halo) plus a little of the long one, so aspect is preserved
      const m = 0.5 * Math.min(w, h) + 0.16 * Math.max(w, h);
      halo = new THREE.Mesh(
          new THREE.PlaneGeometry(w + 2 * m, h + 2 * m),
          new THREE.MeshBasicMaterial({
            color: new THREE.Color(col),
            map: haloTexture(),
            transparent: true,
            opacity: hs,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            // additive + fog would ADD the fog colour instead of
            // fading the glow into it
            fog: false,
          }));
      // BEHIND the panel: the depth test hides the blown centre and
      // leaves exactly the spill around the edges.
      halo.position.z = -0.02;
      halo.renderOrder = 2;
      halo.name = 'PanelHalo';
      group.add(halo);
    }
  }

  group.userData.light = light;
  group.userData.panel = panel;
  group.userData.halo = halo;
  return group;
}
