# three.js cookbook — static objects, three r182 (ESM), exported to GLB by the harness

Every `js` snippet runs as-is in node (the harness test suite concatenates them with
`THREE`, `mergeGeometries` and `RoundedBoxGeometry` in scope).  Y-up, +Z front, meters,
PascalCase Group names.  The harness inlines the relevant chapters into your prompts;
the full file is at `.3dcode/cookbook.md` in your workspace.

## Module structure (skeleton)

`src/parts/<snake>.js` — one builder per plan part, returns a Group **at world pose**:

```js
import * as THREE from 'three';
// ---- plan numbers (metres) ---------------------------------------------------
const SEAT_W = 0.44, SEAT_D = 0.44, SEAT_T = 0.035, SEAT_H = 0.45;
const LEG = 0.035, WELD = 0.003;                  // parts overlap by WELD so they touch

// ---- tiny helpers (copy into every part file; no shared helper module is allowed) ----
const rand = (i) => { const s = Math.sin(i * 12.9898 + 78.233) * 43758.5453; return s - Math.floor(s); };
function mesh(geo, mat, name, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(geo, mat); m.name = name; m.position.set(x, y, z);
  m.castShadow = m.receiveShadow = true; return m;
}
const MAT = {
  oak: () => new THREE.MeshStandardMaterial({ color: 0x8a5a2b, roughness: 0.6, metalness: 0.0 }),
  steel: () => new THREE.MeshStandardMaterial({ color: 0x9a9da2, roughness: 0.3, metalness: 1.0 }),
};

export function buildSeat(THREE) {
  const g = new THREE.Group(); g.name = 'Seat';
  g.add(mesh(new RoundedBoxGeometry(SEAT_W, SEAT_T, SEAT_D, 3, 0.006), MAT.oak(), 'SeatSlab',
             0, SEAT_H - SEAT_T / 2, 0));
  return g;
}
export function buildLegs(THREE) {
  const g = new THREE.Group(); g.name = 'Legs';
  const geo = new THREE.BoxGeometry(LEG, SEAT_H - SEAT_T + WELD, LEG);
  [[1, 1], [-1, 1], [-1, -1], [1, -1]].forEach(([sx, sz], i) =>
    g.add(mesh(geo, MAT.oak(), `Leg${i + 1}`, sx * (SEAT_W / 2 - LEG / 2 - 0.01),
               (SEAT_H - SEAT_T + WELD) / 2, sz * (SEAT_D / 2 - LEG / 2 - 0.01))));
  return g;
}
```

`src/object.js` — assembles parts; nothing else:

```js
export function build(THREE) {
  const root = new THREE.Group(); root.name = 'Chair';
  root.add(buildSeat(THREE), buildLegs(THREE));
  return root;            // Y-up, lowest point y = 0, no root.scale
}
```

Why: the GLB node names come from Group/Mesh names; geometry in metres at world pose
means `Box3().setFromObject(root)` equals what the harness measures.

## Geometry toolkit

