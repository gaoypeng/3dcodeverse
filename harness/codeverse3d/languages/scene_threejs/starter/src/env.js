// src/env.js — the environment of the EXAMPLE scene (the plan-less skeleton the tests build): the ground every zone
// seats on, the light, the sky / backdrop, the atmosphere.  A plan skeleton gets an interface STUB here instead
// (languages/scene_threejs/_env_for_plan): the plan's data, a plate, and no light or atmosphere until the author writes them.
//   export function heightAt(x, z) → ground height (m): the ONE function every zone seats on.
//   export function buildEnv(ctx) → { update?(t, dt), ...whatever zones may share as ctx.env (sunDir, materials, a water level …) }
//   export const SUN_AZIMUTH_DEG → where the light comes from (0 = +X, 90 = +Z); the assembler reads it to fit cameras on the lit side.
// What is measured on any env:
//   • every zone seats on heightAt, so the ground mesh and heightAt must agree (scene_placement: floating > 0.05 m
//     outdoors / 0.02 m indoors, sunken > 0.10 m); each camera's eye stays above heightAt at its (x, z).
//   • scene.fog and scene.background set (no_fog / no_background); fog far ≥ 2 × the plan span or 1/density ≥ span / 1.5
//     (fog_short); the land reaches past the fog from every authored camera; no world edge in any authored frame (judged).
//   • the judged frames sit in the exposure band (scene_frames: dark_frame / blown_frame / flat_frame).
//   • the ground mesh is named 'Ground…' / 'Terrain…' (the census classifies the ground by name); backdrop geometry 'Sky…'.
// Raw three.js is the baseline; src/lib is an OPTIONAL toolbox (the effects catalog lists it).
import * as THREE from 'three';
import { sunRig } from './lib/environment.js';

export const SUN_AZIMUTH_DEG = 60;    // 0 = +X, 90 = +Z — this example's light
export const SUN_ELEVATION_DEG = 38;
export const MOOD = 'day';            // this example's time of day (sunRig's rigs: day | golden | night | overcast)
export const GROUND_SIZE = 160;       // the example meadow's plate (m): past 2 × its span, so the fog has land to read
export const SHADOW_RADIUS = 45;      // half-size (m) of the sun's shadow frustum: shadows are crisp inside it
// An interior's room box, { center, extents, openings, glazed? } (null: this example is outdoors).
export const INTERIOR = null;
const SEED = 7;

/** Ground height at (x, z) — every placement seats on this.  This example: level. */
export function heightAt(x, z) {
  return 0;
}

export function buildEnv(ctx) {
  const { scene } = ctx;
  const group = new THREE.Group();
  group.name = 'Environment';

  // the example's ground: a level plate following heightAt, named Ground
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(GROUND_SIZE, GROUND_SIZE, 1, 1),
                                new THREE.MeshStandardMaterial({ color: 0x6f6a60, roughness: 0.95 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  ground.name = 'Ground';
  group.add(ground);

  // the example's light: the library's rig (sun + hemisphere fill + the baked environment map PBR materials read
  // from — a metal with no scene.environment renders near black)
  const rig = sunRig({ mood: MOOD, azimuth: SUN_AZIMUTH_DEG, elevation: SUN_ELEVATION_DEG, bounds: SHADOW_RADIUS });
  const sun = rig.sun, hemi = rig.fill;
  group.add(sun, sun.target, hemi);
  if (rig.sunDisc) group.add(rig.sunDisc);
  scene.environment = rig.envTex;

  // the example's atmosphere: a fog reaching past the plate, the background its colour
  scene.fog = new THREE.Fog(0xbfc6cc, GROUND_SIZE * 0.25, GROUND_SIZE * 0.9);
  scene.background = new THREE.Color(scene.fog.color);
  scene.add(group);

  const update = () => {};
  const sunDir = sun.position.clone().normalize();
  return { ground, sun, sunDir, hemi, group, seed: SEED, update };
}
