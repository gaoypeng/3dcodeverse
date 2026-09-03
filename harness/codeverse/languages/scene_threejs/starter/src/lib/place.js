/**
 * Placement, derived instead of typed — hand-typed yaws and y's are
 * what produce ungrounded geometry, backwards vehicles and
 * back-to-front seating, so the arithmetic lives here as functions.
 * Every function MUTATES and returns the object, so calls chain; the
 * camera builders (`shot`, `establishingShot`) instead return a ready
 * CAMERAS entry, and `route()` returns a stateless pose function.
 */

import * as THREE from 'three';

const _ray = new THREE.Raycaster();
const _down = new THREE.Vector3(0, -1, 0);
const _box = new THREE.Box3();
const _v = new THREE.Vector3();
const _t = new THREE.Vector3();
const DEG = Math.PI / 180;

/** Unit vector for a `forward` string as reported by assets_api.json. */
function fwdVec(forward) {
  switch (forward) {
    case '+X': return new THREE.Vector3(1, 0, 0);
    case '-X': return new THREE.Vector3(-1, 0, 0);
    case '+Z': return new THREE.Vector3(0, 0, 1);
    default: return new THREE.Vector3(0, 0, -1);   // '-Z', the three.js default
  }
}

/**
 * Every surface y over (x, z), highest first — one down-ray from `top`.
 * `intersectObjects` already sorts by distance, and the ray points
 * down, so the array comes back descending.
 */
function columnAt(x, z, targets, top, far, ignore) {
  _ray.set(_v.set(x, top, z), _down);
  _ray.far = far;
  const ys = [];
  for (const h of _ray.intersectObjects(targets, true)) {
    if (ignore && (h.object === ignore || ignore.getObjectById(h.object.id))) {
      continue;
    }
    ys.push(h.point.y);
  }
  return ys;
}

/**
 * Which of those surfaces a body at height `y` actually rests on: the
 * TOPMOST one at or below it — what is UNDER it, a quay beating the
 * seabed — and only when nothing is under it the LOWEST one above (an
 * object authored at y = 0 on a hillside is still lifted onto the hill).
 *
 * The split is the fix for a measured teleport: the down-ray starts far
 * overhead, so its nearest hit is a ROOF, and anything with something
 * over it flew up onto that roof — a crate under a 3 m arch seated at
 * y 3.62, and an interior camera handed its room group (which is what
 * the catalog tells composers to pass) shipped at eye 4.60 in a room
 * whose ceiling tops out at 3.00 (2026-09-01 port probe).
 */
function restingY(ys, y) {
  for (const v of ys) if (v <= y + 1e-4) return v;
  return ys.length ? ys[ys.length - 1] : null;
}

/**
 * Drop an object until it rests on whatever is under it.
 * Casts down from base corners AND centre: per ray the surface UNDER
 * the base wins (a quay beats the seabed), across rays the LOWEST wins
 * (a wide object on a slope buries its uphill side instead of hovering
 * on the high corner), then sinks `bite` metres so no seam shows.
 *
 * @param {THREE.Object3D} obj Already positioned in x/z.
 * @param {THREE.Object3D|THREE.Object3D[]} surfaces Ground, terrain,
 *   decks, roofs — anything that can be stood on.
 * @param {object} [opts] `bite` metres to sink (default 0.03),
 *   `maxDrop` how far down to look (default 500).
 * @returns {THREE.Object3D} obj, or obj unchanged when nothing is below.
 */
export function seat(obj, surfaces, opts = {}) {
  const targets = Array.isArray(surfaces) ? surfaces : [surfaces];
  const bite = opts.bite === undefined ? 0.03 : opts.bite;
  obj.updateMatrixWorld(true);
  // The SURFACE's world matrix has to be current too: a ground group
  // built and moved in the same tick still carries its old matrix, so
  // every raycast hits the old height and the whole scene seats to it
  // (measured 1.53 m under a quay, 2026-08-04 drawbridge repair).
  for (const t of targets) if (t) t.updateMatrixWorld(true);
  _box.setFromObject(obj);
  if (_box.isEmpty()) return obj;
  const y0 = _box.min.y;
  const xs = [_box.min.x, _box.max.x, (_box.min.x + _box.max.x) / 2];
  const zs = [_box.min.z, _box.max.z, (_box.min.z + _box.max.z) / 2];
  const drop = opts.maxDrop || 500;
  let best = null;
  for (const x of xs) {
    for (const z of zs) {
      const y = restingY(
          columnAt(x, z, targets, y0 + drop * 0.5, drop, obj), y0);
      if (y !== null && (best === null || y < best)) best = y;
    }
  }
  if (best === null) return obj;
  obj.position.y += best - y0 - bite;
  return obj;
}

