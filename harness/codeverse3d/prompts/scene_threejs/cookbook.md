# Scene cookbook — multi-file three.js scenes (r182), headless-rendered by the harness

Every `js` snippet runs as-is in node (the harness test suite concatenates them with
`THREE` and the named addons in scope).  Y-up, +Z front, meters.  GLSL goes through
`lib/shader.js` — use its `makeShaderMaterial(opts)` for every custom shader.
The harness inlines the relevant chapters into your prompts; the full file is at
`.3dcode/cookbook.md` in your workspace.

**The chapters that decide the score.**  Scenes on this track lose most of their points to
five repeatable defects, one chapter each:

| the judge writes | read this chapter |
|---|---|
| "flat ground plane", "one colour", "no paths" | Ground that reads real |
| "a hard world edge is visible against the sky" | Horizon: the world must not end |
| "monotonous / identical cones", "primitive stacked shapes" | Vegetation that reads real · Rocks, cliffs and boulders |
| "sparse", "a diorama on an empty plane", "no midground" | Scene layering · Set dressing |
| "flat light", "too dark", "monochrome", "jagged shadows" | Atmosphere: time-of-day triads |
| "frames at t=0 and t=1.5 are identical" | Motion you can SEE between t = 0 and t = 1.5 s |

## Environment (`src/env.js`): sun + sky + fog + ground — the scene's floor comes first

```js
const BOUNDS = { min: [-50, 0, -50], max: [50, 30, 50] };     // export const in real env.js
const rand = mulberry32(7);        // lib/noise.js: the seeded PRNG — never Math.random

function heightAt(x, z) {
  // deterministic analytic terrain — EVERY placement must call this, or things float
  return 0.8 * Math.sin(x * 0.09) * Math.cos(z * 0.07) + 0.35 * Math.sin((x * 0.31 + z * 0.23));
}

function buildEnv({ THREE, scene }) {
  // fog + matching background: the single biggest depth cue
  scene.fog = new THREE.Fog(0xa8c4dd, 45, 160);               // near ≈ bounds/2, far ≈ 1.6 × diagonal
  scene.background = new THREE.Color(0xa8c4dd);
  // sky dome gradient (cheap, always works; the Sky addon is the fancy alternative)
  const skyGeo = new THREE.SphereGeometry(240, 24, 12);
  const skyMat = makeShaderMaterial({                         // lib/shader.js: tonemap + colorspace included
    name: 'SkyDome', side: THREE.BackSide, fog: false, depthWrite: false,
    uniforms: { uTop: { value: new THREE.Color(0x4e7fb8) }, uHorizon: { value: new THREE.Color(0xd8e4ec) } },
    varyings: 'varying vec3 vP;', vertexMain: '  vP = position;',
    fragmentHead: 'uniform vec3 uTop; uniform vec3 uHorizon;',
    fragmentMain: '  float h = clamp(normalize(vP).y, 0.0, 1.0); gl_FragColor = vec4(mix(uHorizon, uTop, pow(h, 0.55)), 1.0);',
  });
  const sky = new THREE.Mesh(skyGeo, skyMat); sky.name = 'SkyDome'; scene.add(sky);
  // lights: ONE sunRig (lib/environment.js) — key, hemisphere fill, the environment map,
  // the disc, and a shadow camera, map size and bias it fits to `bounds` itself
  const rig = sunRig({ mood: 'day', azimuth: 35, elevation: 48, bounds: 50 });
  scene.add(rig.sun, rig.sun.target, rig.fill);
  if (rig.sunDisc) scene.add(rig.sunDisc);
  scene.environment = rig.envTex;
  // ground displaced by heightAt, vertex-coloured by slope/height (no textures)
  const g = new THREE.PlaneGeometry(100, 100, 128, 128); g.rotateX(-Math.PI / 2);
  const pos = g.attributes.position, col = new Float32Array(pos.count * 3);
  const grass = new THREE.Color(0x5d7c3a), dirt = new THREE.Color(0x77644a), c = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const y = heightAt(pos.getX(i), pos.getZ(i));
    pos.setY(i, y);
    c.copy(dirt).lerp(grass, THREE.MathUtils.clamp(0.5 + y * 0.5, 0, 1));
    col.set([c.r, c.g, c.b], i * 3);
  }
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.computeVertexNormals();
  const ground = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1.0 }));
  ground.name = 'Ground'; ground.receiveShadow = true; scene.add(ground);
  return { update(t, dt) {} };
}
```

Numbers: the rig's own per-mood intensities (tint it, do not add lights); `bounds` = the
content radius, which fits the shadows; fog near ≈ half the bounds, far ≈ 2 × the diagonal; ground ≈ 1
segment per metre.  Full palettes per time of day are in the time-of-day chapter.

## The exact contract the harness assembler enforces

`src/scene.js` is **written by the harness**, not by you — it is regenerated from your
`src/env.js` + `src/zones/*.js` and overwrites anything you put there during the stages.
That generated file is the only caller of your modules, so these five signatures are the
whole interface.  Get one wrong and your module is silently dropped or your animation
never runs.

```js
// This is (verbatim) what the assembler generates and calls:
//
//   const ctx = { THREE, scene, renderer, loaders, heightAt, assets };
//   const env = (await buildEnv(ctx)) || {};        //  src/env.js  -> ONE argument: ctx
//   ctx.env = env;
//   const g = await build(ctx);                     //  src/zones/<snake>.js -> export name is `build`
//   if (!g || !g.isObject3D) throw ...              //  must return a Group/Object3D
//   scene.add(g);
//   function update(t, dt) {
//     if (env.update) env.update(t, dt);            //  env animation ONLY via env.update
//     for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
//   }                                              //  zone animation ONLY via userData.update
//
// So:
//   src/env.js            export function buildEnv(ctx) -> { ..., update(t, dt) }   (+ heightAt, BOUNDS,
//                                                                                    SUN_AZIMUTH_DEG, MATS)
//   src/zones/<snake>.js  export function build(ctx) -> THREE.Group with .userData.update
//   src/assets/<snake>.js export function build<Asset>(THREE, opts) -> THREE.Group   (called by zones)
//
// `tickEnv`, `userData.tick`, `buildEnv(THREE, scene)` and `build<Zone>(ctx)` are NEVER called.
// An asset's own hook is only run if its zone forwards it (pattern below).
function assemblerContractDemo(THREE) {
  const zone = new THREE.Group(); zone.name = 'Demo';
  const asset = new THREE.Group();                       // an asset that animates itself
  asset.userData.update = (t) => { asset.rotation.y = t * 0.8; };
  zone.add(asset);
  const movers = [asset];                                // cache ONCE at build time
  zone.userData.update = (t, dt) => { for (const a of movers) a.userData.update(t, dt); };
  return zone;
}
const contractDemo = assemblerContractDemo(THREE);
contractDemo.userData.update(1.5, 0.016);
console.log('assembler contract demo rotated to', contractDemo.children[0].rotation.y.toFixed(2));
```

`SUN_AZIMUTH_DEG` (a plain `export const`, degrees) is read out of `src/env.js` **by
regex** and used to place the harness's overview cameras on the sun side.  Export it or
the overview rig guesses 45° and may shoot into the sun.

If the assembler is unavailable the harness asks you to write `src/scene.js` by hand: then
write exactly the code in the comment above — `createScene({THREE, renderer, loaders})`
returning `{ scene, cameras, update }`, cameras as plain `{name, position, lookAt, fov}`
objects, animation fanned out through `env.update` and `zone.userData.update`.

## Ground that reads real (blend, paths, edges — never one flat colour)

A single-colour plane is the #1 reason a scene reads as a toy.  Real ground is **three or
four materials blended by height, slope and noise**, with paths carved into the height
function itself and dressed edges where anything meets it.

