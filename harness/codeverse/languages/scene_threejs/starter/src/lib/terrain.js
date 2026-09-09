/**
 * The ground, textured and seatable, in one call (the model takes CODE
 * and ignores prose, so the ground ships as a function). `ground()`
 * returns the SAME height function it displaced the mesh with — place
 * every asset at `h(x, z)`; floating assets are the most-flagged defect.
 */

import * as THREE from 'three';
import * as MAT from './materials.js';

/**
 * Build a displaced, textured ground plane and its height function.
 *
 * @param {object} opts
 *   `size` metres across (default 400), `segments` grid resolution
 *   (default 128, the performance ceiling), `rand` a seeded PRNG
 *   (REQUIRED — no Math.random), `material` a THREE material (defaults
 *   to `MAT.soil()`), `relief` peak-to-trough metres (default 6),
 *   `scale` feature size in metres (default 90), `flat` a function
 *   (x, z) => 0..1 that damps relief to 0 where the scene needs level
 *   ground (a plaza, a quay, a street grid), `name` (default 'Ground').
 * @returns {{mesh: THREE.Mesh, height: (x: number, z: number) => number}}
 *   The mesh to add, and the height function EVERY placement must use.
 */
export function ground(opts = {}) {
  const size = opts.size || 400;
  const segs = Math.min(opts.segments || 128, 128);
  const rand = opts.rand || (() => 0.5);
  const relief = opts.relief === undefined ? 6 : opts.relief;
  const scale = opts.scale || 90;

  // A small seeded lattice, bilinearly sampled: the same field drives the
  // mesh and the height function, so they cannot drift apart.
  const N = 32;
  const lat = new Float32Array(N * N);
  for (let i = 0; i < N * N; i++) lat[i] = rand();
  const sample = (x, z) => {
    // wrap into [0, N): `u + N` for a u of -1e-16 rounds to N itself (below one ulp of 32),
    // and lat[j * N + N] is the next row or undefined — measured 2026-09-08, 15 NaN vertices
    // in an outskirts ring where the polar grid's x came out as -1.5e-14 (D71)
    const wrap = (t) => { let w = t % N; if (w < 0) w += N; return w >= N ? w - N : w; };
    const uu = wrap(x / scale), vv = wrap(z / scale);
    const i0 = Math.floor(uu), j0 = Math.floor(vv);
    const fx = uu - i0, fz = vv - j0;
    const i1 = (i0 + 1) % N, j1 = (j0 + 1) % N;
    const a = lat[j0 * N + i0], b = lat[j0 * N + i1];
    const c = lat[j1 * N + i0], d = lat[j1 * N + i1];
    return (a * (1 - fx) + b * fx) * (1 - fz) + (c * (1 - fx) + d * fx) * fz;
  };

  const height = (x, z) => {
    let n = sample(x, z) * 0.65 + sample(x * 2.9, z * 2.9) * 0.35;
    n = (n - 0.5) * 2 * relief;
    return opts.flat ? n * (1 - Math.min(1, Math.max(0, opts.flat(x, z)))) : n;
  };

  const geo = new THREE.PlaneGeometry(size, size, segs, segs);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    pos.setY(i, height(pos.getX(i), pos.getZ(i)));
  }
  geo.computeVertexNormals();

  // TEXTURED by default, and tiled to the ground's real size so the
  // pattern reads at metres rather than smearing across 400 m.
  //
  // On a COPY, always.  `MAT.soil()` (and every other factory) returns a
  // CACHED material whose maps are shared with every other caller of that
  // look, so retiling in place reached back through the cache: measured
  // 2026-09-01, a wall built with `MAT.soil()` before a 480 m `ground()`
  // came out at repeat 40x40, and two grounds of different sizes fought
  // over one texture.  A Texture clone shares its `.source`, so the copy
  // is a few bytes of state, not a second upload.
  let material = opts.material || MAT.soil();
  if (material.userData && material.userData.shared) {
    material = material.clone();
    material.userData = {};
  }
  const retiled = new Map();
  for (const key of ['map', 'roughnessMap', 'bumpMap', 'normalMap']) {
    const tex = material[key];
    if (!tex || !tex.repeat) continue;
    // bumpMap is usually the SAME texture object as map: clone it once,
    // or the two slots tile independently and the bump slides off the
    // colour it is supposed to emboss.
    let copy = retiled.get(tex);
    if (!copy) {
      copy = tex.clone();
      copy.wrapS = copy.wrapT = THREE.RepeatWrapping;
      copy.repeat.set(size / 12, size / 12);
      copy.needsUpdate = true;
      retiled.set(tex, copy);
    }
    material[key] = copy;
  }

  const mesh = new THREE.Mesh(geo, material);
  mesh.name = opts.name || 'Ground';
  mesh.receiveShadow = true;
  return { mesh, height };
}

