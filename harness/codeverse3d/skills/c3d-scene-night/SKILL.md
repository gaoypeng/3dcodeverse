---
name: c3d-scene-night
description: "Use when a scene_threejs brief says night, dusk, evening, neon, lamplit or moonlit. Night scenes fail two ways: black frames (the gate catches those) and the CHRISTMAS-TREE look - every window a flat glow, light shafts like white tents, twelve competing hues. States what is measured and judged, then the strong options: the library's night windows and neon, glow without postprocessing, fake light pools, the measured shaft ceiling, a wet road with something to mirror, and few light temperatures."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; ACES tone mapping, exposure 1.0.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Ported 2026-09-01 from the scene_multifile_graphics reference ledger; the shaft-opacity ceiling is its measured finding (a normal-blended shaft at 0.3+ rendered as a solid white tent and buried the scene). Pair with c3d-scene-lighting, whose linear-vs-hex table is the other half of every night failure. Not yet A/B-validated here; the sceneloop battery attaches our numbers. Condensed to requirements + options 2026-10-15.'
  verified: "2026-10-15"
  target_metric: "overbright_shaft_findings"
  target_direction: "down"
  target_unit: "solid-reading light shafts per run (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no measured runs yet (2026-09-01); the sceneloop A/B battery sets it"
---

# Night light — few sources, believable glow, no tents

The frame gate stops black frames (`dark_frame`: mean luminance under 0.12 or more than
35 % near-black on an authored camera); this skill is about the opposite failure, a night
lit like a fairground. Read c3d-scene-lighting's linear-vs-hex section first — half of
"my night scene is black" is an albedo 40x darker than its neighbour.

## Emissive windows

Requirement: a lit facade is lit with variation — not every window, not one flat glow —
and the street under it agrees with the windows above (emissive illuminates nothing, so a
facade with no light beside it floats in the dark). Strong option: `makeNightWindows(mesh)`
and `patchWindowInteriors` from `lib/windows.js`, which light a jittered fraction of the
grid with per-window colour; pair each big lit facade with one dim matching PointLight.
`references/window-canvas.md` describes the hand-made canvas version.

## Glow without postprocessing

The renderer's post chain blooms emissives on its own (author them at a peak of a few
units, not twenty). Where you want more, two layered additive sprites per source — a small
bright core plus a large faint halo, both `depthWrite: false` — read as bloom where one
sprite reads as a sticker; the bulb mesh itself is `MeshBasicMaterial` (a lit bulb does not
shade).

## Street lamps — fake the pool, few real lights

WebGL forward lighting costs per fragment per light, and thirty real lights is both slow and
flat. One option: real `PointLight`s (decay 2) on the foreground lamps only, and for the
rest an additive radial-gradient disc on the ground plus a faint gradient cone for the
shaft.

## Light shafts are barely there

Measured in the reference harness: a NORMAL-blended shaft at 0.3+ opacity renders as a
solid white TENT and buries the scene behind it. Additive blending at a few percent opacity,
alpha fading along the cone, is the range that reads; `makeGodRays` from `lib/godrays.js`
does it with its own ceiling. If the shaft reads clearly in a thumbnail, it is too strong.

## Wet road

A near-black road is one of the dark-ground cases the lighting skill warns about: it passes
the band only because it mirrors something. Low roughness, high metalness, and a night
environment bake whose LOWER half carries the window and neon colours (see
c3d-scene-atmosphere for the bake) — `makeMirrorFloor` from `lib/wetground.js` and
`wetten` / `makePuddle` from `lib/rain.js` are the shipped versions. Without a coloured bake
the wet road has nothing to mirror and just goes black.

## Light that agrees with itself

The judge scores the night's light as a whole (`lighting_atmosphere`: "inconsistent
lighting", `thin_atmosphere`: no falloff): every source in frame has to belong to the place —
its lamps, its windows, its signs, its moon or none — and the ground, the air and the
reflections agree with those sources. A moon is one cool directional light at low intensity;
two suns, or a fill with no source in the scene, is what the judge reads as inconsistent.
How many colours the night has is the brief's: a sodium street is one, a market with signs is
many.

## Verify before you finish

One frame: count the distinct light colours; check one lamp's ground pool exists; check no
shaft reads as a solid; check the road mirrors colour, not blackness; read `camera_checks`
for `mean_lum` and `dark_frac` on every authored camera.
