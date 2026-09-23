# Blender (bpy) authoring contract — track `static_object`, language `blender`

## Files (multi-file: one file per plan part, so parts can be refined in parallel)
* `src/model.py` — the ENTRY: imports the part builders, calls them in plan order, runs the
  self-check.  No geometry of its own beyond small glue.
* `src/parts/<snake>.py` — ONE per plan part (`SeatCushion` → `src/parts/seat_cushion.py`),
  exporting `def build_seat_cushion() -> bpy.types.Object` that builds the part at its WORLD
  pose with the exact object name and returns it.  Instances (`Leg_0 … Leg_3`) are looped
  INSIDE that one file, each TOP-LEVEL — NEVER under a parent Empty (parts are measured as
  top-level objects; an Empty merges them into one → part missing).  Self-contained (own small
  helpers); it only DEFINES — never builds at import time (model.py calls each builder once).
* `src/` is on `sys.path`: `from parts.seat import build_seat` or `import parts.seat`.  Optional
  shared helpers: `src/parts/_common.py` (underscore = not a part).  Small objects (1–2 parts)
  may use a single `src/model.py` (example B).
* The harness runs `src/model.py` with `blender -b --factory-startup` in an **emptied scene**,
  takes a census and exports `artifacts/object.glb` (Y-up) + `object.stl`; build errors come
  back as `src/parts/<file>.py:<line>`.  You never export or render.

## Frame, units, placement
* **Z is up, -Y is the front, +X is the right.  Meters.**  (glTF export maps this to
  Y-up / +Z-front automatically — do not pre-rotate.)  The object stands on z = 0 (lowest
  vertex at z = 0 ± 1 mm), footprint centred on the Z axis; real-world dimensions.

## Naming and structure
* One Blender **object per plan part**, `obj.name = "<PartName>"` in PascalCase exactly as the
  plan says.  Auto-suffixed duplicates (`Leg.001`) violate it: name instances `<Name>_0 … <Name>_N-1`.
* Meshes only (no empties as parts, no un-converted curves, no cameras/lights); link every
  object to `bpy.context.scene.collection`.  Plan numbers = constants at the top of each part
  file; keep each part inside its plan bbox; attached parts overlap 0.5–2 mm (deeper = interpenetration).

## Allowed imports · forbidden calls
* Imports: `bpy`, `bmesh`, `mathutils`, `math`, `random`, `itertools`, `functools`, `typing`,
  `dataclasses`, and your own `parts.*` modules.  Nothing else (no numpy loops over vertices,
  no os/sys/subprocess/urllib/socket/pathlib, no `import codeverse3d`).
* Forbidden: `bpy.ops.render.*`, `bpy.ops.export_*`, `bpy.ops.import_*`, `bpy.ops.wm.*`,
  `bpy.ops.image.*`, `bpy.ops.screen.*`, `bpy.ops.view3d.*`, `bpy.data.libraries`,
  `bpy.app.timers`, `open(`, `exec(`, `eval(`, `__import__`, camera / light creation, `input()`,
  network, file writes; never touch `bpy.context.scene.render` / `scene.world` (harness-owned).
* Prefer modifiers (bevel / array / mirror) over dense meshes.

