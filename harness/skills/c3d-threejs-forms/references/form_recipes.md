# FORM words under their three.js API names

Options, not the way: each paragraph names the r182 call that produces a form and the trap that
makes it ship nothing or the wrong thing.  What a part is made of is the brief's and the plan's.

## shell with wall thickness

There is no boolean, so a vessel or housing is one `LatheGeometry` whose `Vector2(radius, y)`
profile climbs the outer wall, crosses the rim and comes back down the inside to the floor.  The
rim segment between the outer and inner radius is the visible wall thickness.  Normals point
outward when the profile runs bottom-up on the outside.

## carved cavity

A through-hole belongs in the outline: a `THREE.Shape` with a `THREE.Path` pushed into
`shape.holes`, extruded with `ExtrudeGeometry`.  Trap: a hole drawn with `absarc(x, y, r, 0,
2 * Math.PI, true)` must be CLOCKWISE (the last argument) or the extrude fills it.
`ExtrudeGeometry` grows along +Z, so a plate whose thickness should run along Y needs
`.rotateX(-Math.PI / 2)`.  A recess that does not pass through is built from the slabs around it.

## radial or linear array

`InstancedMesh(geometry, material, N)` with `setMatrixAt(i, matrix)` per copy, the angle or step
derived from N.  Traps: without `instanceMatrix.needsUpdate = true` every copy sits at the origin,
and without `computeBoundingSphere()` the mesh can be frustum-culled away.  The exporter bakes it
into sibling meshes `Name_0 … Name_{N-1}` (the contract's Materials section), so each copy is
measured on its own: a copy that touches nothing is a floating island.

## faceted solid

A raw `BufferGeometry` with a `Float32BufferAttribute` for `position` and `setIndex(...)`.
Traps: without `computeVertexNormals()` it renders black; a triangle wound clockwise seen from
outside renders dark among lit neighbours — fix its index order rather than `side: DoubleSide`.

## merging the pieces of one part

`mergeGeometries` from `three/addons/utils/BufferGeometryUtils.js` returns `null` (and logs "All
geometries must have compatible attributes") when one input is indexed or carries `uv` and
another does not — normalise with `toNonIndexed()` / `deleteAttribute('uv')` first and call
`computeVertexNormals()` after.  Merging keeps every input's vertices, so two pieces that only
overlap stay two shells in one mesh; keep one `THREE.Group` per plan part.

## revolved profile, tube, rounded slab

`LatheGeometry(points, segments)` turns a list of `Vector2(radius, y)` into a taper, a curved
flank and a rim in one call.  `TubeGeometry(new CatmullRomCurve3(points), tubular, radius,
radial, closed)` sweeps a round section along a path.  `RoundedBoxGeometry(w, h, d, segments,
radius)` from `three/addons/geometries/RoundedBoxGeometry.js` is a slab with a soft edge.
