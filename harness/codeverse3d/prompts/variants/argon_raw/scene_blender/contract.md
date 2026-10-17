# Scene (multi-file Blender bpy) authoring contract — track `scene`, language `scene_blender`

## Files (Blender 5.2 LTS python; Z up, −Y front, metres)
```
src/scene.py            ASSEMBLED BY THE HARNESS — data only (ZONES / ASSETS / HEROES / CAMERAS); never edit it
src/env.py              def height_at(x, y) -> z    def build_env(ctx) -> dict     (world, light, ground, atmosphere; the enclosure of an interior)
src/zones/<snake>.py    def build(ctx) -> bpy.types.Collection      (everything linked into ctx.collection, the zone's own Collection)
src/assets/<snake>.py   def build_<snake>(ctx, variant=0) -> bpy.types.Collection   (built ONCE at the origin, base on z = 0, front facing −Y)
public/assets/<snake>.glb   heroes the harness built as objects — imported for you into ctx.assets['<snake>']
```
No harness library ships in this workspace: every mesh, material, light, world and motion is the
raw bpy / bmesh / mathutils you write (an import of `lib` is a lint ERROR: there is no such
package).  Code several zones share lives in `src/env.py` (`from env import …`); an asset module
is self-contained — it is built and checked on its own.

The harness builds the scene in ONE Blender 5.2 process (`--factory-startup`, an emptied scene,
metric units, fps=30, the frame range 1..46, every random source seeded) in this order, each
builder in its own try/except: `env.build_env(ctx)`; every hero GLB imported; every
`assets.<snake>.build_<snake>(ctx)`; every `zones.<snake>.build(ctx)`.  A builder that raises
is DROPPED with its `file:line` and everything it created is removed — the rest of the scene
still builds and is measured, so one bad zone costs that zone, not the round.  A module that
raises on import (a bad top-level statement) is gone the same way; `build_<zone>(ctx)`,
`create()`, `main()` and `register()` are never called.  Link every object into
`ctx.collection`: an object linked into `ctx.scene.collection` belongs to no zone and is
graded as the harness's.  Then the harness adds the plan's cameras, hides the asset sources
(they render only through their collection instances), writes the census and the `.blend`, and
renders every camera at t = 0 and t = 1.5 s (frames 1 and 46) with Cycles (32 samples +
OpenImageDenoise, AgX view transform, exposure 0).  Render settings, colour management,
cameras, the frame clock and file IO are the harness's: the lint rejects code that touches them.

## The `ctx` every builder receives (data, never verbs)
| field | what it is |
| --- | --- |
| `ctx.scene` | the bpy Scene (read it; its render settings are harness-owned) |
| `ctx.collection` | the Collection THIS builder links into — a zone's own (named after the zone, active, so `bpy.ops` primitives land in it), an asset's own, the scene root for `build_env` |
| `ctx.height_at(x, y)` | the ground: `env.height_at` once the env built, else flat 0.  Seat every placed thing on it or on the surface it stands on |
| `ctx.env` | the dict `build_env` returned (shared materials, the water level, the sun direction…); `{}` before the env builds |
| `ctx.assets` | `{snake: Collection}` — every procedural asset the harness built and every hero it imported, at the origin, base on z = 0 |
| `ctx.rng(seed)` | `random.Random(seed)` — THE randomness a scene may use (`random.*` and unseeded numpy are lint ERRORS) |
| `ctx.fps`, `ctx.times` | 30 and (0.0, 1.5): the frame clock |
| `ctx.frame(t)` | `1 + round(t * fps)`: the frame that shows animation time t — keyframe on `ctx.frame(0.0)` and `ctx.frame(1.5)` |