/** Coerce a `surfaces` argument to an array, `undefined` to []. */
function surfaceList(surfaces) {
  if (surfaces === undefined || surfaces === null) return [];
  return Array.isArray(surfaces) ? surfaces : [surfaces];
}

/**
 * Lift a point so it clears the FLOOR under it — the surface
 * `restingY()` picks, not whatever is highest in the column: handed a
 * room group (which is exactly what the catalog tells composers to
 * pass indoors) the old rule lifted the eye 1.6 m above the CEILING
 * and the shot shipped from on top of the roof.
 * Only ever lifts (an intentionally aerial camera is left where it
 * is), and never through a low ceiling: in a room too short for the
 * clearance the eye stops 0.25 m under it instead of leaving.
 */
function liftAbove(pos, targets, clearance) {
  if (!targets.length) return pos;
  for (const t of targets) if (t) t.updateMatrixWorld(true);
  const ys = columnAt(pos.x, pos.z, targets, pos.y + 1000, 4000);
  const floorY = restingY(ys, pos.y);
  if (floorY === null) return pos;
  let want = Math.max(pos.y, floorY + clearance);
  let ceiling = null;                       // lowest surface above the floor
  for (const v of ys) if (v > floorY + 0.05) ceiling = v;
  if (ceiling !== null && want > ceiling - 0.25) {
    want = Math.max(floorY + 0.3, ceiling - 0.25);
  }
  pos.y = want;
  return pos;
}

/**
 * How far the view from `pos` to `look` gets before something is in
 * the way — ONE definition of "blocked" for both camera builders.
 *
 * @returns {{dir: THREE.Vector3, dist: number, hit: number}} `dir` unit
 *   eye-to-subject, `dist` their separation, `hit` distance to the
 *   first surface in between (Infinity when the view is clear).
 */
function probeView(pos, look, targets) {
  const dir = new THREE.Vector3().subVectors(look, pos);
  const dist = dir.length();
  if (dist < 1e-6 || !targets.length) return { dir, dist, hit: Infinity };
  dir.multiplyScalar(1 / dist);
  _ray.set(pos, dir);
  _ray.far = dist;
  const hits = _ray.intersectObjects(targets, true);
  return { dir, dist, hit: hits.length ? hits[0].distance : Infinity };
}

/** Nothing may fill the frame from closer than this. */
function clearNeeded(dist) {
  return Math.max(2, dist * 0.05);
}

/**
 * Build a CAMERAS entry whose geometry is verified, not guessed.
 * Two checks: (1) a down-ray over `surfaces` lifts the eye to at
 * least 1.6 m above the first hit — never lowers it; (2) a ray toward
 * `lookAt` must not hit anything closer than max(2 m, 5% of target
 * distance), else the camera slides back and up until the frame clears.
 *
 * @param {string} name Camera name for the CAMERAS array.
 * @param {THREE.Vector3|number[]} position Requested eye point.
 * @param {THREE.Vector3|number[]} lookAt What the shot is of.
 * @param {number} [fov] Vertical field of view (default 55).
 * @param {THREE.Object3D|THREE.Object3D[]} [surfaces] Ground, terrain,
 *   decks, walls — everything the camera must clear.
 * @returns {{name: string, position: number[], lookAt: number[],
 *   fov: number}} A ready CAMERAS entry.
 */
