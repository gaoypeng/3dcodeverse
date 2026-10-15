---
name: c3d-scene-motion
description: "Use when a scene_threejs session authors or refines animation, or when scene_frames reported no motion or the judge said nothing_moves. Make the scene visibly move between the two frames the harness photographs, and survive the four mechanisms that silently freeze it. Applies to baseline, zone, refine and compose sessions. Covers how motion is measured, the single update hook the harness actually calls, the amplitudes measured to read at 1024 x 576, and why clearing the motion gate is not the same as scoring animation_life - that is judged per item against the plan animation list."
license: Apache-2.0
compatibility: "scene track, language scene_threejs. three r182 in headless Chrome; the harness photographs each camera at t = 0 s and t = 1.5 s and diffs the pair."
metadata:
  evidence: mixed
  evidence_note: "The mechanism and hook are read from live code (spatial/frame_motion.py, spatial/frame_metrics.py, runtime_js/lib/scene_host.mjs) and are exact. The corpus behind them is thin: 3 graded scene runs / 8 judged rounds on scenes_v1_flash, 2026-08-23. Where a number is from that battery it says so."
  verified: "2026-10-15"
  pairs_with: "c3d-scene-composition, c3d-scene-lighting, c3d-threejs-shader-traps"
  target_metric: "min_authored_changed_frac"
  target_direction: "up"
  target_unit: "fraction of pixels changed on the WEAKEST authored camera, t=0 → t=1.5 s"
  target_measurable: "false"
  target_baseline: "0.006 median / 0.017 mean on the weakest authored camera; n=3 (bench/out, 2026-08-25). The gate needs only ONE authored camera to move, so a run whose establishing shot is frozen still passes - which is why this is a guard, not a target"
---

# Motion the harness can measure

## When to use

You are writing or fixing `update(t, dt)` for a scene, or a gate/judge said the
scene is frozen. Read the measurement first; it is not the one you would guess.

## What is actually measured

`animation_life` is **0.431** — the lowest mean of any criterion in any language
in the corpus (8 judged rounds over 3 graded scene runs, `scenes_v1_flash`,
2026-08-23). Since 2026-08-24 the harness no longer leaves that to perception.
`codeverse3d/spatial/frame_motion.py` diffs the first and last frame of every
camera and hands the judge the number:

* the harness counts changed pixels per camera; a camera counts as **MOVING** when
  enough of its frame changes (a lit window or a spark counts)
* only **authored** cameras — the ones you returned from `createScene` — count.
  The harness's own orbit and eye views are measured and shown but never decide
  the verdict
* if no authored camera moves, `scene_frames` emits an **ERROR** whose message is
  "nothing moves: the largest change on an authored camera is N% of pixels …
  (measured, not perceived)", and the judge is told to treat it as fact

So the requirement is concrete: **at least one authored camera visibly changes between
t = 0 and t = 1.5 s.** A few centimetres of sway on a scene tens of metres wide
does not do it.

## Clearing the bar is necessary, and it is not sufficient

Re-measuring the stored frames of all 8 recorded rounds with today's code
(2026-08-25) gives an uncomfortable result: **every one of them cleared the bar.**
The best authored camera changed 2.1 %–4.7 % of its pixels in every round, and
`animation_life` still came out between 0.1 and 0.8, averaging 0.431.

The reason is in the judge's own words on three of those rounds: "the planned
animations for water and falling leaves are completely static", "the planned
vertex animation for the plant foliage and string lights" is missing. The scene
moved; the things the plan promised did not.

`codeverse3d/tracks/scene.py::judge_context` hands the judge your plan's
`animation` list verbatim, item by item, alongside the measured motion table. So
the criterion is scored per item, not globally:

* **Every named item in the plan's animation list is visibly moving on an
  authored camera.** One mover clears the gate; it does not discharge a
  four-item animation plan.
* **The amplitude written in the plan is intent, not a spec.** That rooftop
  scene's plan asked for "sway with sine displacement amplitude 0.035 m" and
  "oscillate amplitude 0.015 m" on a scene tens of metres wide — 3.5 cm and
  1.5 cm are invisible at this resolution and were duly scored as absent. Keep
  the motion the plan describes; scale the number until the measurement says it
  reads.

## The four ways a scene with animation code still scores zero

Every one of these is a mechanism in `runtime_js/lib/scene_host.mjs`, not a
matter of taste. Three of the four leave your animation code visibly present in
the file, which is why they survive a refine round untouched: on four of the
eight recorded rounds, across two different scenes, the judge wrote that the
planned animation was absent while the code for it sat in `src/`.

1. **`requestAnimationFrame` is a no-op stub.** The host replaces it with a
   counter that returns 0 and never calls back. A render loop written the normal
   browser way runs exactly zero times. The lint also rejects the call outright.
   There is no loop; there is only `update`.
2. **`update` is whatever `createScene` returned, and nothing else.** If your
   returned object has no `update` function the boot records "createScene().update
   (t, dt) is not a function (scene will be static)" and moves on — the build
   still passes. A hook named `userData.tick`, `tickEnv` or `animate` is never
   found by anything: the harness calls exactly one function, and it is your job
   to make that function fan out to every mover.
3. **One throw disables animation for the whole run.** `runUpdate` catches the
   exception, sets `updateBroken`, logs "update() disabled, rendering continues
   without animation" and never calls your function again. The first call happens
   at t = 1/30 s, so a single undefined dereference on the very first step
   freezes every later frame of every camera. `scene_probe` reports this as
   `update_ok: false` with the message — check it before you conclude the
   amplitude was too small.
