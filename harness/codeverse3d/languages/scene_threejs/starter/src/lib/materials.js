/**
 * Shared material library: every factory returns a MeshStandardMaterial
 * with procedural albedo + roughness + linear height maps and a seeded
 * variant knob (flat albedo is the strongest "plastic toy" tell).
 * Deterministic and DOM-free (DataTexture). Brick, cobble and fabric
 * carry their own bonded masonry, irregular stones and woven yarns.
 */

import * as THREE from 'three';
import { clonePatchedMaterial, patchStandard } from './shader.js';

// ---------------------------------------------------------------- noise

function hash2(x, y, seed) {
  let h = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453;
  return h - Math.floor(h);
}

// PERIODIC value noise: the lattice indices wrap at (px, py) cells, so a
// texture sampled over exactly px x py cells is seamless when tiled.  An
// unwrapped lattice is the source of the hard grid lines every repeated
// surface in this library carried — measured on a 120 m ground at
// repeat 10, where the 12 m seams read as paving joints in the dirt.
function valueNoise(x, y, px, py, seed) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const wx = ((xi % px) + px) % px, wx1 = ((xi + 1) % px + px) % px;
  const wy = ((yi % py) + py) % py, wy1 = ((yi + 1) % py + py) % py;
  const a = hash2(wx, wy, seed), b = hash2(wx1, wy, seed);
  const c = hash2(wx, wy1, seed), d = hash2(wx1, wy1, seed);
  return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v;
}

// Lacunarity is 2, not 2.03: a wrapped lattice needs every octave's
// period to divide the texture, and the octaves cannot line up into a
// grid here anyway because each carries its own hash seed.
function fbm(x, y, px, py, seed, octaves = 4) {
  let v = 0, amp = 0.5, f = 1;
  for (let i = 0; i < octaves; i++) {
    v += amp * valueNoise(x * f, y * f, px * f, py * f, seed + i * 17);
    f *= 2;
    amp *= 0.5;
  }
  return v;
}

/** Cells per texture on an axis: at least 1, and a whole number so it wraps. */
const cells = (n) => Math.max(1, Math.round(n));

const _texCache = new Map();

function texture(data, size, color = false) {
  const tex = new THREE.DataTexture(data, size, size);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = 4;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Grayscale/tinted noise DataTexture, cached by its parameters.
 * @param {object} o size, scale, seed, contrast, tint [r,g,b], base
 * @returns {THREE.DataTexture}
 */
export function noiseTexture(o = {}) {
  const size = o.size || 256;
  const scale = o.scale || 8;
  const seed = o.seed === undefined ? 1 : o.seed;
  const contrast = o.contrast === undefined ? 0.35 : o.contrast;
  const base = o.base === undefined ? 1 : o.base;
  const tint = o.tint || [1, 1, 1];
  const streak = o.streak || 0;          // 0 = isotropic, 1 = wood-like
  // A colour map multiplies the authored colour, so a HUE break here
  // reaches every material at once. Value noise alone is what the
  // palette probe names "one flat hue per surface": stone, plaster and
  // sand all shipped as one albedo shaded by luminance.
  const hueBreak = o.hueBreak || 0;
  const key = [size, scale, seed, contrast, base, streak,
               o.linear ? 1 : 0, hueBreak, ...tint].join(':');
  if (_texCache.has(key)) return _texCache.get(key);
  // A colour map MULTIPLIES the authored colour, so the field has to
  // average 0.5 or the delivered colour is not the one the asset asked
  // for — an un-normalised fBm mean wanders with the seed (measured: a
  // conifer green shipped 20% bright on one seed and dark on another).
  // Whole cell counts per axis; every sub-octave below spans a whole
  // count too, or the wrap leaks a seam back in.
  const pu = cells(scale);
  const pv = cells(scale * (streak ? 0.12 + 0.88 * (1 - streak) : 1));
  const su = cells(pu * 0.4);            // the streak's slow cross-grain
  const field = new Float32Array(size * size);
  let sum = 0;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const fx = x / size, fy = y / size;
      let n = fbm(fx * pu, fy * pv, pu, pv, seed);
      if (streak) {
        n = 0.65 * n + 0.35 * fbm(fx * su, fy * pv * 6, su, pv * 6, seed + 91, 3);
      }
      field[y * size + x] = n;
      sum += n;
    }
  }
  const shift = 0.5 - sum / (size * size);
  // The hue field is its OWN pattern at its own scale — tied to the
  // value field it would only make the light patches warm, which is
  // shading, not broken colour. Centred on its measured mean so the
  // delivered colour is still the one the asset asked for.
  let hueF = null;
  if (hueBreak > 0) {
    hueF = new Float32Array(size * size);
    const hu = cells(pu * 0.45), hv = cells(pv * 0.45);
    let hs = 0;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const n = fbm((x / size) * hu, (y / size) * hv, hu, hv, seed + 613);
        hueF[y * size + x] = n;
        hs += n;
      }
    }
    const hm = hs / (size * size);
    for (let i = 0; i < hueF.length; i++) hueF[i] -= hm;
  }
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const n = field[y * size + x] + shift;
      const lum = Math.max(0, Math.min(1, base * (1 - contrast / 2 + contrast * n)));
      const i = (y * size + x) * 4;
      // Warm/cool along the blue-orange axis, which is the swing
      // daylight itself makes: sun-warm where the light lands, sky-cool
      // where it does not. A rotation about the grey axis would return
      // white unchanged, so the tint is a bias, not a rotation.
      const h = hueF ? hueF[y * size + x] * hueBreak * 2 : 0;
      // CLAMPED, because a Uint8Array assignment takes the value mod
      // 256: `lum` alone can never exceed 1, so nothing here needed a
      // clamp until the hue term multiplied past it — and a texel that
      // wrapped came back as a near-black speck, which read as the
      // material getting DARKER and less colourful, the exact opposite
      // of the change (measured: a cathedral lost 17% of its chroma).
      const px = (v) => Math.max(0, Math.min(255, Math.round(v)));
      data[i] = px(255 * lum * tint[0] * (1 + h));
      data[i + 1] = px(255 * lum * tint[1] * (1 + h * 0.15));
      data[i + 2] = px(255 * lum * tint[2] * (1 - h));
      data[i + 3] = 255;
    }
  }
  const tex = texture(data, size, !o.linear);
  _texCache.set(key, tex);
  return tex;
}