```js
// BoxGeometry(w, h, d): x, y, z extents.  CylinderGeometry(rTop, rBottom, h, radial, heightSeg): axis Y.
// SphereGeometry(r, w, h).  TorusGeometry(R, r, radial, tubular): ring in the XY plane (rotateX(PI/2) to lay flat).
// ConeGeometry(r, h, seg).  PlaneGeometry(w, h): XY plane, faces +Z (rotateX(-PI/2) for a floor).
const ring = new THREE.TorusGeometry(0.03, 0.006, 12, 32).rotateX(Math.PI / 2);   // flat ring, axis Y

// LatheGeometry: revolve a profile of Vector2(radius, y) around Y — bottles, vases, knobs, legs
const profile = [[0, 0], [0.032, 0], [0.034, 0.01], [0.034, 0.16], [0.014, 0.21], [0.014, 0.25], [0, 0.25]]
  .map(([r, y]) => new THREE.Vector2(r, y));
const bottle = new THREE.LatheGeometry(profile, 48);          // normals point outward when r goes 0→R→0 bottom-up

// ExtrudeGeometry from a Shape (+ holes) with bevel: plates, brackets, letters, panels
const shp = new THREE.Shape();
shp.moveTo(-0.05, 0); shp.lineTo(0.05, 0); shp.lineTo(0.05, 0.03); shp.lineTo(-0.05, 0.03); shp.closePath();
const hole = new THREE.Path(); hole.absarc(0.03, 0.015, 0.006, 0, Math.PI * 2, true); shp.holes.push(hole);
const plate = new THREE.ExtrudeGeometry(shp, { depth: 0.004, bevelEnabled: true, bevelThickness: 0.0008,
                                               bevelSize: 0.0008, bevelSegments: 2, curveSegments: 16 });
plate.rotateX(-Math.PI / 2);                                   // extrude goes +Z; lay it flat (thickness along Y)

// Rounded rectangle shape helper (use for table tops, seats, screens)
function roundedRectShape(w, h, r) {
  const s = new THREE.Shape(); const x = -w / 2, y = -h / 2;
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r); s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h); s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y); return s;
}
const top = new THREE.ExtrudeGeometry(roundedRectShape(0.6, 0.4, 0.02), { depth: 0.03, bevelEnabled: false }).rotateX(-Math.PI / 2);

// TubeGeometry along a CatmullRomCurve3: cables, handles, pipes, railings
const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(0.05, 0.3, 0), new THREE.Vector3(0.1, 0.2, 0.02),
                                          new THREE.Vector3(0.18, 0.08, 0), new THREE.Vector3(0.3, 0.01, -0.03)]);
const cable = new THREE.TubeGeometry(curve, 48, 0.004, 10, false);

// RoundedBoxGeometry(w, h, d, segments, radius) — the default box for anything touched by hands
const cushion = new RoundedBoxGeometry(0.4, 0.1, 0.4, 4, 0.03);

// mergeGeometries: many static pieces → ONE mesh (fewer draw calls; keep per-part Groups for naming)
function placed(geo, x, y, z, ry = 0) { const g = geo.clone(); g.rotateY(ry); g.translate(x, y, z); return g; }
const slats = mergeGeometries([0, 1, 2, 3].map(i => placed(new THREE.BoxGeometry(0.4, 0.012, 0.05), 0, 0.6 + i * 0.08, -0.2)));
slats.computeVertexNormals();                                  // always after merges / manual edits

// InstancedMesh for N identical small parts (bolts, balusters, spokes)
const bolt = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.004, 0.004, 0.002, 12),
                                     new THREE.MeshStandardMaterial({ color: 0x333333, metalness: 1, roughness: 0.4 }), 8);
const M = new THREE.Matrix4();
for (let i = 0; i < 8; i++) { const a = i / 8 * Math.PI * 2; M.makeTranslation(0.1 * Math.cos(a), 0.031, 0.1 * Math.sin(a)); bolt.setMatrixAt(i, M); }
bolt.instanceMatrix.needsUpdate = true; bolt.computeBoundingSphere(); bolt.name = 'Bolts';
console.log('toolkit ok', bottle.attributes.position.count, plate.attributes.position.count, slats.attributes.position.count);
```

Why: these are the only geometry makers you need; everything else is position/rotation.
Segments: 24–48 radial for visible cylinders, 8–12 for rods; tube radial 8–12.

## Pivots and animation (optional `userData.tick`)

```js
// Rotate/translate a Group placed at the joint, never the mesh itself; identity scale on pivots.
function buildFan(THREE) {
  const g = new THREE.Group(); g.name = 'Fan';
  const hub = new THREE.Group(); hub.name = 'FanHub'; hub.position.set(0, 1.2, 0.05);   // pivot at the axle
  const bladeGeo = new THREE.BoxGeometry(0.02, 0.4, 0.004).translate(0, 0.25, 0);      // blade root at the hub
  for (let i = 0; i < 3; i++) {
    const b = new THREE.Mesh(bladeGeo, new THREE.MeshStandardMaterial({ color: 0xdddddd }));
    b.name = `Blade${i + 1}`; b.rotation.z = i * Math.PI * 2 / 3; hub.add(b);
  }
  g.add(hub);
  g.userData.tick = (dt) => { hub.rotation.z += dt * 2.0; };   // rad/s
  return g;
}
const fan = buildFan(THREE); fan.userData.tick(0.016);
```

Why: a pivot Group makes the rotation axis explicit and keeps geometry static;
geometry `.translate()` moves the blade root to the pivot so it spins correctly.

## Materials (plain-object presets; only Standard/Physical export to GLB)

