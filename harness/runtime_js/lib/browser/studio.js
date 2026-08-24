// Product-shot studio look for object renders (RIG v2): a procedural softbox
// environment through PMREM (studio_env.js), a swept neutral backdrop, one
// shadow-casting key + a soft ambient contact shadow, and the shared renderer
// factory (renderer.js: ACES tone mapping, sRGB output, PCF shadows).
//
//   import { makeRenderer, buildStudio, applyMode, RIG_VERSION } from '/__runtime/lib/browser/studio.js';
//
// RIG v1 (up to 2026-08-23) was `RoomEnvironment` + three white directionals on a
// flat #e9e9ec clear colour.  It lit geometry fine but told the judge nothing
// about materials: with only a uniform grey box to reflect, metallicFactor 0.95
// chrome and metallicFactor 0.85 cast iron both resolve to the same mid grey.
// v2 changes the *environment*, not the object — see studio_env.js for the why,
// and docs/EVAL.md for the measured score delta that switching rigs is worth
// (it is large; scores across the rig change are not comparable).

import * as THREE from 'three';
import { makeRenderer, rendererString } from './renderer.js';
import { backdropTexture, contactShadowTexture, softboxEnvironment } from './studio_env.js';

// the renderer factory lives in renderer.js (shared with the scene host); it is
// re-exported here because studio.js is the object rig's one-stop import
export { makeRenderer, rendererString };

/** Bumped whenever the look changes.  Recorded in views.json so a sheet can be dated. */
export const RIG_VERSION = 2;

export const BACKGROUNDS = { studio: 0xe9e9ec, white: 0xffffff, transparent: null };

/** Tone-mapping exposure per background.  MEASURED over 12 recorded artifacts x 8 views
 *  (96 frames), on the 96x54 sampling grid `lib/host_metrics.mjs` uses, plus object-only
 *  statistics from an alpha-matted render of the same 96 frames:
 *
 *    rig          frame mean_lum  frame modal_frac  object lum std  object p95-p05
 *    v1 (flat bg)     0.865            0.789            0.135           0.406
 *    v2 (sweep)       0.702            0.516            0.131           0.400
 *
 *  v2 is DARKER, not brighter, and the object's own shading range is unchanged: the
 *  difference is contrast STRUCTURE (a swept backdrop, a shaped environment), not
 *  exposure.  25 of the 96 v1 frames were over the flat-frame threshold in
 *  spatial/frame_metrics.py (modal_frac > 0.85); 0 of the v2 frames are, and neither rig
 *  comes near the dark (mean_lum < 0.12) or blown (> 20 % pure white) limits.
 *  Cost (median of 4 runs x 3 objects, warm browser): 8 views at 768 px = 97 ms/view wall
 *  (v1: 117) and 60 ms/view page-side (v1: 61) — the VSM blur spends what the two fewer
 *  lights save, and the driver round trip got cheaper. */
const EXPOSURE = { studio: 1.0, white: 1.05, transparent: 1.0 };

// The environment gives metals something shaped to reflect; the DIRECTIONALS are what
// separate one flat panel of a box-shaped object from the next.  Measured on 96 frames:
// dropping the directional total from v1's 4.0 to 1.8 flattened panelled objects (an
// espresso machine's steel sides stopped reading as three different planes), so the key
// is back up and a low fill returns — the object's shading range now matches v1's while
// the reflections are v2's.
const ENV_INTENSITY = 1.0;
const KEY_INTENSITY = 2.0;
const FILL_INTENSITY = 0.5;
const RIM_INTENSITY = 0.5;
/** Key/rim placement relative to the CAMERA azimuth (deg from the view direction) and
 *  their elevations.  Camera-relative is what a turntable product rig does: the key
 *  stays over the photographer's left shoulder, so the cast shadow always falls away
 *  from the lens instead of sprawling across the backdrop on the rear views — rig v1's
 *  world-fixed key put a metre-long grey slab through the `back`, `left` and `top`
 *  frames of every object. */
const KEY_YAW_DEG = 38;
const KEY_ELEVATION_DEG = 52;
const FILL_YAW_DEG = -58;
const FILL_ELEVATION_DEG = 12;
const RIM_YAW_DEG = -152;
const RIM_ELEVATION_DEG = 34;
/** Cast-shadow darkness and the ambient contact blob under the object.  The cast
 *  shadow is soft (VSM, see buildStudio): a hard-edged slab under a product shot
 *  reads as a rendering mistake, not as light. */
