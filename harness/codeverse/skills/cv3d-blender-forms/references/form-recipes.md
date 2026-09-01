# bpy form recipes

Companion to `cv3d-blender-forms`. Every snippet was written against the harness's own
runtime (Blender 5.0.1, `blender -b --factory-startup`, no operator context you did not
create yourself) and uses only the allowed imports. Longer copyable code lives in the
cookbook section "Modifiers" and friends.

## 1. A beam between two world points (instead of rotating a cylinder)

Euler order is the single most reliable way to place a strut wrong. Build along +Z and
rotate once, with a quaternion derived from the endpoints:

```python
import bpy, bmesh
from mathutils import Vector, Matrix

def beam(name, p0, p1, radius, segments=16):
    """A capsule-less cylinder from p0 to p1, correct for any direction."""
    a, b = Vector(p0), Vector(p1)
    d = b - a
    length = d.length
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments,
                          radius1=radius, radius2=radius, depth=length)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me)
    obj.matrix_world = (Matrix.Translation((a + b) / 2.0)
                        @ Vector((0.0, 0.0, 1.0)).rotation_difference(d.normalized()).to_matrix().to_4x4())
    bpy.context.scene.collection.objects.link(obj)
    return obj
```

The length is the distance between the two points, so it cannot disagree with them, and the
endpoints are where you say they are. Compute `p0`/`p1` from the neighbours' plan bboxes
plus the 1 mm weld from `cv3d-part-contact`.

## 2. Revolved profile (lathe)

```python
def lathe(name, profile_xy, segments=48, axis=(0, 0, 1)):
    """profile_xy: [(radius, z)] from bottom to top, in metres, from the plan."""
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0.0, z)) for r, z in profile_xy]
    for v0, v1 in zip(verts, verts[1:]):
        bm.edges.new((v0, v1))
    bmesh.ops.spin(bm, geom=bm.verts[:] + bm.edges[:], axis=axis, cent=(0, 0, 0),
                   dvec=(0, 0, 0), angle=6.283185, steps=segments, use_merge=True)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj
```

A profile of six or eight `(radius, z)` pairs is what turns "a jug" from a cylinder into a
jug. That is the `geometry_detail` criterion in one function.

## 3. Swept tube along a path — and the real export trap

Verified on Blender 5.0.1 against the harness's own export call: a curve object with a
`bevel_depth` **does** export (the glTF writer evaluates it), so the old advice "always
convert or it exports nothing" is not the failure. The failure is a curve with
`bevel_depth = 0` — a zero-thickness path has no surface, the node exports with no faces,
`spatial/measure.py::solid_parts` drops it, and the contract gate reports the part missing.

Give every curve a bevel, and convert to a mesh whenever a later step needs real geometry
(a boolean, a vertex loop, a measurement). Convert without operators — `bpy.ops.object.convert`
needs the object to be a *selected editable* object in the view layer and fails silently in
background mode with "No editable objects to convert":

```python
def curve_to_mesh(obj):
    """Ops-free CURVE -> MESH: evaluate the bevel and swap the data. Keeps the name."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    name = obj.name
    bpy.data.objects.remove(obj, do_unlink=True)
    new = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(new)
    return new

def tube(name, points, radius, resolution=6):
    cu = bpy.data.curves.new(name + "Data", 'CURVE'); cu.dimensions = '3D'
    cu.bevel_depth = radius          # never 0: that is the part-goes-missing bug
    cu.bevel_resolution = resolution; cu.use_fill_caps = True
    sp = cu.splines.new('POLY'); sp.points.add(len(points) - 1)
    for i, (x, y, z) in enumerate(points):
        sp.points[i].co = (x, y, z, 1.0)
    obj = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(obj)
    return curve_to_mesh(obj)
```

## 4. Shell with a wall thickness

```python
sol = obj.modifiers.new("Solidify", 'SOLIDIFY')
sol.thickness = 0.003        # 3 mm wall
sol.offset = -1              # grow INWARD: the outer surface stays on the plan bbox
sol.use_even_offset = True
```

Leave it unapplied. The exporter evaluates modifiers (`export_apply=True`), so the gate
measures the solidified result without you touching operator context.

## 5. Cavity, and cleaning up after it

```python
def cut(obj, cutter, solver='MANIFOLD'):
    m = obj.modifiers.new("Cut", 'BOOLEAN'); m.operation = 'DIFFERENCE'; m.object = cutter
    if solver not in m.bl_rna.properties["solver"].enum_items.keys():
        solver = 'EXACT'
    m.solver = solver
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cutter, do_unlink=True)   # or it exports as its own part
```

The cutter must overshoot the face it pierces and must never be coplanar with it. If you
leave the modifier for the exporter instead of applying it, you must keep the cutter alive
*and* hidden (`hide_render = hide_viewport = True`); both were verified on Blender 5.0.1 to
keep it out of the GLB while the cut still happens.

## 6. Multi-file wiring (static_object/blender only)

```
src/model.py                 imports every builder, calls each ONCE, then self-checks
src/parts/seat_cushion.py    def build_seat_cushion() -> bpy.types.Object   (defines only)
src/parts/_common.py         shared helpers; the underscore means "not a part"
```

Three failure modes the lint reports, all of them silent at build time:

* the part file exists but `model.py` never imports it -> the part is simply absent;
* the part file calls its own builder at module level -> the part is built twice and the
  second copy is named `SeatCushion.001`;
* the builder is named something other than `build_<snake of the plan name>` -> ImportError
  with a hint naming the exact function it expected.

`urdf_blender` does not use this layout: one `src/model.py`, one mesh object per URDF link,
object name equal to the link name, and `lint:urdf` checks that each link name appears
verbatim in the file.

## 7. A self-check worth writing

```python
def selfcheck(expected):
    names = sorted(o.name for o in bpy.data.objects if o.type == 'MESH')
    assert not [n for n in names if '.' in n], f"auto-suffixed: {names}"
    assert sorted(expected) == names, f"expected {sorted(expected)}, built {names}"
```

Two asserts, run at the end of `main()`, catch the missing part and the duplicated part
before the build report does.