```js
// --- deterministic value noise (no textures, no Math.random) -----------------
const gHash = (x, z) => { const s = Math.sin(x * 127.1 + z * 311.7) * 43758.5453; return s - Math.floor(s); };
function gNoise(x, z) {                                  // smooth, C1, cheap
  const xi = Math.floor(x), zi = Math.floor(z), fx = x - xi, fz = z - zi;
  const u = fx * fx * (3 - 2 * fx), v = fz * fz * (3 - 2 * fz);
  return (gHash(xi, zi) * (1 - u) + gHash(xi + 1, zi) * u) * (1 - v)
       + (gHash(xi, zi + 1) * (1 - u) + gHash(xi + 1, zi + 1) * u) * v;
}
function gFbm(x, z) { return 0.6 * gNoise(x, z) + 0.3 * gNoise(x * 2.1, z * 2.1) + 0.1 * gNoise(x * 4.3, z * 4.3); }

// --- the path is part of heightAt, so everything placed on it lands on it -----
const PATH = [[-26, -20], [-9, -7], [4, 2], [17, 15], [26, 26]];   // polyline in meters
const PATH_HALF_W = 0.9;                                            // 1.8 m wide path
function distToPath(x, z) {
  let best = 1e9;
  for (let i = 0; i < PATH.length - 1; i++) {
    const [ax, az] = PATH[i], [bx, bz] = PATH[i + 1];
    const vx = bx - ax, vz = bz - az, wx = x - ax, wz = z - az;
    const t = Math.max(0, Math.min(1, (wx * vx + wz * vz) / (vx * vx + vz * vz)));
    const dx = wx - t * vx, dz = wz - t * vz;
    best = Math.min(best, Math.sqrt(dx * dx + dz * dz));
  }
  return best;
}
function terrainHeight(x, z) {
  const base = 1.1 * gFbm(x * 0.035, z * 0.035) - 0.55 + 0.25 * Math.sin(x * 0.08) * Math.cos(z * 0.06);
  const d = distToPath(x, z);
  const flat = 1 - Math.min(1, Math.max(0, (d - PATH_HALF_W) / 2.2));   // 1 on the path → 0 at 3.1 m
  return base * (1 - 0.85 * flat) - 0.05 * flat;                        // path is flat and slightly sunken
}

// --- colour by height + slope + noise + path ----------------------------------
const GROUND_PALETTE = {
  grass:  0x54702f,   // flat, low, wet
  dry:    0x8a8148,   // flat, high
  dirt:   0x6b5334,   // moderate slope
  rock:   0x6e6a63,   // steep slope
  path:   0xa89a7c,   // gravel / packed earth
};
function buildBlendedGround(THREE, opts = {}) {
  const size = opts.size ?? 160, seg = opts.seg ?? 160, h = opts.heightAt ?? terrainHeight;
  const P = Object.assign({}, GROUND_PALETTE, opts.palette);
  const g = new THREE.PlaneGeometry(size, size, seg, seg); g.rotateX(-Math.PI / 2);
  const pos = g.attributes.position, col = new Float32Array(pos.count * 3);
  const grass = new THREE.Color(P.grass), dry = new THREE.Color(P.dry), dirt = new THREE.Color(P.dirt);
  const rock = new THREE.Color(P.rock), path = new THREE.Color(P.path), c = new THREE.Color();
  const e = size / seg;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i), y = h(x, z);
    pos.setY(i, y);
    const slope = Math.min(1, (Math.abs(h(x + e, z) - y) + Math.abs(h(x, z + e) - y)) / (e * 1.2));
    const n = gFbm(x * 0.35, z * 0.35);                       // breaks every band into patches
    c.copy(grass).lerp(dry, THREE.MathUtils.clamp(0.35 + y * 0.5 + (n - 0.5) * 0.9, 0, 1));
    c.lerp(dirt, THREE.MathUtils.smoothstep(slope, 0.12, 0.45));
    c.lerp(rock, THREE.MathUtils.smoothstep(slope, 0.45, 0.9));
    const onPath = 1 - THREE.MathUtils.smoothstep(distToPath(x, z), PATH_HALF_W, PATH_HALF_W + 1.4);
    c.lerp(path, onPath * (0.75 + 0.25 * n));
    c.offsetHSL(0, 0, (n - 0.5) * 0.05);                       // last 5 % of tonal break-up
    col.set([c.r, c.g, c.b], i * 3);
  }
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.computeVertexNormals();
  const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1.0, metalness: 0.0 }));
  mesh.name = 'Ground'; mesh.receiveShadow = true;
  return mesh;
}
const groundDemo = buildBlendedGround(THREE, { size: 120, seg: 120 });
console.log('ground verts', groundDemo.geometry.attributes.position.count, 'coloured', !!groundDemo.geometry.attributes.color);
```

**Edge dressing.**  Where anything meets the ground (fence, wall, deck, raised bed) sink it
3–5 cm in AND scatter 6–15 small pieces (tufts, pebbles, leaves, moss) along the seam;
raised platforms get a chamfered kerb, never a naked box edge.

```js
function dressSeam(THREE, { path, n = 24, radius = 0.35, spread = 0.5, color = 0x4f6b2c, heightAt = terrainHeight, seed = 3 }) {
  // path = [[x, z], ...] the seam polyline; returns ONE InstancedMesh of tufts hugging it
  const geo = new THREE.IcosahedronGeometry(radius, 0);
  const mesh = new THREE.InstancedMesh(geo, new THREE.MeshStandardMaterial({ color, roughness: 1, flatShading: true }), n);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), p = new THREE.Vector3(), s = new THREE.Vector3();
  for (let i = 0; i < n; i++) {
    const u = (i + 0.5) / n * (path.length - 1), k = Math.min(path.length - 2, Math.floor(u)), f = u - k;
    const x = path[k][0] + (path[k + 1][0] - path[k][0]) * f + (gHash(seed + i, 1) - 0.5) * spread;
    const z = path[k][1] + (path[k + 1][1] - path[k][1]) * f + (gHash(seed + i, 2) - 0.5) * spread;
    const sc = 0.5 + 0.9 * gHash(seed + i, 3);
    q.setFromAxisAngle(up, gHash(seed + i, 4) * 6.283);
    p.set(x, heightAt(x, z) - radius * 0.35 * sc, z); s.set(sc, sc * 0.6, sc);
    m.compose(p, q, s); mesh.setMatrixAt(i, m);
  }
  mesh.instanceMatrix.needsUpdate = true; mesh.computeBoundingSphere();
  mesh.name = 'SeamTufts'; mesh.castShadow = false; mesh.receiveShadow = true;
  return mesh;
}
console.log('seam tufts', dressSeam(THREE, { path: [[-6, -6], [6, -6], [6, 6]] }).count);
```

Numbers: ground segments ≈ 1 per metre (160 × 160 for 160 m); ≥ 3 blended colours, each
with ≥ 8 % of the visible area; path 1.2–2.0 m wide; a ground plane that is one flat
colour, or a raised bed that is a sharp box on top of grass, are both graded as defects
(`flat_ground`, `undressed_scene`).

## Horizon: the world must not end (fixing `world_edge_visible`)

The single most repeated judge complaint on this track is *"a hard world edge is visible
against the sky"*.  Five rules kill it for good — and rules 4 and 5 are what separate a
hazy horizon from *"plain untextured beige cut-outs"*, which scores worse than no backdrop:

1. **Ground reaches past the fog.**  Ground size ≥ `2.4 × fogFar`.  Detail only needs to
   exist inside the bounds; beyond that, one big low-resolution disc is enough.
2. **Fog colour == sky colour at the horizon.**  Then the ground fades into the sky
   instead of ending at it — and `fogFar` must be scaled to the scene (see the
   time-of-day chapter: the table is for a 100 m scene, multiply by `diag / 100`).
3. **A silhouette ring** at `radius ≈ 0.6 × fogFar` (and ≥ 2 × the bounds half-diagonal):
   24–40 low-poly hills / treeline / rooftops / mesa blocks, height 0.06–0.12 × radius.
   Measured on a 45 m garden with fog 16/77 m: a ring at 46 m reads as distant hills, the
   same ring at 95 m reads as a spiky crater wall, at 20 m as a stone circle.
4. **Never hand-tint the backdrop toward the fog colour.**  `scene.fog` already blends
   every fogged material toward the fog colour by distance; lerping the material as well
   double-applies it, and against a *light* fog (a golden or overcast horizon) it makes the
   backdrop BRIGHTER than the terrain — which renders as pale cardboard and is graded
   `placeholder_material`.  Give it its natural colour **darkened**
   (`base.multiplyScalar(0.6–0.8)`) and let fog do the haze.  Verified A/B on one frame
   with fog 0xf7cf9e: hand-lerped = beige cut-outs, darkened = receding hazy hills.
