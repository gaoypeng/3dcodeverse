/**
 * Overview orbit rig fitted to the CONTENT of a scene (pure).
 *
 * `framingBox(census, bounds)` picks what to frame: the union of the census's
 * content groups (sky dome and ground plane are never content), dropping any
 * group that sprawls past the plan bounds (scatter across the whole ground) and
 * clamping the result to the bounds — never the ground plane, never the sky.
 * `fitOrbitCameras(bbox, views, opts)` then fits each view so the box's eight
 * corners fill the frame (exact per-corner frustum fit with a margin, not a
 * bounding-sphere fit).  Scenes sit on ground, so the rig is ground-aware: eyes
 * never go below ground, low "eye level" views stand just outside the footprint
 * ~1.6 m above ground and look at the content centre.  Azimuth 0 = front (+Z),
 * CCW from above; elevation in degrees above the horizon (conventions.py).
 */

const DEG = Math.PI / 180;
const BOUNDS_SPRAWL = 1.1;   // a content group wider than 110 % of the plan bounds is scatter, not a zone
const BOUNDS_CLAMP = 1.1;    // framing box is clamped to the plan bounds grown by 10 %

/** Unit direction from azimuth/elevation (Y-up, +Z front). */
export function orbitDirection(azimuthDeg, elevationDeg) {
  const az = azimuthDeg * DEG, el = elevationDeg * DEG;
  return [Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el)];
}

function unionBox(boxes) {
  if (!boxes.length) return null;
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (const b of boxes) for (let i = 0; i < 3; i++) { min[i] = Math.min(min[i], b.min[i]); max[i] = Math.max(max[i], b.max[i]); }
  return { min, max, size: max.map((v, i) => v - min[i]) };
}

function span(box) {
  return Math.max(box.max[0] - box.min[0], box.max[2] - box.min[2]);
}

/**
 * The box the overview rig should frame.
 * @param {object|null} census   host census (`groups[]`, `content_bbox`, `bbox`)
 * @param {{min:number[],max:number[]}|null} bounds  plan bounds (optional)
 */
export function framingBox(census, bounds = null) {
  if (!census) return null;
  let groups = (census.groups || []).filter((g) => g.kind === 'content' && g.bbox && g.bbox.min && g.bbox.max);
  if (bounds && bounds.min && bounds.max && groups.length > 1) {
    const limit = span(bounds) * BOUNDS_SPRAWL;
    const kept = groups.filter((g) => span(g.bbox) <= limit);
    if (kept.length) groups = kept;
  }
  let box = unionBox(groups.map((g) => g.bbox)) || census.content_bbox || census.bbox || null;
  if (box && bounds && bounds.min && bounds.max) {
    const c = bounds.min.map((v, i) => (v + bounds.max[i]) / 2);
    const h = bounds.max.map((v, i) => ((v - bounds.min[i]) / 2) * BOUNDS_CLAMP);
    const min = box.min.map((v, i) => Math.max(v, c[i] - h[i]));
    const max = box.max.map((v, i) => Math.min(v, c[i] + h[i]));
    if (min.every((v, i) => v < max[i])) box = { min, max, size: max.map((v, i) => v - min[i]) };
  }
  return box;
}

function corners(bbox) {
  const out = [];
  for (const x of [bbox.min[0], bbox.max[0]]) for (const y of [bbox.min[1], bbox.max[1]]) for (const z of [bbox.min[2], bbox.max[2]]) out.push([x, y, z]);
  return out;
}

/**
 * Smallest distance along `dir` (unit, from `center`) at which every corner sits
 * inside a camera looking back at `center` with the given half-angle tangents.
 *
 * THE distance fit for both rigs: the scene orbit (this module) and the object
 * rig (`browser/camera_fit.js`, which passes the screen-up vector it gives the
 * THREE camera).  Exact per-corner solution — the projected half-extent of a
 * corner is `(offAxis * margin) / (tan · (dist - depthOffset))`, so requiring
 * ≤ 1 on both axes for all eight corners is one max, no iteration.
 * @param {number[]|null} upHint  screen-up used for the roll (default: +Y, or
 *   -Z for an exactly vertical view — what THREE's lookAt does)
 */