export function shot(name, position, lookAt, fov, surfaces) {
  const pos = position instanceof THREE.Vector3
      ? position.clone() : new THREE.Vector3(...position);
  const look = lookAt instanceof THREE.Vector3
      ? lookAt.clone() : new THREE.Vector3(...lookAt);
  const targets = surfaceList(surfaces);
  liftAbove(pos, targets, 1.6);
  for (let i = 0; i < 12 && targets.length; i++) {
    const v = probeView(pos, look, targets);
    if (v.dist < 1e-6) break;
    const clear = clearNeeded(v.dist);
    if (v.hit >= clear) break;
    pos.addScaledVector(v.dir, -(clear - v.hit + 0.25));
    pos.y += 0.5;
    liftAbove(pos, targets, 1.6);
  }
  // INDOORS THERE IS NOWHERE TO RETREAT TO: backing away from a screen
  // walks into the wall behind, and the shot ships from inside the
  // screen. If the view is still blocked, step FORWARD past the
  // blocker instead — what a person holding the camera would do.
  if (targets.length) {
    const v = probeView(pos, look, targets);
    if (v.dist > 1e-6 && v.hit < clearNeeded(v.dist)
        && v.hit < v.dist * 0.6) {
      pos.addScaledVector(v.dir, v.hit + 0.35);
      liftAbove(pos, targets, 1.6);
    }
  }
  return {
    name,
    position: [pos.x, pos.y, pos.z],
    lookAt: [look.x, look.y, look.z],
    fov: fov === undefined ? 55 : fov,
  };
}

// Widest a view direction may sit from the sun before the low-sun mie
// lobe washes the frame. 55 deg still bleached a third of it, 25 deg
// blew the whole image, 120 deg rendered clean (theme-park run).
const SUN_CLEAR_DEG = 90;

/**
 * Push a camera azimuth out of the sun's glare lobe.
 *
 * The rule has been in the composer prompt since the theme-park run and
 * was violated by three of the next three low-sun establishing shots,
 * so it is arithmetic now rather than advice. A camera at azimuth A
 * LOOKS toward A, so the sun at S is in frame when |A - S| is small.
 *
 * @param {number} azDeg Requested camera azimuth in degrees.
 * @param {number} [sunAzDeg] Sun azimuth, same convention; when absent
 *   the request passes through untouched.
 * @returns {number} The nearest azimuth at least 90 deg off the sun.
 */
export function awayFromSun(azDeg, sunAzDeg) {
  if (typeof sunAzDeg !== 'number' || !isFinite(sunAzDeg)) return azDeg;
  const wrap = (d) => ((d % 360) + 540) % 360 - 180;
  const off = wrap(azDeg - sunAzDeg);
  if (Math.abs(off) >= SUN_CLEAR_DEG) return azDeg;
  // Nearest legal side, so the requested composition moves as little as
  // it can rather than jumping to the anti-sun default.
  const side = off === 0 ? 1 : Math.sign(off);
  return sunAzDeg + side * SUN_CLEAR_DEG;
}

/**
 * Frame a hero object whole — the bbox-fitted establishing CAMERAS
 * entry, derived instead of eyeballed: project the 8 world-bbox
 * corners, dolly until the widest NDC extent hits `coverage`, seat the
 * eye with the same down-ray as `shot()`, and — when something stands
 * between the eye and the hero — slide along the fit sphere (up first,
 * then around) until the hero is actually visible, rather than shipping
 * a frame full of ridge.
 *
 * @param {string} name Camera name for the CAMERAS array.
 * @param {THREE.Object3D} heroObj The subject to fit whole.
 * @param {object} [opts] `azDeg` view azimuth (default 35), `elDeg`
 *   elevation (default 22), `fov` (default 45), `coverage` how much of
 *   the frame the hero spans, 0.5-0.9 (default 0.7), `surfaces` to
 *   keep the eye at least 1.6 m above the ground, `sunAzDeg` to have
 *   the azimuth pushed clear of the sun's glare lobe automatically.
 * @returns {{name: string, position: number[], lookAt: number[],
 *   fov: number}} A ready CAMERAS entry.
 */