5. **Solid geometry, never flat cut-outs.**  A distant treeline is cones/boxes with volume;
   a `PlaneGeometry` billboard reads as cardboard from every oblique and overview camera,
   and its base floats.

```js
function buildHorizonRing(THREE, opts = {}) {
  const r = opts.radius ?? (opts.fogFar ? opts.fogFar * 0.6 : 60);    // 0.6 x fog far
  const n = opts.count ?? 30, h0 = opts.minHeight ?? r * 0.06, h1 = opts.maxHeight ?? r * 0.12;
  const base = new THREE.Color(opts.color ?? 0x3d5236), fog = new THREE.Color(opts.fogColor ?? 0xa8c4dd);
  const dark = opts.darken ?? 0.72;      // scene.fog supplies the haze; we only darken
  const shape = opts.shape ?? 'hill';        // 'hill' (cone) | 'block' (city) | 'mesa'
  const geo = shape === 'hill' ? new THREE.ConeGeometry(1, 1, 7)
            : shape === 'mesa' ? new THREE.CylinderGeometry(0.75, 1, 1, 6)
            : new THREE.BoxGeometry(1, 1, 1);
  geo.translate(0, 0.5, 0);                  // origin at the base
  // hazeMix stays 0 unless the ring sits past fog.far, where fog can no longer reach it
  const mat = new THREE.MeshStandardMaterial({ color: base.clone().multiplyScalar(dark).lerp(fog, opts.hazeMix ?? 0), roughness: 1, flatShading: true });
  const mesh = new THREE.InstancedMesh(geo, mat, n);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), p = new THREE.Vector3(), s = new THREE.Vector3();
  for (let i = 0; i < n; i++) {
    const a = (i + 0.35 * gHash(i, 7)) / n * Math.PI * 2;
    const rr = r * (0.82 + 0.3 * gHash(i, 11));
    const hgt = h0 + (h1 - h0) * gHash(i, 13);
    const wid = hgt * (1.1 + 1.6 * gHash(i, 17));
    q.setFromAxisAngle(up, gHash(i, 19) * 6.283);
    p.set(Math.cos(a) * rr, -1.5, Math.sin(a) * rr); s.set(wid, hgt, wid);
    m.compose(p, q, s); mesh.setMatrixAt(i, m);
  }
  mesh.instanceMatrix.needsUpdate = true; mesh.computeBoundingSphere();
  mesh.name = 'HorizonRing'; mesh.castShadow = false; mesh.receiveShadow = false;
  return mesh;                                // add to the SCENE (env), never to a zone
}
const ringDemo = buildHorizonRing(THREE, { shape: 'block', color: 0x2b3140, fogFar: 77 });
console.log('horizon ring', ringDemo.count, 'r', (77 * 0.6).toFixed(1), '#' + ringDemo.material.color.getHexString());
```

Indoor scenes (greenhouse, cabin) need no ring — but the *outside* seen through the glass
must still be something: a haze gradient plus 5–10 silhouette shapes.

## Dusk / night lighting recipe (dark scenes are the #1 gate failure)

"Dusk", "night", "moonlit", "lantern-lit" means COLOURED darkness, never black.  The harness
measures every camera's frame; a frame that is too dark is a gate ERROR
(`scene_frames: dark_frame`) capping the score at 0.55.  Take the `dusk` / `night`
row of the time-of-day table below for sky, sun, fill and fog, then add **practicals** —
every lantern, window and fire is an emissive surface plus a small PointLight:

```js
function addPracticals(THREE, scene, spots) {          // none cast shadows
  const glowMat = new THREE.MeshStandardMaterial({ color: 0xffc070, emissive: 0xffa040, emissiveIntensity: 3.0 });
  return spots.map(([x, y, z], i) => {
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.15, 12, 8), glowMat);
    bulb.position.set(x, y, z); bulb.name = `Practical_${i}`;
    const light = new THREE.PointLight(0xffa040, 1.5, 9, 2); light.position.set(x, y, z);
    scene.add(bulb, light); return light;              // flicker from update(): ×0.75…×1.3
  });
}
console.log('practicals', addPracticals(THREE, new THREE.Scene(), [[4, 1.8, 2], [-3, 2.1, 5]]).length);
```

