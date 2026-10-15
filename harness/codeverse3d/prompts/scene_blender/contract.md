# Scene (multi-file Blender bpy) authoring contract — track `scene`, language `scene_blender`

## Files (Blender 5.2 LTS python; Z up, −Y front, metres)
```
src/scene.py            ASSEMBLED BY THE HARNESS — data only (ZONES / ASSETS / HEROES / CAMERAS); never edit it
src/env.py              def height_at(x, y) -> z    def build_env(ctx) -> dict     (world, sun, ground, atmosphere; the enclosure of an interior)
src/zones/<snake>.py    def build(ctx) -> bpy.types.Collection      (everything linked into ctx.collection, the zone's own Collection)
src/assets/<snake>.py   def build_<snake>(ctx, variant=0) -> bpy.types.Collection   (built ONCE at the origin, base on z = 0, front facing −Y)
src/lib/*.py            HARNESS-OWNED bpy library — import and call, never edit (a write to it is reverted; the catalog ships with every brief)
public/assets/<snake>.glb   heroes the harness built as objects — imported for you into ctx.assets['<snake>']
```

The harness builds the scene in ONE Blender 5.2 process (`--factory-startup`, an emptied scene,
metric units, fps=30, the frame range 1..46, every random source seeded) in this order, each
builder in its own try/except: `env.build_env(ctx)`; every hero GLB imported; every
`assets.<snake>.build_<snake>(ctx)`; every `zones.<snake>.build(ctx)`.  A builder that raises
is DROPPED with its `file:line` and everything it created is removed — the rest of the scene
still builds and is measured, so one bad zone costs that zone, not the round.  Then the
harness adds the plan's cameras, hides the asset sources (they render only through their
collection instances), writes the census and the `.blend`, and renders every camera at
t = 0 and t = 1.5 s (frames 1 and 46) with Cycles (32 samples + OpenImageDenoise, AgX view
transform, exposure 0).  Render settings, colour management, cameras, the frame clock and
file IO are the harness's: the lint rejects code that touches them.

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
`from env import …` (zones may read env constants).  Nothing else resolves in the build.

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
/ `glob` / `io`.  Blender 5.2 removed: `use_nodes` (deprecated — materials, worlds and lights
always have a node tree), `sky_type = 'NISHITA'` (it is `'MULTIPLE_SCATTERING'`), the boolean
solver `'FAST'` (`FLOAT` / `EXACT` / `MANIFOLD`), `mod["Socket_2"] = v` for geometry-nodes
inputs (`getattr(mod.properties.inputs, "Socket_2").value = v`), `Action.fcurves`
(`action.layers[0].strips[0].channelbag(action.slots[0]).fcurves`), `Mesh.calc_normals()`,
`use_auto_smooth`, the Principled inputs `Specular` / `Subsurface` / `Transmission` /
`Emission` (now `Specular IOR Level`, `Subsurface Weight`, `Transmission Weight`,
`Emission Color` + `Emission Strength`), `ShaderNodeMixRGB` (it is `ShaderNodeMix` with
`data_type = 'RGBA'`: inputs 6 / 7 are the colours, output 2 the result).

## Motion
Animation is DATA in the `.blend`: keyframes on `ctx.frame(t)` for the two times in
`ctx.times` (`lib.motion.keyframe_path`, `drift`, `sway`, `shape_key_sweep`), or a SIMPLE
driver of `frame` (`lib.motion.spin`: `frame * 0.1` — sin / cos / min / max / clamp / lerp,
`+ - * /`, comparisons; nothing that needs Python, which a `--factory-startup` build silently
evaluates to 0).  Set `keyframe_points[].interpolation = 'LINEAR'` for motion that should
read at t = 1.5 as a travel, not an ease.  Amplitudes that read in a 1024 × 576 frame: ≥ 0.10
rad of rotation, ≥ 0.05 m of travel per 1.5 s, a particle / leaf / boat ≥ 0.6 m.  A hero
GLB's own keyframed clip plays in every instance by itself.

## Naming
PascalCase object names, globally unique — a Blender object name is global, so a second
`Lantern_0` becomes `Lantern_0.001` and the placement table then reports a prop nobody
planned: number every placed copy with `lib.place.unique_name(base, i)` (`Lantern_0`,
`Lantern_1`…) and never reuse a base another zone uses.  Name the terrain and the backdrop
`Ground…` / `Terrain…` / `Water…` / `Sky…` (`SkyRidge`: the census classifies by name — a sky-named
mesh wider than 50 m is backdrop, not content);
an interior's shell `RoomShell…` / `Ceiling…` (the overview rig lifts the lid by name).

