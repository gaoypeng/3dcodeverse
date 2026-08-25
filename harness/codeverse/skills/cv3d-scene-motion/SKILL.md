---
name: cv3d-scene-motion
description: "Make a scene_threejs scene visibly move between the two frames the harness photographs, and survive the four mechanisms that silently freeze it. Use in any scene session that authors or refines animation — baseline, zone, refine or compose — and on every repair round where the scene_frames gate reported no motion or the judge answered nothing_moves. Covers the measured motion threshold, the single update hook the harness actually calls, the amplitudes that read at 1024 x 576, and why clearing the motion gate is not the same as scoring animation_life — that is judged per item against the plan animation list."
license: Apache-2.0
compatibility: "scene track, language scene_threejs. three r182 in headless Chrome; the harness photographs each camera at t = 0 s and t = 1.5 s and diffs the pair."
metadata:
  evidence: mixed
  evidence_note: "The mechanism, thresholds and hook are read from live code (spatial/frame_motion.py, spatial/frame_metrics.py, runtime_js/lib/scene_host.mjs) and are exact. The corpus behind them is thin: 3 graded scene runs / 8 judged rounds on scenes_v1_flash, 2026-08-23. Where a number is from that battery it says so."
  verified: "2026-08-25"
  pairs_with: "cv3d-scene-composition, cv3d-scene-lighting, cv3d-threejs-shader-traps"
  target_metric: "min_authored_changed_frac"
  target_direction: "up"
  target_unit: "fraction of pixels changed on the WEAKEST authored camera, t=0 → t=1.5 s"
  target_measurable: "false"
  target_baseline: "0.006 median / 0.017 mean on the weakest authored camera; n=3 (bench/out, 2026-08-25). The gate needs only ONE authored camera over the 0.4 pct bar, so a run whose establishing shot is frozen at 0.13 pct still passes - which is why this is a guard, not a target"
---

# Motion the harness can measure

## When to use

You are writing or fixing `update(t, dt)` for a scene, or a gate/judge said the
scene is frozen. Read the measurement first; it is not the one you would guess.

## What is actually measured

`animation_life` is **0.431** — the lowest mean of any criterion in any language
in the corpus (8 judged rounds over 3 graded scene runs, `scenes_v1_flash`,
2026-08-23). Since 2026-08-24 the harness no longer leaves that to perception.
`codeverse/spatial/frame_motion.py` diffs the first and last frame of every
camera and hands the judge the number:

* a **pixel** counts as changed when any channel differs by more than 8 of 255
* a camera counts as **MOVING** when at least **0.4 %** of its pixels changed, or
  when 0.08 % of them changed by more than 60 of 255 (a lit window, a spark)
* only **authored** cameras — the ones you returned from `createScene` — count.
  The harness's own orbit and eye views are measured and shown but never decide
  the verdict
* if no authored camera moves, `scene_frames` emits an **ERROR** whose message is
  "nothing moves: the largest change on an authored camera is N% of pixels …
  (measured, not perceived)", and the judge is told to treat it as fact

So the target is concrete: **one authored camera over 0.4 % of changed pixels
between t = 0 and t = 1.5 s.** On a 1024 x 576 frame that is about 2,360 pixels —
roughly a 50 x 50 patch. A few centimetres of sway on a scene tens of metres wide
does not reach it.

## Clearing the bar is necessary, and it is not sufficient

Re-measuring the stored frames of all 8 recorded rounds with today's code
(2026-08-25) gives an uncomfortable result: **every one of them cleared the bar.**
The best authored camera changed 2.1 %–4.7 % of its pixels in every round — five
to twelve times the threshold — and `animation_life` still came out between 0.1
and 0.8, averaging 0.431.

The reason is in the judge's own words on three of those rounds: "the planned
animations for water and falling leaves are completely static", "the planned
vertex animation for the plant foliage and string lights" is missing. The scene
moved; the things the plan promised did not.

`codeverse/tracks/scene.py::judge_context` hands the judge your plan's
`animation` list verbatim, item by item, alongside the measured motion table. So
the criterion is scored per item, not globally:

