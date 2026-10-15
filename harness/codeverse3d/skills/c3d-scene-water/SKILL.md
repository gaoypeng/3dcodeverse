---
name: c3d-scene-water
description: "Use when a scene_threejs session builds water in any form - pond, lake, harbor, river, ocean, pool, canal, waterfall or fountain. Water is a dielectric, not a mirror: the single most common water bug is metalness, and the second is an environment bake it has nothing to reflect. States what has to be true and how the judge and the motion probe read it, then the library's water modules as the strong options, the shared noise block that keeps terrain, shoreline and scatter agreeing, Gerstner for open water, and foam that reads."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; ACES tone mapping, exposure 1.0.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Ported 2026-09-01 from the scene_multifile_graphics reference ledger, which shipped the metallic-water mistake once and documented the fix, and whose equirect audit showed dark water is usually the ENVIRONMENT bake, not the water (see c3d-scene-atmosphere). Not yet A/B-validated here; the sceneloop battery attaches our numbers. Condensed to requirements + options 2026-10-15.'
  verified: "2026-10-15"
  target_metric: "metallic_water_planes"
  target_direction: "down"
  target_unit: "water materials with metalness above 0.3 (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no measured runs yet (2026-09-01); the sceneloop A/B battery sets it"
---

# Water — a dielectric with something to reflect

Two facts produce almost every convincing water plane: water reflects the ENVIRONMENT
(c3d-scene-atmosphere's bake has to exist and be upright), and water is a DIELECTRIC — it
reflects a few percent head-on and nearly everything at grazing angles. That fresnel ramp is
why real water is dark at your feet and mirror-bright at the horizon.

## What has to be true

* The judge reads water under `materials_shaders_effects`: a uniformly tinted mirror at
  every angle (the dirty-chrome-puddle look) is the metalness mistake; black water at
  midday is an inverted or missing environment bake.
* The motion probe reads water like everything else: the readable combination is a
  vertical wave of at least 0.04 m **and** a scrolling uv/normal of at least 0.15 m/s on an
  authored camera; a mirror-flat pond that only changes its highlight does not clear the
  pixel bar (`nothing_moves`).
* The shoreline agrees with the terrain: water, bank and anything seated near it sample the
  one height function, or the placement gate reports `floating` / `sunken` at the edge.
* ONE reflective water body per scene (the library's reflectors each re-render the scene).

## Strong options from the library

`makeOceanSurface` from `lib/ocean.js` for the ocean, a lake or any sizeable body
(dispersive Gerstner waves on displaced geometry, reflected scene, crest foam);
`makeOcean` from `lib/water.js` for calm or distant water where no wave shape reads;
`makeStream` for a curved shallow stream that refracts its bed; `makeWaterfall` for a sheet
over a weir with its own spray; `makeWaterMist` / `makeSpray` from `lib/watermist.js`;
`patchShoreWet` / `patchShoreFoam` / `patchShallowWater` from `lib/waterside.js` on the
bank; `patchCaustics` on the bed and `patchUnderwater` on anything below the surface. Each
carries `userData.update(t, dt)` — call it from the zone's own update.

## Your own material

One shape that has passed both measures: a plane with a dielectric `MeshPhysicalMaterial`
(`metalness: 0`, water's `ior` — the reflectivity then comes from the environment map through
the fresnel ramp), low roughness, and a base colour dark enough that the environment supplies
the brightness; for the motion measure, a normal map scrolled from the shared `uTime` (two
copies at different scales and directions do not tile visibly) through `onBeforeCompile` or
`patchStandard` from `lib/shader.js`. The one geometric fact: inside any shoreline the terrain
lies below the water level, or the shore z-fights (contract).

## One height for terrain and water

Terrain height, water shorelines, foam and scatter masks have to AGREE (the placement gate
seats on `heightAt`; a shore whose GLSL noise differs from the CPU's floats or z-fights).
`fbm2` in `lib/noise.js` is the CPU side and `noiseDataTexture` / `bakeFbm3` there bake the
same field into a texture a shader can sample — one source of truth, no GLSL copy to keep in
step. A noise function of your own in GLSL is fine when its JS mirror is the function the
terrain uses.

## Foam, open water, falls — where each has paid

Foam from DEPTH against the shared height hugs the banks and reads in a still frame; crest
foam on open water is harder and pays less. Open ocean reads as sea when the surface itself
displaces (Gerstner waves, dispersive — `makeOceanSurface` ships them; your own keeps the
steepness sum below 1 or the crests loop over, and takes normals from the displaced
surface) rather than only scrolling normals on a flat plane. A waterfall reads with scrolled
vertical streaks thresholded to white over translucent water and soft mist at the base
(`makeWaterfall`, `makeWaterMist`); a fountain's particles as pure ballistic arcs of `t` still
read frozen mid-flight. `references/gerstner-and-foam.md` has the wave maths.

## Verify before you finish

One rendered frame: water should be DARKER looking straight down than toward the horizon
(fresnel working), and it should carry the sky's colour (bake upright — if it mirrors
darkness at midday, fix the atmosphere skill's orientation bug first, not the water). Then
the motion table: the water's camera reads MOVING.