```js
const PRESETS = {
  oak:      { color: 0x8a5a2b, roughness: 0.60, metalness: 0.0 },
  walnut:   { color: 0x4a2c17, roughness: 0.55, metalness: 0.0 },
  steel:    { color: 0x9a9da2, roughness: 0.30, metalness: 1.0 },
  brass:    { color: 0xd4a24c, roughness: 0.35, metalness: 1.0 },
  black_plastic: { color: 0x111111, roughness: 0.45, metalness: 0.0 },
  white_ceramic: { color: 0xf0efe8, roughness: 0.15, metalness: 0.0 },
  fabric:   { color: 0x5a6a8c, roughness: 0.95, metalness: 0.0 },
  rubber:   { color: 0x151515, roughness: 0.85, metalness: 0.0 },
  glass:    { color: 0xdfe9f2, roughness: 0.05, metalness: 0.0, transparent: true, opacity: 0.35 },
  emissive: { color: 0xfff1c0, emissive: 0xffe08a, emissiveIntensity: 2.0, roughness: 0.4 },
};
const mat = (key, extra = {}) => new THREE.MeshStandardMaterial({ ...PRESETS[key], ...extra });
const glassMat = new THREE.MeshPhysicalMaterial({ color: 0xffffff, transmission: 0.9, roughness: 0.05, thickness: 0.005, transparent: true });

// vertex colours (exported as COLOR_0): cheap variation without textures
function colorByHeight(geo, lo = new THREE.Color(0x2b5a1f), hi = new THREE.Color(0x9bd65a)) {
  const pos = geo.attributes.position, col = new Float32Array(pos.count * 3), c = new THREE.Color();
  geo.computeBoundingBox(); const y0 = geo.boundingBox.min.y, y1 = geo.boundingBox.max.y || 1e-6;
  for (let i = 0; i < pos.count; i++) { c.copy(lo).lerp(hi, (pos.getY(i) - y0) / (y1 - y0)); col.set([c.r, c.g, c.b], i * 3); }
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  return new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.8 });
}
const leafMat = colorByHeight(new THREE.SphereGeometry(0.5, 16, 12));
console.log('materials ok', mat('oak').color.getHexString(), leafMat.vertexColors);
```

Why: `MeshStandardMaterial` maps 1:1 to glTF PBR; `ShaderMaterial`, `MeshToonMaterial` and
procedural textures are lost on export.  Colours given as hex are sRGB (correct);
`material.color.r` is linear internally — don't compare it with hex.

## Detail (how to look good cheaply)

| feature | recipe | size |
|---|---|---|
| rounded edges | `RoundedBoxGeometry(w,h,d, 3, r)` | r 2–6 mm furniture, 0.5–1 mm devices |
| chamfered plate | `ExtrudeGeometry(shape, {bevelEnabled, bevelSize 1 mm, bevelSegments 1})` | |
| seams / panel gaps | build panels 1 mm apart, darker material in the gap OR a thin dark box | 0.8–1.5 mm |
| inset panel | a slightly smaller box 1–2 mm proud/sunk into the face | inset 5–10 mm |
| fasteners | `InstancedMesh` of 12-seg cylinders, head Ø 5–8 mm, h 1–2 mm, sunk 0.5 mm | |
| trim / edge band | `TubeGeometry` along the outline or a thin `TorusGeometry` | Ø 4–10 mm |
| feet / glides | cylinders Ø 15–25 mm, h 5–10 mm under legs | |
| cushions | `RoundedBoxGeometry(w, 0.08–0.12, d, 4, 0.03)` | |
| handles / knobs | `TorusGeometry` / `LatheGeometry`, overlapping the panel 2 mm | knob Ø 25–35 mm |
| fabric / wood variation | `colorByHeight` or per-instance colour via `setColorAt` | |

Rules: ≥ 3 distinct masses in the silhouette; break every large flat face once; thin
things are 1–3 mm, never 0; anything hands touch gets a radius.

## Density: visual complexity without hand-modelling every screw

Measured on this harness (183 judged rounds): **triangles inside a part are free score, extra
top-level parts are not** — ρ(tri_per_part, geometry_detail) ≈ +0.08 while ρ(n_plan_parts,
assembly_fit) = −0.48.  Everything below adds density *inside* a part the plan already names,
so `src/parts/<snake>.js` still returns exactly one `THREE.Group` called `<PascalName>`.

**Where the budget goes** — silhouette first, then the 2–3 largest faces, then whatever is at eye
height and at +Z (the front), then where two materials meet.  Not: the underside, the back, or
interior volumes.  Skip subdividing a flat panel; add a seam instead.

| priority | what | tri cost | recipe |
|---|---|---|---|
| 1 | silhouette break (taper, waist, overhang) | 0 | `LatheGeometry`, `ExtrudeGeometry` of the real section |
| 2 | rounded edges everywhere hands touch | ×2–4 on that piece | `RoundedBoxGeometry`, `bevelEnabled` |
| 3 | panel lines on big faces | ~100 | thin dark boxes 1 mm proud in the seam |
| 4 | fasteners at real joints | 50–150 each | `instanceRing` |
| 5 | counted repeats (slats, spokes, dentils) | 100–400 each | `repeatMerged` |
| 6 | greebles in a bounded patch | 300–1500 | `greeblePatch` |
| 7 | per-instance colour/scale variation | 0 | `setColorAt`, seeded jitter |