/*
 * A cliff face is ONE connected ribbon, never a pile of slabs (slab
 * piles read as buildings). Strata are VERTEX-COLOUR bands sampled on
 * the displaced surface so a band cannot detach; the same field backs
 * `faceAt(s, y)` so props seat ON the measured rock.
 */

/**
 * Build one connected, displaced cliff ribbon and its face function.
 * Spans x in [-length/2, length/2], base on y = 0, FACES +Z; parent
 * face-seated props to the mesh.
 *
 * @param {object} opts
 *   `length` metres along the wall (default 200), `height` metres
 *   (default 40), `rand` a seeded PRNG (REQUIRED — no Math.random),
 *   `strata` band count OR an array of hex band colours (default 6
 *   seeded variations of `color`), `color` base rock hex (default
 *   0x8a7a66), `relief` ledge/gully depth in metres (default
 *   height * 0.15, clamped 2..8), `name` (default 'Cliff').
 * @returns {{mesh: THREE.Mesh, faceAt: (s: number, y: number) =>
 *     THREE.Vector3}}
 *   ONE mesh (zero children — nothing to float), and `faceAt(s, y)` —
 *   `s` metres along the wall from its left end (0..length), `y`
 *   metres up from the base — returning the point ON the displaced
 *   face in the mesh's local space. Offset ~0.2 m along +Z to keep a
 *   seated prop clear of the rock.
 */
