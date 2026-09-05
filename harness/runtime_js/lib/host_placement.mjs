/**
 * Placement table — deterministic "does every placed asset sit on something?"
 * for a THREE.Scene (page-side or node; pure three.js math, no renderer).
 * Added 2026-08-26: the scene track had NO deterministic placement check — the
 * census only reported 2-D zone-footprint overlap (INFO) and floating / sunken
 * assets were left to the VLM (defect −0.06), while the scene_v1 `floating_part`
 * cap could never fire because no gate emitted those words.
 *
 * A "placed asset" is each direct child of a top-level scene child (the zone
 * groups the census already calls `groups`) that has Mesh descendants; a
 * top-level Mesh is an asset of its own.  Assets are named by Object3D name.
 *
 * Per asset (all in world space, metres):
 *   bbox            exact AABB of its (non-instanced) mesh vertices
 *   ground_gap_m    min over its FOOT COLUMNS of the vertical distance from the
 *                   foot to the nearest surface of any OTHER mesh below it (terrain,
 *                   floor, table…); falls back to the census ground_y when nothing
 *                   is beneath.  Foot columns are up to 5 XZ points taken from the
 *                   asset's lowest vertices (lowest, ±x, ±z extremes of the band
 *                   ≤ 5 % of its height above its minimum) — so a table is measured
 *                   at its leg tips, not under its top.
 *   sunk_m          max over columns of how far the foot sits below a surface that
 *                   passes through the asset's own vertical extent at that column,
 *                   or below a ground-like surface (terrain/floor) above it.
 *   support / sunk_into   the mesh (its owning asset when it is one) that was hit
 *   on_water        a water-named surface lies at/above the foot at some column: the
 *                   foot is in the water (boat, jetty post) → supported, never sunk
 *   attached        other assets whose AABB intersects this one's AABB + 2 cm
 *   supported       gap ≤ 0.02 m or sunk
 *   floating        gap > 0.05 m and not sunk (python re-derives with the indoor
 *                   threshold; these booleans are for tools/humans)
 *   touches_nothing gap > 0.02 m, not sunk, attached to nothing
 * Interpenetration: pairs of checked assets whose AABBs overlap by > 20 % of the
 * smaller box, refined by sampling the smaller asset's vertices for containment
 * in the larger one's meshes (parity of surfaces above the point per column) —
 * a bench under a tree canopy is NOT an interpenetration, a crate half inside a
 * wall is.  `aabb_overlap` is the box fraction (the reported number), `inside_frac`
 * the vertex containment that confirmed it.
 *
 * Exempt (listed with a reason, never scored): lights, cameras, ground / sky
 * meshes (`backdrop.mjs` rules), enclosures whose AABB swallows the content box,
 * instanced-only assets (scatter cannot be sampled per instance at this budget;
 * instanced meshes are also never used as support), VOLUMETRIC assets — every mesh
 * of which writes no depth, i.e. haze, god rays, glow (`nonSolid`) — and anything
 * the author tags `userData.placement = 'free'` on the asset or its zone (a bird, a
 * hanging lantern, a drone).  A volumetric mesh is not indexed at all, so it is
 * never a support, never something to sink into and never an overlap partner.
 * First 400 assets, 4 s budget; the table says when it cut.
 *
 * Vertical rays only, so every mesh gets a "column index": world vertices once
 * and, above 1 500 triangles, a uniform XZ grid of triangle buckets.  A column
 * query is point-in-triangle in XZ + barycentric height — double-sided by
 * construction (a raycaster with FrontSide materials never sees the terrain from
 * below, which is exactly the sunk case).
 */

import { classifyBackdrop, GROUND_NAME_RE, nonSolid } from './backdrop.mjs';
import { geometryTriangles } from './census.mjs';