## Placement (the scene_placement gate, run on every build)
Every root object you link into a zone (a mesh, a parent Empty with children, a
collection-instance Empty) is ONE placement row.  From its lowest evaluated vertices the gate
looks down for the nearest surface and up for one passing through it: a gap under the foot
larger than 0.05 m outdoors / 0.02 m indoors is `floating` (an ERROR at 0.08 m, or when more
than 2 things float); a foot buried deeper than 0.10 m is `sunken` (an ERROR at 0.20 m and a
fifth of the thing's height — rocks, posts and bushes may sit partly in; stairs and ramps may
follow the slope; a foot at or under water is fine); a bbox touching nothing is `unsupported`;
two boxes sharing more than 10% of the smaller one's volume is `interpenetration` (an ERROR at 35%).
Seat with `lib.place.seat(obj, ctx.height_at)` (the same foot columns the gate samples: gap ≤ 0.02 m
after) or `lib.scatter.instance(..., height_at=ctx.height_at)`, and check your pairs with
`lib.place.clear_of(objs)` before you finish.  `obj["placement"] = "free"` exempts
a thing MEANT to hang in the air (a bird, a lantern on a chain, a kite) — nothing else: the
gate reports the share of placed things tagged free and warns past 25%, errors past 50%.
Geometry-nodes instance clouds (`lib.vegetation.grass_patch`, `lib.scatter.gn_scatter`) are
counted as instances and exempt per asset, like scatter is.

## What else the gates measure
`scene_frames`: every authored camera's frame at both times — too dark, blown out, mostly
sky / ground, the camera inside geometry or below the ground.  `scene_probe` (from the census
GLB and the bpy facts): unique triangles over the scene ceiling (3,000,000), a Python driver
or an app handler, no world / no atmosphere, render settings touched.  A hero that would not
import is a WARN on its file; a zone / asset / env that raised is an ERROR on its line.

## COMPLETE minimal examples (the harness builds exactly these; copy their shape)
`src/env.py`
```python
"""src/env.py — the environment of `Harbour at dusk`: world, sun, ground, atmosphere."""
import bpy

from lib.lighting import sun
from lib.materials import material
from lib.sky import haze, sun_world
from lib.terrain import ground, make_height_at

BOUNDS = {"center": (0.0, 0.0, 5.0), "extents": (120.0, 120.0, 30.0)}   # the plan's, in this frame
SUN_AZIMUTH_DEG = 250.0     # compass angle of the sun: 0 = +X, 90 = +Y
SUN_ELEVATION_DEG = 8.0
_terrain = make_height_at(seed=3, relief=1.2, scale=40.0, flat_radius=60.0)


def height_at(x, y):
    """THE ground (z at x, y): level inside the content radius, rolling beyond; every seat reads it."""
    return _terrain(x, y)


def build_env(ctx):
    sun_world(ctx.scene, elevation_deg=SUN_ELEVATION_DEG, azimuth_deg=SUN_AZIMUTH_DEG, strength=0.25, dust=3.0)
    _, towards_sun = sun(ctx.collection, azimuth_deg=SUN_AZIMUTH_DEG, elevation_deg=SUN_ELEVATION_DEG,
                         color="FFB070", energy=2.5)
    ground_mat = material("GroundMat", "4A5A33", roughness=1.0, noise_scale=0.35)
    ground("Ground", ctx.collection, size=320.0, segments=160, height_at=height_at, material=ground_mat)
    haze(ctx.collection, (0.0, 0.0, 15.0), (320.0, 320.0, 30.0), density=0.004, color="D9703A")
    return {"ground_material": ground_mat, "sun_direction": tuple(towards_sun), "water_level": 0.0}
```
`src/zones/quay.py`
```python
"""src/zones/quay.py — zone "Quay": L-shaped granite quay with bollards and crates."""
import bmesh
import bpy

from lib.materials import material
from lib.place import clear_of, seat, unique_name
from lib.scatter import instance

CENTRE = (-20.0, 0.0)          # the plan's zone centre, in this frame
DECK_TOP = 1.2                 # the quay stands 1.2 m above the water


def _slab(name, mat, size, loc, collection):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bmesh.ops.translate(bm, vec=loc, verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    return ob


def build(ctx):
    rng = ctx.rng(11)
    granite = material("QuayGranite", "6E6A63", roughness=0.9, noise_scale=0.6)
    deck = _slab("QuayDeck", granite, (40.0, 60.0, DECK_TOP), (CENTRE[0], CENTRE[1], DECK_TOP / 2), ctx.collection)
    seat(deck, ctx.height_at)                      # its lowest vertices meet the ground
    placed = [deck]
    for i in range(6):                             # bollards along the water edge, ON the deck
        x = CENTRE[0] + 18.0 + rng.uniform(-0.2, 0.2)
        y = CENTRE[1] - 25.0 + i * 10.0
        placed.append(instance(ctx.collection, ctx.assets["bollard"], unique_name("Bollard", i), x, y,
                               height_at=lambda px, py: DECK_TOP, rot_z=rng.uniform(0.0, 6.283)))
    for a, b, frac in clear_of(placed):            # [] here — nothing shares more than 10 % of the smaller box
        print("quay: fix the overlap", a, "x", b, f"{frac:.0%}")
    return ctx.collection
```