## COMPLETE minimal example A — multi-file (verified with Blender 5.0 headless)
`src/parts/seat.py`
```py
import bpy, bmesh

SEAT_D, SEAT_T, SEAT_Z = 0.34, 0.04, 0.45          # metres, from the plan (bbox top at 0.45)

def build_seat() -> bpy.types.Object:
    """Seat — round wooden disc; top face at z = SEAT_Z.  Returns the object at world pose."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=SEAT_D / 2, radius2=SEAT_D / 2, depth=SEAT_T)
    me = bpy.data.meshes.new("Seat"); bm.to_mesh(me); bm.free()
    seat = bpy.data.objects.new("Seat", me); seat.location = (0, 0, SEAT_Z - SEAT_T / 2)
    bpy.context.scene.collection.objects.link(seat)
    bev = seat.modifiers.new("Bevel", 'BEVEL'); bev.width = 0.006; bev.segments = 3
    wood = bpy.data.materials.new("Wood")
    bsdf = wood.node_tree.nodes["Principled BSDF"]          # nodes exist by default (4.x/5.x)
    bsdf.inputs["Base Color"].default_value, bsdf.inputs["Roughness"].default_value = (0.55, 0.33, 0.16, 1.0), 0.6
    seat.data.materials.append(wood)
    return seat
```
`src/parts/leg.py`
```py
import bpy, bmesh, math

LEG_R, LEG_N, LEG_RING = 0.02, 3, 0.12             # metres, from the plan
LEG_H = 0.45 - 0.04 + 0.001                        # reaches 1 mm into the seat (weld)

def build_leg() -> list[bpy.types.Object]:
    """Leg x3 — steel rods Leg_0..Leg_2 on a ring, each TOP-LEVEL (no parent Empty!)."""
    steel = bpy.data.materials.new("Steel"); bsdf = steel.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.62, 1.0)
    bsdf.inputs["Roughness"].default_value, bsdf.inputs["Metallic"].default_value = 0.35, 1.0
    legs = []
    for i in range(LEG_N):
        a = 2 * math.pi * i / LEG_N
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=LEG_R, radius2=LEG_R, depth=LEG_H)
        me = bpy.data.meshes.new(f"Leg_{i}"); bm.to_mesh(me); bm.free()
        leg = bpy.data.objects.new(f"Leg_{i}", me)
        leg.location = (LEG_RING * math.cos(a), LEG_RING * math.sin(a), LEG_H / 2)
        bpy.context.scene.collection.objects.link(leg)
        leg.data.materials.append(steel); legs.append(leg)
    return legs
```
`src/model.py`
```py
"""Stool — entry: imports the part builders, calls them in plan order, self-checks."""
import bpy
from parts.seat import build_seat
from parts.leg import build_leg

def _selfcheck() -> None:
    names = [o.name for o in bpy.data.objects if o.type == 'MESH']
    assert names and all('.' not in n for n in names), f"missing or auto-suffixed meshes: {names}"

def main() -> None:
    build_seat()
    build_leg()
    _selfcheck()

main()
```

## COMPLETE minimal example B — single file (small objects; verified with Blender 5.0)
```python
import bpy, bmesh, math

SEAT_D, SEAT_T, SEAT_Z = 0.34, 0.04, 0.45      # metres, from the plan
LEG_R, LEG_N, LEG_RING = 0.02, 3, 0.12

def make_cylinder(name, radius, depth, location, segments=32):
    """Cylinder along Z centred at `location` via bmesh (no operator/context needed)."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius, depth=depth)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me); obj.location = location
    bpy.context.scene.collection.objects.link(obj); return obj

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name); bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value, bsdf.inputs["Metallic"].default_value = roughness, metallic
    return mat

def build_seat() -> bpy.types.Object:
    seat = make_cylinder("Seat", SEAT_D / 2, SEAT_T, (0, 0, SEAT_Z - SEAT_T / 2), 48)
    bev = seat.modifiers.new("Bevel", 'BEVEL'); bev.width = 0.006; bev.segments = 3
    seat.data.materials.append(make_material("Wood", (0.55, 0.33, 0.16), 0.6))
    return seat

def build_leg(i: int) -> bpy.types.Object:
    a, h = 2 * math.pi * i / LEG_N, SEAT_Z - SEAT_T + 0.001          # 1 mm into the seat (weld)
    leg = make_cylinder(f"Leg{i + 1}", LEG_R, h, (LEG_RING * math.cos(a), LEG_RING * math.sin(a), h / 2), 24)
    leg.data.materials.append(make_material(f"Steel{i + 1}", (0.6, 0.6, 0.62), 0.35, 1.0))
    return leg

def _selfcheck() -> None:
    names = [o.name for o in bpy.data.objects if o.type == 'MESH']
    assert names and all('.' not in n for n in names), f"missing or auto-suffixed meshes: {names}"

def main() -> None:
    build_seat()
    for i in range(LEG_N):
        build_leg(i)
    _selfcheck()

main()
```