export const MAX_ASSETS = 400;
export const TIME_BUDGET_MS = 4000;
export const CONTACT_TOL_M = 0.02;
export const FLOATING_M = 0.05;
export const SUNK_M = 0.10;
export const OVERLAP_MIN_FRAC = 0.20;
export const WATER_RE = /\b(water|ocean|sea|lake|river|pond|pool|stream|canal)\b/i;
const FOOT_BAND_MIN_M = 0.02;
const FOOT_BAND_FRAC = 0.05;
const MAX_COLUMNS = 5;
const GRID_MIN_TRIS = 1500;
const MAX_INDEXED_TRIS = 1500000;
const OVERLAP_SAMPLES = 64;
const EPS = 1e-3;
const BARY_TOL = 1e-4;

const r3 = (v) => +v.toFixed(3);

/** World-space vertices + XZ triangle buckets for vertical column queries on one mesh. */
class ColumnIndex {
  constructor(mesh) {
    const geo = mesh.geometry;
    const pos = geo.attributes.position;
    this.mesh = mesh;
    this.tris = geometryTriangles(geo);
    this.index = geo.index ? geo.index.array : null;
    const n = pos.count;
    const xyz = new Float32Array(n * 3);
    const e = mesh.matrixWorld.elements;
    let minx = Infinity, miny = Infinity, minz = Infinity, maxx = -Infinity, maxy = -Infinity, maxz = -Infinity;
    for (let i = 0; i < n; i++) {
      const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
      const wx = e[0] * x + e[4] * y + e[8] * z + e[12];
      const wy = e[1] * x + e[5] * y + e[9] * z + e[13];
      const wz = e[2] * x + e[6] * y + e[10] * z + e[14];
      xyz[3 * i] = wx; xyz[3 * i + 1] = wy; xyz[3 * i + 2] = wz;
      if (wx < minx) minx = wx; if (wx > maxx) maxx = wx;
      if (wy < miny) miny = wy; if (wy > maxy) maxy = wy;
      if (wz < minz) minz = wz; if (wz > maxz) maxz = wz;
    }
    this.xyz = xyz;
    this.count = n;
    this.min = [minx, miny, minz];
    this.max = [maxx, maxy, maxz];
    this.grid = null;
    this.kind = this.empty ? 'content' : classifyBackdrop(mesh, { min: { x: minx, y: miny, z: minz }, max: { x: maxx, y: maxy, z: maxz } });
    this.water = isWaterLike(mesh);
    this.groundLike = this.kind === 'ground' || GROUND_NAME_RE.test(spaced(mesh.name)) || GROUND_NAME_RE.test(spaced(nearestName(mesh)));
  }

  get empty() { return this.count === 0 || this.tris === 0 || !Number.isFinite(this.min[0]); }

  containsXZ(x, z, pad = EPS) {
    return x >= this.min[0] - pad && x <= this.max[0] + pad && z >= this.min[2] - pad && z <= this.max[2] + pad;
  }

  vertexOf(t, k) { return this.index ? this.index[3 * t + k] : 3 * t + k; }

  _buildGrid() {
    const cells = Math.min(128, Math.max(8, Math.ceil(Math.sqrt(this.tris) / 2)));
    const sx = Math.max(1e-6, this.max[0] - this.min[0]), sz = Math.max(1e-6, this.max[2] - this.min[2]);
    const buckets = new Array(cells * cells);
    const xyz = this.xyz;
    for (let t = 0; t < this.tris; t++) {
      let cx0 = Infinity, cx1 = -Infinity, cz0 = Infinity, cz1 = -Infinity;
      for (let k = 0; k < 3; k++) {
        const v = this.vertexOf(t, k);
        const x = xyz[3 * v], z = xyz[3 * v + 2];
        if (x < cx0) cx0 = x; if (x > cx1) cx1 = x;
        if (z < cz0) cz0 = z; if (z > cz1) cz1 = z;
      }
      const ix0 = Math.max(0, Math.floor((cx0 - this.min[0]) / sx * cells)), ix1 = Math.min(cells - 1, Math.floor((cx1 - this.min[0]) / sx * cells));
      const iz0 = Math.max(0, Math.floor((cz0 - this.min[2]) / sz * cells)), iz1 = Math.min(cells - 1, Math.floor((cz1 - this.min[2]) / sz * cells));
      for (let ix = ix0; ix <= ix1; ix++) for (let iz = iz0; iz <= iz1; iz++) {
        const b = ix * cells + iz;
        (buckets[b] || (buckets[b] = [])).push(t);
      }
    }
    this.grid = { cells, sx, sz, buckets };
  }

