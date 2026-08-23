# Blender (bpy) authoring contract — track `static_object`, language `blender`

## Files
* `src/model.py` — ONE pure-bpy script.  The harness runs it with
  `blender -b --factory-startup --python wrappers/run_bpy.py -- --script src/model.py`
  inside an **emptied scene**, then applies transforms, takes a census, exports
  `artifacts/object.glb` (Y-up) and `object.stl`.  You never export or render.

## Frame, units, placement
* **Z is up, -Y is the front, +X is the right.  Meters.**  (glTF export maps this to
  Y-up / +Z-front automatically — do not pre-rotate.)
* The object stands on z = 0 (lowest vertex at z = 0 ± 1 mm), footprint centred on the
  Z axis.  Real-world dimensions (a chair seat at z ≈ 0.45, a table top at z ≈ 0.75).

## Naming and structure
* One Blender **object per plan part**, `obj.name = "<PartName>"` in PascalCase exactly
  as the plan says (`SeatCushion`, `LeftFrontLeg`).  Blender auto-suffixes duplicates
  (`Leg.001`) — that is a contract violation: give every instance its own unique plan name
  (`LegFrontLeft`, `LegFrontRight`, …) or the plan's `<Name>1..N`.
* Meshes only (no empties as parts, no curves left un-converted, no cameras/lights).
  Link every object to `bpy.context.scene.collection` (or a child collection).
* Put part-local helpers in functions: `build_<snake>() -> bpy.types.Object`; call them
  from a `main()` at the bottom; `main()` is executed at import time (module level call).

## Allowed imports
`bpy`, `bmesh`, `mathutils`, `math`, `random`, `itertools`, `functools`, `typing`,
`dataclasses`.  Nothing else (no numpy loops over vertices, no os/sys/subprocess/
urllib/socket/pathlib, no `import codeverse`).

## Forbidden calls
`bpy.ops.render.*`, `bpy.ops.export_*`, `bpy.ops.import_*`, `bpy.ops.wm.*`,
`bpy.ops.image.*`, `bpy.ops.screen.*`, `bpy.ops.view3d.*`, `bpy.data.libraries`,
`bpy.app.timers`, `open(`, `exec(`, `eval(`, `__import__`, any camera / light creation,
any `input()` / network / file write.  Do not touch `bpy.context.scene.render` or
`scene.world` — the harness owns render settings.

## Limits
≤ 600 k triangles total (aim 20–150 k); subdivision levels ≤ 2; no boolean chains > 12
cutters per object; script finishes in < 120 s headless.

## Self-check (do this at the end of `main()`)
```python
import bpy
def _selfcheck() -> None:
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    assert meshes, "no mesh objects built"
    for o in meshes:
        assert '.' not in o.name, f"auto-suffixed name {o.name!r}: rename the instance"
```

## COMPLETE minimal example (verified with Blender 5.0 headless)
```python
import bpy, bmesh, math

SEAT_D, SEAT_T, SEAT_Z = 0.34, 0.04, 0.45      # metres, from the plan
LEG_R, LEG_N, LEG_RING = 0.02, 3, 0.12

def _link(obj: bpy.types.Object) -> bpy.types.Object:
    bpy.context.scene.collection.objects.link(obj)
    return obj

def make_cylinder(name: str, radius: float, depth: float, location, segments: int = 32):
    """Cylinder along Z centred at `location` via bmesh (no operator/context needed)."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments,
                          radius1=radius, radius2=radius, depth=depth)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    return _link(obj)

def make_material(name: str, rgb, roughness=0.5, metallic=0.0) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]          # nodes exist by default (4.x/5.x)
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_seat() -> bpy.types.Object:
    seat = make_cylinder("Seat", SEAT_D / 2, SEAT_T, (0, 0, SEAT_Z - SEAT_T / 2), 48)
    bev = seat.modifiers.new("Bevel", 'BEVEL'); bev.width = 0.006; bev.segments = 3
    seat.data.materials.append(make_material("Wood", (0.55, 0.33, 0.16), 0.6))
    return seat

def build_leg(i: int) -> bpy.types.Object:
    a = 2 * math.pi * i / LEG_N
    x, y = LEG_RING * math.cos(a), LEG_RING * math.sin(a)
    h = SEAT_Z - SEAT_T + 0.004                       # overlaps the seat by 4 mm (weld)
    leg = make_cylinder(f"Leg{i + 1}", LEG_R, h, (x, y, h / 2), 24)
    leg.data.materials.append(make_material(f"Steel{i + 1}", (0.6, 0.6, 0.62), 0.35, 1.0))
    return leg

def main() -> None:
    build_seat()
    for i in range(LEG_N):
        build_leg(i)

main()
```
