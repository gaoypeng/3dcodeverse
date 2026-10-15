---
name: c3d-scene-lighting
description: "Use when lighting a scene_threejs baseline, env or refine session, or when scene_frames called a frame too dark, blown out or flat. Light the scene so the frame gate passes and the mood still reads. Gives what the frame gate measures (the exposure band every authored camera has to sit in), the one thing hex colours hide - how dark an albedo really is once the renderer works in linear light - and the sky-lit / practical-lit cases with the starter sunRig as one toolbox."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; renderer fixed at ACES Filmic tone mapping, exposure 1.0, sRGB output.
metadata:
  evidence: mixed
  evidence_note: 'The checks and renderer settings are read from live code, namely spatial/frame_metrics.py and runtime_js/lib/browser/renderer.js, and the luminance figures are computed from those settings. The corpus half is thin and labelled as such, being 4 scene_threejs runs in scenes_v1_flash mined 2026-08-25, so it is quoted as examples and never as a rate.'
  verified: "2026-10-15"
  target_metric: "dark_or_flat_frames"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "1.00 mean / 1.0 median findings per run; n=3 (bench/out, last gated round, 2026-08-25); 2.67 on the first gated round"
---

# Lighting a scene the frame gate will accept

## What is measured, and how hard it bites

The harness renders every camera and scores the pixels (`spatial/frame_metrics.py`):

| finding | fires when | severity |
|---|---|---|
| too dark | mean luminance under 0.12 *or* more than 35 % of pixels near-black | ERROR on an authored camera (cap 0.55), WARN on a harness rig view |
| blown out | more than 20 % of pixels pure white | same (cap 0.60) |
| flat frame | one luminance band dominates the frame (an ERROR, cap 0.65, when it holds more than 92 %) | WARN / ERROR |

Two consequences people get wrong:

* **The two dark conditions are independent.** A real finding from `scenes_v1_flash`:
  `frame too dark: mean luminance 0.07, 0% of pixels near black` — a frame with not one
  near-black pixel still failed, because the whole image was dim. Another in the same
  battery: `mean luminance 0.17, 43% of pixels near black` — bright enough on average,
  failed on the black fraction. Fix whichever one the message names.
* **Flat is a contrast failure, not a brightness failure.** Adding light to a flat frame
  usually makes it flatter. Of the 4 scene runs in `scenes_v1_flash` (mined 2026-08-25),
  two produced flat-frame findings and one produced ten separate dark-frame findings.

## Judge dark colours in linear, not in hex

The renderer is fixed: ACES Filmic tone mapping at exposure 1.0, sRGB output
(`runtime_js/lib/browser/renderer.js`). Every hex colour you write is decoded to linear
before any lighting happens, and hex badly understates how dark a dark colour is.

`0x1d3d22` foliage has a relative luminance of **0.037**. `0xf0f4f8` plaster has **0.900**.
That is **24x**, while the raw hex bytes are only about 6x apart. Under one key light, with
everything else equal:

| surface | key x0.5 | key x1 | key x2 |
|---|---|---|---|
| foliage `0x1d3d22` (albedo 0.037) | frame 0.07 | frame **0.15** | frame 0.27 |
| plaster `0xf0f4f8` (albedo 0.900) | frame 0.75 | frame 0.87 | frame 0.94 |

A scene made of dark foliage stays dim **even when it is fully lit**, and doubling
the key only moves it to 0.27. Neither more light nor more exposure changes the ratio — only
a lighter albedo does. The band (mean luminance >= 0.12, under 35 % near-black on every
authored camera) is the requirement; under this renderer a ground albedo under about 0.25
needs the frame's brightness to come from elsewhere — sky, lit walls, water, practicals —
so plan for that if the brief's ground is dark (wet asphalt, a coal yard, a night street).

The same arithmetic says where the headroom is: the tone curve is steep at the dark end —
going from linear 0.02 to 0.05 moves the frame from 0.08 to 0.20. Small increases pay near
the floor.