  _candidates(x, z) {
    if (this.tris <= GRID_MIN_TRIS) return null;   // null = all triangles
    if (!this.grid) this._buildGrid();
    const g = this.grid;
    const ix = Math.floor((x - this.min[0]) / g.sx * g.cells), iz = Math.floor((z - this.min[2]) / g.sz * g.cells);
    if (ix < 0 || iz < 0 || ix >= g.cells || iz >= g.cells) return [];
    return g.buckets[ix * g.cells + iz] || [];
  }

  /** Heights (world y) of every surface of this mesh crossing the vertical line at (x, z). */
  heightsAt(x, z, out = []) {
    if (!this.containsXZ(x, z)) return out;
    const list = this._candidates(x, z);
    const n = list ? list.length : this.tris;
    const xyz = this.xyz;
    for (let i = 0; i < n; i++) {
      const t = list ? list[i] : i;
      const a = this.vertexOf(t, 0), b = this.vertexOf(t, 1), c = this.vertexOf(t, 2);
      const ax = xyz[3 * a], az = xyz[3 * a + 2], bx = xyz[3 * b], bz = xyz[3 * b + 2], cx = xyz[3 * c], cz = xyz[3 * c + 2];
      const d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz);
      if (Math.abs(d) < 1e-12) continue;            // vertical / degenerate: never blocks a vertical line
      const l1 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d;
      const l2 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d;
      const l3 = 1 - l1 - l2;
      if (l1 < -BARY_TOL || l2 < -BARY_TOL || l3 < -BARY_TOL) continue;
      out.push(l1 * xyz[3 * a + 1] + l2 * xyz[3 * b + 1] + l3 * xyz[3 * c + 1]);
    }
    return out;
  }

  /** Parity test: is world point (x, y, z) inside this (closed) mesh? */
  contains(x, y, z) {
    const hs = this.heightsAt(x, z);
    let above = 0;
    for (const h of hs) if (h > y) above += 1;
    return above % 2 === 1;
  }
}

function boxJson(min, max) {
  return { min: min.map(r3), max: max.map(r3), size: [max[0] - min[0], max[1] - min[1], max[2] - min[2]].map(r3) };
}

function materialNames(mesh) {
  const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  return mats.map((m) => (m && (m.name || (m.map && m.map.name))) || '').join(' ');
}

function nearestName(obj) {
  for (let o = obj; o; o = o.parent) if (o.name) return o.name;
  return obj.type || 'mesh';
}

/** 'PondWater' / 'pond_water' → 'Pond Water' so the shared word regexes see the words. */
function spaced(name) { return String(name || '').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[_\-.]+/g, ' '); }

function isWaterLike(mesh) { return !!mesh && (WATER_RE.test(spaced(mesh.name)) || WATER_RE.test(spaced(materialNames(mesh))) || WATER_RE.test(spaced(nearestName(mesh)))); }

/**
 * Every visible, non-instanced Mesh with a position attribute → ColumnIndex (skipping
 * giants and volumetrics).  A `nonSolid` mesh is collected into `volumetric` instead:
 * it is neither a support, nor something to sink into, nor an overlap partner, but an
 * asset made only of them still has to appear in the table with a reason.
 */