## Frame and units
Z is UP, −Y is the FRONT, +X is the right; metres; the ground is `height_at(x, y)` around
z = 0.  The PLAN you are handed was written in the Y-up GLB frame (the three.js convention) —
every number the harness prints in your brief (bounds, zone bboxes, camera positions,
layouts) is already converted to this frame; the plan's prose is not: where it says
"y = 1.2 m up" read z = 1.2, and where it says "+z towards the camera" read −y.  Sizes are
width × depth × height here (the plan's width × height × depth, reordered).

## Allowed imports
`bpy`, `bmesh`, `mathutils` (Vector, Matrix, Euler, noise), `math`, `numpy` (only through
`numpy.random.default_rng(seed)` for randomness), `itertools`, `functools`, `collections`,
`dataclasses`, `colorsys`, and the workspace's own package `env`: `from env import …` (zones may
read whatever `env` defines at module level).  Nothing else resolves in the build.

## FORBIDDEN (the lint's policy table; each is an ERROR that fails the build gate)
`bpy.ops.render.*`, `bpy.ops.wm.*` (open/save/append/link), `bpy.ops.export_*`,
`bpy.ops.import_*` (a hero is already imported), `bpy.ops.image.save`, `bpy.data.libraries`,
`bpy.data.cameras.new` / `bpy.ops.object.camera_add` (cameras are the plan's), any
`scene.render.*` / `scene.cycles.*` / `scene.eevee.*` / `scene.view_settings.*` /
`scene.display_settings.*` / `scene.camera` / `scene.frame_*` assignment, `frame_set`,
`bpy.app.handlers`, `bpy.app.timers`, a driver whose expression needs Python
(`driver.use_self`, attribute access, `**`, `%`, `noise.*` in an expression), the world
outside `src/env.py` (`bpy.data.worlds.new`, `scene.world =`), `random.*` and unseeded numpy,
`open()`, `os` / `sys` / `pathlib` / `time` / `datetime` / `subprocess` / `shutil` / `tempfile`
/ `glob` / `io`.  The lint WARNS on `bpy.ops.mesh.primitive_*` inside a loop (every operator
call rebuilds the scene state) and on `bpy.ops.view3d.*` / `bpy.ops.screen.*` (no viewport in
background mode).

## Blender 5.2 API facts (each is a build error or a silent zero)
Removed: `use_nodes` (deprecated — materials, worlds and lights always have a node tree; a new
material already carries `Principled BSDF` and `Material Output`), `sky_type = 'NISHITA'` (it is
`'MULTIPLE_SCATTERING'`, with `aerosol_density`), the boolean solver `'FAST'` (`FLOAT` / `EXACT` /
`MANIFOLD`), `mod["Socket_2"] = v` for geometry-nodes inputs (`getattr(mod.properties.inputs,
"Socket_2").value = v`), `Action.fcurves` (actions are slotted:
`action.layers[0].strips[0].channelbag(action.slots[0]).fcurves`),
`Mesh.calc_normals()` and `use_auto_smooth` (`me.shade_smooth()` or `f.smooth = True` per face),
the Principled inputs `Specular` / `Subsurface` / `Transmission` / `Emission` (now `Specular IOR
Level`, `Subsurface Weight`, `Transmission Weight`, `Coat Weight`, `Sheen Weight`, `Emission Color`
+ `Emission Strength`), `ShaderNodeMixRGB` (it is `ShaderNodeMix` with `data_type = 'RGBA'`:
inputs 6 / 7 are the colours, output 2 the result; the mix input is `Factor`, not `Fac`).
Background mode: `bpy.context.object` / `selected_objects` are empty after `bpy.data.objects.new`
— keep your own references.  `bm.verts[i]` raises before `ensure_lookup_table()` after any
topology change.  A fresh object's `matrix_world` is identity until the view layer updates
(compose `parent.matrix_world @ child.matrix_basis` yourself, or call `bpy.context.view_layer.update()`
before reading it; parent with a RELATIVE `location`).  Geometry nodes: a new `GeometryNodeTree`
has no `inputs` / `outputs` and no nodes — its sockets are `ng.interface.new_socket(name,
in_out='INPUT' | 'OUTPUT', socket_type='NodeSocketGeometry')`, and the `NodeGroupInput` /
`NodeGroupOutput` nodes are yours to add and link; `obj.modifiers.new(name, 'NODES').node_group = ng`.

## What the renderer does with what you build
Cycles at 32 samples + OpenImageDenoise, AgX, exposure 0, 1024 × 576; no post chain and no
bloom: an emissive surface at strength 3–8 reads as a lamp, 20+ whites the frame out
(`blown_frac`), and a lamp beside it is what lights the wall.  A SURFACE shader always has a
silhouette: in Cycles a soft particle (steam, smoke, dust, spray) renders without an edge only
as a VOLUME — a material whose only linked output is the Material Output's `Volume`, fed by a
`ShaderNodeVolumePrincipled`; a translucent puff, metaball, torus or disc is graded
`hard_edged_particles`.  The census is the evaluated depsgraph: modifiers, geometry
nodes and instances count as they render; a volume-only mesh is fog, never a placed thing.
Triangle budgets: 15,000 per asset, 40,000 per hero, 3,000,000 unique for the scene (an
instance cloud counts its instances rendered, its source once).

## Motion
Animation is DATA in the `.blend`: `obj.keyframe_insert(data_path, frame=ctx.frame(t))` for the
two times in `ctx.times` (a location, a rotation, a shape-key value, a node input), or a SIMPLE
driver of `frame` (`fc = obj.driver_add("rotation_euler", 2)`, `fc.driver.expression = "frame * 0.1"`
— sin / cos / min / max / clamp / lerp, `+ - * /`, comparisons; nothing that needs Python, which a
`--factory-startup` build silently evaluates to 0).  Keys live on the slotted action:
`a = obj.animation_data.action`, then `a.layers[0].strips[0].channelbag(a.slots[0]).fcurves`; set
each `keyframe_points[].interpolation = 'LINEAR'` for motion that should read at t = 1.5 as a
travel, not an ease.  The harness differences the two frames per camera
and reports the number (a camera moves at ≥ 0.4 % of its pixels changed by > 8/255; none moving
is the `no_motion` ERROR, the judge's `nothing_moves`).  Amplitudes that have registered
at 1024 × 576: ≥ 0.10 rad of rotation, ≥ 0.05 m of travel per 1.5 s, a particle / leaf / boat
≥ 0.6 m.  A hero GLB's own keyframed clip plays in every instance by itself.

## Naming
PascalCase object names, globally unique — a Blender object name is global, so a second
`Lantern_0` becomes `Lantern_0.001` and the placement table then reports a prop nobody
planned: number every placed copy `f"{base}_{i}"` (`Lantern_0`, `Lantern_1`…) and never reuse a
base another zone uses.  The census classifies by name: a
ground / terrain mesh is `Ground…` / `Terrain…`, water `Water…`, and anything meant as distant
backdrop (if the brief's place has one) `Sky…` — a sky-named mesh wider than 50 m is filed as
backdrop, not content, so the overview cameras frame the content; an interior's shell is
`RoomShell…` / `Ceiling…` (the overview rig lifts the lid by name).

## Placement (the scene_placement gate, run on every build)
Every root object you link into a zone (a mesh, a parent Empty with children, a
collection-instance Empty) is ONE placement row.  From its lowest evaluated vertices the gate
looks down for the nearest surface and up for one passing through it: a gap under the foot
larger than 0.05 m outdoors / 0.02 m indoors is `floating` (an ERROR at 0.08 m, or when more
than 2 things float); a foot buried deeper than 0.10 m is `sunken` (an ERROR at 0.20 m and a
fifth of the thing's height — rocks, posts and bushes may sit partly in; stairs and ramps may
follow the slope; a foot at or under water is fine); a bbox touching nothing is `unsupported`;
two boxes sharing more than 10% of the smaller one's volume is `interpenetration` (an ERROR at 35%).
The feet are the gate's foot band: the root's evaluated vertices within max(0.02 m, 5 % of its
height) of its lowest one, sampled as up to 5 columns (the lowest vertex and the band's ±x / ±y
extremes).  Seating is yours to compute: take the root's world-space vertices (its own, its
children's, and for a collection-instance Empty the source collection's through the Empty's
matrix), keep that band, and move the root by the smallest `ctx.height_at(x, y) - z` over it —
the lowest-hanging foot then touches and none floats.  The pair check is yours too: keep any two
roots' world bounding boxes from sharing more than 10% of the smaller one.  A thing resting on one corner with the others in
the air (four or more foot columns, at most one touching, the worst gap > 0.05 m) is
`cantilevered` (a WARN).  `obj["placement"] = "free"` exempts a thing MEANT
to hang in the air (a bird, a lantern on a chain, a kite) — nothing else: the gate reports the
share of placed things tagged free and warns past 25%, errors past 50%.  Geometry-nodes instance
clouds (an Instance on Points tree over a ground patch) are counted as instances and exempt per
asset.  A PLANNED content is also measured against the plan bounds
(`out_of_bounds`, a WARN: entirely outside them by more than max(5 m, 10 % of the span); scenery that
continues the place past the bounds is never reported) and an asset placed
at a size other than the plan's (beyond 2.5 × is a WARN, 4 × an ERROR).

## What else the gates measure
`scene_frames`: every authored camera's frame at both times — `dark_frame` (mean luminance
< 0.12 or > 35 % near-black, cap 0.55), `blown_frame` (> 20 % near-white), `flat_frame` (> 85 %
of the pixels in one luminance band; ERROR above 92 %), mostly sky / ground (an establishing
camera needs ≥ 20 % of its frame to be content, every other authored camera ≥ 10 %:
`content_small`), the camera inside geometry or within 0.5 m of a surface
(`camera_in_geometry`), 6 or more of its 9 sight rays ending within 1.5 m (`camera_blocked`), or
below the ground under it (`camera_underground`) — the camera findings cap the score at 0.50; an
eye less than 0.3 m above the ground is a `camera_low` WARN, more than 80 m above it
`camera_high`.  `frame_motion`: the two frames are differenced per camera — a camera MOVES when
≥ 0.4 % of its pixels change by more than 8/255 (or ≥ 0.08 % by more than 60/255); no authored
camera moving is `no_motion` (ERROR), and the judge's `nothing_moves` follows it.  A hero GLB
covering < 0.5 % of the frame in every authored camera is `hero_unseen` (ERROR; < 2 % in the
camera named for it, a WARN).  `scene_probe` (from the census GLB and the bpy facts): unique
triangles over the scene ceiling (3,000,000), a Python driver or an app handler, no world / no
atmosphere (`no_fog`: neither a world Volume nor a bounded fog volume — mist is not read),
render settings touched.  Outdoors, `fog_short` warns when 1 / density < span / 1.5 (the census
reads the volume's density: a density above 1.5 / span dissolves the world inside two thirds of
the plan span) and `world_edge` warns when no standing geometry reaches past 1.25 × the bounds'
half-extent — the world continues past the content in whatever form the brief's place has.  A
hero that would not import is a WARN on its file; a zone / asset / env that raised is an ERROR on
its line.

## Interiors
A plan with `interior: true`: the enclosure (floor, walls, lid) is `src/env.py`'s, with the lid
meshes named `RoomShell…` / `Ceiling…`; it must read enclosed from every authored camera and be
lit inside (the frame gate); the floating threshold indoors is 0.02 m.  The room's shape, its
openings and its extent are the brief's, built in `build_env`.  Zones dress the inside;
a partition, counter or alcove a zone needs is the zone's, seated and clear of the shell.

## Realism (judged; the gates above measure the rest)
Surfaces are photoreal: every large surface varies at more than one scale — broad tone and wear
over metres AND fine grain or bump over centimetres — in a node tree you wire (Noise / Voronoi /
Wave textures through Mix and Bump nodes); one constant colour per surface is
`placeholder_material` / `flat_ground`.  Natural things have organic silhouettes: a tree is a
trunk branching into limbs that carry leaf or needle cards, a rock an irregular asymmetric mass —
never a cone, a ball on a stick or a cube standing in for one (`asset_quality_detail`); one
shape stamped many times is `monotonous_vegetation`.  The world
continues past the content in every authored frame (`world_edge`, `fog_short`; judged
`world_edge_visible`).

## Two modules the harness builds as-is (the interface shown once; the content is a harbour, yours is the brief's)
`src/env.py`
```python
"""src/env.py — the environment of `Harbour at dusk`: world, sun, ground, atmosphere."""
import math

import bmesh
import bpy

SUN_AZIMUTH_DEG, SUN_ELEVATION_DEG = 250.0, 8.0   # the plan's light (compass 0 = +X, 90 = +Y); zones may `from env import` these
WORLD_M = 320.0                                   # how far this place's land and air reach (plan span 100 m): the brief's number


def height_at(x, y):   # THE ground: any function of (x, y) the brief's landform needs — here level quay land, a basin east, ground rising inland
    return -2.0 * min(1.0, x / 25.0) if x >= 0.0 else 0.04 * max(0.0, -45.0 - x)


def linked(ctx, name, bm, mat):                   # code zones share lives here (`from env import linked`)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ctx.collection.objects.link(ob := bpy.data.objects.new(name, me))
    return ob


def build_env(ctx):
    ctx.scene.world = bpy.data.worlds.new("Dusk")   # REQUIRED: a world — its sky is the brief's (§ Blender 5.2 API facts)
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))   # REQUIRED: a lamp; a Sun shines down its own -Z
    sun.rotation_euler = (math.radians(90.0 - SUN_ELEVATION_DEG), 0.0, math.radians(SUN_AZIMUTH_DEG + 90.0))
    ctx.collection.objects.link(sun)
    ground = bpy.data.materials.new("GroundMat")    # STAND-IN surface: one constant colour is graded flat_ground (§ Realism)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=160, y_segments=160, size=WORLD_M / 2)   # size is the half-side
    for v in bm.verts:                               # REQUIRED: the ground samples height_at, which zones seat on
        v.co.z = height_at(v.co.x, v.co.y)
    linked(ctx, "Ground", bm, ground)
    haze = bpy.data.materials.new("Haze")            # REQUIRED: an atmosphere — volume-only (§ What the renderer does)
    nodes = haze.node_tree.nodes
    nodes.remove(nodes["Principled BSDF"])
    vol = nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = 1.0 / WORLD_M   # 1 / density >= span / 1.5 (fog_short)
    haze.node_tree.links.new(vol.outputs["Volume"], nodes["Material Output"].inputs["Volume"])
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(WORLD_M, WORLD_M, 30.0), verts=bm.verts)
    linked(ctx, "Atmosphere", bm, haze).location.z = 15.0
    return {"ground_material": ground, "water_level": 0.0}
```
`src/zones/quay.py`
```python
"""src/zones/quay.py — zone "Quay": the granite quay deck with its bollards."""
import bmesh
import bpy

from env import linked

CENTRE, DECK_TOP, N_BOLLARDS = (-20.0, 0.0), 1.2, 6   # the plan's zone centre, deck height and count, in this frame


def build(ctx):
    rng = ctx.rng(11)
    granite = bpy.data.materials.new("QuayGranite")  # STAND-IN surface: the deck's granite is yours to wire (§ Realism)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(40.0, 60.0, DECK_TOP), verts=bm.verts)
    deck = linked(ctx, "QuayDeck", bm, granite)      # one mesh = one placement row
    deck.location = (*CENTRE, ctx.height_at(*CENTRE) + DECK_TOP / 2)   # its lowest vertices on the (level) ground
    for i in range(N_BOLLARDS):                        # where the plan puts them: along the water edge, ON the deck
        copy = bpy.data.objects.new(f"Bollard_{i}", None)   # a collection-instance Empty = one placement row
        copy.instance_type, copy.instance_collection = "COLLECTION", ctx.assets["bollard"]
        copy.location = (CENTRE[0] + 18.0 + rng.uniform(-0.2, 0.2), CENTRE[1] - 25.0 + i * 10.0, DECK_TOP)   # the deck top is its ground
        copy.rotation_euler.z = rng.uniform(0.0, 6.283)
        ctx.collection.objects.link(copy)
    return ctx.collection
```