export function establishingShot(name, heroObj, opts = {}) {
  // `azDeg` is where the EYE stands; it looks back at the centre, so
  // the view direction is azDeg + 180 and that is what must clear the
  // sun. Correcting the eye azimuth directly would aim it AT the glare.
  const azDeg = awayFromSun(
      (opts.azDeg === undefined ? 35 : opts.azDeg) + 180,
      opts.sunAzDeg) - 180;
  const elDeg = opts.elDeg === undefined ? 22 : opts.elDeg;
  const fov = opts.fov === undefined ? 45 : opts.fov;
  const coverage = opts.coverage === undefined ? 0.7 : opts.coverage;
  const targets = surfaceList(opts.surfaces);
  heroObj.updateMatrixWorld(true);
  _box.setFromObject(heroObj);
  if (_box.isEmpty()) {
    return { name, position: [10, 6, 10], lookAt: [0, 0, 0], fov };
  }
  const center = _box.getCenter(new THREE.Vector3());
  const radius = Math.max(
      _box.getSize(new THREE.Vector3()).length() / 2, 0.25);
  const corners = [];
  for (const cx of [_box.min.x, _box.max.x])
    for (const cy of [_box.min.y, _box.max.y])
      for (const cz of [_box.min.z, _box.max.z])
        corners.push(new THREE.Vector3(cx, cy, cz));
  // 16:9 is the harness's render aspect (800x450 / 1280x720).
  const camera = new THREE.PerspectiveCamera(fov, 16 / 9, 0.1, 1e6);
  /** The dolly distance that puts the widest bbox extent on `coverage`. */
  const fit = (dirVec) => {
    let d = radius / Math.sin((fov / 2) * DEG);
    for (let it = 0; it < 5; it++) {
      camera.position.copy(center).addScaledVector(dirVec, d);
      camera.lookAt(center);
      camera.updateMatrixWorld(true);
      let maxExt = 1e-6;
      for (const c of corners) {
        _v.copy(c).project(camera);
        maxExt = Math.max(maxExt, Math.abs(_v.x), Math.abs(_v.y));
      }
      d = d * (maxExt / coverage);
    }
    return d;
  };
  const eyeDir = (aDeg, eDeg) => {
    const a = aDeg * DEG;
    const e = eDeg * DEG;
    return new THREE.Vector3(Math.cos(e) * Math.cos(a), Math.sin(e),
                             Math.cos(e) * Math.sin(a));
  };
  // MOVE ON THE FIT SPHERE, NEVER BACKWARDS. `shot()` retreats from a
  // blocker, which is right for a street camera and wrong here: backing
  // away shrinks the hero the establishing shot exists to show. Look
  // OVER the obstacle first (raise the elevation — what a person with a
  // camera does), then step around it (swing the azimuth), re-fitting at
  // every candidate so the framing survives the move. Measured before
  // this: a hero behind a 9 m ridge shipped a frame filled by ridge at
  // 1.19 m of an 8.92 m shot — the "one wall fills the frame" failure
  // the module was written to end (2026-09-01 port probe).
  const arcs = [[0, 0], [0, 9], [0, 19], [0, 31], [24, 6], [-24, 6],
                [24, 20], [-24, 20], [48, 10], [-48, 10], [0, 46]];
  let pos = null;
  let bestGap = -1;
  for (const [dAz, dEl] of arcs) {
    const a = dAz === 0 ? azDeg
        : awayFromSun(azDeg + dAz + 180, opts.sunAzDeg) - 180;
    const dir = eyeDir(a, Math.min(70, elDeg + dEl));
    const p = center.clone().addScaledVector(dir, fit(dir));
    liftAbove(p, targets, 1.6);
    const v = probeView(p, center, targets);
    if (v.hit >= clearNeeded(v.dist)) { pos = p; break; }
    // Nothing clears: keep whichever candidate sees furthest into the
    // scene, so the shot degrades toward the most open direction.
    if (v.hit > bestGap) { bestGap = v.hit; pos = p; }
  }
  return {
    name,
    position: [pos.x, pos.y, pos.z],
    lookAt: [center.x, center.y, center.z],
    fov,
  };
}

/**
 * Turn an object so its own forward axis points at a target.
 *
 * @param {THREE.Object3D} obj
 * @param {THREE.Vector3|number[]} target World point to face.
 * @param {string} [forward] The asset's forward as reported by
 *   assets_api.json ('+X' | '-X' | '+Z' | '-Z'); defaults to '-Z'.
 * @returns {THREE.Object3D} obj
 */
export function faceToward(obj, target, forward) {
  const p = target instanceof THREE.Vector3
      ? target : new THREE.Vector3(...target);
  _t.subVectors(p, obj.position);
  _t.y = 0;
  if (_t.lengthSq() < 1e-9) return obj;
  return alignAlong(obj, _t, forward);
}

/**
 * Turn an object so its forward axis runs along a direction — pass a
 * route tangent, not a guessed angle (backwards vehicles came from
 * guessed yaws).
 *
 * @param {THREE.Object3D} obj
 * @param {THREE.Vector3|number[]} dir Direction of travel (y ignored).
 * @param {string} [forward] Asset forward from assets_api.json.
 * @returns {THREE.Object3D} obj
 */
