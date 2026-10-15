# Blender (bpy) authoring contract — track `static_object`, language `blender`

What must be true, where, and how the harness measures it.  Nothing here is a construction to
copy: the gate constants are named so the numbers can be checked, not so they can be typed in.

## Files (what the lint `lint:blender` and the wrapper read)
* `src/model.py` — the ENTRY the wrapper runs: it imports each part's builder and calls it (a
  builder called twice builds its part twice, `Name.001`).
* `src/parts/<snake>.py` — one per plan part (`SeatCushion` → `src/parts/seat_cushion.py`),
  defining `build_seat_cushion() -> bpy.types.Object` that builds the part at its WORLD pose with
  its exact name, links it to `bpy.context.scene.collection` and returns it.  Instances
  (`Leg_0 … Leg_N-1`) are built inside that one file and returned as a list.  A part file only
  DEFINES — a module-level `build_*()` or `main()` call builds the part twice (`Name.001`, lint
  WARN); a part file `model.py` never imports is never built (lint WARN); a builder not named
  `build_<snake>` is a lint ERROR and an ImportError.  `src/` is on `sys.path`
  (`from parts.seat import build_seat`); `src/parts/_<name>.py` is a shared helper, not a part.
* A single `src/model.py` with no `src/parts/` is also accepted (the lint then expects the part
  names in that file).
* The harness runs `src/model.py` with `blender -b --factory-startup` in an EMPTY scene, takes a
  census and exports `artifacts/object.glb` (Y-up) + `object.stl`, applying modifiers on export.
  Build errors come back as `src/parts/<file>.py:<line>`.  The wrapper seeds `random`, numpy
  and `mathutils.noise` (`--seed`, default 0) before your module runs, so unseeded draws repeat
  from build to build; a `random.Random(n)` of your own is reproducible too.

## Frame, units, placement (`check_contract`)
* Frame and units are `conventions.py`'s: Z up, −Y front, +X right, metres (a 450 mm seat is
  `0.45`; a measured bbox of 450 × 500 × 900 means the plan's numbers were typed in mm).  The
  glTF export maps this to Y-up itself — do not pre-rotate.
* Lowest vertex at z = 0: off by more than 1 cm (`GROUND_TOL_M`) is a WARN, more than 3 × that an
  ERROR.  Footprint centre within max(1 cm, 10 % of the larger horizontal extent) of the Z axis
  (WARN).  Both findings print the translation that fixes them.
* The harness exports AS AUTHORED and never re-centres or grounds: translating the whole assembly
  so its lowest vertex is at z = 0 is fine; moving object origins (`bpy.ops.object.origin_set`)
  changes what the gate measures.

## Naming and structure (`check_contract`, the census)
* One top-level mesh object per plan part, `obj.name` in PascalCase exactly as the plan spells it;
  the gate matches on names, so a merged mesh reports every other part missing and a misspelt
  name is an ERROR.  Instances are `<Name>_0 … <Name>_N-1`; an auto-suffixed `Leg.001` means two
  objects claimed one name.
* Parts are measured as top-level objects: a parent Empty merges its whole subtree into ONE part
  (plan part reported missing), and a scaled parent bakes its scale into the children.
  Collections are organisation only (not exported as nodes).  `bpy.ops.object.join` inherits the
  ACTIVE object's transform (lint WARN when no active object is set).
* Every object made through the data API must be linked to `bpy.context.scene.collection` or it
  is not in the census.  Every visible object exports: a leftover cutter or guide ships as a part
  — delete it, or set `hide_render` / `hide_viewport` / `hide_set(True)` (any one keeps it out of
  the GLB; an unapplied boolean still evaluates).
* Measured on Blender 5.2.2 with the wrapper's export call: a curve object exports only when
  `bevel_depth > 0` — a bare path exports no node at all and the part is reported missing.
  Convert to a mesh when a later step needs real geometry.

## Sizes and contact (`check_contract`, `check_connectivity`)
* Each part's built AABB (after modifiers) matches its plan row: per axis
  `tol = max(1 cm, 10 % of the planned extent)`; past `tol` is a WARN, past 3 × `tol` an ERROR,
  and the finding prints the per-axis delta.  Instanced parts are measured on extents only — each
  copy or the union of copies, whichever fits the plan box better; a wrong copy count is a WARN.
* Parts that touch: surface gap ≤ 2 mm (`CONTACT_GAP_M`) counts as contact; overlap deeper than
  2 mm (`PENETRATION_WARN_M`) is reported as interpenetration, deeper than 10 mm with at least 2 %
  of one part's surface samples inside the other is an ERROR.
  Every part must be in the ground-touching component: a floating part is an ERROR whose finding
  prints the translation (or the extension) that closes the gap.
* Islands: no shell inside a part may be smaller than 5 % of that part's largest extent
  (`TINY_ISLAND_FRACTION`, WARN "tiny disconnected island") — boolean debris, a stray vertex, an
  un-merged mirror half.  Zero-thickness faces have no volume for the gate's inside test and break
  booleans; coincident faces of two parts z-fight.
