// src/env.js — environment for Golden Hour Rooftop Garden
// Setting: Modern city high-rise rooftop, summer, clear golden hour dusk.
// Mood: warm, serene, luxurious urban oasis.
// Bounds: centre (0.000, 8.000, 0.000) m, extents (36.000, 24.000, 36.000) m

import * as THREE from 'three';
import { makeSkyMaterial } from './shaders/sky.js';

export const BOUNDS = {
  min: [-18, -4, -18],
  max: [18, 20, 18],
  center: [0, 8, 0],
  size: [36, 24, 36]
};

// Planned parameters
export const SUN_AZIMUTH_DEG = 245;
export const SUN_ELEVATION_DEG = 14;
export const SUN_COLOR = 0xffd09e;
export const SUN_INTENSITY = 3.2;

export const SKY_ZENITH = 0x2d325a;
export const SKY_HORIZON = 0xf99a4e;

export const HEMI_SKY_COLOR = 0xa87c6d;
export const HEMI_GROUND_COLOR = 0x3a3242;
export const HEMI_INTENSITY = 0.8;

export const FOG_COLOR = 0xe69b6a;
export const FOG_NEAR = 30;
export const FOG_FAR = 180;

export const DECK_Y = 0.15;
export const PARAPET_HEIGHT = 1.05;
export const TERRACE_SIZE = 26; // Rooftop garden terrace dimensions

// Shared materials registry
export const MATS = {
  woodDeck: new THREE.MeshStandardMaterial({
    color: 0x8a5b36,
    roughness: 0.75,
    metalness: 0.05,
    name: 'WoodDeck'
  }),
  stoneParapet: new THREE.MeshStandardMaterial({
    color: 0x4a4645,
    roughness: 0.85,
    metalness: 0.1,
    name: 'StoneParapet'
  }),
  stoneCap: new THREE.MeshStandardMaterial({
    color: 0x2e2b2a,
    roughness: 0.7,
    metalness: 0.1,
    name: 'StoneCap'
  }),
  buildingBase: new THREE.MeshStandardMaterial({
    color: 0x232529,
    roughness: 0.9,
    metalness: 0.2,
    name: 'BuildingBase'
  })
};

/**
 * Terrain height function: Rooftop terrace decking elevated at y=0.15.
 * Returns 0.15 inside rooftop perimeter, allowing assets to sit seamlessly on the deck.
 */
export function heightAt(x, z) {
  // Flat rooftop terrace at y = 0.15
  return DECK_Y;
}

export function tickEnv(t, dt) {}

