---
name: 3d-lang-blender
description: "Use when writing, refining or repairing a static_object in Blender bpy (language blender): the files and builder names the lint expects, the Z-up metre frame, naming, the sandbox (allowed imports, forbidden bpy calls), the exact thresholds check-contract / check-connectivity / detail_budget measure, what the GLB exporter keeps, the Blender 5.2.2 API traps, and the 3dcode loop that ships it."
license: Apache-2.0
compatibility: Blender 5.2 LTS headless (--factory-startup); the 3dcode build runs src/model.py in an empty scene and exports artifacts/object.glb.
metadata:
  verified: "2026-11-07"
---

# Blender bpy — static objects

You own the mesh: every part is authored geometry with the exact name the plan gives it.  The
build owns the scene setup, the export and the cameras: never save, render or add lights.

## Files (the lint and the wrapper read exactly these)
* `src/model.py` — the entry: imports each part's builder and calls it ONCE (a builder called
  twice builds `Name.001`).
* `src/parts/<snake>.py` — one per plan part (`SeatCushion` → `seat_cushion.py`) defining
  `build_seat_cushion() -> bpy.types.Object`: builds the part at its WORLD pose with its exact
  name, links it to `bpy.context.scene.collection`, returns it; instances `Leg_0 … Leg_N-1` are
  built in that one file and returned as a list.  A part file only DEFINES (a module-level call
  builds twice: lint WARN); a part file `model.py` never imports is never built (WARN); a builder
  not named `build_<snake>` is a lint ERROR.  `src/` is on `sys.path`
  (`from parts.seat import build_seat`); `src/parts/_<name>.py` is a shared helper.  A single
  `src/model.py` with no `src/parts/` is accepted (the lint then expects the part names there).
* The build runs `blender -b --factory-startup` on an EMPTY scene, takes a census, exports
  `artifacts/object.glb` (Y-up) + `object.stl` with modifiers applied; errors come back as
  `src/parts/<file>.py:<line>`.  `random`, numpy and `mathutils.noise` are seeded before your
  module runs, so draws repeat from build to build.
* `references/interface.md` is the interface, executable.

## Frame, units, placement (`3dcode check-contract`)
* Z up, −Y front, +X right, metres (a 450 mm seat is `0.45`; a measured bbox of 450 × 500 × 900
  means the plan was typed in mm).  The exporter maps to Y-up itself: do not pre-rotate.
* Lowest vertex at z = 0: off by > 1 cm is a WARN, > 3 cm an ERROR.  Footprint centre within
  max(1 cm, 10 % of the larger horizontal extent) of the Z axis (WARN).  Findings print the
  translation that fixes them.  The export is AS AUTHORED — translating the whole assembly is
  fine; `bpy.ops.object.origin_set` changes what is measured.

## Naming and structure (`check-contract`, the census)
* One top-level mesh object per plan part, `obj.name` exactly the plan's PascalCase; a merged
  mesh reports every other part missing, a misspelt name is an ERROR.  Instances
  `<Name>_0 … <Name>_N-1`; `Leg.001` means two objects claimed one name.
* A parent Empty merges its subtree into ONE part; a scaled parent bakes its scale into the
  children.  Collections are organisation only.  `bpy.ops.object.join` inherits the ACTIVE
  object's transform (lint WARN without one).
* Every object made through the data API must be linked to `bpy.context.scene.collection`;
  every visible object exports — delete cutters and guides or `hide_render` /
  `hide_viewport` / `hide_set(True)`.  A curve exports only with `bevel_depth > 0`.

