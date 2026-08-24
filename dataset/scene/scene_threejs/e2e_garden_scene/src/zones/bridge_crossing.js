// src/zones/bridge_crossing.js — zone "BridgeCrossing"
// Central elevated crossing zone where the arched wooden bridge spans across the pond neck,
// flanked by glowing stone lanterns on the approaches and framed by mossy rock clusters and stepping stones.
// PLAN bbox: centre (0.500, 1.200, 0.500) m, extents (6.000, 3.500, 5.000) m
// Contents: RedArchedBridge, StoneLantern

import * as THREE from 'three';
import { buildStoneLantern } from '../assets/stone_lantern.js';
import { buildMossyRock } from '../assets/mossy_rock.js';
import { buildSteppingStone } from '../assets/stepping_stone.js';
import { buildWaterLilyPad } from '../assets/water_lily_pad.js';

// Deterministic LCG random helper
function makeRand(seed = 1337) {
  let s = (seed | 0) + 0x6D2B79F5;
  return function() {
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    s = (s + 0x6D2B79F5) | 0;
    return ((t >>> 0) / 4294967296);
  };
}

export function buildBridgeCrossing(ctx = {}) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || (() => 0);
  const rand = (typeof ctx.rand === 'function') ? ctx.rand : makeRand(42);

  const zone = new T.Group();
  zone.name = 'BridgeCrossing';

  const animatedUpdaters = [];

  // Helper to place and orient objects
  const place = (obj, x, y, z, ry = 0) => {
    obj.position.set(x, y, z);
    obj.rotation.y = ry;
    zone.add(obj);
    if (obj.userData && typeof obj.userData.tick === 'function') {
      animatedUpdaters.push(obj.userData.tick);
    }
    return obj;
  };

  // -------------------------------------------------------------
  // 1. Red Arched Bridge (Spanning North-South across pond neck)
  // -------------------------------------------------------------
  const bridgeX = 0.5;
  const bridgeZ = 0.5;
  const bridgeRotY = 0.0;

  // Calculate seating elevation across bridge footprint ends (Z = -1.6m and Z = +2.6m)
  const hSouth = heightAt(bridgeX, bridgeZ - 2.1);
  const hNorth = heightAt(bridgeX, bridgeZ + 2.1);
  const bridgeY = Math.max(hSouth, hNorth, 0.24);

  if (ctx.assets && ctx.assets.red_arched_bridge) {
    const bridgeMesh = ctx.assets.red_arched_bridge.clone();
    bridgeMesh.name = 'RedArchedBridge';
    bridgeMesh.traverse((child) => {
      if (child.isMesh) {
        child.castShadow = true;
        child.receiveShadow = true;
      }
    });
    place(bridgeMesh, bridgeX, bridgeY, bridgeZ, bridgeRotY);
  }

  // Stone foundation abutment blocks under bridge footings for physical grounding
  const foundationMat = new T.MeshStandardMaterial({
    color: 0x5a5854,
    roughness: 0.9,
    metalness: 0.05
  });

  const abutmentGeo = new T.BoxGeometry(1.40, 0.24, 0.25);
  // South abutment
  const southAbutment = new T.Mesh(abutmentGeo, foundationMat);
  southAbutment.name = 'BridgeAbutmentSouth';
  southAbutment.position.set(bridgeX, hSouth - 0.06, bridgeZ - 1.85);
  southAbutment.castShadow = true;
  southAbutment.receiveShadow = true;
  zone.add(southAbutment);

  // North abutment
  const northAbutment = new T.Mesh(abutmentGeo, foundationMat);
  northAbutment.name = 'BridgeAbutmentNorth';
  northAbutment.position.set(bridgeX, hNorth - 0.06, bridgeZ + 1.85);
  northAbutment.castShadow = true;
  northAbutment.receiveShadow = true;
  zone.add(northAbutment);

  // -------------------------------------------------------------
  // 2. Stone Lanterns (Toro) flanking bridge approaches
  // -------------------------------------------------------------
  const lanternLights = [];

  // South approach lantern (East flank)
  const lanSouthX = 1.25;
  const lanSouthZ = -1.15;
  const lanSouthY = heightAt(lanSouthX, lanSouthZ);
  const lanternSouth = buildStoneLantern(T);
  place(lanternSouth, lanSouthX, lanSouthY, lanSouthZ, -0.4);

  const lightSouth = new T.PointLight(0xffa544, 2.6, 6.0, 1.4);
  lightSouth.name = 'LanternLightSouth';
  lightSouth.position.set(lanSouthX, lanSouthY + 1.15, lanSouthZ);
  lightSouth.castShadow = true;
  lightSouth.shadow.bias = -0.002;
  lightSouth.shadow.mapSize.set(512, 512);
  zone.add(lightSouth);
  lanternLights.push({ light: lightSouth, baseIntensity: 2.6, phase: 0.0 });

  // North approach lantern (West flank)
  const lanNorthX = -0.40;
  const lanNorthZ = 2.05;
  const lanNorthY = heightAt(lanNorthX, lanNorthZ);
  const lanternNorth = buildStoneLantern(T);
  place(lanternNorth, lanNorthX, lanNorthY, lanNorthZ, 2.7);

  const lightNorth = new T.PointLight(0xffa544, 2.6, 6.0, 1.4);
  lightNorth.name = 'LanternLightNorth';
  lightNorth.position.set(lanNorthX, lanNorthY + 1.15, lanNorthZ);
  lightNorth.castShadow = true;
  lightNorth.shadow.bias = -0.002;
  lightNorth.shadow.mapSize.set(512, 512);
  zone.add(lightNorth);
  lanternLights.push({ light: lightNorth, baseIntensity: 2.6, phase: 1.7 });

  // -------------------------------------------------------------
  // 3. Stepping Stones (Tobi-Ishi) leading to bridge thresholds
  // -------------------------------------------------------------
  const steppingStonesData = [
    // South approach steps (strictly within Z >= -2.0)
    { x: 0.50, z: -1.50, ry: 0.2, seed: 101 },
    { x: 0.46, z: -1.70, ry: -0.3, seed: 102 },
    // North approach steps (strictly within Z <= 3.0)
    { x: 0.54, z: 2.45, ry: 0.4, seed: 201 },
    { x: 0.58, z: 2.65, ry: -0.2, seed: 202 }
  ];

  steppingStonesData.forEach((sd) => {
    const stone = buildSteppingStone(T, { seed: sd.seed });
    const sy = heightAt(sd.x, sd.z);
    place(stone, sd.x, sy, sd.z, sd.ry);
  });

  // -------------------------------------------------------------
  // 4. Mossy Rocks (Granite boulders framing abutments & shore)
  // -------------------------------------------------------------
  const rocksData = [
    { x: -0.95, z: -0.85, scale: 0.76, ry: 0.6, seed: 401 },
    { x: 1.55, z: -0.60, scale: 0.78, ry: 2.1, seed: 402 },
    { x: -0.90, z: 1.45, scale: 0.74, ry: 1.4, seed: 403 },
    { x: 1.60, z: 1.35, scale: 0.78, ry: 3.5, seed: 404 }
  ];

  rocksData.forEach((rd) => {
    const rock = buildMossyRock(T, { seed: rd.seed });
    rock.scale.setScalar(rd.scale);
    const ry = heightAt(rd.x, rd.z);
    place(rock, rd.x, ry, rd.z, rd.ry);
  });

  // -------------------------------------------------------------
  // 5. Water Lily Pads on water plane near bridge arches
  // -------------------------------------------------------------
  const waterSurfaceY = -0.15;
  const lilyPadsData = [
    { x: 1.25, z: 0.35, ry: 1.1, seed: 501 },
    { x: -0.50, z: 0.50, ry: 2.8, seed: 502 }
  ];

  lilyPadsData.forEach((ld) => {
    const lily = buildWaterLilyPad(T, { seed: ld.seed });
    place(lily, ld.x, waterSurfaceY, ld.z, ld.ry);
  });

  // -------------------------------------------------------------
  // 6. Animation update hook
  // -------------------------------------------------------------
  const update = (t, dt) => {
    // 1) Warm lantern light flutter (~2.2 Hz ±15%)
    for (let i = 0; i < lanternLights.length; i++) {
      const item = lanternLights[i];
      const flicker = 1.0 + 0.15 * Math.sin(t * 13.82 + item.phase) + 0.05 * Math.sin(t * 31.4 + item.phase * 2.0);
      item.light.intensity = item.baseIntensity * flicker;
    }

    // 2) Child object updates (e.g. lantern emissive and lily bobbing)
    for (let i = 0; i < animatedUpdaters.length; i++) {
      animatedUpdaters[i](t, dt);
    }
  };

  zone.userData.update = update;
  zone.userData.tick = update;

  return zone;
}

export const build = buildBridgeCrossing;
