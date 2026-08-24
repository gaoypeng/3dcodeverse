// src/env.js — environment owned by the scene: ground, sky, sun, fog, heightAt.
import * as THREE from 'three';
import { makeSkyMaterial } from './shaders/sky.js';
import { makeWaterMaterial } from './shaders/water.js';

export const BOUNDS = { min: [-13, -1, -13], max: [13, 7, 13] };
export const SUN_AZIMUTH_DEG = 235;    // Azimuth 235°
export const SUN_ELEVATION_DEG = 24;   // Elevation 24°
export const GROUND_SIZE = 80;         // Ground plane extent (m)

const FOG_NEAR = 18;
const FOG_FAR = 45;
const SKY_RADIUS = 300;

// Shared material registry for garden elements
export const MATS = {
  moss: new THREE.MeshStandardMaterial({ color: 0x4a602e, roughness: 0.92, metalness: 0.02 }),
  darkWood: new THREE.MeshStandardMaterial({ color: 0x3d271d, roughness: 0.78, metalness: 0.04 }),
  bridgeWood: new THREE.MeshStandardMaterial({ color: 0x5a3825, roughness: 0.7, metalness: 0.05 }),
  stone: new THREE.MeshStandardMaterial({ color: 0x6e716b, roughness: 0.85, metalness: 0.05 }),
  lanternStone: new THREE.MeshStandardMaterial({ color: 0x5e605a, roughness: 0.8, metalness: 0.05 }),
  gravel: new THREE.MeshStandardMaterial({ color: 0xc4beaf, roughness: 0.95, metalness: 0.0 }),
  mapleLeafRed: new THREE.MeshStandardMaterial({ color: 0xb32018, roughness: 0.55, metalness: 0.0, side: THREE.DoubleSide }),
  mapleLeafOrange: new THREE.MeshStandardMaterial({ color: 0xd64f1a, roughness: 0.55, metalness: 0.0, side: THREE.DoubleSide }),
  bamboo: new THREE.MeshStandardMaterial({ color: 0x859152, roughness: 0.65, metalness: 0.03 }),
};

/**
 * Base mossy ground plane at y=0.0 with gentle undulations;
 * pond basin recessed to y=-0.6.
 * Deterministic and cheap.
 */
export function heightAt(x, z) {
  const dx = (x - 1.2) / 4.6;
  const dz = (z + 1.0) / 3.6;
  const r2 = dx * dx + dz * dz;
  const r = Math.sqrt(r2);
  const base = 0.05 * Math.sin(x * 0.28) * Math.cos(z * 0.22) + 0.025 * Math.sin((x + z) * 0.45);
  
  if (r < 1.0) {
    const pondDepth = -0.6 * 0.5 * (1.0 + Math.cos(r * Math.PI));
    return pondDepth + base * r * r;
  }
  return base;
}

function groundGeometry(size, segs) {
  const g = new THREE.PlaneGeometry(size, size, segs, segs);
  g.rotateX(-Math.PI / 2);
  const pos = g.attributes.position;
  const count = pos.count;
  
  // Vertex colours: blend from pond basin bed to lush mossy surface
  const colors = new Float32Array(count * 3);
  const cPondBed = new THREE.Color(0x282a20);
  const cShore = new THREE.Color(0x3e4a28);
  const cMoss = new THREE.Color(0x4c632f);
  const cLush = new THREE.Color(0x567035);
  const tempCol = new THREE.Color();
  
  for (let i = 0; i < count; i++) {
    const x = pos.getX(i);
    const z = pos.getZ(i);
    const y = heightAt(x, z);
    pos.setY(i, y);
    
    if (y < -0.3) {
      tempCol.copy(cPondBed);
    } else if (y < -0.05) {
      const alpha = (y - (-0.3)) / 0.25;
      tempCol.copy(cPondBed).lerp(cShore, alpha);
    } else if (y < 0.04) {
      const alpha = (y - (-0.05)) / 0.09;
      tempCol.copy(cShore).lerp(cMoss, alpha);
    } else {
      const alpha = Math.min(1.0, (y - 0.04) / 0.04);
      tempCol.copy(cMoss).lerp(cLush, alpha);
    }
    
    colors[i * 3] = tempCol.r;
    colors[i * 3 + 1] = tempCol.g;
    colors[i * 3 + 2] = tempCol.b;
  }
  
  g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  g.computeVertexNormals();
  return g;
}

