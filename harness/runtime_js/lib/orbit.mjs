/**
 * Overview orbit rig fitted to a census bbox (pure).  Scenes sit on ground,
 * so the rig is ground-aware: eyes never go below ground, low "eye level"
 * views are lifted to ~1.6 m above ground.  Azimuth 0 = front (+Z), CCW from
 * above; elevation in degrees above the horizon (see codeverse/conventions.py).
 */

const DEG = Math.PI / 180;

/** Unit direction from azimuth/elevation (Y-up, +Z front). */
export function orbitDirection(azimuthDeg, elevationDeg) {
  const az = azimuthDeg * DEG, el = elevationDeg * DEG;
  return [Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el)];
}

/**
 * Build camera specs for `views` around `bbox` ({min:[..], max:[..]}).
 * @returns {Array<{name, position, lookAt, fov, kind:'orbit'}>}
 */
export function fitOrbitCameras(bbox, views, { fov = 50, aspect = 16 / 9, groundY = null, margin = 1.15, noFog = true } = {}) {
  if (!bbox || !bbox.min || !bbox.max) return [];
  const size = bbox.max.map((v, i) => v - bbox.min[i]);
  const center = bbox.min.map((v, i) => v + size[i] / 2);
  const radius = Math.max(0.5, Math.hypot(size[0], size[1], size[2]) / 2);
  const fovV = fov * DEG;
  const fovH = 2 * Math.atan(Math.tan(fovV / 2) * aspect);
  const dist = (radius / Math.sin(Math.min(fovV, fovH) / 2)) * margin;
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
      eye = center.map((c, i) => c + d[i] * dist);
      lookAt = center.slice();
    }
    if (eye[1] < floor + 0.5) eye[1] = floor + 0.5;
    out.push({ name: v.name, position: eye.map((x) => +x.toFixed(3)), lookAt: lookAt.map((x) => +x.toFixed(3)), fov, kind: 'orbit', noFog: !!noFog && el > 20 });
  }
  return out;
}
