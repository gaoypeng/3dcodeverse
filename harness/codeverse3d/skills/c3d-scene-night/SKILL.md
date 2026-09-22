---
name: c3d-scene-night
description: "Use when a scene_threejs brief says night, dusk, evening, neon, lamplit or moonlit. Night scenes fail two ways: black frames (the gate catches those) and the CHRISTMAS-TREE look - every window a flat glow, light shafts like white tents, twelve competing hues. Gives the emissive-window recipe, glow without postprocessing, fake light pools, the measured shaft opacity, wet-road numbers, and the discipline of two light temperatures."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; ACES tone mapping, exposure 1.0.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Ported 2026-09-01 from the scene_multifile_graphics reference ledger; the shaft-opacity ceiling is its measured finding (a normal-blended shaft at 0.3+ rendered as a solid white tent and buried the scene). Pair with c3d-scene-lighting, whose linear-vs-hex table is the other half of every night failure. Not yet A/B-validated here; the sceneloop battery attaches our numbers.'
  verified: "2026-09-01"
  target_metric: "overbright_shaft_findings"
  target_direction: "down"
  target_unit: "solid-reading light shafts per run (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no measured runs yet (2026-09-01); the sceneloop A/B battery sets it"
---

# Night light — few sources, believable glow, no tents

The frame gate stops black frames; this skill is about the opposite failure:
night scenes lit like a fairground. Real night is ONE or TWO light temperatures
and a lot of dark. Read c3d-scene-lighting's linear-vs-hex section first —
half of "my night scene is black" is an albedo 40x darker than its neighbour.

## Emissive windows (buildings at night)

Per building ARCHETYPE (not per building), one CanvasTexture: dark facade,
window grid, ~35% of the rects lit warm with jitter, a few TV-blue. Use it as
`map` AND `emissiveMap`, `emissiveIntensity` 1-2. Emissive does NOT illuminate
anything else — pair each big lit facade or sign with one dim matching
PointLight so the street below agrees with the window above.

## Glow without postprocessing

Two layered additive sprites per source: a small bright core plus a large faint
halo at ~0.15 opacity, both `depthWrite: false`; the bulb mesh itself is
`MeshBasicMaterial` (a lit bulb does not shade). One sprite reads as a sticker;
the two-layer stack is what reads as bloom.

## Street lamps — fake the pool, budget the real lights

The pool of light on the ground is an additive radial-gradient disc ON the
ground plus a faint gradient cone for the shaft. Real `PointLight`s (decay 2)
are a budget: 4-8 for foreground lamps, no more — WebGL forward lighting costs
per-fragment per-light, and thirty real lights is both slow and flat.

## Light shafts are barely there

Additive blending, opacity 0.03-0.08, alpha fading along the cone. The
reference harness measured the failure: a NORMAL-blended shaft at 0.3+ opacity
renders as a solid white TENT and buries the scene behind it. If the shaft
reads clearly in a thumbnail, it is too strong.

## Wet road (the night city's best friend)

Near-black road, `roughness 0.12-0.25`, `metalness ~0.8`, and a night
environment bake whose LOWER half carries the window/neon colours (the road
mirrors what is above it — see c3d-scene-atmosphere for the bake). Puddles:
a few irregular darker planes at `roughness 0.03`. Without the coloured bake
the wet road has nothing to mirror and just goes black.

## Discipline: two temperatures

Pick 1-2 dominant temperatures — sodium orange + cool white is the classic —
plus RARE neon accents. Every additional hue divides the frame's coherence.
The moon is one slate-blue DirectionalLight, not a second sun.

## Verify before you finish

One frame: count the distinct light colours (more than 3 is a fairground);
check one lamp's ground pool exists; check no shaft reads as a solid; check
the road mirrors colour, not blackness.