export function buildEnv(ctx) {
  // Support both buildEnv(ctx) and buildEnv(THREE, scene) contracts
  const T = ctx.THREE || THREE;
  const scene = ctx.scene || ctx;
  const group = new T.Group();
  group.name = 'Environment';

  const az = (SUN_AZIMUTH_DEG * Math.PI) / 180;
  const el = (SUN_ELEVATION_DEG * Math.PI) / 180;
  const sunDir = new T.Vector3(
    Math.sin(az) * Math.cos(el),
    Math.sin(el),
    Math.cos(az) * Math.cos(el)
  ).normalize();

  // --- Sky Dome
  const skyMat = makeSkyMaterial(T);
  if (skyMat.uniforms) {
    if (skyMat.uniforms.uZenith) skyMat.uniforms.uZenith.value.set(SKY_ZENITH);
    if (skyMat.uniforms.uHorizon) skyMat.uniforms.uHorizon.value.set(SKY_HORIZON);
    if (skyMat.uniforms.uSunDir) skyMat.uniforms.uSunDir.value.copy(sunDir);
  }
  const skyGeo = new T.SphereGeometry(300, 32, 16);
  const sky = new T.Mesh(skyGeo, skyMat);
  sky.name = 'Sky';
  sky.frustumCulled = false;
  group.add(sky);

  // --- Lights
  // Key light: Directional sunlight
  const sunDist = 90;
  const sun = new T.DirectionalLight(SUN_COLOR, SUN_INTENSITY);
  sun.name = 'SunLight';
  sun.position.set(sunDir.x * sunDist, sunDir.y * sunDist, sunDir.z * sunDist);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.near = 10;
  sun.shadow.camera.far = 200;
  sun.shadow.camera.left = -25;
  sun.shadow.camera.right = 25;
  sun.shadow.camera.top = 25;
  sun.shadow.camera.bottom = -25;
  sun.shadow.bias = -0.0005;
  sun.shadow.normalBias = 0.02;
  sun.target.position.set(0, 0.5, 0);
  group.add(sun, sun.target);

  // Soft ambient sky lighting
  const ambient = new T.HemisphereLight(HEMI_SKY_COLOR, HEMI_GROUND_COLOR, HEMI_INTENSITY);
  ambient.name = 'AmbientLight';
  group.add(ambient);

  // Additional subtle warm fill for golden hour bounce
  const warmFill = new T.DirectionalLight(0xffaa66, 0.6);
  warmFill.position.set(-sunDir.x * 40, 20, -sunDir.z * 40);
  group.add(warmFill);

  // --- Atmospheric Fog
  scene.fog = new T.Fog(FOG_COLOR, FOG_NEAR, FOG_FAR);
  scene.background = new T.Color(SKY_HORIZON);

  // --- Rooftop Terrace Architecture
  // 1. Elevated Timber Deck (at y = 0.15, height 0.3)
  const deckGeo = new T.BoxGeometry(TERRACE_SIZE, 0.3, TERRACE_SIZE);
  const deckMesh = new T.Mesh(deckGeo, MATS.woodDeck);
  deckMesh.name = 'RooftopDeck';
  deckMesh.position.set(0, DECK_Y - 0.15, 0); // Top surface at y = 0.15
  deckMesh.receiveShadow = true;
  deckMesh.castShadow = true;
  group.add(deckMesh);

  // 2. High-Rise Building Substructure beneath deck
  const subBuildingGeo = new T.BoxGeometry(TERRACE_SIZE + 0.4, 40, TERRACE_SIZE + 0.4);
  const subBuilding = new T.Mesh(subBuildingGeo, MATS.buildingBase);
  subBuilding.name = 'BuildingCore';
  subBuilding.position.set(0, -20.15, 0);
  subBuilding.receiveShadow = true;
  group.add(subBuilding);

  // 3. Perimeter Parapet Wall (1.05m high stone parapet above deck y=0.15)
  // Wall dimensions: 4 segments surrounding the terrace
  const parapetGroup = new T.Group();
  parapetGroup.name = 'ParapetWalls';
  const wallThick = 0.4;
  const wallHeight = PARAPET_HEIGHT;
  const wallY = DECK_Y + wallHeight / 2;
  const halfSize = TERRACE_SIZE / 2;

  // North & South walls (+Z and -Z)
  const nsWallGeo = new T.BoxGeometry(TERRACE_SIZE + wallThick, wallHeight, wallThick);
  const wallS = new T.Mesh(nsWallGeo, MATS.stoneParapet);
  wallS.position.set(0, wallY, halfSize);
  wallS.castShadow = true;
  wallS.receiveShadow = true;

  const wallN = new T.Mesh(nsWallGeo, MATS.stoneParapet);
  wallN.position.set(0, wallY, -halfSize);
  wallN.castShadow = true;
  wallN.receiveShadow = true;

  // East & West walls (+X and -X)
  const ewWallGeo = new T.BoxGeometry(wallThick, wallHeight, TERRACE_SIZE - wallThick);
  const wallE = new T.Mesh(ewWallGeo, MATS.stoneParapet);
  wallE.position.set(halfSize, wallY, 0);
  wallE.castShadow = true;
  wallE.receiveShadow = true;

  const wallW = new T.Mesh(ewWallGeo, MATS.stoneParapet);
  wallW.position.set(-halfSize, wallY, 0);
  wallW.castShadow = true;
  wallW.receiveShadow = true;

  // Parapet stone top coping caps (darker stone rim)
  const capHeight = 0.08;
  const capThick = wallThick + 0.08;
  const capY = DECK_Y + wallHeight + capHeight / 2;

  const nsCapGeo = new T.BoxGeometry(TERRACE_SIZE + capThick, capHeight, capThick);
  const capS = new T.Mesh(nsCapGeo, MATS.stoneCap);
  capS.position.set(0, capY, halfSize);
  capS.castShadow = true;

  const capN = new T.Mesh(nsCapGeo, MATS.stoneCap);
  capN.position.set(0, capY, -halfSize);
  capN.castShadow = true;

  const ewCapGeo = new T.BoxGeometry(capThick, capHeight, TERRACE_SIZE - wallThick);
  const capE = new T.Mesh(ewCapGeo, MATS.stoneCap);
  capE.position.set(halfSize, capY, 0);
  capE.castShadow = true;

  const capW = new T.Mesh(ewCapGeo, MATS.stoneCap);
  capW.position.set(-halfSize, capY, 0);
  capW.castShadow = true;

  parapetGroup.add(wallS, wallN, wallE, wallW, capS, capN, capE, capW);
  group.add(parapetGroup);

  scene.add(group);

  const update = (t, dt) => {
    if (skyMat.uniforms && skyMat.uniforms.uTime) {
      skyMat.uniforms.uTime.value = t;
    }
  };

  return {
    sun,
    ambient,
    sky,
    ground: deckMesh,
    water: null,
    fog: scene.fog,
    heightAt,
    hemi: ambient,
    group,
    update
  };
}
