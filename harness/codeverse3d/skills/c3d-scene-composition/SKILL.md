---
name: c3d-scene-composition
description: "Use when a scene_threejs session owns the environment, a zone's placement or the camera list, or when scene_frames reported a camera problem. States what the harness's deterministic instruments require of a composition - one height function everything samples, a world that does not end in shot, cameras whose eye, clearance and coverage are measured - and how each is measured, so the brief's own place can satisfy them in its own way. Includes the sky/ground/content classifier that silently decides two gate findings, the camera score caps, and a menu of horizon treatments."
license: Apache-2.0
compatibility: track scene, language scene_threejs (three r182, headless Chrome, the harness builds the cameras from your plain camera objects).
metadata:
  evidence: mixed
  evidence_note: "Camera checks, score caps and the backdrop classifier are read from live code (spatial/frame_metrics.py, judges/rubrics/scene_v2.yaml, runtime_js/lib/backdrop.mjs, runtime_js/lib/scene_host.mjs). The corpus behind them is thin: only 3 graded scene runs exist, so every count below names its n."
  verified: "2026-10-15"
  corpus: "3 graded scene_threejs runs with gate artefacts, mined 2026-08-25; 60 argon iteration-0 scenes re-read 2026-10-15 for the sameness audit"
  target_metric: "camera_placement_findings"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "0.00 findings per run; n=3 (bench/out, last gated round, 2026-08-25); 0.33 on the first gated round, so the repair loop already clears it"
---

# Composing a scene the instruments can read

**Read this if** you are writing `env.js`, placing a zone, or returning the `cameras`
array. The harness measures a composition in a handful of numbers; this sheet says which
numbers, where they come from, and leaves the picture to the brief. Nothing here is a
layout: the 60 argon iteration-0 scenes (2026-10-15) all came out as one block on a flat
plate ringed by hills because every session built the same recipe, and that is what the
judge marks as a toy.

## What has to be true, in whatever order you build it

* **The plan's bounds** are the content box: the harness clamps its own overview rig to them
  and the placement gate measures planned assets against them (`out_of_bounds`, a WARN past
  max(5 m, 10 % of the span)). Nothing in `env.js` restates them for the harness — it reads
  only `heightAt`, `buildEnv` and the sun-azimuth export (the contract) from that file. They are where the content
  lives, not where the world stops: the world the authored cameras see continues past them.
* **One height function.** `heightAt(x, z)` in `env.js` is the only ground authority: the
  ground mesh samples it, every zone seats on it, every camera's eye is set from it. A zone
  that invents its own ground level is how props end up buried and cameras underground.
* **The world does not end in shot.** From every authored camera nothing shows the ground
  ending or an empty horizon line (judged: `world_edge_visible`); the placement report
  measures outdoor standing geometry reaching past the bounds (`world_edge`, a WARN) and fog
  reaching past the content (`fog_short`, `no_fog`). See the horizon menu below.
* **Cameras are derived from measured content**, not guessed (the table below).

## The classifier nobody tells you about

`runtime_js/lib/backdrop.mjs` sorts every drawable into **sky / ground / content** from its
*name* and its *world box shape*, and that classification decides `content_frac` (the
coverage gate) and `ground_y` (the camera-height gate). The rules, in order:

| rule | class |
|---|---|
| name matches sky, skydome, skybox, stars, clouds, sun, moon, atmosphere **and** span > 50 m | sky |
| span > 2000 m, or height > 300 m with span > 300 m | sky |
| any `InstancedMesh` | **content** (always) |
| name matches ground, terrain, floor, water, ocean, sea, lake, river, plane, sand, grass, land **and** span > 20 m and height < 6% of span | ground |
| span > 40 m and height < 2% of span (any name) | ground |
| everything else | content |

Three consequences to design around:

* **A wide flat slab is ground whatever you call it.** A 50 m plaza deck or a big pond is
  classified ground, so it stops counting as content, and its *top* raises `ground_y`.
* **`ground_y` is the top of every ground mesh unioned**, so a raised terrace or a hill sets
  the floor that camera heights are checked against. In this corpus a garden reported
  `ground_y = 6.21 m` and three authored cameras placed at absolute eye height came back as
  `camera is 1.0 / 4.6 / 4.8 m below the ground level` — three ERRORs in one run.
* **`content_frac` counts drawables classified content.** A bigger ground plane does not
  raise it; what raises it in a thin shot is whatever the brief's place has in that view
  (buildings, trees, people, boats, instanced cover — the brief decides which).

## Cameras are measured, and the numbers are hard

