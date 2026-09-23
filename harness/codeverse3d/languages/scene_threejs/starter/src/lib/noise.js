/**
 * Seeded noise substrate: fBm fields, procedural DataTextures, and
 * terrain displacement. ImprovedNoise is UNSEEDED and the platform's
 * global RNG is banned, so `mulberry32` is the sanctioned PRNG (`lehmer`
 * only keeps the older effects tuned on it where they were) and
 * fbm2/fbm3 seed the lattice via per-octave domain offsets. Other lib
 * modules build on this — keep the exported signatures stable.
 * (The ban is stated without naming the call: the scene_threejs lint
 * greps sources for it and a doc comment is not an exemption.)
 */

import * as THREE from 'three';
import { ImprovedNoise } from 'three/addons/math/ImprovedNoise.js';

const _noise = new ImprovedNoise();

/**
 * The sanctioned seeded PRNG — same seed, same sequence, every run.
 * Never the platform's global RNG.
 *
 * @param {number} seed Any integer.
 * @returns {() => number} A function returning uniform values in [0, 1).
 */
export function mulberry32(seed) {
  let a = seed | 0;
  return function () {
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * The 16807 (Park-Miller) stream the older effects were tuned on —
 * celestial, godrays, veils, watermist, submerged and
 * `instanceVariation`. Kept because moving them to `mulberry32` would
 * move every star, shaft and drop they place; new code takes that.
 *
 * @param {number} seed Any integer; 0 is taken as 1.
 * @returns {() => number} A function returning uniform values in (0, 1).
 */
export function lehmer(seed) {
  let s = (seed >>> 0) || 1;
  return () => ((s = (s * 16807) % 2147483647) / 2147483647);
}

// Seed-derived per-octave domain offsets; they also de-align octave
// zeros so grid artifacts can't stack. Cached: fbm runs per vertex.
const _offsetCache = new Map();

function _offsets(seed, octaves) {
  const key = seed + ':' + octaves;
  let offs = _offsetCache.get(key);
  if (!offs) {
    const rand = mulberry32(seed);
    offs = [];
    for (let o = 0; o < octaves; o++) {
      offs.push([rand() * 256, rand() * 256, rand() * 256]);
    }
    // Scene previews routinely cycle through fresh seeds. Eviction only
    // recomputes deterministic offsets; it cannot change a field's values.
    if (_offsetCache.size >= 256) _offsetCache.delete(_offsetCache.keys().next().value);
    _offsetCache.set(key, offs);
  }
  return offs;
}

/**
 * Seeded 3D fractal Brownian motion over ImprovedNoise.
 *
 * @param {number} x Sample coordinate.
 * @param {number} y Sample coordinate.
 * @param {number} z Sample coordinate.
 * @param {object} [opts] `octaves` (default 4), `lacunarity` (default
 *   2), `gain` (default 0.5), `seed` (default 1).
 * @returns {number} Normalized field value in measured about +-0.25 with a seed- and region-dependent bias, NOT the [-1, 1] the octave sum suggests.
 */
export function fbm3(x, y, z, opts = {}) {
  const octaves = opts.octaves ?? 4;
  const lacunarity = opts.lacunarity ?? 2;
  const gain = opts.gain === undefined ? 0.5 : opts.gain;
  if (!Number.isInteger(octaves) || octaves < 0 || octaves > 32 ||
      ![x, y, z, lacunarity, gain].every(Number.isFinite) || gain < 0) {
    throw new RangeError('fbm3: use finite coordinates, 0..32 integer octaves and nonnegative gain');
  }
  if (octaves === 0) return 0;
  const offs = _offsets(opts.seed === undefined ? 1 : opts.seed, octaves);
  let amp = 1;
  let freq = 1;
  let sum = 0;
  let norm = 0;
  for (let o = 0; o < octaves; o++) {
    const off = offs[o];
    sum += amp * _noise.noise(
      x * freq + off[0], y * freq + off[1], z * freq + off[2]);
    norm += amp;
    amp *= gain;
    freq *= lacunarity;
  }
  const value = sum / norm;
  if (!Number.isFinite(value)) throw new RangeError('fbm3: octave parameters overflow');
  return value;
}

/**
 * Seeded 2D fBm — the terrain/map workhorse. Feed it PRE-SCALED
 * coordinates: `fbm2(x * f, z * f)` where `f ~ 1 / featureMetres`
 * (0.02 gives ~50 m hills).
 *
 * @param {number} x Sample coordinate.
 * @param {number} z Sample coordinate.
 * @param {object} [opts] Same options as `fbm3`.
 * @returns {number} Normalized field value in measured about +-0.25 with a seed- and region-dependent bias, NOT the [-1, 1] the octave sum suggests.
 */
export function fbm2(x, z, opts = {}) {
  return fbm3(x, 0, z, opts);
}

/**
 * Bake a per-pixel function into a tiling RGBA DataTexture — the only
 * way to get a texture in this pipeline (npm three ships no image
 * assets, and asset modules are DOM-free so no canvas).
 *
 * @param {number} [size] Texture width and height in px (default 256).
 * @param {(u: number, v: number) => (number|Array<number>)} [fn]
 *   Called per pixel with u, v in [0, 1); returns a 0..1 grey value or
 *   an [r, g, b] / [r, g, b, a] array of 0..1 channels. Defaults to a
 *   mid-grey fBm breakup field. RepeatWrapping is set, but seamless
 *   tiling is the fn's job — build tiling fns from integer-frequency
 *   sines or sample fbm2 at a low frequency so the seam stays subtle.
 * @returns {THREE.DataTexture} RepeatWrapping, needsUpdate already set.
 */
export function noiseDataTexture(size = 256, fn) {
  if (!Number.isInteger(size) || size < 1 || size > 4096) {
    throw new RangeError('noiseDataTexture: size must be an integer from 1 to 4096');
  }
  const f = fn ||
      ((u, v) => 0.5 + 0.35 * fbm2(u * 6, v * 6, { seed: 1 }));
  const data = new Uint8Array(size * size * 4);
  const byte = (c) => Math.max(0, Math.min(255, Math.round(c * 255)));
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const v = f(x / size, y / size);
      const i = (y * size + x) * 4;
      if (typeof v === 'number') {
        data[i] = data[i + 1] = data[i + 2] = byte(v);
        data[i + 3] = 255;
      } else {
        data[i] = byte(v[0]);
        data[i + 1] = byte(v[1]);
        data[i + 2] = byte(v[2]);
        data[i + 3] = v.length > 3 ? byte(v[3]) : 255;
      }
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  // DataTexture defaults to NearestFilter with no mipmaps. Used as a
  // puddle alpha mask on a ground plane, one screen pixel then spans
  // dozens of texels and the wet/dry edge tears into stair-stepped
  // slabs at grazing angles (2026-08-04 audit, Shibuya).
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Trilinear read of a cubic n×n×n grid stored x-fastest, `stride` values per
 * cell.  Coordinates are in cells; `periodic` wraps them, otherwise they clamp
 * to the edge.  The CPU twin of sampling a Data3DTexture, so a density query
 * and the texture it baked agree.
 *
 * @returns {number} The interpolated value of `channel`.
 */
export function sampleGrid3(data, n, x, y, z, stride = 1, channel = 0, periodic = true) {
  const ix = Math.floor(x), iy = Math.floor(y), iz = Math.floor(z);
  const fx = x - ix, fy = y - iy, fz = z - iz;
  const index = (v) => periodic ? ((v % n) + n) % n : Math.max(0, Math.min(n - 1, v));
  let value = 0;
  for (let dz = 0; dz < 2; dz++) for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
    const weight = (dx ? fx : 1 - fx) * (dy ? fy : 1 - fy) * (dz ? fz : 1 - fz);
    value += data[((index(iz + dz) * n + index(iy + dy)) * n + index(ix + dx)) * stride + channel] * weight;
  }
  return value;
}

/**
 * An RGBA8 cubic Data3DTexture, linearly filtered, repeating unless
 * `repeat` is false (then clamped to the edge).  needsUpdate is set.
 */
export function dataTexture3D(data, size, repeat = true) {
  const texture = new THREE.Data3DTexture(data, size, size, size);
  texture.format = THREE.RGBAFormat;
  texture.minFilter = texture.magFilter = THREE.LinearFilter;
  texture.wrapS = texture.wrapT = texture.wrapR = repeat ? THREE.RepeatWrapping : THREE.ClampToEdgeWrapping;
  texture.unpackAlignment = 1;
  texture.needsUpdate = true;
  return texture;
}

/**
 * Displace a geometry's Y by seeded fBm and refresh its normals.
 * Expects an XZ-plane geometry (`PlaneGeometry` after `rotateX`);
 * displacement ADDS to existing Y, so pre-shaped geometry keeps shape.
 *
 * @param {THREE.BufferGeometry} geometry Modified in place.
 * @param {number} amp Field multiplier in metres (fBm typically spans about
 *   [-0.25, 0.25], depending on seed and sampled region).
 * @param {number} freq Spatial frequency, ~1 / featureMetres (0.02
 *   gives ~50 m hills).
 * @param {number} [seed] Noise seed (default 1).
 * @returns {THREE.BufferGeometry} The same geometry, for chaining.
 */
export function displaceY(geometry, amp, freq, seed) {
  const pos = geometry.attributes.position;
  const opts = { seed: seed === undefined ? 1 : seed };
  for (let i = 0; i < pos.count; i++) {
    pos.setY(i, pos.getY(i) +
        amp * fbm2(pos.getX(i) * freq, pos.getZ(i) * freq, opts));
  }
  pos.needsUpdate = true;
  geometry.computeVertexNormals();
  if (geometry.boundingBox) geometry.computeBoundingBox();
  if (geometry.boundingSphere) geometry.computeBoundingSphere();
  return geometry;
}
