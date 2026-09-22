/**
 * Canopy: a crown that reads as LEAVES rather than as a green solid.
 *
 * The failure this replaces is not a lack of effort. A builder given a
 * crown reaches for a displaced Icosahedron, and a good one stacks 32
 * of them with four octaves of noise — the measured autumn avenue did
 * exactly that. It still reads as painted rock, because every one of
 * those blobs is a CLOSED CONTINUOUS SURFACE: no gap for the sky to
 * come through, one normal field over the whole mass, and one albedo.
 * Silhouette is what tells an eye "leaves", and a convex hull has none.
 *
 * So a crown here is a few thousand separate ovate cards, each with its
 * own outward facing, its own tilt and its own hue. Three consequences
 * follow, and they are the point:
 *  - the edge breaks up, because the edge IS leaves;
 *  - light passes BETWEEN them, so the crown has depth instead of a
 *    lit side and a dark side;
 *  - the colour is mixed by the eye, not by the material. Neighbouring
 *    leaves sit up to `hue` radians apart, which is what a broken-colour
 *    painter does and what a single MeshStandardMaterial cannot do
 *    however carefully its one colour is chosen.
 *
 * Every crown you pass is ONE draw call together — `position` stays at
 * zero and each leaf is built in the vertex shader from its instance
 * attributes, the same construction `makeGrass` uses for blades. A
 * woodland of 40 trees is one mesh, not 40, and not the 158k separate
 * meshes the avenue shipped.
 */

import * as THREE from 'three';
import { fbm3, mulberry32 } from './noise.js';
import { patchStandard, shadowLike, tickShaders } from './shader.js';
import { patchLeafSSS } from './foliage_shade.js';
import { windOf } from './grass.js';

const LOCAL_DIR = [
  'vec3 leafLocalDir(vec3 w) {',
  '  mat3 m = mat3(modelMatrix);',
  '  return vec3(dot(w, m[0]) / max(dot(m[0], m[0]), 1e-6),',
  '              dot(w, m[1]) / max(dot(m[1], m[1]), 1e-6),',
  '              dot(w, m[2]) / max(dot(m[2], m[2]), 1e-6));',
  '}',
].join('\n');

/**
 * Build the leaf mass for one or more crowns.
 *
 * Deterministic in `seed`: same seed, same leaves. Drive it from your
 * `tick()` — `canopy.userData.tick(t)` — or nothing moves.
 *
 * @param {object} [opts]
 *   `crowns` array of `{position: [x, y, z], radius, height?, seed?}`,
 *   or `{position, size: [x, y, z]}` for a mass that is not a ball —
 *   a hedge is `size: [0.9, 1.4, 20]`
 *   in the group's local space — one entry per tree, all sharing the
 *   single draw call; a lone crown may instead be given as `position`
 *   + `radius`. `radius` metres of the crown's half-width (default 3),
 *   `height` its half-height (default `radius * 0.85`, so a crown is
 *   slightly squat). `leaves` cards per crown — by default it is
 *   derived from each mass's own surface area, which is the only way
 *   one number suits both a hedge and an oak;
 *   `maxLeaves` hard cap over ALL crowns (default 160000 — spread by
 *   thinning every crown alike, never by dropping the last tree).
 *   `size` leaf-spray length in metres (default `0.12 *` the mean crown
 *   radius, so coverage holds at any tree size — set it small only for
 *   a crown a camera stands under). `color` the albedo of
 *   a leaf in full sun, `underColor` one deep inside the crown
 *   (default a darker, cooler form of `color`). `hue` radians of
 *   leaf-to-leaf hue spread (default 0.30 — this is the broken colour;
 *   0 gives back the flat mass). `shell` 0..1 how strongly leaves
 *   crowd the outer surface (default 0.72; real crowns are hollow).
 *   `droop` how far a tip falls (default 0.35 of leaf length).
 *   `backlit` the transmission through a leaf, as `patchLeafSSS` takes
 *   it (`{sunDir, tint, strength}`) — on by default, because the rim
 *   of a crown against the sky is lit through and not lit on; `false`
 *   turns it off, and a `sunDir` below the horizon fades it out for
 *   you (the glow is added to the ALBEDO, so nothing else would).
 *   `wind` as `makeGrass` takes it. `shadows` cast leaf
 *   shadows via a
 *   displaced depth pass (default false — the cost that made grass
 *   default the same way). `seed` PRNG seed (default 7). `name` group
 *   name.
 * @returns {THREE.Group} Named `Canopy`, holding one `Leaves` mesh,
 *   with `userData.tick(t)`.
 */