function indexMeshes(scene, notes) {
  const map = new Map();
  const volumetric = new Set();
  scene.traverse((o) => {
    if (!o.visible || !o.isMesh || o.isInstancedMesh || !o.geometry || !o.geometry.attributes || !o.geometry.attributes.position) return;
    if (geometryTriangles(o.geometry) > MAX_INDEXED_TRIS) { notes.push(`skipped ${nearestName(o)}: > ${MAX_INDEXED_TRIS} triangles`); return; }
    if (nonSolid(o)) { volumetric.add(o); return; }
    const ci = new ColumnIndex(o);
    if (!ci.empty) map.set(o, ci);
  });
  return { map, volumetric };
}

function isFree(obj) { return !!(obj && obj.userData && obj.userData.placement === 'free'); }

/** The placed assets: direct children of each top-level scene child (or a top-level mesh itself). */
function collectAssets(scene, indices, volumetrics, contentBox) {
  const assets = [];
  let total = 0;
  const seen = new Set();
  for (const zone of scene.children) {
    if (!zone.visible || zone.isLight || zone.isCamera) continue;
    const children = zone.isMesh || zone.isInstancedMesh ? [zone] : zone.children;
    for (const child of children) {
      if (!child.visible || child.isLight || child.isCamera || seen.has(child)) continue;
      seen.add(child);
      const meshes = [];
      let instanced = 0;
      let volumetric = 0;
      child.traverse((o) => {
        if (!o.visible) return;
        if (o.isInstancedMesh) instanced += 1;
        else if (indices.has(o)) meshes.push(indices.get(o));
        else if (volumetrics.has(o)) volumetric += 1;
      });
      if (!meshes.length && !instanced && !volumetric) continue;
      total += 1;
      if (assets.length >= MAX_ASSETS) continue;
      const a = { obj: child, name: child.name || `${child.type}_${total}`, zone: zone === child ? '' : (zone.name || zone.type), meshes, instanced, exempt: '' };
      if (isFree(zone) || isFree(child)) a.exempt = 'free';
      // instanced first: an asset that is scatter PLUS a haze shell is exempt because its
      // instances cannot be sampled at this budget, which is the more informative reason
      else if (!meshes.length && instanced) a.exempt = 'instanced';
      else if (!meshes.length && volumetric) a.exempt = 'volumetric';
      else {
        a.min = [Infinity, Infinity, Infinity]; a.max = [-Infinity, -Infinity, -Infinity];
        let backdrop = 0;
        for (const ci of meshes) {
          for (let k = 0; k < 3; k++) { a.min[k] = Math.min(a.min[k], ci.min[k]); a.max[k] = Math.max(a.max[k], ci.max[k]); }
          // a ground / floor / water surface is what things sit ON, whatever its size
          if (ci.kind !== 'content' || ci.groundLike || ci.water) backdrop += 1;
        }
        if (backdrop === meshes.length || GROUND_NAME_RE.test(spaced(a.name)) || WATER_RE.test(spaced(a.name))) a.exempt = 'backdrop';
        else if (contentBox && enclosesContent(a, contentBox)) a.exempt = 'enclosure';
      }
      assets.push(a);
    }
  }
  return { assets, total };
}

function enclosesContent(a, cb) {
  const span = Math.max(a.max[0] - a.min[0], a.max[2] - a.min[2]);
  const cspan = Math.max(cb.max[0] - cb.min[0], cb.max[2] - cb.min[2]);
  const inside = [0, 1, 2].every((k) => a.min[k] <= cb.min[k] + EPS && a.max[k] >= cb.max[k] - EPS);
  return inside && span > 2 * Math.max(cspan, 1);
}