```js
// --- seeded RNG: identical repeats read as CG, 2-5 % variation reads as real.
// NEVER Math.random() — the harness rebuilds your code and the judge sees both builds.
function rng(seed) { let s = seed >>> 0 || 1; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296; }

// --- repeatMerged: N placed copies of one geometry, merged into ONE mesh.
// The cheapest countable detail there is: spokes, slats, dentils, rafter tails, balusters.
function repeatMerged(geo, n, place, { seed = 0, jitter = 0, tilt = 0, scale = 0 } = {}) {
  const r = rng(seed), out = [];
  for (let i = 0; i < n; i++) {
    const g = geo.clone();
    const s = 1 + (r() * 2 - 1) * scale;
    if (scale) g.scale(s, s, s);
    if (tilt) { g.rotateX((r() * 2 - 1) * tilt); g.rotateZ((r() * 2 - 1) * tilt); }
    const [x, y, z] = place(i, r);
    g.translate(x + (r() * 2 - 1) * jitter, y + (r() * 2 - 1) * jitter, z + (r() * 2 - 1) * jitter);
    out.push(g);
  }
  const merged = mergeGeometries(out);
  merged.computeVertexNormals();
  return merged;
}

// --- repeatRadial: N copies ROTATED around an axis through the origin.  Spokes, dentils,
// cage wires, flutes, balusters, turbine blades — anything on a circle.  Merged into one mesh.
function repeatRadial(geo, n, { axis = 'y', phase = 0, seed = 0, tilt = 0 } = {}) {
  const r = rng(seed), out = [];
  for (let i = 0; i < n; i++) {
    const g = geo.clone(), a = phase + (i / n) * Math.PI * 2;
    if (tilt) g.rotateX((r() * 2 - 1) * tilt);
    if (axis === 'y') g.rotateY(a); else if (axis === 'x') g.rotateX(a); else g.rotateZ(a);
    out.push(g);
  }
  const merged = mergeGeometries(out);
  merged.computeVertexNormals();
  return merged;
}

// 24 wheel spokes, ONE mesh: a rod laid along X, rotated 24× about Z (the wheel's axis)
const spokeGeo = new THREE.CylinderGeometry(0.004, 0.004, 0.62, 6).rotateZ(Math.PI / 2);
const spokes = new THREE.Mesh(repeatRadial(spokeGeo, 24, { axis: 'z' }),
                              new THREE.MeshStandardMaterial({ metalness: 0.9, roughness: 0.35 }));
spokes.name = 'Spokes';
```

```js
// --- instanceRing: N identical fasteners as ONE InstancedMesh with per-instance colour.
// The harness BAKES InstancedMesh into plain `<Name>_<i>` meshes at GLB export, so the
// instance count becomes real objects — use it for 6-40 small repeats, not for 500.
function instanceRing(geo, mat, n, radius, y, { seed = 0, tint = 0 } = {}) {
  const r = rng(seed), im = new THREE.InstancedMesh(geo, mat, n);
  const m = new THREE.Matrix4(), c = new THREE.Color();
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    m.makeRotationY(-a);
    m.setPosition(radius * Math.cos(a), y, radius * Math.sin(a));
    im.setMatrixAt(i, m);
    if (tint) { c.setHSL(0, 0, 0.5 + (r() * 2 - 1) * tint); im.setColorAt(i, c); }
  }
  im.instanceMatrix.needsUpdate = true;
  if (im.instanceColor) im.instanceColor.needsUpdate = true;
  im.computeBoundingSphere();
  return im;
}

const boltHead = new THREE.CylinderGeometry(0.005, 0.0045, 0.002, 8);
const bolts = instanceRing(boltHead, new THREE.MeshStandardMaterial({ metalness: 1, roughness: 0.45 }),
                           8, 0.09, 0.031, { seed: 4, tint: 0.06 });
bolts.name = 'FlangeBolts';

// --- greeblePatch: seeded surface clutter bounded to a rectangle, merged into one mesh.
// Bound it so it can never break the silhouette or leave the part's planned bbox.
function greeblePatch(w, d, { n = 24, seed = 0, hMin = 0.002, hMax = 0.010, cell = 0.02 } = {}) {
  const r = rng(seed), out = [];
  for (let i = 0; i < n; i++) {
    const bw = cell * (0.35 + r()), bd = cell * (0.35 + r()), bh = hMin + r() * (hMax - hMin);
    const g = new THREE.BoxGeometry(bw, bh, bd);
    g.translate((r() - 0.5) * (w - bw), bh / 2, (r() - 0.5) * (d - bd));
    out.push(g);
  }
  const merged = mergeGeometries(out);
  merged.computeVertexNormals();
  return merged;
}

// --- panelLines: 1 mm dark ribs sunk into a face — the cheapest "manufactured" cue there is.
function panelLines(lines, thickness = 0.0015) {
  const geos = lines.map(([x, y, z, w, h]) =>
    new THREE.BoxGeometry(Math.max(w, thickness), Math.max(h, thickness), thickness).translate(x, y, z));
  const merged = mergeGeometries(geos);
  merged.computeVertexNormals();
  return merged;
}

// one housing part: shell + greebles + seams + bolts = ONE Group named as the plan says
const housing = new THREE.Group();
housing.name = 'MotorHousing';
housing.add(new THREE.Mesh(new RoundedBoxGeometry(0.24, 0.10, 0.16, 3, 0.006),
                           new THREE.MeshStandardMaterial({ color: 0x5a5f66, roughness: 0.55 })));
housing.add(new THREE.Mesh(greeblePatch(0.20, 0.12, { n: 26, seed: 7 }).translate(0, 0.05, 0),
                           new THREE.MeshStandardMaterial({ color: 0x4c5158, roughness: 0.6 })));
housing.add(new THREE.Mesh(panelLines([[0, 0.0, 0.0805, 0.22, 0.001], [0, -0.03, 0.0805, 0.22, 0.001]]),
                           new THREE.MeshStandardMaterial({ color: 0x1a1c1f, roughness: 0.9 })));
housing.add(bolts);
```