const _structureCache = new Map();
const smooth = (a, b, value) => {
  const t = Math.max(0, Math.min(1, (value - a) / (b - a)));
  return t * t * (3 - 2 * t);
};
const fract = (value) => value - Math.floor(value);
const wrap = (value, period) => ((value % period) + period) % period;

// Surface channels: R = linear height, G = roughness multiplier,
// B = mortar/sediment coverage. They share one field so no joint can
// be painted over the raised center of a brick or stone.
function structuredTextures(kind, seed, options) {
  const contrast = Math.max(0, options.contrast ?? 0.4);
  const scale = Math.max(1, options.scale ?? 8);
  const streak = Math.max(0, Math.min(1, options.streak ?? 0));
  const key = `${kind}:${seed}:${contrast}:${scale}:${streak}`;
  if (_structureCache.has(key)) return _structureCache.get(key);
  const size = 512, color = new Uint8Array(size * size * 4);
  const surface = new Uint8Array(size * size * 4);
  const cycles = Math.max(16, Math.min(256, Math.round(scale * 4)));
  const cyclesY = Math.max(2, Math.round(cycles * (1 - streak * .92)));
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const u = (x + 0.5) / size, v = (y + 0.5) / size;
    const grain = fbm(u * cycles, v * cyclesY, cycles, cyclesY, seed + 101, 3);
    let height, roughness, joint = 0, tone, hue = 0;
    if (kind === 'brick') {
      const row = Math.floor(v * 12), bx = u * 4 + (row % 2) * 0.5;
      const id = hash2(wrap(Math.floor(bx), 4), row, seed);
      const fx = fract(bx), fy = fract(v * 12);
      const edge = Math.min(Math.min(fx, 1 - fx) / 4,
        Math.min(fy, 1 - fy) / 12);
      const chipped = edge + (grain - 0.45) * 0.006;
      joint = 1 - smooth(0.002, 0.008, chipped);
      const bevel = smooth(0.004, 0.015, chipped);
      height = 0.15 + bevel * (0.67 + (id - 0.5) * 0.12)
        + (grain - 0.45) * (0.12 - joint * 0.07);
      roughness = 0.87 + grain * 0.12 + joint * 0.05;
      tone = (0.76 + id * 0.22) * (0.89 + grain * 0.20);
      hue = (id - 0.5) * 0.13;
    } else if (kind === 'cobble') {
      const px = u * 6, py = v * 6, ix = Math.floor(px), iy = Math.floor(py);
      let first = 1e9, second = 1e9, id = 0;
      for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) {
        const cx = ix + i, cy = iy + j, wx = wrap(cx, 6), wy = wrap(cy, 6);
        const dx = cx + 0.2 + hash2(wx, wy, seed) * 0.6 - px;
        const dy = cy + 0.2 + hash2(wx, wy, seed + 23) * 0.6 - py;
        const d = dx * dx + dy * dy;
        if (d < first) { second = first; first = d; id = hash2(wx, wy, seed + 47); }
        else if (d < second) second = d;
      }
      const edge = Math.sqrt(second) - Math.sqrt(first);
      const chipped = edge + (grain - 0.45) * 0.055;
      joint = 1 - smooth(0.025, 0.105, chipped);
      const round = smooth(0.045, 0.32, chipped);
      height = 0.12 + round * (0.60 + id * 0.16) + (grain - 0.45) * 0.11;
      roughness = 0.78 + grain * 0.20 + joint * 0.13;
      tone = (0.72 + id * 0.25) * (0.89 + grain * 0.20);
      hue = (id - 0.5) * 0.18;
    } else if (kind === 'wood' || kind === 'paintedWood') {
      // The board's longitudinal direction is +V. Growth rings bend
      // around a few embedded knots; fine pores follow that same flow.
      const warp = (fbm(u*4,v*2,4,2,seed+67,3)-.5)*.075;
      let flow=u+warp,knot=0,knotRing=0;
      for(let k=0;k<2;k++){
        const kx=.16+hash2(k,17,seed)*.68,ky=.15+hash2(k,39,seed)*.7;
        const dx=wrap(u-kx+.5,1)-.5,dy=wrap(v-ky+.5,1)-.5;
        const radius=Math.hypot(dx/.062,dy/.18),weight=Math.exp(-radius*radius*.65);
        flow+=dx/(Math.abs(dx)+.035)*weight*.042;
        knot=Math.max(knot,weight);
        knotRing+=weight*(.5+.5*Math.sin(radius*17+warp*30));
      }
      const rings = Math.pow(.5+.5*Math.sin((flow*Math.round(scale*8)+warp)*Math.PI*2),8);
      const pores = smooth(.57,.77,fbm(u*160,v*18,160,18,seed+89,2));
      const grainDark = Math.max(rings*.75,knotRing*.72+knot*.18);
      const painted = kind === 'paintedWood';
      height = .67 - grainDark*(painted?.07:.24)-pores*(painted?.015:.1)+(grain-.5)*.025;
      roughness = (painted?.86:.9)+pores*.08+grain*.04;
      tone = painted ? .98-grainDark*.075-pores*.025
        : .94-grainDark*.24-pores*.12-knot*.08+(grain-.5)*.08;
      hue = (grain-.5)*(painted?.025:.065);
    } else {
      // Alternating over/under yarns, elliptical cross sections and
      // longitudinal fibers. A plain checkerboard has no woven relief.
      const px = u * 64, py = v * 64;
      const ix = Math.floor(px), iy = Math.floor(py);
      const fx = fract(px), fy = fract(py);
      const over = (ix + iy) % 2;
      const warp = Math.pow(Math.max(0, Math.sin(fx * Math.PI)), 0.7);
      const weft = Math.pow(Math.max(0, Math.sin(fy * Math.PI)), 0.7);
      const yarn = over ? warp : weft;
      const underside = over ? weft : warp;
      height = 0.12 + yarn * 0.63 + underside * (1 - yarn) * 0.18;
      const fiber = Math.sin((over ? fx : fy) * Math.PI * 6) * 0.035;
      height += fiber * yarn;
      roughness = 0.86 + (1 - yarn) * 0.12;
      tone = (0.74 + yarn * 0.21 + grain * 0.07) * (over ? 1 : 0.91);
    }
    const defaultContrast=kind==='brick'?.4:kind==='cobble'?.5:kind==='fabric'?.22:kind==='paintedWood'?.18:.4;
    tone=1-(1-tone)*contrast/defaultContrast;
    const at = (y * size + x) * 4;
    const byte = (value) => Math.round(Math.max(0, Math.min(1, value)) * 255);
    color[at] = byte(tone * (1 + hue));
    color[at + 1] = byte(tone * (1 + hue * 0.15));
    color[at + 2] = byte(tone * (1 - hue));
    color[at + 3] = 255;
    surface[at] = byte(height);
    surface[at + 1] = byte(roughness);
    surface[at + 2] = byte(joint);
    surface[at + 3] = 255;
  }
  const result = { map: texture(color, size, true), surface: texture(surface, size) };
  _structureCache.set(key, result);
  return result;
}