export function alignAlong(obj, dir, forward) {
  const d = dir instanceof THREE.Vector3
      ? dir.clone() : new THREE.Vector3(...dir);
  d.y = 0;
  if (d.lengthSq() < 1e-9) return obj;
  d.normalize();
  const f = fwdVec(forward);
  // Yaw that takes the asset's own forward onto d.
  obj.rotation.y = Math.atan2(d.x, d.z) - Math.atan2(f.x, f.z);
  return obj;
}

/**
 * Face an object the same way as the thing it sits on or in — a
 * person must look the way the chair looks, not AT the chair (the
 * classic 180-degree seating error).
 *
 * @param {THREE.Object3D} obj The figure being seated.
 * @param {THREE.Object3D} seatObj The chair/bench/stool.
 * @param {string} [forward] The figure's forward from assets_api.json.
 * @param {string} [seatForward] The seat's forward from assets_api.json.
 * @returns {THREE.Object3D} obj
 */
export function matchFacing(obj, seatObj, forward, seatForward) {
  const f = fwdVec(seatForward).applyEuler(
      new THREE.Euler(0, seatObj.rotation.y, 0));
  return alignAlong(obj, f, forward);
}

/**
 * Sample a route as a closed or open path and walk objects along it.
 *
 * Returns positions AND tangents, so callers cannot place a vehicle
 * without also having the direction it should face.
 *
 * A closed path divides into n equal arcs (the last sample is one arc
 * short of the start, which is where it belongs). An OPEN path spans
 * end to end — n samples, n-1 gaps: dividing by n instead left the row
 * a whole gap short of its own last waypoint (6 posts over a 12 m quay
 * stopped at x = 10, and the missing post is visible in the frame).
 *
 * @param {number[][]} points Control points [[x,y,z], ...].
 * @param {number} n How many samples.
 * @param {object} [opts] `closed` (default true).
 * @returns {Array<{position: THREE.Vector3, tangent: THREE.Vector3}>}
 */
export function alongPath(points, n, opts = {}) {
  const closed = opts.closed !== false;
  const curve = new THREE.CatmullRomCurve3(
      points.map((p) => new THREE.Vector3(...p)), closed);
  const out = [];
  const span = closed ? n : Math.max(1, n - 1);
  for (let i = 0; i < n; i++) {
    const t = i / span;
    out.push({
      position: curve.getPointAt(t),
      tangent: curve.getTangentAt(t).setY(0).normalize(),
    });
  }
  return out;
}

/**
 * A teleport-free, dt-free path mover — `alongPath()`'s animated twin.
 * The pose is a pure function of absolute `t`: no internal state, no
 * `+=` drift, a closed loop wraps continuously by construction, and
 * an open path ping-pongs instead of the modulo jump to its start.
 *
 * @param {number[][]|THREE.Vector3[]} points Control points.
 * @param {object} [opts] `closed` (default true; false ping-pongs),
 *   `speed` metres per second (default 1), `forward` the asset's
 *   forward from assets_api.json, `wheels` array of `{node, radius}`
 *   rotated by arcDistance/radius so wheels roll exactly the ground
 *   they cover, `lane` signed metres of sideways offset (positive =
 *   left of travel; a ping-pong return leg flips sides, so a two-way
 *   path keeps its directions apart), `phase` 0..1 head start along
 *   the path. `lane`/`phase` are pure per-walker constants — several
 *   walkers may share one path without ever meeting, and `crowdOn()`
 *   does that assignment arithmetic for a whole crowd. `avoid` the
 *   scene (or an array of roots): the path is re-projected clear of
 *   furniture-scale solids at build time — pass it whenever a route
 *   crosses furnished space; `clearance` kept margin in metres
 *   (default 0.45).
 * @returns {{length: number, curve: THREE.CatmullRomCurve3,
 *   poseAt: function}} `poseAt(t, obj, over)` places, orients and
 *   rolls `obj` at absolute time `t` and returns it; without `obj` it
 *   returns `{position, tangent, u}`. `over` is an optional per-call
 *   `{lane, phase}` override — how `crowdOn()` walks many figures on
 *   one shared curve.
 */
/**
 * Collect furniture-scale obstacle boxes under `avoid` — the SAME
 * filter the renderer's mover-through-solid instrument uses, so what
 * the path avoids and what the instrument flags stay one definition.
 * Buildings are not obstacles (a covered lane sits INSIDE its bbox).
 */
