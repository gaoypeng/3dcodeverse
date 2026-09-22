// Camera fitting for the OBJECT rig (browser ESM; imports 'three' through the page's
// import map).  The distance math itself is shared with the scene orbit rig —
// `fitDistance` in /__runtime/lib/orbit.mjs is the single owner (pure, no three).
//
//   import { viewDirection, fitCameraToBox } from '/__runtime/lib/browser/camera_fit.js';
//
// Convention (codeverse3d.conventions): azimuth 0 = front (+Z), counter-clockwise seen
// from above (90 = camera on +X = the object's right side); elevation above the horizon.

import * as THREE from 'three';
import { fitDistance, orbitDirection } from '../orbit.mjs';

const DEG = Math.PI / 180;

/** Unit vector from the target towards the camera for (azimuth, elevation) in degrees. */
export function viewDirection(azimuthDeg, elevationDeg) {
  return new THREE.Vector3(...orbitDirection(azimuthDeg, elevationDeg));
}

/** The 8 corners of a Box3. */
export function boxCorners(box) {
  const out = [];
  for (const x of [box.min.x, box.max.x])
    for (const y of [box.min.y, box.max.y])
      for (const z of [box.min.z, box.max.z]) out.push(new THREE.Vector3(x, y, z));
  return out;
}

/**
 * Screen-up for a view: near-vertical views make (0,1,0) ~collinear with the
 * view direction, which makes lookAt's roll unstable — pin up to -Z (front at
 * the bottom, like a plan drawing).
 */
function screenUp(elevationDeg) {
  if (Math.abs(elevationDeg) > 60) return [0, 0, elevationDeg > 0 ? -1 : 1];
  return [0, 1, 0];
}

/**
 * Place `camera` along (azimuth, elevation) from the box centre so that the
 * projected bbox corners fill `fill` of the half-frame (both axes).
 * Returns {position, lookAt, distance}.
 */
export function fitCameraToBox(camera, box, azimuthDeg, elevationDeg, { fill = 0.85, margin = 1.0 } = {}) {
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 1e-3) * margin;   // near/far scale only
  const dir = viewDirection(azimuthDeg, elevationDeg);
  const up = screenUp(elevationDeg);
  camera.up.set(up[0], up[1], up[2]);

  const tanV = Math.tan((camera.fov * DEG) / 2);
  const d = Math.max(
    1e-4,
    fitDistance(
      { min: box.min.toArray(), max: box.max.toArray() },
      center.toArray(), dir.toArray(), tanV * camera.aspect, tanV, 1 / fill, up,
    ),
  );
  camera.position.copy(center).addScaledVector(dir, d);
  camera.lookAt(center);
  camera.near = Math.max(d * 0.01, 1e-4);
  camera.far = d * 10 + radius * 4;
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return { position: camera.position.toArray(), lookAt: center.toArray(), distance: d };
}