export function makeCanopy(opts = {}) {
  const crowns = crownList(opts);
  // A leaf card stands for what the camera can RESOLVE, which at any
  // useful distance is a spray, not one leaf. Sized off the mass's
  // THINNEST axis, because that is what sets the scale of the foliage
  // on it: a 34 m hedge sized off its length grew half-metre leaves.
  const thin = crowns.reduce(
      (a, c) => a + Math.min(c.rx, c.ry, c.rz), 0) / crowns.length;
  const size = opts.size === undefined
      ? Math.max(0.05, Math.min(0.4, thin * 0.12))
      : opts.size;
  const seed = opts.seed === undefined ? 7 : opts.seed;
  const shell = clamp01(opts.shell === undefined ? 0.72 : opts.shell);
  const droop = opts.droop === undefined ? 0.35 : opts.droop;
  const hue = opts.hue === undefined ? 0.3 : opts.hue;
  // Albedos, not screen colours: a scene sun runs at 5-6, so a hex that
  // already looks like sunlit foliage tone-maps to pale felt.
  const lit = new THREE.Color(
      opts.color === undefined ? 0x4d6b28 : opts.color);
  const under = new THREE.Color(
      opts.underColor === undefined ? shadeOf(lit) : opts.underColor);
  const wind = windOf(opts.wind);

  const g = new THREE.Group();
  g.name = opts.name || 'Canopy';
  const field = leafField(crowns, opts, shell, size, seed);
  g.add(leafMesh(field, size, droop, hue, lit, under, wind,
      opts.shadows === true,
      opts.backlit === false ? null : (opts.backlit || {})));
  g.userData.tick = (t) => tickShaders(g, t);
  return g;
}

/** Clamp to -1..1: the hue offset is scaled by `hue` downstream. */
function clampSigned(v) {
  return Math.max(-1, Math.min(1, v));
}

/** Clamp to 0..1 without importing MathUtils for three calls. */
function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

/**
 * A leaf in shadow is darker, cooler AND more saturated — never merely
 * darker. The old form desaturated it (`s * 0.9`), which is what a
 * lightening does, not a shading: mixed against the lit colour across a
 * crown it pulled the whole mass toward grey-green felt. A shade leaf is
 * physically the more chlorophyll-dense one, so its chroma goes UP as
 * its value comes down.
 */
function shadeOf(c) {
  const hsl = {};
  c.getHSL(hsl);
  return new THREE.Color().setHSL(
      hsl.h + 0.045, Math.min(1, hsl.s * 1.15), hsl.l * 0.3);
}

/**
 * How much SUN there is to come through a leaf, from the sun's own
 * direction. `patchLeafSSS` adds its transmission to the ALBEDO, before
 * any light is applied, so it does not go out when the sun does: on the
 * night pass of the showcase — sun at -20 deg, ground and sky correctly
 * dark — every crown stayed a bright yellow-green and read as daylight
 * foliage pasted onto a night frame. The ramp holds FULL strength for
 * any sun at or above the horizon, because a low sun is exactly when a
 * backlit crown glows most, and closes over the five degrees below it.
 */
function sunUp(dir) {
  if (!dir || dir.y === undefined) return 1;
  return clamp01((dir.y + 0.09) / 0.09);
}

/** Read `crowns`, or the single-crown spelling, into one list. */
function crownList(opts) {
  const raw = Array.isArray(opts.crowns) && opts.crowns.length
      ? opts.crowns
      : [{ position: opts.position, radius: opts.radius }];
  return raw.map((c, i) => {
    const r = c.radius === undefined ? 3 : c.radius;
    const p = c.position || [0, r, 0];
    // Per-axis half-extents, because not every leaf mass is a ball. A
    // hedge is a long low one, and the first builder offered this
    // library wrote its own Icosahedra rather than fake a hedgerow out
    // of spheres — its comment said "non-spherical" in as many words.
    const sz = Array.isArray(c.size) ? c.size : null;
    return {
      x: p[0], y: p[1], z: p[2],
      rx: sz ? sz[0] : r,
      ry: sz ? sz[1] : (c.height === undefined ? r * 0.85 : c.height),
      rz: sz ? sz[2] : r,
      seed: c.seed === undefined ? i * 131 + 5 : c.seed,
    };
  });
}