// ------------------------------------------------------------ factories

function shade(hex, k) {
  const c = new THREE.Color(hex);
  // Spread is a RATIO of the colour's own lightness, never a fixed
  // subtraction: dark colours have tiny linear-space lightness, and a
  // fixed step clamped them to BLACK — an albedo that cannot be lit.
  const hsl = c.getHSL({ h: 0, s: 0, l: 0 });
  c.setHSL(hsl.h, hsl.s, Math.max(hsl.l * 0.5, hsl.l + k));
  return c;
}

// ONE MATERIAL PER DISTINCT LOOK, not per call. A city fabric asking for
// a fresh material per building measured 2,552 of them in one scene, each
// its own state change and its own 256 px texture pair (1,309 textures,
// 436 MB). Variant is quantised first: nobody can tell 2,552 shades apart
// across a +-4% lightness spread, and the buckets collapse textures too.
const _matCache = new Map();
const _tintCache = new Map();
// ODD count on purpose: variant 0.5 means THE COLOUR THE AUTHOR
// WROTE, and an even bucket count has no midpoint to land on.
const _VARIANT_STEPS = 9;
// Seed only picks WHICH noise pattern, not what the surface looks like.
// Assets hand a fresh seed per copy, so an unbucketed seed gave every
// one of 770 trees its own material and its own texture pair.
const _SEED_BUCKETS = 6;