function obstacleBoxes(avoid) {
  const solids = [];
  const roots = Array.isArray(avoid) ? avoid : [avoid];
  for (const root of roots) {
    if (!root || !root.traverse) continue;
    if (root.updateMatrixWorld) root.updateMatrixWorld(true);
    root.traverse((o) => {
      if (!o.isMesh || solids.length >= 3000) return;
      const nm = (o.name || (o.parent && o.parent.name) || '');
      if (/ground|floor|terrain|road|path|water|sky|dome|wall|roof|ceiling/i
          .test(nm)) return;
      const b = new THREE.Box3().setFromObject(o);
      if (b.isEmpty()) return;
      const sx = b.max.x - b.min.x;
      const sy = b.max.y - b.min.y;
      const sz = b.max.z - b.min.z;
      // Height alone is not architecture: fountains/columns/statues
      // are tall AND collide — the footprint caps below are what
      // separate an obstacle from a building.
      if (sy < 0.3 || sy > 14) return;
      if (Math.max(sx, sz) > 12 || sx * sz > 40) return;
      if (Math.min(sx, sz) < 0.05) return;
      solids.push(b);
    });
  }
  return solids;
}

/** Push a point out of every expanded box via the cheapest face. */
function pushClear(p, solids, clearance) {
  for (let iter = 0; iter < 3; iter++) {
    let moved = false;
    for (const b of solids) {
      if (p.x < b.min.x - clearance || p.x > b.max.x + clearance ||
          p.z < b.min.z - clearance || p.z > b.max.z + clearance) {
        continue;
      }
      if (b.min.y > p.y + 1.2 || b.max.y < p.y - 1.2) continue;
      const dxMin = p.x - (b.min.x - clearance);
      const dxMax = (b.max.x + clearance) - p.x;
      const dzMin = p.z - (b.min.z - clearance);
      const dzMax = (b.max.z + clearance) - p.z;
      const m = Math.min(dxMin, dxMax, dzMin, dzMax);
      if (m === dxMin) p.x = b.min.x - clearance;
      else if (m === dxMax) p.x = b.max.x + clearance;
      else if (m === dzMin) p.z = b.min.z - clearance;
      else p.z = b.max.z + clearance;
      moved = true;
    }
    if (!moved) break;
  }
  return p;
}

/**
 * Densify a path to ~0.75 m samples and push every blocked sample
 * clear, so a leg that CROSSES a stall mid-segment reroutes around
 * it — waypoint-only checks miss exactly that case.
 */
function clearPath(pts, solids, clearance, closed) {
  if (!solids.length || pts.length < 2) return pts;
  const out = [];
  const last = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < last; i++) {
    const A = pts[i];
    const B = pts[(i + 1) % pts.length];
    const steps = Math.max(1, Math.ceil(A.distanceTo(B) / 0.75));
    for (let k = 0; k < steps; k++) {
      out.push(pushClear(A.clone().lerp(B, k / steps), solids, clearance));
    }
  }
  if (!closed) out.push(pushClear(pts[pts.length - 1].clone(),
                                  solids, clearance));
  return out;
}

