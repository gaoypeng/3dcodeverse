---
name: c3d-scene-atmosphere
description: "Use when a scene_threejs env, baseline or refine session builds the sky, the environment map, clouds or fog. The sky is not a backdrop: it is the light source every PBR material reflects. States what has to be true (scene.environment set before the first frame and agreeing with the visible sky; clouds that read as volumes; haze that leaves the midground readable) and how it is read, with the library's sky, cloud and fog modules as the strong options and the one-line equirect orientation bug that blackens water."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; renderer fixed at ACES Filmic tone mapping, exposure 1.0, sRGB output.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Recipes ported 2026-09-01 from the scene_multifile_graphics reference harness, where they carry measured origins: its audit found the equirect bake INVERTED in 4 of 9 delivered scenes (a Venice canal at patch RGB 35,27,18 went to 100,87,43 by flipping one line), and its PMREM note is from three.js source (cube size = width/4). Not yet A/B-validated on this harness; the sceneloop battery attaches our own numbers. Condensed to requirements + options 2026-10-15.'
  verified: "2026-10-15"
  target_metric: "env_bake_orientation_flags"
  target_direction: "down"
  target_unit: "inverted bakes per run (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no measured runs yet (2026-09-01); the sceneloop A/B battery sets it"
---

# Sky, environment and air — the frame's light source

Requirement: if any material reflects (metal, glass, water, wet ground), `scene.environment`
is set before the first frame renders and agrees with the sky the camera sees. The order
you write things in is yours; a reflective surface born before its environment exists
simply mirrors black until it is set.

## The sky

Interface: a drawable is classified **sky** when its name matches sky / skydome / skybox /
stars / clouds / sun / moon / atmosphere and spans more than 50 m (or by size alone past
2000 m) — then it inflates neither `content_frac` nor the overview fit. Whether the brief's
place has a visible sky at all is the brief's (an interior, a cave, deep space).

Strong options: `makeSky(scene, { rig })` from `lib/sky.js` is the shipped dome, lit from the
same `sunRig` as the scene (it keeps a `scene.background` already set, else sets its horizon colour). One of your own is a `BackSide`
sphere (`fog: false`, `depthWrite: false`, built with `makeShaderMaterial` from `lib/shader.js`
so the tonemap chunks stay) whose fragment colour is a function of the view direction: a
horizon-to-zenith gradient, a sun term around `sunDir` (a halo and, if the brief's weather
shows it, a disk), and a haze band that pulls the lowest degrees toward the horizon colour.
The curve shapes and the colours are the brief's sky: a low sun widens the halo and warms the
horizon, an overcast sky is one flat gradient, a night sky hashes the direction for stars
(`makeStars`, `makeAurora` in `lib/celestial.js` do it for you) and shades a moon.

## Bake the SAME sky into `scene.environment`

`sunRig` returns `envTex` baked from its own sky — assign it and the requirement is met.
If you bake your own equirect `DataTexture`, these are bugs when broken:

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

## Clouds

Requirement: if the brief's sky has clouds, they read as soft volumes at the authored
cameras, not as hard-edged cards (judged: `hard_edged_particles`, `materials_shaders_effects`).
Strong options: `makeClouds` / `makeCirrus` from `lib/clouds.js` (one ray-marched deck,
scene-lit); `makeCloudVolume` from `lib/cloudvolume.js` when a cumulus is close enough to
need internal light; or sprite puffs from one shared radial-gradient canvas in a flattened
cluster, bottoms tinted darker, `depthWrite: false`. Volumetrics cost frame time; the brief
decides whether a cloud earns it.

## Haze

If the brief's weather has haze or fog: keep it in the sky's hue family and thin enough that
the midground stays readable — the frame gate reads a washed frame as `flat_frame` (one
luminance band holding > 92 % of an authored frame is an ERROR) and the judge reads no
falloff at all as `thin_atmosphere`. `makeHeightFog` and `patchAerialPerspective` from
`lib/atmosphere.js` do ground fog and distance haze; `scene.fog` alone does the simplest
version. A clear dry day may have almost none. The shader-trap skill covers the fog uniforms
custom shaders must honour.

## Verify before you finish

Render one frame and look at a reflective surface: water or glass mirroring a
BRIGHT sky half means the bake is upright; mirroring darkness at midday means
row 0 went to the zenith. `references/exotic-space.md` holds the validated
black-hole / nebula / planet notes for space briefs.