let activeSkyMat = null;
let activeWaterMat = null;

export function tickEnv(t, dt) {
  if (activeSkyMat && activeSkyMat.uniforms.uTime) {
    activeSkyMat.uniforms.uTime.value = t;
  }
  if (activeWaterMat && activeWaterMat.uniforms.uTime) {
    activeWaterMat.uniforms.uTime.value = t;
  }
}

export function buildEnv(arg1, arg2) {
  let T = THREE;
  let scene = null;
  if (arg1 && arg1.isScene) {
    scene = arg1;
  } else if (arg2 && arg2.isScene) {
    T = arg1 || THREE;
    scene = arg2;
  } else if (arg1 && arg1.scene) {
    T = arg1.THREE || THREE;
    scene = arg1.scene;
  }

  const group = new THREE.Group();
  group.name = 'Environment';

  // --- Ground mesh with vertex colors
  const groundMat = new T.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.92,
    metalness: 0.02,
  });
  const ground = new T.Mesh(groundGeometry(GROUND_SIZE, 120), groundMat);
  ground.name = 'Ground';
  ground.receiveShadow = true;
  group.add(ground);

  // --- Reflective water surface plane at y = -0.12
  const waterMat = makeWaterMaterial(T);
  activeWaterMat = waterMat;
  const waterGeo = new T.CircleGeometry(4.7, 48);
  waterGeo.scale(1.0, 0.78, 1.0);
  waterGeo.rotateX(-Math.PI / 2);
  const water = new T.Mesh(waterGeo, waterMat);
  water.position.set(1.2, -0.12, -1.0);
  water.name = 'PondWater';
  water.receiveShadow = true;
  group.add(water);

  // --- Sun directional light at azimuth 235°, elevation 24°
  const az = (SUN_AZIMUTH_DEG * Math.PI) / 180;
  const el = (SUN_ELEVATION_DEG * Math.PI) / 180;
  const sunDist = 55;
  const sx = Math.cos(el) * Math.sin(az) * sunDist;
  const sy = Math.sin(el) * sunDist;
  const sz = Math.cos(el) * Math.cos(az) * sunDist;

  const sun = new T.DirectionalLight(0xfff0d4, 2.4);
  sun.name = 'SunLight';
  sun.position.set(sx, sy, sz);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.near = 5;
  sun.shadow.camera.far = 100;
  sun.shadow.camera.left = -18;
  sun.shadow.camera.right = 18;
  sun.shadow.camera.top = 18;
  sun.shadow.camera.bottom = -18;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.025;
  sun.target.position.set(0, 0, 0);
  group.add(sun, sun.target);

  const sunDir = sun.position.clone().normalize();
  waterMat.uniforms.uSunDir.value.copy(sunDir);

  // --- Hemisphere light: sky #e8f0ff, ground #4a402e, intensity 0.6
  const hemi = new T.HemisphereLight(0xe8f0ff, 0x4a402e, 0.6);
  hemi.name = 'HemisphereLight';
  group.add(hemi);

  // --- Atmospheric sky dome
  const skyMat = makeSkyMaterial(T);
  activeSkyMat = skyMat;
  skyMat.uniforms.uSunDir.value.copy(sunDir);
  const sky = new T.Mesh(new T.SphereGeometry(SKY_RADIUS, 32, 16), skyMat);
  sky.name = 'Sky';
  sky.frustumCulled = false;
  group.add(sky);

  // --- Light ground fog: color #dfd8c8, start 18 m, end 45 m
  if (scene) {
    scene.fog = new T.Fog(0xdfd8c8, FOG_NEAR, FOG_FAR);
    scene.background = new T.Color(0xdfd8c8);
    scene.add(group);
  }

  const update = (t, dt) => tickEnv(t, dt);

  return {
    sun,
    sunDir,
    ambient: hemi,
    hemi,
    sky,
    ground,
    water,
    fog: scene ? scene.fog : null,
    heightAt,
    group,
    update,
  };
}