* **Every named item in the plan's animation list must be visibly moving on an
  authored camera.** One hero mover clears the gate; it does not discharge a
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
   behind the establishing camera is worth nothing; put the hero motion in the
   frame the first camera looks at.

## The shape that works

```js
export async function createScene({ THREE, renderer, loaders }) {
  const scene = new THREE.Scene();
  const env = buildEnv(THREE, scene);
  const zones = [buildGrove(THREE, ctx), buildHarbour(THREE, ctx)];
  scene.add(...zones);
  const movers = [env, ...zones].filter(Boolean);
  return {
    scene, cameras,
    update(t, dt) {
      for (const m of movers) (m.userData?.update ?? m.update)?.(t, dt);
    },
  };
}
```

One list, built once, walked by the single hook the harness calls. Add a mover to
the list in the same edit that creates it — a zone whose `userData.update` nobody
walks is failure mode 2 wearing a disguise.

## Pose from absolute `t`

The contract requires `update` to be deterministic: the same `t` gives the same
frame. The host does step the simulation in fixed 1/30 s increments and does pass
you a real `dt`, so `+=` will not desynchronise — but it makes every pose depend
on how many steps ran, which is exactly the thing a refine round changes by
accident. Write the pose, do not integrate it:

```js
// yes
pivot.rotation.z = Math.sin(t / PERIOD * 6.283 + phase) * AMP;
leaf.position.y  = home.y - ((t * FALL_SPEED + phase) % CYCLE);
// no
pivot.rotation.z += dt * SPEED;
```

Two more rules that follow from the pair of frames:

* **Start mid-phase.** `Math.sin(0) = 0`, so a pure sine puts every sway at rest
  in the t = 0 photograph and the scene reads dead in the very frame the judge
  looks at first. Give each element a phase offset, or drive it with
  `Math.sin(t / P * 6.283 + 1.0)`.
* **Seed the phase per instance.** `phase = rand(i) * 6.283` from your seeded
  hash. A hundred trees sharing one phase is one big object nodding, which both
  looks wrong and cancels itself out in the pixel diff when half the crowd moves
  into the space the other half left.

Never use `Math.random()`, `Date.now()` or `performance.now()` — the contract
forbids all three, and a non-deterministic scene makes the two frames
incomparable.

## Amplitudes that clear 0.4 %

Give the scene **one unmistakable hero motion** — a turning wheel, a drifting
boat, falling leaves, a rotating beam, a flock — and then give every remaining
item on the plan's animation list an amplitude from this table. The hero clears
the gate; the rest of the list is what the criterion is scored against.

| what moves | amplitude over 1.5 s | period |
|---|---|---|
| foliage / banner sway | 0.10–0.20 rad (6–11 degrees) at the pivot | 1.6–3 s |
| grass, reeds | 0.15 rad, phase-offset per instance | 1.2–2 s |
| water | wave height at least 0.04 m **and** a scrolling uv/normal at 0.15 m/s | 2–4 s |
| falling leaves, petals, snow, dust | at least 0.6 m of travel, 60–250 particles | recycle over 6–12 s |
| flame / lantern flicker | intensity x0.75 to x1.3, plus `emissiveIntensity` | 0.1–0.3 s |
| vehicles, boats, birds | at least 1.5 m of travel | loop |

Pivot placement matters as much as amplitude: rotate a group whose origin is at
the **base** of the trunk or the **top** of the flagpole, so the far end swings.
Rotating the mesh about its own centre moves half as many pixels for the same
angle.

## Prove it before you finish

Call `scene_views` (its default is `times=[0, 1.5]`) and read the measured
motion table it prints — it names each authored camera and its changed fraction.
Then do the harder check the table cannot do for you: put the two sheets side by
side and walk the plan's animation list item by item, pointing at each one in the
picture. An item you cannot point at is the one the judge will name. If your best
authored camera is under 0.4 %, or an item is invisible, double the amplitude or
move the mover into frame. Do not ship on the strength of having written the
code — and if nothing at all moved, run `scene_probe` first: a frozen scene is
more often a thrown `update` than a small amplitude.

## Depth

`references/motion_recipes.md` — the sway pivot, the recycling particle field,
the scrolling-water uniform and the mover-registration pattern, each as code you
can paste and adapt.