Rules of thumb: the starter's `env.js` lights the scene with ONE `sunRig({...})` from
`lib/environment.js` — key, hemisphere fill, the environment map metals read from, the disc
and fitted shadows — and clamps `intensity` / `fill` UP to per-mood floors (day and golden
sun ≥ 4.5 with fill ≥ 1.0–1.2, overcast 2.0 / 1.6, the night moon 2.2 / 0.8); turning the key
down is NOT how you make dusk, tinting it is (`sunColor`, `fillSky`, `fillGround`, `zenith`,
`horizon`, and a set sun — `elevation` < 0 — is the night rig); ground albedo ≥ 0.25; no
black `scene.background` (the sky gradient's horizon band is the brightest thing in the frame).

**Exposure self-check (after each build):** `scene_views` → the frame table
(`camera_checks` in metrics.json) for every authored camera: read `mean_lum` / `dark_frac` /
`blown_frac` / `content_frac` and any finding it names.  If a frame is dark, raise `fill` by
+0.3 and `intensity` by +0.8 in the `sunRig({...})` call and re-render — never a second
DirectionalLight beside the rig (measured 2026-09-07: a blue-hour lighthouse stacked a
"TwilightKey" on the night rig and still read 0.13).  Black shade is the most
common cause: at sun elevation < 25° keep `fill` ≥ 1.0.

## Atmosphere: time-of-day triads with numbers

Pick the row, hand its colours to the rig, then only tune exposure.  `sun` = the rig's key
(`sunColor`, `elevation`; `intensity` is the mood's floor or higher), `hemi` = the rig's fill
(`fillSky` / `fillGround`; `fill` ≥ the floor).  Bare-light intensities measured without the
rig's environment map sit below its floors — the colours, elevations and fog are what a row
gives you.  Fog colour is always the sky's **horizon** colour — never white, never grey
unless the brief says overcast.

| time of day | rig mood | sky zenith | sky horizon = fog | sun colour | sun elev | fill sky / ground | fog near/far (100 m scene) |
|---|---|---|---|---|---|---|---|
| dawn | golden | 0x2f4a72 | 0xe8a06a | 0xffb073 | 8° | 0x7d94c4 / 0x4a3a2c | 25 / 150 |
| morning | day | 0x3d76c2 | 0xbcd6ea | 0xfff1d0 | 35° | 0xbfd6f0 / 0x5e5442 | 45 / 190 |
| noon | day | 0x2f6fd0 | 0xcfe2f2 | 0xfffaf0 | 70° | 0xcfe2f2 / 0x6b6152 | 60 / 220 |
| golden hour | golden | 0x3b5f96 | 0xf0a860 | 0xffc27a | 12° | 0x86a8d8 / 0x5c4630 | 35 / 170 |
| overcast | overcast | 0x8e9aa6 | 0xc3cad1 | 0xd8dee4 | 45° | 0xc3cad1 / 0x6f6a62 | 30 / 160 |
| dusk | golden | 0x33285c | 0xd9703a | 0xffa060 | 6° | 0x7a5aa0 / 0x3a2e22 | 30 / 140 |
| night (moon) | night | 0x0d1430 | 0x22305a | 0xa8c0ff | 40° | 0x36406e / 0x1a1c24 | 25 / 120 |

**Colour temperature contrast is the whole trick**: the key light is warm, the fill is the
*opposite* hue (cool blue sky bounce).  A scene lit warm-on-warm — orange sun with an
orange hemisphere — comes out as the monochrome-orange soup the rooftop run was marked
down for.  Keep `fillSky` at least 60° of hue away from `sunColor`.

```js
import { sunRig } from './lib/environment.js';
const TIME_OF_DAY = {
  dawn:        { mood: 'golden',   zenith: 0x2f4a72, horizon: 0xe8a06a, sun: 0xffb073, elev: 8,  fillSky: 0x7d94c4, fillGround: 0x4a3a2c, fog: [25, 150] },
  morning:     { mood: 'day',      zenith: 0x3d76c2, horizon: 0xbcd6ea, sun: 0xfff1d0, elev: 35, fillSky: 0xbfd6f0, fillGround: 0x5e5442, fog: [45, 190] },
  noon:        { mood: 'day',      zenith: 0x2f6fd0, horizon: 0xcfe2f2, sun: 0xfffaf0, elev: 70, fillSky: 0xcfe2f2, fillGround: 0x6b6152, fog: [60, 220] },
  goldenHour:  { mood: 'golden',   zenith: 0x3b5f96, horizon: 0xf0a860, sun: 0xffc27a, elev: 12, fillSky: 0x86a8d8, fillGround: 0x5c4630, fog: [35, 170] },
  overcast:    { mood: 'overcast', zenith: 0x8e9aa6, horizon: 0xc3cad1, sun: 0xd8dee4, elev: 45, fillSky: 0xc3cad1, fillGround: 0x6f6a62, fog: [30, 160] },
  dusk:        { mood: 'golden',   zenith: 0x33285c, horizon: 0xd9703a, sun: 0xffa060, elev: 6,  fillSky: 0x7a5aa0, fillGround: 0x3a2e22, fog: [30, 140] },
  night:       { mood: 'night',    zenith: 0x0d1430, horizon: 0x22305a, sun: 0xa8c0ff, elev: 40, fillSky: 0x36406e, fillGround: 0x1a1c24, fog: [25, 120] },
};

function applyTimeOfDay(THREE, scene, key, opts = {}) {
  const T = TIME_OF_DAY[key] || TIME_OF_DAY.morning;
  // the table's fog is calibrated for a 100 m scene: scale it by the bounds diagonal or
  // a 30 m garden disappears into soup (and its horizon ring never hazes).
  const fs = opts.fogScale ?? Math.min(2.5, Math.max(0.3, (opts.boundsDiagonal ?? 100) / 100));
  scene.fog = new THREE.Fog(T.horizon, T.fog[0] * fs, T.fog[1] * fs);
  scene.background = new THREE.Color(T.horizon);
  // ONE rig: key, hemisphere fill, environment map, disc, and shadows whose frustum and map
  // are fitted to `bounds` (texel ≤ 0.05 m) — never a second DirectionalLight beside it
  const bounds = opts.bounds ?? 60;
  const rig = sunRig({ mood: T.mood, azimuth: opts.azimuthDeg ?? 135, elevation: opts.elevationDeg ?? T.elev,
    sunColor: T.sun, fillSky: T.fillSky, fillGround: T.fillGround, zenith: T.zenith, horizon: T.horizon,
    bounds, fill: opts.fill, intensity: opts.intensity });
  scene.add(rig.sun, rig.sun.target, rig.fill);
  if (rig.sunDisc) scene.add(rig.sunDisc);
  scene.environment = rig.envTex;
  return { rig, palette: T, fogFar: scene.fog.far, shadowTexelM: (2 * bounds) / rig.sun.shadow.mapSize.x };
}
const todScene = new THREE.Scene();
const tod = applyTimeOfDay(THREE, todScene, 'goldenHour', { azimuthDeg: 250, bounds: 26, boundsDiagonal: 45 });
console.log('golden hour shadow texel (m)', tod.shadowTexelM.toFixed(3), 'fog far', tod.fogFar.toFixed(1), 'ring radius', (tod.fogFar * 0.6).toFixed(1));
```

**Haze layering / light shafts.**  Three or four horizontal haze quads at 1–6 m
(`depthWrite: false`, opacity 0.04–0.10, tinted the fog colour) make distance read even in
a flat scene; light shafts are thin additive cones along the sun, ≤ 0.12 opacity or milk.

```js
function buildHazeBands(THREE, opts = {}) {
  const n = opts.count ?? 4, size = opts.size ?? 200, color = opts.color ?? 0xd9c7a8;
  const g = new THREE.Group(); g.name = 'HazeBands';
  for (let i = 0; i < n; i++) {
    const q = new THREE.Mesh(new THREE.PlaneGeometry(size, size).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.035 + 0.02 * i, depthWrite: false, fog: false, side: THREE.DoubleSide }));
    q.position.y = (opts.base ?? 3.0) + i * (opts.step ?? 1.8);   // clear of eye-level cameras
    q.renderOrder = 5 + i; g.add(q);
  }
  return g;
}
function buildLightShaft(THREE, opts = {}) {
  const len = opts.length ?? 14, r = opts.radius ?? 1.6;
  const cone = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.25, r, len, 12, 1, true),
    new THREE.MeshBasicMaterial({ color: opts.color ?? 0xffe6b8, transparent: true, opacity: opts.opacity ?? 0.09,
                                 depthWrite: false, fog: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending }));
  cone.name = 'LightShaft'; cone.renderOrder = 9;
  const dir = (opts.dir ?? new THREE.Vector3(-0.4, -1, -0.25)).clone().normalize();
  cone.position.copy(opts.at ?? new THREE.Vector3(0, len * 0.5, 0));
  cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), dir);
  return cone;
}
console.log('haze bands', buildHazeBands(THREE).children.length, 'shaft', buildLightShaft(THREE).name);
```

Camera safety: the harness raycasts from every authored camera and calls geometry right in
front of the lens "camera inside geometry" (an ERROR).  Keep haze bands and shafts at least 1 m clear
of every eye-level camera, or — since these cards are not solid surfaces — give them
`mesh.raycast = () => {};` so the probe ignores them.  Never do that to real geometry.

## Water plane (use makeWaterMaterial from src/shaders/water.js)

```js
function buildLake(THREE, waterMat, x, z, r, level) {
  const lake = new THREE.Mesh(new THREE.CircleGeometry(r, 48).rotateX(-Math.PI / 2), waterMat);
  lake.name = 'Lake'; lake.position.set(x, level, z); lake.renderOrder = 2;   // after opaque terrain
  return lake;   // carve the terrain BELOW level around (x, z) in heightAt, or the shore z-fights
}
```

Why: water is a flat plane + the shader; `heightAt` must dip below the water level inside
the shoreline (a radial depression term) or the shore z-fights.

## Vegetation that reads real (species, jitter, clusters, ground cover)

A ring of identical cones is the second most common complaint.  Fix it with **three to five
species**, per-instance **scale / hue / rotation jitter**, **clustered** placement and a
**ground-cover** layer — all instanced, so a whole forest is 6–10 draw calls.

```js
// One species = a trunk + a crown with its own silhouette + hue band.  A conifer's crown is
// a cone; a broadleaf or scrub crown is LEAVES — `makeCanopy` (lib/canopy.js), never an
// icosahedron or any displaced ball (the catalog's crown rule).
function speciesGeometries(THREE) {
  return {
    conifer: { trunk: new THREE.CylinderGeometry(0.10, 0.20, 3.0, 6).translate(0, 1.5, 0),
               crown: new THREE.ConeGeometry(1.15, 4.4, 8).translate(0, 4.4, 0), hue: 0.32, sat: 0.42, lum: 0.20, h: [0.8, 1.5] },
    broadleaf: { trunk: new THREE.CylinderGeometry(0.14, 0.26, 2.4, 7).translate(0, 1.2, 0),
               canopy: { y: 3.4, radius: 1.7 }, hue: 0.24, sat: 0.45, lum: 0.28, h: [0.9, 1.6] },
    scrub:  { trunk: new THREE.CylinderGeometry(0.07, 0.11, 0.7, 5).translate(0, 0.35, 0),
               canopy: { y: 1.0, radius: 0.75 }, hue: 0.18, sat: 0.35, lum: 0.30, h: [0.7, 1.3] },
  };
}

// Clustered scatter: pick K cluster seeds, then drop members around them (Poisson-ish).
function clusteredPoints(n, area, seed = 5, clusters = 0, spread = 0) {
  const K = clusters || Math.max(2, Math.round(n / 7));
  const sp = spread || Math.min(area.w, area.d) * 0.13;
  const out = [];
  for (let i = 0; i < n; i++) {
    const k = i % K;
    const cx = area.x + (gHash(seed + k, 101) - 0.5) * area.w * 0.9;
    const cz = area.z + (gHash(seed + k, 103) - 0.5) * area.d * 0.9;
    const a = gHash(seed + i, 107) * 6.283, r = sp * Math.sqrt(gHash(seed + i, 109));
    out.push([cx + Math.cos(a) * r, cz + Math.sin(a) * r, k]);
  }
  return out;
}

function buildForest(THREE, opts = {}) {
  const area = opts.area ?? { x: 0, z: 0, w: 70, d: 70 };
  const mix = opts.mix ?? { conifer: 34, broadleaf: 22, scrub: 40 };
  const heightAtFn = opts.heightAt ?? terrainHeight, seed = opts.seed ?? 5;
  const SP = speciesGeometries(THREE), group = new THREE.Group(); group.name = opts.name ?? 'Vegetation';
  const bark = new THREE.MeshStandardMaterial({ color: 0x53412c, roughness: 0.95 });
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
  const p = new THREE.Vector3(), s = new THREE.Vector3(), tint = new THREE.Color();
  let si = 0;
  for (const [name, count] of Object.entries(mix)) {
    const sp = SP[name]; if (!sp || count <= 0) continue;
    const trunks = new THREE.InstancedMesh(sp.trunk, bark, count);
    const crowns = sp.crown ? new THREE.InstancedMesh(sp.crown, new THREE.MeshStandardMaterial({ roughness: 0.85, flatShading: true }), count) : null;
    const leafy = [];                                                                 // this species' makeCanopy crowns
    const pts = clusteredPoints(count, area, seed + si * 37);
    pts.forEach(([x, z], i) => {
      const k = sp.h[0] + (sp.h[1] - sp.h[0]) * gHash(seed + si * 31 + i, 211);      // 1) scale jitter
      const lean = (gHash(seed + i, 213) - 0.5) * 0.10;                              // 2) a few degrees of lean
      q.setFromAxisAngle(up, gHash(seed + i, 217) * 6.283);                          // 3) yaw jitter
      const qq = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0.3).normalize(), lean).multiply(q);
      p.set(x, heightAtFn(x, z), z); s.set(k * (0.85 + 0.3 * gHash(i, 219)), k, k * (0.85 + 0.3 * gHash(i, 221)));
      m.compose(p, qq, s); trunks.setMatrixAt(i, m);
      if (!crowns) { leafy.push({ position: [x, p.y + sp.canopy.y * k, z], radius: sp.canopy.radius * k }); return; }
      crowns.setMatrixAt(i, m);
      tint.setHSL(sp.hue + (gHash(seed + i, 223) - 0.5) * 0.055,                     // 4) hue jitter ±0.03
                  sp.sat + (gHash(seed + i, 227) - 0.5) * 0.18,
                  sp.lum + (gHash(seed + i, 229) - 0.5) * 0.10);                     // 5) value jitter
      crowns.setColorAt(i, tint);
    });
    trunks.instanceMatrix.needsUpdate = true; trunks.castShadow = true;
    trunks.computeBoundingSphere(); trunks.name = `${name}Trunks`; group.add(trunks);
    if (crowns) {
      crowns.instanceMatrix.needsUpdate = true;
      if (crowns.instanceColor) crowns.instanceColor.needsUpdate = true;
      crowns.castShadow = crowns.receiveShadow = true; crowns.computeBoundingSphere();
      crowns.name = `${name}Crowns`; group.add(crowns);
    } else {                                                                           // one leaf mesh per species
      group.add(makeCanopy({ crowns: leafy, color: new THREE.Color().setHSL(sp.hue, sp.sat, sp.lum), seed: seed + si, name: `${name}Crowns` }));
    }
    si++;
  }
  return group;
}
const forestDemo = buildForest(THREE, { area: { x: 0, z: 0, w: 60, d: 60 } });
console.log('forest draw calls', forestDemo.children.length, 'names', forestDemo.children.map(c => c.name).join(','));
```

**Ground cover + litter** turns "objects on a plane" into "plants growing out of the
ground": one InstancedMesh of small tapered blades (no textures needed).

```js
function buildGroundCover(THREE, opts = {}) {
  const n = opts.count ?? 900, area = opts.area ?? { x: 0, z: 0, w: 40, d: 40 };
  const heightAtFn = opts.heightAt ?? terrainHeight, seed = opts.seed ?? 9;
  const blade = new THREE.ConeGeometry(0.09, 0.34, 3).translate(0, 0.17, 0);   // 3-sided = 1 tri/face, cheap
  const mesh = new THREE.InstancedMesh(blade, new THREE.MeshStandardMaterial({ roughness: 1, flatShading: true }), n);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
  const p = new THREE.Vector3(), s = new THREE.Vector3(), c = new THREE.Color();
  const pts = clusteredPoints(n, area, seed, Math.round(n / 25));
  pts.forEach(([x, z], i) => {
    const k = 0.6 + 1.1 * gHash(seed + i, 307);
    q.setFromAxisAngle(up, gHash(seed + i, 311) * 6.283);
    p.set(x, heightAtFn(x, z) - 0.02, z); s.set(k, k * (0.7 + 0.8 * gHash(i, 313)), k);
    m.compose(p, q, s); mesh.setMatrixAt(i, m);
    c.setHSL(0.25 + (gHash(seed + i, 317) - 0.5) * 0.06, 0.40 + 0.2 * gHash(i, 319), 0.22 + 0.14 * gHash(i, 323));
    mesh.setColorAt(i, c);
  });
  mesh.instanceMatrix.needsUpdate = true; if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.computeBoundingSphere(); mesh.name = opts.name ?? 'GroundCover';
  mesh.castShadow = false; mesh.receiveShadow = true;
  return mesh;
}
const coverDemo = buildGroundCover(THREE, { count: 400, area: { x: 0, z: 0, w: 24, d: 24 } });
console.log('ground cover', coverDemo.count, 'tris', coverDemo.count * 3);
```

Density per 100 m² of planted zone: 2–5 trees, 8–20 shrubs, 250–600 tufts, 30–80 litter
pieces.  Less and the judge writes *"monotonous / undressed"*.

## Rocks, cliffs and boulders that read organic (never a row of boxes)

Stacked cuboids read as buildings — the desert-canyon run lost 0.2 to exactly that.  A rock
is a **noise-displaced polyhedron**; a cliff is a **ridge polyline of rotated, non-uniformly
scaled chunks with jittered strata**, never an axis-aligned wall.

```js
function rockGeometry(THREE, { radius = 1, detail = 1, rough = 0.34, seed = 1 } = {}) {
  const g = new THREE.IcosahedronGeometry(radius, detail);
  const pos = g.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    const d = 1 + rough * (gFbm(x * 1.7 + seed * 13, z * 1.7 + y * 1.1) - 0.5) * 2;
    pos.setXYZ(i, x * d, y * d * 0.8, z * d);                     // flatter than tall = looks weathered
  }
  g.computeVertexNormals(); return g;
}

function buildCliffRidge(THREE, opts = {}) {
  // ridge = [[x, z], ...]; chunks straddle it with random yaw/tilt and strata colour bands
  const ridge = opts.ridge ?? [[-30, -14], [-12, -10], [2, -6], [18, -9], [30, -16]];
  const n = opts.count ?? 26, height = opts.height ?? 9, seed = opts.seed ?? 21;
  const heightAtFn = opts.heightAt ?? terrainHeight;
  const strata = (opts.strata ?? [0x8c4a2f, 0xa8663c, 0xc08a5a, 0x7a3b26]).map(h => new THREE.Color(h));
  const group = new THREE.Group(); group.name = opts.name ?? 'CliffRidge';
  const geo = rockGeometry(THREE, { radius: 1, detail: 1, rough: 0.42, seed });
  const mesh = new THREE.InstancedMesh(geo, new THREE.MeshStandardMaterial({ roughness: 0.95, flatShading: true }), n);
  const m = new THREE.Matrix4(), e = new THREE.Euler(), q = new THREE.Quaternion(), p = new THREE.Vector3(), s = new THREE.Vector3();
  for (let i = 0; i < n; i++) {
    const u = i / (n - 1) * (ridge.length - 1), k = Math.min(ridge.length - 2, Math.floor(u)), f = u - k;
    const bx = ridge[k][0] + (ridge[k + 1][0] - ridge[k][0]) * f;
    const bz = ridge[k][1] + (ridge[k + 1][1] - ridge[k][1]) * f;
    const layer = i % strata.length;                                       // stack in strata bands
    const y0 = heightAtFn(bx, bz);
    const lift = height * (0.18 + 0.26 * layer) * (0.75 + 0.5 * gHash(seed + i, 401));
    const jx = (gHash(seed + i, 403) - 0.5) * 3.4, jz = (gHash(seed + i, 407) - 0.5) * 3.4;
    e.set((gHash(seed + i, 409) - 0.5) * 0.5, gHash(seed + i, 411) * 6.283, (gHash(seed + i, 413) - 0.5) * 0.5);
    q.setFromEuler(e);
    p.set(bx + jx, y0 + lift * 0.55, bz + jz);
    s.set(2.6 + 3.4 * gHash(seed + i, 415), lift * (0.7 + 0.6 * gHash(seed + i, 417)), 2.2 + 3.0 * gHash(seed + i, 419));
    m.compose(p, q, s); mesh.setMatrixAt(i, m);
    mesh.setColorAt(i, strata[layer].clone().offsetHSL(0, 0, (gHash(seed + i, 421) - 0.5) * 0.08));
  }
  mesh.instanceMatrix.needsUpdate = true; if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.computeBoundingSphere(); mesh.castShadow = mesh.receiveShadow = true; mesh.name = 'CliffChunks';
  group.add(mesh); return group;
}
const cliffDemo = buildCliffRidge(THREE);
console.log('cliff chunks', cliffDemo.children[0].count);
```

Rules: chunk yaw random over the full circle (an axis-aligned box gives it away); scale
non-uniform (x ≠ y ≠ z by ≥ 20 %); strata luminance differing by ≥ 0.08; the ridge
polyline bending ≥ 15° at least twice; floor boulders reuse `rockGeometry` at 0.3–1.6 m.

## Set dressing: how many props a place needs

Judges call a scene "sparse", "token" or "undressed" long before they call it wrong.
Counts that read as *lived in*, per zone (a zone is typically 10 × 10 m to 30 × 30 m):

| scene type | hero assets | mid props (0.3–1.5 m) | small props (< 0.3 m) | ground cover / litter |
|---|---|---|---|---|
| garden / park | 3–5 (lantern, bridge, bench) | 12–25 (rocks, planters, pots, stumps, buckets) | 25–60 (pebbles, leaves, tools, cups, twigs) | 300–800 tufts |
| desert / canyon | 2–4 (hero boulder, arch, wreck) | 15–30 boulders in 3 size bands | 30–70 (pebbles, bones, dry brush) | 60–200 scrub clumps |
| rooftop / urban | 3–6 (pergola, planters, lounge) | 15–30 (pots, crates, lamps, chairs, vents, ducts) | 30–80 (cups, books, cushions, cables, bottles) | 100–300 plant tufts |
| interior | 3–6 (bench rows, water feature) | 15–30 (pots, tools, watering cans, crates) | 40–100 (labels, trowels, gloves, jars) | n/a |

Cheap rules: 3 size bands per prop family, one InstancedMesh per band; place props
**against something** (wall base, path edge, under a bench) — props alone in open ground
read as scatter; never a grid or ring (≥ 20 % positional jitter, full yaw jitter).

## Zones (`src/zones/*.js`): Groups with a bbox discipline

```js
// Each zone owns a rectangle of the plan; everything it adds stays inside it and sits on
// heightAt (or on a structure the zone built).  Naming: zone Group = plan zone name.
function buildCampfire(THREE, { heightAt }) {
  const zone = new THREE.Group(); zone.name = 'Campfire';
  const cx = -14, cz = 10, cy = heightAt(-14, 10);
  const stoneGeo = new THREE.IcosahedronGeometry(0.22, 0);
  const stones = new THREE.InstancedMesh(stoneGeo, new THREE.MeshStandardMaterial({ color: 0x8b8b90, roughness: 0.95 }), 9);
  const m = new THREE.Matrix4();
  for (let i = 0; i < 9; i++) {
    const a = i / 9 * Math.PI * 2;
    m.makeTranslation(cx + Math.cos(a) * 0.8, heightAt(cx + Math.cos(a) * 0.8, cz + Math.sin(a) * 0.8) + 0.08, cz + Math.sin(a) * 0.8);
    stones.setMatrixAt(i, m);
  }
  stones.computeBoundingSphere(); zone.add(stones);
  const fire = new THREE.PointLight(0xff8033, 8.0, 12, 2);   // physical falloff; small radius
  fire.position.set(cx, cy + 0.5, cz); zone.add(fire);
  zone.userData.update = (t) => { fire.intensity = 7.0 + Math.sin(t * 9.0) * 1.2 + Math.sin(t * 23.0) * 0.6; };
  return zone;
}
const campfireDemo = buildCampfire(THREE, { heightAt }); campfireDemo.userData.update(1.0);
```

Rules: shadows come from the sun and the hero lights only; decorative lights get
`castShadow = false` and distance-limited falloff.

Placement is MEASURED (`check_placement`; the `scene_placement` gate runs on every build): each
direct child of your zone group is an asset; from its lowest vertices the harness looks down for the
nearest surface and up for one passing through it.  A gap under the foot → `floating`; a foot buried
under a surface → `sunken` (rocks/posts/bushes may sit half their height in the ground; a basin
or pit never counts; a foot at/under water is fine); bbox touching nothing → `unsupported`; boxes sharing
a large share of their volume → `interpenetration`.  Seat things with `heightAt(x, z)` and they pass.  A thing MEANT to hang in
the air (bird, drone): `obj.userData.placement = 'free'` on it or its zone = exempt.  Instanced meshes are not checked.

## Assets (`src/assets/*.js`) and GLB assets from Blender

```js
// Procedural asset factory: exactly like a static object part — Y-up, on y = 0, PascalCase.
function buildLantern(THREE, opts = {}) {
  const g = new THREE.Group(); g.name = 'Lantern';
  const metal = new THREE.MeshStandardMaterial({ color: 0x2c2c30, roughness: 0.5, metalness: 0.8 });
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.04, 2.2, 10), metal);
  post.position.y = 1.1; g.add(post);
  const cage = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.14, 0.26, 6), metal);
  cage.position.y = 2.2 + 0.13 - 0.004; g.add(cage);
  const glow = new THREE.Mesh(new THREE.SphereGeometry(0.08, 12, 8),
    new THREE.MeshStandardMaterial({ color: 0xffe6b0, emissive: 0xffc46a, emissiveIntensity: 3.0 }));
  glow.position.y = 2.33; g.add(glow);
  return g;
}
// GLB asset (built in Blender, compiled to public/assets/<snake>.glb): the assembled scene.js
// preloads it — named, shadowed, its clips kept — into ctx.assets['<snake>'].  A zone only clones:
function placeHero(ctx, key, x, z) {
  const hero = ctx.assets[key] && ctx.assets[key].clone();   // Y-up meters, base at y = 0, clips play by themselves
  if (hero) hero.position.set(x, ctx.heightAt(x, z), z);
  return hero;   // null when the hero was NOT AVAILABLE — build a procedural stand-in instead
}
console.log('lantern ok', buildLantern(THREE).children.length);
```

Why: a scene mixes cheap instanced fillers with a few hero assets; GLBs come in Y-up meters
(same frame), so a loaded asset is placed exactly like a procedural one.

## Animation: update(t, dt) patterns + particles

```js
// Rigid movers: cache a `home` Vector3 at build time, then set (never accumulate) the
// pose from t — `obj.position.y = home.y + Math.sin(t * 0.9) * 0.06` etc.
// Particles: THREE.Points + PointsMaterial is enough for dust/fireflies/snow (custom
// point shaders only if the plan demands).  Recycle positions inside update.
function buildFireflies(THREE, n = 120, area = { x: 0, z: 0, w: 30, d: 30 }) {
  const pos = new Float32Array(n * 3);
  const rnd = mulberry32(11);
  for (let i = 0; i < n; i++) pos.set([area.x + (rnd() - 0.5) * area.w, 0.6 + rnd() * 2.2, area.z + (rnd() - 0.5) * area.d], i * 3);
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const pts = new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xd9f76e, size: 0.06, sizeAttenuation: true, transparent: true, opacity: 0.9 }));
  pts.name = 'Fireflies';
  const base = pos.slice();
  pts.userData.update = (t) => {
    const p = geo.attributes.position.array;
    for (let i = 0; i < n; i++) {
      p[i * 3] = base[i * 3] + Math.sin(t * 0.7 + i) * 0.4;
      p[i * 3 + 1] = base[i * 3 + 1] + Math.sin(t * 1.3 + i * 2.1) * 0.25;
      p[i * 3 + 2] = base[i * 3 + 2] + Math.cos(t * 0.5 + i * 0.7) * 0.4;
    }
    geo.attributes.position.needsUpdate = true;
  };
  return pts;
}
const ff = buildFireflies(THREE); ff.userData.update(2.0);
console.log('fireflies', ff.geometry.attributes.position.count);
```

Rules: `update` mutates cached objects only — no `new`, no `traverse`, no material
creation per frame.  Everything is a function of `t` (deterministic renders at t = 1.5).

## Motion you can SEE between t = 0 and t = 1.5 s

The harness renders every camera at t = 0 and t = 1.5 s and the judge diffs the two
frames.  Sub-centimetre sway is invisible at 1024 × 576 and scores as `nothing_moves`
(−0.05 and the whole animation criterion).  Amplitudes that actually read:

| what moves | amplitude | period | note |
|---|---|---|---|
| foliage sway | ±0.10–0.20 rad (6–11°) | 1.6–3 s | rotate the crown/branch group, not the trunk base |
| grass / reeds | ±0.15 rad | 1.2–2 s | phase-offset per instance |
| water | wave height ≥ 0.04 m **and** a scrolling normal/uv at ≥ 0.15 m/s | 2–4 s | a still mirror reads as static |
| falling leaves / petals / snow / dust | ≥ 0.6 m of travel in 1.5 s | recycle over 6–12 s | 60–250 particles |
| flags / banners / string lights | ±0.12 rad | 1.2 s | the classic "the scene is alive" cue |
| flame / lantern flicker | intensity ×0.75…×1.3 | 0.1–0.3 s | plus emissiveIntensity on the material |
| vehicles / boats / birds | ≥ 1.5 m of travel in 1.5 s | loop | one hero mover beats ten twitchers |

Give the scene **one unmistakable hero motion** (rotating beam, drifting boat, falling
leaves, spinning wheel, flock) plus the ambient ones — all pure functions of `t`.

```js
function makeSwayGroup(THREE, child, opts = {}) {
  // wrap anything in a pivot that sways visibly; pivot at the BASE so it bends, not slides
  const pivot = new THREE.Group(); pivot.name = opts.name ?? 'Sway';
  pivot.add(child);
  const amp = opts.amp ?? 0.14, per = opts.period ?? 2.2, ph = opts.phase ?? 0;
  pivot.userData.update = (t) => {
    pivot.rotation.z = Math.sin((t / per) * 6.283 + ph) * amp;
    pivot.rotation.x = Math.sin((t / (per * 1.37)) * 6.283 + ph * 1.7) * amp * 0.55;
  };
  return pivot;
}
const swayDemo = makeSwayGroup(THREE, new THREE.Mesh(new THREE.ConeGeometry(0.6, 2, 6), new THREE.MeshStandardMaterial({ color: 0x3f6b2a })));
swayDemo.userData.update(0); const a0 = swayDemo.rotation.z;
swayDemo.userData.update(1.5); const a1 = swayDemo.rotation.z;
console.log('sway delta (rad) over 1.5s', Math.abs(a1 - a0).toFixed(3), (Math.abs(a1 - a0) > 0.05 ? 'VISIBLE' : 'TOO SMALL'));
```

Self-check: after `scene_views(times=[0, 1.5])` compare the two sheets.  If you cannot
point at what changed, double the amplitude.

## Scene layering: foreground, midground, background

A "diorama on an empty plane" scores ~0.5 whatever else you do.  Every authored camera needs
**three depth layers**, built on purpose:

| layer | distance from camera | what lives there | how much of the frame |
|---|---|---|---|
| foreground frame | 1.5–5 m | a branch, a post, a parapet edge, a leaning tool, tall grass, an arch | 10–25 % of the frame area, usually at an edge/corner, may be partly cut off |
| midground subject | 8–30 m | the hero assets: what the brief is about | fills the centre third |
| background | 40 m–fog far | silhouette ring, distant treeline / skyline / mesas, sky gradient | the top third |

```js
// Place a framing element just off the camera's axis, at 2-4 m, so it brackets the shot.
function frameForeground(THREE, cam, object, opts = {}) {
  const dist = opts.dist ?? 3.0, side = opts.side ?? -1, lift = opts.lift ?? 0;
  const eye = new THREE.Vector3().fromArray(cam.position);
  const at = new THREE.Vector3().fromArray(cam.lookAt);
  const fwd = at.clone().sub(eye).setY(0).normalize();
  const right = new THREE.Vector3().crossVectors(fwd, new THREE.Vector3(0, 1, 0)).normalize();
  const pos = eye.clone().add(fwd.multiplyScalar(dist)).add(right.multiplyScalar(side * dist * 0.75));
  object.position.set(pos.x, (opts.heightAt ?? terrainHeight)(pos.x, pos.z) + lift, pos.z);
  object.rotation.y = Math.atan2(eye.x - pos.x, eye.z - pos.z) + (opts.yaw ?? 0);
  return object;
}
const camDemo = { name: 'Establishing', position: [24, 9, 26], lookAt: [0, 2, 0], fov: 42 };
const framer = frameForeground(THREE, camDemo, new THREE.Mesh(rockGeometry(THREE, { radius: 1.4, seed: 4 }),
  new THREE.MeshStandardMaterial({ color: 0x5b5348, roughness: 1, flatShading: true })), { side: -1 });
console.log('foreground framer at', framer.position.toArray().map(v => +v.toFixed(2)).join(','));
```

**Keep the cameras' first metres clear.**  Scatter is blind: it will happily plant a
conifer 1 m in front of the establishing camera and turn the money shot into a wall of
bark (measured — it cost a whole grade before the mask was added).  Every scatter loop
skips instances inside a clearance disc around each authored camera, and the establishing
camera sits *above the canopy*: `y ≥ tallest thing in the shot + 0.30 × distance to the
subject`, looking DOWN at the subject.

```js
// Give every zone the authored camera list, then mask the SCATTER with it (deliberate
// foreground props are placed by hand afterwards and are not masked).
function cameraMask(cameras, opts = {}) {
  // 2.5-5 m is enough: bigger discs leave bald patches in a small scene
  const r = opts.radius ?? 4, discs = cameras.map(c => [c.position[0], c.position[2], r]);
  for (const c of cameras) {                       // also clear 25 % of the way to the subject
    const dx = c.lookAt[0] - c.position[0], dz = c.lookAt[2] - c.position[2];
    discs.push([c.position[0] + dx * 0.22, c.position[2] + dz * 0.22, r * 0.6]);
  }
  return (x, z) => discs.some(([cx, cz, rr]) => (x - cx) ** 2 + (z - cz) ** 2 < rr * rr);
}
const maskDemo = cameraMask([{ name: 'Establishing', position: [8, 10.5, 22], lookAt: [0, 1.4, -1], fov: 44 }]);
console.log('camera mask blocks (8,22)?', maskDemo(8, 22), ' blocks (0,0)?', maskDemo(0, 0));
```

Skipped instances are not deleted — write a zero-scale matrix (`m.makeScale(0, 0, 0)`) so
the instance count and every index stay stable.

**Atmospheric perspective** does the rest: with fog matching the sky, a background object
loses contrast and saturation with distance for free — do not hand-tint on top of it (see
the horizon chapter); darken the distant material instead.

## Cameras: measured, never guessed

```js
function planCameras(THREE, scene) {
  const box = new THREE.Box3();                       // measure CONTENT, not the 240 m sky dome
  scene.traverse(o => { if (o.isMesh && o.name !== 'SkyDome') box.expandByObject(o); });
  if (box.isEmpty()) box.set(new THREE.Vector3(-20, 0, -20), new THREE.Vector3(20, 10, 20));
  const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
  const d = Math.max(size.x, size.z);
  return [
    // 1) establishing: high 3/4, sees ≥70% of bounds, sun BEHIND the camera shoulder
    { name: 'Establishing', position: [c.x + d * 0.55, size.y * 1.1 + 6, c.z + d * 0.6], lookAt: [c.x, 2, c.z], fov: 42 },
    // 2) mid: inside the scene at eye height, aimed at a zone's focal point
    { name: 'GroveMid', position: [8, heightAt(8, 6) + 1.6, 6], lookAt: [0, heightAt(0, -10) + 2.0, -10], fov: 50 },
    // 3) detail: 2-4 m from a hero object, slightly above eye line
    { name: 'CampfireDetail', position: [-11, heightAt(-11, 8) + 1.8, 8], lookAt: [-14, heightAt(-14, 10) + 0.6, 10], fov: 45 },
  ];
}
console.log('cameras', planCameras(THREE, new THREE.Scene()).map(c => c.name));
```

Rules: eye height 1.6 m (+ terrain!), fov 35–60, look at a focal POINT (an object, not
the horizon), never inside geometry or up against a surface, 3–4 cameras:
establishing → mid → detail.  Compute positions from measured bounds/heightAt, not vibes.

## Post-processing (the harness's own chain, ON by default)

The harness renders scene pictures through its own post chain (GTAO + a selective emissive
bloom + a grade that is identity unless `scene.userData.grade` is set), and the scene must
still read without it (`emissiveIntensity` ≤ 3, no white-out).  Do not build an
`EffectComposer`: nothing reads one exported from `scene.js`.

## Performance

* Anything placed > 5 × is an InstancedMesh, static one-material clutter is `mergeGeometries`.
* `update` caches lists, mutates in place, allocates nothing per frame.
* `scene_probe` reports draws/tris/fps — run it before polishing.  When it is slow the usual
  criminals are un-instanced repeats, per-frame `traverse`, and 4096² shadow maps.

## Pitfalls (symptom → cause → fix)

1. **Everything floats / sinks on a slope** → placement ignored `heightAt`; every position
   is `(x, heightAt(x, z) + halfHeightOrRootOffset, z)`.
2. **Black frame** → no lights, or fog far < camera distance, or camera inside a hill.
   **White frame** → sun + hemisphere both high + fog colour white + bloom.
3. **Scene washed out** → ambient/hemisphere too strong; contrast comes from the sun/shadow
   ratio (sun ≈ 4–6 × hemisphere).
4. **Shadows missing** → `castShadow` on the light AND meshes, `receiveShadow` on ground,
   shadow camera frustum too small, or light too far (increase `far`).
5. **Shadow acne / peter-panning** → a hand-set bias or map size on the rig's sun; `sunRig`
   fits both to `bounds` — pass the content radius and leave them alone.
6. **`Math.random()` anywhere** → different geometry every build; renders not reproducible;
   use `mulberry32(seed)` from `lib/noise.js`.
7. **Low fps** → count draw calls first (`scene_probe`); the usual criminals: hundreds of
   Meshes that should be instances, per-frame `traverse`, 4096 shadow maps, fog off with a
   500 m far plane.
8. **Fog hides everything** → `Fog(near, far)` in metres — near 45/far 160 for a 100 m
   scene; never near 1/far 10.
9. **Sky dome black or fogged** → sky material needs `fog: false`, `side: BackSide`,
   `depthWrite: false`, and the tonemap/colorspace includes (see the env recipe).
10. **GLB asset invisible** → the zone never cloned `ctx.assets['<snake>']` (nothing loads a GLB
    itself), or its pivot is not at the base; re-export from Blender with the object on z = 0.
11. **Camera inside a tree / wall** → positions are guesses; compute from `heightAt` +
    known object positions, then check with `scene_views` (the harness flags
    camera-inside-geometry).
12. **update() allocates** (`new Vector3` per frame, `getSize` per frame) → GC hitches;
    hoist scratch objects to module scope.
13. **Zones overlap / fight** → each zone stays in its plan rectangle; shared borders
    belong to the env (paths, fences).
14. **Lights per lantern** → 30 PointLights kill the frame; ONE emissive material + 1–2
    real lights near the camera path.
15. **`nothing_moves` although you animated things** → the hook is named `userData.tick`
    (never called), or `tickEnv` (never called), or the amplitude is < 2 cm / < 0.05 rad so
    t=0 and t=1.5 look identical.  Hook = `userData.update`; amplitudes from
    "Motion you can SEE".
16. **A tree 1 m in front of the establishing camera** → the scatter loop had no camera
    mask; see `cameraMask` in "Scene layering".
17. **The backdrop reads as pale cardboard** → hand-lerped toward a light fog colour
    (double haze → brighter than the ground) and/or built from flat planes.  Darken the
    base colour, let `scene.fog` haze it, use solid geometry.
18. **Jagged / stair-stepped shadows** → the shadow span is the whole terrain instead of the
    content: `sunRig({ bounds })` sizes the map to the span it is given, so give it the
    content radius, not the ground size.
19. **Monochrome orange (or blue) frame** → key and fill share a hue.  The fill
    (HemisphereLight sky colour) must be ≥ 60° of hue away from the sun colour.
20. **The scene is a diorama on a plane** → no foreground layer and no background layer.
    Three depth layers per authored camera, always ("Scene layering").
21. **A pond / terrace / lawn is a hard-edged disc or square lying ON the ground** → carve
    it into `heightAt` (a radial depression for water, a shallow shelf for a terrace) and
    ring the seam with a kerb + dressing; a plane laid on top always shows its outline.
22. **`camera is N m below ground level`** → the plan's camera heights are absolute
    (metres above y = 0) but the terrain lifted the floor.  Keep `heightAt` within about
    ±1.5 m of 0 inside the bounds, or add `heightAt(x, z)` to the camera's y in scene.js.

## Self-check (before you call it done)

```js
function sceneSelfcheck(THREE, scene, cameras) {
  let tris = 0, draws = 0, lights = 0, shadowLights = 0;
  scene.traverse(o => {
    if (o.isLight) { lights++; if (o.castShadow) shadowLights++; }
    if (o.isMesh || o.isPoints) {
      draws++;
      const g = o.geometry, n = g.index ? g.index.count / 3 : g.attributes.position.count / 3;
      tris += n * (o.isInstancedMesh ? o.count : 1);
    }
  });
  if (!scene.fog) throw new Error('scene.fog not set');
  if (!cameras || cameras.length < 3) throw new Error('need 3-4 cameras');
  console.log(`[selfcheck] draws=${draws} tris=${Math.round(tris)} lights=${lights} shadowLights=${shadowLights} cameras=${cameras.length}`);
}
const demoScene = new THREE.Scene(); buildEnv({ THREE, scene: demoScene });
demoScene.add(forestDemo, coverDemo, campfireDemo, ff);
sceneSelfcheck(THREE, demoScene, planCameras(THREE, demoScene));
```

Composition self-check on every authored frame (the judge grades exactly these):

* three depth layers — something inside 5 m, the subject at 8–30 m, a silhouette/sky band above;
* no hard world edge anywhere against the sky;
* the ground shows ≥ 3 blended colours and at least one path / worn area;
* vegetation shows ≥ 3 silhouettes and visible hue/scale variation, in clusters not a ring;
* ≥ 12 mid props and ≥ 25 small props per dressed zone, placed against things;
* shade is coloured (sky-tinted), not black; key and fill hues differ;
* t=0 and t=1.5 are visibly different.

Then: `build` → `check_placement` (floating/sunken/interpenetration + the fix per asset) → `scene_probe` (draws/tris/fps/console) → `scene_views` (authored cameras +
overview rig, with `camera_checks`) → `shader_probe` if you wrote GLSL → fix the worst →
repeat.  Look at every camera's frame AND its numbers: no black frames, no white-out, the
subject (not sky/ground) fills the establishing shot, `camera_in_geometry` false, and `check_placement`
clean (nothing floating, sunken or interpenetrating).
