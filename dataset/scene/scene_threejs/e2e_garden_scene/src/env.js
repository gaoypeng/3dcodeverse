// src/env.js — environment owned by the scene: ground, sky, sun, fog, heightAt.
//
// Setting: Kyoto garden, late autumn, clear dusk with twilight sky. Mood: serene, contemplative, warm evening glow.
// Bounds: centre (0.000, 2.500, 0.000) m, extents (26.000, 8.000, 26.000) m
//
// Twilight sky dome fading from deep indigo zenith (#161b33) to warm amber-magenta horizon (#a4505d).
// Sun sunken below horizon with soft ambient fill (#242d4a, intensity 0.8) and gentle directional
// moonlight/crepuscular key light from azimuth 215° elevation 18° (#d8b695, intensity 1.2).
// Soft dusk fog (#1c1e30, start 14 m, end 35 m).
// Ground elevation ranges from mossy banks at y=0.3 m down to pond bed at y=-0.6 m; water surface plane rests at y=-0.15 m.

import * as THREE from 'three';
import { makeSkyMaterial } from './shaders/sky.js';

export const BOUNDS = {
  min: [-13, -1.5, -13],
  max: [13, 6.5, 13]
};

export const SUN_AZIMUTH_DEG = 215;
export const SUN_ELEVATION_DEG = 18;
export const GROUND_SIZE = 80;
const FOG_NEAR = 14;
const FOG_FAR = 35;
const FOG_COLOR = 0x1c1e30;
const SKY_RADIUS = 300;

export const WATER_SURFACE_Y = -0.15;

/** Shared materials registry for scene coherence */
export const MATS = {
  mossyGround: new THREE.MeshStandardMaterial({
    color: 0x485834,
    roughness: 0.92,
    metalness: 0.03
  }),
  pondBed: new THREE.MeshStandardMaterial({
    color: 0x1a2123,
    roughness: 0.9,
    metalness: 0.1
  }),
  stone: new THREE.MeshStandardMaterial({
    color: 0x7a7978,
    roughness: 0.85,
    metalness: 0.05
  }),
  darkWood: new THREE.MeshStandardMaterial({
    color: 0x3a2318,
    roughness: 0.75,
    metalness: 0.0
  }),
  vermilion: new THREE.MeshStandardMaterial({
    color: 0xb53525,
    roughness: 0.45,
    metalness: 0.1
  }),
  autumnLeafRed: new THREE.MeshStandardMaterial({
    color: 0xb32c22,
    roughness: 0.6,
    metalness: 0.0
  }),
  autumnLeafAmber: new THREE.MeshStandardMaterial({
    color: 0xdf8028,
    roughness: 0.6,
    metalness: 0.0
  }),
  lanternGlow: new THREE.MeshStandardMaterial({
    color: 0xffd285,
    emissive: 0xffa544,
    emissiveIntensity: 3.2,
    roughness: 0.35
  })
};

/**
 * Deterministic terrain height:
 * Koi pond basin located roughly near x: 1.0..6.5, z: -1.0..4.0
 * Ground elevation ranges from mossy banks at y=0.3 m down to pond bed at y=-0.6 m.
 */
export function heightAt(x, z) {
  // Elliptical pond center and extents
  const pondCenterX = 3.2;
  const pondCenterZ = 1.6;
  const dx = (x - pondCenterX) / 4.2;
  const dz = (z - pondCenterZ) / 3.4;
  const dSq = dx * dx + dz * dz;

  // Base gentle undulating terrain for banks
  const baseNoise = 0.28 + 0.06 * Math.sin(x * 0.4 + 0.5) * Math.cos(z * 0.35 + 0.2) + 0.03 * Math.sin((x - z) * 0.7);

  if (dSq < 1.0) {
    // Inside pond basin down to -0.6m
    const t = Math.sqrt(dSq); // 0 (center) to 1 (rim)
    const smoothT = t * t * (3.0 - 2.0 * t);
    return THREE.MathUtils.lerp(-0.6, baseNoise, smoothT);
  } else if (dSq < 1.4) {
    // Basin edge / bank rise up to ~0.3m
    const t = (Math.sqrt(dSq) - 1.0) / 0.4;
    const smoothT = t * t * (3.0 - 2.0 * t);
    return THREE.MathUtils.lerp(baseNoise, baseNoise + 0.04, smoothT);
  }

  return baseNoise;
}