function _quantise(v) {
  const c = Math.min(1, Math.max(0, v));
  return Math.round(c * (_VARIANT_STEPS - 1)) / (_VARIANT_STEPS - 1);
}

function _seedBucket(s) {
  return Math.abs(Math.round(s === undefined ? 3 : s)) % _SEED_BUCKETS;
}

/** Cache key for a RESOLVED option set, or null when uncacheable. */
function _matKey(o, variant) {
  if (o.unique) return null;
  try {
    const rest = Object.assign({}, o);
    delete rest.variant;
    delete rest.unique;
    rest.seed = _seedBucket(o.seed);
    return JSON.stringify([rest, variant], (k, v) =>
        (v && v.isColor ? v.getHex() : v));
  } catch (err) {
    return null;
  }
}

/**
 * Build a textured MeshStandardMaterial.
 *
 * Identical options return the SAME material instance; pass
 * `unique: true` when a caller means to mutate its own copy.
 *
 * @param {object} o color, roughness, metalness, scale, seed, variant,
 *     repeat, streak, contrast, bump, tintNoise, extra, unique
 * @returns {THREE.MeshStandardMaterial}
 */
function make(o) {
  // Unspecified variant = THE COLOUR THE AUTHOR WROTE (0.5, the
  // midpoint); defaulting to 0 handed every caller the dark extreme.
  const variant = _quantise(o.variant === undefined ? 0.5 : o.variant);
  const key = _matKey(o, variant);
  if (key !== null && _matCache.has(key)) return _matCache.get(key);
  const seed = _seedBucket(o.seed) + Math.round(variant * 97);
  const col = shade(o.color, (variant - 0.5) * (o.spread === undefined ? 0.08 : o.spread));
  const structured = o.structure ? structuredTextures(o.structure, seed, o) : null;
  const map = structured?.map || noiseTexture({
    scale: o.scale || 6, seed, contrast: o.contrast === undefined ? 0.3 : o.contrast,
    streak: o.streak || 0, size: 256,
    // Mineral surfaces carry the most hue break (a granite face is
    // pink, grey and near-black at arm's length); paint and metal the
    // least, because a factory finish really is one colour.
    hueBreak: o.hueBreak === undefined ? 0.12 : o.hueBreak,
  });
  const rough = structured?.surface || noiseTexture({
    scale: (o.scale || 6) * 1.7, seed: seed + 41,
    contrast: 0.5, base: 1, streak: o.streak || 0, linear: true, size: 256,
  });
  const rep = o.repeat || 1;
  const m = new THREE.MeshStandardMaterial(Object.assign({
    color: col,
    map,
    roughnessMap: rough,
    bumpMap: o.bump === 0 ? null : (structured?.surface || map),
    bumpScale: o.bump === undefined ? 0.015 : o.bump,
    roughness: o.roughness,
    metalness: o.metalness === undefined ? 0 : o.metalness,
  }, o.extra || {}));
  if (rep !== 1) {
    const copies = new Map();
    for (const slot of ['map', 'roughnessMap', 'bumpMap', 'normalMap']) {
      const source = m[slot];
      if (!source?.isTexture) continue;
      let copy = copies.get(source);
      if (!copy) {
        copy = source.clone();
        copy.repeat.set(rep, rep);
        copy.needsUpdate = true;
        copies.set(source, copy);
      }
      m[slot] = copy;
    }
  }
  if (structured && m.map?.source === structured.map.source
      && (o.structure === 'brick' || o.structure === 'cobble')) {
    patchStandard(m, {
      name: 'materials:structure',
      uniforms: {
        uMaterialStructure: { value: structured.surface },
        uMaterialJointColor: { value: new THREE.Color(o.jointColor
          ?? (o.structure === 'brick' ? 0xa69b86 : 0x36372f)) },
      },
      fragmentHead: 'uniform sampler2D uMaterialStructure;\nuniform vec3 uMaterialJointColor;',
      fragmentBody: [
        '#ifdef USE_MAP',
        'float materialJoint = texture2D(uMaterialStructure, vMapUv).b;',
        'diffuseColor.rgb = mix(diffuseColor.rgb, uMaterialJointColor, materialJoint);',
        '#endif',
      ].join('\n'),
      metalnessBody: [
        '#ifdef USE_MAP',
        'metalnessFactor *= 1.0 - materialJoint;',
        '#endif',
      ].join('\n'),
    });
  }
  if (key !== null) {
    m.userData.shared = true;
    _matCache.set(key, m);
  }
  return m;
}

