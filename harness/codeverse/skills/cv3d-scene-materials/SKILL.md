---
name: cv3d-scene-materials
description: "Use in every scene_threejs build or refine session that authors surfaces - terrain, buildings, props, vegetation. The strongest 'plastic toy' tell is a flat albedo; the second is identical twins. Gives the photographic checklist: procedural variance on every large surface, per-instance jitter, roughness as the realism dial, bevels near the camera, the tight shadow camera, domain-warped terrain with banded colour, and scatter that is intentional instead of uniform."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; ACES tone mapping, GTAO + soft bloom supplied by the renderer.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Ported 2026-09-01 from the scene_multifile_graphics reference ledger (its 18-reviewer audit of 137 scenes; flat walls and duplicated assets are among its most-cited defects, and trees/rocks its two weakest measured classes). Not yet A/B-validated here; the sceneloop battery attaches our numbers.'
  verified: "2026-09-01"
  target_metric: "flat_albedo_surfaces"
  target_direction: "down"
  target_unit: "large surfaces with zero value variance (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no measured runs yet (2026-09-01); the sceneloop A/B battery sets it"
---

# Surfaces that read as a place, not a render

The renderer supplies ambient occlusion, soft bloom and ACES tone mapping.
What still separates a photograph from a toy is AUTHORED: material variance
and light contrast.

## Never ship a flat albedo

One constant `color` per large surface is the strongest plastic tell. Give
every large surface a subtle procedural map: a CanvasTexture of low-contrast
fbm (use the shared noise block from cv3d-scene-water) as `map` multiplied
into the tint, or as `roughnessMap`; `repeat` scaled so one texel is 2-5 cm at
walking distance. Stone, plaster, soil and wood all read instantly better with
5-15% value noise.

## Vary per instance

Copies of one asset get +-6% hue and +-10% lightness jitter — clone the
material once per VARIANT, not per instance — plus scale 0.7-1.3, random
Y-rotation and a tiny tilt. Identical twins read as duplicated geometry;
slight variance reads as a place.

## Roughness is the realism dial

Real surfaces are rarely uniform. Working numbers: wet stone 0.25, dry stone
0.85, painted wood 0.6, weathered wood 0.9, glass 0.05 with `envMapIntensity`
1.5, brushed metal 0.35 with metalness 0.8, foliage 0.75 with slight sheen.
Push a `roughnessMap` (same noise) so highlights break up instead of forming
perfect ovals.

## Bevel what the camera approaches

Razor 90-degree edges catch light unnaturally and read as CAD. Anything within
a few metres of a camera gets `ExtrudeGeometry` with `bevelEnabled` or an
equivalent chamfer. Background boxes can stay sharp.

## Terrain that is landscape, not noise blob

Heightfield: `PlaneGeometry(W, D, N, N)` displaced by 5-octave fbm with DOMAIN
WARP and a power curve — that pair is what separates hills from static:

```js
const y = Math.pow(fbm(x + 30 * fbm2(x * .008, z * .008), z), 1.6);
```

Recompute vertex normals AFTER displacing. Colour by slope and height bands
via vertexColors: rock where `normal.y < 0.65`, else sand -> grass -> rock ->
snow with smoothstep blends and +-5% hue jitter — never one flat green.

## Scatter intentionally

Uniform random scatter reads as noise. Jittered grid (spacing = the mean
distance you want, +-40% jitter), times a clump mask so groves and clearings
form (threshold the shared low-frequency noise at 0.5), times slope/height
rejection. Every instance: y from the SHARED height function, sunk 0.1 m so nothing
hovers on a blade of grass — the placement gate measures floaters.

## The tight shadow camera

One key light (sun) at intensity 2.5-4 with `castShadow`, a dim
`HemisphereLight` 0.3-0.6 fill with a ground-bounce colour, and no third light
without a reason. Then TIGHTEN `light.shadow.camera.left/right/top/bottom` to
the extent the cameras can actually see, `mapSize` 2048-4096: a shadow map
stretched over two kilometres has metre-wide pixels and every shadow is mush.

## Ground the frame

A low, wide gradient patch of darker ground colour under big structures, plus
the slight darkening where verticals meet the ground, anchors everything the
AO pass does not reach.

## Verify before you finish

Zoom one frame to a large wall or the terrain: if the surface is one flat
value, it needs the noise map. Find two copies of the same asset: if you
cannot tell them apart, jitter is missing. Look at one shadow edge: mush means
the shadow camera is too wide.