`codeverse3d/spatial/frame_metrics.py` runs on the authored cameras, where every finding is an
**ERROR** (the harness's own orbit rig only gets WARNs). `judges/rubrics/scene_v2.yaml`
turns those ERRORs into score ceilings:

| condition | constant | finding | cap on the run |
|---|---|---|---|
| nearest surface < 0.5 m, or the camera is inside a mesh box | `NEAR_HIT_M` | `camera inside / touching geometry` | **0.50** |
| eye below ground and the frame corroborates it (dark, empty or one-colour) | — | `camera_underground` | **0.50** |
| the first camera (measured as the establishing shot) shows < 20 % content | — | `establishing shot shows too little content` | **0.60** |
| any authored shot that is almost all sky/ground | — | `shot shows little content` | WARN |
| eye less than 0.3 m above ground | `EYE_MIN_ABOVE_GROUND_M` | `ant's-eye view` | WARN |
| eye more than 80 m above ground | `EYE_MAX_ABOVE_GROUND_M` | `satellite view, not a shot` | WARN |
| one luminance band dominates the frame | — | `flat frame` | WARN; ERROR and **0.65** when it holds nearly all of it |

So compute the eye from the terrain and aim each camera at the subject the brief gives it:

```js
const y = heightAt(x, z) + EYE;   // terrain FIRST, then the eye height the brief's shot has
```

Where a camera stands, how many there are and what each frames is the brief's. A strong
option for the establishing shot is `establishingShot` from `lib/place.js`, which takes the
hero object and derives distance and height from its measured box; `seat` and `shot` in
the same module place a camera or a prop on the surfaces you pass. Whatever you use, the
first camera in the array is the one measured as establishing.

Two notes that save a wasted repair round:

* `camera is N m below the scene's highest ground surface` is downgraded to a **WARN** when
  the frame itself renders fine — that usually means a hill or a raised bed reaches above a
  camera standing in the open, not a buried camera. Check the frame before you move it.
* Do **not** set `near` or `far`. `runtime_js/lib/scene_host.mjs` computes
  `far = max(1000, farthest scene-bbox corner * 1.25 + 10)` from the *whole* scene, so a
  distant backdrop or sky dome is never clipped.

## The world must not end in shot — a menu, not a recipe

The requirement: from every authored camera, nothing shows the ground ending or an empty
horizon line (`world_edge_visible`, judged; all 4 composition issues across the 3 graded
runs mined 2026-08-25 were that sentence). Any ground mesh extends past the fog's far
distance along every authored view, or something stands between the camera and its edge.
How the world continues is the brief's place. Strong options, each on its own enough:

* **Terrain that keeps going** — `makeOutskirts` from `lib/environment.js` carries the
  land from the content out to the fog with seam-matched relief, fields and wooded
  clusters; `ground` from `lib/terrain.js` with its `relief` option is the simpler version.
* **A ridge or mesa on ONE side**, with the opposite side falling away to fog or water.
* **A treeline** (`makeImposters` from `lib/woodland.js` at distance) or an orchard edge.
* **A far skyline** — `cityFabric` from `lib/building.js`, a harbour wall, a viaduct.
* **A shoreline**: `makeOceanSurface` or `makeOcean` to the horizon on one or more sides.
* **Fog only**, when the brief's weather is thick enough that the ground fades before it
  ends (`makeHeightFog`, `patchAerialPerspective` from `lib/atmosphere.js`).
* **Walls, cliffs, or an interior** with no horizon at all (`roomShell`, `cliff`).

A ring of near-identical copies stamped evenly round the world is measured
(`stamped_ring`, the placement report) and judged `monotonous_vegetation`; whatever closes
the horizon has to read as that place's far distance at the authored cameras' range, not as
a placeholder or a flat card (`placeholder_material`, judged). If you use fog, a fog colour
that matches the horizon it fades into keeps the ground edge from reading as a line;
`scene.fog` already blends distant materials toward it, so pre-tinting a backdrop toward
the fog colour doubles the haze and renders as pale cardboard.

## Depth that reads

`empty_midground` (judged) is an authored frame with empty regions the brief's place would
fill; what fills them — and at which distances — is the brief's. Two measured limits on the
near field: the gate needs `nearest_hit_m` >= 0.5 m, and no more than 5 of the 9 sight rays
may end within 1.5 m (`camera_blocked`). Scatter is blind, so keep each authored camera's
first metres clear of it — a conifer planted on the lens turns the money shot into bark.

## Verify before you finish

`scene_views` renders the authored cameras and prints `camera_checks` per camera —
`mean_lum`, `modal_frac`, `content_frac`, `nearest_hit_m`, `eye_height_m`, `ground_y`. Read
those six numbers against the table above rather than judging the thumbnails by eye, and
`scene_probe` for triangles, draw calls and fps. A camera that fails the table fails it
deterministically; there is nothing to argue with.

`references/measured-cameras.md` holds the finding-to-fix table and the naming rules the
classifier reads.