export function cliff(opts = {}) {
  const length = opts.length || 200;
  const height = opts.height || 40;
  const rand = opts.rand || (() => 0.5);
  const relief = opts.relief === undefined
      ? Math.min(8, Math.max(2, height * 0.15)) : opts.relief;

  // Strata band colours: authored hexes, or seeded variations of the
  // base rock colour.
  let bandColors;
  if (Array.isArray(opts.strata) && opts.strata.length) {
    bandColors = opts.strata.map((c) => new THREE.Color(c));
  } else {
    const n = (typeof opts.strata === 'number' && opts.strata > 0)
        ? Math.round(opts.strata) : 6;
    const base = new THREE.Color(
        opts.color === undefined ? 0x8a7a66 : opts.color);
    bandColors = [];
    for (let i = 0; i < n; i++) {
      const c = base.clone();
      c.offsetHSL((rand() - 0.5) * 0.04, (rand() - 0.5) * 0.1,
                  (rand() - 0.5) * 0.2);
      bandColors.push(c);
    }
  }
  const bands = bandColors.length;
  const bandH = height / bands;

  // One seeded lattice drives the mesh, the band edges AND faceAt, so
  // none of them can drift apart (the same trick ground() uses).
  const N = 32;
  const lat = new Float32Array(N * N);
  for (let i = 0; i < N * N; i++) lat[i] = rand();
  const sample = (u, v) => {
    const uu = ((u % N) + N) % N, vv = ((v % N) + N) % N;
    const i0 = Math.floor(uu), j0 = Math.floor(vv);
    const fx = uu - i0, fz = vv - j0;
    const i1 = (i0 + 1) % N, j1 = (j0 + 1) % N;
    const a = lat[j0 * N + i0], b = lat[j0 * N + i1];
    const c = lat[j1 * N + i0], d = lat[j1 * N + i1];
    return (a * (1 - fx) + b * fx) * (1 - fz) + (c * (1 - fx) + d * fx) * fz;
  };

  // Per-band ledge offsets and a few meandering gullies, all seeded up
  // front so faceAt closes over the exact numbers the mesh used.
  const bandStep = [];
  for (let i = 0; i < bands; i++) bandStep.push((rand() - 0.5) * 2);
  const gullies = [];
  const nGullies = Math.max(2, Math.round(length / 70));
  for (let k = 0; k < nGullies; k++) {
    gullies.push({
      x: (rand() - 0.5) * length * 0.9,
      w: 3 + rand() * 7,
      d: 0.5 + rand() * 0.6,
      drift: (rand() - 0.5) * 14,
      phase: rand() * Math.PI * 2,
    });
  }

  const bandOf = (x, y) => {
    // Band edges undulate with the rock instead of ruler lines;
    // sampled per-vertex on the same geometry, so they cannot detach.
    const wob = (sample(x / 26 + 5.5, 7.7) - 0.5) * bandH * 0.7;
    const i = Math.floor((y + wob) / bandH);
    return Math.min(bands - 1, Math.max(0, i));
  };

  const depth = (x, y) => {
    const n = sample(x / 16, y / 16) * 0.6 + sample(x / 4, y / 4) * 0.4;
    let z = (n - 0.5) * relief;                      // rough rock
    z += bandStep[bandOf(x, y)] * relief * 0.3;      // stratum ledge
    for (const g of gullies) {                       // carved gullies
      const gx = g.x + Math.sin(g.phase + (y / height) * 4) * g.drift;
      const t = (x - gx) / g.w;
      z -= Math.exp(-t * t) * g.d * relief;
    }
    return z;
  };

  const segX = Math.min(240, Math.max(24, Math.round(length / 1.25)));
  const segY = Math.min(64, Math.max(8, Math.round(height / 1.25)));
  const geo = new THREE.PlaneGeometry(length, height, segX, segY);
  geo.translate(0, height / 2, 0);                   // base rests on y = 0
  const pos = geo.attributes.position;
  const col = new Float32Array(pos.count * 3);
  const tone = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = pos.getY(i);
    const z = depth(x, y);
    pos.setZ(i, z);
    tone.copy(bandColors[bandOf(x, y)]);
    // Cheap AO: recessed rock reads darker, ledges catch the light.
    tone.offsetHSL(0, 0, Math.max(-1, Math.min(1, z / relief)) * 0.09);
    col[i * 3] = tone.r;
    col[i * 3 + 1] = tone.g;
    col[i * 3 + 2] = tone.b;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  geo.computeVertexNormals();

  // Cloned so setting repeat cannot leak into the shared texture cache.
  const map = MAT.noiseTexture({ scale: 20, seed: 9, contrast: 0.4 }).clone();
  const rough = MAT.noiseTexture(
      { scale: 34, seed: 50, contrast: 0.5, linear: true }).clone();
  map.repeat.set(length / 20, height / 20);
  rough.repeat.copy(map.repeat);
  map.needsUpdate = true;
  rough.needsUpdate = true;
  const material = new THREE.MeshStandardMaterial({
    vertexColors: true,
    map,
    roughnessMap: rough,
    bumpMap: map,
    bumpScale: 0.05,
    roughness: 0.95,
  });

  const mesh = new THREE.Mesh(geo, material);
  mesh.name = opts.name || 'Cliff';
  mesh.castShadow = true;
  mesh.receiveShadow = true;

  const faceAt = (s, y) => {
    const x = Math.max(-length / 2, Math.min(length / 2, s - length / 2));
    const yy = Math.max(0, Math.min(height, y));
    return new THREE.Vector3(x, yy, depth(x, yy));
  };

  return { mesh, faceAt };
}
