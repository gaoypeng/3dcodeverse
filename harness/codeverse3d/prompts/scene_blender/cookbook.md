# Scene cookbook — multi-file Blender bpy scenes (5.2 LTS), built and rendered headless by the harness

Every `python` snippet runs as-is INSIDE a zone's `build(ctx)`: the harness test suite writes
each one into `src/zones/<n>.py` under `import math · import bmesh · import bpy ·
from mathutils import Euler, Matrix, Vector, noise` with `rng = ctx.rng(7)` in scope, builds
the workspace in Blender 5.2.2 and lints it as a zone.  A block whose first line is `# role: env` is
module-level code of `src/env.py` instead (the world is only ever built there).  Z up, −Y
front, metres.  Materials, lights and worlds always have a node tree (no `use_nodes`).  The
shipped `src/lib/` does most of what is below in one call — its catalog is in your brief;
these snippets show the raw construction so you can read, extend or replace a call.  The
harness inlines the relevant chapters into your prompts; the full file is at
`.3dcode/cookbook.md` in your workspace.

**The chapters that decide the score.**  Scenes on this track lose most of their points to
a few repeatable defects, one chapter each:

| the judge / gate writes | read this chapter |
|---|---|
| "flat ground plane", "one colour", "no paths" | Ground that reads real |
| "a hard world edge is visible against the sky" | Horizon: the world must not end |
| `floating` / `sunken` / `interpenetration` (the placement gate) | Placement self-check |
| "monotonous / identical cones", "primitive stacked shapes" | Vegetation that reads real · Rocks, cliffs and boulders |
| "sparse", "a diorama on an empty plane", "no midground" | Scene layering · Set dressing |
| "flat light", "too dark", "monochrome", `dark_frame` | Atmosphere: time-of-day triads · Dusk / night lighting recipe · Lighting for Cycles + AgX |
| "frames at t=0 and t=1.5 are identical", `nothing_moves` | Motion you can SEE between t = 0 and t = 1.5 s |
| "plastic", "one grey material", "untextured" | Materials: Principled recipes and the lib palette |

## Environment (`src/env.py`): world + sun + ground + atmosphere — the scene's floor comes first

```python
# role: env
import math

import bmesh
import bpy
from mathutils import Vector, noise

BOUNDS = {"center": (0.0, 0.0, 5.0), "extents": (120.0, 120.0, 30.0)}   # the plan's, in this frame
MOOD = "dusk"                  # the brief's time of day, decided ONCE (the time-of-day table below)
SUN_AZIMUTH_DEG = 250.0        # compass angle of the sun: 0 = +X, 90 = +Y
SUN_ELEVATION_DEG = 8.0
GROUND_SIZE = 320.0            # >= 2.5 x the plan span: the ground must reach past the haze
GROUND_SEGMENTS = 160          # ~1 vertex per 2 m over 320 m; fine enough for a carved path
PATH = [(-26.0, 20.0), (-9.0, 7.0), (4.0, -2.0), (17.0, -15.0), (26.0, -26.0)]   # (x, y) polyline
PATH_HALF_W = 0.9              # a 1.8 m path, carved INTO the height so props on it sit on it


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def _smooth(x, a, b):
    t = _clamp((x - a) / (b - a))
    return t * t * (3.0 - 2.0 * t)


def _mix(c1, c2, t):
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def _dist_to_path(x, y):
    best = 1e9
    for (ax, ay), (bx, by) in zip(PATH, PATH[1:]):
        vx, vy, wx, wy = bx - ax, by - ay, x - ax, y - ay
        t = _clamp((wx * vx + wy * vy) / (vx * vx + vy * vy))
        best = min(best, math.hypot(wx - t * vx, wy - t * vy))
    return best


def height_at(x, y):
    """THE ground: rolling relief within +/-1.5 m (the plan's cameras are absolute heights), flat on
    the path.  Every zone seats on this; the ground mesh IS this function sampled."""
    base = 1.1 * noise.fractal(Vector((x * 0.035, y * 0.035, 0.0)), 1.0, 2.0, 3) + 0.25 * math.sin(x * 0.08) * math.cos(y * 0.06)
    flat = 1.0 - _clamp((_dist_to_path(x, y) - PATH_HALF_W) / 2.2)        # 1 on the path -> 0 at 3.1 m
    return base * (1.0 - 0.85 * flat) - 0.05 * flat                       # the path is flat and slightly sunken


def _ground_material():
    """Base colour from the mesh's 'Blend' colour attribute (one material, four blended colours), plus a
    noise bump so a flat area still catches the light."""
    mat = bpy.data.materials.new("GroundMat")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 1.0
    attr = nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "Blend"
    links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    tex = nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = 6.0
    tex.inputs["Detail"].default_value = 6.0
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.25
    links.new(tex.outputs["Factor"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def _ground(ctx, mat):
    """The terrain mesh displaced by height_at, colour-blended by height, slope, noise and the path."""
    me = bpy.data.meshes.new("Ground")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=GROUND_SEGMENTS, y_segments=GROUND_SEGMENTS, size=GROUND_SIZE / 2)
    for v in bm.verts:
        v.co.z = height_at(v.co.x, v.co.y)
    for f in bm.faces:
        f.smooth = True
    bm.to_mesh(me)
    bm.free()
    grass, dry, dirt, rock, path = (0.33, 0.44, 0.18), (0.54, 0.51, 0.28), (0.42, 0.33, 0.20), (0.43, 0.42, 0.39), (0.66, 0.60, 0.49)
    blend = me.color_attributes.new("Blend", "FLOAT_COLOR", "POINT")
    e = GROUND_SIZE / GROUND_SEGMENTS
    for v, c in zip(me.vertices, blend.data):
        x, y, z = v.co
        slope = min(1.0, (abs(height_at(x + e, y) - z) + abs(height_at(x, y + e) - z)) / (e * 1.2))
        n = 0.5 + 0.5 * noise.noise(Vector((x * 0.35, y * 0.35, 0.0)))     # breaks every band into patches
        col = _mix(grass, dry, _clamp(0.35 + z * 0.5 + (n - 0.5) * 0.9))
        col = _mix(col, dirt, _smooth(slope, 0.12, 0.45))
        col = _mix(col, rock, _smooth(slope, 0.45, 0.9))
        col = _mix(col, path, (1.0 - _smooth(_dist_to_path(x, y), PATH_HALF_W, PATH_HALF_W + 1.4)) * (0.75 + 0.25 * n))
        c.color = (col[0], col[1], col[2], 1.0)
    me.materials.append(mat)
    ob = bpy.data.objects.new("Ground", me)
    ctx.collection.objects.link(ob)
    return ob


def _sun(ctx, color, energy):
    """ONE Sun lamp from the compass angle + elevation; returns the unit vector TOWARDS the sun."""
    az, el = math.radians(SUN_AZIMUTH_DEG), math.radians(SUN_ELEVATION_DEG)
    towards_sun = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
    lamp = bpy.data.lights.new("Sun", "SUN")
    lamp.energy = energy                   # W/m^2: 2-5 for day, 0.05-0.3 for a moon (AgX, exposure 0)
    lamp.color = color
    lamp.angle = math.radians(2.0)         # soft-edged shadows; 0.5 deg is the real sun, too crisp for a diorama
    ob = bpy.data.objects.new("Sun", lamp)
    ob.rotation_euler = (-towards_sun).to_track_quat("-Z", "Y").to_euler()
    ctx.collection.objects.link(ob)
    return towards_sun


def _atmosphere(ctx, density, color):
    """A BOUNDED fog volume over the scene (a world volume over a large scene swallows the sun).  The
    census reads a volume-only mesh as fog, never as matter, so it is no placement row."""
    me = bpy.data.meshes.new("Atmosphere")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(GROUND_SIZE, GROUND_SIZE, 40.0), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(BOUNDS["center"][0], BOUNDS["center"][1], 18.0), verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    mat = bpy.data.materials.new("AtmosphereFog")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.remove(nodes["Principled BSDF"])
    fog = nodes.new("ShaderNodeVolumePrincipled")
    fog.inputs["Density"].default_value = density
    fog.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    links.new(fog.outputs["Volume"], nodes["Material Output"].inputs["Volume"])
    me.materials.append(mat)
    ctx.collection.objects.link(bpy.data.objects.new("Atmosphere", me))


def build_env(ctx):
    tod = apply_time_of_day(ctx, MOOD)             # the world (sky texture + strength) from the table below
    towards_sun = _sun(ctx, tod["sun_color"], tod["sun_energy"])
    mat = _ground_material()
    _ground(ctx, mat)
    _atmosphere(ctx, tod["fog_density"], tod["fog_color"])
    return {"ground_material": mat, "sun_direction": tuple(towards_sun), "mood": MOOD, "palette": tod}
```

Numbers: ground ≥ 2.5 × the plan span at ~1 vertex per 2 m (the path needs the resolution,
the far field does not); `height_at` within ±1.5 m of 0 inside the bounds; the fog box 40 m
tall over the whole ground; sun angle 2° (soft, readable shadows).  `build_env` returns what
zones share — the ground material, the sun direction, the palette — and zones read it as
`ctx.env`.

## The exact contract the harness wrapper enforces

`src/scene.py` is **written by the harness**: a data file naming the zones, assets, heroes
and the plan's cameras, regenerated by the assembler and overwritten after your stages.  The
wrapper then runs, in one Blender process, each in its own try/except:

