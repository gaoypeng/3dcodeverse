// Browser-side object render rig: load a GLB, apply mode/isolate/explode, fit the
// camera per view, return PNGs as base64.  Driven by render_glb.mjs via puppeteer.
//
//   import { renderGlbViews } from '/__runtime/lib/browser/render_rig.js';
//   const result = await renderGlbViews({ glbUrl, views, mode, width, height, isolate, explode, background, animTime, shadow, fill });

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { fitCameraToBox, viewDirection } from './camera_fit.js';
import { makeRenderer, buildStudio, applyMode, aimStudio, rendererString, RIG_VERSION } from './studio.js';

const FOV_DEG = 35;
/** Views steeper than this fit themselves (a plan view of a tall object needs a very
 *  different distance); everything in the orbit band shares one distance so the object
 *  keeps the same apparent size across the montage. */
const UNIFORM_FRAMING_MAX_ELEVATION = 60;
/** ...but never pull a view back by more than this, or a deep object shrinks every other
 *  frame to the size its widest side needs and the judge loses detail resolution. */
const UNIFORM_FRAMING_MAX_PULLBACK = 1.10;

function loadGlb(url) {
  return new Promise((resolve, reject) => {
    new GLTFLoader().load(url, resolve, undefined, (e) => reject(new Error(`GLTFLoader failed for ${url}: ${e && e.message ? e.message : e}`)));
  });
}

/** The object root: the single child of the glTF scene if there is one, else the scene itself. */
function objectRoot(scene) {
  const kids = scene.children.filter((c) => !c.isLight && !c.isCamera);
  return kids.length === 1 && kids[0].children.length > 0 ? kids[0] : scene;
}

function norm(name) {
  return String(name || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** Hide every mesh that is not (under) a node whose name is in `names`. Returns missing names. */
function isolate(root, names) {
  const wanted = new Map(names.map((n) => [norm(n), n]));
  const found = new Set();
  const keep = new Set();
  root.traverse((o) => {
    if (o.name && wanted.has(norm(o.name))) {
      found.add(norm(o.name));
      o.traverse((d) => keep.add(d));
    }
  });
  root.traverse((o) => {
    if ((o.isMesh || o.isInstancedMesh || o.isSkinnedMesh) && !keep.has(o)) o.visible = false;
  });
  return [...wanted.entries()].filter(([k]) => !found.has(k)).map(([, n]) => n);
}

/** Push each part (direct child of the object root) radially from the object centre. */
function explode(root, factor) {
  root.updateWorldMatrix(true, true);
  const whole = new THREE.Box3().setFromObject(root, true);
  const center = whole.getCenter(new THREE.Vector3());
  // linear part of root's inverse world matrix: world-space delta -> root-local delta
  const toLocal = new THREE.Matrix3().setFromMatrix4(root.matrixWorld.clone().invert());
  for (const part of root.children) {
    const box = new THREE.Box3().setFromObject(part, true);
    if (box.isEmpty()) continue;
    const delta = box.getCenter(new THREE.Vector3()).sub(center).multiplyScalar(factor);
    part.position.add(delta.applyMatrix3(toLocal));
  }
  root.updateWorldMatrix(true, true);
}

function visibleBox(root) {
  root.updateWorldMatrix(true, true);
  const box = new THREE.Box3();
  root.traverseVisible((o) => {
    if ((o.isMesh || o.isInstancedMesh || o.isSkinnedMesh) && o.name !== '__wire') {
      box.expandByObject(o, true);
    }
  });
  return box;
}

/** The largest self-fit distance over the orbit-band views (null when there are none). */
function uniformDistance(camera, box, views, fill) {
  let best = null;
  for (const v of views) {
    if (Math.abs(v.elevation) > UNIFORM_FRAMING_MAX_ELEVATION) continue;
    const d = fitCameraToBox(camera, box, v.azimuth, v.elevation, { fill }).distance;
    best = best == null ? d : Math.max(best, d);
  }
  return best;
}

/** Re-place an already-fitted camera at an explicit distance along (azimuth, elevation). */
function placeAt(camera, box, azimuth, elevation, distance) {
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
  camera.position.copy(center).addScaledVector(viewDirection(azimuth, elevation), distance);
  camera.lookAt(center);
  camera.near = Math.max(distance * 0.01, 1e-4);
  camera.far = distance * 10 + radius * 4;
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return { position: camera.position.toArray(), lookAt: center.toArray(), distance };
}

/**
 * Render all views.  Resolves to
 * {ok, renderer, views:[{name, b64, camera_position, look_at, fov, width, height}], warnings, timing}
 */
export async function renderGlbViews(cfg) {
  const t0 = performance.now();
  const warnings = [];
  const width = cfg.width || 768;
  const height = cfg.height || 768;
  const mode = cfg.mode || 'shaded';
  const background = mode === 'silhouette' ? 'white' : cfg.background || 'studio';

  const canvas = document.getElementById('c') || document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const renderer = makeRenderer(canvas, width, height, { transparent: background === 'transparent' });
  if (mode === 'silhouette' || mode === 'normals') renderer.toneMapping = THREE.NoToneMapping;

  const gltf = await loadGlb(cfg.glbUrl);
  const tLoad = performance.now();
  const scene = new THREE.Scene();
  scene.add(gltf.scene);
  const root = objectRoot(gltf.scene);

  if (cfg.animTime != null && gltf.animations && gltf.animations.length) {
    const mixer = new THREE.AnimationMixer(gltf.scene);
    for (const clip of gltf.animations) mixer.clipAction(clip).play();
    mixer.update(Number(cfg.animTime));
  }
  if (cfg.isolate && cfg.isolate.length) {
    const missing = isolate(root, cfg.isolate);
    if (missing.length) warnings.push(`isolate: no node named ${missing.join(', ')}`);
  }
  if (cfg.explode) explode(root, Number(cfg.explode));

  const look = applyMode(root, mode);
  const box = visibleBox(root);
  if (box.isEmpty()) throw new Error('nothing visible to render (empty GLB or isolate hid everything)');
  const rig = buildStudio(renderer, scene, box, { background, shadow: look.shadow && cfg.shadow !== false, lights: look.lights });

  const camera = new THREE.PerspectiveCamera(FOV_DEG, width / height, 0.01, 100);
  const fill = cfg.fill || 0.90;
  const uniform = uniformDistance(camera, box, cfg.views, fill);
  const views = [];
  for (const v of cfg.views) {
    let fit = fitCameraToBox(camera, box, v.azimuth, v.elevation, { fill });
    if (uniform != null && Math.abs(v.elevation) <= UNIFORM_FRAMING_MAX_ELEVATION && uniform > fit.distance) {
      fit = placeAt(camera, box, v.azimuth, v.elevation,
                    Math.min(uniform, fit.distance * UNIFORM_FRAMING_MAX_PULLBACK));
    }
    aimStudio(rig, v.azimuth, v.elevation);
    renderer.render(scene, camera);
    const b64 = canvas.toDataURL('image/png').split(',')[1];
    views.push({ name: v.name, b64, camera_position: fit.position, look_at: fit.lookAt, fov: FOV_DEG, width, height, azimuth: v.azimuth, elevation: v.elevation });
  }
  const tEnd = performance.now();
  return {
    ok: true,
    renderer: rendererString(renderer),
    rig_version: RIG_VERSION,
    views,
    warnings,
    bbox: { min: box.min.toArray(), max: box.max.toArray() },
    timing: { load_ms: Math.round(tLoad - t0), render_ms: Math.round(tEnd - tLoad) },
  };
}
