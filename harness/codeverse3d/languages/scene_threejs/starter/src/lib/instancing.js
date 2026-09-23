/**
 * Place one asset hundreds of times for the cost of building it once.
 * Hand-instancing is fiddly enough that no delivered scene ever did
 * it, so the capability ships as a function.
 */

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { snapshotResources, attachDisposal } from './lifecycle.js';

/** A baked mirror changes triangle orientation as well as vertex positions. */
function reverseWinding(geometry) {
  if (!geometry.index) {
    geometry.setIndex(Array.from({length:geometry.attributes.position.count}, (_, i) => i));
  }
  const index = geometry.index;
  for (let i = 0; i + 2 < index.count; i += 3) {
    const b = index.getX(i + 1);
    index.setX(i + 1, index.getX(i + 2));
    index.setX(i + 2, b);
  }
  index.needsUpdate = true;
  return geometry;
}

/**
 * Build one prototype and stamp it across many transforms.
 *
 * The prototype is built ONCE. Every mesh inside it becomes one
 * InstancedMesh sharing that mesh's geometry and material, so a 200-copy
 * placement costs the prototype's triangles once on the CPU side and one
 * draw call per (geometry, material) pair.
 *
 * A mesh carrying an ARRAY of materials is split along its geometry
 * groups first, so a box whose door face is a second material keeps it:
 * merging by `material[0]` alone painted every face with the first one.
 *
 * @param {(opts: object) => THREE.Object3D} build The asset's exported
 *     build function.
 * @param {Array<{position?: number[]|THREE.Vector3, rotationY?: number,
 *     scale?: number|number[], color?: number[]|THREE.Color|number|string}>}
 *     placements One entry per copy. `rotationY` is radians; `scale` is
 *     uniform when a number. `color` MULTIPLIES that copy's albedo — no
 *     `vertexColors` needed, three defines `USE_INSTANCING_COLOR` off the
 *     instance buffer alone. Give it as a LINEAR `[r, g, b]` (what
 *     `scatterGrid` emits) or a `THREE.Color`; a hex or a CSS string is
 *     read as sRGB, so `0x808080` is not "half" but a quarter — the trap
 *     that turns a tint into a dimmer.
 * @param {object} [opts] `buildOpts` is passed to `build` for the
 *     prototype; `name` names the returned group.
 * @returns {THREE.Group} A group of InstancedMeshes, positioned in world
 *     space by the placements. Empty group when there are no placements.
 *     Mirrored placements use a second batch with baked reversed winding;
 *     every instance matrix then has positive determinant, as Three requires.
 *     userData.dispose() releases copied geometry and instance buffers, while
 *     prototype materials/textures remain borrowed. Later children are unowned.
 */