const CAST_SHADOW_OPACITY = 0.24;
const CONTACT_OPACITY = 0.34;
const CONTACT_SPREAD = 1.9;

let _envTexture = null;   // PMREM output is reusable for the whole page

function environmentTexture(renderer) {
  if (_envTexture) return _envTexture;
  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  const envScene = softboxEnvironment();
  _envTexture = pmrem.fromScene(envScene, 0.02).texture;
  envScene.traverse((o) => {
    if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); }
    if (o.geometry) o.geometry.dispose();
  });
  pmrem.dispose();
  return _envTexture;
}

/** Test seam: drop the cached PMREM texture (a fresh renderer needs a fresh env). */
export function resetStudioCache() {
  if (_envTexture) _envTexture.dispose();
  _envTexture = null;
}

function addContactShadow(scene, center, box, radius) {
  const size = box.getSize(new THREE.Vector3());
  const spread = Math.max(size.x, size.z, radius * 0.5) * CONTACT_SPREAD;
  const mat = new THREE.MeshBasicMaterial({
    color: 0x000000, transparent: true, opacity: CONTACT_OPACITY,
    alphaMap: contactShadowTexture(), depthWrite: false, toneMapped: false,
  });
  const blob = new THREE.Mesh(new THREE.PlaneGeometry(spread, spread), mat);
  blob.name = '__contact_shadow';
  blob.rotation.x = -Math.PI / 2;
  blob.position.set(center.x, box.min.y + radius * 2e-3, center.z);
  blob.renderOrder = -1;
  scene.add(blob);
  return blob;
}

const DEG = Math.PI / 180;

/** Unit vector towards a light placed at (yaw from +Z, elevation), both degrees. */
function lightDirection(yawDeg, elevationDeg) {
  const a = yawDeg * DEG;
  const e = elevationDeg * DEG;
  return new THREE.Vector3(Math.sin(a) * Math.cos(e), Math.sin(e), Math.cos(a) * Math.cos(e));
}

/**
 * Point the rig at a view: key/rim yaw with the camera azimuth and the environment
 * spins with them, so every frame is the same product shot from a different side.
 * `azimuthDeg` follows the harness convention (0 = camera on +Z = object front);
 * `elevationDeg` only raises the key for steep (plan / underside) views.
 */
export function aimStudio(rig, azimuthDeg, elevationDeg = 0) {
  if (!rig || !rig.key) return;
  const { center, radius, key, fill, rim, scene } = rig;
  const d = radius * 4;
  // a plan view needs the key nearly overhead, or the shadow sprawls across the frame
  const keyEl = Math.min(80, Math.max(KEY_ELEVATION_DEG, Math.abs(elevationDeg) * 0.85));
  key.position.copy(center).addScaledVector(lightDirection(azimuthDeg + KEY_YAW_DEG, keyEl), d);
  key.target.position.copy(center);
  key.target.updateMatrixWorld();
  for (const [light, yaw, el] of [[fill, FILL_YAW_DEG, FILL_ELEVATION_DEG], [rim, RIM_YAW_DEG, RIM_ELEVATION_DEG]]) {
    if (!light) continue;
    light.position.copy(center).addScaledVector(lightDirection(azimuthDeg + yaw, el), d);
    light.target.position.copy(center);
    light.target.updateMatrixWorld();
  }
  if (scene) scene.environmentRotation.set(0, azimuthDeg * DEG, 0);
}

/**
 * Add environment + backdrop + lights (+ shadow catcher) to `scene`, scaled to `box`.
 * Returns the rig handle `{catcher, key, fill, rim, blob, center, radius, scene}` — `catcher`
 * is the shadow plane callers exclude from framing, the rest is what `aimStudio` moves.
 */
