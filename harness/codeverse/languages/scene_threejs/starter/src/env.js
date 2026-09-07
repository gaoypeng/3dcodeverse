// src/env.js — environment owned by the scene: ground, sky, sun, fog, heightAt.
//   export function buildEnv(ctx) → { ground, sky, sun, hemi, update(t, dt) }
//   export function heightAt(x, z) → ground height (m) so zones can seat objects.
import * as THREE from 'three';
import { makeSkyMaterial } from './shaders/sky.js';
import { sunRig } from './lib/environment.js';

export const SUN_AZIMUTH_DEG = 60;    // where the sun is (0 = +Z front, CCW from above); cameras on the sun side are front-lit
export const SUN_ELEVATION_DEG = 38;
export const GROUND_SIZE = 160;       // ground plane extent (m); fog is tuned to it
const FOG_NEAR = 60, FOG_FAR = 260;
const SKY_RADIUS = 600;

/** Gentle rolling meadow; deterministic, cheap (called per placed object). */
export function heightAt(x, z) {
  return 0.35 * Math.sin(x * 0.13) * Math.cos(z * 0.11) + 0.18 * Math.sin((x + z) * 0.27);
}

function groundGeometry(size, segs) {
  const g = new THREE.PlaneGeometry(size, size, segs, segs);
  g.rotateX(-Math.PI / 2);                       // PlaneGeometry lies in XY → rotate to XZ
  const pos = g.attributes.position;
  for (let i = 0; i < pos.count; i++) pos.setY(i, heightAt(pos.getX(i), pos.getZ(i)));
  g.computeVertexNormals();
  return g;
}

export function buildEnv(ctx) {
  const { scene } = ctx;
  const group = new THREE.Group();
  group.name = 'Environment';

  // --- ground (receives shadows; vertex colour variation = cheap "grass")
  const ground = new THREE.Mesh(
    groundGeometry(GROUND_SIZE, 96),
    new THREE.MeshStandardMaterial({ color: 0x5f8a3c, roughness: 0.95, metalness: 0.0 }),
  );
  ground.name = 'Ground';
  ground.receiveShadow = true;
  group.add(ground);

  const az = (SUN_AZIMUTH_DEG * Math.PI) / 180, el = (SUN_ELEVATION_DEG * Math.PI) / 180;

  // --- sky: big inverted sphere with a gradient ShaderMaterial (custom GLSL, no lighting needed)
  const skyMat = makeSkyMaterial(THREE);
  skyMat.uniforms.uSunDir.value.set(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
  const sky = new THREE.Mesh(new THREE.SphereGeometry(SKY_RADIUS, 32, 16), skyMat);
  sky.name = 'Sky';
  sky.frustumCulled = false;
  group.add(sky);

  // --- lights + the baked environment: the library's rig — sun, hemisphere fill, the env map
  // metals read from, the visible disc.  Measured 2026-09-07 on this renderer: a metalness-0.9
  // sphere renders near black under hand-rolled sun + hemi (no scene.environment) and reads as
  // metal with this rig; every recorded Blender hero carried metalness 0.7-0.9 into a scene
  // whose env.js set no environment map (0 of 127).
  const rig = sunRig({ azimuth: SUN_AZIMUTH_DEG, elevation: SUN_ELEVATION_DEG, bounds: 45 });
  const sun = rig.sun, hemi = rig.fill;
  group.add(sun, sun.target, hemi);
  if (rig.sunDisc) group.add(rig.sunDisc);
  scene.environment = rig.envTex;

  // --- fog + background matched to the sky horizon colour
  scene.fog = new THREE.Fog(0xcfdcec, FOG_NEAR, FOG_FAR);
  scene.background = new THREE.Color(0xcfdcec);
  scene.add(group);

  const update = (t) => { skyMat.uniforms.uTime.value = t; };
  const sunDir = sun.position.clone().normalize();
  return { ground, sky, sun, sunDir, hemi, group, update };
}
