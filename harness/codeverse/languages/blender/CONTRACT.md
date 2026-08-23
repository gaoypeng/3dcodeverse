# Blender (bpy) authoring contract

You write **raw bpy** (Blender 5.x Python API) in a multi-file layout:

* `src/model.py` — the ENTRY: imports the part builders, calls them in plan order, self-checks.
  No geometry of its own beyond small glue.
* `src/parts/<snake>.py` — ONE per plan part (`SeatCushion` → `src/parts/seat_cushion.py`), exporting
  `def build_seat_cushion() -> bpy.types.Object` that builds the part at its WORLD pose with the exact
  object name(s) and returns it (instances `Leg_0..Leg_3` looped inside that one file).  Self-contained;
  it only DEFINES — never builds at import time.  `src/` is on `sys.path`:
  `from parts.seat_cushion import build_seat_cushion`.  Small objects (1–2 parts) may use one `src/model.py`.

The harness runs `blender -b --factory-startup --python run_bpy.py -- --script src/model.py` in an
**EMPTY scene** (no Cube/Camera/Light), reports errors as `src/parts/<file>.py:<line>`, and exports
`artifacts/object.glb` (+ `object.stl`) itself.

## Frame, units, placement
* **Z is up, -Y is the front** of the object, +X is its right. **Units are meters.**
* The object **stands on z = 0** (lowest point at z = 0) and its footprint is **centred on the Z axis**.
* Dimensions come from the plan's bboxes: respect them to ±1 cm; parts that "attach" must touch (no gaps).

## Objects and names
* One mesh object per part, **named exactly as the plan's PascalCase part name** (`SeatCushion`).
  Instances: `Leg_0 … Leg_3`, each a TOP-LEVEL object. **Never parent instances under an
  Empty** — the harness measures top-level GLB nodes as parts, so an Empty parent merges all
  instances into one part and the plan part is reported missing.
* Helper objects (boolean cutters) must be removed or hidden
  (`cutter.hide_set(True); cutter.hide_render = True`) — visible objects are exported as-is.
* Modifiers (bevel, subdivision, boolean, array, solidify, mirror) may stay unapplied:
  the exporter applies them. Keep the total under **500 k triangles** and finish in **< 120 s**.

## Materials
* Every visible mesh gets a material: `mat = bpy.data.materials.new(name)`;
  `bsdf = mat.node_tree.nodes["Principled BSDF"]`; set `inputs["Base Color"]`, `["Roughness"]`, `["Metallic"]`.
  (Blender 4+/5 input names: "Specular IOR Level", "Subsurface Weight", "Transmission Weight",
  "Coat Weight", "Sheen Weight", "Emission Color" / "Emission Strength".)
* The GLB keeps only **flat PBR values + image textures + vertex colours**. Procedural node textures
  (Noise, Voronoi, Wave …) are NOT exported — express detail with geometry and flat PBR per part.

## Forbidden (the harness owns these; the lint rejects them)
* cameras, lights, world/background, render settings, `bpy.ops.render.*`
* `bpy.ops.export_*`, `bpy.ops.import_*`, `bpy.ops.wm.*`, `bpy.data.libraries`, `open()`, os/shutil/subprocess/network
* imports other than `bpy, bmesh, mathutils, math, random (seeded), numpy` (+ stdlib data helpers)

## Headless pitfalls (these cause most build failures)
* `bpy.ops.*` act on the **selection/active object**: grab `obj = bpy.context.object` right after
  `primitive_*_add`; do not rely on `bpy.context.selected_objects`.
* `primitive_cube_add(size=1, scale=(sx,sy,sz))` then
  `bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)` — pass all three flags.
* bmesh: call `bm.verts.ensure_lookup_table()` (and edges/faces) before `bm.verts[i]`; finish with
  `bm.to_mesh(me); bm.free(); me.update()`.
* `modifier_apply` needs the object active + selected in OBJECT mode — or leave modifiers to the exporter.
* `bpy.ops.object.join` inherits the ACTIVE object's transform — prefer separate named parts.
* `use_auto_smooth` is gone: use `bpy.ops.object.shade_smooth_by_angle(angle=0.52)` or `shade_smooth()`.
* Context dict overrides `bpy.ops.x({...})` are gone: `with bpy.context.temp_override(object=obj): ...`.
* World AABB: `[obj.matrix_world @ Vector(c) for c in obj.bound_box]` after `bpy.context.view_layer.update()`.

## Minimal example (copy the pattern; as a part file: wrap the body in `def build_table_top():` and return `top`)
```python
import bpy
import math

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.02), scale=(0.4, 0.3, 0.04))
top = bpy.context.object
top.name = "TableTop"
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
top.data.materials.append(make_material("Oak", (0.55, 0.36, 0.2), roughness=0.6))
bev = top.modifiers.new("Bevel", "BEVEL"); bev.width = 0.005; bev.segments = 3
```
