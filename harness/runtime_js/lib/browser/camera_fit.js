// Camera fitting shared by the object and scene renderers (browser ESM; imports 'three'
// through the page's import map).
//
//   import { viewDirection, fitCameraToBox } from '/__runtime/lib/browser/camera_fit.js';
//
// Convention (codeverse.conventions): azimuth 0 = front (+Z), counter-clockwise seen
// from above (90 = camera on +X = the object's right side); elevation above the horizon.

import * as THREE from 'three';

const DEG = Math.PI / 180;

/** Unit vector from the target towards the camera for (azimuth, elevation) in degrees. */
export function viewDirection(azimuthDeg, elevationDeg) {
  const az = azimuthDeg * DEG;
  const el = elevationDeg * DEG;
  return new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
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
 * Place `camera` along (azimuth, elevation) from the box centre so that the
 * projected bbox corners fill `fill` of the half-frame (both axes).  Projected
 * extent is ~1/distance, so a few multiplicative iterations converge.
 * Returns {position, lookAt, distance}.
 */
export function fitCameraToBox(camera, box, azimuthDeg, elevationDeg, { fill = 0.85, iterations = 8, margin = 1.0 } = {}) {
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 1e-3) * margin;
  const dir = viewDirection(azimuthDeg, elevationDeg);
  // Near-vertical views: (0,1,0) is ~collinear with the view direction which makes
  // lookAt's roll unstable; pin screen-up to -Z (front at the bottom, like a plan).
  if (Math.abs(elevationDeg) > 60) camera.up.set(0, 0, elevationDeg > 0 ? -1 : 1);
  else camera.up.set(0, 1, 0);

  const fovRad = camera.fov * DEG;
  let d = radius / Math.sin(fovRad / 2);
  const corners = boxCorners(box);
  for (let i = 0; i < iterations; i++) {
    camera.position.copy(center).addScaledVector(dir, d);
    camera.lookAt(center);
    camera.near = Math.max(d * 0.01, 1e-4);
    camera.far = d * 10 + radius * 4;
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld(true);
    let maxExt = 1e-6;
    for (const c of corners) {
      const ndc = c.clone().project(camera);
      maxExt = Math.max(maxExt, Math.abs(ndc.x), Math.abs(ndc.y));
    }
    const next = d * (maxExt / fill);
    if (Math.abs(next - d) < 1e-4 * d) {
      d = next;
      break;
    }
    d = next;
  }
  camera.position.copy(center).addScaledVector(dir, d);
  camera.lookAt(center);
  camera.near = Math.max(d * 0.01, 1e-4);
  camera.far = d * 10 + radius * 4;
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return { position: camera.position.toArray(), lookAt: center.toArray(), distance: d };
}
