---
name: cv3d-scene-composition
description: Lay out a three.js scene so the harness's deterministic instruments agree with the picture — one height function everything samples, a world that does not end in shot, and cameras computed from measured bounds instead of guessed. Includes the sky/ground/content classifier that silently decides two gate findings, and the exact scene_frames thresholds and score caps for camera placement and frame coverage. Use in any scene_threejs session that owns the environment, a zone's placement, or the camera list, and on any repair round where scene_frames reported a camera problem.
license: Apache-2.0
compatibility: track scene, language scene_threejs (three r182, headless Chrome, the harness builds the cameras from your plain camera objects).
metadata:
  evidence: mixed
  evidence_note: "Gate thresholds, score caps and the backdrop classifier are read from live code (spatial/frame_metrics.py, judges/rubrics/scene_v1.yaml, runtime_js/lib/backdrop.mjs, runtime_js/lib/scene_host.mjs). The corpus behind them is thin: only 3 graded scene runs exist, so every count below names its n."
  verified: "2026-08-25"
  corpus: "3 graded scene_threejs runs with gate artefacts, mined 2026-08-25"
---

# Composing a scene the instruments can read

**Read this if** you are writing `env.js`, placing a zone, or returning the `cameras`
array. Two of the three graded scene runs in this corpus carry a composition issue and it is
the same one in both — a visible world edge — and one of those two also placed three
authored cameras below the ground. Both failures are arithmetic, not taste.

## Build in this order

1. **Bounds** — read `BOUNDS` from the plan. Everything below is derived from its
   horizontal diagonal `diag`.
2. **One height function.** `heightAt(x, z)` in `env.js` is the only terrain authority.
   The ground mesh samples it, every zone samples it, every camera samples it. A zone that
   invents its own ground level is how props end up buried and cameras end up underground.
3. **Ground**, then **horizon**, then zones, then cameras. Cameras last, because they are
   computed from what exists.

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

Three consequences you must design around:

* **A wide flat slab is ground whatever you call it.** A 50 m plaza deck or a big pond is
  classified ground, so it stops counting as content, and its *top* raises `ground_y`.
* **`ground_y` is the top of every ground mesh unioned**, so a raised terrace or a hill sets
  the floor that camera heights are checked against. In this corpus a garden reported
  `ground_y = 6.21 m` and three authored cameras placed at absolute eye height came back as
  `camera is 1.0 / 4.6 / 4.8 m below the ground level` — three ERRORs in one run.
* **Instanced scatter always counts as content.** The cheapest way to lift a thin
  establishing shot over the 20% content floor is more instanced planting and props, not a
  bigger ground plane (which counts against you).

## Cameras are measured, and the numbers are hard

`codeverse/spatial/frame_metrics.py` runs on the authored cameras, where every finding is an
**ERROR** (the harness's own orbit rig only gets WARNs). `judges/rubrics/scene_v1.yaml`
turns those ERRORs into score ceilings:

| condition | constant | finding | cap on the run |
|---|---|---|---|
| nearest surface < 0.5 m, or the camera is inside a mesh box | `NEAR_HIT_M` | `camera inside / touching geometry` | **0.50** |
| eye below ground and the frame corroborates it (dark, empty or one-colour) | — | `camera_underground` | **0.50** |
| establishing shot with content < 20% of the pixels | `CONTENT_MIN_ESTABLISHING` | `establishing shot shows too little content` | **0.60** |
| any authored shot with content < 10% | `CONTENT_MIN_AUTHORED` | `shot shows little content` | WARN |
| eye less than 0.3 m above ground | `EYE_MIN_ABOVE_GROUND_M` | `ant's-eye view` | WARN |
| eye more than 80 m above ground | `EYE_MAX_ABOVE_GROUND_M` | `satellite view, not a shot` | WARN |
| one luminance band holds > 85% of the frame | `FLAT_MODAL_FRAC` | `flat frame` | WARN, ERROR and **0.65** past 92% |

So compute, never guess:

```js
const y = heightAt(x, z) + 1.6;                     // human shot: terrain FIRST, then eye height
const d = Math.max(size.x, size.z);                 // measured content span, sky dome excluded
const establishing = { name: 'Establishing', fov: 42,
  position: [c.x + d * 0.55, size.y * 1.1 + 6, c.z + d * 0.6], lookAt: [c.x, 2, c.z] };
```

Two notes that save a wasted repair round:

* `camera is N m below the scene's highest ground surface` is downgraded to a **WARN** when
  the frame itself renders fine — that usually means a hill or a raised bed reaches above a
  camera standing in the open, not a buried camera. Check the frame before you move it.
* Do **not** set `near` or `far`. `runtime_js/lib/scene_host.mjs` computes
  `far = max(1000, farthest scene-bbox corner * 1.25 + 10)` from the *whole* scene, so a
  distant horizon ring or sky dome is never clipped.

## The world must not end in shot

This is the most repeated judge complaint on the track and **no gate catches it** — all 4
composition issues recorded across the 3 graded scene runs (2 major, 2 minor, mined
2026-08-25) are the same sentence: *a hard world edge is visible where the ground plane ends
and meets the sky*. The arithmetic that kills it, all derived from `diag`:

```
fogScale   = clamp(diag / 100, 0.3, 2.5)          // the cookbook's fog table is for 100 m
fogFar     = fogScale * (the time-of-day row's far)
groundSize >= 2.4 * fogFar                        // ground reaches past the fog
ringRadius  = max(0.6 * fogFar, 2 * (diag / 2))   // silhouette ring, 24-40 solid shapes
skyRadius   > ringRadius                          // the dome has to contain the ring
fog colour == the sky's horizon colour            // or the ground ends AT the sky, not IN it
```

Solid shapes, never plane billboards, and never hand-tint the ring toward the fog colour —
`scene.fog` already does that by distance, and doing it twice produces the pale cardboard
the judge grades as a placeholder material. Code: `read_cookbook(section="Horizon: the world
must not end")` and `read_cookbook(section="Atmosphere: time-of-day triads with numbers")`
for the fog row.

## What you have already been given, and what you have not

`env` sessions receive the *Ground that reads real*, *Horizon*, *Atmosphere* and *Dusk /
night lighting* chapters inlined in the prompt. **Zone, compose and refine sessions do
not** — if you own a zone or the camera list, this skill plus `read_cookbook` is the whole
of your guidance on the horizon and on camera placement.

## Verify before you finish

`scene_views` renders the authored cameras and prints `camera_checks` per camera —
`mean_lum`, `modal_frac`, `content_frac`, `nearest_hit_m`, `eye_height_m`, `ground_y`. Read
those six numbers against the table above rather than judging the thumbnails by eye, and
`scene_probe` for triangles, draw calls and fps. A camera that fails the table fails it
deterministically; there is nothing to argue with.

Worked derivations — bounds to fog to ring to the three cameras, and the coverage-repair
checklist — are in `references/measured-cameras.md`.