```text
ctx.collection = scene root;      out = env.build_env(ctx);  ctx.env = out (a dict) ;  ctx.height_at = env.height_at
for each hero:   import public/assets/<snake>.glb into a Collection          -> ctx.assets[snake]
for each asset:  ctx.collection = its own Collection;  assets.<snake>.build_<snake>(ctx)  -> ctx.assets[snake]
for each zone:   ctx.collection = its own Collection (active);  zones.<snake>.build(ctx)
                 a raise = that zone DROPPED with file:line, everything it made removed; the rest builds
then: the plan's cameras, the asset sources excluded from the view layer, frame 1 set, the census written,
      scene.blend saved, every camera rendered at frames 1 and 46 (t = 0 and t = 1.5 s)
```

So `build_env(ctx)`, `build(ctx)` and `build_<snake>(ctx, variant=0)` are the whole interface:
`build_<zone>(ctx)`, `create()`, `main()` and `register()` are NEVER called, and a module that
raises on import (a bad top-level statement) is gone from the scene.  Link every object into
`ctx.collection`; an object linked into `ctx.scene.collection` instead belongs to no zone and is
graded as the harness's.

## Ground that reads real (blend, paths, edges — never one flat colour)

A single-colour plane is the #1 reason a scene reads as a toy.  Real ground is **three or four
colours blended by height, slope and noise** (the `_ground` recipe above: a `FLOAT_COLOR`
attribute read by an Attribute node), with the path carved into `height_at` itself and dressed
edges where anything meets it.  `lib.terrain.ground(...)` builds exactly that; `lib.terrain.
make_height_at(...)` the relief.  Where a built thing meets the ground, sink it 3–5 cm AND
scatter 6–15 small pieces along the seam:

```python
def seam_dressing(ctx, name, points, count, rng, radius=0.18, color=(0.31, 0.42, 0.17)):
    """Tufts / pebbles hugging a seam polyline [(x, y), ...] as ONE mesh (one placement row, exempt-free)."""
    mat = bpy.data.materials.new(name + "Mat")
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Roughness"].default_value = 1.0
    bm = bmesh.new()
    for i in range(count):
        u = (i + 0.5) / count * (len(points) - 1)
        k = min(len(points) - 2, int(u))
        f = u - k
        x = points[k][0] + (points[k + 1][0] - points[k][0]) * f + rng.uniform(-0.5, 0.5)
        y = points[k][1] + (points[k + 1][1] - points[k][1]) * f + rng.uniform(-0.5, 0.5)
        r = radius * rng.uniform(0.5, 1.4)
        new = bmesh.ops.create_icosphere(bm, subdivisions=1, radius=r)
        bmesh.ops.scale(bm, vec=(1.0, 1.0, 0.6), verts=new["verts"])
        bmesh.ops.translate(bm, vec=(x, y, ctx.height_at(x, y) - r * 0.35), verts=new["verts"])   # sunk a third
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    ctx.collection.objects.link(ob)
    return ob

seam = seam_dressing(ctx, "SeamTufts", [(-6.0, -6.0), (6.0, -6.0), (6.0, 6.0)], 24, rng)
print("seam tufts", len(seam.data.polygons))
```

Numbers: ≥ 3 blended colours, each with ≥ 8 % of the visible area; path 1.2–2.0 m wide; a
ground plane that is one flat colour, or a raised bed that is a sharp box on top of grass, are
both graded as defects (`flat_ground`, `undressed_scene`).  A raised platform gets a chamfered
kerb (`bmesh.ops.bevel` on its top edges, offset 0.03), never a naked box edge.

## Horizon: the world must not end (closing the frame in Cycles)

The single most repeated judge complaint on this track is *"a hard world edge is visible
against the sky"*.  Five rules kill it for good:

1. **Ground reaches past the haze.**  Ground size ≥ 2.5 × the plan span, the fog box over all
   of it; detail only needs to exist inside the bounds.
2. **The fog's colour is the sky's horizon colour.**  Then the ground fades into the sky
   instead of ending at it (the time-of-day table pairs them).
3. **A silhouette ring** at `radius ≈ 0.6 × (1 / fog density)` and ≥ 2 × the bounds
   half-diagonal: 24–40 low-poly hills / treeline / rooftops / mesas, height 0.06–0.12 ×
   radius.  Measured on a 45 m garden: a ring at 46 m reads as distant hills, the same ring
   at 95 m as a crater wall, at 20 m as a stone circle.
4. **Never tint the backdrop toward the fog colour.**  The volume already hazes it by
   distance; a pre-tinted ridge reads as pale cardboard (`placeholder_material`).  Give it
   its natural colour DARKENED (× 0.6–0.8) and let the fog do the haze.
5. **Solid geometry, never flat cut-outs.**  A ring of planes reads as cardboard from every
   oblique and overview camera, and its base floats.

```python
def horizon_ring(ctx, radius, rng, count=30, shape="hill", color=(0.17, 0.24, 0.16), darken=0.72):
    """Build this from build_env (an env object is not a placement row): ONE mesh of low hills / blocks /
    mesas around the content, each base at the ground.  Named Sky... (SkyRidge, SkyHills): a sky-named
    mesh wider than 50 m is BACKDROP to the census, so the overview cameras frame the content, not the ring."""
    mat = bpy.data.materials.new("SkyRidgeMat")
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (color[0] * darken, color[1] * darken, color[2] * darken, 1.0)
    bsdf.inputs["Roughness"].default_value = 1.0
    bm = bmesh.new()
    for i in range(count):
        a = (i + 0.35 * rng.random()) / count * math.tau
        rr = radius * rng.uniform(0.82, 1.12)
        hgt = radius * rng.uniform(0.06, 0.12)
        wid = hgt * rng.uniform(1.1, 2.7)
        x, y = math.cos(a) * rr, math.sin(a) * rr
        if shape == "hill":
            new = bmesh.ops.create_cone(bm, cap_ends=True, segments=7, radius1=wid, radius2=0.0, depth=hgt)
        elif shape == "mesa":
            new = bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=wid, radius2=wid * 0.75, depth=hgt)
        else:
            new = bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, vec=(wid, wid * 0.8, hgt), verts=new["verts"])
        bmesh.ops.rotate(bm, cent=(0.0, 0.0, 0.0), matrix=Matrix.Rotation(rng.uniform(0.0, math.tau), 3, "Z"), verts=new["verts"])
        bmesh.ops.translate(bm, vec=(x, y, ctx.height_at(x, y) + hgt / 2 - 1.5), verts=new["verts"])   # base 1.5 m under the ground
    me = bpy.data.meshes.new("SkyRidge")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new("SkyRidge", me)
    ctx.collection.objects.link(ob)
    return ob

ring = horizon_ring(ctx, 60.0, rng, shape="block", color=(0.17, 0.19, 0.25))
print("sky ridge pieces", len(ring.data.polygons))
```

Interiors (a greenhouse, a cabin) need no ring — but the *outside* seen through the glass must
still be something: the sky world plus 5–10 silhouette shapes beyond the windows.

## Dusk / night lighting recipe (dark scenes are the #1 gate failure)

"Dusk", "night", "moonlit", "lantern-lit" means COLOURED darkness, never black.  The harness
measures every camera's frame; a frame that is too dark is a gate ERROR (`scene_frames:
dark_frame`) capping the score at 0.55.  Exposure is fixed at 0 and you cannot touch it: take
the `dusk` / `night` row of the time-of-day table for the world, the sun (or moon) and the
fog, then add **practicals** — every lantern, window and fire near a camera is an emissive
surface (the lamp you SEE, Emission Strength 5–10, it blooms) plus its own Point light with a
radius (the light that reaches the ground: 30–60 W within ~3 m).  `lib.lighting.practical(...)`
is this in one call; the raw version, as ONE placed thing (post → bulb → light, parented, one
placement row seated on the ground):

```python
def practical(ctx, name, x, y, height=2.3, color=(1.0, 0.70, 0.44), energy=45.0, radius=0.42, rng=None):
    wood = bpy.data.materials.new(name + "Post")
    wood.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.16, 0.12, 0.08, 1.0)
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=0.045, radius2=0.035, depth=height)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, height / 2), verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(wood)
    post = bpy.data.objects.new(name, me)
    post.location = (x, y, ctx.height_at(x, y) - 0.03)          # 3 cm into the ground: the seam reads planted
    ctx.collection.objects.link(post)
    glow = bpy.data.materials.new(name + "Glow")
    bsdf = glow.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Emission Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Emission Strength"].default_value = 8.0
    bme = bpy.data.meshes.new(name + "Bulb")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.09)
    bm.to_mesh(bme)
    bm.free()
    bme.materials.append(glow)
    bulb = bpy.data.objects.new(name + "Bulb", bme)
    bulb.parent = post                                           # a child: the post is the ONE placement row
    bulb.location = (0.0, 0.0, height + 0.05)                    # relative to the post
    ctx.collection.objects.link(bulb)
    lamp = bpy.data.lights.new(name + "Light", "POINT")
    lamp.energy = energy
    lamp.color = color
    lamp.shadow_soft_size = radius                               # a radius: soft, physical falloff
    light = bpy.data.objects.new(name + "Light", lamp)
    light.parent = bulb
    ctx.collection.objects.link(light)
    return post

