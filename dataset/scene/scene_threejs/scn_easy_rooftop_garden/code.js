// src/scene.js — ASSEMBLED BY THE HARNESS (codeverse assembler). Edit zones/env instead; re-assembly overwrites this file.
import * as THREE from 'three';
import { buildEnv, heightAt } from './env.js';
import { build as buildLoungeArea } from './zones/lounge_area.js';
import { build as buildPlanterPerimeter } from './zones/planter_perimeter.js';
import { build as buildSkylineBackdrop } from './zones/skyline_backdrop.js';

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
  await addZone(buildLoungeArea, 'LoungeArea');
  await addZone(buildPlanterPerimeter, 'PlanterPerimeter');
  await addZone(buildSkylineBackdrop, 'SkylineBackdrop');

  const cameras = [
    { name: 'establishing', position: [9.5, 4.2, 11.5], lookAt: [-1, 1.6, -2], fov: 52 },
    { name: 'lounge_eye_level', position: [1.8, 1.6, 2.4], lookAt: [-3.5, 1.4, -6], fov: 58 },
    { name: 'planter_detail', position: [-4.5, 1.3, 2], lookAt: [-2, 1.7, 0.2], fov: 42 },
  ];

  function update(t, dt) {
    if (env.update) env.update(t, dt);
    for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
  }
  return { scene, cameras, update };
}