function groundGeometry(size, segs) {
  const g = new THREE.PlaneGeometry(size, size, segs, segs);
  g.rotateX(-Math.PI / 2);
  const pos = g.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    pos.setY(i, heightAt(pos.getX(i), pos.getZ(i)));
  }
  g.computeVertexNormals();
  return g;
}

export function tickEnv(t, dt) {}

export function buildEnv(ctx) {
  const scene = (ctx && ctx.scene) ? ctx.scene : ctx;
  const T = (ctx && ctx.THREE) ? ctx.THREE : THREE;
  const group = new THREE.Group();
  group.name = 'Environment';

  // --- 1. Fog & Scene Background
  scene.fog = new T.Fog(FOG_COLOR, FOG_NEAR, FOG_FAR);
  scene.background = new T.Color(FOG_COLOR);

  // --- 2. Ambient Fill & Key Light & Garden Sky Fill
  // Boosted ambient fill for clear ground visibility in dusk (#333e60, intensity 1.3)
  const ambient = new T.AmbientLight(0x333e60, 1.3);
  group.add(ambient);

  // Hemisphere light to gently lift ground shadows with twilight indigo-amber bounce
  const hemi = new T.HemisphereLight(0x50658a, 0x383526, 0.9);
  group.add(hemi);

  // Directional moonlight/crepuscular key light from azimuth 215° elevation 18° (#e0c0a2, intensity 1.6)
  const az = (SUN_AZIMUTH_DEG * Math.PI) / 180;
  const el = (SUN_ELEVATION_DEG * Math.PI) / 180;
  const keyDist = 50;
  const keyLight = new T.DirectionalLight(0xe0c0a2, 1.6);
  keyLight.position.set(
    Math.cos(el) * Math.sin(az) * keyDist,
    Math.sin(el) * keyDist,
    Math.cos(el) * Math.cos(az) * keyDist
  );
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2048, 2048);
  keyLight.shadow.camera.near = 1;
  keyLight.shadow.camera.far = 100;
  keyLight.shadow.camera.left = -20;
  keyLight.shadow.camera.right = 20;
  keyLight.shadow.camera.top = 20;
  keyLight.shadow.camera.bottom = -20;
  keyLight.shadow.bias = -0.0003;
  keyLight.shadow.normalBias = 0.02;
  keyLight.target.position.set(0, 0, 0);
  group.add(keyLight, keyLight.target);

  // --- 3. Sky Dome
  // Twilight sky dome fading from deep indigo zenith (#161b33) to warm amber-magenta horizon (#a4505d)
  let skyMat;
  try {
    skyMat = makeSkyMaterial(T);
    if (skyMat.uniforms) {
      if (skyMat.uniforms.uZenith) skyMat.uniforms.uZenith.value.set(0x161b33);
      if (skyMat.uniforms.uHorizon) skyMat.uniforms.uHorizon.value.set(0xa4505d);
      if (skyMat.uniforms.uSunDir) {
        skyMat.uniforms.uSunDir.value.set(
          Math.cos(el) * Math.sin(az),
          Math.sin(el),
          Math.cos(el) * Math.cos(az)
        ).normalize();
      }
    }
  } catch (e) {
    skyMat = new T.MeshBasicMaterial({ color: 0x161b33, side: T.BackSide });
  }

  const sky = new T.Mesh(new T.SphereGeometry(SKY_RADIUS, 32, 24), skyMat);
  sky.name = 'Sky';
  sky.frustumCulled = false;
  group.add(sky);

  // --- 4. Ground Terrain
  const groundGeo = groundGeometry(GROUND_SIZE, 128);
  const ground = new T.Mesh(groundGeo, MATS.mossyGround);
  ground.name = 'Ground';
  ground.receiveShadow = true;
  group.add(ground);

  scene.add(group);

  const update = (t, dt) => {
    if (skyMat.uniforms && skyMat.uniforms.uTime) {
      skyMat.uniforms.uTime.value = t;
    }
    tickEnv(t, dt);
  };

  const sunDir = keyLight.position.clone().normalize();

  return {
    sun: keyLight,
    ambient,
    hemi,
    sky,
    ground,
    fog: scene.fog,
    heightAt,
    sunDir,
    group,
    update
  };
}
