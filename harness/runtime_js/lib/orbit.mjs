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
 */
export function fitDistance(bbox, center, dir, tanH, tanV, margin) {
  // same roll as THREE's lookAt (world up = +Y unless the view is exactly vertical)
  const up0 = Math.abs(dir[1]) > 0.9999 ? [0, 0, -1] : [0, 1, 0];
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

/**
 * Build camera specs for `views` around `bbox` ({min:[..], max:[..]}).
 * @returns {Array<{name, position, lookAt, fov, kind:'orbit'}>}
 */
export function fitOrbitCameras(bbox, views, { fov = 50, aspect = 16 / 9, groundY = null, margin = 1.05, noFog = true } = {}) {
  if (!bbox || !bbox.min || !bbox.max) return [];
  const size = bbox.max.map((v, i) => v - bbox.min[i]);
  const center = bbox.min.map((v, i) => v + size[i] / 2);
  const tanV = Math.tan((fov * DEG) / 2);
  const tanH = tanV * aspect;
  const minDist = Math.max(1.5, 0.35 * Math.hypot(size[0], size[1], size[2]));
  const floor = groundY === null || groundY === undefined ? bbox.min[1] : groundY;
  const out = [];
  const horizDist = (dx, dz) => {
    // distance from the centre to the bbox's xz boundary along (dx, dz)
    const hx = size[0] / 2, hz = size[2] / 2;
    const tx = Math.abs(dx) > 1e-6 ? hx / Math.abs(dx) : Infinity;
    const tz = Math.abs(dz) > 1e-6 ? hz / Math.abs(dz) : Infinity;
    return Math.min(tx, tz);
  };
  for (const v of views) {
    const el = Math.max(3, Math.min(89, v.elevation));
    const d = orbitDirection(v.azimuth, el);
    let eye, lookAt;
    if (el <= 20) {
      // eye-level: a person standing just outside the content footprint, looking in
      const dh = orbitDirection(v.azimuth, 0);
      const edge = horizDist(dh[0], dh[2]);
      const back = edge + Math.max(2, 0.12 * Math.max(size[0], size[2]));
      const eyeH = floor + 1.6 + Math.min(4, 0.1 * size[1]);
      eye = [center[0] + dh[0] * back, eyeH, center[2] + dh[2] * back];
      lookAt = [center[0], Math.min(center[1], floor + 0.35 * size[1] + 0.5), center[2]];
    } else {
      const dist = Math.max(minDist, fitDistance(bbox, center, d, tanH, tanV, margin));
      eye = center.map((c, i) => c + d[i] * dist);
      lookAt = center.slice();
    }
    if (eye[1] < floor + 0.5) eye[1] = floor + 0.5;
    out.push({ name: v.name, position: eye.map((x) => +x.toFixed(3)), lookAt: lookAt.map((x) => +x.toFixed(3)), fov, kind: 'orbit', noFog: !!noFog && el > 20 });
  }
  return out;
}