4. **The motion is off camera.** Only authored cameras decide it. A drifting boat
   behind the establishing camera is worth nothing; put the motion in the
   frames the authored cameras look at.

## The one hook

Interface: `createScene({ THREE, renderer, loaders })` returns `{ scene, cameras,
update(t, dt) }`; the host calls that one `update` and nothing else, so it has to reach
every mover you create. How you structure modules is yours; one way is a list walked
in `update`:

```js
const movers = [env, ...zones].filter(Boolean);          // built once, at scene build
return { scene, cameras, update(t, dt) {
  for (const m of movers) (m.userData?.update ?? m.update)?.(t, dt);
} };
```

Add a mover to the list in the same edit that creates it — a zone whose
`userData.update` nobody walks is failure mode 2 wearing a disguise. Library effects
(`makeFire`, `makeSmoke`, `makeOceanSurface`, `makeFlag` …) carry their own
`userData.update(t, dt)`; call each once from the zone that owns it, and anything built
with `patchStandard` is driven by one `tickShaders(scene, t)` in `scene.js`.

## Pose from absolute `t`

The contract requires `update` to be deterministic: the same `t` gives the same
frame. The host does step the simulation in fixed 1/30 s increments and does pass
you a real `dt`, so `+=` will not desynchronise — but it makes every pose depend
on how many steps ran, which is exactly the thing a refine round changes by
accident. Write the pose, do not integrate it:

```js
pivot.rotation.z = Math.sin(t / PERIOD * 6.283 + phase) * AMP;      // yes
leaf.position.y  = home.y - ((t * FALL_SPEED + phase) % CYCLE);     // yes
pivot.rotation.z += dt * SPEED;                                      // no
```

Two things that have made the difference register in the pair of frames:

* **Starting mid-phase.** `Math.sin(0) = 0`, so a pure sine puts every sway at rest
  in the t = 0 photograph and the scene reads dead in the very frame the judge
  looks at first; a phase offset per element avoids that.
* **A phase per instance** from your seeded `mulberry32`. A hundred trees sharing one
  phase is one big object nodding, which both looks wrong and cancels itself out in the
  pixel diff when half the crowd moves into the space the other half left.

`Math.random()`, `Date.now()` and `performance.now()` are forbidden by the contract — a
non-deterministic scene makes the two frames incomparable.

## Amplitudes that read

What moves is the plan's animation list. These ranges have been measured to read at
1024 x 576 between the two photographs; below them an item is scored as absent:

| what moves | amplitude over 1.5 s | period |
|---|---|---|
| foliage / banner sway | 0.10–0.20 rad (6–11 degrees) at the pivot | 1.6–3 s |
| grass, reeds | 0.15 rad, phase-offset per instance | 1.2–2 s |
| water | wave height at least 0.04 m **and** a scrolling uv/normal at 0.15 m/s | 2–4 s |
| falling leaves, petals, snow, dust | at least 0.6 m of travel | recycle over 6–12 s |
| flame / lantern flicker | intensity x0.75 to x1.3, plus `emissiveIntensity` | 0.1–0.3 s |
| vehicles, boats, birds | at least 1.5 m of travel | loop |

Where the pivot sits changes what the measure sees: a group whose origin is at the
base of the trunk or the top of the flagpole swings its far end through the frame,
while a mesh rotated about its own centre moves half as many pixels for the same
angle.

## Prove it before you finish

Call `scene_views` (its default is `times=[0, 1.5]`) and read the measured
motion table it prints — it names each authored camera and its changed fraction.
Then do the harder check the table cannot do for you: put the two sheets side by
side and walk the plan's animation list item by item, pointing at each one in the
picture. An item you cannot point at is the one the judge will name. If every
authored camera reads STATIC, or an item is invisible, double the amplitude or
move the mover into frame. Do not ship on the strength of having written the
code — and if nothing at all moved, run `scene_probe` first: a frozen scene is
more often a thrown `update` than a small amplitude.

## People, vehicles, weather, fire

Requirement: weather and fire read as the phenomenon at the authored cameras and not as
hard-edged quads or discs (judged: `hard_edged_particles`); a particle cloud moves or it
is a decal. Options that read:

* **A walking figure**: `figure` + `walk` from `lib/figure.js`; or your own hierarchy
  with pivots AT the joints, legs opposite-phase from `t`, a per-person seed on phase and
  speed so a crowd is not in lockstep — frozen mid-stride reads perfectly.
* **Paths**: `alongPath` / `route` / `crowdOn` in `lib/place.js`, or a `CatmullRomCurve3`
  sampled at `(t * speed / len + u0) % 1`, facing the tangent; distinct `u0` offsets space
  the traffic.
* **Rain** as elongated streaks (`makeRain`, `makeRainVeil` at distance) — elongation is
  the motion cue; **snow** as soft sprites with per-flake sway (`makeSnowfall`); **motes**
  and pollen from `makeMotes`; every particle gets its own phase so a frozen frame is
  mid-motion, never at a synchronized extreme.
* **Fire additive, smoke normal**: `makeFire` / `makeFireField` with a flickering light,
  `makeSmoke` / `makeSteam` as a bounded density — a chain of spheres is not steam.

## Depth

`references/motion_recipes.md` — the sway pivot, a recycling particle field, the
scrolling-water uniform and the mover-registration pattern, short enough to adapt.
