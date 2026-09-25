/**
 * Page-side frame/camera instruments: luminance statistics from a downscaled
 * readback of the render canvas and near-geometry tests for a camera.
 */

import { GROUND_NAME_RE, classifyBackdrop, nonSolid, drawableBox, nameText } from './backdrop.mjs';

export const SAMPLE_W = 96;
export const SAMPLE_H = 54;

/**
 * Downscaled RGBA readback of `canvas` on the shared 96x54 sampling grid.
 * THE one place a render canvas is sampled page-side (frame statistics here,
 * coverage mask passes in host_coverage.mjs) — same grid, same cost, so the
 * numbers of the two instruments are comparable.
 * @returns {{data: Uint8ClampedArray, n: number}}
 */
export function sampleFrame(canvas) {
  const small = document.createElement('canvas');
  small.width = SAMPLE_W;
  small.height = SAMPLE_H;
  const ctx = small.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(canvas, 0, 0, SAMPLE_W, SAMPLE_H);
  return { data: ctx.getImageData(0, 0, SAMPLE_W, SAMPLE_H).data, n: SAMPLE_W * SAMPLE_H };
}

/** Mean luminance and dark/blown fractions of the current canvas. */
export function frameStats(canvas) {
  const { data, n } = sampleFrame(canvas);
  let sum = 0, sumSq = 0, dark = 0, blown = 0;
  const hist = new Uint32Array(8);
  for (let i = 0; i < n; i++) {
    const r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    sum += lum; sumSq += lum * lum;
    if (lum < 10) dark += 1;
    if (r > 250 && g > 250 && b > 250) blown += 1;
    hist[Math.min(7, Math.floor(lum / 32))] += 1;
  }
  const mean = sum / n;
  const std = Math.sqrt(Math.max(0, sumSq / n - mean * mean));
  // "flat" frames: one luminance bucket holds almost everything (void / wall)
  let modal = 0;
  for (const h of hist) modal = Math.max(modal, h);
  return {
    mean_lum: +(mean / 255).toFixed(4),
    lum_std: +(std / 255).toFixed(4),
    dark_frac: +(dark / n).toFixed(4),
    blown_frac: +(blown / n).toFixed(4),
    modal_frac: +(modal / n).toFixed(4),
  };
}

/** The keys `frameStats` returns — what an external frame replaces in a camera check. */
export const FRAME_STAT_KEYS = ['mean_lum', 'lum_std', 'dark_frac', 'blown_frac', 'modal_frac'];

/**
 * `frameStats` of an image another renderer wrote (scene_blender: the Blender PNG of a view,
 * handed in as a data URL): the SAME sampling grid and the same statistics as a canvas frame,
 * so both languages' metrics.json read through one implementation and one set of thresholds.
 */
export async function imageFrameStats(url) {
  const img = new Image();
  img.src = url;
  await img.decode();
  return frameStats(img);
}

// six skewed directions, not the axes: a ray straight up from a round camera position hits the
// centre line of an axis-aligned ceiling, i.e. the diagonal both triangles share, twice
const PARITY_DIRS = [[1, 0.37, 0.23], [-1, 0.29, -0.41], [0.31, 1, 0.19], [-0.27, -1, 0.35], [0.23, 0.41, 1], [-0.39, 0.17, -1]];

/**
 * Is `eye` inside the mesh's own volume?  Ray parity in six directions over the
 * geometry's triangles (both faces — a back-face culled room interior still counts,
 * which is what the Raycaster could not do), majority of the six.  A room with an
 * open door or no ceiling still reads inside; a lattice, a star, a thin ring or a
 * hollow arch the lens merely stands near does not.
 */
export function eyeInsideMesh(o, eye, THREE) {
  const g = o.geometry, pos = g.attributes.position, idx = g.index;
  if (!pos) return false;
  const local = eye.clone().applyMatrix4(new THREE.Matrix4().copy(o.matrixWorld).invert());
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), hit = new THREE.Vector3();
  const n = idx ? idx.count : pos.count;
  const vertex = (i) => (idx ? idx.getX(i) : i);
  let odd = 0;
  for (const d of PARITY_DIRS) {
    const ray = new THREE.Ray(local, new THREE.Vector3(...d).normalize());
    let hits = 0;
    for (let i = 0; i + 2 < n; i += 3) {
      a.fromBufferAttribute(pos, vertex(i)); b.fromBufferAttribute(pos, vertex(i + 1)); c.fromBufferAttribute(pos, vertex(i + 2));
      if (ray.intersectTriangle(a, b, c, false, hit)) hits += 1;
    }
    if (hits % 2 === 1) odd += 1;
  }
  return odd >= 4;
}