for i, (x, y) in enumerate([(4.0, 2.0), (-3.0, 5.0)]):
    practical(ctx, f"Lantern_{i}", x, y)
print("practicals", 2)
```

Rules of thumb: the moon is a Sun lamp at 0.05–0.3 W/m², cool (0.66, 0.75, 1.0), elevation
30–50°; the night world is a deep blue Background (never black) at strength 0.05–0.15; ground
albedo ≥ 0.25; every practical near a camera path gets its own light, the far ones share ONE
emissive material and light nothing.  **Exposure self-check (after each build):** `scene_views`
→ the frame table (`camera_checks`) for every authored camera: read `mean_lum` / `dark_frac` /
`blown_frac` / `content_frac`.  If a frame is dark, raise the world strength by +0.05 and the
practicals by +15 W and re-render — never add a second Sun.

## Atmosphere: time-of-day triads with numbers

Pick the row, hand its numbers to the world and the sun, then only tune the practicals.  Cycles
reads physical units and AgX compresses the top: a day scene is a Sun of 3–5 W/m² under a sky
world at strength 0.3–0.5; dusk is a low warm sun at 1–2 with a strong dusty sky; night is a
moon at 0.1 with a blue world at 0.1.  The fog colour is always the sky's **horizon** colour —
never white, never grey unless the brief says overcast.

| time of day | world | strength | sun W/m² | sun colour | sun elev | air density | fog density | fog colour |
|---|---|---|---|---|---|---|---|---|
| dawn | sky texture | 0.25 | 2.0 | (1.00, 0.69, 0.45) | 8° | 1.6 | 0.006 | (0.91, 0.63, 0.42) |
| morning | sky texture | 0.40 | 3.5 | (1.00, 0.95, 0.82) | 35° | 1.0 | 0.004 | (0.74, 0.84, 0.92) |
| noon | sky texture | 0.50 | 5.0 | (1.00, 0.98, 0.94) | 70° | 0.8 | 0.003 | (0.81, 0.89, 0.95) |
| golden | sky texture | 0.30 | 2.5 | (1.00, 0.76, 0.48) | 12° | 1.8 | 0.006 | (0.94, 0.66, 0.38) |
| overcast | flat colour (0.78, 0.81, 0.84) | 0.60 | 1.2 | (0.85, 0.87, 0.90) | 45° | — | 0.008 | (0.76, 0.79, 0.82) |
| dusk | sky texture | 0.20 | 1.2 | (1.00, 0.63, 0.38) | 6° | 2.0 | 0.006 | (0.85, 0.44, 0.23) |
| night (moon) | flat colour (0.05, 0.08, 0.19) | 0.10 | 0.12 | (0.66, 0.75, 1.00) | 40° | — | 0.004 | (0.13, 0.19, 0.35) |

```python
# role: env
TIME_OF_DAY = {
    "dawn":     {"sky": "texture", "world_strength": 0.25, "sun_energy": 2.0, "sun_color": (1.0, 0.69, 0.45), "sun_elevation": 8.0, "air": 1.6, "fog_density": 0.006, "fog_color": (0.91, 0.63, 0.42)},
    "morning":  {"sky": "texture", "world_strength": 0.40, "sun_energy": 3.5, "sun_color": (1.0, 0.95, 0.82), "sun_elevation": 35.0, "air": 1.0, "fog_density": 0.004, "fog_color": (0.74, 0.84, 0.92)},
    "noon":     {"sky": "texture", "world_strength": 0.50, "sun_energy": 5.0, "sun_color": (1.0, 0.98, 0.94), "sun_elevation": 70.0, "air": 0.8, "fog_density": 0.003, "fog_color": (0.81, 0.89, 0.95)},
    "golden":   {"sky": "texture", "world_strength": 0.30, "sun_energy": 2.5, "sun_color": (1.0, 0.76, 0.48), "sun_elevation": 12.0, "air": 1.8, "fog_density": 0.006, "fog_color": (0.94, 0.66, 0.38)},
    "overcast": {"sky": (0.78, 0.81, 0.84), "world_strength": 0.60, "sun_energy": 1.2, "sun_color": (0.85, 0.87, 0.90), "sun_elevation": 45.0, "air": 1.0, "fog_density": 0.008, "fog_color": (0.76, 0.79, 0.82)},
    "dusk":     {"sky": "texture", "world_strength": 0.20, "sun_energy": 1.2, "sun_color": (1.0, 0.63, 0.38), "sun_elevation": 6.0, "air": 2.0, "fog_density": 0.006, "fog_color": (0.85, 0.44, 0.23)},
    "night":    {"sky": (0.05, 0.08, 0.19), "world_strength": 0.10, "sun_energy": 0.12, "sun_color": (0.66, 0.75, 1.0), "sun_elevation": 40.0, "air": 1.0, "fog_density": 0.004, "fog_color": (0.13, 0.19, 0.35)},
}


def apply_time_of_day(ctx, key):
    """The world of the brief's time of day (env.py only: the world is the env's).  A physical sky texture
    by day (MULTIPLE_SCATTERING — 'NISHITA' no longer exists; its knobs are sun_elevation / sun_rotation /
    air_density / ozone_density / altitude), a flat deep colour for overcast / night; the sun disc is off
    because the Sun LAMP is the sun.  Returns the row for the lamp and the fog."""
    t = TIME_OF_DAY.get(key, TIME_OF_DAY["morning"])
    world = bpy.data.worlds.new("Sky")
    ctx.scene.world = world
    nodes, links = world.node_tree.nodes, world.node_tree.links
    bg = nodes["Background"]
    if t["sky"] == "texture":
        sky = nodes.new("ShaderNodeTexSky")
        sky.sky_type = "MULTIPLE_SCATTERING"
        sky.sun_disc = False
        sky.sun_elevation = math.radians(t["sun_elevation"])
        sky.sun_rotation = math.radians(SUN_AZIMUTH_DEG)
        sky.air_density = t["air"]                                # hazier, warmer at a low sun
        links.new(sky.outputs["Color"], bg.inputs["Color"])
    else:
        bg.inputs["Color"].default_value = (t["sky"][0], t["sky"][1], t["sky"][2], 1.0)
    bg.inputs["Strength"].default_value = t["world_strength"]
    return t
```

**Colour temperature contrast is the whole trick**: the key (sun) is warm, the fill (the sky
world) is cool — a sky texture gives you that for free; a flat warm world under a warm sun is
the monochrome-orange soup scenes are marked down for.  Shade must carry sky colour, never
black: with the sun under 25° keep the world strength ≥ 0.2.

## Lighting for Cycles + AgX: sun, sky and area numbers (exposure stays 0)

The harness renders with Cycles at 32 samples + denoise, AgX, exposure 0, and you cannot
change any of it — so the NUMBERS are the lighting.  Sun 1–5 W/m² (table above); the world
0.1–0.6; an Area light for a window or a studio key: 200–800 W at 1–2 m size (`shape =
'DISK'` or `'RECTANGLE'`, aimed with `to_track_quat('-Z', 'Y')`); a Spot 100–400 W, `spot_size`
45–60°, `spot_blend` 0.3; a practical Point 30–60 W with `shadow_soft_size` 0.3–0.5.  Never a
light at 1000 W "to be safe": AgX rolls the highlights off and the frame reads blown
(`blown_frac`).  Soft shadows come from the light's SIZE (`angle` on a Sun, `shadow_soft_size`
on a Point, `size` on an Area), not from a second light.

```python
def area_light(ctx, name, loc, target, color=(1.0, 0.93, 0.80), energy=400.0, size=1.5, shape="DISK"):
    """An Area light aimed at `target` — a window, a studio key, a street lamp's throw."""
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.color = color
    data.size = size
    data.shape = shape
    ob = bpy.data.objects.new(name, data)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    ctx.collection.objects.link(ob)
    return ob

