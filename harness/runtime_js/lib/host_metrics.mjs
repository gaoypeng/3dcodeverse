/**
 * Page-side frame/camera instruments: luminance statistics from a downscaled
 * readback of the render canvas and near-geometry tests for a camera.
 */

import { nonSolid } from './backdrop.mjs';

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
  let top = 0, bottom = 0;
  const hist = new Uint32Array(8);
  for (let i = 0; i < n; i++) {
    const r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    sum += lum; sumSq += lum * lum;
    if (lum < 10) dark += 1;
    if (r > 250 && g > 250 && b > 250) blown += 1;
    hist[Math.min(7, Math.floor(lum / 32))] += 1;
    if (i < n / 2) top += lum; else bottom += lum;
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
    top_bottom_ratio: +((top + 1) / (bottom + 1)).toFixed(3),
  };
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

/**
 * Near-geometry test: centre + 3x3 grid rays from the camera; nearest hit
 * distance, plus meshes whose own volume contains the eye (closed rooms,
 * back-face culled interiors — `eyeInsideMesh`).
 */
export function nearGeometry(scene, camera, THREE, limitM = 0.3) {
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
  let hits = 0;
  for (const [x, y] of pts) {
    rc.setFromCamera(new THREE.Vector2(x, y), camera);
    let hit = null;
    try { hit = rc.intersectObjects(targets, false)[0]; } catch (e) { hit = null; }
    if (hit) {
      hits += 1;
      if (hit.distance < nearest) { nearest = hit.distance; nearestName = hit.object.name || hit.object.parent?.name || hit.object.type; }
    }
  }
  const inside = [];
  const eye = camera.position;
  const box = new THREE.Box3();
  for (const o of targets) {
    if (o.isInstancedMesh) continue;
    if (!o.geometry) continue;
    if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
    box.copy(o.geometry.boundingBox).applyMatrix4(o.matrixWorld);
    const s = box.getSize(new THREE.Vector3());
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
  const center = new THREE.Vector3();
  camera.getWorldDirection(center);
  return {
    nearest_hit_m: Number.isFinite(nearest) ? +nearest.toFixed(3) : null,
    nearest_hit_name: nearestName,
    rays_hit: hits,
    rays_total: pts.length,
    inside_mesh_bbox: inside.slice(0, 5),
    camera_in_geometry: (Number.isFinite(nearest) && nearest < limitM) || inside.length > 0,
    eye_height_m: +eye.y.toFixed(3),
    ground_below_m: groundBelow,
    ground_below_name: groundBelowName,
    look_dir: [center.x, center.y, center.z].map((v) => +v.toFixed(3)),
  };
}

/**
 * Deterministic camera repair, added 2026-08-30; ON by default since 3f463ce
 * (drivers pass --camera-repair; python's probe_env_args() disables it only when
 * CV3D_CAMERA_REPAIR says false/off/no/0).
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
const REPAIR_OFFSETS = [
  [0, 0], [0, 0.5], [0.5, 0], [0.5, 0.5], [1, 0.5], [1, 1], [2, 1], [2, 2], [3, 2], [4, 2],
];
const REPAIR_CLEAR_M = 0.5;

export function repairCameraSpec(scene, spec, THREE, makeCam) {
  const dir = new THREE.Vector3(
    spec.position[0] - spec.lookAt[0], spec.position[1] - spec.lookAt[1], spec.position[2] - spec.lookAt[2]);
  if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
  dir.normalize();
  let before = null;
  for (const [back, up] of REPAIR_OFFSETS) {
    const pos = [spec.position[0] + dir.x * back, spec.position[1] + dir.y * back + up, spec.position[2] + dir.z * back];
    const candidate = { ...spec, position: pos };
    const n = nearGeometry(scene, makeCam(candidate), THREE);
    if (before === null) before = n;
    const clear = !n.camera_in_geometry && (n.nearest_hit_m === null || n.nearest_hit_m >= REPAIR_CLEAR_M);
    if (clear) {
      if (back === 0 && up === 0) return null;   // the authored camera is fine: no repair
      return { spec: candidate, name: spec.name || '', moved_back_m: back, moved_up_m: up,
               nearest_before: before.nearest_hit_m, inside_before: before.inside_mesh_bbox,
               nearest_after: n.nearest_hit_m };
    }
  }
  return null;   // nothing within the retreat budget clears it: keep the authored shot
}
