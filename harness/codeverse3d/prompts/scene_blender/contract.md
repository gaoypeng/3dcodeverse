# Scene (multi-file Blender bpy) authoring contract — track `scene`, language `scene_blender`

## Files (Blender 5.2 LTS python; Z up, −Y front, metres)
```
src/scene.py            ASSEMBLED BY THE HARNESS — data only (ZONES / ASSETS / HEROES / CAMERAS); never edit it
src/env.py              def height_at(x, y) -> z    def build_env(ctx) -> dict     (world, light, ground, atmosphere; the enclosure of an interior)
src/zones/<snake>.py    def build(ctx) -> bpy.types.Collection      (everything linked into ctx.collection, the zone's own Collection)
src/assets/<snake>.py   def build_<snake>(ctx, variant=0) -> bpy.types.Collection   (built ONCE at the origin, base on z = 0, front facing −Y)
src/lib/*.py            HARNESS-OWNED bpy library — an optional toolbox: import and call, never edit (a write to it is reverted; the catalog ships with every brief)
public/assets/<snake>.glb   heroes the harness built as objects — imported for you into ctx.assets['<snake>']
```

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
`dataclasses`, `colorsys`, and the workspace's own packages: `from lib.<module> import …`,
`from env import …` (zones may read whatever `env` defines at module level).  Nothing else
resolves in the build.

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
`action.layers[0].strips[0].channelbag(action.slots[0]).fcurves`, or `lib.motion.fcurves`),
`Mesh.calc_normals()` and `use_auto_smooth` (`me.shade_smooth()` or `f.smooth = True` per face),
the Principled inputs `Specular` / `Subsurface` / `Transmission` / `Emission` (now `Specular IOR
Level`, `Subsurface Weight`, `Transmission Weight`, `Coat Weight`, `Sheen Weight`, `Emission Color`
+ `Emission Strength`), `ShaderNodeMixRGB` (it is `ShaderNodeMix` with `data_type = 'RGBA'`:
inputs 6 / 7 are the colours, output 2 the result; the mix input is `Factor`, not `Fac`).
Background mode: `bpy.context.object` / `selected_objects` are empty after `bpy.data.objects.new`
— keep your own references.  `bm.verts[i]` raises before `ensure_lookup_table()` after any
topology change.  A fresh object's `matrix_world` is identity until the view layer updates
(`lib.place.world_matrix`, or `bpy.context.view_layer.update()` before reading it; parent with a
RELATIVE `location`).

## What the renderer does with what you build
Cycles at 32 samples + OpenImageDenoise, AgX, exposure 0, 1024 × 576; no post chain and no
bloom: an emissive surface at strength 3–8 reads as a lamp, 20+ whites the frame out
(`blown_frac`), and a lamp beside it is what lights the wall.  A SURFACE shader always has a
silhouette: in Cycles a soft particle (steam, smoke, dust, spray) renders without an edge only
as a Principled VOLUME (`lib.materials.volume`); a translucent puff, metaball, torus or disc is
graded `hard_edged_particles`.  The census is the evaluated depsgraph: modifiers, geometry
nodes and instances count as they render; a volume-only mesh is fog, never a placed thing.
Triangle budgets: 15,000 per asset, 40,000 per hero, 3,000,000 unique for the scene (an
instance cloud counts its instances rendered, its source once).

