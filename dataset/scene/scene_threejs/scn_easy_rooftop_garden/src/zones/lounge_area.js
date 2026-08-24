// src/zones/lounge_area.js — zone "LoungeArea"
// Central timber deck section covered by an open-beam wooden pergola, furnished with an
// L-shaped outdoor sofa, low coffee table, and overhead catenary string lights.
// Bbox: centre (0.000, 1.800, 0.000) m, extents (8.000, 3.600, 8.000) m
// Contents: Pergola, StringLights, LoungeSofa, CoffeeTable

import * as THREE from 'three';
import { buildPergola } from '../assets/pergola.js';
import { buildStringLights } from '../assets/string_lights.js';
import { buildLoungeSofa } from '../assets/lounge_sofa.js';
import { buildCoffeeTable } from '../assets/coffee_table.js';

export function buildLoungeArea(ctx = {}) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || (() => 0.15);
  const rand = ctx.rand || Math.random;

  const zone = new T.Group();
  zone.name = 'LoungeArea';

  const groundY = heightAt(0, 0);

  // 1. Outdoor Woven Area Rug (defining the cozy conversation zone under the pergola)
  const rugMat = new T.MeshStandardMaterial({
    color: 0xcfcaa5,
    roughness: 0.95,
    metalness: 0.02,
  });
  const rugBorderMat = new T.MeshStandardMaterial({
    color: 0x3d3733,
    roughness: 0.9,
    metalness: 0.02,
  });

  const rugGroup = new T.Group();
  rugGroup.name = 'OutdoorRug';

  const rugMesh = new T.Mesh(new T.BoxGeometry(3.60, 0.012, 3.20), rugMat);
  rugMesh.position.set(0.10, groundY + 0.006, -0.10);
  rugMesh.receiveShadow = true;
  rugGroup.add(rugMesh);

  // Decorative border trim on rug
  const border1 = new T.Mesh(new T.BoxGeometry(3.64, 0.014, 0.08), rugBorderMat);
  border1.position.set(0.10, groundY + 0.007, -0.10 + 1.56);
  const border2 = new T.Mesh(new T.BoxGeometry(3.64, 0.014, 0.08), rugBorderMat);
  border2.position.set(0.10, groundY + 0.007, -0.10 - 1.56);
  const border3 = new T.Mesh(new T.BoxGeometry(0.08, 0.014, 3.20), rugBorderMat);
  border3.position.set(0.10 + 1.76, groundY + 0.007, -0.10);
  const border4 = new T.Mesh(new T.BoxGeometry(0.08, 0.014, 3.20), rugBorderMat);
  border4.position.set(0.10 - 1.76, groundY + 0.007, -0.10);
  rugGroup.add(border1, border2, border3, border4);
  zone.add(rugGroup);

  // 2. Modern Wooden Pergola (Centered over the deck)
  const pergola = buildPergola(T, { seed: 101, variant: 0 });
  pergola.position.set(0.0, groundY, 0.0);
  zone.add(pergola);

  // 3. Overhead Catenary String Lights (Hung under pergola roof beams)
  const stringLights = buildStringLights(T, { seed: 42 });
  stringLights.position.set(0.0, groundY + 2.36, 0.0);
  zone.add(stringLights);

  // 4. L-Shaped Sectional Outdoor Lounge Sofa
  const loungeSofa = buildLoungeSofa(T, { seed: 42 });
  loungeSofa.position.set(0.15, groundY, -0.35);
  zone.add(loungeSofa);

  // 5. Rectangular Low Coffee Table
  const coffeeTable = buildCoffeeTable(T, { seed: 42 });
  coffeeTable.position.set(0.35, groundY, 0.35);
  coffeeTable.rotation.y = -0.05;
  zone.add(coffeeTable);

  // 6. Accent Contemporary Outdoor Pouf / Ottoman (Right side seating accent)
  const poufMat = new T.MeshStandardMaterial({
    color: 0x36393e,
    roughness: 0.9,
    metalness: 0.02,
  });
  const poufBandMat = new T.MeshStandardMaterial({
    color: 0x9e6a38,
    roughness: 0.7,
    metalness: 0.05,
  });

  const poufGroup = new T.Group();
  poufGroup.name = 'AccentPouf';
  const poufMesh = new T.Mesh(new T.CylinderGeometry(0.30, 0.32, 0.36, 16), poufMat);
  poufMesh.position.set(1.40, groundY + 0.18, 0.35);
  poufMesh.castShadow = true;
  poufMesh.receiveShadow = true;
  const poufBand = new T.Mesh(new T.CylinderGeometry(0.322, 0.322, 0.04, 16), poufBandMat);
  poufBand.position.set(1.40, groundY + 0.18, 0.35);
  poufGroup.add(poufMesh, poufBand);
  zone.add(poufGroup);

  // 7. Architectural Deck Lanterns (Corner floor ambient accents)
  const lanternMetalMat = new T.MeshStandardMaterial({
    color: 0x221f1d,
    roughness: 0.4,
    metalness: 0.85,
  });
  const lanternGlassMat = new T.MeshStandardMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.35,
    roughness: 0.1,
  });
  const candleMat = new T.MeshStandardMaterial({
    color: 0xffeedd,
    roughness: 0.7,
  });
  const candleFlameMat = new T.MeshStandardMaterial({
    color: 0xffaa33,
    emissive: 0xff8811,
    emissiveIntensity: 2.2,
    roughness: 0.2,
  });

  const floorLanterns = [];
  const lanternCoords = [
    [1.75, 1.65, 0.48],   // Front right near pergola post
    [-1.75, 1.65, 0.38],  // Front left near pergola post
    [-1.70, -1.65, 0.44], // Back left near pergola post
  ];

  for (let li = 0; li < lanternCoords.length; li++) {
    const [lx, lz, lh] = lanternCoords[li];
    const lantern = new T.Group();
    lantern.name = `FloorLantern_${li + 1}`;
    lantern.position.set(lx, groundY, lz);

    const lWidth = 0.18;
    // Base & Top caps
    const baseCap = new T.Mesh(new T.BoxGeometry(lWidth, 0.02, lWidth), lanternMetalMat);
    baseCap.position.y = 0.01;
    baseCap.castShadow = true;
    const topCap = new T.Mesh(new T.BoxGeometry(lWidth, 0.025, lWidth), lanternMetalMat);
    topCap.position.y = lh - 0.0125;
    topCap.castShadow = true;

    // Glass walls
    const glass = new T.Mesh(new T.BoxGeometry(lWidth - 0.03, lh - 0.04, lWidth - 0.03), lanternGlassMat);
    glass.position.y = lh / 2;

    // Pillar struts
    const strutGeo = new T.BoxGeometry(0.015, lh, 0.015);
    const halfO = lWidth / 2 - 0.01;
    const sOffsets = [[-halfO, -halfO], [halfO, -halfO], [-halfO, halfO], [halfO, halfO]];
    for (const [sox, soz] of sOffsets) {
      const strut = new T.Mesh(strutGeo, lanternMetalMat);
      strut.position.set(sox, lh / 2, soz);
      strut.castShadow = true;
      lantern.add(strut);
    }

    // Candle inside
    const candleH = lh * 0.45;
    const candle = new T.Mesh(new T.CylinderGeometry(0.035, 0.035, candleH, 12), candleMat);
    candle.position.y = 0.02 + candleH / 2;
    candle.castShadow = true;

    // Flame
    const flame = new T.Mesh(new T.ConeGeometry(0.012, 0.03, 8), candleFlameMat);
    flame.position.y = 0.02 + candleH + 0.015;

    lantern.add(baseCap, topCap, glass, candle, flame);
    zone.add(lantern);

    floorLanterns.push({ flame, candleFlameMat, phase: li * 1.5 });
  }

  // Unified zone animation tick handler
  zone.userData.update = (t, dt) => {
    if (stringLights.userData.update) stringLights.userData.update(t, dt);
    if (coffeeTable.userData.update) coffeeTable.userData.update(t, dt);
    if (pergola.userData.update) pergola.userData.update(t, dt);

    for (let i = 0; i < floorLanterns.length; i++) {
      const fl = floorLanterns[i];
      const flicker = 1.8 + 0.4 * Math.sin(t * 8.0 + fl.phase) + 0.2 * Math.sin(t * 14.3 + fl.phase);
      fl.candleFlameMat.emissiveIntensity = flicker;
      fl.flame.scale.set(1 + 0.06 * Math.sin(t * 9.0 + fl.phase), 1 + 0.1 * Math.sin(t * 11.5 + fl.phase), 1 + 0.06 * Math.cos(t * 9.0));
    }
  };

  zone.userData.tick = zone.userData.update;

  return zone;
}

export const build = buildLoungeArea;