/** Up to MAX_COLUMNS foot columns [x, y, z] from the asset's lowest vertices. */
function footColumns(a) {
  const band = a.min[1] + Math.max(FOOT_BAND_MIN_M, FOOT_BAND_FRAC * (a.max[1] - a.min[1]));
  let lowest = null, minx = null, maxx = null, minz = null, maxz = null;
  for (const ci of a.meshes) {
    const xyz = ci.xyz;
    for (let i = 0; i < ci.count; i++) {
      const y = xyz[3 * i + 1];
      if (y > band) continue;
      const p = [xyz[3 * i], y, xyz[3 * i + 2]];
      if (!lowest || y < lowest[1]) lowest = p;
      if (!minx || p[0] < minx[0]) minx = p;
      if (!maxx || p[0] > maxx[0]) maxx = p;
      if (!minz || p[2] < minz[2]) minz = p;
      if (!maxz || p[2] > maxz[2]) maxz = p;
    }
  }
  const out = [];
  for (const p of [lowest, minx, maxx, minz, maxz]) {
    if (!p) continue;
    if (out.some((q) => Math.hypot(q[0] - p[0], q[2] - p[2]) < 0.01)) continue;
    out.push(p);
    if (out.length >= MAX_COLUMNS) break;
  }
  return out;
}

/** One column: nearest other surface below the foot, nearest above, own top. */
function probeColumn(a, col, indices, owner, groundY) {
  const [x, y0, z] = col;
  let y1 = y0;
  for (const ci of a.meshes) for (const h of ci.heightsAt(x, z)) if (h > y1) y1 = h;
  let below = null, above = null, onWater = false;
  for (const ci of indices.values()) {
    if (a.meshSet.has(ci) || ci.kind === 'sky' || !ci.containsXZ(x, z)) continue;
    if (ci.min[1] > a.max[1] + EPS) continue;      // entirely above the asset: cannot support or bury it
    // only something that STARTS below the foot can bury it (terrain, a floor slab, a table
    // under a badly stacked crate); a neighbour standing at the same level is a lateral
    // overlap and belongs to the interpenetration pass
    const startsBelow = ci.min[1] < y0 - EPS || ci.groundLike;
    for (const h of ci.heightsAt(x, z)) {
      // a foot at or under a water surface is IN the water: boats, jetty posts, reeds —
      // whatever is below the surface is invisible, so neither floating nor sunk applies
      if (ci.water && h >= y0 - EPS) onWater = true;
      if (h <= y0 + EPS) { if (!below || h > below.y) below = { y: h, ci }; } else if (startsBelow && (!above || h < above.y)) above = { y: h, ci };
    }
  }
  const res = { gap: below ? y0 - below.y : (groundY === null ? null : y0 - groundY),
    support: below ? owner(below.ci.mesh) : (groundY === null ? '' : 'ground_y'), water: onWater, sunk: 0, into: '' };
  if (above && (above.y <= y1 + EPS || above.ci.groundLike)) {
    res.sunk = above.y - y0; res.into = owner(above.ci.mesh);
    if (!below) { res.gap = -res.sunk; res.support = res.into; }   // under a surface with nothing beneath: gap is "that far under"
  }
  return res;
}

function boxOverlapFrac(a, b) {
  const size = (o) => [0, 1, 2].map((k) => Math.max(0.01, o.max[k] - o.min[k]));
  const sa = size(a), sb = size(b);
  let vol = 1;
  for (let k = 0; k < 3; k++) {
    const ov = Math.min(a.min[k] + sa[k], b.min[k] + sb[k]) - Math.max(a.min[k], b.min[k]);
    if (ov <= 0) return 0;
    vol *= ov;
  }
  return vol / Math.min(sa[0] * sa[1] * sa[2], sb[0] * sb[1] * sb[2]);
}

function touches(a, b, tol) {
  return [0, 1, 2].every((k) => a.min[k] - tol <= b.max[k] && b.min[k] - tol <= a.max[k]);
}