```js
// --- profile sweeps: draw the SECTION once, drag it along the path.  Mouldings, handrails,
// rims, gutters, cornices.  A curved path needs one sample per 5-10 degrees, not four corners.
function sweep(sectionPts, pathPts, { closed = false, radialSegments = 1 } = {}) {
  const shape = new THREE.Shape(sectionPts.map(([x, y]) => new THREE.Vector2(x, y)));
  const curve = new THREE.CatmullRomCurve3(pathPts.map(([x, y, z]) => new THREE.Vector3(x, y, z)), closed);
  const geo = new THREE.ExtrudeGeometry(shape, { steps: pathPts.length * 2, bevelEnabled: false,
                                                 extrudePath: curve, curveSegments: 4 * radialSegments });
  geo.computeVertexNormals();
  return geo;
}

const HANDRAIL = [[0.022, 0], [0.010, 0.009], [-0.010, 0.009], [-0.022, 0], [0, -0.006]];
const helix = [];
for (let a = 0; a <= 360; a += 5) {
  const t = (a * Math.PI) / 180;
  helix.push([0.70 * Math.cos(t), 0.95 + 0.0075 * a, 0.70 * Math.sin(t)]);
}
const handrail = new THREE.Mesh(sweep(HANDRAIL, helix), new THREE.MeshStandardMaterial({ color: 0x2b2b2b }));
handrail.name = 'Handrail';

// --- the density check: what would tell a photo of the real thing from this?
// "edges are perfectly sharp" -> RoundedBoxGeometry / bevelEnabled.  "one flat face" -> panelLines.
// "no fixings" -> instanceRing.  "repeats are identical" -> repeatMerged with jitter/tilt/scale.
// "all one grey" -> split the materials.  Then count: `measure` prints the triangle total.
const density = [spokes, bolts, housing, handrail];
let densityTris = 0;
for (const o of density) o.traverse((m) => { if (m.isMesh) densityTris += (m.geometry.index ? m.geometry.index.count : m.geometry.attributes.position.count) / 3 * (m.count || 1); });
console.log('[selfcheck] density recipes tris', Math.round(densityTris), 'parts', density.length);
if (densityTris < 3000) throw new Error('density recipes produced almost no geometry: ' + densityTris);
```

## Common objects — dimensions (metres, Y-up) and decomposition

