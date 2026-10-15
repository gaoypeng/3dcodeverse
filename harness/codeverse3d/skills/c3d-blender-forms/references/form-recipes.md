# bpy form options

Companion to `c3d-blender-forms`. Written against Blender 5.2.2 LTS, `blender -b
--factory-startup`, no operator context you did not create yourself, the contract's
allowed imports only. Each paragraph names a form under its real API and the trap that
bites it — an option, not the way.

## 1. A beam between two world points

Build the shape along +Z (`bmesh.ops.create_cone` with equal radii and `depth` = the endpoint
distance), then set `obj.matrix_world` from the midpoint translation times
`Vector((0, 0, 1)).rotation_difference(direction).to_matrix().to_4x4()`.  The length then
cannot disagree with the endpoints, and no Euler order is involved — two chained Euler
rotations are the most reliable way to place a strut wrong.

## 2. Revolved profile (lathe)

`bmesh.ops.spin(bm, geom=verts + edges, axis=(0, 0, 1), cent=(0, 0, 0), angle=2π, steps=n,
use_merge=True)` on a `(radius, 0, z)` profile.  Traps: spin needs the EDGES in `geom` (verts
alone give a point cloud); `bmesh.ops.remove_doubles` afterwards welds the seam and the axis
points, and `bmesh.ops.recalc_face_normals` fixes a profile drawn top-down.

## 3. Swept tube along a path

A curve (`bpy.data.curves.new(name, 'CURVE')`, `dimensions = '3D'`, a `POLY` spline whose
`points[i].co = (x, y, z, 1.0)`) with `bevel_depth > 0` and `use_fill_caps = True` exports as a
mesh; with `bevel_depth = 0` it exports no node and the part is reported missing (measured on
5.2.2, contract.md).  When a later step needs real geometry, convert without operators —
`bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))`, then a new object under the
same name — because `bpy.ops.object.convert` needs a selected editable object and fails headless
with "No editable objects to convert".  A bevelled strip can come back as one island per
segment; `remove_doubles` on the converted mesh welds it.

## 4. Shell with a wall thickness

A SOLIDIFY modifier with `offset = -1` grows inward, so the outer surface stays where the plan
bbox puts it; `use_even_offset = True` keeps the wall even on curves.  Left unapplied, the
exporter evaluates it (`export_apply=True`).  A bevel on a shell goes after the solidify in the
stack, or it bevels a zero-thickness surface.

## 5. Cavity, and cleaning up after it

A BOOLEAN modifier with `operation = 'DIFFERENCE'` and `solver` `'MANIFOLD'` (closed inputs),
`'EXACT'` or `'FLOAT'` (`'FAST'` no longer exists).  Applied with `modifier_apply` inside
`bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj])`, then
remove the cutter with `bpy.data.objects.remove(cutter, do_unlink=True)` — or leave the
modifier for the exporter and keep the cutter alive and hidden (`hide_render`), which keeps it
out of the GLB while the cut still happens (measured on 5.2.2).  A cutter coplanar with the
face it cuts fails silently.

## 6. Multi-file wiring

The file layout, the builder names and the three silent failures the lint reports (a part
file never imported, a builder called at import time, a builder not named
`build_<snake>`) are the contract's `Files` section; the contract's executable example is
the whole interface. `urdf_blender` does not use this layout: one `src/model.py`, the object
named after each URDF link (plus anything parented under it, which the wrapper joins into the
link's mesh), and `lint:urdf` checks that each link name appears verbatim in the file.

## 7. The skeleton's self-check

The skeleton's `model.py` ends with an optional `_selfcheck()`: it asserts that meshes exist and
that no name is auto-suffixed, prints the lowest point, and fails the build only past the
contract gate's ERROR line for the ground (3 × `GROUND_TOL_M`).  Keep it, change it or delete
it; `check_contract` and `check_connectivity` are the real census.
