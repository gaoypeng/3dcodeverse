// src/scene.js — ASSEMBLED BY THE HARNESS (codeverse assembler). Edit zones/env instead; re-assembly overwrites this file.
import * as THREE from 'three';
import { buildEnv, heightAt } from './env.js';
import { build as buildBambooPerimeter } from './zones/bamboo_perimeter.js';
import { build as buildMapleGroveBank } from './zones/maple_grove_bank.js';
import { build as buildPondBasin } from './zones/pond_basin.js';
import { build as buildZenGarden } from './zones/zen_garden.js';

export async function createScene({ renderer, loaders }) {
  const scene = new THREE.Scene();
  const ctx = { THREE, scene, renderer, loaders, heightAt: heightAt, assets: {} };
  const assetFiles = {  };
  for (const [key, url] of Object.entries(assetFiles)) {
    try { ctx.assets[key] = (await loaders.gltf.loadAsync(url)).scene; }
    catch (e) { console.error(`[assets] failed to load ${url}: ${e && e.message}`); }
  }
  const env = (await buildEnv(ctx)) || {};
  ctx.env = env;

  const zones = [];
  const addZone = async (build, name) => {
    const g = await build(ctx);
    if (!g || !g.isObject3D) throw new Error(`zone ${name}: build(ctx) must return a THREE.Group`);
    if (!g.name) g.name = name;
    scene.add(g);
    zones.push(g);
  };
  await addZone(buildBambooPerimeter, 'BambooPerimeter');
  await addZone(buildMapleGroveBank, 'MapleGroveBank');
  await addZone(buildPondBasin, 'PondBasin');
  await addZone(buildZenGarden, 'ZenGarden');

  const cameras = [
    { name: 'establishing', position: [-9.5, 5.2, 10.5], lookAt: [0.5, 0.6, -1], fov: 46 },
    { name: 'bridge_view', position: [-3.8, 1.65, 4.2], lookAt: [1.5, 0.85, -0.8], fov: 52 },
    { name: 'zen_detail', position: [-4, 1.4, 0.5], lookAt: [-6, 0.2, 3.5], fov: 40 },
  ];

  function update(t, dt) {
    if (env.update) env.update(t, dt);
    for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
  }
  return { scene, cameras, update };
}