| object | W × D × H | parts | key numbers |
|---|---|---|---|
| dining chair | 0.45 × 0.50 × 0.90 | Seat, Backrest, Leg1..4, Stretchers | seat y 0.45, t 0.035, leg 0.035, rake 8° |
| sofa | 2.0 × 0.9 × 0.85 | Base, SeatCushion1..3, BackCushion1..3, ArmLeft/Right, Legs | seat y 0.42, arm y 0.60, cushion 0.11 |
| table | 1.6 × 0.9 × 0.75 | Top, Apron, Leg1..4 | top t 0.035, legs 0.07 at 0.05 inset |
| desk lamp | Ø 0.18 × 0.55 | Base, ArmLower, ArmUpper, Shade, Bulb | arm Ø 0.012, shade Ø 0.15 |
| mug | Ø 0.085 × 0.095 | Body (Lathe), Handle (Tube) | wall 4 mm, handle Ø 12 mm |
| bottle | Ø 0.075 × 0.30 | Body (Lathe), Cap | neck Ø 0.028 |
| bicycle | 1.75 × 0.6 × 1.05 | Frame(Tubes), FrontWheel/RearWheel(Torus + spokes), Fork, Handlebar, Saddle, Crank, Pedals, Chain | wheel Ø 0.70, tyre 0.03, BB y 0.27 |
| car | 4.6 × 1.85 × 1.45 | Body(Extrude side profile), Cabin, Wheel×4, Bumpers, Mirrors, Lights | wheel Ø 0.65, wheelbase 2.7, clearance 0.15 |
| house | 10 × 8 × 7 | Walls, Roof(Extrude triangle), Door, Windows, Chimney | storey 2.8, pitch 35°, door 0.9 × 2.1 |
| tree | crown Ø 7 × H 12 | Trunk(Cylinder taper), Branches(Tube), Crown(3–6 spheres, vertex colours) | trunk Ø 0.4 |
| bookshelf | 0.8 × 0.3 × 1.8 | SideLeft/Right, Top, Bottom, Shelf1..4, Back | panel 18 mm |
| monitor | 0.62 × 0.2 × 0.45 | Panel, Stand, Base | bezel 8 mm |

Decomposition: 1 main mass → 2–6 secondary masses touching it → attachments overlapping
≥ 2 mm → detail pass.  Z is the FRONT: the chair's backrest sits at −Z, seat front at +Z.

## Raw BufferGeometry and a spoked wheel (when no primitive fits)

```js
// Custom BufferGeometry: positions (+ index) → computeVertexNormals().  A ramp wedge:
function wedgeGeometry(w, h, d) {
  const g = new THREE.BufferGeometry();
  const v = [ -w/2,0,d/2,  w/2,0,d/2,  w/2,0,-d/2, -w/2,0,-d/2,   // base quad
              -w/2,h,-d/2,  w/2,h,-d/2 ];                          // top back edge
  const idx = [0,2,1, 0,3,2,  0,1,5, 0,5,4,  1,2,5,  3,4,2, 4,5,2, 0,4,3];
  g.setAttribute('position', new THREE.Float32BufferAttribute(v, 3));
  g.setIndex(idx);
  g.computeVertexNormals();                    // ALWAYS, or it shades black
  return g;
}
const ramp = new THREE.Mesh(wedgeGeometry(0.6, 0.25, 0.5), new THREE.MeshStandardMaterial({ color: 0x777777 }));

// Spoked wheel: torus tyre + lathed rim + instanced spokes + hub — the bicycle staple
function buildWheel(THREE, R = 0.35, tyreR = 0.017, spokes = 18) {
  const g = new THREE.Group(); g.name = 'Wheel';
  const tyre = new THREE.Mesh(new THREE.TorusGeometry(R, tyreR, 10, 48),
                              new THREE.MeshStandardMaterial({ color: 0x151515, roughness: 0.85 }));
  tyre.name = 'Tyre'; g.add(tyre);
  // the torus lies in the XY plane, so this wheel's axle is the LOCAL Z axis; build the
  // whole wheel that way and let the parent orient it (axle along X: wheel.rotateY(PI/2)).
  const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.08, 16).rotateX(Math.PI / 2),
                             new THREE.MeshStandardMaterial({ color: 0x8a8d92, metalness: 1, roughness: 0.3 }));
  hub.name = 'Hub'; g.add(hub);
  const spokeGeo = new THREE.CylinderGeometry(0.0012, 0.0012, R - 0.02, 5).translate(0, (R - 0.02) / 2 + 0.015, 0);
  const spoke = new THREE.InstancedMesh(spokeGeo, hub.material, spokes);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), z = new THREE.Vector3(0, 0, 1);
  for (let i = 0; i < spokes; i++) {
    q.setFromAxisAngle(z, i / spokes * Math.PI * 2);
    m.makeRotationFromQuaternion(q);
    spoke.setMatrixAt(i, m);
  }
  spoke.computeBoundingSphere(); spoke.name = 'Spokes'; g.add(spoke);
  return g;   // parent: wheel.rotateY(Math.PI/2) to put the axle along X, then position it
}
const wheel = buildWheel(THREE);
console.log('wheel parts', wheel.children.map(c => c.name), 'ramp tris', ramp.geometry.index.count / 3);
```