/** A sight ray that ends this close is a surface in the lens, not the shot. */
export const BLOCKED_M = 1.5;

/** A ground-classified mesh at least this wide is terrain, not a floor a shot may sit under. */
export const TERRAIN_SPAN_M = 40;

/** Does the name carry a ground word?  CamelCase split first: `HeadlandTerrain` is a terrain. */
export function groundNamed(name) {
  return GROUND_NAME_RE.test(nameText(name));
}

/**
 * The lowest terrain-scale ground surface straight above `eye`, or null.  Cast DOWN from
 * 500 m up and take the last hit above the eye: a FrontSide terrain is back faces to a ray
 * from beneath, and the Raycaster honours material.side.
 */
export function groundAbove(eye, groundMeshes, THREE) {
  if (!groundMeshes.length) return null;
  try {
    const top = eye.clone(); top.y += 500;
    const down = new THREE.Raycaster(top, new THREE.Vector3(0, -1, 0), 0, 500 - 1e-3);
    const hits = down.intersectObjects(groundMeshes, false);
    const h = hits.length ? hits[hits.length - 1] : null;
    return h ? { distance: +(h.point.y - eye.y).toFixed(3), y: h.point.y, name: h.object.name || h.object.parent?.name || h.object.type } : null;
  } catch (e) { return null; }
}

/**
 * Near-geometry test: centre + 3x3 grid rays from the camera; nearest hit
 * distance, plus meshes whose own volume contains the eye (closed rooms,
 * back-face culled interiors — `eyeInsideMesh`).
 */
