---
name: c3d-scene-atmosphere
description: "Use when a scene_threejs env, baseline or refine session builds the sky, the environment map, clouds or fog. The sky is not a backdrop: it is the light source every PBR material reflects. Gives the sky-dome shader, the equirect bake that turns it into scene.environment (with the one-line orientation bug that blackens water), the cloud recipes that read in a still frame, and the fog numbers that add depth instead of eating the scene."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; renderer fixed at ACES Filmic tone mapping, exposure 1.0, sRGB output.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Recipes ported 2026-09-01 from the scene_multifile_graphics reference harness, where they carry measured origins: its audit found the equirect bake INVERTED in 4 of 9 delivered scenes (a Venice canal at patch RGB 35,27,18 went to 100,87,43 by flipping one line), and its PMREM note is from three.js source (cube size = width/4). Not yet A/B-validated on this harness; the sceneloop battery attaches our own numbers.'
  verified: "2026-09-01"
  target_metric: "env_bake_orientation_flags"
  target_direction: "down"
  target_unit: "inverted bakes per run (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no measured runs yet (2026-09-01); the sceneloop A/B battery sets it"
---

# Sky, environment and air — the frame's light source comes first

Build the sky BEFORE any PBR material exists. `scene.environment` is what makes
metal, glass, water and wet ground live; a scene assembled first and skied later
has every reflective surface already born dead.

## The sky dome

A big sphere, `side: THREE.BackSide`, `depthWrite: false`, named `Sky`, with a
ShaderMaterial over the view direction `d = normalize(vWorldPos)`:

```glsl
vec3 col = mix(horizonColor, zenithColor, pow(clamp(d.y, 0., 1.), 0.6));
float sunAmt = max(dot(d, sunDir), 0.0);
col += hazeColor * pow(sunAmt, 8.0) * 0.25;               // mie halo
col += sunColor * smoothstep(0.9993, 0.9998, sunAmt);     // sun disk
col = mix(col, horizonColor, pow(1.0 - clamp(d.y, 0., 1.), 8.0)); // haze band
```

* Sunset: sun low (`sunDir.y` around 0.1), horizon orange/rose, widen the halo
  (`pow(sunAmt, 4.0) * 0.6`).
* Night: zenith around #050a18, horizon #0d1626; stars by hashing the direction
  (`vec3 c = floor(d*300.); step(0.9985, hash(c.xy + c.z))`, vary brightness);
  a moon disk shaded with fbm and ONE slate-blue DirectionalLight.

## Bake the SAME sky into `scene.environment`

Mirror the sky function in JS and bake it into an equirect `DataTexture`, then
`scene.environment = tex`. Rules that are bugs when broken:

* **256x128 or larger.** three derives its PMREM cube size as `width / 4`; a
  64x32 bake gives 16-pixel cube faces — technically valid, visually useless.
* **Bake WITHOUT the sun disk** — the disk doubles every specular highlight.
* `tex.mapping = THREE.EquirectangularReflectionMapping;`
  `tex.colorSpace = THREE.SRGBColorSpace;`
* **The orientation bug that blackens water**: three samples v = 1 at the
  ZENITH, and a `DataTexture` defaults to `flipY = false`, so ROW 0 IS STRAIGHT
  DOWN. Fill with `const v = 1 - y / h;` (theta = v * PI from the zenith).
  Inverted, the ground half sits where the sky belongs and every reflective
  surface mirrors the dark half — the reference harness found this in 4 of 9
  delivered scenes; its canal went from near-black to green-with-sun by
  flipping that single line.
* Keep dome and bake the same function, and paint the cloud shapes faintly into
  the bake too, so reflections agree with the sky the camera sees.

## Clouds that read in a still frame

Billboard sprite puffs beat everything else per token:

* ONE shared CanvasTexture: 4-8 overlapping white radial gradients on 128 px.
* Per cloud: 6-15 `THREE.Sprite`s in a flattened ellipsoid (x-spread 2-3x the
  y), scales 8-25 m, opacity 0.35-0.7, bottoms tinted darker,
  `depthWrite: false`.
* Overcast / cirrus: 1-3 huge planes at altitude with
  `alpha = smoothstep(.5, .75, fbm(uv * k + t * dir))`, kept well above every
  camera.
* Raymarched volumetrics only when clouds ARE the subject of the brief.

## Fog adds depth or eats the scene — nothing in between

`FogExp2` density 0.002-0.01 outdoors, always in the sky's hue family. Fog that
softens the far hills sells the depth; fog that hides the mid-ground kills the
frame, and the frame gate reads it as washed out. The shader-trap skill covers
the fog uniforms custom shaders must honour; this one only sets the numbers.

## Order of operations

1. Sky dome + sun direction.  2. JS mirror -> equirect bake ->
`scene.environment`.  3. Lights (see c3d-scene-lighting).  4. Only THEN
materials, water, assets — they are all downstream of the environment.

## Verify before you finish

Render one frame and look at a reflective surface: water or glass mirroring a
BRIGHT sky half means the bake is upright; mirroring darkness at midday means
row 0 went to the zenith. `references/exotic-space.md` holds the validated
black-hole / nebula / planet recipes for space briefs.