Why: an indexed position buffer plus `computeVertexNormals()` covers any faceted shape a
primitive cannot; a wheel is 3 draw calls at any spoke count thanks to the InstancedMesh.

## Pitfalls (symptom → cause → fix)

1. **Object lies on its side in the render** → you built Z-up.  three.js/glTF are Y-up;
   height is Y, front is +Z.  `CylinderGeometry` axis is Y; floors are `PlaneGeometry` rotated
   `-PI/2` about X.
2. **`mesh.position` vs `geometry.translate`** — position moves the pivot (animation-
   friendly), `geometry.translate()` bakes the offset (needed before `mergeGeometries` and
   for blade-root pivots).  Pick deliberately; never do both by accident.
3. **"All geometries must have compatible attributes"** in `mergeGeometries` → one input
   has `uv`/`color` or an index and another not.  Delete extra attributes
   (`geo.deleteAttribute('uv')`) or `toNonIndexed()` all; then `computeVertexNormals()`.
4. **Faceted / black shading after edits** → call `geometry.computeVertexNormals()`; for
   flipped faces use `geometry.scale(-1,1,1)` carefully or rebuild the profile order.
5. **`userData.tick` never runs** → nothing in the harness calls it (the GLB export drops
   it): the judged pose is the one `build` returns; don't animate scale.
6. **Renderer / Scene / Camera / Light / OrbitControls in object code** → forbidden; the
   module is imported in node with no WebGL.  `document`/`canvas`/`Image`/`TextureLoader`
   crash the build.
7. **Imports that do not exist in r182**: `three/examples/js/*`, `BufferGeometryUtils.
   mergeBufferGeometries` (now `mergeGeometries`), `Geometry`/`Face3`, `THREE.sRGBEncoding`,
   `outputEncoding`.  Use `three/addons/utils/BufferGeometryUtils.js`,
   `three/addons/geometries/RoundedBoxGeometry.js`.
8. **`root.scale.set(0.01, …)` to convert mm → m** → bounds/gates see scaled geometry
   inconsistently; author in metres, root scale stays (1,1,1).
9. **Floating parts** → overlap neighbours by ≥ 2 mm along the contact normal; legs reach
   INTO the seat, handles INTO the body.
10. **Hidden parts via tiny scale** → use `visible = false` or don't add them; never
    `scale.set(0,0,0)` (NaN normals in the exporter).
11. **`InstancedMesh` disappears** → `computeBoundingSphere()` after setting matrices, or
    `frustumCulled = false`; set `instanceMatrix.needsUpdate = true`.
12. **Math.random()** → non-reproducible builds; use the `rand(i)` hash.
13. **ShaderMaterial / textures** → not in GLB; use Standard/Physical + vertex colours.
14. **Huge triangle counts** → `SphereGeometry(r, 64, 64)` ×100 = 800 k tris; use 16×12 for
    small spheres, instancing for repeats, merged geometries for static detail.
15. **Mixed three copies** (`instanceof` failures) → import only from `'three'` and
    `'three/addons/…'`; never copy three sources into `src/`.

## Self-check (before you call it done)

```js
export function selfcheck(THREE, root, expectedNames = [], extentsHint = null) {
  const names = new Set(); let tris = 0;
  root.traverse(o => {
    if (o.name) names.add(o.name);
    if (o.isMesh) { const g = o.geometry; const n = g.index ? g.index.count / 3 : g.attributes.position.count / 3;
                    tris += n * (o.isInstancedMesh ? o.count : 1); }
  });
  const missing = expectedNames.filter(n => !names.has(n));
  if (missing.length) throw new Error(`missing parts: ${missing.join(', ')}`);
  const box = new THREE.Box3().setFromObject(root); const size = box.getSize(new THREE.Vector3());
  if (Math.abs(box.min.y) > 0.002) throw new Error(`lowest point y=${box.min.y.toFixed(4)} (expected 0)`);
  if (extentsHint) extentsHint.forEach((e, i) => { if (Math.abs(size.getComponent(i) - e) > 0.05) throw new Error(`extents ${size.toArray()} vs plan ${extentsHint}`); });
  console.log(`[selfcheck] parts=${names.size} tris=${tris} extents=${size.toArray().map(v => v.toFixed(3))}`);
  return { tris, size };
}
selfcheck(THREE, build(THREE), ['Seat', 'Legs', 'Leg1', 'Leg4'], [0.44, 0.45, 0.44]);
```
The harness also calls an exported `selfcheck(THREE, root)` on the built group (defaults
above make that call permissive); a throw fails the build as `SelfCheckError` with your
message, so keep the assertions truthful.

