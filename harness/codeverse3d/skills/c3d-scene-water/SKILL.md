---
name: c3d-scene-water
description: "Use when a scene_threejs session builds water in any form - pond, lake, harbor, river, ocean, pool, canal, waterfall or fountain. Water is a dielectric, not a mirror: the single most common water bug is metalness, and the second is an environment bake it has nothing to reflect. Gives the material numbers, the shared noise block that keeps terrain, shoreline and scatter agreeing, the Gerstner ocean, and foam that reads."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; ACES tone mapping, exposure 1.0.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Ported 2026-09-01 from the scene_multifile_graphics reference ledger, which shipped the metallic-water mistake once and documented the fix, and whose equirect audit showed dark water is usually the ENVIRONMENT bake, not the water (see c3d-scene-atmosphere). Not yet A/B-validated here; the sceneloop battery attaches our numbers.'
  verified: "2026-09-01"
  target_metric: "metallic_water_planes"
  target_direction: "down"
  target_unit: "water materials with metalness above 0.3 (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no measured runs yet (2026-09-01); the sceneloop A/B battery sets it"
---

# Water — a dielectric with something to reflect

Two facts produce almost every convincing water plane: water reflects the
ENVIRONMENT (build c3d-scene-atmosphere's bake first), and water is a
DIELECTRIC — it reflects ~2% head-on and ~100% at grazing angles. That fresnel
ramp is why real water is dark at your feet and mirror-bright at the horizon.

## The material (lake, harbor, pond, pool)

Flat plane + `MeshPhysicalMaterial`:

* `metalness: 0` and `ior: 1.333` — NON-NEGOTIABLE. Metallic water is a
  uniformly tinted mirror at every angle: the dirty-chrome-puddle look. Do not
  raise metalness to "make it reflective"; reflectivity comes from the
  environment map through the fresnel ramp.
* `roughness: 0.05-0.15`, base colour a DARK blue-green (the environment
  supplies the brightness).
* Motion: a small fbm normal `DataTexture` (RepeatWrapping), scrolled as TWO
  copies at different scales and directions via `onBeforeCompile` (two layers
  kill tiling), driven by the shared `uTime`.
* Bias the water plane ~0.02 BELOW the shore ground, or the shoreline
  z-fights.

## The shared noise block (one source of truth for height)

Terrain height, water shorelines, foam and scatter masks must AGREE, so ship
value-noise + fbm ONCE in GLSL and mirror it exactly in JS:

```glsl
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.-2.*f);
  return mix(mix(hash(i), hash(i+vec2(1,0)), f.x),
             mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y); }
float fbm(vec2 p){ float v = 0., a = .5;
  for (int i = 0; i < 5; i++){ v += a * noise(p); p *= 2.03; a *= .5; } return v; }
```

One height function `h(x, z)` sampled by the terrain mesh, the shoreline foam,
tree placement and building foundations is the single biggest coherence win a
scene can buy.

## Shoreline foam beats crest foam

In the water fragment shader, foam from DEPTH against the shared height —
`1. - smoothstep(0., 1.2, waterLevel - h(x, z))`, multiplied by noise — hugs
the banks and reads instantly. Crest foam on open water is harder and pays
less.

## Open ocean — Gerstner, not scrolling normals

ShaderMaterial vertex shader summing 3-4 Gerstner waves:
`k = 2.*PI/L; f = k*dot(D, p.xz) - sqrt(9.8*k)*t;` displace xz by
`Q*A*D*cos(f)`, y by `A*sin(f)`. Keep the sum of Q*A*k below 1 (above it the
crests loop over). Grid 256x256 or denser; get normals by finite-differencing
the DISPLACED surface, not from the flat plane. Fragment side: fresnel mix to
the horizon colour (`pow(1. - dot(N, V), 5.)`).

## Waterfall and fountain

Waterfall: a curved plane whose fragment shader scrolls vertical fbm streaks,
thresholded to white over translucent blue; mist is a cluster of soft sprites
at the base. Fountain: `THREE.Points` where each particle's position is a pure
ballistic arc of `fract(seed + t * rate)` — frozen mid-flight it still reads.

## Verify before you finish

One rendered frame: water should be DARKER looking straight down than toward
the horizon (fresnel working), and it should carry the sky's colour (bake
upright — if it mirrors darkness at midday, fix the atmosphere skill's
orientation bug first, not the water).