## Lit from the sky: day, overcast, dusk

* **Tint, do not dim.** Dusk is coloured darkness: change the key's hue and the
  hemisphere's rather than turning the key down, so the frame stays in the band. The
  starter `sunRig` moods (`day | golden | night | overcast`; a set sun is the night rig)
  are tested starting points, not the only triads.
* **The sun agrees with the sky the brief describes** (judged: `unlit_or_blown` "lit from
  a direction that contradicts the sky", `lighting_atmosphere`). On a clear Earth day that
  means white at noon and warm only near the horizon; a dust storm, smoke or another planet
  say otherwise.
* **Shade carries the ambient colour the sky gives it.** Under a blue sky the fill is
  cooler than the sun — a hue gap of around 60° is a range that reads — while an overcast
  day, fog or a single-source night is legitimately close to one hue. The judge's
  `thin_atmosphere` names "the key and fill share one hue" on a day that should not.
* **If there is fog, its colour matches the sky it fades into**, otherwise the ground edge
  reads as a line (`world_edge_visible`); the background is whatever the brief's sky is.

The starter `sunRig({ mood, intensity, fill, sunColor, fillSky, fillGround })` is one
toolbox: its floors clamp requests UP, so under-lighting is not reachable through it, and
its shadow camera is fitted to the `bounds` you pass. Any lighting that keeps every
authored camera in the band passes; a second directional light doubles every shadow, which
the judge reads as light from two suns. If the frame is blown instead: `intensity` at the
floor, `fill` low, sky below white, large emissive surfaces back at the practical level.

## Lit from its practicals: lanterns, lamps, fire, neon, windows

When the brief says the light comes from practicals, the picture is supposed to be dark
between them: that contrast IS the subject. The target is a BAND, not a floor you can fall
through — real contrast with a dim-but-lit mean and little pure black on every authored
camera; the gate reports a dim frame WITH contrast as "dim but lit", a WARN. Both ends of
the band are measured failures, one week apart in the same wave:

* Over the top: adding an AmbientLight or a HemisphereLight to lift a lantern-lit temple
  courtyard's average (2026-08-25) washed dark granite paving to near-white, flattened the
  frame to "snow at dawn", and drowned a KoiWater ShaderMaterial that was running
  correctly the entire time.
* Through the floor: "remove the AmbientLight and the HemisphereLight" applied on its own
  to a neon alley produced mean luminance **0.001-0.022** and an 89 % black frame, because
  **emissive illuminates nothing** — a glowing sign with no light beside it lights the
  frame exactly as much as an unlit one does.

So every lantern, lamp, sign, window or fire near a camera gets its OWN `PointLight` next
to its emissive surface (the starter's `PRACTICAL` numbers in `lib/environment.js` are
one measured setting; `makeNeonTube` + `patchNeonSpill` in `lib/neon.js` do a sign and the
light it throws), a low but non-zero base colour on dark materials so they read as material
and not as void, and shadows left dark. The starter night rig was measured to sit in the
band; added fills push the frame out of it, as the courtyard showed.

One run managed both ends at once: in the same temple scene, the CenserDetail frame was a
near-black silhouette while the overview cameras looked across a pale washed-out ground.
Check the band on EVERY camera, including the harness rig views nobody wrote a shot for.

For a flat frame, add contrast rather than light: a shadow-casting key, materials with
genuinely different albedos, and a camera aimed at content rather than at sky.

## Prove it

`scene_views`, then read `camera_checks` in `metrics.json` per camera: `mean_lum`,
`dark_frac`, `blown_frac`, `modal_frac`. Those are the same four numbers the gate uses, so
there is no reason to guess. A dark harness rig view is only a WARN, but the code that
writes these findings records a measured case where exactly that cost a battery a 0.60 cap
three rounds running — fix it anyway.

Worked numbers, the linear-luminance arithmetic and a per-time-of-day check:
`references/exposure-math.md`.
