// src/zones/koi_pond.js — zone "KoiPond"
// Description: Naturalistic pond basin with water surface, organic curved stone perimeter, lily pads, and stepping stones across shallows.
// Bbox: centre (3.500, 0.000, 1.500) m, extents (11.000, 2.500, 13.000) m.
// Contents: WaterLilyPad, MossyRock, SteppingStone

import * as THREE from 'three';
import { buildWaterLilyPad } from '../assets/water_lily_pad.js';
import { buildMossyRock } from '../assets/mossy_rock.js';
import { buildSteppingStone } from '../assets/stepping_stone.js';
import { makeWaterMaterial } from '../shaders/water.js';

export function buildKoiPond(ctx = {}) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || (() => 0);
  const zone = new T.Group();
  zone.name = 'KoiPond';

  const WATER_Y = -0.15;
  const animatedItems = [];

  // --- Pond Water Plane with PondWaterRipple shader material ---
  let pondWaterMat;
  try {
    pondWaterMat = makeWaterMaterial(T);
    if (pondWaterMat.uniforms) {
      if (pondWaterMat.uniforms.uDeep) pondWaterMat.uniforms.uDeep.value.set(0x0a1622);
      if (pondWaterMat.uniforms.uShallow) pondWaterMat.uniforms.uShallow.value.set(0x284e68);
      if (pondWaterMat.uniforms.uSunDir) {
        pondWaterMat.uniforms.uSunDir.value.set(-0.45, 0.6, -0.65).normalize();
      }
    }
  } catch (e) {
    pondWaterMat = new T.MeshStandardMaterial({
      color: 0x1b384d,
      roughness: 0.1,
      metalness: 0.2,
      transparent: true,
      opacity: 0.88
    });
  }

  // Pond water plane geometry covering the basin
  const pondWaterGeo = new T.PlaneGeometry(10.5, 9.0, 48, 48);
  pondWaterGeo.rotateX(-Math.PI / 2);
  const pondWater = new T.Mesh(pondWaterGeo, pondWaterMat);
  pondWater.name = 'PondWaterPlane';
  pondWater.position.set(3.5, WATER_Y, 1.5);
  pondWater.receiveShadow = true;
  pondWater.renderOrder = 2;
  zone.add(pondWater);

  // Helper to place and orient assets on terrain
  const placeOnGround = (obj, x, z, ry = 0, yOffset = 0) => {
    const y = heightAt(x, z) + yOffset;
    obj.position.set(x, y, z);
    obj.rotation.y = ry;
    zone.add(obj);
    return obj;
  };

  // Helper to place floating assets on water surface
  const placeOnWater = (obj, x, z, ry = 0) => {
    obj.position.set(x, WATER_Y, z);
    obj.rotation.y = ry;
    zone.add(obj);
    if (obj.userData && typeof obj.userData.tick === 'function') {
      animatedItems.push(obj);
    }
    return obj;
  };

  // 1. Naturalistic organic rock perimeter encircling the pond basin
  const perimeterRockConfigs = [
    { x: 1.8, z: -1.2, ry: 0.4, scale: [0.95, 0.9, 0.95], seed: 11 },
    { x: 3.0, z: -1.55, ry: 1.2, scale: [1.1, 1.0, 1.05], seed: 23 },
    { x: 4.4, z: -1.35, ry: 2.1, scale: [1.0, 0.85, 0.9], seed: 37 },
    { x: 5.9, z: -0.6, ry: 0.8, scale: [1.15, 1.05, 1.1], seed: 49 },
    { x: 6.7, z: 0.8, ry: 1.7, scale: [0.9, 0.8, 0.85], seed: 61 },
    { x: 6.8, z: 2.3, ry: 2.9, scale: [1.05, 0.95, 1.0], seed: 73 },
    { x: 6.1, z: 3.8, ry: 0.3, scale: [1.1, 1.0, 1.05], seed: 85 },
    { x: 4.6, z: 4.55, ry: 1.5, scale: [1.0, 0.9, 0.95], seed: 97 },
    { x: 3.1, z: 4.5, ry: 2.6, scale: [1.2, 1.05, 1.1], seed: 109 },
    { x: 1.7, z: 3.7, ry: 3.4, scale: [0.95, 0.85, 0.9], seed: 121 },
    { x: 1.6, z: -0.2, ry: 1.9, scale: [0.85, 0.75, 0.8], seed: 133 },
    // Shallows accent boulders
    { x: 3.8, z: 0.3, ry: 0.9, scale: [0.8, 0.7, 0.8], seed: 145, yOff: -0.05 },
    { x: 2.5, z: 2.9, ry: 2.2, scale: [0.75, 0.65, 0.75], seed: 157, yOff: -0.03 },
  ];

  perimeterRockConfigs.forEach((cfg) => {
    const rock = buildMossyRock(T, { seed: cfg.seed });
    if (cfg.scale) rock.scale.set(...cfg.scale);
    placeOnGround(rock, cfg.x, cfg.z, cfg.ry, cfg.yOff || 0);
  });

  // 2. Stepping stones path (tobi-ishi) crossing the shallows
  const steppingStoneConfigs = [
    { x: 5.9, z: -0.1, ry: 0.2, seed: 201 },
    { x: 5.3, z: 0.55, ry: 0.8, seed: 211 },
    { x: 4.8, z: 1.25, ry: 1.4, seed: 223 },
    { x: 4.5, z: 2.0, ry: 0.6, seed: 235 },
    { x: 4.7, z: 2.75, ry: 1.9, seed: 247 },
    { x: 5.3, z: 3.45, ry: 2.5, seed: 259 },
    { x: 6.0, z: 4.05, ry: 0.4, seed: 271 },
  ];

  steppingStoneConfigs.forEach((cfg) => {
    const stone = buildSteppingStone(T, { seed: cfg.seed });
    const groundH = heightAt(cfg.x, cfg.z);
    // Ensure stone is seated on pond bed while emerging proudly above water
    const yPos = Math.max(groundH, WATER_Y - 0.04);
    stone.position.set(cfg.x, yPos, cfg.z);
    stone.rotation.y = cfg.ry;
    zone.add(stone);
  });

  // 3. Clustered water lily pads floating on the pond water surface
  const lilyPadConfigs = [
    { x: 2.7, z: 0.9, ry: 0.4, seed: 301 },
    { x: 3.5, z: 2.2, ry: 1.7, seed: 313 },
    { x: 2.3, z: 2.1, ry: 3.1, seed: 327 },
    { x: 3.9, z: 0.9, ry: 4.6, seed: 341 },
    { x: 1.9, z: 1.4, ry: 1.1, seed: 353 },
    { x: 3.1, z: 3.1, ry: 2.4, seed: 367 },
    { x: 4.3, z: 1.9, ry: 5.2, seed: 379 },
  ];

  lilyPadConfigs.forEach((cfg) => {
    const pad = buildWaterLilyPad(T, { seed: cfg.seed });
    placeOnWater(pad, cfg.x, cfg.z, cfg.ry);
  });

  // Animation update hook
  zone.userData.update = (t, dt) => {
    // Drive water ripple shader animation
    if (pondWaterMat.uniforms && pondWaterMat.uniforms.uTime) {
      pondWaterMat.uniforms.uTime.value = t;
    }
    for (let i = 0; i < animatedItems.length; i++) {
      const item = animatedItems[i];
      if (item.userData && typeof item.userData.tick === 'function') {
        item.userData.tick(t, dt);
      }
    }
  };

  return zone;
}

export const build = buildKoiPond;