* Budgets (`detail_budget`, sized from the plan's part × instance count): fewer than 350
  triangles per built object is a WARN (floor clamped to 1 500 – 40 000), more than 20 000 per
  built object an ERROR (ceiling clamped to 60 000 – 600 000); a build over 3 s per built object
  (at least 20 s) is a WARN, and the build is killed at `build_timeout_s` (300 s by default).
  Per-vertex Python loops over large meshes are what hits the time limit — `foreach_set`,
  modifiers or fewer segments are the usual way out.

## Sandbox (what the lint rejects)
* Imports the lint allows: `bpy`, `bmesh`, `mathutils`, `bpy_extras`, `math`, `random`, `numpy`,
  `itertools`, `functools`, `collections`, `typing`, `dataclasses`, `colorsys`, `statistics`,
  `operator`, `enum`, `copy`, `sys`, `os`, `__future__` and your own `parts.*` modules
  (`ALLOWED_IMPORTS`).  Anything else is a WARN; `pathlib`, `subprocess`, `urllib`, `requests`,
  `socket`, `http`, `shutil`, `ctypes`, `pickle` are ERRORs (`FORBIDDEN_IMPORTS`).
* Forbidden calls (ERROR): `bpy.ops.render.*`, `bpy.ops.export_*`, `bpy.ops.import_*`,
  `bpy.ops.wm.*`, `bpy.ops.image.save`, `bpy.data.libraries.*`, `open(`, `os.system` / file-IO
  `os.*`, `subprocess.*`, `shutil.*`, network.  WARN: cameras, lights, worlds, render settings,
  `scene.camera`, `scene.world` (harness-owned), `bpy.ops.view3d.*` / `bpy.ops.screen.*` (no
  viewport headless), `sys.exit`.  No `exec` / `eval` / `__import__` / `input()` either: the
  script runs unattended inside the harness's Blender process.

## Materials (what the exporter keeps, what the census measures)
* The GLB keeps flat PBR — base colour, metallic, roughness, emission, alpha — plus IMAGE textures
  only; procedural node trees (noise, wave, ramps) are NOT exported, so the judge sees the flat
  base colour.  Every new material already has a node tree (`mat.use_nodes` is deprecated, lint
  INFO); the Principled BSDF inputs are `Base Color`, `Roughness`, `Metallic`, `Alpha`,
  `Emission Color` + `Emission Strength`, `Specular IOR Level`, `Transmission Weight`,
  `Coat Weight`, `Sheen Weight`, `Subsurface Weight` — the pre-4.0 keys `Specular`,
  `Transmission`, `Emission`, `Clearcoat`, `Sheen`, `Subsurface` raise KeyError (lint ERROR,
  `REMOVED_BSDF_INPUTS`).  Transparency: `mat.surface_render_method = 'BLENDED'` (the EEVEE
  setting the exporter reads; `mat.blend_method` still exists on 5.2.2 but is legacy).
* Several colours on one part = several slots on the mesh (`obj.data.materials.append`) and
  `polygon.material_index` per face; every polygon starts on slot 0.  The census warns when a
  mesh carries slots no polygon uses or an index past the last slot — that warning means the
  assignment did nothing and the part exports in one colour.

## API traps (each is a build error on Blender 5.2.2 or a silent wrong size; the lint's severity is given where it checks one)
* `bpy.ops.*` needs context: wrap it in `bpy.context.temp_override(object=obj, active_object=obj,
  selected_objects=[obj])`; the dict-override form `bpy.ops.x({...})` was removed in 4.0 (ERROR).
  `bpy.context.object` / `selected_objects` are unreliable headless — keep your own references.
* `bmesh.ops.create_*` needs no context and centres the shape on the origin;
  `bpy.ops.mesh.primitive_*_add` defaults to `size=2` — pass `size=1` with `scale=` (else extents
  double, WARN) and then `transform_apply(location=False, rotation=False, scale=True)` with all
  three keywords (an omitted one defaults to True and bakes the location, WARN).  Keep
  `obj.scale` at 1 by export time.
* `bm.verts[i]` / `bm.faces[i]` need `ensure_lookup_table()` after `from_mesh` or any topology op
  (ERROR, IndexError).  `bmesh.ops.create_cube` / `create_cone` return only `verts`.
* Modifiers may stay unapplied (the exporter applies them).  `modifier_apply` needs
  `temp_override` (WARN) and a single-user mesh (`obj.data = obj.data.copy()` first).  Boolean
  solvers are `MANIFOLD` (closed inputs), `EXACT`, `FLOAT` — `'FAST'` raises (ERROR); a cutter
  coplanar with the face it cuts fails silently.
* Removed in 4.x/5.x, lint ERROR: `use_auto_smooth` / `auto_smooth_angle` (use
  `polygon.use_smooth` or `bpy.ops.object.shade_smooth_by_angle`), `Mesh.calc_normals()`,
  geometry-nodes inputs by item access (`mod["Socket_2"] = v` →
  `getattr(mod.properties.inputs, "Socket_2").value = v`).  `BLENDER_EEVEE_NEXT` is a lint WARN
  (render settings are the harness's).
* Removed on 5.2.2 and NOT linted (the build fails with AttributeError / ImportError / TypeError):
  `Mesh.calc_normals_split()`, `edge.crease` / `edge.bevel_weight` (attributes `crease_edge` /
  `bevel_weight_edge`), `scene.node_tree`, the `bgl` module, constructing
  `gpu.types.GPUShader(...)`.

## The interface, executable (verified with Blender 5.2.2 LTS headless)
Two plan parts, `Body` and `Knob` × 2 — the file names, builder names, linking, naming and the
instance list the lint and the gates expect.  Extents and positions are the plan row's numbers.

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