export function nearGeometry(scene, camera, THREE, limitM = 0.3, lookAt = null) {
  const rc = new THREE.Raycaster();
  rc.far = 50;
  let nearest = Infinity;
  let nearestName = '';
  const targets = [];
  // A volumetric pass is not something a lens can be "inside": greenhouse's
  // `camera inside ['SunShaft_0','SunShaft_3']` (bench/out/scene_baseline, 2026-09-05)
  // was three god-ray slabs of MeshBasicMaterial at opacity 0.075 with
  // `depthWrite: false` — you see straight through them, which is their whole job.
  // Same rule as the placement gate, one spelling, in backdrop.mjs.
  scene.traverse((o) => { if ((o.isMesh || o.isInstancedMesh) && o.visible && !nonSolid(o)) targets.push(o); });
  const pts = [];
  for (let gy = -1; gy <= 1; gy++) for (let gx = -1; gx <= 1; gx++) pts.push([gx * 0.6, gy * 0.6]);
  let nearRays = 0;
  for (const [x, y] of pts) {
    rc.setFromCamera(new THREE.Vector2(x, y), camera);
    let hit = null;
    try { hit = rc.intersectObjects(targets, false)[0]; } catch (e) { hit = null; }
    if (hit) {
      if (hit.distance < BLOCKED_M) nearRays += 1;
      if (hit.distance < nearest) { nearest = hit.distance; nearestName = hit.object.name || hit.object.parent?.name || hit.object.type; }
    }
  }
  const inside = [];
  const eye = camera.position;
  const groundMeshes = [];
  for (const o of targets) {
    if (o.isInstancedMesh) continue;
    const box = drawableBox(o, THREE);
    if (!box) continue;
    const s = box.getSize(new THREE.Vector3());
    // terrain overhead must also be NAMED as ground: a 40 m 'Ceiling' is ground-shaped to the
    // classifier, and loop 25's cathedral read "camera 17.8 m under Ceiling" on every interior
    // camera (2026-09-09) — a lens under a roof is a shot, a lens under a terrain is a bug
    if (Math.max(s.x, s.z) >= TERRAIN_SPAN_M && classifyBackdrop(o, box) === 'ground'
        && groundNamed(o.name || o.parent?.name || '')) groundMeshes.push(o);
    if (Math.max(s.x, s.y, s.z) > 60 || Math.min(s.x, s.y, s.z) < 0.05) continue; // ground/sky/flat decals
    // The bbox is the cheap pre-filter; the verdict is the mesh's own volume.  A windmill's
    // 22 m lattice sails own a 22 x 22 m bbox that is nearly all air: measured 2026-09-07
    // (loop 9), a detail camera 4.9 m from the nearest surface was "inside ['Sails_1']" for
    // three rounds, the repair moved it twice for nothing and the judge marked it critical.
    if (box.containsPoint(eye) && eyeInsideMesh(o, eye, THREE)) inside.push(o.name || o.parent?.name || o.type);
  }
  // The surface under the lens: one ray straight down to the nearest solid mesh.  The
  // census ground_y is the TOP of every ground mesh scene-wide, so on relief terrain a
  // camera at eye level over a low patch read "0.06 m above ground — ant's-eye view"
  // against a 3.1 m snow mound elsewhere (loop 21 ski station, 2026-09-09) and the judge
  // repeated the false warning as a major issue.  Null when nothing lies beneath (a lens
  // under the terrain, or off the edge of the ground) — the gate then falls back to ground_y.
  let groundBelow = null;
  let groundBelowName = '';
  try {
    const down = new THREE.Raycaster(eye.clone(), new THREE.Vector3(0, -1, 0), 0, 500);
    const h = down.intersectObjects(targets, false)[0];
    if (h) { groundBelow = +h.distance.toFixed(3); groundBelowName = h.object.name || h.object.parent?.name || h.object.type; }
  } catch (e) { groundBelow = null; }
  // …and a ground surface ABOVE the lens: a camera under the terrain looks up at back faces,
  // so the frame renders "fine" (the structures float over a void) and nothing said buried —
  // cmp6's lighthouse (2026-09-09) shot its slipway from under the headland for three rounds
  // while the judge called it major each time.  Ground-classified meshes only (backdrop.mjs):
  // a roof or a bridge deck overhead is a shot, a terrain overhead is a bug.
  const above = groundAbove(eye, groundMeshes, THREE);
  // The line of sight to the plan's lookAt: what cuts it, and how far along.  cmp6's crypt
  // (2026-09-09): AthanorDetail's ray to the hero met VaultPillarMasonry at 0.9 m of 2.8 m,
  // the hero was never in frame, and the judge called IT "a massive untextured grey box".
  let targetDist = null, targetHit = null, targetHitName = '';
  if (Array.isArray(lookAt) && lookAt.length === 3) {
    const to = new THREE.Vector3(lookAt[0], lookAt[1], lookAt[2]).sub(eye);
    targetDist = +to.length().toFixed(3);
    if (targetDist > 1e-6) {
      try {
        const los = new THREE.Raycaster(eye.clone(), to.clone().normalize(), 0, targetDist);
        const h = los.intersectObjects(targets, false)[0];
        if (h) { targetHit = +h.distance.toFixed(3); targetHitName = h.object.name || h.object.parent?.name || h.object.type; }
      } catch (e) { targetHit = null; }
    }
  }
  return {
    nearest_hit_m: Number.isFinite(nearest) ? +nearest.toFixed(3) : null,
    nearest_hit_name: nearestName,
    rays_total: pts.length,
    inside_mesh_bbox: inside.slice(0, 5),
    camera_in_geometry: (Number.isFinite(nearest) && nearest < limitM) || inside.length > 0,
    eye_height_m: +eye.y.toFixed(3),
    ground_below_m: groundBelow,
    ground_below_name: groundBelowName,
    ground_above_m: above ? above.distance : null,
    ground_above_name: above ? above.name : '',
    near_rays: nearRays,
    near_limit_m: BLOCKED_M,
    target_distance_m: targetDist,
    target_hit_m: targetHit,
    target_hit_name: targetHitName,
  };
}

/**
 * Deterministic camera repair, added 2026-08-30; ON by default since 3f463ce
 * (drivers pass --camera-repair; python's probe_env_args() disables it only when
 * C3D_CAMERA_REPAIR says false/off/no/0).
 *
 * Why: cameras belong to the PLAN — no refine agent owns a file that could fix
 * one, so camera_in_geometry stood in final rounds across whole batteries
 * (camera_unusable in 23/48 scored runs; fv_izakaya_night lost 0.26 to it three
 * rounds straight).  The measurement (nearGeometry) is deterministic, so the fix
 * can be: try the smallest retreat (backward along the view axis, then upward)
 * that clears the lens, keeping lookAt — composition survives, the shot stops
 * being inside the furniture.
 *
 * Pure: no renderer, raycasts only.  `makeCam(spec)` is supplied by the host so
 * near/far/aspect match the real render exactly.
 */
