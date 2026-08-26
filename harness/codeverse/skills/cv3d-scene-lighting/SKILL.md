---
name: cv3d-scene-lighting
description: Light a three.js scene so the frame gate passes and the mood still reads. Use in a scene baseline, env or refine session, and on any round where scene_frames called a frame too dark, blown out or flat. Gives the exact thresholds the gate measures, the key / fill / practical recipe with numbers, and the one thing hex colours hide - how dark an albedo really is once the renderer works in linear light.
license: Apache-2.0
compatibility: three r0.182, headless WebGL; renderer fixed at ACES Filmic tone mapping, exposure 1.0, sRGB output.
metadata:
  evidence: mixed
  evidence_note: 'Thresholds and renderer settings are read from live code, namely spatial/frame_metrics.py and runtime_js/lib/browser/renderer.js, and the luminance figures are computed from those settings. The corpus half is thin and labelled as such, being 4 scene_threejs runs in scenes_v1_flash mined 2026-08-25, so it is quoted as examples and never as a rate.'
  verified: "2026-08-25"
---

# Lighting a scene the frame gate will accept

## What is measured, and how hard it bites

The harness renders every camera and scores the pixels (`spatial/frame_metrics.py`):

| finding | fires when | severity |
|---|---|---|
| too dark | mean luminance < **0.12** *or* > **35 %** of pixels near black | ERROR on an authored camera, WARN on a harness rig view |
| blown out | > **20 %** of pixels pure white | same |
| flat frame | one luminance band holds > **85 %** of pixels (> **92 %** makes it an ERROR on an authored camera) | WARN / ERROR |

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

A scene made of dark foliage sits on the gate line **when it is fully lit**, and doubling
the key only moves it to 0.27. Neither more light nor more exposure changes the ratio — only
a lighter albedo does. So: lift the albedos of the large surfaces first, then light. Ground
albedo below about 0.25 is a scene that cannot pass without heroic lighting.

The same arithmetic says where the headroom is: mean frame luminance 0.12 corresponds to a
linear scene luminance of only ~0.03, and the tone curve is steep down there — going from
linear 0.02 to 0.05 moves the frame from 0.08 to 0.20. Small increases pay near the floor.

## The recipe

* **Tint, do not dim** — for DUSK, and for a night lit from the sky. Dusk is coloured
  darkness, so change the key's hue and the hemisphere's rather than turning the key down.
  Take the whole row from the cookbook's time-of-day table rather than inventing a triad.
* **But a PRACTICAL-lit night really is dim, and fill will ruin it.** When the brief says
  the light comes from lanterns, lamps, fire, neon or windows, the picture is supposed to
  be dark between them: that contrast IS the subject. Adding AmbientLight or a
  HemisphereLight to lift the average is the single most destructive thing you can do to
  such a scene — measured 2026-08-25 on a lantern-lit temple courtyard, the added fill
  washed dark granite paving to near-white, flattened the whole frame to "snow at dawn",
  and drowned a KoiWater ShaderMaterial that was running correctly the entire time. Light
  it from its own practicals instead: emissive on the lamp, a PointLight at each one, a
  low but non-zero base colour on dark materials so they read as material and not as void,
  and shadows left dark. The gate now tells these two cases apart — a dim frame WITH
  contrast (`lum_std` >= 0.12) is reported as "dim but lit" and is only a WARN.
* **The sun is white at noon and warm only near the horizon.** A warm sun at high elevation
  reads as an error, not as mood.
* **The fill is the opposite hue from the key.** Hemisphere light is sky colour above,
  ground bounce below; keep its colour at least 60 degrees of hue from the sun's. Warm key
  plus warm fill is the monochrome-orange soup that gets marked down.
* **Emissive is a material, not a light.** An emissive surface glows in the image and
  illuminates nothing — and the default pipeline renders without bloom, so it must look
  right unaided. Pair every lantern, window or fire with a small PointLight.
* **Fog colour is the sky's horizon colour**, never white or grey unless the brief says
  overcast, and never a black background.

Working numbers for a scene lit from the SKY (day, overcast, dusk), straight from the
gate's fix hint: DirectionalLight **2-4**, HemisphereLight **0.5-1.0**,
`emissiveIntensity` **2-6** with a PointLight **0.5-2** per practical, and keep mean
luminance **>= 0.15** so you are not sitting on the threshold. If the frame is blown
instead: key <= 3, hemisphere <= 1.0, sky below 0.9 white, `emissiveIntensity` <= 4 on
large surfaces.

For a scene lit from its PRACTICALS the target is contrast, not average — but it is a
BAND, not a floor you can fall through. Aim at `lum_std` **>= 0.12** with mean luminance
**0.15-0.28** and under 10 % pure black on every authored camera. One dim moon/sky key at
**<= 0.2** for shape in the shadows, at most one low ambient **0.06-0.12**, and no
HemisphereLight and no second DirectionalLight.

Both ends of that band are measured failures, one week apart in the same wave:
* Over the top: chasing the sky-lit ">= 0.15 mean" in a lantern-lit courtyard produced a
  flat grey wash whose granite paving read as snow and whose working water shader was
  invisible.
* Through the floor: "remove the AmbientLight and the HemisphereLight" applied on its own
  to a neon alley produced mean luminance **0.001-0.022** and an 89 % black frame. The
  reason is the rule three bullets up — **emissive illuminates nothing**. A glowing sign
  with no PointLight beside it lights the frame exactly as much as an unlit one does.

One run managed both ends at once, which is the clearest sign that "darker" is not the
instruction: in the same temple scene, the CenserDetail frame was a near-black silhouette
where only the censer's glowing interior was legible, while the overview cameras looked
across a pale washed-out ground plane. Under-lit where it was art-directed, over-lit where
it was not. Check the band on EVERY camera, including the harness rig views nobody wrote
a shot for — that is where a scene quietly keeps the failure you thought you had fixed.

So removing fill is only half the instruction and is destructive alone. Every lantern,
lamp, sign, window or fire needs its OWN `PointLight` next to its emissive surface —
`decay: 2`, `distance` 12-18 m, `intensity` 6-14, and enough of them (10-16 in a courtyard
or a street) that the lit pools overlap into a readable picture. The practicals are the
lighting rig; the emissive is only what the fixture looks like.

For a flat frame, add contrast rather than light: a shadow-casting key, materials with
genuinely different albedos, and a camera aimed at content rather than at sky.

## Prove it

`scene_views`, then read `camera_checks` in `metrics.json` per camera: `mean_lum`,
`dark_frac`, `blown_frac`, `modal_frac`. Those are the same four numbers the gate uses, so
there is no reason to guess. A dark harness rig view is only a WARN, but the code that
writes these findings records a measured case where exactly that cost a battery a 0.60 cap
three rounds running — fix it anyway.

Code and the full palette table: cookbook `scene_threejs` sections *Dusk / night lighting
recipe*, *Atmosphere: time-of-day triads with numbers*.

Worked numbers, the linear-luminance arithmetic and a per-time-of-day check:
`references/exposure-math.md`.