/** Knud Thomsen's approximation; exact enough to set a leaf count. */
function ellipsoidArea(c) {
  const p = 1.6;
  const a = Math.pow(c.rx, p), b = Math.pow(c.ry, p), d = Math.pow(c.rz, p);
  return 4 * Math.PI * Math.pow((a * b + a * d + b * d) / 3, 1 / p);
}

/**
 * Place every leaf of every crown.
 *
 * Leaves crowd the outer shell because that is where a real crown puts
 * them and because interior leaves are invisible work: `shell` biases
 * the sampled radius, it does not hollow the crown out, so a camera
 * looking up through the branches still finds leaves overhead.
 */
function leafField(crowns, opts, shell, size, seed) {
  const cap = opts.maxLeaves === undefined ? 160000 : opts.maxLeaves;
  // Count follows AREA, so coverage — about 1.8 masses-worth of leaf,
  // dense enough to read yet open enough to pass sky — holds for a
  // hedge and for an oak alike. One fixed count cannot: the same 3100
  // cards that clothe a crown leave a 34 m hedgerow bald.
  const leafArea = Math.max(1e-4, size * size * 0.5);
  const counts = crowns.map((c) => opts.leaves !== undefined
      ? opts.leaves
      : Math.max(120, Math.round(1.8 * ellipsoidArea(c) / leafArea)));
  let total = counts.reduce((a, b) => a + b, 0);
  if (total > cap) {
    // Thin every mass alike; dropping the last one leaves a gap a
    // viewer reads as a missing tree.
    const f = cap / total;
    for (let i = 0; i < counts.length; i++) {
      counts[i] = Math.max(1, Math.floor(counts[i] * f));
    }
    total = counts.reduce((a, b) => a + b, 0);
  }
  const n = total;
  const pos = new Float32Array(n * 3);
  const axis = new Float32Array(n * 3);
  const vary = new Float32Array(n * 4);
  const box = new THREE.Box3();
  let k = 0;
  for (let ci = 0; ci < crowns.length; ci++) {
    const c = crowns[ci];
    const per = counts[ci];
    const rand = mulberry32(seed + c.seed);
    for (let i = 0; i < per; i++, k++) {
      // Direction first, radius second: sampling a box and rejecting
      // would thin the poles of every crown alike and read as a seam.
      const u = rand() * 2 - 1;
      const phi = rand() * Math.PI * 2;
      const s = Math.sqrt(Math.max(0, 1 - u * u));
      const dx = s * Math.cos(phi), dy = u, dz = s * Math.sin(phi);
      const t = Math.pow(rand(), 1 - shell * 0.85);
      // A crown is boughs, not a ball: without this the silhouette is
      // a perfect sphere and no amount of leaf detail reads as a tree.
      // fbm3 is SIGNED about zero — unlike GLSL's astraFbm2, which
      // runs 0..0.75 about 0.375. Recentring it here is how the crown
      // ends up shrunk to one side.
      //
      // ONE low frequency, deliberately. A second, finer octave on the
      // radius was tried and rendered worse: it scatters leaves ALONG
      // the ray instead of moving the shell, so the crown loses the
      // dense outer skin that makes it read as mass and thins to a
      // haze. Boughs are big; the fine detail is the leaf card itself.
      const lump = 1 + 0.42 * fbm3(dx * 1.15, dy * 1.15, dz * 1.15,
          { seed: c.seed + 3, octaves: 2 });
      const px = c.x + dx * c.rx * t * lump;
      const py = c.y + dy * c.ry * t * lump;
      const pz = c.z + dz * c.rz * t * lump;
      pos[k * 3] = px; pos[k * 3 + 1] = py; pos[k * 3 + 2] = pz;
      // The outward normal of an ELLIPSOID, not of the unit sphere the
      // direction was drawn on: on a long hedge those differ by most
      // of a right angle, and the lighting follows the wrong shape.
      const nx = dx / c.rx, ny = dy / c.ry, nz = dz / c.rz;
      const nl = Math.hypot(nx, ny, nz) || 1;
      axis[k * 3] = nx / nl;
      axis[k * 3 + 1] = ny / nl;
      axis[k * 3 + 2] = nz / nl;
      vary[k * 4] = 0.62 + rand() * 0.76;          // size
      // Hue in two scales. Per-leaf alone averages back to one green
      // at any distance — a canopy Monet paints turns warm in whole
      // passages and cool in others, and that is the scale that
      // survives being seen from across the street.
      vary[k * 4 + 1] = clampSigned(
          (rand() * 2 - 1) * 0.45
          + fbm3(px * 0.42, py * 0.42, pz * 0.42,
                 { seed: c.seed + 61, octaves: 2 }) * 1.5);
      vary[k * 4 + 2] = rand() * Math.PI * 2;      // twist / phase
      // Depth into the crown, plus a lift for the leaves the sky sees:
      // a canopy is lit from ABOVE, not evenly over its shell.
      vary[k * 4 + 3] = clamp01(t * (0.72 + 0.28 * (dy * 0.5 + 0.5)));
      box.expandByPoint(new THREE.Vector3(px, py, pz));
    }
  }
  return { n, pos, axis, vary, box };
}

