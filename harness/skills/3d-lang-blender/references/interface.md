# The interface, executable (Blender 5.2.2 LTS headless)

Two plan parts, `Body` and `Knob` × 2: the file names, builder names, linking, naming and the
instance list the lint and the gates expect.  Extents and positions are the plan rows'.

`src/parts/body.py`
```py
import bpy, bmesh

def build_body() -> bpy.types.Object:
    """Body: a mesh at its WORLD pose, named as the plan names it, linked to the scene."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.12, 0.08, 0.05), verts=bm.verts)   # the plan row's extents
    me = bpy.data.meshes.new("Body"); bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new("Body", me)
    obj.location = (0.0, 0.0, 0.025)                                 # lowest vertex on z = 0
    bpy.context.scene.collection.objects.link(obj)
    return obj
```
`src/parts/knob.py`
```py
import bpy, bmesh

def build_knob() -> list[bpy.types.Object]:
    """Knob x2: instances are TOP-LEVEL objects Knob_0, Knob_1 — never under a parent Empty."""
    out = []
    for i, x in enumerate((-0.04, 0.04)):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.01, radius2=0.01, depth=0.02)
        me = bpy.data.meshes.new(f"Knob_{i}"); bm.to_mesh(me); bm.free()
        obj = bpy.data.objects.new(f"Knob_{i}", me); obj.location = (x, 0.0, 0.05 + 0.01)
        bpy.context.scene.collection.objects.link(obj); out.append(obj)   # flush on Body's top: contact
    return out
```
`src/model.py`
```py
import bpy
from parts.body import build_body
from parts.knob import build_knob

def main() -> None:
    build_body()
    build_knob()
    assert all("." not in o.name for o in bpy.data.objects), "auto-suffixed name: two objects share a plan name"

main()
```
