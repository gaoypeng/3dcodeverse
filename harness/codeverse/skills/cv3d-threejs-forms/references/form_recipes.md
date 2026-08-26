# One recipe per FORM word

Every snippet below was executed against the harness's own `three@0.182` in node
on 2026-08-25 and printed the counts shown. Adapt the numbers; keep the shape of
the call.

## shell with wall thickness — the boolean you do not have

A cup, a housing, a bin, a pot. Not "cylinder minus cylinder": one lathe profile
that climbs the outer wall, crosses the rim and comes back down the inside.

```js
function cupGeometry(rOuter, wall, h, floor) {
  const rIn = rOuter - wall;
  const p = [[0, 0], [rOuter, 0], [rOuter, h], [rIn, h], [rIn, floor], [0, floor]];
  return new THREE.LatheGeometry(p.map(([r, y]) => new THREE.Vector2(r, y)), 48);
}
// cupGeometry(0.042, 0.004, 0.095, 0.006) -> 294 vertices
```

The rim between `[rOuter, h]` and `[rIn, h]` is the visible wall thickness —
refinement kind 6, free, and the single clearest signal that a vessel was
modelled rather than approximated by a solid cylinder.

## carved cavity — holes belong in the outline

```js
function bracketGeometry(w, d, t, holeR, holeAt) {
  const s = new THREE.Shape();
  s.moveTo(-w/2, -d/2); s.lineTo(w/2, -d/2); s.lineTo(w/2, d/2); s.lineTo(-w/2, d/2); s.closePath();
  for (const [hx, hz] of holeAt) {
    const hole = new THREE.Path();
    hole.absarc(hx, hz, holeR, 0, Math.PI * 2, true);   // `true` = clockwise, required for a hole
    s.holes.push(hole);
  }
  const g = new THREE.ExtrudeGeometry(s, { depth: t, bevelEnabled: true, bevelThickness: 0.0006,
                                           bevelSize: 0.0006, bevelSegments: 1, curveSegments: 16 });
  g.rotateX(-Math.PI / 2);       // ExtrudeGeometry grows along +Z; lay it flat
  g.translate(0, t / 2, 0);      // and sit its underside on y = 0
  return g;
}
// bracketGeometry(0.09, 0.05, 0.004, 0.003, [[-0.03, 0], [0.03, 0]]) -> 548 triangles
```

Two refinement kinds in one call: the holes (kind 5) and the bevel (kind 1).

A slot or a rebate that does not pass through works the same way one level up:
build the face out of the slabs *around* the recess rather than trying to remove
material from a single slab.

## radial array — one geometry, one draw call

```js
const teeth = new THREE.InstancedMesh(
  new THREE.BoxGeometry(0.0015, 0.026, 0.0012),
  new THREE.MeshStandardMaterial({ color: 0xeeeeee }), 14);
const M = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
const pos = new THREE.Vector3(), sc = new THREE.Vector3(1, 1, 1);
for (let i = 0; i < 14; i++) {
  const a = i / 14 * Math.PI * 2;                    // angle from the COUNT, never a typed list
  q.setFromAxisAngle(up, a);
  pos.set(0.016 * Math.cos(a), 0.168, 0.016 * Math.sin(a));
  M.compose(pos, q, sc);
  teeth.setMatrixAt(i, M);
}
teeth.instanceMatrix.needsUpdate = true;   // or the copies all sit at the origin
teeth.computeBoundingSphere();             // or the whole mesh is frustum-culled away
teeth.name = 'ConeTeeth';
```

Remember what this costs at the gate: the exporter turns `ConeTeeth` into a
group of `ConeTeeth_0 … ConeTeeth_13`, so if the copies do not touch the body
they read as thirteen stray islands. Sink each copy into the surface.

## faceted solid — raw BufferGeometry

```js
function wedge(w, h, d) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(
    [-w/2,0,d/2,  w/2,0,d/2,  w/2,0,-d/2,  -w/2,0,-d/2,  -w/2,h,-d/2,  w/2,h,-d/2], 3));
  g.setIndex([0,2,1, 0,3,2, 0,1,5, 0,5,4, 1,2,5, 3,4,2, 4,5,2, 0,4,3]);
  g.computeVertexNormals();          // omit this and the mesh renders black
  return g;
}
```

Wind every triangle counter-clockwise seen from outside. If a face renders dark
while its neighbours are lit, that triangle's index order is reversed — fix the
order, do not add a light and do not set `side: DoubleSide` to hide it.

## merging the pieces of one part

```js
const a = new THREE.BoxGeometry(0.04, 0.012, 0.05).toNonIndexed(); a.deleteAttribute('uv');
const b = new THREE.BoxGeometry(0.04, 0.012, 0.05).translate(0, 0.02, 0).toNonIndexed(); b.deleteAttribute('uv');
const merged = mergeGeometries([a, b]);   // -> 72 vertices
merged.computeVertexNormals();
```

`mergeGeometries` throws "All geometries must have compatible attributes" when
one input has `uv` or an index and another does not. Normalise first
(`toNonIndexed()` everything and delete the attributes you are not using), merge,
then recompute normals. Merging is for static detail *inside* one part — keep one
`THREE.Group` per plan part so the census and the gates still see your parts.

## revolved profile, tube and rounded slab, in one line each

```js
const knob  = new THREE.LatheGeometry([[0,0],[0.014,0],[0.016,0.004],[0.010,0.018],[0,0.020]]
                .map(([r, y]) => new THREE.Vector2(r, y)), 32);
const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(0.05,0.30,0), new THREE.Vector3(0.10,0.20,0.02),
                                          new THREE.Vector3(0.18,0.08,0), new THREE.Vector3(0.30,0.01,-0.03)]);
const handle = new THREE.TubeGeometry(curve, 48, 0.004, 10, false);
const cushion = new RoundedBoxGeometry(0.40, 0.10, 0.40, 4, 0.03);   // 2916 vertices
```

The `LatheGeometry` profile is the highest-value call in this language: a taper
(kind 2), a curved flank (kind 3), a shaped edge (kind 4) and a rim (kind 6) all
come out of the same list of points at no extra cost, and it is the difference
between a knob that reads as turned and a knob that reads as a cylinder.
