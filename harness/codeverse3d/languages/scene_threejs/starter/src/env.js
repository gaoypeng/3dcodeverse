// src/env.js — environment owned by the scene: ground, sky, sun, fog, heightAt.
//   export function buildEnv(ctx) → { ground, sky, sun, hemi, update(t, dt) }
//   export function heightAt(x, z) → ground height (m) so zones can seat objects.
//
// The outdoor world ships from the harness-owned library (D71, 2026-09-08): a textured,
// displaced ground that is LEVEL inside the content radius and rolls beyond it, the sky
// gradient + horizon ridge + matched fog of `worldShell`, the relief / fields / copses of
// `makeOutskirts` between the content and that ridge, and the sun rig with its baked
// environment.  Measured over the day's exteriors: with a flat one-colour plane and a bare
// dome as the default, every env session that under-delivered (or died) shipped "flat
// untextured ground", "no aerial perspective", "hard world edge" — the three most frequent
// defects of the tally.  TUNE these (palette, mood, relief, flat areas, the path); do not
// replace them with a plane.
import * as THREE from 'three';
import { makeOutskirts, roomShell, sunRig, worldShell } from './lib/environment.js';
import { mulberry32 } from './lib/noise.js';
import { ground } from './lib/terrain.js';

export const SUN_AZIMUTH_DEG = 60;    // where the sun is, as sunRig reads it (0 = +X, 90 = +Z front); cameras on the sun side are front-lit
export const SUN_ELEVATION_DEG = 38;
export const MOOD = 'day';            // day | golden | night | overcast — the rig, the shell and the fog agree on it
export const GROUND_SIZE = 160;       // ground plane extent (m); the outskirts start at its edge
export const CONTENT_RADIUS = 45;     // the plan bounds' radius (m): level ground inside, rolling beyond; the shadow frustum
// An INTERIOR plan (the skeleton fills this from plan.bounds): the room shell — floor is the
// ground, walls and ceiling on the bounds' faces.  Cut the plan's windows and doors as
// `openings` (see lib/environment.js roomShell) and light the inside; never delete it.  A glass
// house (`glazed: true`, from the plan) is glass walls and roof on an iron frame grid: the sun
// and the land outside come through it.
export const INTERIOR = null;         // e.g. { center: [0, 3, 0], extents: [14, 6, 16], openings: [{ face: 'west', center: [8, 2.5], size: [6, 3] }] }
const SEED = 7;

// The ground and its height function are ONE thing (terrain.js): built at module load with a
// seeded PRNG, so `heightAt` is exactly the field the mesh was displaced with.  Level inside
// CONTENT_RADIUS (the plan's cameras were written for y ≈ 0), rolling out to the outskirts.
const GROUND = ground({
  size: GROUND_SIZE, segments: 128, rand: mulberry32(SEED), relief: 3, scale: 60,
  flat: (x, z) => 1 - Math.min(1, Math.max(0, (Math.hypot(x, z) - CONTENT_RADIUS) / CONTENT_RADIUS)),
});

/** Ground height at (x, z) — every placement seats on this. */
export function heightAt(x, z) {
  return GROUND.height(x, z);
}

export function buildEnv(ctx) {
  const { scene } = ctx;
  const group = new THREE.Group();
  group.name = 'Environment';

  // --- ground (textured, receives shadows) and, for an interior, the enclosure on the bounds
  const ground = GROUND.mesh;
  group.add(ground);
  if (INTERIOR) group.add(roomShell(INTERIOR));   // the enclosure exists before anyone dresses the room

  // --- the world beyond: sky gradient + horizon ridge + fog the colour of the horizon, and the
  // land between the content and that ridge (relief, field patchwork, distant copses)
  const shell = worldShell({ rand: mulberry32(SEED + 1), mood: MOOD, bounds: CONTENT_RADIUS });
  group.add(shell.group);
  if (!INTERIOR || INTERIOR.glazed) group.add(makeOutskirts({ inner: GROUND_SIZE / 2, shellRadius: shell.radius, heightAt, seed: SEED }));

  // --- lights + the baked environment: the library's rig — sun, hemisphere fill, the env map
  // metals read from, the visible disc.  Measured 2026-09-07 on this renderer: a metalness-0.9
  // sphere renders near black under hand-rolled sun + hemi (no scene.environment) and reads as
  // metal with this rig; every recorded Blender hero carried metalness 0.7-0.9 into a scene
  // whose env.js set no environment map (0 of 127).
  const rig = sunRig({ mood: MOOD, azimuth: SUN_AZIMUTH_DEG, elevation: SUN_ELEVATION_DEG, bounds: CONTENT_RADIUS });
  const sun = rig.sun, hemi = rig.fill;
  group.add(sun, sun.target, hemi);
  if (rig.sunDisc) group.add(rig.sunDisc);
  scene.environment = rig.envTex;

  // --- fog + background: the shell's, matched to its horizon colour
  scene.fog = shell.fog;
  scene.background = new THREE.Color(shell.fog.color);
  scene.add(group);

  const update = () => {};
  const sunDir = sun.position.clone().normalize();
  return { ground, sky: shell.group, sun, sunDir, hemi, group, update };
}
