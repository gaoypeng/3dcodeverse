# Motion — what the measure needs, and the library's movers

The measure: each authored camera is rendered at t = 0 and t = 1.5 s and the two frames are
differenced; a camera MOVES when ≥ 0.4 % of its pixels change by more than 8/255 (or ≥ 0.08 %
by more than 60/255). None moving is the gate's `no_motion` ERROR and the judge's
`nothing_moves`. Everything below is one way to clear it; the library's own movers
(`makeFalling` in `lib/flowers.js`, `makeSnowfall` / `makeMotes` in `lib/veils.js`,
`makeFlock` in `lib/flock.js`, `makeFlag` in `lib/cloth.js`, `patchWind` in `lib/foliage_shade.js`)
cover the common cases without any code of
yours, and each carries `userData.update(t, dt)` for the zone to call.

## Seeded randomness

`Math.random()` is forbidden (a non-deterministic scene makes the two photographs
incomparable, and the lint warns on it). `mulberry32` from `lib/noise.js` is the shipped
generator (`lehmer` is the lighter one); one call per instance at build time gives each its
phase.

## A sway that registers

What has measured as visible (`three@0.182` in node, 2026-08-25): a pivot group whose origin
is at the BASE of what sways (the child translated up into it, not centred on it), a rotation
written as a pure function of `t` — a sine over the period the plan gives, with a per-instance
phase from the seeded generator so the t = 0 photograph catches the element already leaning —
and a second axis at an incommensurate period so the motion never reads as a metronome. At
0.14 rad amplitude a trunk swung 0.209 rad between the two frames and sat 0.069 rad off
upright in the first one; the plan's amplitude table says what reads for each kind of mover.
A hundred trees on one phase is one large object nodding, and symmetrical crowds partly
cancel in the pixel diff.

## Falling particles — recycled, a pure function of t

Allocate the position, home and phase arrays once at build time (the contract forbids
per-frame allocation and `traverse` in `update`); each frame then sets every particle's
position from `t` alone — the fall is `(t + phase) mod cycle` scaled by the speed, with a
small lateral drift from the same clock — and sets `geometry.attributes.position.needsUpdate
= true` (without it nothing moves at all). The modulo is what makes recycling stateless: no
counter to drift, no reset to miss. 160 particles at 0.9 m/s travelled 1.35 m between the
frames, and calling `update(1.5)` twice returned the identical value. The `PointsMaterial`
needs a radial-alpha sprite `map` with `transparent: true` and `depthWrite: false`, or every
particle is a square (`hard_edged_particles`, judged).

## Registering movers

A zone's `group.userData.update = (t, dt) => { ... }` is the only zone hook the assembled
`src/scene.js` calls, and it calls it once per frame; an asset's own `userData.update` runs
only if its zone caches it at build time and calls it from there. The harness calls nothing
else: not `userData.tick`, not `animate`, not a `requestAnimationFrame` callback (that
function is a stub here that returns 0 and never fires). Register a mover in the same edit
that creates it.

## Scrolling a shader without a clock of its own

A `ShaderMaterial` animates by having its `uTime` uniform written from the same `update`,
never by reading a clock inside itself (`Date.now` and `performance.now` are forbidden, and
the render happens at a simulated time, not a wall-clock one): `mat.uniforms.uTime.value = t`
from the zone's hook.

Two failure shapes to avoid: a uniform declared in the shader but missing from
`uniforms` (`runtime_js/lib/host_compile.mjs` reports that as an `unbound_uniform`
ERROR, and `glsl_audit.mjs` catches it statically), and a `uTime` that is
written but multiplied by so small a factor that the surface is identical at
t = 0 and t = 1.5. For water the readable combination is a vertical wave of at
least 4 cm **and** a uv/normal scroll of at least 0.15 m/s — a mirror-flat pond
that only changes its specular highlight does not clear the pixel bar.

## Reading the verdict

`scene_views` prints the measured table under the contact sheet, one line per
camera:

```
Establishing [authored]: 3.7% of pixels changed between t=0s and t=1.5s (max delta 243/255) — MOVING
```

`MOVING` on an authored camera is the whole of the gate. It is not the whole of
`animation_life`: that is scored against the plan's animation list, item by item.