key = area_light(ctx, "WindowKey", (6.0, -4.0, 3.2), (0.0, 0.0, 0.8))
print("area light", key.data.energy)
```

Zones add lights only for practicals and the odd window; the Sun and the world are
`src/env.py`'s (the lint rejects a world built anywhere else).

## Atmosphere volume: the fog box, local mist and light shafts

The env's fog box (`_atmosphere` above, density 0.003–0.008, the horizon colour) is the depth
cue that makes distance read.  A local MIST — over a pond, in a hollow, under a lantern — is a
second, denser, lower box: a volume-only mesh is fog to the census (no placement row), so it
can sit anywhere.  Light shafts are the same box with a higher `Anisotropy` (0.4–0.6) placed
where the sun crosses an opening; never a cone mesh with an emissive material.

```python
def mist(ctx, name, center, size, density=0.02, color=(0.80, 0.84, 0.88), anisotropy=0.0):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bmesh.ops.translate(bm, vec=center, verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    mat = bpy.data.materials.new(name + "Vol")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.remove(nodes["Principled BSDF"])
    vol = nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = density
    vol.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    vol.inputs["Anisotropy"].default_value = anisotropy
    links.new(vol.outputs["Volume"], nodes["Material Output"].inputs["Volume"])
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    ctx.collection.objects.link(ob)
    return ob

pond_mist = mist(ctx, "PondMist", (12.0, -8.0, 0.6), (16.0, 12.0, 1.2), density=0.03)
print("mist", pond_mist.name)
```

## Water

Water is a subdivided plane with a Principled BSDF that TRANSMITS (Transmission Weight 1, IOR
1.33, Roughness 0.05–0.15, a dark teal Base Color) over a bed carved BELOW the water level in
`height_at` (a radial depression — or the shore z-fights and the shallows never read), plus a
noise bump for ripples whose Mapping node scrolls between the two frames (the motion the judge
diffs).  `lib.water.water_surface(...)` and `lib.materials.water(...)` are this in two calls;
`lib.water.pool_height` the bed.  Name it `Water…` (the census classifies it; a foot at or
under the water is never "sunken").

```python
def water_plane(ctx, name, center, radius, level, color=(0.09, 0.25, 0.29), segments=48):
    mat = bpy.data.materials.new(name + "Mat")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Roughness"].default_value = 0.10
    bsdf.inputs["IOR"].default_value = 1.33
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    coords = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    ripple = nodes.new("ShaderNodeTexNoise")
    ripple.inputs["Scale"].default_value = 0.8
    ripple.inputs["Detail"].default_value = 8.0
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    links.new(coords.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], ripple.inputs["Vector"])
    links.new(ripple.outputs["Factor"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    # the ripples travel 0.6 m / 0.2 m between the two sampled times: a still mirror reads as static
    loc = mapping.inputs["Location"]
    loc.default_value = (0.0, 0.0, 0.0)
    loc.keyframe_insert("default_value", frame=ctx.frame(0.0))
    loc.default_value = (0.6, 0.2, 0.0)
    loc.keyframe_insert("default_value", frame=ctx.frame(1.5))
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=True, segments=segments, radius=radius)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    ob.location = (center[0], center[1], level)
    ctx.collection.objects.link(ob)
    return ob

pond = water_plane(ctx, "WaterPond", (12.0, -8.0), 6.0, -0.25)
print("water", pond.name)
```

## Vegetation that reads real (species, jitter, clusters, ground cover)

A ring of identical cones is the second most common complaint.  Fix it with **three to five
species** (`lib.vegetation.tree(...)`: broadleaf / cypress / oak / pine; `shrub`, `fern`,
`tropical`), per-instance **scale / hue / rotation jitter**, **clustered** placement and a
**ground-cover** layer.  Raw: each tree is ONE mesh with two material slots (one placement
row), its hue jitter through `Object Info → Color` so a dozen trees share one material and
each still differs:

```python
def foliage_material(name, base=(0.22, 0.38, 0.14)):
    """Base colour = the OBJECT's colour (ob.color), so one material serves every tree with its own hue."""
    mat = bpy.data.materials.new(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.85
    info = nodes.new("ShaderNodeObjectInfo")
    links.new(info.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def tree_mesh(kind, k, rng):
    """trunk + crown in one bmesh; the crown's faces get material slot 1.  k = scale jitter (0.8-1.5)."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=7, radius1=0.20 * k, radius2=0.10 * k, depth=2.6 * k)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 1.3 * k), verts=bm.verts)
    n_trunk = len(bm.faces)
    if kind == "conifer":
        crown = bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=1.15 * k, radius2=0.0, depth=4.4 * k)
        bmesh.ops.translate(bm, vec=(0.0, 0.0, 4.4 * k), verts=crown["verts"])
    else:                                                        # broadleaf / scrub: a displaced ball, flattened
        r = (1.7 if kind == "broadleaf" else 0.75) * k
        crown = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=r)
        off = Vector((rng.uniform(0.0, 100.0), rng.uniform(0.0, 100.0), 0.0))     # a different crown every tree
        for v in crown["verts"]:
            v.co *= 1.0 + 0.28 * noise.noise(v.co * 1.3 + off)
        bmesh.ops.scale(bm, vec=(1.0, 1.0, 0.8), verts=crown["verts"])
        bmesh.ops.translate(bm, vec=(0.0, 0.0, (3.4 if kind == "broadleaf" else 1.0) * k), verts=crown["verts"])
    bm.faces.ensure_lookup_table()
    for f in bm.faces[n_trunk:]:
        f.material_index = 1
    for f in bm.faces:
        f.smooth = kind != "conifer"
    me = bpy.data.meshes.new("Tree")
    bm.to_mesh(me)
    bm.free()
    return me


def clustered_points(n, area, rng, clusters=0):
    """area = (cx, cy, w, d); K cluster seeds, members scattered around them (Poisson-ish, never a grid)."""
    cx, cy, w, d = area
    k = clusters or max(2, round(n / 7))
    seeds = [(cx + (rng.random() - 0.5) * w * 0.9, cy + (rng.random() - 0.5) * d * 0.9) for _ in range(k)]
    sp = min(w, d) * 0.13
    out = []
    for i in range(n):
        sx, sy = seeds[i % k]
        a, r = rng.random() * math.tau, sp * math.sqrt(rng.random())
        out.append((sx + math.cos(a) * r, sy + math.sin(a) * r))
    return out


def forest(ctx, name, area, mix, bark, leaves, rng, height_at):
    """mix = {kind: count}; one mesh object per tree (ONE placement row each), seated 3 cm into the ground."""
    objs = []
    i = 0
    for kind, count in mix.items():
        for x, y in clustered_points(count, area, rng):
            k = rng.uniform(0.8, 1.5)
            me = tree_mesh(kind, k, rng)
            me.materials.append(bark)
            me.materials.append(leaves)
            ob = bpy.data.objects.new(f"{name}{kind.capitalize()}_{i}", me)
            ob.location = (x, y, height_at(x, y) - 0.03)
            ob.rotation_euler = (rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05), rng.uniform(0.0, math.tau))   # a few degrees of lean, full yaw
            hue = (0.22 + rng.uniform(-0.04, 0.04), 0.38 + rng.uniform(-0.08, 0.08), 0.14 + rng.uniform(-0.05, 0.05))
            ob.color = (hue[0], hue[1], hue[2], 1.0)                                                           # hue + value jitter
            ctx.collection.objects.link(ob)
            objs.append(ob)
            i += 1
    return objs

bark = bpy.data.materials.new("Bark")
bark.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.33, 0.25, 0.17, 1.0)
bark.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.95
trees = forest(ctx, "Grove", (0.0, 0.0, 40.0, 40.0), {"conifer": 5, "broadleaf": 4, "scrub": 6}, bark, foliage_material("Leaves"), rng, ctx.height_at)
print("trees", len(trees), "species", 3)
```

**Ground cover** turns "objects on a plane" into "plants growing out of the ground": a
geometry-nodes cloud of blades over a patch (`lib.vegetation.grass_patch(...)`) — an instance
cloud, counted as instances by the census and exempt from per-asset placement:

```python
def grass_patch(ctx, name, center, size, density, seed, height_at, color=(0.29, 0.42, 0.16)):
    """A surface displaced by height_at + a hidden 3-sided blade instanced on it by geometry nodes."""
    mat = bpy.data.materials.new(name + "Mat")
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=24, y_segments=24, size=size / 2)
    for v in bm.verts:
        v.co.z = height_at(center[0] + v.co.x, center[1] + v.co.y) - 0.02
    bm.to_mesh(me)
    bm.free()
    patch = bpy.data.objects.new(name, me)
    patch.location = (center[0], center[1], 0.0)
    ctx.collection.objects.link(patch)
    bme = bpy.data.meshes.new(name + "Blade")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=3, radius1=0.05, radius2=0.0, depth=0.34)   # 3 faces per blade
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 0.17), verts=bm.verts)
    bm.to_mesh(bme)
    bm.free()
    bme.materials.append(mat)
    blade = bpy.data.objects.new(name + "Blade", bme)
    blade.hide_render = True                                     # the source is never seen, only its instances
    blade.hide_viewport = True
    ctx.collection.objects.link(blade)
    ng = bpy.data.node_groups.new(name + "Scatter", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    dens = ng.interface.new_socket("Density", in_out="INPUT", socket_type="NodeSocketFloat")
    n = ng.nodes
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    dist = n.new("GeometryNodeDistributePointsOnFaces")
    dist.inputs["Seed"].default_value = seed
    scale = n.new("FunctionNodeRandomValue")                     # FLOAT (the default): inputs 0 / 1 are Min / Max
    scale.inputs[0].default_value = 0.6
    scale.inputs[1].default_value = 1.6
    scale.inputs["Seed"].default_value = seed
    inst = n.new("GeometryNodeInstanceOnPoints")
    info = n.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = blade
    join = n.new("GeometryNodeJoinGeometry")
    ng.links.new(gi.outputs["Geometry"], dist.inputs["Mesh"])
    ng.links.new(gi.outputs["Density"], dist.inputs["Density"])
    ng.links.new(dist.outputs["Points"], inst.inputs["Points"])
    ng.links.new(dist.outputs["Rotation"], inst.inputs["Rotation"])
    ng.links.new(scale.outputs["Value"], inst.inputs["Scale"])
    ng.links.new(info.outputs["Geometry"], inst.inputs["Instance"])
    ng.links.new(inst.outputs["Instances"], join.inputs["Geometry"])
    ng.links.new(gi.outputs["Geometry"], join.inputs["Geometry"])
    ng.links.new(join.outputs["Geometry"], go.inputs["Geometry"])
    mod = patch.modifiers.new("Scatter", "NODES")
    mod.node_group = ng
    getattr(mod.properties.inputs, dens.identifier).value = density   # Blender 5.2: inputs are RNA properties, never mod["Socket_2"]
    return patch

cover = grass_patch(ctx, "GroundCover", (0.0, -14.0), 20.0, 6.0, 9, ctx.height_at)
print("ground cover", cover.name)
```

Density per 100 m² of planted zone: 2–5 trees, 8–20 shrubs, 250–600 blades (`density` 2.5–6
per m²), 30–80 litter pieces.  Less and the judge writes *"monotonous / undressed"*.

## Rocks, cliffs and boulders that read organic (never a row of boxes)

Stacked cuboids read as buildings.  A rock is a **noise-displaced icosphere, flattened and
yawed** (`lib.terrain.boulder`, `rock_field`, `talus`); a cliff is a **ridge polyline of rotated,
non-uniformly scaled chunks with jittered strata** (`lib.terrain.cliff`), never an axis-aligned
wall:

```python
def rock_geometry(bm, radius, rough, seed, detail=2):
    """A displaced icosphere into `bm`; returns its verts.  Flatter than tall = weathered."""
    new = bmesh.ops.create_icosphere(bm, subdivisions=detail, radius=radius)
    off = Vector((seed * 13.0, seed * 7.0, 0.0))
    for v in new["verts"]:
        d = 1.0 + rough * noise.noise(v.co * (1.7 / radius) + off)
        v.co = Vector((v.co.x * d, v.co.y * d, v.co.z * d * 0.8))
    return new["verts"]


def boulder(ctx, name, x, y, radius, rng, color=(0.42, 0.40, 0.37)):
    mat = bpy.data.materials.new(name + "Mat")
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Roughness"].default_value = 0.95
    bm = bmesh.new()
    verts = rock_geometry(bm, radius, 0.34, rng.randrange(1000), detail=2)
    bmesh.ops.scale(bm, vec=(rng.uniform(1.0, 1.4), rng.uniform(0.8, 1.1), 1.0), verts=verts)     # non-uniform
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    ob.location = (x, y, ctx.height_at(x, y) + radius * 0.45)       # a rock sits partly IN the ground (allowed)
    ob.rotation_euler = (0.0, 0.0, rng.uniform(0.0, math.tau))
    ctx.collection.objects.link(ob)
    return ob


def cliff_ridge(ctx, name, ridge, count, height, rng, strata=((0.55, 0.29, 0.18), (0.66, 0.40, 0.24), (0.75, 0.54, 0.35), (0.48, 0.23, 0.15))):
    """Chunks straddling a polyline [(x, y), ...], stacked in strata bands, as ONE mesh with a per-vertex
    'Strata' colour (an Attribute node reads it)."""
    mat = bpy.data.materials.new(name + "Mat")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.95
    attr = nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "Strata"
    links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    bm = bmesh.new()
    bands = []
    for i in range(count):
        u = i / (count - 1) * (len(ridge) - 1)
        k = min(len(ridge) - 2, int(u))
        f = u - k
        bx = ridge[k][0] + (ridge[k + 1][0] - ridge[k][0]) * f + rng.uniform(-1.7, 1.7)
        by = ridge[k][1] + (ridge[k + 1][1] - ridge[k][1]) * f + rng.uniform(-1.7, 1.7)
        layer = i % len(strata)
        lift = height * (0.18 + 0.26 * layer) * rng.uniform(0.75, 1.25)
        verts = rock_geometry(bm, 1.0, 0.42, rng.randrange(1000), detail=1)
        bmesh.ops.scale(bm, vec=(rng.uniform(2.6, 6.0), rng.uniform(2.2, 5.2), lift * rng.uniform(0.7, 1.3)), verts=verts)
        bmesh.ops.rotate(bm, cent=(0.0, 0.0, 0.0), matrix=Euler((rng.uniform(-0.25, 0.25), rng.uniform(0.0, math.tau), rng.uniform(-0.25, 0.25))).to_matrix(), verts=verts)
        bmesh.ops.translate(bm, vec=(bx, by, ctx.height_at(bx, by) + lift * 0.55), verts=verts)
        bands.append((len(verts), strata[layer]))
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    colour = me.color_attributes.new("Strata", "FLOAT_COLOR", "POINT")
    j = 0
    for n, (r, g, b) in bands:                                   # verts were appended band by band, in order
        for _ in range(n):
            colour.data[j].color = (r, g, b, 1.0)
            j += 1
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    ctx.collection.objects.link(ob)
    return ob

rocks = [boulder(ctx, f"Boulder_{i}", -8.0 + i * 2.2, 12.0 + (i % 2) * 1.5, rng.uniform(0.4, 1.3), rng) for i in range(4)]
cliff = cliff_ridge(ctx, "CliffRidge", [(-30.0, 14.0), (-12.0, 10.0), (2.0, 6.0), (18.0, 9.0), (30.0, 16.0)], 26, 9.0, rng)
print("boulders", len(rocks), "cliff verts", len(cliff.data.vertices))
```

Rules: chunk yaw random over the full circle; scale non-uniform (x ≠ y ≠ z by ≥ 20 %);
strata luminance differing by ≥ 0.08; the ridge polyline bending ≥ 15° at least twice; floor
boulders reuse `rock_geometry` at 0.3–1.6 m and may sit a third of their height IN the ground
(the gate allows rocks, posts and bushes partly buried).

## Set dressing: how many props a place needs

Judges call a scene "sparse", "token" or "undressed" long before they call it wrong.  Counts
that read as *lived in*, per zone (a zone is typically 10 × 10 m to 30 × 30 m):

| scene type | hero assets | mid props (0.3–1.5 m) | small props (< 0.3 m) | ground cover / litter |
|---|---|---|---|---|
| garden / park | 3–5 (lantern, bridge, bench) | 12–25 (rocks, planters, pots, stumps, buckets) | 25–60 (pebbles, leaves, tools, cups, twigs) | 300–800 blades |
| desert / canyon | 2–4 (hero boulder, arch, wreck) | 15–30 boulders in 3 size bands | 30–70 (pebbles, bones, dry brush) | 60–200 scrub clumps |
| harbour / street | 3–6 (boat, crane, cart) | 15–30 (crates, barrels, bollards, nets, lamps) | 30–80 (ropes, buckets, bottles, fish boxes) | 100–300 weeds / litter |
| interior | 3–6 (bench rows, a stove) | 15–30 (pots, tools, crates, stools) | 40–100 (labels, jars, gloves, cups) | n/a |

Cheap rules: 3 size bands per prop family; props go **against something** (a wall base, a
path edge, under a bench) — props alone in open ground read as scatter; never a grid or a
ring (≥ 20 % positional jitter, full yaw jitter); repeats are collection instances of an
asset, each with its own unique name (`lib.scatter.scatter(...)` does the names, the seating
and the jitter):

```python
def place_asset(ctx, source, name, x, y, rot_z, height_at, scale=1.0):
    """ONE collection-instance Empty of an asset Collection, seated: one placement row, PascalCase unique name."""
    ob = bpy.data.objects.new(name, None)
    ob.instance_type = "COLLECTION"
    ob.instance_collection = source
    ob.location = (x, y, height_at(x, y))
    ob.rotation_euler = (0.0, 0.0, rot_z)
    ob.scale = (scale, scale, scale)
    ctx.collection.objects.link(ob)
    return ob


def along_seam(points, n, rng, offset=0.6):
    """n (x, y, yaw) spots hugging a seam polyline, on its +side, jittered: props AGAINST a wall or a path edge."""
    out = []
    for i in range(n):
        u = (i + 0.5 + rng.uniform(-0.3, 0.3)) / n * (len(points) - 1)
        k = min(len(points) - 2, max(0, int(u)))
        f = u - k
        (ax, ay), (bx, by) = points[k], points[k + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length                        # the seam's left normal
        d = offset * rng.uniform(0.7, 1.4)
        out.append((ax + dx * f + nx * d, ay + dy * f + ny * d, math.atan2(dy, dx) + rng.uniform(-0.4, 0.4)))
    return out

source = ctx.assets.get("bollard")                               # a planned asset: ctx.assets['<snake>']
if source is None:                                               # (no such plan asset: a stand-in crate)
    cm = bpy.data.meshes.new("CrateStandIn")
    cb = bmesh.new()
    bmesh.ops.create_cube(cb, size=0.6)
    bmesh.ops.translate(cb, vec=(0.0, 0.0, 0.3), verts=cb.verts)
    cb.to_mesh(cm)
    cb.free()
    source = bpy.data.collections.new("CrateStandIn")
    ctx.collection.children.link(source)
    source.objects.link(bpy.data.objects.new("CrateStandIn", cm))
spots = along_seam([(-10.0, -4.0), (0.0, -4.0), (8.0, 2.0)], 9, rng)
props = [place_asset(ctx, source, f"QuayProp_{i}", x, y, yaw, ctx.height_at, scale=rng.uniform(0.85, 1.15)) for i, (x, y, yaw) in enumerate(spots)]
print("props against the seam", len(props))
```

## Scene layering: foreground, midground, background

A "diorama on an empty plane" scores ~0.5 whatever else you do.  Every authored camera needs
**three depth layers**, built on purpose:

| layer | distance from camera | what lives there | how much of the frame |
|---|---|---|---|
| foreground frame | 1.5–5 m | a branch, a post, a parapet edge, a leaning tool, tall grass, an arch | 10–25% of the frame area, usually at an edge, may be partly cut off |
| midground subject | 8–30 m | the hero assets: what the brief is about | fills the centre third |
| background | 40 m–the haze | the backdrop ring, a distant treeline / skyline / mesas, the sky | the top third |

```python
CAMERAS = [((24.0, -26.0, 9.0), (0.0, 0.0, 2.0)), ((8.0, -22.0, 1.7), (0.0, 1.0, 1.4))]   # the plan's, in this frame


def frame_foreground(ctx, cam, obj, dist=3.0, side=-1.0, lift=0.0):
    """Place `obj` just off the camera's axis at `dist` so it brackets the shot (a hand-placed framer)."""
    eye, at = Vector(cam[0]), Vector(cam[1])
    fwd = (at - eye)
    fwd.z = 0.0
    fwd.normalize()
    right = fwd.cross(Vector((0.0, 0.0, 1.0)))
    p = eye + fwd * dist + right * (side * dist * 0.75)
    obj.location = (p.x, p.y, ctx.height_at(p.x, p.y) + lift)
    obj.rotation_euler = (0.0, 0.0, math.atan2(eye.y - p.y, eye.x - p.x))
    return obj


def camera_mask(cameras, radius=4.0):
    """(x, y) -> True inside a clearance disc around any camera, or on its first quarter towards the subject:
    SKIP scattered instances there (scatter is blind; a tree 1 m in front of the lens ruins the shot)."""
    discs = [(c[0][0], c[0][1], radius) for c in cameras]
    for (px, py, _), (lx, ly, _) in cameras:
        discs.append((px + (lx - px) * 0.22, py + (ly - py) * 0.22, radius * 0.6))
    return lambda x, y: any((x - cx) ** 2 + (y - cy) ** 2 < r * r for cx, cy, r in discs)

fm = bpy.data.meshes.new("Framer")
fb = bmesh.new()
bmesh.ops.create_cone(fb, cap_ends=True, segments=8, radius1=0.08, radius2=0.05, depth=2.4)
bmesh.ops.translate(fb, vec=(0.0, 0.0, 1.2), verts=fb.verts)
fb.to_mesh(fm)
fb.free()
framer = bpy.data.objects.new("FramerPost", fm)
ctx.collection.objects.link(framer)
frame_foreground(ctx, CAMERAS[1], framer, side=-1.0)
blocked = camera_mask(CAMERAS)
print("framer at", tuple(round(v, 2) for v in framer.location), "mask blocks (8,-22)?", blocked(8.0, -22.0), "(0,0)?", blocked(0.0, 0.0))
```

**Keep the cameras' first metres clear**, and the establishing camera sits *above the
canopy* looking DOWN at the subject.  Atmospheric perspective does the rest: with the fog the
horizon colour, a background object loses contrast and saturation with distance for free —
darken the distant material, never tint it.

## Motion you can SEE between t = 0 and t = 1.5 s

The harness renders every camera at t = 0 and t = 1.5 s (frames 1 and 46 at 30 fps) and the
judge diffs the two frames.  Sub-centimetre sway is invisible at 1024 × 576 and scores as
`nothing_moves`.  Animation is DATA: keyframes on `ctx.frame(0.0)` and `ctx.frame(1.5)`, or a
SIMPLE driver of `frame` (`lib.motion.keyframe_path`, `drift`, `sway`, `spin`, `scroll`,
`shape_key_sweep` are these in one call).  Amplitudes that read:

| what moves | amplitude | note |
|---|---|---|
| foliage sway | ±0.10–0.20 rad (6–11°) | rotate the crown or the whole tree about its base, not the trunk's middle |
| grass / reeds | ±0.15 rad | phase-offset per patch |
| water | ripples scrolling ≥ 0.15 m/s (the Mapping keyframes above) and a surface bob ≥ 0.04 m | a still mirror reads as static |
| falling leaves / petals / snow / dust | ≥ 0.6 m of travel in 1.5 s | a small instance cloud, each with its own keyframes |
| flags / banners / string lights | ±0.12 rad | the classic "the scene is alive" cue |
| flame / lantern flicker | Emission Strength × 0.75…× 1.3 | keyframe the socket's `default_value` |
| vehicles / boats / birds / a mill | ≥ 1.5 m of travel, or a visible turn | one hero mover beats ten twitchers |

```python
def keyframe_linear(ctx, obj, path, index, v0, v1):
    """Two keys on the harness's two times, LINEAR (an eased curve barely moves by t = 1.5).  Blender 5.2:
    the curves live in the action's channelbag — Action.fcurves is gone."""
    setattr(obj, path, _set_index(getattr(obj, path), index, v0))
    obj.keyframe_insert(path, index=index, frame=ctx.frame(0.0))
    setattr(obj, path, _set_index(getattr(obj, path), index, v1))
    obj.keyframe_insert(path, index=index, frame=ctx.frame(1.5))
    act = obj.animation_data.action
    for fc in act.layers[0].strips[0].channelbag(act.slots[0]).fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"


def _set_index(vec, index, value):
    out = list(vec)
    out[index] = value
    return out

hull_mat = bpy.data.materials.new("HullMat")
hull_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.55, 0.12, 0.10, 1.0)
hm = bpy.data.meshes.new("Boat")
hb = bmesh.new()
bmesh.ops.create_cube(hb, size=1.0)
bmesh.ops.scale(hb, vec=(2.0, 6.0, 1.2), verts=hb.verts)
bmesh.ops.translate(hb, vec=(0.0, 0.0, 0.6), verts=hb.verts)
hb.to_mesh(hm)
hb.free()
hm.materials.append(hull_mat)
boat = bpy.data.objects.new("Boat_0", hm)
boat.location = (14.0, -6.0, -0.35)                                          # its waterline on the water (z = -0.25)
ctx.collection.objects.link(boat)
keyframe_linear(ctx, boat, "location", 1, -6.0, -3.0)                        # drifts 3 m along +Y in 1.5 s: the hero motion
keyframe_linear(ctx, boat, "rotation_euler", 0, -0.04, 0.04)                 # and rolls 4.6 degrees
pivot = bpy.data.objects.new("MillPivot", None)                              # a spinning thing: a SIMPLE driver on frame
pivot.location = (-6.0, 8.0, ctx.height_at(-6.0, 8.0) + 3.0)
pivot["placement"] = "free"                                                  # an Empty up in the air is exempt BY DESIGN
ctx.collection.objects.link(pivot)
drv = pivot.driver_add("rotation_euler", 1).driver
drv.type = "SCRIPTED"
drv.expression = "frame * 0.12"                                              # 5.4 rad over the 46 frames; inside the simple subset
print("boat keys", boat.animation_data.action is not None, "driver simple", drv.is_simple_expression)
```

Give the scene **one unmistakable hero motion** (a drifting boat, turning sails, falling
leaves, a flock) plus the ambient ones.  A driver that needs Python (`noise.*`, `**`, `%`,
attribute access, `use_self`) evaluates to 0 in this build — the lint and the census both
reject it.  Self-check: after `scene_views` compare the two sheets; if you cannot point at
what changed, double the amplitude.

## Soft particles: steam, smoke, dust, motes and sparks (never a hard-edged quad)

The judge's `hard_edged_particles` defect — and the brief's "a soft particle effect … never
hard-edged quads" must-item, which caps the whole score — fired on what 8 of 30 argon Blender
scenes built on 2026-10-15: ico-sphere "puffs" wearing a translucent Principled BSDF, metaballs,
stacked tori, flat discs and polygon "sparks".  A SURFACE shader always has a silhouette.  In
Cycles a soft thing is a VOLUME: `lib.materials.volume` on a cloud of subdivision-3 spheres renders
with no edge at all (measured on Blender 5.2.2: 40 puffs add 0.7 s to a 32-spp GPU frame).

```python
from lib.materials import emissive, volume
from lib.mesh import Batch
from lib.motion import drift

# 1. steam / smoke / dust: ONE Batch of overlapping volume spheres = one placement row, tagged free
steam = volume("KettleSteam", "ECE9E4", density=0.3, noise_scale=4.0)          # dust: density .15-.25; smoke: .5-.8 and a darker colour
rng = ctx.rng(31)
plume = Batch("KettleSteam")
for i in range(22):
    r = 0.14 + 0.03 * i + rng.uniform(-0.03, 0.03)                               # widens as it rises
    spread = 0.06 * (1.0 + 0.3 * i)
    plume.sphere(steam, (rng.uniform(-spread, spread), rng.uniform(-spread, spread), 0.10 + 0.11 * i), (r, r, 0.8 * r), subdivision=3)
steam_root = plume.finish(ctx.collection)[0]                                    # [0] is the batch's root Empty
steam_root.location = (4.0, -2.0, ctx.height_at(4.0, -2.0) + 0.9)              # the spout / vent / chimney top
steam_root["placement"] = "free"
drift(steam_root, (0.0, 0.0, 0.7), ctx)                                        # rises 0.7 m in 1.5 s: motion the frames show

# 2. motes / sparks / embers: tiny EMISSIVE spheres (<= 1.5 cm): at that size subdivision 2 shows no facet
spark = emissive("Spark", "FFB347", 30.0)
cloud = Batch("ForgeSparks")
for i in range(90):
    cloud.sphere(spark, (rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(0.0, 1.6)), rng.uniform(0.006, 0.014), subdivision=2)
spark_root = cloud.finish(ctx.collection)[0]
spark_root.location = (-3.0, 1.0, ctx.height_at(-3.0, 1.0) + 0.6)
spark_root["placement"] = "free"
drift(spark_root, (0.2, 0.0, 0.9), ctx)                                        # sparks climb; dust motes drift +/-0.3 m sideways instead
print("particles", steam_root.name, spark_root.name)
```

Rules: a particle cloud is ONE object (a `Batch`), tagged `placement = "free"`, and it MOVES
(`drift` / `sway`, >= 0.6 m in 1.5 s so the judge sees it); density 0.15-0.35 for steam and dust,
0.5-0.8 for smoke; no disc, plane, cone, torus, metaball or quad is ever a particle; a volume is
lit like everything else, so under a dim world give it `emission=0.3` or it comes out grey.

## Placement self-check (`lib.place.seat`, `clear_of`)

Placement is MEASURED (`check_placement`; the `scene_placement` gate runs on every build): each
root object in your zone collection is an asset; from its lowest evaluated vertices the harness
looks down for the nearest surface and up for one passing through it.  A gap under the foot →
`floating` (ERROR at 0.08 m or past 2 floaters); a foot buried → `sunken` (ERROR at 0.20 m and a
fifth of the height; rocks, posts and bushes may sit partly in; stairs and ramps may follow the
slope; a foot at/under water is fine); bbox touching nothing → `unsupported`; two boxes sharing
more than 10% of the smaller → `interpenetration` (ERROR at 35%).  `lib.place.seat(obj,
ctx.height_at)` samples the same foot columns the gate does; `lib.place.clear_of(objs)` is the
pair check.

**Scatter clear of what you placed.**  9 of the 13 argon Blender zones that shipped an
interpenetration ERROR in iteration 0 (2026-10-15) had scattered small props INTO a cart, a rack,
a shrub or a salt mound (100 % of the smaller box).  Place the big things first, then mask their
footprints out of every scatter, then run `clear_of` on roots AND scattered Empties and delete
what it still names:

```python
from lib.materials import material
from lib.mesh import Batch
from lib.place import bbox_world, clear_mask, clear_of
from lib.scatter import poisson, scatter

cart = Batch("HandCart")                                                     # the big thing: placed by hand FIRST
cart.box(material("CartMat", "6B4A2B"), (0.0, 0.0, 0.5), (1.6, 2.4, 1.0))
cart_root = cart.finish(ctx.collection)[0]
cart_root.location = (2.0, 2.0, ctx.height_at(2.0, 2.0))
placed_roots = [cart_root]                                                   # every hand-placed root of this zone
rng = ctx.rng(11)
mask = clear_mask(placed_roots, margin=0.3)
pts = poisson(rng, area=(-1.0, -1.0, 5.0, 5.0), min_dist=0.9, max_count=40, mask=mask)
bollards = scatter(ctx.collection, ctx.assets["bollard"], "YardBollard", pts, height_at=ctx.height_at, rng=rng)
for a, b, frac in clear_of([*placed_roots, *bollards], overlap=0.10):       # (name, name, fraction) rows
    smaller = min((bpy.data.objects[a], bpy.data.objects[b]), key=lambda o: (lambda lo, hi: (hi - lo).length)(*bbox_world(o)))
    bpy.data.objects.remove(smaller)                                         # a prop you cannot move 0.3 m clear is a prop you do not need
print("scattered", len(bollards), "clear of", cart_root.name)
```

Raw, for a thing you built yourself:

```python
def foot_and_box(obj):
    """World-space (lowest z, bbox min, bbox max) of the EVALUATED object (modifiers and parents included)."""
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    corners = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return lo.z, lo, hi


def seat_on_ground(obj, height_at, sink=0.0):
    """Lower / lift the object so its foot meets the ground under its centre (gap <= 0.02 m after)."""
    foot, lo, hi = foot_and_box(obj)
    cx, cy = (lo.x + hi.x) / 2, (lo.y + hi.y) / 2
    obj.location.z += height_at(cx, cy) - sink - foot
    return obj.location.z


def overlapping(objs, frac=0.20):
    """Pairs whose boxes share more than `frac` of the smaller box's volume (the gate's interpenetration)."""
    boxes = [(o, *foot_and_box(o)[1:]) for o in objs]
    out = []
    for i, (a, alo, ahi) in enumerate(boxes):
        for b, blo, bhi in boxes[i + 1:]:
            inter = 1.0
            for k in range(3):
                inter *= max(0.0, min(ahi[k], bhi[k]) - max(alo[k], blo[k]))
            small = min((ahi - alo).x * (ahi - alo).y * (ahi - alo).z, (bhi - blo).x * (bhi - blo).y * (bhi - blo).z)
            if small > 0 and inter / small > frac:
                out.append((a.name, b.name))
    return out

crate_mat = bpy.data.materials.new("CrateMat")
crate_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.5, 0.36, 0.2, 1.0)
crates = []
for i in range(3):
    cm = bpy.data.meshes.new(f"Crate_{i}")
    cb = bmesh.new()
    bmesh.ops.create_cube(cb, size=0.8)
    cb.to_mesh(cm)
    cb.free()
    cm.materials.append(crate_mat)
    ob = bpy.data.objects.new(f"Crate_{i}", cm)
    ob.location = (2.0 + i * 0.95, 3.0, 5.0)                                 # dropped from the air on purpose
    ctx.collection.objects.link(ob)
    seat_on_ground(ob, ctx.height_at)                                        # ...and seated
    crates.append(ob)
print("seated", [round(foot_and_box(c)[0] - ctx.height_at(c.location.x, c.location.y), 3) for c in crates], "overlaps", overlapping(crates))
```

A thing MEANT to hang in the air (a bird, a lantern on its chain, a kite) gets
`obj["placement"] = "free"` — the gate reports the share of tagged things, so it is never the
way around a seating problem.  Instance clouds (geometry nodes) are not checked per asset.

## Instancing and scatter (collection instances vs geometry-nodes clouds)

Two ways to repeat, with different gate rows:

* **A collection instance** (`lib.scatter.instance` / `scatter`): an Empty with
  `instance_type = 'COLLECTION'` pointing at `ctx.assets['<snake>']` (or a Collection you built
  once).  ONE placement row per instance — seated, named, measured against the plan's size.
  Use it for anything the judge will look at: props, trees near a camera, a bench.
* **A geometry-nodes cloud** (`lib.scatter.gn_scatter`, `lib.vegetation.grass_patch`): points
  distributed on a surface, an object instanced on them.  Counted as instances by the census,
  exempt from per-asset placement, one object.  Use it for the hundreds: blades, pebbles,
  litter, a far treeline.

```python
source = ctx.assets.get("bollard")
if source is None:                                               # (no such plan asset: a stand-in post)
    pm = bpy.data.meshes.new("PostStandIn")
    pb = bmesh.new()
    bmesh.ops.create_cone(pb, cap_ends=True, segments=8, radius1=0.08, radius2=0.08, depth=0.9)
    bmesh.ops.translate(pb, vec=(0.0, 0.0, 0.45), verts=pb.verts)
    pb.to_mesh(pm)
    pb.free()
    source = bpy.data.collections.new("PostStandIn")
    ctx.collection.children.link(source)
    source.objects.link(bpy.data.objects.new("PostStandIn", pm))
instances = []
for i in range(6):                                               # a row of posts along the quay edge, jittered
    x, y = -12.0 + i * 3.0 + rng.uniform(-0.2, 0.2), -9.0 + rng.uniform(-0.15, 0.15)
    e = bpy.data.objects.new(f"QuayPost_{i}", None)
    e.instance_type = "COLLECTION"
    e.instance_collection = source
    e.location = (x, y, ctx.height_at(x, y))
    e.rotation_euler = (0.0, 0.0, rng.uniform(0.0, math.tau))
    ctx.collection.objects.link(e)
    instances.append(e)
print("instances", len(instances), "of", source.name)
```

A Collection you build for instancing (not a plan asset) lives inside your zone collection;
its source objects at the origin are seen unless you exclude it — simplest is to instance the
plan's assets (`ctx.assets`) and build one-offs as plain objects.

## Materials: Principled recipes and the lib palette

Every surface is a Principled BSDF with a COLOUR and a roughness; a grey default is graded
`placeholder_material`.  Blender 5.2 socket names: `Base Color`, `Roughness`, `Metallic`, `IOR`,
`Alpha`, `Normal`, `Subsurface Weight`, `Specular IOR Level`, `Transmission Weight`, `Coat
Weight`, `Sheen Weight`, `Emission Color`, `Emission Strength` (the old `Specular`,
`Subsurface`, `Transmission`, `Emission` raise KeyError).  Colour mixing is `ShaderNodeMix`
with `data_type = 'RGBA'` (inputs 6 and 7, output 2) — `ShaderNodeMixRGB` is gone.  The lib
palette (`lib.materials`: `material`, `masonry`, `limestone`, `ashlar`, `woodgrain`,
`chipped_paint`, `worn_alloy`, `mineral_surface`, `water`, `glass`, `foliage`, `snow`, `ice`,
`emissive`, `tint`) builds these; the raw recipes:

```python
def material(name, color, roughness=0.5, metal=0.0, noise_scale=0.0, bump=0.08, emission=0.0):
    """One Principled material; noise_scale > 0 adds a procedural bump so flat surfaces still catch light."""
    mat = bpy.data.materials.new(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metal
    if emission:
        bsdf.inputs["Emission Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    if noise_scale:
        tex = nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = noise_scale
        tex.inputs["Detail"].default_value = 8.0
        b = nodes.new("ShaderNodeBump")
        b.inputs["Strength"].default_value = bump
        links.new(tex.outputs["Factor"], b.inputs["Height"])
        links.new(b.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def woodgrain(mat, dark=(0.16, 0.10, 0.06), light=(0.50, 0.35, 0.22), scale=3.0):
    """Wave texture → Mix (RGBA) between two tones → Base Color; along the object's Y."""
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    coords = nodes.new("ShaderNodeTexCoord")
    wave = nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "Y"
    wave.inputs["Scale"].default_value = scale
    wave.inputs["Distortion"].default_value = 4.0
    wave.inputs["Detail"].default_value = 3.0
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs[6].default_value = (dark[0], dark[1], dark[2], 1.0)
    mix.inputs[7].default_value = (light[0], light[1], light[2], 1.0)
    links.new(coords.outputs["Object"], wave.inputs["Vector"])
    links.new(wave.outputs["Factor"], mix.inputs["Factor"])
    links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.65
    return mat


def glass(name, color=(0.9, 0.95, 1.0), roughness=0.05, ior=1.5):
    mat = material(name, color, roughness=roughness)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bsdf.inputs["IOR"].default_value = ior
    return mat

stone = material("Granite", (0.43, 0.42, 0.39), roughness=0.9, noise_scale=12.0, bump=0.15)
oak = woodgrain(material("Oak", (0.5, 0.35, 0.22)))
pane = glass("Pane")
brass = material("Brass", (0.80, 0.62, 0.30), roughness=0.35, metal=1.0)
lamp_glow = material("Glow", (1.0, 0.72, 0.45), emission=6.0)
sm = bpy.data.meshes.new("MaterialSampler")
sb = bmesh.new()
bmesh.ops.create_cube(sb, size=0.5)
bmesh.ops.translate(sb, vec=(0.0, 0.0, 0.25), verts=sb.verts)
sb.to_mesh(sm)
sb.free()
for m in (stone, oak, pane, brass, lamp_glow):
    sm.materials.append(m)
sampler = bpy.data.objects.new("MaterialSampler", sm)
sampler.location = (-4.0, -4.0, ctx.height_at(-4.0, -4.0))
ctx.collection.objects.link(sampler)
print("materials", [m.name for m in sm.materials])
```

Albedos belong in 0.02–0.8 linear (pure black and pure white are always wrong); wood and
stone roughness 0.6–0.95, metals `Metallic` 1 with roughness 0.2–0.5, glass transmission 1;
an emissive at strength 5–10 blooms, 50 whites the frame out.

## Assets (`src/assets/<snake>.py`): `build_<snake>` returning a Collection at the origin, base z = 0

An asset module builds EVERY part into `ctx.collection` (its own, pre-made) at the origin,
base on z = 0, front facing −Y, and returns `ctx.collection`; the harness builds it once,
hides the source and the zones place collection instances of it.  Several parts with distinct
materials, unique names, under the triangle budget (15000 per asset); `variant` (0..n) changes
the SILHOUETTE, not just the scale.  A hero (`blender_glb`) is built by the object track instead
and imported for you.  The shape, inline (in a real asset file `build_lantern` is the module's
top-level function):

```python
def _mat(name, color, roughness=0.5, metal=0.0, emission=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metal
    if emission:
        bsdf.inputs["Emission Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def build_lantern(ctx, variant=0):
    iron = _mat("LanternIron", (0.12, 0.12, 0.13), roughness=0.5, metal=0.8)
    glow = _mat("LanternGlow", (1.0, 0.78, 0.45), emission=6.0)
    post_h = 2.2 if variant == 0 else 1.6
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=0.04, radius2=0.03, depth=post_h)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, post_h / 2), verts=bm.verts)
    cage = bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=0.14, radius2=0.12, depth=0.26)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, post_h + 0.13 - 0.004), verts=cage["verts"])      # overlaps the post by 4 mm
    me = bpy.data.meshes.new("LanternBody")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(iron)
    body = bpy.data.objects.new("LanternBody", me)
    ctx.collection.objects.link(body)
    gm = bpy.data.meshes.new("LanternGlass")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.08)
    bm.to_mesh(gm)
    bm.free()
    gm.materials.append(glow)
    bulb = bpy.data.objects.new("LanternGlass", gm)
    bulb.parent = body
    bulb.location = (0.0, 0.0, post_h + 0.13)
    ctx.collection.objects.link(bulb)
    return ctx.collection