/** The one instanced mesh every crown shares. */
function leafMesh(field, size, droop, hue, lit, under, wind, shadows,
    backlit) {
  // Segmented along its length: the ovate profile is zero at BOTH
  // ends, so a single quad's four corners all collapse to zero width
  // and the crown draws nothing at all.
  const base = new THREE.PlaneGeometry(1, 1, 1, 3);
  const geom = new THREE.InstancedBufferGeometry();
  geom.index = base.index;
  geom.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array(base.attributes.position.count * 3), 3));
  geom.setAttribute('aCorner', base.attributes.position);
  geom.setAttribute('uv', base.attributes.uv);
  geom.setAttribute('normal', base.attributes.normal);
  geom.instanceCount = field.n;
  const inst = (name, arr, sz) => geom.setAttribute(
      name, new THREE.InstancedBufferAttribute(new Float32Array(arr), sz));
  inst('iPos', field.pos, 3);
  inst('iAxis', field.axis, 3);
  inst('iVar', field.vary, 4);
  // position is zero, so the derived bounds would be a point at the
  // origin: state the crowns' real box, and never cull on it.
  geom.boundingBox = field.box.clone().expandByScalar(size);
  geom.boundingSphere = geom.boundingBox.getBoundingSphere(
      new THREE.Sphere());

  // 0.78 measured against 0.70 and 0.82 on our pipeline and kept: a
  // leaf IS waxy, but with no post chain the dielectric lobe reflects a
  // bright sky straight into the crown's crest, and 0.70 traded 0.02 of
  // measured foliage saturation for that gloss. 0.78 keeps the sheen at
  // grazing angles without chalking the top of the tree.
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.78, metalness: 0,
    side: THREE.FrontSide, name: 'CanopyLeaf',
  });
  patchStandard(mat, {
    name: 'canopy:leaf',
    uniforms: {
      uLeafLit: { value: lit.clone() },
      uLeafUnder: { value: under.clone() },
      uLeafSize: { value: size },
      uLeafDroop: { value: droop },
      uLeafHue: { value: hue },
      uLeafWind: { value: wind.dir.clone() },
      uLeafAmp: { value: wind.amp },
      uLeafSpeed: { value: wind.speed },
    },
    vertexHead: LEAF_HEAD,
    vertexBody: LEAF_VERTEX,
    fragmentHead: [
      'uniform vec3 uLeafLit;',
      'uniform vec3 uLeafUnder;',
      'varying vec3 vLeaf;',
      'varying vec2 vLeafB;',
    ].join('\n'),
    fragmentBody: LEAF_FRAGMENT,
  });

  // A crown without the backlit glow is a black rim against the sky:
  // the underside leaves take no sun and no bounce, and only light
  // coming THROUGH them says foliage rather than soot.
  if (backlit) {
    // 0.62 was too much on OUR pipeline: the transmission lands on the
    // ALBEDO (it is added before lighting), so at 0.62 the sun-side
    // leaves ran past 1.0 albedo and tone-mapped to pale mint. 0.44
    // keeps the whole crown inside the 0.02-0.8 albedo band and leaves
    // the glow to read as glow rather than as a wash.
    const amt = backlit.strength === undefined ? 0.44 : backlit.strength;
    patchLeafSSS(mat, { sunDir: backlit.sunDir, tint: backlit.tint,
      strength: amt * sunUp(backlit.sunDir) });
  }
  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = 'Leaves';
  mesh.frustumCulled = false;
  mesh.receiveShadow = true;
  // Without the displaced depth pass a shadow pass draws only the
  // degenerate zero-position quads, so casting stays off by default.
  mesh.castShadow = false;
  if (shadows) {
    shadowLike(mesh, 'canopy:leafDepth', LEAF_HEAD, LEAF_VERTEX);
  }
  return mesh;
}

