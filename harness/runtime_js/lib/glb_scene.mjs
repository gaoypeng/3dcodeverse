/**
 * The scene module of an OFFLINE scene (scene_blender, DESIGN §4.2): the `createScene` the host
 * imports when a driver is given `--glb`.  The harness-written census GLB (languages/wrappers/
 * _census_glb.py) is served at CENSUS_GLB_URL; its root nodes are hoisted into the scene — env
 * objects by name, one group per zone — and the harness cameras come from the scene's
 * `extras.c3d_cameras` (GLB frame).  The GLB is the t = 0 pose, so `update` changes nothing:
 * motion is measured on the Blender frames.  Nothing here is the agent's code.
 */
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

export const CENSUS_GLB_URL = '/__census.glb';

export async function createScene() {
  const scene = new THREE.Scene();
  // its own loader, not the host's: the host's records every GLB a scene loads as a hero
  // (census `glb_assets`), and the census GLB is the whole scene, not a hero
  const gltf = await new GLTFLoader().loadAsync(CENSUS_GLB_URL);
  for (const child of [...gltf.scene.children]) scene.add(child);
  const cameras = (gltf.scene.userData && gltf.scene.userData.c3d_cameras) || [];
  return { scene, cameras, update() {} };
}
