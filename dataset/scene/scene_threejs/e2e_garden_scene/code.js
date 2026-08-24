// src/scene.js — ASSEMBLED BY THE HARNESS (codeverse assembler). Edit zones/env instead; re-assembly overwrites this file.
import * as THREE from 'three';
import { buildEnv, heightAt } from './env.js';
import { build as buildBridgeCrossing } from './zones/bridge_crossing.js';
import { build as buildGravelCourt } from './zones/gravel_court.js';
import { build as buildKoiPond } from './zones/koi_pond.js';
import { build as buildMapleGrove } from './zones/maple_grove.js';

export async function createScene({ renderer, loaders }) {
  const scene = new THREE.Scene();
  const ctx = { THREE, scene, renderer, loaders, heightAt: heightAt, assets: {} };
  const assetFiles = { red_arched_bridge: '/assets/red_arched_bridge.glb' };
  for (const [key, url] of Object.entries(assetFiles)) {
    try { ctx.assets[key] = (await loaders.gltf.loadAsync(url)).scene; }
    catch (e) { console.error(`[assets] failed to load ${url}: ${e && e.message}`); }
  }
  const env = buildEnv(ctx) || {};
  ctx.env = env;

  const zones = [];
  const addZone = (build, name) => {
    const g = build(ctx);
    if (!g || !g.isObject3D) throw new Error(`zone ${name}: build(ctx) must return a THREE.Group`);
    if (!g.name) g.name = name;
    scene.add(g);
    zones.push(g);
  };
  addZone(buildBridgeCrossing, 'BridgeCrossing');
  addZone(buildGravelCourt, 'GravelCourt');
  addZone(buildKoiPond, 'KoiPond');
  addZone(buildMapleGrove, 'MapleGrove');

  const cameras = [
    { name: 'establishing', position: [-9.5, 5.2, -9], lookAt: [1, 1.2, 1], fov: 48 },
    { name: 'bridge_and_pond', position: [-3.2, 1.6, -1.8], lookAt: [2.5, 0.8, 2], fov: 52 },
    { name: 'lantern_detail', position: [2.2, 1.4, -1], lookAt: [3.6, 1.2, 0.6], fov: 40 },
  ];

  function update(t, dt) {
    if (env.update) env.update(t, dt);
    for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
  }
  return { scene, cameras, update };
}