const LEAF_HEAD = [
  'uniform float uTime;',
  'uniform float uLeafSize;',
  'uniform float uLeafDroop;',
  'uniform float uLeafHue;',
  'uniform vec2 uLeafWind;',
  'uniform float uLeafAmp;',
  'uniform float uLeafSpeed;',
  'attribute vec3 aCorner;',
  'attribute vec3 iPos;',
  'attribute vec3 iAxis;',
  'attribute vec4 iVar;',
  'varying vec3 vLeaf;',
  // Per-leaf constants the FRAGMENT grade needs and the geometry does
  // not: x a decorrelated lightness draw, y how far the leaf's own
  // facing looks at the sky. Both are constant over a card, so they
  // ride a varying rather than being recomputed per pixel.
  'varying vec2 vLeafB;',
  LOCAL_DIR,
].join('\n');

const LEAF_VERTEX = [
  '  vec3 lfN = normalize(iAxis);',
  // Any stable tangent will do, but the naive cross with +Y collapses
  // for the leaves at the crown's top and bottom.
  '  vec3 lfRef = abs(lfN.y) > 0.9 ? vec3(1.0, 0.0, 0.0)',
  '                                : vec3(0.0, 1.0, 0.0);',
  '  vec3 lfT = normalize(cross(lfRef, lfN));',
  '  vec3 lfB = cross(lfN, lfT);',
  '  float lfTw = iVar.z;',
  '  vec3 lfE1 = lfT * cos(lfTw) + lfB * sin(lfTw);',
  '  vec3 lfE2 = -lfT * sin(lfTw) + lfB * cos(lfTw);',
  // Ovate: widest past the middle, drawn to a point at the tip. This
  // profile IS the silhouette, and the silhouette is the whole reason
  // for the card.
  '  float lfV = aCorner.y + 0.5;',
  '  float lfProf = sin(3.14159 * pow(lfV, 0.72));',
  '  float lfS = iVar.x * uLeafSize;',
  '  vec2 lfC = vec2(aCorner.x * lfProf, aCorner.y);',
  '  vec3 lfP = iPos + lfE1 * (lfC.x * lfS) + lfE2 * (lfC.y * lfS);',
  '  lfP.y -= uLeafDroop * lfS * lfV * lfV;',
  // Gusts are streaks running downwind, as in the grass: the same wind
  // has to move a meadow and the trees over it alike.
  '  vec3 lfW3 = leafLocalDir(vec3(uLeafWind.x, 0.0, uLeafWind.y));',
  '  vec2 lfW = normalize(lfW3.xz + vec2(1e-5, 0.0));',
  '  float lfG = astraFbm2(vec2(',
  '      dot(iPos.xz, lfW) * 0.05 - uTime * uLeafSpeed * 0.5,',
  '      dot(iPos.xz, vec2(-lfW.y, lfW.x)) * 0.2), 2);',
  '  float lfT2 = uTime * uLeafSpeed;',
  // Outer leaves move most: the trunk end of a bough barely travels.
  '  float lfSway = uLeafAmp * (0.2 + 1.1 * lfG) * iVar.w',
  '      * (0.6 + 0.4 * sin(lfT2 * 1.7 + iVar.z));',
  '  lfP.xz += lfW * lfSway * 0.45;',
  // Flutter is the leaf turning on its own stem, which is what catches
  // the light — a card that only translates reads as dead foliage.
  '  float lfFl = uLeafAmp * (0.35 + 0.65 * lfG)',
  '      * sin(lfT2 * 3.1 + iVar.z * 2.3);',
  '  lfP += lfN * (lfFl * lfC.y * lfS * 0.9);',
  '  transformed = lfP;',
  '  vLeaf = vec3(iVar.w, iVar.y * uLeafHue, lfV);',
  // Decorrelated from hue, size and twist alike, so no two neighbours
  // agree on VALUE either. Hue spread on its own still averages to one
  // even green at a distance; a crown's real dazzle is that some leaves
  // catch and some do not.
  '  float lfJit = fract(sin(iVar.z * 91.7 + dot(iPos.xz, vec2(12.9898,',
  '      78.233))) * 43758.5453);',
  '  vLeafB = vec2(lfJit, lfN.y * 0.5 + 0.5);',
  '#ifndef FLAT_SHADED',
  // Outward from the crown, not the card's own facing: this is what
  // makes a crown light like a volume instead of a heap of flat chips.
  // Leaves turn toward the light, and a canopy's light is the SKY, so
  // the outward normal is bent upward — but only PART of the way. At
  // the old fixed +1.15 every normal in the crown fell within ~40 deg
  // of straight up, so with a 38 deg sun every leaf took the same
  // near-full diffuse and the mass lost its form: measured, the two
  // crowns rendered as flat pale discs with no lit side. The bend is
  // now weak on the shell, where the outward normal IS the crown's
  // shape, and strong in the interior, where nothing but sky reaches
  // and a leaf lit only by its own facing goes to soot.
  '  float lfSky = 0.34 + 0.44 * (1.0 - iVar.w);',
  '  vNormal = normalize(normalMatrix',
  '      * normalize(lfN + vec3(0.0, lfSky, 0.0)',
  '                  + lfE1 * (lfFl * 0.5)));',
  '#endif',
].join('\n');