export const plaster = (o = {}) => make(Object.assign(
    { color: 0xeae0d0, roughness: 0.85, scale: 5, contrast: 0.22, bump: 0.01 }, o));
export const brick = (o = {}) => make(Object.assign(
    { color: 0x9c4a35, roughness: 0.9, scale: 14, contrast: 0.4,
      bump: 0.012, structure: 'brick' }, o));
export const travertine = (o = {}) => make(Object.assign(
    { color: 0xd9cdb4, roughness: 0.7, scale: 4, contrast: 0.3, streak: 0.4 }, o));
export const granite = (o = {}) => make(Object.assign(
    { color: 0x8d8b88, roughness: 0.6, scale: 22, contrast: 0.45,
      hueBreak: 0.16 }, o));
export const cobble = (o = {}) => make(Object.assign(
    { color: 0x6f6a63, roughness: 0.88, scale: 26, contrast: 0.5, bump: 0.04,
      hueBreak: 0.16, structure: 'cobble' }, o));
export const asphalt = (o = {}) => make(Object.assign(
    { color: 0x2f3134, roughness: 0.82, scale: 30, contrast: 0.25 }, o));
export const terracotta = (o = {}) => make(Object.assign(
    { color: 0xb2593a, roughness: 0.78, scale: 10, contrast: 0.32 }, o));
export const weatheredWood = (o = {}) => make(Object.assign(
    { color: 0x7a5a3c, roughness: 0.92, scale: 8, contrast: 0.4, streak: 0.9,
      bump: 0.006, structure: 'wood' }, o));
export const paintedWood = (o = {}) => make(Object.assign(
    { color: 0xc8b18a, roughness: 0.6, scale: 7, contrast: 0.18, streak: 0.8,
      hueBreak: 0.05, bump: 0.0015, structure: 'paintedWood' }, o));
export const paintedIron = (o = {}) => make(Object.assign(
    { color: 0x2d3a3f, roughness: 0.45, metalness: 0.35, scale: 9,
      contrast: 0.16, hueBreak: 0.04 }, o));
export const brushedSteel = (o = {}) => make(Object.assign(
    { color: 0xb9bfc4, roughness: 0.35, metalness: 0.85, scale: 18,
      contrast: 0.12, streak: 0.95, bump: 0.004, hueBreak: 0.03 }, o));
export const giltBronze = (o = {}) => make(Object.assign(
    { color: 0xb08a3a, roughness: 0.32, metalness: 0.9, scale: 12,
      contrast: 0.2 }, o));
export const fabric = (o = {}) => make(Object.assign(
    { color: 0xb04b46, roughness: 0.95, scale: 40, contrast: 0.22,
      bump: 0.0008, structure: 'fabric' }, o));
export const foliage = (o = {}) => make(Object.assign(
    { color: 0x3f6b32, roughness: 0.8, scale: 16, contrast: 0.35,
      hueBreak: 0.20, extra: { flatShading: false } }, o));
export const soil = (o = {}) => make(Object.assign(
    { color: 0x6a5540, roughness: 0.95, scale: 20, contrast: 0.4, bump: 0.04,
      hueBreak: 0.18 }, o));
