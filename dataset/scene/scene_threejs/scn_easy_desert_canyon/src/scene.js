// src/scene.js — ASSEMBLED BY THE HARNESS (codeverse assembler). Edit zones/env instead; re-assembly overwrites this file.
import * as THREE from 'three';
import { buildEnv, heightAt } from './env.js';
import { build as buildCanyonWashFloor } from './zones/canyon_wash_floor.js';
import { build as buildEastCliffWall } from './zones/east_cliff_wall.js';
import { build as buildWestCliffWall } from './zones/west_cliff_wall.js';

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
  await addZone(buildCanyonWashFloor, 'CanyonWashFloor');
  await addZone(buildEastCliffWall, 'EastCliffWall');
  await addZone(buildWestCliffWall, 'WestCliffWall');

  const cameras = [
    { name: 'establishing', position: [0, 14, 52], lookAt: [0, 4, -15], fov: 52 },
    { name: 'canyon_floor_eye', position: [-3.5, 1.7, 18], lookAt: [2.5, 2.2, -12], fov: 55 },
    { name: 'boulder_detail', position: [4.8, 1.6, -6.5], lookAt: [1.5, 1.8, -10.2], fov: 45 },
  ];

  function update(t, dt) {
    if (env.update) env.update(t, dt);
    for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
  }
  return { scene, cameras, update };
}
