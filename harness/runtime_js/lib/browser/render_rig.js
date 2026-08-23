// Browser-side object render rig: load a GLB, apply mode/isolate/explode, fit the
// camera per view, return PNGs as base64.  Driven by render_glb.mjs via puppeteer.
//
//   import { renderGlbViews } from '/__runtime/lib/browser/render_rig.js';
//   const result = await renderGlbViews({ glbUrl, views, mode, width, height, isolate, explode, background, animTime, shadow, fill });

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { fitCameraToBox } from './camera_fit.js';
import { makeRenderer, buildStudio, applyMode, rendererString } from './studio.js';

const FOV_DEG = 35;

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
  buildStudio(renderer, scene, box, { background, shadow: look.shadow && cfg.shadow !== false, lights: look.lights });

  const camera = new THREE.PerspectiveCamera(FOV_DEG, width / height, 0.01, 100);
  const views = [];
  for (const v of cfg.views) {
    const fit = fitCameraToBox(camera, box, v.azimuth, v.elevation, { fill: cfg.fill || 0.85 });
    renderer.render(scene, camera);
    const b64 = canvas.toDataURL('image/png').split(',')[1];
    views.push({ name: v.name, b64, camera_position: fit.position, look_at: fit.lookAt, fov: FOV_DEG, width, height, azimuth: v.azimuth, elevation: v.elevation });
  }
  const tEnd = performance.now();
  return {
    ok: true,
    renderer: rendererString(renderer),
    views,
    warnings,
    bbox: { min: box.min.toArray(), max: box.max.toArray() },
    timing: { load_ms: Math.round(tLoad - t0), render_ms: Math.round(tEnd - tLoad) },
  };
}
