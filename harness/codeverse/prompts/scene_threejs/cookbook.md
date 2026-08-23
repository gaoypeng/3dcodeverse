# Scene cookbook — multi-file three.js scenes (r182), headless-rendered by the harness

Every `js` snippet runs as-is in node (the harness test suite concatenates them with
`THREE` and the named addons in scope).  Y-up, +Z front, meters.  GLSL lives in
`glsl_cookbook.md` — use its `makeShaderMaterial` for every custom shader.
Use `read_cookbook(section="<heading>")` for one chapter.

## Assembly skeleton (`src/scene.js` orchestrates, builds nothing itself)

```js
// scene.js: build env → zones → cameras; cache everything update() needs.  Never traverse per frame.
const rand = (i) => { const s = Math.sin(i * 12.9898 + 78.233) * 43758.5453; return s - Math.floor(s); };

async function exampleCreateScene({ THREE, renderer, loaders }) {
  const scene = new THREE.Scene();
  const env = buildEnv(THREE, scene);                       // sky, sun, fog, ground (below)
  const zones = [buildOrchard(THREE, { heightAt })];        // every zone: PascalCase Group
  for (const z of zones) scene.add(z);
  const movers = zones.filter(z => z.userData.update);      // cached ONCE
  const mats = collectAnimatedMaterials(scene);             // from glsl_cookbook wire-up
  const cameras = planCameras(THREE, scene);                // measured, not guessed (below)
  return {
    scene, cameras,
    update(t, dt) {
      env.update(t, dt);
      for (const z of movers) z.userData.update(t, dt);
      for (const m of mats) m.userData.update(t, dt);
    },
  };
}
```

## Environment (`src/env.js`): sun + sky + fog + ground — the scene's floor comes first