export function instanceAsset(build, placements, opts = {}) {
  const group = new THREE.Group();
  group.name = opts.name || 'InstancedAsset';
  if (!placements || placements.length === 0) return attachDisposal(group, []);

  const proto = build(opts.buildOpts || {});
  proto.updateWorldMatrix(true, true);

  // Collect meshes with transforms RELATIVE to the prototype root,
  // then MERGE by material — unmerged, a 1418-mesh asset would cost
  // 1418 draw calls per placement batch; merged, one per material.
  const buckets = new Map();
  const inv = new THREE.Matrix4().copy(proto.matrixWorld).invert();
  proto.traverseVisible((n) => {
    if (!n.isMesh || n.isInstancedMesh || !n.geometry) return;
    const mats = Array.isArray(n.material) ? n.material : [n.material];
    if (!mats[0]) return;
    const xf = new THREE.Matrix4().copy(inv).multiply(n.matrixWorld);
    for (const { material, geometry } of splitByMaterial(n.geometry, mats)) {
      const g = geometry;
      g.applyMatrix4(xf);
      if (xf.determinant() < 0) reverseWinding(g);
      // Merging needs identical attribute sets; drop the extras.
      for (const name of Object.keys(g.attributes)) {
        // `color` stays: a vertexColors material with no color
        // attribute renders BLACK, and mergeStatic() returns one.
        if (!['position', 'normal', 'uv', 'color'].includes(name)) {
          g.deleteAttribute(name);
        }
      }
      if (!g.attributes.normal) g.computeVertexNormals();
      if (!g.attributes.uv) {
        const n2 = g.attributes.position.count;
        g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(n2 * 2), 2));
      }
      if (!buckets.has(material.uuid)) {
        buckets.set(material.uuid, { material, geos: [] });
      }
      buckets.get(material.uuid).geos.push(g);
    }
  });
  const parts = [];
  for (const { material, geos } of buckets.values()) {
    for (const geometry of mergeBucket(geos)) {
      parts.push({ geometry, material, local: new THREE.Matrix4() });
    }
  }
  if (!parts.length) return attachDisposal(group, []);

  const world = placements.map((p) => {
    const pos = p.position instanceof THREE.Vector3
        ? p.position
        : new THREE.Vector3(...(p.position || [0, 0, 0]));
    const s = Array.isArray(p.scale)
        ? new THREE.Vector3(...p.scale)
        : new THREE.Vector3(1, 1, 1).multiplyScalar(
            typeof p.scale === 'number' ? p.scale : 1);
    const matrix = new THREE.Matrix4().compose(
        pos,
        new THREE.Quaternion().setFromEuler(
            new THREE.Euler(0, p.rotationY || 0, 0)),
        s);
    if (!matrix.elements.every(Number.isFinite) || Math.abs(matrix.determinant()) < 1e-12) {
      throw new RangeError('instanceAsset: placements need finite transforms and nonzero scale');
    }
    return matrix;
  });

  const m = new THREE.Matrix4();
  const tint = new THREE.Color();
  const reflection = new THREE.Matrix4().makeScale(-1, 1, 1);
  const batches = [[], []];
  for (let i = 0; i < world.length; i++) batches[world[i].determinant() < 0 ? 1 : 0].push(i);
  for (const part of parts) {
   for (let mirrored = 0; mirrored < batches.length; mirrored++) {
    const indices = batches[mirrored];
    if (!indices.length) continue;
    const geometry = mirrored
        ? reverseWinding(part.geometry.clone().applyMatrix4(reflection)) : part.geometry;
    const mesh = new THREE.InstancedMesh(
        geometry, part.material, indices.length);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    let tinted = false;
    for (let i = 0; i < indices.length; i++) {
      const sourceIndex = indices[i];
      m.copy(world[sourceIndex]).multiply(part.local);
      if (mirrored) m.multiply(reflection);
      mesh.setMatrixAt(i, m);
      const c = placements[sourceIndex].color;
      if (c !== undefined && c !== null) {
        // An array is LINEAR — the space the multiply happens in, and the
        // only way to say "1.06 of the albedo". set() reads a hex or a
        // string as sRGB.
        if (Array.isArray(c)) tint.setRGB(c[0], c[1], c[2]);
        else tint.set(c);
        mesh.setColorAt(i, tint);
        tinted = true;
      }
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (tinted && mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.frustumCulled = false;  // one bbox for the whole spread
    mesh.userData.placementIndices = Int32Array.from(indices);
    group.add(mesh);
   }
  }
  return attachDisposal(group, snapshotResources(group, {materials:false}));
}

/**
 * One geometry per material the mesh actually draws with.
 *
 * A multi-material mesh draws its groups with different materials; taking
 * `material[0]` for the whole geometry paints the door, the glass and the
 * roof with the wall's material and the copy loses every second colour it
 * had. Each group becomes its own geometry, de-indexed so it carries only
 * the vertices it uses.
 * @private
 */
function splitByMaterial(geo, mats) {
  const groups = geo.groups || [];
  if (mats.length < 2 || groups.length === 0) {
    return [{ material: mats[0], geometry: geo.clone() }];
  }
  const out = [];
  const idx = geo.index;
  const total = idx ? idx.count : geo.attributes.position.count;
  for (const gr of groups) {
    const start = Math.max(0, gr.start | 0);
    const count = Math.min(gr.count === Infinity ? total : gr.count | 0,
                           total - start);
    const material = mats[gr.materialIndex || 0] || mats[0];
    if (count <= 0 || !material) continue;
    let piece;
    if (idx) {
      piece = geo.clone();
      piece.clearGroups();
      piece.setIndex(Array.from(idx.array.slice(start, start + count)));
      piece = piece.toNonIndexed();  // drops the vertices this group misses
    } else {
      piece = new THREE.BufferGeometry();
      for (const name of Object.keys(geo.attributes)) {
        const a = geo.attributes[name];
        piece.setAttribute(name, new THREE.BufferAttribute(
            a.array.slice(start * a.itemSize, (start + count) * a.itemSize),
            a.itemSize, a.normalized));
      }
    }
    out.push({ material, geometry: piece });
  }
  return out.length ? out : [{ material: mats[0], geometry: geo.clone() }];
}

/**
 * Merge one material's geometries into as few draws as possible.
 *
 * `mergeGeometries` returns NULL — silently — when the inputs disagree on
 * their attribute set or on being indexed, and the old code dropped that
 * bucket: the asset rendered with a part MISSING and nothing said so.
 * Harmonise what can be harmonised (a white `color` for the geometries
 * that lack one, de-index the mixed case) and fall back to separate draws
 * rather than to a hole.
 * @private
 */
function mergeBucket(geos) {
  if (geos.length <= 1) return geos;
  let list = geos;
  if (list.some((g) => g.index) && list.some((g) => !g.index)) {
    list = list.map((g) => (g.index ? g.toNonIndexed() : g));
  }
  if (list.some((g) => g.attributes.color)) {
    for (const g of list) {
      if (g.attributes.color) continue;
      const n = g.attributes.position.count;
      g.setAttribute('color',
          new THREE.BufferAttribute(new Float32Array(n * 3).fill(1), 3));
    }
  }
  const merged = mergeGeometries(list, false);
  return merged ? [merged] : list;
}

/**
 * Fill a rectangle with placements on a jittered grid.
 *
 * A perfect lattice reads as wallpaper and an unconstrained random
 * scatter clumps and overlaps; a jittered grid is the middle that reads
 * as a city block or an orchard. Deterministic: pass your seeded PRNG.
 *
 * Every copy also gets its OWN albedo. One prototype means one colour,
 * and a field of thirty identical greens or thirty identical roof reds
 * is the flat "high repetition of identical towers" read however much
 * the silhouettes vary — no orchard has two leaves the same hue. `tint`
 * is a per-copy LINEAR multiplier averaging 0.96, so the field keeps the
 * albedo the asset was authored with and mostly gains SPREAD: pass a
 * number to scale the default strength, `{ chroma, value }` to set the
 * hue swing and the light/dark swing apart, or `false` for the old flat
 * field (a copy that must match a stated brand colour exactly).
 *
 * @param {object} area `{x, z, w, d}` — centre and extents in metres.
 * @param {number} spacing Nominal metres between copies.
 * @param {() => number} rand Seeded PRNG returning [0, 1).
 * @param {object} [opts] `jitter` (0-1, default 0.35) fraction of
 *     spacing to offset by; `yaw` true (default) for random Y rotation,
 *     false for none, or a NUMBER of radians to snap to — `Math.PI / 2`
 *     is the built fabric that faces its street instead of spinning
 *     freely, with `yawJitter` (default 0.035 rad) of hand-placed slop
 *     on top; `scaleVar` (default 0.12) uniform scale spread;
 *     `heightVar`/`widthVar` (default 0) per-instance NON-uniform
 *     stretch — a city fabric wants heightVar 0.4-0.7 so the block is
 *     not one model repeated; `clearance` metres of centre-to-centre
 *     room every copy is given (jitter plus scaleVar routinely lands two
 *     copies inside each other, and two coincident roofs z-fight);
 *     `tint` per-copy albedo variation (see below); `height` a
 *     function (x, z) => y for the ground under each copy; `skip` a
 *     predicate (x, z) => boolean to leave gaps (streets, water, parks).
 * @returns {Array<object>} Placements for `instanceAsset`.
 */
export function scatterGrid(area, spacing, rand, opts = {}) {
  if (!Number.isFinite(spacing) || spacing <= 0 ||
      ![area.x, area.z, area.w, area.d].every(Number.isFinite) || area.w < 0 || area.d < 0) {
    throw new RangeError('scatterGrid: spacing must be positive and area finite with nonnegative size');
  }
  if (area.w === 0 || area.d === 0) return [];
  const jitter = opts.jitter ?? 0.35;
  const scaleVar = opts.scaleVar ?? 0.12;
  // Per-instance SHAPE variation, not just yaw: cheap repetition is
  // not density. heightVar/widthVar stretch each copy independently,
  // free through the per-instance matrix.
  const heightVar = opts.heightVar ?? 0;
  const widthVar = opts.widthVar ?? 0;
  const tint = tintAmount(opts.tint);
  const clearance = Math.max(0, opts.clearance ?? 0);
  const yawStep = typeof opts.yaw === 'number' ? Math.abs(opts.yaw) : 0;
  const yawSlots = yawStep > 0
      ? Math.max(1, Math.round((Math.PI * 2) / yawStep)) : 0;
  const yawSlop = opts.yawJitter ?? 0.035;
  const kept = clearance > 0 ? new Map() : null;
  const out = [];
  const nx = Math.max(1, Math.round(area.w / spacing));
  const nz = Math.max(1, Math.round(area.d / spacing));
  for (let ix = 0; ix < nx; ix++) {
    for (let iz = 0; iz < nz; iz++) {
      const x = area.x - area.w / 2 + (ix + 0.5) * (area.w / nx)
          + (rand() - 0.5) * spacing * jitter;
      const z = area.z - area.d / 2 + (iz + 0.5) * (area.d / nz)
          + (rand() - 0.5) * spacing * jitter;
      if (opts.skip && opts.skip(x, z)) continue;
      if (kept && crowded(kept, x, z, clearance)) continue;
      const base = 1 + (rand() - 0.5) * 2 * scaleVar;
      const scale = (heightVar || widthVar)
          ? [base * (1 + (rand() - 0.5) * 2 * widthVar),
             base * (1 + (rand() - 0.5) * 2 * heightVar),
             base * (1 + (rand() - 0.5) * 2 * widthVar)]
          : base;
      let rotationY = 0;
      if (opts.yaw !== false) {
        rotationY = yawSlots
            ? Math.floor(rand() * yawSlots) * yawStep
                + (rand() - 0.5) * 2 * yawSlop
            : rand() * Math.PI * 2;
      }
      const p = {
        position: [x, opts.height ? opts.height(x, z) : 0, z],
        rotationY,
        scale,
      };
      if (tint) p.color = tintOf(rand, tint);
      out.push(p);
      if (kept) remember(kept, x, z, clearance);
    }
  }
  return out;
}

// Default per-copy albedo spread, set against the crown measurement the
// canopy work landed on — 9.4 degrees of hue across one tree, where a
// photograph carries 25-45. Measured over 400 copies of the showcase
// assets, this pair gives a 5-95 hue spread of 5.3 deg on the terracotta
// roof, 13.8 on the leaf green and 28.6 on the near-neutral plaster
// (whose hue is the least stable and moves furthest for the least
// perceived change), with a 5-95 lightness spread of 0.072 / 0.058 /
// 0.114. The roof moves least in HUE because terracotta already sits on
// the warm axis the swing runs along — there it spends itself on
// saturation and value instead, which is what a batch of tiles does.
// Half of this measured 2.7 / 6.9 / 12.3 and the widest pixel gap
// between a tinted field and a flat one was 10/255: real, and invisible.
const TINT = { chroma: 0.22, value: 0.22 };

// The value swing is wider DOWNWARD (x0.6 up, x1.2 down). A copy that
// has stood in weather is darker than the reference far more often than
// brighter, and the up-swing has a ceiling the down-swing does not: an
// albedo authored near the top of the 0.02-0.8 band has no room to be
// multiplied, and a surface pushed past it has no shading left in it.
const VALUE_UP = 0.6, VALUE_DOWN = 1.2;

// How much of the chroma swing goes ACROSS the warm-cool axis (the
// green-magenta one). Well under half: see tintOf.
const ACROSS = 0.45;

/** Normalise the `tint` option to amounts, or null for none. @private */
function tintAmount(spec) {
  if (spec === false || spec === 0 || spec === null) return null;
  if (spec === undefined || spec === true) return TINT;
  if (typeof spec === 'number') {
    return { chroma: TINT.chroma * spec, value: TINT.value * spec };
  }
  return { chroma: spec.chroma ?? TINT.chroma, value: spec.value ?? TINT.value };
}

/**
 * One copy's albedo multiplier, in LINEAR space and centred on white.
 *
 * The hue part is an offset in the plane PERPENDICULAR to the grey axis
 * — the only directions that change hue without changing brightness —
 * drawn over an ELLIPSE, not a disc: 1.0 along warm-cool (red up, blue
 * down) and 0.45 across it. A disc spreads a near-neutral copy in any
 * direction at all, and a violet wall beside an olive one reads as a
 * bug; the same spread laid on the temperature axis reads as different
 * limewash under the same sun, which is what the eye expects of a row of
 * houses. `value` then scales the whole triple, so the field is moved
 * APART rather than lifted or dimmed (its mean lands at 0.96 of the
 * authored albedo, the weathering skew).
 *
 * It is a MULTIPLIER and not a hue rotation on purpose: a rotation about
 * the grey axis drives the small channels of a near-primary negative and
 * the clamp then forks one swing into two lobes — the failure the flower
 * field was measured splitting into an amber half and a magenta half. A
 * positive multiplier cannot leave the hue's own quadrant.
 * @private
 */
function tintOf(rand, amt) {
  const a = rand() * Math.PI * 2;
  const m = amt.chroma * Math.sqrt(rand());
  const r = (rand() - 0.5) * 2;
  const v = 1 + amt.value * r * (r < 0 ? VALUE_DOWN : VALUE_UP);
  const warm = Math.cos(a) * m, cast = Math.sin(a) * m * ACROSS;
  // A guard, not a shaper: the defaults' corner is 1.31 and 1600 copies
  // measured a widest channel of 1.29.
  const clamp = (c) => Math.min(1.35, Math.max(0.5, c));
  return [
    clamp(v * (1 + 0.70711 * warm - 0.40825 * cast)),
    clamp(v * (1 + 0.81650 * cast)),
    clamp(v * (1 - 0.70711 * warm - 0.40825 * cast)),
  ];
}

// Kept centres in a hash grid of `clearance`-sized cells: the 3x3
// neighbourhood holds every copy close enough to matter, so the test
// stays O(1) per candidate instead of O(n) against the whole field.
const cellKey = (i, j) => `${i},${j}`;

/** @private */
function crowded(kept, x, z, cell) {
  const i = Math.floor(x / cell), j = Math.floor(z / cell);
  const r2 = cell * cell;
  for (let di = -1; di <= 1; di++) {
    for (let dj = -1; dj <= 1; dj++) {
      const a = kept.get(cellKey(i + di, j + dj));
      if (!a) continue;
      for (let n = 0; n < a.length; n += 2) {
        const dx = a[n] - x, dz = a[n + 1] - z;
        if (dx * dx + dz * dz < r2) return true;
      }
    }
  }
  return false;
}

/** @private */
function remember(kept, x, z, cell) {
  const k = cellKey(Math.floor(x / cell), Math.floor(z / cell));
  const a = kept.get(k);
  if (a) a.push(x, z);
  else kept.set(k, [x, z]);
}
