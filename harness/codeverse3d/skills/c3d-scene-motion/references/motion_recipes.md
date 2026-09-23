# Motion recipes

Executed against the harness's own `three@0.182` in node on 2026-08-25; the
printed values are quoted under each block.

## The seeded hash you need first

`Math.random()` is forbidden (a non-deterministic scene makes the two photographs
incomparable, and the lint warns on it).

```js
const rand = (i) => { const s = Math.sin(i * 12.9898 + 78.233) * 43758.5453; return s - Math.floor(s); };
```

## Sway pivot — base-anchored, phase-seeded, mid-phase at t = 0

```js
function swayPivot(THREE, child, { name = 'Sway', amp = 0.14, period = 2.2, seed = 0 } = {}) {
  const pivot = new THREE.Group(); pivot.name = name; pivot.add(child);
  const phase = rand(seed) * 6.2832 + 0.8;      // + 0.8 so t = 0 is NOT the rest pose
  pivot.userData.update = (t) => {
    pivot.rotation.z = Math.sin(t / period * 6.2832 + phase) * amp;
    pivot.rotation.x = Math.sin(t / (period * 1.37) * 6.2832 + phase * 1.7) * amp * 0.55;
  };
  return pivot;
}
// seed 3, amp 0.14: rotation.z moves 0.209 rad between t = 0 and t = 1.5, and
// sits at -0.069 rad in the t = 0 frame rather than dead upright.
```

Three things are load-bearing and all three are cheap:

* the pivot Group's origin is at the **base** of what sways, so the far end
  travels; the child geometry is translated up into it, not centred on it
* the two axes use incommensurate periods (`period` and `period * 1.37`) so the
  motion never repeats exactly and never looks like a metronome
* `+ 0.8` in the phase means the t = 0 photograph catches the element already
  leaning. A pure `Math.sin(t * k)` is exactly zero at t = 0, which is the frame
  the judge sees first.

Wrap each instance with its own `seed` (its index in the scatter loop). A hundred
trees on one phase is one large object nodding — and worse for the pixel diff,
symmetrical crowds partly cancel: half the copies move into the pixels the other
half vacated.

## Falling particles — recycled, pure function of t

```js
function fallingField(THREE, n, area, { fall = 0.9, cycle = 8, seed = 0 } = {}) {
  const pos = new Float32Array(n * 3), base = new Float32Array(n * 3), ph = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    base[i*3]   = area.x + (rand(i + seed) - 0.5) * area.w;
    base[i*3+1] = area.top;
    base[i*3+2] = area.z + (rand(i + seed + 600) - 0.5) * area.d;
    ph[i] = rand(i + seed + 900) * cycle;          // stagger, or they fall as one sheet
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const pts = new THREE.Points(geo, new THREE.PointsMaterial({ size: 0.05 }));
  pts.name = 'Leaves';
  pts.userData.update = (t) => {
    const p = geo.attributes.position.array;
    for (let i = 0; i < n; i++) {
      const u = ((t + ph[i]) % cycle) / cycle;     // wraps; no state accumulates
      p[i*3]   = base[i*3]   + Math.sin((t + ph[i]) * 0.8) * 0.35;
      p[i*3+1] = base[i*3+1] - u * fall * cycle;
      p[i*3+2] = base[i*3+2] + Math.cos((t + ph[i]) * 0.6) * 0.35;
    }
    geo.attributes.position.needsUpdate = true;     // without this nothing moves at all
  };
  return pts;
}
// 160 particles, fall 0.9 m/s: a leaf travels 1.35 m between t = 0 and t = 1.5,
// and calling update(1.5) twice returns the identical value.
```

The modulo is what makes recycling stateless: no counter to drift, no reset to
miss, and `update(1.5)` gives the same frame however many times it is called.
Allocate `pos`, `base` and `ph` once at build time — the contract forbids
per-frame allocation and `traverse` in `update`.

## Registering movers

```js
// in each zone / asset builder
zone.userData.update = (t, dt) => { /* ... */ };

// in src/scene.js — ONE list, built once, walked by the one hook the harness calls
const movers = [env, ...zones, ...assets].filter(Boolean);
return { scene, cameras, update(t, dt) { for (const m of movers) (m.userData?.update ?? m.update)?.(t, dt); } };
```

Add to `movers` in the same edit that creates the mover. The harness calls
nothing else: not `userData.tick`, not `animate`, not a `requestAnimationFrame`
callback (that function is a stub here that returns 0 and never fires).

## Scrolling a shader without a clock of its own

A `ShaderMaterial` animates by having its `uTime` uniform written from the same
`update`, never by reading a clock inside itself (`Date.now` and
`performance.now` are forbidden, and the render happens at a simulated time, not
a wall-clock one).

```js
const water = makeWaterMaterial(THREE, { /* ... */ });   // uniforms: { uTime: { value: 0 }, ... }
group.userData.update = (t) => { water.uniforms.uTime.value = t; };
```

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