export const skin = (o = {}) => make(Object.assign(
    { color: 0xd9a17c, roughness: 0.68, scale: 30, contrast: 0.08, bump: 0,
      hueBreak: 0.06 }, o));

/**
 * Water: low roughness + metalness so the scene environment carries the
 * reflection, with a scrolling normal-ish bump for surface break-up.
 * @param {object} o color, roughness, scale, variant
 * @returns {THREE.MeshStandardMaterial}
 */
export const water = (o = {}) => {
  // Water is a DIELECTRIC: metalness stays 0 and the ior-1.333 fresnel
  // ramp produces the reflection. Any metalness makes a uniformly
  // tinted mirror at every angle — the "dirty chrome puddle" look.
  const base = make(Object.assign(
      { color: 0x14414f, roughness: 0.06, metalness: 0, scale: 12,
        contrast: 0.1, bump: 0.02 }, o));
  const m = new THREE.MeshPhysicalMaterial({
    color: base.color,
    map: base.map,
    roughnessMap: base.roughnessMap,
    bumpMap: base.bumpMap,
    bumpScale: base.bumpScale,
    roughness: o.roughness === undefined ? 0.06 : o.roughness,
    metalness: 0,
    ior: o.ior === undefined ? 1.333 : o.ior,
    // Deep water is not see-through at scene scale; the darkness IS the
    // depth. Shallows pass `opacity` down to let the bed read through.
    transparent: o.opacity !== undefined,
    opacity: o.opacity === undefined ? 1 : o.opacity,
  });
  return Object.assign(m, o.extra || {});
};

/**
 * Glass: needs a real scene.environment to read as glass.
 * @param {object} o color, opacity
 * @returns {THREE.MeshStandardMaterial}
 */
export const glass = (o = {}) => new THREE.MeshPhysicalMaterial(
    Object.assign({
      color: o.color === undefined ? 0xbfd6de : o.color,
      roughness: 0.05,
      // Dielectric like water: the ior-1.5 fresnel ramp mirrors sky on
      // grazing faces while camera-facing glass stays readable.
      metalness: 0,
      ior: 1.5,
      transparent: true,
      opacity: o.opacity === undefined ? 0.35 : o.opacity,
      envMapIntensity: 1.6,
    }, o.extra || {}));

/**
 * Darken and roughen a material to read as weathered/dirty.
 * @param {THREE.Material} mat material to age IN PLACE
 * @param {number} amount 0..1
 * @returns {THREE.Material} the same material
 */
export function weather(mat, amount = 0.35) {
  if (!mat) return mat;
  // A cached material is shared by every caller that asked for the same
  // look, so ageing in place would age the whole city. Copy first.
  const m = mat.userData && mat.userData.shared ? clonePatchedMaterial(mat) : mat;
  // Ratio, never a fixed step: getHSL works in LINEAR space, where a
  // dark colour's lightness is tiny — offsetHSL drove asphalt to pure
  // black, an albedo nothing can light. Same fix `shade()` already has.
  if (m.color) {
    const hsl = m.color.getHSL({ h: 0, s: 0, l: 0 });
    m.color.setHSL(hsl.h, hsl.s * (1 - 0.28 * amount),
                   hsl.l * (1 - 0.45 * amount));
  }
  if (typeof m.roughness === 'number') {
    m.roughness = Math.min(1, m.roughness + 0.18 * amount);
  }
  if (typeof m.bumpScale === 'number') m.bumpScale *= 1 + amount;
  return m;
}

/**
 * Per-instance colour jitter: clone the material and shift hue/lightness
 * so copies of one asset stop reading as duplicated geometry.
 * @param {THREE.Material} mat template material (not mutated)
 * @param {number} variant 0..1
 * @returns {THREE.Material} the tinted clone
 */
export function tint(mat, variant = 0.5) {
  const v = _quantise(variant);
  const key = mat.uuid + ':' + mat.version + ':' + v;
  const hit = _tintCache.get(key);
  if (hit) return hit;
  const m = clonePatchedMaterial(mat);
  m.userData.shared = true;
  if (m.color) {
    m.color.offsetHSL((v - 0.5) * 0.04, 0, 0);
    const hsl = m.color.getHSL({ h: 0, s: 0, l: 0 });
    m.color.setHSL(hsl.h, hsl.s, hsl.l * (1 + (v - 0.5) * 0.5));
  }
  _tintCache.set(key, m);
  return m;
}
