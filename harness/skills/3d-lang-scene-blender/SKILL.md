---
name: 3d-lang-scene-blender
description: "Use when writing, refining or repairing a scene in Blender bpy (language scene_blender): the module files and their exact functions (env height_at/build_env, zone build, asset build_<snake>), the ctx every builder receives, the Z-up frame and the Y-up plan conversion, allowed imports and the lint's forbidden table, Blender 5.2 API facts, what Cycles does with what you build, motion as keyframes/drivers, the placement and frame gate thresholds, and the 3dcode loop."
license: Apache-2.0
compatibility: Blender 5.2 LTS headless (--factory-startup, an emptied scene, fps 30, frames 1..46); Cycles 32 samples + OpenImageDenoise, AgX at exposure 0, 1024 x 576 — all owned by the 3dcode build.
metadata:
  verified: "2026-11-07"
---

# Blender bpy scenes

Every mesh, material, light, world and motion is bpy / bmesh / mathutils you write.  The build
owns the process, the render settings, the cameras, the frame clock and the export; you build
into the Collection you are handed.

## Files (Z up, −Y front, metres)
```
src/scene.py            written by `3dcode validate-plan` from plan.json — data only (ZONES / ASSETS / HEROES / CAMERAS); never edit it, re-run validate-plan after a plan change
src/env.py              def height_at(x, y) -> z    def build_env(ctx) -> dict     (world, light, ground, atmosphere; an interior's shell)
src/zones/<snake>.py    def build(ctx) -> bpy.types.Collection      (everything linked into ctx.collection, the zone's own)
src/assets/<snake>.py   def build_<snake>(ctx, variant=0) -> bpy.types.Collection   (ONCE at the origin, base on z = 0, front −Y)
public/assets/<snake>.glb   heroes built as objects (a static_object/blender workspace's object.glb) — imported into ctx.assets['<snake>']
```
Code zones share lives in `src/env.py` (`from env import …`); an asset module is
self-contained (built and checked on its own).  The build runs ONE Blender process in this
order, each builder in its own try/except: `env.build_env(ctx)`; every hero imported; every
`assets.<snake>.build_<snake>(ctx)`; every `zones.<snake>.build(ctx)`.  A builder that raises
is DROPPED with its `file:line` and everything it created removed — the rest still builds, so
one bad zone costs that zone.  `build_<zone>(ctx)`, `create()`, `main()`, `register()` are never
called.  Link every object into `ctx.collection` (an object in `ctx.scene.collection` belongs to
no zone).  Then the build adds the plan's cameras, hides the asset sources, writes the census
and the `.blend`, and renders every camera at t = 0 and t = 1.5 s (frames 1 and 46).
`references/interface.md` has an env and a zone module as-is.