export function route(points, opts = {}) {
  const closed = opts.closed !== false;
  const speed = opts.speed === undefined ? 1 : opts.speed;
  const lane0 = opts.lane === undefined ? 0 : opts.lane;
  const phase0 = opts.phase === undefined ? 0 : opts.phase;
  const wheels = opts.wheels || [];
  let pts = points.map((p) => (p instanceof THREE.Vector3
      ? p.clone() : new THREE.Vector3(...p)));
  // `avoid: scene` makes furniture-clearance CONSTRUCTIVE: the path
  // is re-projected off solids at build time, so a walker cannot
  // stride through a stall whatever coordinates were authored.
  if (opts.avoid) {
    pts = clearPath(pts, obstacleBoxes(opts.avoid),
                    opts.clearance === undefined ? 0.45 : opts.clearance,
                    closed);
  }
  const curve = new THREE.CatmullRomCurve3(pts, closed);
  const length = curve.getLength();
  const poseAt = (t, obj, over) => {
    const lane = over && over.lane !== undefined ? over.lane : lane0;
    const phase = over && over.phase !== undefined ? over.phase : phase0;
    const s = t * speed + phase * length;
    let u = 0;
    let dirSign = 1;
    if (length > 1e-9) {
      if (closed) {
        u = ((s / length) % 1 + 1) % 1;
      } else {
        // Ping-pong: out along the path, back along the path — never
        // the open-path modulo jump from the end to the start.
        const p = ((s / length) % 2 + 2) % 2;
        if (p <= 1) {
          u = p;
        } else {
          u = 2 - p;
          dirSign = -1;
        }
      }
    }
    const position = curve.getPointAt(u);
    const tangent = curve.getTangentAt(u).multiplyScalar(dirSign);
    if (lane) {
      // Sideways offset in the horizontal plane, positive = left of
      // travel; built from the dirSign-flipped tangent so a ping-pong
      // path returns on its other side (two-way traffic self-separates).
      const h = Math.hypot(tangent.x, tangent.z);
      if (h > 1e-9) {
        position.x += lane * (tangent.z / h);
        position.z += lane * (-tangent.x / h);
      }
    }
    if (!obj) return { position, tangent, u };
    obj.position.copy(position);
    alignAlong(obj, tangent, opts.forward);
    // Roll from the arc distance actually COVERED, which on a ping-pong
    // return leg decreases: `s` only ever grows, so a cart driving back
    // along an open path spun its wheels forward while reversing
    // (measured -0.4 m of travel against +0.8 rad of wheel, 2026-09-01).
    // A closed loop keeps `s` — u wraps, and wrapping u would snap the
    // wheel by length/radius once a lap.
    const rolled = closed ? s : u * length;
    for (const w of wheels) {
      w.node.rotation.x = rolled / w.radius;
    }
    return obj;
  };
  return { length, curve, poseAt };
}

/**
 * Give every figure on a shared route its own lane and phase, so a
 * crowd can never bunch into one body. The guarantee is arithmetic,
 * not a solver: one shared speed + equal phase slots keep the
 * arc-length gap >= `spacing` forever (golden-ratio jitter is bounded
 * by 80% of the slot slack, so it breaks the parade look without
 * touching the bound); overflow opens parallel lanes at `laneStep`.
 *
 * @param {{length: number, poseAt: function}} routeObj From `route()`
 *   — one shared speed per route is what makes the gap permanent (two
 *   speeds on one path collide within seconds).
 * @param {THREE.Object3D[]} figures The walkers, one entry each.
 * @param {object} [opts] `spacing` minimum along-path gap in metres
 *   (default 2 — walker body plus clear air), `laneStep` sideways gap
 *   between overflow lanes (default 0.7 — shoulder width + margin).
 * @returns {{assignments: Array<{lane: number, phase: number}>,
 *   tick: function}} `tick(t)` poses every figure at absolute time
 *   `t` (pure — call it from the scene's `tick`); `assignments` is
 *   the per-figure `{lane, phase}` in `figures` order, overriding the
 *   route's own `lane`/`phase` opts.
 */
export function crowdOn(routeObj, figures, opts = {}) {
  const spacing = opts.spacing === undefined ? 2 : opts.spacing;
  const laneStep = opts.laneStep === undefined ? 0.7 : opts.laneStep;
  const n = figures.length;
  const cap = Math.max(1, Math.floor(routeObj.length / spacing));
  const lanes = Math.max(1, Math.ceil(n / cap));
  const per = Math.ceil(n / lanes);
  const assignments = [];
  for (let i = 0; i < n; i++) {
    const k = Math.floor(i / per);            // lane index
    const j = i - k * per;                    // slot within the lane
    const m = Math.min(per, n - k * per);     // walkers in this lane
    const slot = 1 / m;
    const spacingU = Math.min(spacing / routeObj.length, slot);
    // Jitter spends at most 80% of the slack, so the consecutive gap
    // slot - 2*jit stays >= spacingU: the bound survives the jitter.
    const jit = 0.4 * (slot - spacingU);
    const eps = (((j * 0.618034) % 1) - 0.5) * 2 * jit;
    assignments.push({
      lane: (k - (lanes - 1) / 2) * laneStep,
      phase: k * (slot / lanes) + j * slot + eps,
    });
  }
  return {
    assignments,
    tick(t) {
      for (let i = 0; i < n; i++) {
        routeObj.poseAt(t, figures[i], assignments[i]);
      }
      return figures;
    },
  };
}