export function buildStudio(renderer, scene, box, { background = 'studio', shadow = true, lights = true } = {}) {
  const bg = BACKGROUNDS[background];
  if (bg === undefined) throw new Error(`unknown background '${background}' (studio|white|transparent)`);
  renderer.toneMappingExposure = EXPOSURE[background] ?? 1.0;
  if (bg === null) {
    renderer.setClearColor(0x000000, 0);
  } else if (background === 'studio' && lights) {
    scene.background = backdropTexture();
    renderer.setClearColor(bg, 1);   // still the clear colour for any un-drawn pixel
  } else {
    renderer.setClearColor(bg, 1);
  }

  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
  const rig = { catcher: null, key: null, fill: null, rim: null, blob: null, center, radius, scene };

  if (lights) {
    scene.environment = environmentTexture(renderer);
    scene.environmentIntensity = ENV_INTENSITY;

    // ONE shadow-casting key, placed high and camera-relative (see aimStudio) so the
    // cast shadow stays under the object instead of the metre-long grey slab rig v1
    // threw across the backdrop.
    const key = new THREE.DirectionalLight(0xffffff, KEY_INTENSITY);
    key.castShadow = shadow;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = radius * 0.012;
    // VSM is the only three shadow map whose blur radius actually does anything
    // (PCFSoftShadowMap ignores `shadow.radius`), and a soft-edged contact shadow is
    // most of what separates a product shot from a viewport grab.  Object rig only —
    // renderer.js keeps PCF for the scene host.
    renderer.shadowMap.type = THREE.VSMShadowMap;
    key.shadow.radius = 5;
    key.shadow.blurSamples = 12;
    const cam = key.shadow.camera;
    cam.left = cam.bottom = -radius * 1.45;
    cam.right = cam.top = radius * 1.45;
    cam.near = radius * 0.5;
    cam.far = radius * 8;
    scene.add(key, key.target);

    // A low fill opens the shadow side; a back-rim keeps a dark object off the backdrop.
    const fill = new THREE.DirectionalLight(0xffffff, FILL_INTENSITY);
    const rim = new THREE.DirectionalLight(0xffffff, RIM_INTENSITY);
    scene.add(fill, fill.target, rim, rim.target);
    rig.key = key;
    rig.fill = fill;
    rig.rim = rim;
    aimStudio(rig, 0);
  }

  if (shadow && lights) {
    const geo = new THREE.CircleGeometry(radius * 6, 64);
    const mat = new THREE.ShadowMaterial({ opacity: CAST_SHADOW_OPACITY });
    const catcher = new THREE.Mesh(geo, mat);
    catcher.name = '__shadow_catcher';
    catcher.rotation.x = -Math.PI / 2;
    catcher.position.set(center.x, box.min.y - radius * 1e-3, center.z);
    catcher.receiveShadow = true;
    scene.add(catcher);
    rig.catcher = catcher;
    rig.blob = addContactShadow(scene, center, box, radius);
  }
  return rig;
}

const CLAY = new THREE.MeshStandardMaterial({ color: 0x9c9c9c, roughness: 0.85, metalness: 0.0 });
const NORMALS = new THREE.MeshNormalMaterial();
const SILHOUETTE = new THREE.MeshBasicMaterial({ color: 0x000000 });
const WIRE = new THREE.MeshBasicMaterial({ color: 0x1a1a1a, wireframe: true, transparent: true, opacity: 0.45, depthTest: true });

/**
 * Apply a render mode to every mesh under `root`:
 *  shaded     original materials
 *  clay       uniform grey MeshStandardMaterial (geometry stays readable)
 *  wire       clay + wireframe overlay
 *  normals    MeshNormalMaterial
 *  silhouette unlit black (caller uses a white background, no lights)
 * Returns {lights, shadow} hints for buildStudio.
 */
export function applyMode(root, mode) {
  const meshes = [];
  root.traverse((o) => {
    if (o.isMesh || o.isInstancedMesh || o.isSkinnedMesh) meshes.push(o);
  });
  for (const m of meshes) {
    m.castShadow = true;
    m.receiveShadow = true;
    if (mode === 'shaded') continue;
    if (mode === 'clay' || mode === 'wire') m.material = CLAY;
    else if (mode === 'normals') m.material = NORMALS;
    else if (mode === 'silhouette') m.material = SILHOUETTE;
    else throw new Error(`unknown mode '${mode}' (shaded|clay|wire|normals|silhouette)`);
  }
  if (mode === 'wire') {
    for (const m of meshes) {
      const w = new THREE.Mesh(m.geometry, WIRE);
      w.name = '__wire';
      m.add(w);
    }
  }
  const lit = mode === 'shaded' || mode === 'clay' || mode === 'wire';
  return { lights: lit, shadow: lit };
}