// [back, up, side] metres along the view axis, world up, and the camera's right; the search
// takes the first that clears.  Sideways moves are what step out from behind a pillar or a
// snow bank — a retreat alone only backs into the same line of sight.
const REPAIR_OFFSETS = [
  [0, 0, 0], [0, 0.5, 0], [0.5, 0, 0], [0.5, 0.5, 0], [1, 0.5, 0], [1, 1, 0], [0, 1, 1.5], [0, 1, -1.5],
  [2, 1, 0], [1, 1.5, 2], [1, 1.5, -2], [2, 2, 0], [2, 2, 2.5], [2, 2, -2.5], [3, 2, 0], [3, 3, 3], [3, 3, -3], [4, 2, 0],
];
const REPAIR_CLEAR_M = 0.5;
const EYE_ABOVE_GROUND_M = 1.6;
const REPAIR_BLOCKED_RAYS = 6;          // of the 9 sight rays within BLOCKED_M: the frame is a surface
const REPAIR_CUT_FRAC = 0.5;            // the line of sight to lookAt cut before this fraction of the distance

/** Is this lens clear: not in geometry, nothing within REPAIR_CLEAR_M, not staring at a surface,
 *  and its line of sight to the plan's lookAt not cut short? */
function lensClear(n) {
  if (n.camera_in_geometry) return false;
  if (n.nearest_hit_m !== null && n.nearest_hit_m < REPAIR_CLEAR_M) return false;
  if (n.near_rays >= REPAIR_BLOCKED_RAYS) return false;
  if (n.target_hit_m !== null && n.target_distance_m && n.target_hit_m < REPAIR_CUT_FRAC * n.target_distance_m) return false;
  return true;
}

export function repairCameraSpec(scene, spec, THREE, makeCam) {
  const dir = new THREE.Vector3(
    spec.position[0] - spec.lookAt[0], spec.position[1] - spec.lookAt[1], spec.position[2] - spec.lookAt[2]);
  if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
  dir.normalize();
  const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), dir);
  if (right.lengthSq() < 1e-9) right.set(1, 0, 0);
  right.normalize();
  const lookAt = Array.isArray(spec.lookAt) ? spec.lookAt : null;
  // A lens under a ground surface (terrain overhead) is lifted to eye level above it first —
  // no backward retreat clears that, and the plan's camera is nobody else's to move.
  const before = nearGeometry(scene, makeCam(spec), THREE, undefined, lookAt);
  let lift = 0;
  let underBefore = '';
  if (before.ground_above_m !== null) { lift = before.ground_above_m + EYE_ABOVE_GROUND_M; underBefore = before.ground_above_name; }
  // A lens staring at a surface (loop 22's crypt: 9 of 9 rays on a well 0.75 m away, a black
  // frame for a round) or whose line of sight to its subject is cut (loop 22's ski station: a
  // snow bank at 44 % of the way, three rounds of "the view is blocked" while no refine session
  // could move the plan's camera) is searched out of it the same way.
  const blockedBefore = before.near_rays >= REPAIR_BLOCKED_RAYS;
  const cutBefore = before.target_hit_m !== null && before.target_distance_m
                    && before.target_hit_m < REPAIR_CUT_FRAC * before.target_distance_m ? before.target_hit_name : '';
  for (const [back, up, side] of REPAIR_OFFSETS) {
    const pos = [spec.position[0] + dir.x * back + right.x * side, spec.position[1] + dir.y * back + up + lift,
                 spec.position[2] + dir.z * back + right.z * side];
    const candidate = { ...spec, position: pos };
    const authored = lift === 0 && back === 0 && up === 0 && side === 0;
    const n = authored ? before : nearGeometry(scene, makeCam(candidate), THREE, undefined, lookAt);
    if (lensClear(n)) {
      if (authored) return null;   // the authored camera is fine: no repair
      return { spec: candidate, name: spec.name || '', moved_back_m: back, moved_up_m: +(up + lift).toFixed(3),
               moved_side_m: side, nearest_before: before.nearest_hit_m, inside_before: before.inside_mesh_bbox,
               under_before: underBefore, blocked_before: blockedBefore, cut_before: cutBefore,
               nearest_after: n.nearest_hit_m };
    }
  }
  return null;   // nothing within the retreat budget clears it: keep the authored shot
}