```js
const BOUNDS = { min: [-50, 0, -50], max: [50, 30, 50] };     // export const in real env.js
const SUN_DIR = new THREE.Vector3(0.55, 0.62, 0.35).normalize();  // one sun direction, reused everywhere

function heightAt(x, z) {
  // deterministic analytic terrain — EVERY placement must call this, or things float
  return 0.8 * Math.sin(x * 0.09) * Math.cos(z * 0.07) + 0.35 * Math.sin((x * 0.31 + z * 0.23));
}

function buildEnv(THREE, scene) {
  // fog + matching background: the single biggest depth cue
  scene.fog = new THREE.Fog(0xa8c4dd, 45, 160);               // near ≈ bounds/2, far ≈ 1.6 × diagonal
  scene.background = new THREE.Color(0xa8c4dd);
  // sky dome gradient (cheap, always works; the Sky addon is the fancy alternative)
  const skyGeo = new THREE.SphereGeometry(240, 24, 12);
  const skyMat = new THREE.ShaderMaterial({
    side: THREE.BackSide, fog: false, depthWrite: false,
    uniforms: { uTop: { value: new THREE.Color(0x4e7fb8) }, uHorizon: { value: new THREE.Color(0xd8e4ec) } },
    vertexShader: 'varying vec3 vP; void main() { vP = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `uniform vec3 uTop; uniform vec3 uHorizon; varying vec3 vP;
      void main() { float h = clamp(normalize(vP).y, 0.0, 1.0); gl_FragColor = vec4(mix(uHorizon, uTop, pow(h, 0.55)), 1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
      }`,
  });
  const sky = new THREE.Mesh(skyGeo, skyMat); sky.name = 'SkyDome'; scene.add(sky);
  // lights: hemisphere fill + ONE shadow-casting sun.  Physical intensities (no legacy lights).
  scene.add(new THREE.HemisphereLight(0xbfd6f0, 0x5e5442, 0.55));
  const sun = new THREE.DirectionalLight(0xfff0d8, 2.6);
  sun.position.copy(SUN_DIR).multiplyScalar(80); sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0005;
  Object.assign(sun.shadow.camera, { left: -55, right: 55, top: 55, bottom: -55, near: 5, far: 200 });
  scene.add(sun, sun.target);
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

Numbers: sun 2.2–3.2, hemisphere 0.4–0.7 (never both high — washed out); shadow map 2048,
frustum just past the bounds; fog near ≈ half the bounds, far ≈ 1.5–2 × the diagonal;
128 × 128 ground segments for 100 m (≈ 0.8 m resolution).

## Water plane (use makeWaterMaterial from the GLSL cookbook)

```js
function buildLake(THREE, waterMat, x, z, r, level) {
  const lake = new THREE.Mesh(new THREE.CircleGeometry(r, 48).rotateX(-Math.PI / 2), waterMat);
  lake.name = 'Lake'; lake.position.set(x, level, z); lake.renderOrder = 2;   // after opaque terrain
  return lake;   // carve the terrain BELOW level around (x, z) in heightAt, or the shore z-fights
}
```

Why: water is a flat plane + the shader; the terrain function must dip below the water
level inside the shoreline (add a radial depression term to `heightAt`).

## Instanced vegetation and deterministic scatter

```js
function scatterMatrices(THREE, n, area, heightAt, seed = 1) {
  // hash-based, deterministic; NEVER Math.random().  area = {x, z, w, d}
  const out = []; const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
  for (let i = 0; i < n; i++) {
    const x = area.x + (rand(seed + i) - 0.5) * area.w;
    const z = area.z + (rand(seed + i + 5000) - 0.5) * area.d;
    const s = 0.7 + 0.6 * rand(seed + i + 9000);
    q.setFromAxisAngle(up, rand(seed + i + 13000) * Math.PI * 2);
    m.compose(new THREE.Vector3(x, heightAt(x, z), z), q, new THREE.Vector3(s, s, s));
    out.push(m.clone());
  }
  return out;
}

function buildOrchard(THREE, { heightAt }) {
  const zone = new THREE.Group(); zone.name = 'Orchard';
  const N = 60;
  const trunkGeo = new THREE.CylinderGeometry(0.09, 0.16, 2.2, 7).translate(0, 1.1, 0);   // origin at the ROOT
  const crownGeo = new THREE.IcosahedronGeometry(1.3, 1).translate(0, 3.1, 0);
  const trunks = new THREE.InstancedMesh(trunkGeo, new THREE.MeshStandardMaterial({ color: 0x5a4028, roughness: 0.9 }), N);
  const crowns = new THREE.InstancedMesh(crownGeo, new THREE.MeshStandardMaterial({ color: 0x4a7a2e, roughness: 0.85 }), N);
  const mats = scatterMatrices(THREE, N, { x: 0, z: -12, w: 36, d: 24 }, heightAt, 7);
  const tint = new THREE.Color();
  mats.forEach((m, i) => {
    trunks.setMatrixAt(i, m); crowns.setMatrixAt(i, m);
    crowns.setColorAt(i, tint.setHSL(0.26 + 0.05 * rand(i + 33), 0.45, 0.32 + 0.1 * rand(i + 66)));  // per-instance variety
  });
  trunks.castShadow = crowns.castShadow = true;
  trunks.instanceMatrix.needsUpdate = crowns.instanceMatrix.needsUpdate = true;
  crowns.instanceColor.needsUpdate = true;
  trunks.computeBoundingSphere(); crowns.computeBoundingSphere();
  trunks.name = 'OrchardTrunks'; crowns.name = 'OrchardCrowns';
  zone.add(trunks, crowns);
  return zone;
}
const orchardDemo = buildOrchard(THREE, { heightAt });
console.log('orchard instances', orchardDemo.children.map(c => c.count));
```

Why: geometry origin at the root + `heightAt` in the matrix = nothing floats; one
InstancedMesh per organ = 2 draw calls for a forest; `setColorAt` breaks the clone look.
For wind, give the crowns `makeSwayMaterial` from the GLSL cookbook.

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

Rules: ≤ 3 shadow-casting lights per scene (the sun + 1–2 heroes); decorative lights get
`castShadow = false` and distance-limited falloff.

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
// GLB asset (built by src/assets/<name>.py in Blender, compiled to public/assets/<name>.glb):
async function loadAsset(loaders, name) {
  const gltf = await loaders.gltf.loadAsync(`/assets/${name}.glb`);
  const root = gltf.scene; root.name = name;
  root.traverse(o => { if (o.isMesh) { o.castShadow = o.receiveShadow = true; } });
  return root;   // clone() per placement; GLB is Y-up meters already
}
console.log('lantern ok', buildLantern(THREE).children.length);
```

Why: a scene mixes cheap instanced fillers with a few hero assets; GLBs come in Y-up
meters (same frame), so a loaded asset is placed exactly like a procedural one.

## Animation: update(t, dt) patterns + particles

```js
function buildBoat(THREE) {
  const boat = new THREE.Group(); boat.name = 'Boat';
  const hull = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.2, 2.6, 8).rotateZ(Math.PI / 2).rotateY(Math.PI / 2),
                              new THREE.MeshStandardMaterial({ color: 0x7a4a26, roughness: 0.7 }));
  boat.add(hull);
  const home = new THREE.Vector3(6, 0.0, 8);
  boat.position.copy(home);
  boat.userData.update = (t) => {                     // bobbing + slow drift; pure function of t
    boat.position.y = home.y + Math.sin(t * 0.9) * 0.06;
    boat.rotation.z = Math.sin(t * 0.7 + 1.0) * 0.04;
    boat.rotation.x = Math.sin(t * 1.1) * 0.03;
  };
  return boat;
}
// Particles: THREE.Points + PointsMaterial is enough for dust/fireflies/snow (custom
// point shaders only if the plan demands).  Recycle positions inside update.
function buildFireflies(THREE, n = 120, area = { x: 0, z: 0, w: 30, d: 30 }) {
  const pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) pos.set([area.x + (rand(i) - 0.5) * area.w, 0.6 + rand(i + 300) * 2.2, area.z + (rand(i + 600) - 0.5) * area.d], i * 3);
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
    { name: 'OrchardMid', position: [8, heightAt(8, 6) + 1.6, 6], lookAt: [0, heightAt(0, -10) + 2.0, -10], fov: 50 },
    // 3) detail: 2-4 m from a hero object, slightly above eye line
    { name: 'CampfireDetail', position: [-11, heightAt(-11, 8) + 1.8, 8], lookAt: [-14, heightAt(-14, 10) + 0.6, 10], fov: 45 },
  ];
}
console.log('cameras', planCameras(THREE, new THREE.Scene()).map(c => c.name));
```

Rules: eye height 1.6 m (+ terrain!), fov 35–60, look at a focal POINT (an object, not
the horizon), never inside geometry and ≥ 0.5 m from any surface, 3–5 cameras:
establishing → mid → detail.  Compute positions from measured bounds/heightAt, not vibes.

## Post-processing (optional; the default pipeline renders WITHOUT it)

```js
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

// Export from scene.js ONLY if the plan asks for bloom; harness support is optional, so
// the scene must also look right without it (emissiveIntensity ≤ 3, no white-out).
function makeComposer(THREE, renderer, scene, camera, width, height) {
  const composer = new EffectComposer(renderer);
  composer.setSize(width, height);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(width, height), 0.35, 0.6, 0.85);
  composer.addPass(bloom);                        // strength ≤ 0.5, threshold ≥ 0.8: glow, not soup
  composer.addPass(new OutputPass());             // ALWAYS last: tone mapping + sRGB live here
  return composer;
}
console.log('composer factory ready', typeof makeComposer);
```

## Performance budget

* ≤ 2 M triangles, ≤ 200 draw calls, ≤ 40 materials, ≤ 3 shadow casting lights.
* Anything placed > 5 × = InstancedMesh; static clutter of one material = `mergeGeometries`.
* Ground 128² segments max; trees 300–800 tris each (instanced), hero assets ≤ 40 k.
* `update` < 4 ms: cache lists, mutate in place, no per-frame allocation.
* `scene_probe` reports draws/tris/fps — run it before polishing.

## Pitfalls (symptom → cause → fix)

1. **Everything floats / sinks on a slope** → placement ignored `heightAt`; every position
   is `(x, heightAt(x, z) + halfHeightOrRootOffset, z)`.
2. **Black frame** → no lights, or fog far < camera distance, or camera inside a hill.
   **White frame** → sun + hemisphere both high + fog colour white + bloom.
3. **Scene washed out** → ambient/hemisphere too strong; contrast comes from the sun/shadow
   ratio (sun ≈ 4–6 × hemisphere).
4. **Shadows missing** → `castShadow` on the light AND meshes, `receiveShadow` on ground,
   shadow camera frustum too small, or light too far (increase `far`).
5. **Shadow acne / peter-panning** → `sun.shadow.bias = -0.0005`, keep mapSize 2048.
6. **`Math.random()` anywhere** → different geometry every build; renders not reproducible;
   use `rand(i)` hashes with explicit seeds.
7. **fps < 20** → count draw calls first (`scene_probe`); the usual criminals: hundreds of
   Meshes that should be instances, per-frame `traverse`, 4096 shadow maps, fog off with a
   500 m far plane.
8. **Fog hides everything** → `Fog(near, far)` in metres — near 45/far 160 for a 100 m
   scene; never near 1/far 10.
9. **Sky dome black or fogged** → sky material needs `fog: false`, `side: BackSide`,
   `depthWrite: false`, and the tonemap/colorspace includes (see the env recipe).
10. **GLB asset invisible** → wrong URL (must be `/assets/<name>.glb`), or its pivot is not
    at the base; re-export from Blender with the object on z = 0.
11. **Camera inside a tree / wall** → positions are guesses; compute from `heightAt` +
    known object positions, then check with `render_sheet` (the harness flags
    camera-inside-geometry).
12. **update() allocates** (`new Vector3` per frame, `getSize` per frame) → GC hitches;
    hoist scratch objects to module scope.
13. **Zones overlap / fight** → each zone stays in its plan rectangle; shared borders
    belong to the env (paths, fences).
14. **Lights per lantern** → 30 PointLights kill the frame; ONE emissive material + 1–2
    real lights near the camera path.

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
  if (tris > 2e6) throw new Error(`tri budget blown: ${tris}`);
  if (draws > 200) throw new Error(`draw calls: ${draws} > 200`);
  if (shadowLights > 3) throw new Error(`${shadowLights} shadow lights (max 3)`);
  if (!scene.fog) throw new Error('scene.fog not set');
  if (!cameras || cameras.length < 2) throw new Error('need 3-5 cameras');
  console.log(`[selfcheck] draws=${draws} tris=${Math.round(tris)} lights=${lights} cameras=${cameras.length}`);
}
const demoScene = new THREE.Scene(); buildEnv(THREE, demoScene);
demoScene.add(orchardDemo, campfireDemo, ff);
sceneSelfcheck(THREE, demoScene, planCameras(THREE, demoScene));
```

Then: `build` → `scene_probe` (draws/tris/fps/console) → `render_sheet` per camera →
`shader_probe` if you wrote GLSL → fix the worst → repeat.  Look at every camera's frame:
no black, no white-out, nothing floating, the subject actually in frame.