## The `ctx` every builder receives (data, never verbs)
| field | what it is |
| --- | --- |
| `ctx.scene` | the bpy Scene (read it; its render settings are the build's) |
| `ctx.collection` | the Collection THIS builder links into — a zone's own (active, so `bpy.ops` primitives land in it), an asset's own, the scene root for `build_env` |
| `ctx.height_at(x, y)` | the ground: `env.height_at` once the env built, else flat 0 |
| `ctx.env` | the dict `build_env` returned (shared materials, the water level, the sun direction…); `{}` before |
| `ctx.assets` | `{snake: Collection}` — every procedural asset built and every hero imported, at the origin, base on z = 0 |
| `ctx.rng(seed)` | `random.Random(seed)` — THE randomness (`random.*` and unseeded numpy are lint ERRORS) |
| `ctx.fps`, `ctx.times` | 30 and (0.0, 1.5) |
| `ctx.frame(t)` | `1 + round(t * fps)`: keyframe on `ctx.frame(0.0)` and `ctx.frame(1.5)` |

## Frame
Z up, −Y front, +X right, metres, the ground `height_at(x, y)` around z = 0.  The PLAN is
written in the Y-up GLB frame: every number the build prints for you (bounds, zone bboxes,
camera positions) is already converted; the plan's prose is not — "y = 1.2 m up" is z = 1.2,
"+z towards the camera" is −y.  Sizes here are width × depth × height (the plan's width ×
height × depth reordered).

## Allowed imports
`bpy`, `bmesh`, `mathutils` (Vector, Matrix, Euler, noise), `math`, `numpy` (randomness only
through `numpy.random.default_rng(seed)`), `itertools`, `functools`, `collections`,
`dataclasses`, `colorsys`, and `from env import …` (module-level names of `env.py`).

## Forbidden (lint ERRORs that fail the build gate)
`bpy.ops.render.*`, `bpy.ops.wm.*`, `bpy.ops.export_*`, `bpy.ops.import_*`,
`bpy.ops.image.save`, `bpy.data.libraries`, `bpy.data.cameras.new` / `bpy.ops.object.camera_add`,
any `scene.render.*` / `scene.cycles.*` / `scene.eevee.*` / `scene.view_settings.*` /
`scene.display_settings.*` / `scene.camera` / `scene.frame_*` assignment, `frame_set`,
`bpy.app.handlers`, `bpy.app.timers`, a driver whose expression needs Python (`use_self`,
attribute access, `**`, `%`, `noise.*`), the world outside `src/env.py`, `random.*` and
unseeded numpy, `open()`, `os` / `sys` / `pathlib` / `time` / `datetime` / `subprocess` /
`shutil` / `tempfile` / `glob` / `io`.  WARN: `bpy.ops.mesh.primitive_*` inside a loop (each
call rebuilds the scene state), `bpy.ops.view3d.*` / `bpy.ops.screen.*`.

## Blender 5.2 API facts (a build error or a silent zero)
Removed: `use_nodes` (materials, worlds and lights always have a tree; a new material carries
`Principled BSDF` and `Material Output`), `sky_type = 'NISHITA'` (`'MULTIPLE_SCATTERING'`, with
`aerosol_density`), boolean `'FAST'` (`FLOAT` / `EXACT` / `MANIFOLD`), `mod["Socket_2"] = v`
(`getattr(mod.properties.inputs, "Socket_2").value = v`), `Action.fcurves` (slotted:
`action.layers[0].strips[0].channelbag(action.slots[0]).fcurves`), `Mesh.calc_normals()` and
`use_auto_smooth` (`me.shade_smooth()` or `f.smooth = True`), the Principled inputs `Specular` /
`Subsurface` / `Transmission` / `Emission` (now `Specular IOR Level`, `Subsurface Weight`,
`Transmission Weight`, `Coat Weight`, `Sheen Weight`, `Emission Color` + `Emission Strength`),
`ShaderNodeMixRGB` (`ShaderNodeMix` with `data_type = 'RGBA'`: inputs 6 / 7 the colours, output
2 the result; the mix input is `Factor`).  Background mode: `bpy.context.object` /
`selected_objects` are empty after `bpy.data.objects.new` — keep your own references.
`bm.verts[i]` raises before `ensure_lookup_table()`.  A fresh object's `matrix_world` is
identity until the view layer updates (compose `parent.matrix_world @ child.matrix_basis` or
`bpy.context.view_layer.update()`).  Geometry nodes: a new tree has no sockets or nodes —
`ng.interface.new_socket(name, in_out='INPUT' | 'OUTPUT', socket_type='NodeSocketGeometry')`,
add the `NodeGroupInput` / `NodeGroupOutput` nodes yourself,
`obj.modifiers.new(name, 'NODES').node_group = ng`.

## What the renderer does with what you build
Cycles at 32 samples + OIDN, AgX, exposure 0, 1024 × 576; no post chain, no bloom: an emissive
at strength 3–8 reads as a lamp, 20+ whites the frame (`blown_frac`), and a lamp beside it is
what lights the wall.  Light is physical: a Sun in W/m², lamps in Watts; starting points that
passed on a mid-albedo ground — a day Sun of 2–5 W/m² under a sky world at strength 0.2–0.5, a
dusk or night lit by practicals (an emissive skin plus a Point light with a radius), a moon
0.05–0.3, fog density 0.003–0.008.  Hue, material and geometry work are invisible on near-black
pixels.  A SURFACE shader always has a silhouette: a soft particle (steam, smoke, dust, spray)
renders edgeless only as a VOLUME — a material whose only linked output is the Material
Output's `Volume`, fed by a `ShaderNodeVolumePrincipled`; a translucent puff, metaball, torus
or disc is `hard_edged_particles`.  The census is the evaluated depsgraph (modifiers, geometry
nodes and instances count as rendered; a volume-only mesh is fog, never a placed thing).
Triangle budgets: 15 000 per asset, 40 000 per hero, 3 000 000 unique for the scene.

## Motion
Animation is DATA: `obj.keyframe_insert(data_path, frame=ctx.frame(t))` at the two times (a
location, rotation, shape key, node input), or a SIMPLE driver of `frame`
(`fc = obj.driver_add("rotation_euler", 2)`, `fc.driver.expression = "frame * 0.1"` — sin / cos
/ min / max / clamp / lerp, `+ - * /`, comparisons; anything needing Python evaluates to 0 in
a `--factory-startup` build).  Set each `keyframe_points[].interpolation = 'LINEAR'` for a
travel.  The build differences the two frames per camera: a camera moves when ≥ 0.4 % of its
pixels change by more than 8/255; none moving is `no_motion` (ERROR), the judge's
`nothing_moves`.  Amplitudes that registered: ≥ 0.10 rad, ≥ 0.05 m per 1.5 s, a particle /
leaf / boat ≥ 0.6 m.  A hero GLB's own clip plays in every instance by itself.

## Naming
PascalCase, globally unique (a second `Lantern_0` becomes `Lantern_0.001` and reports a prop
nobody planned): number every copy `f"{base}_{i}"`, never reuse a base across zones.  The
census classifies by name: `Ground…` / `Terrain…`, `Water…`, distant backdrop `Sky…` (a
sky-named mesh wider than 50 m is backdrop, not content), an interior's shell `RoomShell…` /
`Ceiling…` (the overview rig lifts the lid).

## Placement (`3dcode check-placement`, on every build)
Every root object linked into a zone (a mesh, a parent Empty with children, a collection-
instance Empty) is ONE row.  From its lowest evaluated vertices the gate looks down for the
nearest surface and up for one through it: a gap > 0.05 m outdoors / 0.02 m indoors is
`floating` (ERROR at 0.08 m, or more than 2 floaters); buried > 0.10 m is `sunken` (ERROR at
0.20 m and a fifth of the height; rocks, posts and bushes may sit partly in; a foot at or under
water is fine); touching nothing `unsupported`; two boxes sharing > 10 % of the smaller
`interpenetration` (ERROR at 35 %).  The feet are the foot band — the evaluated vertices within
max(0.02 m, 5 % of its height) of the lowest, sampled as up to 5 columns.  Seat a root by moving
it by the smallest `ctx.height_at(x, y) - z` over that band's world-space vertices (children and
the instanced collection through the Empty's matrix included), or onto its support's top; keep
any two roots' world boxes from sharing > 10 %.  Four or more foot columns with at most one
touching and a gap > 0.05 m is `cantilevered` (WARN).  `obj["placement"] = "free"` only for
things MEANT to hang (WARN past 25 % tagged, ERROR past 50 %).  Geometry-nodes instance clouds
are exempt per asset.  A planned content entirely outside the bounds + max(5 m, 10 % of the
span) is `out_of_bounds` (WARN); an asset placed beyond 2.5 × the plan's size is a WARN, 4 × an
ERROR — never re-scale an instance to fit; a chimney or pipe that must reach further is a
separate object.  Place an asset as a collection-instance Empty (`bpy.data.objects.new(name,
None)`, `instance_type = 'COLLECTION'`, `instance_collection = ctx.assets['<snake>']`).

## What else the gates measure
`scene_frames` (`3dcode scene-views`), every authored camera at both times: `dark_frame` (mean
luminance < 0.12 or > 35 % near-black, cap 0.55), `blown_frame` (> 20 % near-white),
`flat_frame` (> 85 % of pixels in one band; ERROR above 92 %), `content_small` (establishing
camera ≥ 20 % content, others ≥ 10 %), `camera_in_geometry` (inside or within 0.5 m of a
surface), `camera_blocked` (6 of 9 sight rays within 1.5 m), `camera_underground` — the camera
findings cap at 0.50; eye < 0.3 m above the ground `camera_low`, > 80 m `camera_high` (WARN).
`hero_unseen`: a hero covering < 0.5 % of the frame in every camera is an ERROR (< 2 % in its
named camera a WARN).  `scene_probe` (`3dcode scene-probe`): unique triangles over 3 000 000, a
Python driver or app handler, no world, no atmosphere (`no_fog`: neither a world Volume nor a
bounded fog volume), render settings touched.  Outdoors `fog_short` warns when 1 / density <
span / 1.5 and `world_edge` when no standing geometry reaches 1.25 × the bounds half-extent.  A
dropped zone / asset / env is an ERROR on its line.

## Interiors
`interior: true`: the enclosure (floor, walls, lid named `RoomShell…` / `Ceiling…`) is
`src/env.py`'s; it must read enclosed from every camera and be lit inside; the floating
threshold indoors is 0.02 m.  Zones dress the inside; a partition or counter a zone needs is the
zone's, seated and clear of the shell.

## Realism (judged)
Every large surface varies at more than one scale — broad tone and wear over metres AND fine
grain or bump over centimetres — in a node tree you wire (Noise / Voronoi / Wave through Mix and
Bump); one constant colour per surface is `placeholder_material` / `flat_ground`.  A tree is a
trunk branching into limbs carrying leaf or needle cards, a rock an irregular asymmetric mass —
never a cone, a ball on a stick or a cube (`asset_quality_detail`); one shape stamped many times
is `monotonous_vegetation`.  The world continues past the content in every frame
(`world_edge_visible`).  The brief's time of day is a hard constraint, decided once in
`src/env.py` and shared through the dict `build_env` returns.

## Asset modules
`build_<snake>(ctx, variant=0)` links every part into `ctx.collection` (pre-made, active) and
returns it: at the origin, lowest point z = 0, footprint centred, front −Y, sized as the plan
states (ERROR beyond 2.5 × or under 0.4 × on any axis); zones instance it at scale 1 and never
call it.  Imports `bpy`, `bmesh`, `mathutils`, `math`; no lights, cameras, world, file IO,
ground plane.  Every part has a Principled material with a real colour and roughness, varied
at more than one scale; unique PascalCase names, one root mesh or children parented at
RELATIVE locations; ≤ 15 000 triangles.  `variant` changes the SILHOUETTE when several copies
are placed; `ctx.rng(seed)` varies details.  A moving part (when the description names one) is
keyframed on its OWN rotation/location with its origin at the pivot (≥ 0.15 rad or ≥ 0.05 m
registers).  Every sub-shape overlaps a neighbour by ≥ 2 mm; the lowest point is exactly z = 0.

## The loop
write → `3dcode build` → `3dcode check-placement` → seat what floats, lift what sank, separate
what overlaps → `3dcode build` → `3dcode scene-views` → read `camera_checks.mean_lum` per
camera and the MEASURED MOTION line, OPEN the sheet → fix the lighting → `3dcode scene-probe`
for the census (triangles, drivers, the world) → repeat → `3dcode check` clean.  Get the floor
and the light right; spend everything after that on the brief.