export function fitDistance(bbox, center, dir, tanH, tanV, margin, upHint = null) {
  // same roll as THREE's lookAt (world up = +Y unless the view is exactly vertical)
  const up0 = upHint || (Math.abs(dir[1]) > 0.9999 ? [0, 0, -1] : [0, 1, 0]);
  const f = dir.map((v) => -v);   // forward; right = forward × up0; up = right × forward
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const norm = (v) => { const l = Math.hypot(...v) || 1; return v.map((x) => x / l); };
  const right = norm(cross(f, up0));
  const up = norm(cross(right, f));
  let dist = 0;
  for (const c of corners(bbox)) {
    const rel = c.map((v, i) => v - center[i]);
    const depthOffset = rel[0] * dir[0] + rel[1] * dir[1] + rel[2] * dir[2];   // + toward the eye
    const x = Math.abs(rel[0] * right[0] + rel[1] * right[1] + rel[2] * right[2]);
    const y = Math.abs(rel[0] * up[0] + rel[1] * up[1] + rel[2] * up[2]);
    dist = Math.max(dist, depthOffset + (x * margin) / tanH, depthOffset + (y * margin) / tanV);
  }
  return dist;
}

function boxGeom(bbox, groundY) {
  const size = bbox.max.map((v, i) => v - bbox.min[i]);
  const center = bbox.min.map((v, i) => v + size[i] / 2);
  const floor = groundY === null || groundY === undefined ? bbox.min[1] : groundY;
  return { size, center, floor };
}

function round3(spec) {
  spec.position = spec.position.map((x) => +x.toFixed(3));
  spec.lookAt = spec.lookAt.map((x) => +x.toFixed(3));
  return spec;
}

/**
 * Eye-level camera standing just outside the bbox footprint on the given
 * azimuth side, ~1.6 m above ground, looking into the content.  THE owner of
 * the zone-camera fit (assemble.py consumes it through probe_scene.mjs).
 * @returns {{position:number[], lookAt:number[], fov:number}}
 */
export function fitZoneCamera(bbox, { azimuth = 45, floor = null, fov = 50 } = {}) {
  const g = boxGeom(bbox, floor);
  const [dx, , dz] = orbitDirection(azimuth, 0);
  // distance from the centre to the bbox's xz boundary along (dx, dz)
  const hx = g.size[0] / 2, hz = g.size[2] / 2;
  const tx = Math.abs(dx) > 1e-6 ? hx / Math.abs(dx) : Infinity;
  const tz = Math.abs(dz) > 1e-6 ? hz / Math.abs(dz) : Infinity;
  const back = Math.min(tx, tz) + Math.max(2, 0.12 * Math.max(g.size[0], g.size[2]));
  const eyeH = Math.max(g.floor + 0.5, g.floor + 1.6 + Math.min(4, 0.1 * g.size[1]));
  return round3({
    position: [g.center[0] + dx * back, eyeH, g.center[2] + dz * back],
    lookAt: [g.center[0], Math.min(g.center[1], g.floor + 0.35 * g.size[1] + 0.5), g.center[2]],
    fov,
  });
}

/**
 * Overview camera on the given azimuth side, fitted so the bbox's eight
 * corners fill the frame (exact per-corner frustum fit, ground-aware — never
 * a bounding-sphere fit).  THE owner of the overview fit.
 * @returns {{position:number[], lookAt:number[], fov:number}}
 */
export function fitOverviewCamera(bbox, { azimuth = 45, elevation = 30, fov = 50, aspect = 16 / 9, groundY = null, margin = 1.05 } = {}) {
  const g = boxGeom(bbox, groundY);
  const el = Math.max(3, Math.min(89, elevation));
  const d = orbitDirection(azimuth, el);
  const tanV = Math.tan((fov * DEG) / 2);
  const tanH = tanV * aspect;
  const minDist = Math.max(1.5, 0.35 * Math.hypot(g.size[0], g.size[1], g.size[2]));
  const dist = Math.max(minDist, fitDistance(bbox, g.center, d, tanH, tanV, margin));
  const eye = g.center.map((c, i) => c + d[i] * dist);
  if (eye[1] < g.floor + 0.5) eye[1] = g.floor + 0.5;
  return round3({ position: eye, lookAt: g.center.slice(), fov });
}

/**
 * Build camera specs for `views` around `bbox` ({min:[..], max:[..]}):
 * fitOverviewCamera above 20°, fitZoneCamera at eye level.
 * @returns {Array<{name, position, lookAt, fov, kind:'orbit'}>}
 */
export function fitOrbitCameras(bbox, views, { fov = 50, aspect = 16 / 9, groundY = null, margin = 1.05, noFog = true } = {}) {
  if (!bbox || !bbox.min || !bbox.max) return [];
  const out = [];
  for (const v of views) {
    const el = Math.max(3, Math.min(89, v.elevation));
    const fit = el <= 20
      ? fitZoneCamera(bbox, { azimuth: v.azimuth, floor: groundY, fov })
      : fitOverviewCamera(bbox, { azimuth: v.azimuth, elevation: el, fov, aspect, groundY, margin });
    out.push({ name: v.name, ...fit, kind: 'orbit', noFog: !!noFog && el > 20 });
  }
  return out;
}