/** Fraction of the smaller asset's sampled vertices inside the larger one's meshes. */
function containment(small, large) {
  const total = small.meshes.reduce((n, ci) => n + ci.count, 0);
  const stride = Math.max(1, Math.floor(total / OVERLAP_SAMPLES));
  let sampled = 0, inside = 0;
  for (const ci of small.meshes) {
    for (let i = 0; i < ci.count; i += stride) {
      const x = ci.xyz[3 * i], y = ci.xyz[3 * i + 1], z = ci.xyz[3 * i + 2];
      sampled += 1;
      if (x < large.min[0] || x > large.max[0] || y < large.min[1] || y > large.max[1] || z < large.min[2] || z > large.max[2]) continue;
      if (large.meshes.some((lc) => lc.contains(x, y, z))) inside += 1;
    }
  }
  return { frac: sampled ? inside / sampled : 0, sampled };
}

function pairs(checked) {
  const out = [];
  for (let i = 0; i < checked.length; i++) {
    for (let j = i + 1; j < checked.length; j++) {
      const a = checked[i], b = checked[j];
      if (!touches(a, b, CONTACT_TOL_M)) continue;
      a.attached.push(b.name); b.attached.push(a.name);
      const aabb = boxOverlapFrac(a, b);
      if (aabb <= OVERLAP_MIN_FRAC) continue;
      const vol = (o) => [0, 1, 2].reduce((v, k) => v * Math.max(0.01, o.max[k] - o.min[k]), 1);
      const [small, large] = vol(a) <= vol(b) ? [a, b] : [b, a];
      const c = containment(small, large);
      if (c.frac > OVERLAP_MIN_FRAC) out.push({ a: small.name, b: large.name, zone_a: small.zone, zone_b: large.zone, aabb_overlap: r3(aabb), inside_frac: r3(c.frac), samples: c.sampled });
    }
  }
  return out.sort((x, y) => y.overlap - x.overlap).slice(0, 40);
}

/**
 * The placement table for `scene`.
 * @param {THREE.Scene} scene
 * @param {object} THREE          unused today (kept for symmetry with sceneCensus)
 * @param {{groundY?: number|null, contentBox?: {min:number[], max:number[]}|null}} opts
 */
export function placementTable(scene, THREE, opts = {}) {
  const t0 = Date.now();
  const groundY = Number.isFinite(opts.groundY) ? opts.groundY : null;
  const notes = [];
  scene.updateMatrixWorld(true);
  const { map: indices, volumetric } = indexMeshes(scene, notes);
  const { assets, total } = collectAssets(scene, indices, volumetric, opts.contentBox || null);
  const meshOwner = new Map();
  for (const a of assets) { a.meshSet = new Set(a.meshes); for (const ci of a.meshes) meshOwner.set(ci.mesh, a.name); }
  const owner = (mesh) => meshOwner.get(mesh) || nearestName(mesh);
  const checked = [];
  let timeCut = false;
  const rows = [];
  for (const a of assets) {
    const row = { name: a.name, zone: a.zone, meshes: a.meshes.length, instanced: a.instanced, exempt: a.exempt,
      bbox: a.min ? boxJson(a.min, a.max) : null };
    if (a.exempt) { rows.push(row); continue; }
    if (Date.now() - t0 > TIME_BUDGET_MS) { row.exempt = 'time_budget'; timeCut = true; rows.push(row); continue; }
    const cols = footColumns(a);
    let best = null, sunk = null, water = false;
    for (const col of cols) {
      const r = probeColumn(a, col, indices, owner, groundY);
      if (r.gap !== null && (!best || r.gap < best.gap)) best = r;
      if (r.sunk > 0 && (!sunk || r.sunk > sunk.sunk)) sunk = r;
      if (r.water) water = true;   // one foot in the water is enough: a jetty is half on the shore
    }
    a.attached = [];
    a.row = row;
    Object.assign(row, {
      columns: cols.length,
      ground_gap_m: best ? r3(best.gap) : null,
      support: best ? best.support : '',
      sunk_m: sunk ? r3(sunk.sunk) : 0,
      sunk_into: sunk ? sunk.into : '',
      on_water: water,
    });
    checked.push(a);
    rows.push(row);
  }
  const inter = pairs(checked);
  for (const a of checked) {
    const row = a.row;
    row.attached = a.attached.slice(0, 8);
    const gap = row.ground_gap_m === null ? 0 : row.ground_gap_m;
    row.supported = gap <= CONTACT_TOL_M || row.sunk_m > 0 || row.on_water;
    row.floating = gap > FLOATING_M && row.sunk_m === 0 && !row.on_water;
    row.touches_nothing = gap > CONTACT_TOL_M && row.sunk_m === 0 && !row.on_water && a.attached.length === 0;
  }
  const exempt = {};
  for (const r of rows) if (r.exempt) exempt[r.exempt] = (exempt[r.exempt] || 0) + 1;
  return {
    version: 1, assets: rows, interpenetrations: inter, total, checked: checked.length,
    truncated: total > assets.length || timeCut, truncated_by_time: timeCut, exempt,
    thresholds: { contact_m: CONTACT_TOL_M, floating_m: FLOATING_M, sunk_m: SUNK_M, overlap_min: OVERLAP_MIN_FRAC },
    ground_y: groundY, notes, duration_ms: Date.now() - t0,
  };
}

