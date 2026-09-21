// Procedural product-shot environment for the OBJECT rig (studio.js) — rig v2.
//
//   import { softboxEnvironment, backdropTexture, contactShadowTexture } from '/__runtime/lib/browser/studio_env.js';
//
// Everything here is generated from constants (no HDRI file, no randomness), so a
// render is byte-reproducible for a given GPU/driver — the render cache keys on the
// hash of these files (spatial/render.py::_rig_signature).
//
// Why it exists: rig v1 lit objects with `RoomEnvironment` + three white
// directionals.  A metal (metallicFactor 0.85-0.98 is what the agents actually
// author — measured over 44 bench GLBs) has nothing but a uniform grey box to
// reflect there, so chrome, cast iron and brass all resolve to the same matte
// grey and the judge writes "the chrome parts lack metallic reflections and
// appear as matte grey" (mus_easy_drum r0) or "uniform, untextured flat grey
// material, lacking the requested cast-iron appearance" (arch_hard_spiral_stair
// r1).  A metal only reads as metal when there is something *shaped* in the
// reflection: bright rectangular softboxes, a darker floor, a horizon between
// them.  That is exactly what this builds.

import * as THREE from 'three';

/** Vertical grey ramp sampled at 1/8th steps from the zenith (v=1) to the nadir (v=0). */
const DOME_STOPS = [
  [0.00, 0.16], [0.30, 0.20], [0.46, 0.26],
  [0.50, 0.40], [0.62, 0.50], [0.85, 0.58], [1.00, 0.62],
];

/** Softboxes: [w, h, x, y, z, intensity].  Positions are on a ~6-unit shell; PMREM
 *  only sees directions and angular sizes, so the absolute scale is arbitrary. */
const SOFTBOXES = [
  [5.0, 3.4, 2.6, 4.0, 3.0, 9.0],    // key — upper front-right, the big highlight
  [4.4, 2.6, -4.2, 1.6, 1.8, 2.0],   // fill — lower front-left, opens the shadow side
  [4.0, 2.2, -1.2, 3.4, -4.4, 4.5],  // rim — high behind, separates the object from the backdrop
  [7.0, 7.0, 0.0, -2.6, 0.0, 0.55],  // floor bounce (faces up)
];

function ramp(stops, t) {
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, v0] = stops[i - 1];
      const [t1, v1] = stops[i];
      const k = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
      return v0 + (v1 - v0) * k;
    }
  }
  return stops[stops.length - 1][1];
}

/** 1 x H DataTexture of the dome ramp, in sRGB bytes (v=0 at the bottom row). */
function domeTexture(height = 128) {
  const data = new Uint8Array(height * 4);
  for (let i = 0; i < height; i++) {
    const v = (i + 0.5) / height;
    const lin = ramp(DOME_STOPS, v);
    const b = Math.round(255 * Math.pow(Math.min(1, Math.max(0, lin)), 1 / 2.2));
    data.set([b, b, b, 255], i * 4);
  }
  const tex = new THREE.DataTexture(data, 1, height, THREE.RGBAFormat);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return tex;
}

function areaLightMaterial(intensity) {
  const m = new THREE.MeshBasicMaterial();
  m.color.setScalar(intensity);
  m.toneMapped = false;
  return m;
}

/**
 * The scene PMREMGenerator.fromScene() turns into `scene.environment`:
 * a gradient dome + four softboxes.  Caller disposes via `.dispose()` on the
 * returned object (three's PMREM keeps only the resulting texture).
 */
export function softboxEnvironment() {
  const scene = new THREE.Scene();
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(10, 24, 16),
    new THREE.MeshBasicMaterial({ map: domeTexture(), side: THREE.BackSide, toneMapped: false }),
  );
  dome.name = '__env_dome';
  scene.add(dome);
  for (const [w, h, x, y, z, intensity] of SOFTBOXES) {
    const p = new THREE.Mesh(new THREE.PlaneGeometry(w, h), areaLightMaterial(intensity));
    p.position.set(x, y, z);
    p.lookAt(0, 0, 0);
    scene.add(p);
  }
  return scene;
}

/**
 * Backdrop for `scene.background`: a neutral sweep — bright hotspot a little above
 * the frame centre falling off to a darker rim, i.e. what a lit paper cyclorama
 * looks like.  No hue, so it never tints a judge's read of the object's colour.
 *
 * The default sits BELOW mid-white (0.90 at the hotspot, 0.60 at the corners) on
 * purpose: rig v1's flat 0xe9e9ec (0.91 everywhere) left a white appliance with
 * nothing to separate against, and put 79 % of an average frame into one luminance
 * bucket — over the flat-frame threshold in spatial/frame_metrics.py.
 */
export function backdropTexture(size = 256, { centre = 0.90, edge = 0.60, hotspotY = 0.60 } = {}) {
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    // texture row 0 is the BOTTOM of the screen for a background texture
    const ny = (y + 0.5) / size;
    for (let x = 0; x < size; x++) {
      const nx = (x + 0.5) / size;
      const dx = (nx - 0.5) * 1.15;
      const dy = ny - hotspotY;
      const r = Math.min(1, Math.sqrt(dx * dx + dy * dy) / 0.78);
      const v = centre + (edge - centre) * (r * r * (3 - 2 * r));  // smoothstep falloff
      const b = Math.round(255 * Math.min(1, Math.max(0, v)));
      const i = (y * size + x) * 4;
      data.set([b, b, b, 255], i);
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Radial alpha blob used as the ambient contact shadow under the object.  A cast
 * shadow alone leaves an object looking pasted on when the key is high or the
 * view is from below; this is the soft occlusion darkening that says "it is
 * standing on something".
 */
export function contactShadowTexture(size = 128, { power = 2.4 } = {}) {
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = (x + 0.5) / size - 0.5;
      const dy = (y + 0.5) / size - 0.5;
      const r = Math.min(1, Math.sqrt(dx * dx + dy * dy) * 2);
      // three's `alphaMap` samples the GREEN channel, so the falloff lives in RGB
      const a = Math.round(255 * Math.pow(1 - r, power));
      const i = (y * size + x) * 4;
      data.set([a, a, a, 255], i);
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return tex;
}