built = build_lantern(ctx, variant=1)
print("asset parts", [o.name for o in built.objects if o.name.startswith("Lantern")])
```

Why: a scene mixes cheap instanced props with a few hero assets; the harness hands every
asset to the zones the same way (`ctx.assets['<snake>']`), so a zone places a procedural
module and an imported GLB with the same four lines.

## Interiors: the room shell is the env's

A brief set INSIDE a built space (`interior: true` in the plan) gets its floor, walls and
ceiling from `src/env.py` — `lib.architecture.room_shell(collection, mat, center=…,
extents=…, thickness=0.2, openings=(…))` on the bounds' faces, named `RoomShell…` /
`Ceiling…` so the overview rig can lift the lid — with the windows and doors cut as openings
and the inside LIT (an Area light per window, practicals; a closed room under a sun reads
black).  Zones dress the inside and never build walls; a fixture on a wall sits flush against
the inner face of the bounds.  Measured 2026-09-07 over six interior runs: with no owner for
walls and roof, every one was judged "not enclosed, a diorama on a flat plane" (0.0–0.3).

## Traps (Blender 5.2 LTS API facts; each is a build error or a silent zero)

1. **`use_nodes` is deprecated** — materials, worlds and lights always have a node tree; a
   new material already carries `Principled BSDF` and `Material Output`.
2. **`sky_type = 'NISHITA'` raises** — it is `'MULTIPLE_SCATTERING'`.
3. **Principled sockets renamed** — `Specular IOR Level`, `Subsurface Weight`, `Transmission
   Weight`, `Emission Color` + `Emission Strength`, `Coat Weight`, `Sheen Weight`.
4. **`ShaderNodeMixRGB` is gone** — `ShaderNodeMix` with `data_type = 'RGBA'`: inputs 6 / 7,
   output 2.
5. **Boolean solvers are `FLOAT` / `EXACT` / `MANIFOLD`** — `'FAST'` raises.
6. **Geometry-nodes inputs are RNA properties** — `getattr(mod.properties.inputs,
   "Socket_2").value = v`; `mod["Socket_2"] = v` raises TypeError.
7. **`Action.fcurves` is gone** — `action.layers[0].strips[0].channelbag(action.slots[0]).
   fcurves` for the interpolation.
8. **`Mesh.calc_normals()` and `use_auto_smooth` are gone** — `for f in bm.faces: f.smooth =
   True` or `me.shade_smooth()`.
9. **No `bpy.ops` in loops** — every operator rebuilds the scene state; build with `bmesh` and
   `bpy.data`, place collection instances, scatter with geometry nodes.
10. **`bpy.context.object` / `selected_objects` after `bpy.data.objects.new`** — nothing is
    selected in background mode; keep your own references.
11. **A driver that needs Python evaluates to 0** — only `frame`, `sin / cos / min / max /
    clamp / lerp`, `+ - * /` and comparisons; no `noise`, `**`, `%`, attribute access,
    `use_self`.
12. **A Python world outside `env.py`**, a camera, `frame_set`, `scene.render.*` — lint ERRORS
    that fail the build gate; the harness owns them.
13. **Names are global** — `Lantern_0` twice becomes `Lantern_0.001`; number every placed
    copy per zone family and never reuse another zone's base name.
14. **`bm.verts[i]` before `ensure_lookup_table()`** — IndexError; call it after any topology
    change, or iterate instead of indexing.
15. **Triangle budgets** — 15000 per asset instance, 40000 for a hero, 3,000,000 unique
    triangles for the whole scene (a gate ERROR past it); instance clouds count their
    instances, so a 200-blade cloud over 400 points is 240,000 rendered triangles but 600
    unique ones.
16. **`matrix_world` of a fresh `bpy.data` object is identity** until the view layer updates —
    parent with a RELATIVE `location` (the practical recipe), or call
    `bpy.context.view_layer.update()` before reading it.

## Self-check (before you call it done)

Composition self-check on every authored frame (the judge grades exactly these):

* three depth layers — something inside 5 m, the subject at 8–30 m, a backdrop and sky above;
* no hard world edge anywhere against the sky; the fog the horizon's colour;
* the ground shows ≥ 3 blended colours and at least one path / worn area;
* vegetation shows ≥ 3 silhouettes and visible hue / scale variation, in clusters not a ring;
* ≥ 12 mid props and ≥ 25 small props per dressed zone, placed against things, every one seated;
* shade is coloured (the sky's), not black; the key and the world differ in hue;
* t = 0 and t = 1.5 are visibly different.

Then: `build` → `check_placement` (floating / sunken / unsupported / interpenetration + the
fix per asset, in metres) → `scene_probe` (unique triangles, drivers, the world, console) →
`scene_views` (authored cameras + the overview rig, with `camera_checks`) → fix the worst →
repeat.  Look at every camera's frame AND its numbers: no black frames, no white-out, the
subject (not sky / ground) fills the establishing shot, `camera_in_geometry` false, and
`check_placement` clean.