Then use the tools: `build` → `render_sheet` → `check_connectivity` → `isolate` → `check_contract`.

## Worked example: mug (Lathe body + Tube handle, welded) and desk lamp (pivots)

```js
export function buildMug(THREE) {
  const R = 0.042, H = 0.095, WALL = 0.004, FLOOR = 0.006;
  const g = new THREE.Group(); g.name = 'Mug';
  // profile goes: outside bottom → outside top → inside top → inside floor → axis (closed, outward normals)
  const pts = [[0, 0], [R - 0.006, 0], [R, 0.006], [R, H - 0.004], [R - 0.002, H], [R - WALL, H],
               [R - WALL, FLOOR], [0, FLOOR]].map(([r, y]) => new THREE.Vector2(r, y));
  const body = new THREE.Mesh(new THREE.LatheGeometry(pts, 48), new THREE.MeshStandardMaterial({ color: 0xf0efe8, roughness: 0.2 }));
  body.name = 'MugBody'; g.add(body);
  // handle: a C-curve that starts and ends 3 mm INSIDE the wall (weld), tube Ø 12 mm
  const c = new THREE.CatmullRomCurve3([
    new THREE.Vector3(R - 0.003, H * 0.78, 0), new THREE.Vector3(R + 0.028, H * 0.72, 0),
    new THREE.Vector3(R + 0.034, H * 0.45, 0), new THREE.Vector3(R + 0.022, H * 0.22, 0),
    new THREE.Vector3(R - 0.003, H * 0.20, 0)]);
  const handle = new THREE.Mesh(new THREE.TubeGeometry(c, 32, 0.006, 12, false), body.material);
  handle.name = 'MugHandle'; g.add(handle);
  return g;
}

export function buildDeskLamp(THREE) {
  const g = new THREE.Group(); g.name = 'DeskLamp';
  const metal = new THREE.MeshStandardMaterial({ color: 0x2a2d33, roughness: 0.35, metalness: 0.9 });
  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.10, 0.02, 48), metal); base.name = 'Base'; base.position.y = 0.01;
  // lower arm pivots at the base top; upper arm pivots at the elbow; shade pivots at the wrist
  const shoulder = new THREE.Group(); shoulder.name = 'Shoulder'; shoulder.position.set(0, 0.02 - 0.003, 0); shoulder.rotation.z = -0.35;
  const armLo = new THREE.Mesh(new THREE.CylinderGeometry(0.007, 0.007, 0.30, 16).translate(0, 0.15, 0), metal); armLo.name = 'ArmLower';
  const elbow = new THREE.Group(); elbow.name = 'Elbow'; elbow.position.y = 0.30 - 0.004; elbow.rotation.z = 1.25;
  const armUp = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.26, 16).translate(0, 0.13, 0), metal); armUp.name = 'ArmUpper';
  const wrist = new THREE.Group(); wrist.name = 'Wrist'; wrist.position.y = 0.26 - 0.004; wrist.rotation.z = -1.2;
  const shade = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.075, 0.12, 32, 1, true).translate(0, -0.05, 0),
                               new THREE.MeshStandardMaterial({ color: 0xc8c8c8, roughness: 0.4, metalness: 0.6, side: THREE.DoubleSide }));
  shade.name = 'Shade';
  const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.02, 16, 12), new THREE.MeshStandardMaterial({ color: 0xfff4d0, emissive: 0xffe3a0, emissiveIntensity: 2 }));
  bulb.name = 'Bulb'; bulb.position.y = -0.06;
  wrist.add(shade, bulb); armUp.add(wrist); elbow.add(armUp); armLo.add(elbow); shoulder.add(armLo);
  g.add(base, shoulder);
  g.userData.tick = (dt) => { elbow.rotation.z = 1.25 + 0.15 * Math.sin((elbow.userData.t = (elbow.userData.t || 0) + dt) * 0.8); };
  return g;
}
const lamp = buildDeskLamp(THREE); lamp.userData.tick(0.1);
selfcheck(THREE, buildMug(THREE), ['MugBody', 'MugHandle'], [0.118, 0.095, 0.084]);
const lampBox = new THREE.Box3().setFromObject(lamp); console.log('lamp height', lampBox.max.y.toFixed(3));
```

Why: the mug profile contains the wall (no boolean needed); the handle curve ends inside
the wall so `check_connectivity` sees one island; the lamp's three pivot Groups sit at the
joints so `tick` can articulate it without touching geometry.
