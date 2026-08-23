// Neutral studio look for object renders: RoomEnvironment PMREM + key/fill/rim
// directionals, ACES tone mapping, sRGB output, optional shadow catcher.
//
//   import { makeRenderer, buildStudio, applyMode } from '/__runtime/lib/browser/studio.js';

import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export const BACKGROUNDS = { studio: 0xe9e9ec, white: 0xffffff, transparent: null };

/** WebGLRenderer on `canvas` sized width x height (device pixel ratio 1). */
export function makeRenderer(canvas, width, height, { transparent = false } = {}) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: transparent, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  return renderer;
}

/** UNMASKED_RENDERER_WEBGL string (or the masked one). */
export function rendererString(renderer) {
  const gl = renderer.getContext();
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  return String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
}

/**
 * Add environment + lights (+ shadow catcher) to `scene`, scaled to `box`.
 * Returns the shadow-catcher mesh (or null) so callers can exclude it from framing.
 */
export function buildStudio(renderer, scene, box, { background = 'studio', shadow = true, lights = true } = {}) {
  const bg = BACKGROUNDS[background];
  if (bg === undefined) throw new Error(`unknown background '${background}' (studio|white|transparent)`);
  if (bg === null) renderer.setClearColor(0x000000, 0);
  else renderer.setClearColor(bg, 1);

  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);

  if (lights) {
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environmentIntensity = 0.8;

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.copy(center).add(new THREE.Vector3(1.0, 1.6, 1.3).normalize().multiplyScalar(radius * 4));
    key.target.position.copy(center);
    key.castShadow = shadow;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0005;
    key.shadow.normalBias = radius * 0.01;
    const cam = key.shadow.camera;
    cam.left = cam.bottom = -radius * 1.6;
    cam.right = cam.top = radius * 1.6;
    cam.near = radius * 0.5;
    cam.far = radius * 8;
    scene.add(key, key.target);

    const fill = new THREE.DirectionalLight(0xffffff, 0.8);
    fill.position.copy(center).add(new THREE.Vector3(-1.2, 0.8, 0.6).normalize().multiplyScalar(radius * 4));
    fill.target.position.copy(center);
    scene.add(fill, fill.target);

    const rim = new THREE.DirectionalLight(0xffffff, 1.0);
    rim.position.copy(center).add(new THREE.Vector3(-0.5, 1.2, -1.4).normalize().multiplyScalar(radius * 4));
    rim.target.position.copy(center);
    scene.add(rim, rim.target);
  }

  let catcher = null;
  if (shadow && lights) {
    const geo = new THREE.PlaneGeometry(radius * 20, radius * 20);
    const mat = new THREE.ShadowMaterial({ opacity: 0.22 });
    catcher = new THREE.Mesh(geo, mat);
    catcher.name = '__shadow_catcher';
    catcher.rotation.x = -Math.PI / 2;
    catcher.position.set(center.x, box.min.y - radius * 1e-3, center.z);
    catcher.receiveShadow = true;
    scene.add(catcher);
  }
  return catcher;
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