## Motion
Animation is DATA in the `.blend`: keyframes on `ctx.frame(t)` for the two times in
`ctx.times` (`lib.motion.keyframe_path`, `drift`, `sway`, `shape_key_sweep`), or a SIMPLE
driver of `frame` (`lib.motion.spin`: `frame * 0.1` — sin / cos / min / max / clamp / lerp,
`+ - * /`, comparisons; nothing that needs Python, which a `--factory-startup` build silently
evaluates to 0).  Set `keyframe_points[].interpolation = 'LINEAR'` for motion that should
read at t = 1.5 as a travel, not an ease.  The harness differences the two frames per camera
and reports the number (a camera moves at ≥ 0.4 % of its pixels changed by > 8/255; none moving
is the `no_motion` ERROR, the judge's `nothing_moves`).  Amplitudes that have registered
at 1024 × 576: ≥ 0.10 rad of rotation, ≥ 0.05 m of travel per 1.5 s, a particle / leaf / boat
≥ 0.6 m.  A hero GLB's own keyframed clip plays in every instance by itself.

## Naming
PascalCase object names, globally unique — a Blender object name is global, so a second
`Lantern_0` becomes `Lantern_0.001` and the placement table then reports a prop nobody
planned: number every placed copy with `lib.place.unique_name(base, i)` (`Lantern_0`,
`Lantern_1`…) and never reuse a base another zone uses.  The census classifies by name: a
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
`lib.place.seat(obj, ctx.height_at)` samples the same foot columns the gate does (gap ≤ 0.02 m
after); `lib.scatter.instance(..., height_at=ctx.height_at)` seats as it places;
`lib.place.clear_of(objs)` is the pair check.  A thing resting on one corner with the others in
the air (four or more foot columns, at most one touching, the worst gap > 0.05 m) is
`cantilevered` (a WARN).  `obj["placement"] = "free"` exempts a thing MEANT
to hang in the air (a bird, a lantern on a chain, a kite) — nothing else: the gate reports the
share of placed things tagged free and warns past 25%, errors past 50%.  Geometry-nodes instance
clouds (`lib.vegetation.grass_patch`, `lib.scatter.gn_scatter`) are counted as instances and
exempt per asset, like scatter is.  A PLANNED content is also measured against the plan bounds
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
lit inside (the frame gate); the floating threshold indoors is 0.02 m.  `lib.architecture.room_shell`
builds a box with openings; the room's shape and extent are the brief's.  Zones dress the inside;
a partition, counter or alcove a zone needs is the zone's, seated and clear of the shell.

## Two modules the harness builds as-is (the interface shown once; the content is a harbour, yours is the brief's)
`src/env.py`
```python
"""src/env.py — the environment of `Harbour at dusk`: world, sun, ground, atmosphere."""
from lib.lighting import sun
from lib.materials import material
from lib.sky import haze, sun_world
from lib.terrain import ground

SUN_AZIMUTH_DEG, SUN_ELEVATION_DEG = 250.0, 8.0   # the plan's light (compass 0 = +X, 90 = +Y); zones may `from env import` these
WORLD_M = 320.0                                   # how far this place's land and air reach (plan span 100 m): the brief's number


def height_at(x, y):   # THE ground: any function of (x, y) the brief's landform needs — here level quay land, a basin east, ground rising inland
    return -2.0 * min(1.0, x / 25.0) if x >= 0.0 else 0.04 * max(0.0, -45.0 - x)


def build_env(ctx):
    sun_world(ctx.scene, elevation_deg=SUN_ELEVATION_DEG, azimuth_deg=SUN_AZIMUTH_DEG, strength=0.25, dust=3.0)
    _, towards_sun = sun(ctx.collection, azimuth_deg=SUN_AZIMUTH_DEG, elevation_deg=SUN_ELEVATION_DEG, color="FFB070", energy=2.5)
    ground_mat = material("GroundMat", "4A5A33", roughness=1.0, noise_scale=0.35)
    ground("Ground", ctx.collection, size=WORLD_M, segments=160, height_at=height_at, material=ground_mat)       # past every camera's fog
    haze(ctx.collection, (0.0, 0.0, 15.0), (WORLD_M, WORLD_M, 30.0), density=1.0 / WORLD_M, color="D9703A")   # 1/density ≥ span/1.5
    return {"ground_material": ground_mat, "sun_direction": tuple(towards_sun), "water_level": 0.0}
```
`src/zones/quay.py`
```python
"""src/zones/quay.py — zone "Quay": the granite quay deck with its bollards."""
from lib.materials import material
from lib.mesh import Batch
from lib.place import clear_of, seat, unique_name
from lib.scatter import instance

CENTRE, DECK_TOP, N_BOLLARDS = (-20.0, 0.0), 1.2, 6   # the plan's zone centre, deck height and count, in this frame


def build(ctx):
    rng = ctx.rng(11)
    granite = material("QuayGranite", "6E6A63", roughness=0.9, noise_scale=0.6)
    b = Batch("QuayDeck")                              # one mesh per material, one placement row
    b.box(granite, (CENTRE[0], CENTRE[1], DECK_TOP / 2), (40.0, 60.0, DECK_TOP))
    deck = b.finish(ctx.collection, parent=False)[0]
    seat(deck, ctx.height_at)                          # its lowest vertices meet the ground
    placed = [deck]
    for i in range(N_BOLLARDS):                        # where the plan puts them: along the water edge, ON the deck
        x, y = CENTRE[0] + 18.0 + rng.uniform(-0.2, 0.2), CENTRE[1] - 25.0 + i * 10.0
        placed.append(instance(ctx.collection, ctx.assets["bollard"], unique_name("Bollard", i), x, y,
                               height_at=lambda px, py: DECK_TOP, rot_z=rng.uniform(0.0, 6.283)))
    for a, b_, frac in clear_of(placed):               # [] here — nothing shares more than 10 % of the smaller box
        print("quay: fix the overlap", a, "x", b_, f"{frac:.0%}")
    return ctx.collection
```