const LEAF_FRAGMENT = [
  // Depth runs 0.26..0.89 over a crown (measured), so a straight mix
  // never reached either end and the interior colour was decoration
  // that never showed. Widen it to the range the field actually
  // occupies and the crown gets an inside.
  '  vec3 lfC = mix(uLeafUnder, uLeafLit,',
  '      smoothstep(0.18, 0.88, vLeaf.x));',
  // The hue spread between neighbours is the broken colour; without it
  // the mass returns to one flat green however many cards it holds.
  '  lfC = astraHueShift(lfC, vLeaf.y);',
  // Sun leaves and shade leaves are not one leaf at two brightnesses:
  // the ones the sky sees are thinner and yellower, the ones facing the
  // ground keep their chlorophyll and read cool. This is a real
  // botanical split and it is what gives a crown its warm crest.
  '  lfC *= mix(vec3(0.92, 1.0, 1.06), vec3(1.12, 1.04, 0.82),',
  '      vLeafB.y * vLeafB.y);',
  // Value spread leaf to leaf, +-20%: the broken-colour painter varies
  // the tone as much as the hue.
  '  lfC *= 0.80 + 0.40 * vLeafB.x;',
  // The crown's own occlusion. A shadow map sized for a whole scene has
  // texels far larger than a leaf, so it can shade the crown's broad
  // underside but never the metre of leaf a ray crosses on its way in —
  // and without that the inside of a tree is as bright as its rim,
  // which is the single clearest tell of fake foliage. Depth is the
  // only occlusion signal there is here, and it is a good one.
  '  lfC *= 0.55 + 0.45 * smoothstep(0.05, 0.85, vLeaf.x);',
  // Sky occlusion, the other half of the same story: a leaf on the
  // underside of a crown sees a hand's width of sky and a great deal of
  // leaf, and it is this — not the sun — that draws the dark belly
  // under a tree and the bright crest over it. Without it a crown lit
  // by a bright environment has the same value top and bottom, which is
  // why the first render read as a flat pale disc.
  '  lfC *= 0.62 + 0.38 * vLeafB.y;',
  // A leaf is thin: the light behind it comes through near the tip,
  // where there is least of it — and it comes through WARM, because
  // what survives a leaf is the red end. Adding the leaf's own colour
  // back (the old form) only raised its value and paled the crown.
  '  lfC += uLeafLit * vec3(1.5, 1.15, 0.5) * 0.19',
  '      * smoothstep(0.45, 1.0, vLeaf.z) * vLeaf.x;',
  // Albedo, not a screen colour: hold the whole crown inside the band a
  // physical dielectric occupies, so no leaf can blow out under a sun
  // running at 5-6 and none goes to a black chip in shade. The floor is
  // TINTED — a flat grey floor is how a deep crown loses its colour
  // exactly where the two occlusions have made it most saturated.
  '  vec3 lfFloor = max(uLeafUnder * 0.25, vec3(0.012));',
  '  diffuseColor.rgb = clamp(lfC, lfFloor, vec3(0.8));',
].join('\n');