## Sizes and contact (`check-contract`, `3dcode check-connectivity`)
* Each part's built AABB (after modifiers) vs its plan row: per axis `tol = max(1 cm, 10 % of
  the planned extent)`; past `tol` WARN, past 3 × `tol` ERROR, with the per-axis delta.
  Instances are measured on extents (each copy or their union); a wrong copy count is a WARN.
* Contact: surface gap ≤ 2 mm counts as touching; overlap > 2 mm is interpenetration (WARN),
  > 10 mm with ≥ 2 % of one part's surface inside the other an ERROR (score cap 0.70).  Every
  part must reach the ground-touching component through contacts: a floating part is an ERROR
  (cap 0.60) whose finding prints the fix.  Fix a gap by moving or extending the part, never
  with connector sticks.
* Islands: no shell inside a part smaller than 5 % of its largest extent (WARN "tiny
  disconnected island": boolean debris, a stray vertex, an un-merged mirror half).
  Zero-thickness faces have no volume and break booleans; coincident faces z-fight.
* Budgets (`detail_budget`, sized from parts × instances): < 350 triangles per built object WARN
  (floor clamped to 1 500–40 000 total), > 20 000 per built object ERROR (ceiling clamped to
  60 000–600 000); a build over 3 s per built object (≥ 20 s) WARN; killed at 300 s.
  Per-vertex Python loops over large meshes hit the limit — `foreach_set`, modifiers, fewer
  segments.

## Sandbox (the lint)
* Allowed imports: `bpy`, `bmesh`, `mathutils`, `bpy_extras`, `math`, `random`, `numpy`,
  `itertools`, `functools`, `collections`, `typing`, `dataclasses`, `colorsys`, `statistics`,
  `operator`, `enum`, `copy`, `sys`, `os`, `__future__`, your `parts.*`.  Else WARN; `pathlib`,
  `subprocess`, `urllib`, `requests`, `socket`, `http`, `shutil`, `ctypes`, `pickle` ERROR.
* ERROR calls: `bpy.ops.render.*`, `bpy.ops.export_*` / `import_*`, `bpy.ops.wm.*`,
  `bpy.ops.image.save`, `bpy.data.libraries.*`, `open(`, `os.system` / file-IO `os.*`,
  `subprocess.*`, `shutil.*`, network, `exec` / `eval` / `__import__` / `input()`.  WARN:
  cameras, lights, worlds, render settings, `scene.camera`, `scene.world`, `bpy.ops.view3d.*` /
  `bpy.ops.screen.*`, `sys.exit`.

## Materials (what the exporter keeps)
* Flat PBR only — base colour, metallic, roughness, emission, alpha — plus IMAGE textures;
  procedural node trees are NOT exported (the judge sees the flat base colour).  Principled
  inputs on 5.2: `Base Color`, `Roughness`, `Metallic`, `Alpha`, `Emission Color` +
  `Emission Strength`, `Specular IOR Level`, `Transmission Weight`, `Coat Weight`,
  `Sheen Weight`, `Subsurface Weight`; the pre-4.0 keys (`Specular`, `Transmission`,
  `Emission`, `Clearcoat`, `Sheen`, `Subsurface`) raise KeyError (lint ERROR).  `mat.use_nodes`
  is deprecated (every material has a tree).  Transparency: `mat.surface_render_method = 'BLENDED'`.
* Several colours on one part = several slots (`obj.data.materials.append`) +
  `polygon.material_index`; the census warns on unused slots or an index past the last slot.

## API traps (Blender 5.2.2; build errors or silent wrong sizes)
* `bpy.ops.*` needs `bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj])`;
  the dict-override form was removed in 4.0 (ERROR).  `bpy.context.object` / `selected_objects`
  are unreliable headless — keep your own references.
* `bmesh.ops.create_*` centres on the origin and needs no context; `bpy.ops.mesh.primitive_*_add`
  defaults to `size=2` — pass `size=1` with `scale=`, then
  `transform_apply(location=False, rotation=False, scale=True)` with all three keywords.
  `obj.scale` is 1 at export time.
* `bm.verts[i]` / `bm.faces[i]` need `ensure_lookup_table()` after `from_mesh` or any topology
  op (IndexError).  `create_cube` / `create_cone` return only `verts`.
* Modifiers may stay unapplied (the exporter applies them); `modifier_apply` needs
  `temp_override` and a single-user mesh.  Boolean solvers `MANIFOLD` / `EXACT` / `FLOAT`
  (`'FAST'` raises); a cutter coplanar with the face it cuts fails silently.
* Removed (lint ERROR): `use_auto_smooth` / `auto_smooth_angle` (use `polygon.use_smooth` or
  `shade_smooth_by_angle`), `Mesh.calc_normals()`, `mod["Socket_2"] = v`
  (`getattr(mod.properties.inputs, "Socket_2").value = v`).  Removed and NOT linted:
  `calc_normals_split()`, `edge.crease` / `edge.bevel_weight` (`crease_edge` /
  `bevel_weight_edge` attributes), `scene.node_tree`, `bgl`, `gpu.types.GPUShader(...)`.

## The loop
write → `3dcode build` → `3dcode check-contract` + `3dcode check-connectivity` → fix everything
they report → repeat → `3dcode render-sheet` (or `render-views`) → OPEN the sheet and look →
`3dcode check` clean → finish.  `isolate --part X` and `cross-section` answer "is this part
where I think it is" faster than reasoning about transforms.  A clean simpler object beats a
richer one with a visible defect: clear the two gates first, then spend on the brief's detail.
