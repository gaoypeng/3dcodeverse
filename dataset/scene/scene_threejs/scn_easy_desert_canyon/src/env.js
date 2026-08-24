// src/env.js — environment module for Sunlit Red Rock Canyon
// Plan:
// - Clear gradient sky from pale desert blue #9fc2d6 at zenith to warm ochre #e8c49e at horizon.
// - Low sun at azimuth 235°, elevation 14°, light colour #ffddaa at intensity 3.4 casting long dramatic shadows.
// - Deep reddish-amber fog #d9a07a with density 0.016.
// - Canyon floor at y=0 is undulating sandy red silt (#c27b4f) with a central dry wash bed depression at y=-0.4.
// - Bounds: centre (0, 15, 0), extents (70, 36, 120) m.
import * as THREE from 'three';
import { makeSkyMaterial } from './shaders/sky.js';

export const BOUNDS = {
  min: [-35, -3, -60],
  max: [35, 33, 60]
};

export const SUN_AZIMUTH_DEG = 235;
export const SUN_ELEVATION_DEG = 14;
export const GROUND_SIZE = 360;
const SKY_RADIUS = 800;

export const MATS = {
  redSand: new THREE.MeshStandardMaterial({
    color: 0xc27b4f,
    roughness: 0.95,
    metalness: 0.02
  }),
  washGravel: new THREE.MeshStandardMaterial({
    color: 0x9e603d,
    roughness: 0.9,
    metalness: 0.05
  }),
  redRock: new THREE.MeshStandardMaterial({
    color: 0xb55a36,
    roughness: 0.88,
    metalness: 0.04
  }),
  sunlitRock: new THREE.MeshStandardMaterial({
    color: 0xd47748,
    roughness: 0.85,
    metalness: 0.03
  }),
  shadowRock: new THREE.MeshStandardMaterial({
    color: 0x6e3828,
    roughness: 0.92,
    metalness: 0.02
  }),
  deadWood: new THREE.MeshStandardMaterial({
    color: 0x4a3c31,
    roughness: 0.95,
    metalness: 0.0
  }),
  desertScrub: new THREE.MeshStandardMaterial({
    color: 0x6e683b,
    roughness: 0.9,
    metalness: 0.0
  })
};

/**
 * Deterministic heightAt function for Canyon wash and terrain.
 * Central dry wash bed depression along x near 0 down to -0.4m,
 * with subtle undulating silt ripples and wash banks.
 */
export function heightAt(x, z) {
  // Wash channel meanders slightly along Z: washCenter = 2.5 * sin(z * 0.04)
  const washCenter = 2.5 * Math.sin(z * 0.04) + 1.2 * Math.sin(z * 0.09);
  const distFromWash = Math.abs(x - washCenter);
  
  // Dry wash depression: width ~ 10m, depth up to -0.4m
  let washDepth = 0;
  if (distFromWash < 8.0) {
    const norm = distFromWash / 8.0;
    // smoothstep bowl shape
    const shape = 0.5 * (1.0 + Math.cos(norm * Math.PI));
    washDepth = -0.4 * shape;
  }

  // Undulating sandy ripples / low banks
  const undulation = 0.12 * Math.sin(x * 0.18 + z * 0.08) + 
                     0.08 * Math.cos(x * 0.07 - z * 0.14) +
                     0.04 * Math.sin((x + z) * 0.35);

  // Slight rise towards cliff bases beyond |x| > 15
  let cliffRise = 0;
  if (Math.abs(x) > 15) {
    cliffRise = Math.pow((Math.abs(x) - 15) * 0.12, 1.4);
  }

  return washDepth + undulation + cliffRise;
}

function groundGeometry(size, segs) {
  const g = new THREE.PlaneGeometry(size, size, segs, segs);
  g.rotateX(-Math.PI / 2);
  const pos = g.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const vx = pos.getX(i);
    const vz = pos.getZ(i);
    pos.setY(i, heightAt(vx, vz));
  }
  g.computeVertexNormals();
  return g;
}

export function buildEnv(ctx) {
  // Support both buildEnv(ctx) and buildEnv(THREE, scene)
  const scene = ctx.scene || ctx;
  const T = THREE;

  const group = new T.Group();
  group.name = 'Environment';

  // --- Ground terrain (undulating sandy red silt #c27b4f)
  const groundMat = new T.MeshStandardMaterial({
    color: 0xc27b4f,
    roughness: 0.95,
    metalness: 0.02,
  });
  const ground = new T.Mesh(groundGeometry(GROUND_SIZE, 128), groundMat);
  ground.name = 'Ground';
  ground.receiveShadow = true;
  ground.castShadow = false;
  group.add(ground);

  // --- Sun direction calculations
  // Azimuth 235°, Elevation 14°
  const az = (SUN_AZIMUTH_DEG * Math.PI) / 180;
  const el = (SUN_ELEVATION_DEG * Math.PI) / 180;
  const sunDir = new T.Vector3(
    Math.sin(az) * Math.cos(el),
    Math.sin(el),
    Math.cos(az) * Math.cos(el)
  ).normalize();

  // --- Sky dome with gradient from zenith #9fc2d6 to horizon #e8c49e
  const skyMat = makeSkyMaterial(T);
  skyMat.uniforms.uZenith.value.setHex(0x9fc2d6);
  skyMat.uniforms.uHorizon.value.setHex(0xe8c49e);
  skyMat.uniforms.uSunDir.value.copy(sunDir);
  const sky = new T.Mesh(new T.SphereGeometry(SKY_RADIUS, 32, 16), skyMat);
  sky.name = 'Sky';
  sky.frustumCulled = false;
  group.add(sky);

  // --- Sun Light: #ffddaa at intensity 3.4
  const sunDist = 120;
  const sun = new T.DirectionalLight(0xffddaa, 3.4);
  sun.position.copy(sunDir).multiplyScalar(sunDist);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.near = 10;
  sun.shadow.camera.far = 240;
  sun.shadow.camera.left = -50;
  sun.shadow.camera.right = 50;
  sun.shadow.camera.top = 70;
  sun.shadow.camera.bottom = -70;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.03;
  sun.target.position.set(0, 5, 0);
  group.add(sun, sun.target);

  // --- Ambient / Hemisphere Light
  // Sky reflection pale blue-tan, ground bounce warm reddish-amber
  const hemi = new T.HemisphereLight(0xe8c49e, 0x7a3a22, 0.7);
  group.add(hemi);

  // --- Fog: Deep reddish-amber fog #d9a07a with density 0.016
  const fogColor = new T.Color(0xd9a07a);
  const fog = new T.FogExp2(fogColor, 0.016);
  scene.fog = fog;
  scene.background = fogColor;
  scene.add(group);

  const update = (t, dt) => {
    if (skyMat.uniforms && skyMat.uniforms.uTime) {
      skyMat.uniforms.uTime.value = t;
    }
  };

  return { ground, sky, sun, sunDir, hemi, fog, heightAt, group, update };
}

export function tickEnv(t, dt) {}