// ============================================================================ settle
// Deterministic auto-seat, added 2026-08-30.  Measured on the 48-run scene batteries:
// 38 sunken + 22 floating gate ERRORS were still present in FINAL rounds — the refine
// agent is handed the exact "lower X by 0.23 m" hint and demonstrably does not apply
// it reliably.  The measurement is deterministic, so the FIX can be too: after boot
// (before any census or render) every clearly mis-seated asset is translated onto its
// support.  The zone code keeps its wrong constant; every frame anyone sees or judges
// is seated.  The moves are returned and carried in the census, so nothing is silent.
//
// Deliberately conservative, mirroring the python gate's exemptions
// (codeverse/spatial/scene_placement.py — keep the two in sync):
//   · exempt assets (free / backdrop / enclosure / instanced) are never touched
//   · a foot in the water is a boat / jetty: never touched
//   · BURIED-ok names (basin, trench, pool…) are below ground by definition
//   · PARTIAL-ok names (rocks, posts, trees…) may bury half their height; only a
//     burial past 3/4 height is pulled up, and only to the 40 % embed that reads
//     as "grown in", never to the surface
//   · a floating asset that TOUCHES another asset may be mounted on it: skipped
//   · normal sunken assets keep a 4 cm embed (the zone recipes ask for 3-5 cm)
const SETTLE_EMBED_M = 0.04;
const SETTLE_PARTIAL_FRAC = 0.40;
const SETTLE_MAX_MOVE_M = 6;
const SETTLE_SEAT_EPS_M = 0.005;
const BURIED_OK_RE = /\b(basin|bed|canal|cave|cellar|crater|ditch|drain|foundations?|graves?|gutter|holes?|lakebed|moat|pits?|pools?|riverbed|trench(es)?|tunnels?|wells?)\b/i;
const SLOPE_CONFORMAL_RE = /\b(stairs?|stairways?|staircases?|steps?|ramps?|walkways?|paths?|roads?|terraces?|platforms?)\b/i;
const PARTIAL_OK_RE = /\b(boulders?|bridges?|bush(es)?|cliffs?|docks?|dunes?|fences?|flowers?|grass|hills?|jett(y|ies)|logs?|mounds?|outcrops?|pebbles?|piers?|piles?|plants?|poles?|posts?|reeds?|rocks?|roots?|shrubs?|stakes?|stones?|stumps?|trees?|trunks?|tufts?)\b/i;

/** Measure every placed asset once, then translate the clearly mis-seated ones onto
 * their support.  Returns `{count, moves}`; mutates object positions (world-space dy
 * applied through each parent's frame) and leaves matrices updated. */
export function settleScene(scene, THREE, opts = {}) {
  const groundY = Number.isFinite(opts.groundY) ? opts.groundY : null;
  const notes = [];
  scene.updateMatrixWorld(true);
  const { map: indices, volumetric } = indexMeshes(scene, notes);
  const { assets } = collectAssets(scene, indices, volumetric, opts.contentBox || null);
  const checked = assets.filter((a) => !a.exempt);
  for (const a of checked) { a.meshSet = new Set(a.meshes); }
  const meshOwner = new Map();
  for (const a of assets) for (const ci of a.meshes) meshOwner.set(ci.mesh, a.name);
  const owner = (mesh) => meshOwner.get(mesh) || nearestName(mesh);
  const moves = [];
  const t0 = Date.now();
  for (const a of checked) {
    if (Date.now() - t0 > TIME_BUDGET_MS) break;
    const cols = footColumns(a);
    if (!cols.length) continue;
    let best = null, sunk = null, water = false, minSunk = Infinity, anyRest = false;
    for (const col of cols) {
      const r = probeColumn(a, col, indices, owner, groundY);
      if (r.gap !== null && (!best || r.gap < best.gap)) best = r;
      if (r.sunk > 0) {
        if (!sunk || r.sunk > sunk.sunk) sunk = r;
        minSunk = Math.min(minSunk, r.sunk);
      } else {
        minSunk = 0;
        if (r.gap !== null && r.gap <= CONTACT_TOL_M) anyRest = true;
      }
      if (r.water) water = true;
    }
    if (water) continue;
    const name = spaced(a.name);
    const height = Math.max(a.max[1] - a.min[1], 1e-6);
    let dy = 0, why = '';
    if (sunk && sunk.sunk > 0) {
      if (BURIED_OK_RE.test(name) || SLOPE_CONFORMAL_RE.test(name)) continue;
      // Slope guard (2026-08-30): a structure following a hillside is "deeply sunk" at
      // its uphill columns while its downhill columns rest — lifting by the DEEPEST
      // burial strands the low end in the air.  Measured on t36_santorini: six
      // StoneStairways lifted +1.3..+3.3 m turned a 0.258 scene into a 0.000 one.
      // Settle only what is sunk at EVERY column, and lift by the SHALLOWEST burial.
      if (anyRest || minSunk < Math.max(SUNK_M, 0.5 * sunk.sunk)) continue;
      const frac = minSunk / height;
      if (PARTIAL_OK_RE.test(name)) {
        if (frac <= 0.75) continue;                       // grown / driven in: fine
        dy = minSunk - SETTLE_PARTIAL_FRAC * height;      // pull up to a 40 % embed
        why = 'sunken_partial';
      } else if (minSunk > SUNK_M) {
        dy = minSunk - SETTLE_EMBED_M;                    // reseat with a 4 cm embed
        why = 'sunken';
      } else { continue; }
    } else if (best && best.gap !== null && best.gap > FLOATING_M) {
      // mounted on a neighbour?  touching anything → leave it alone
      if (checked.some((b) => b !== a && touches(a, b, CONTACT_TOL_M))) continue;
      dy = -(best.gap - SETTLE_SEAT_EPS_M);               // drop onto the support
      why = 'floating';
    } else { continue; }
    if (!Number.isFinite(dy) || Math.abs(dy) < 1e-4 || Math.abs(dy) > Math.max(SETTLE_MAX_MOVE_M, height)) continue;
    const obj = a.obj, parent = obj.parent || scene;
    const w = new THREE.Vector3();
    obj.getWorldPosition(w);
    const delta = parent.worldToLocal(new THREE.Vector3(w.x, w.y + dy, w.z))
      .sub(parent.worldToLocal(w.clone()));
    obj.position.add(delta);
    moves.push({ name: a.name, zone: a.zone, dy_m: Math.round(dy * 1000) / 1000, why });
  }
  if (moves.length) scene.updateMatrixWorld(true);
  return { count: moves.length, moves };
}
