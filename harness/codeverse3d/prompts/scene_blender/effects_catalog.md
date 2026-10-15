# The shipped bpy library — call these before writing your own

`src/lib/` is in your workspace already: thirteen modules of Blender 5.2 LTS bpy
that build, seat, light and animate what a scene needs.  They are not an
outside package — they are harness code shipped into the workspace, so
importing them is allowed where "never import a helper SDK" still forbids
everything else (and a write to `src/lib/` is reverted).

Reach for one FIRST.  Write your own only for something no entry here covers,
and then build it with `Batch` from `lib/mesh.py` — plain vertex and face lists,
never `bpy.ops` primitives in a loop.

Import with the package path from any file — `src/env.py`, `src/zones/x.py`
and `src/assets/x.py` all say `from lib.materials import material` or
`from lib import mesh, place` (`src/` is on the path; the modules import each
other the same way).

| Want | Call |
| --- | --- |
| noise on the CPU, a seeded generator, a cheap per-item jitter | `rng(seed)` (THE random: never the module-level `random`) · `hash01(i, seed=0)` 0..1 from an index · `fbm2(x, y, *, octaves=4, lacunarity=2.0, gain=0.5, seed=0)` / `fbm3(x, y, z, *, …)` gradient noise about -1..1 with 1 unit = one feature (call with `x / scale_m`) · `ridged(x, y, *, seed=0, octaves=5)` 0..1 crests · `mountain_height(x, y, *, seed=0, amplitude=45.0, frequency=0.013)` a horizon ridge in metres · `height_field(size, segments, fn)` a row-major grid of samples — `lib/noise.py`; pure python: a 96 x 96 field with four octaves is ~60 ms, so keep heavy fields to the env |
| a prop built from parts as ONE object (one placement row, one mesh per material) | `Batch(name)` then `.box(mat, loc, size, rotation=0.0)` (loc = centre, 12 triangles) · `.sphere(mat, loc, scale, subdivision=1)` (level 1 = 20 triangles, 2 = 80, 3 = 320, 4 = 1 280) · `.lathe(mat, xy, profile, sides=32, phase=0.0)` (profile = [(radius, z), …], capped) · `.cone(mat, loc, radius, height, top=0.0, sides=24)` (loc = base centre) · `.beam(mat, a, b, radius, end=None, sides=8)` (a capped tube; `end` tapers) · `.torus(mat, loc, radius, tube, major=64, minor=8)` · `.ring(mat, center, normal, radius, tube, segments=96)` (a hoop about `normal`) · `.add(mat, vertices, faces, smooth=False)` raw lists · `.transformed(matrix)` a view whose additions are transformed (a figure turned to its heading) · `.finish(collection, *, bevel=0.0, parent=True, name_prefix="")` → `[root, mesh, …]`: ONE root Empty named after the batch (the placement row; `root["placement"] = "free"` for a hanging thing), origin at the base centre; `parent=False` for an asset's own collection · `mesh_object(name, vertices, faces, mat, collection, *, smooth=False)` · `curve_object(name, points, radius, mat, collection, *, cyclic=False)` a rope / cable / vine · `sphere_template(level)` an icosphere's lists · `parent_under(objects, collection, name)` several objects as one row — `lib/mesh.py`; metres, Z up, -Y front |
| a textured Principled material without a texture file | `material(name, color, roughness=.5, metal=0.0, *, noise_scale=0.0, bump=.08, emission=0.0)` (hex `"8A6A3F"` or a linear tuple; `noise_scale` features / m adds breakup + bump) · `masonry(name, color, mortar="625D66", scale=2.0)` rows / m · `limestone(name, color, scale=1.2)` · `ashlar(name, color)` 1.2 x 0.4 m blocks · `woodgrain(mat, dark="29190F", light="805939", direction=(3,.1,5))` on an existing material · `chipped_paint(mat, paint, substrate, scale=6)` · `worn_alloy(name, color, *, seed=0, metal=.55)` · `mineral_surface(name, color, *, stone=False)` rock / soil, cracks when `stone` · `water(name, color="163F4A", roughness=.12, scale=.8, *, flow=(0.6,0.2), ctx=None)` ONE refracting surface (give it a bed; ripples drift with `ctx`) · `clear_water(name)` a basin · `glass(name, color="FFFFFF", roughness=.05, ior=1.5)` · `foliage(name, color, variants=3)` → a LIST of leaf materials (give neighbours different ones) · `snow(name="Snow")` · `ice(name="Ice")` · `emissive(name, color, strength)` a light-giving skin (3-8 reads as a lamp) · `tint(mat, variant)` a shifted copy (-1..1) · `volume(name, color, density=.4, *, anisotropy=.3, noise_scale=0.0, emission=0.0)` a Principled VOLUME with no surface — the ONLY soft steam / smoke / dust / spark haze in Cycles, on a `Batch` of subdivision-3 spheres (see "Soft particles" in the cookbook) — `lib/materials.py`; patterns read OBJECT coordinates so they keep their metre scale; albedos belong in 0.02-0.8 linear |
| the ground, hills, rock | `make_height_at(*, seed=0, relief=3.0, scale=60.0, flat_radius=45.0, base=0.0)` → THE `height_at` function of (x, y) (level inside `flat_radius`, hills of `relief` m beyond; env.py returns it) · `ground(name, collection, *, size, segments, height_at, material, center=(0,0), smooth=True)` the terrain mesh (name it `Ground…`; 96 segments = 18 k triangles) · `cliff(name, collection, *, length, height, height_at, material, seed=0)` a rock face along +X (~2.4 k) · `boulder(batch, mat, center, scale, *, seed=0, detail=3)` standing on `center` z (80 / 320 / 1 280 triangles at detail 2 / 3 / 4) · `rock_field(batch, mat, center, radius, count, height_at, rng)` 80 apiece · `talus(batch, mat, center, scale, seed)` a scree heap (~800) · `basalt_column(batch, materials, center, radius, height, rng, lean=(0,0))` a hexagonal cluster (~300) · `shoreline_mask(x, y, water_level, height_at)` 0 in the water, 1 half a metre up — `lib/terrain.py`; every recipe's `center` is the GROUND point: pass `height_at` (x, y) |
| the sky, fog, night dressing (world setters are ENV ONLY) | `sun_world(scene, *, elevation_deg=12.0, azimuth_deg=125.0, strength=.2, dust=2.0)` the physical MULTIPLE_SCATTERING sky, compass 0 = +X / 90 = +Y like the Sun lamp, disc off (the lamp is the sun) · `night_world(scene, color="17273F", strength=.3)` · `gradient_world(scene, stops, *, clouds=True, strength=.7)` stops = `[(0, "F2C9A0"), (1, "1F4A8E")]` horizon → zenith · `haze(collection, loc, size, density=.003, color="ADC9E1", name="Atmosphere")` a bounded fog box (volume-only: the census reports it as fog, never as matter; 0.002 clear, 0.01 mist, 0.03 fog) · `aurora(name, collection, center, *, span=560, height=72, seed=0, strength=.24, ctx=None)` a curtain at `center` (x, y, altitude), tagged free — NAME it with a sky word (`"AuroraSky"`) so the census files it as sky · `stars(batch, mat, *, seed=0, count=900, radius=600)` into a batch named `Stars…` with an `emissive` mat; tag its root free — `lib/sky.py`; no clouds volume, no moon disc |
| lamps | `sun(collection, *, azimuth_deg, elevation_deg, color="FFCF9C", energy=3.0, angle_deg=2.0, name="Sun")` → `(lamp, towards_sun)`, W/m² (3-5 day, 0.3-1 dusk) · `area_light(collection, name, loc, color, energy, *, size=5.0, target=None, shape="DISK")` Watts · `point_light(collection, name, loc, color, energy, *, radius=.05)` · `spot(collection, name, loc, color, energy, *, size_deg=45.0, blend=.3, target=None)` · `practical(collection, name, loc, color="FFB270", energy=300, *, size=.42, target=None)` the light a visible fixture throws (pair it with an `emissive` skin) · `aim(obj, target)` — `lib/lighting.py`; exposure and the view transform are the harness's: light the scene, never the camera |
| water: a lake, a fall, jets, spray | `water_surface(name, collection, *, size, level, material=None, ripple=0.045, seed=0, height_fn=None)` ONE plane at z = `level` centred on the origin — move the object; `height_fn` drops cells where the ground stands above the water (name it `…Water` / `…Lake`: a foot in it is "on water", never floating; ~4.6 k) · `pool_height(x, y, *, level=-.25)` a `height_at` for things that float · `waterfall(name, collection, center, *, width, height, seed=0, ctx=None, layers=4)` foam sheets dropping onto `center` (the impact level), flow toward -Y, ~400 per layer · `hanging_fall(name, collection, outlet, along, across, *, width, height, seed=0, ctx=None)` one sheet from a spout · `fountain_jets(batch, collection, water_mat, *, center, nozzle_r, landing_r, count=14, rise=1.85, ctx=None, seed=0)` parabolic tubes + spray (~220 per jet) · `droplets(batch, mat, center, width, bottom, rng, count=260)` 4 triangles each · `foam_shader(name)` — `lib/water.py`; no fluid solver, no caustics: a fall is sheets whose texture streams with `ctx` |
| trees, shrubs, ferns, grass, flowers | `tree(batch, loc, height, bark, foliage, *, seed=0, kind="broadleaf", max_leaves=4000)` kinds broadleaf / oak / cypress / pine — a crown of leaf CARDS (3-9 k triangles; `max_leaves` caps it; pine ≈ 350) · `shrub(batch, bark, foliage, center, *, scale=1.0, seed=0)` (~300) · `leaf_spray(batch, foliage, center, rng, *, radius=.32, count=35)` a vine knot · `fern(batch, mat, center, *, scale=1.0, seed=0)` · `tropical(batch, leaves, stem, center, *, scale=1.0, seed=0)` · `grass_clump(batch, mats, x, y, z, rng)` 12 blades · `perennial_bed(batch, center, radius, height_at, foliage, petals, rng)` (~1.1 k) · `grass_patch(name, collection, surface, *, blade=None, density=5.0, seed=0, height=.3, color="4A6B2A")` a geometry-nodes instance CLOUD on `surface` (a hidden `terrain.ground` patch: `hide_render = True`) — exempt from per-asset placement, 3 quads per blade · `tree_stand(collection, batch, positions, height_at, rng, bark=…, foliage=…, height=7.0, kind=…)` a copse as ONE object — `lib/vegetation.py`; every `loc` / `center` is the ground point |
| buildings, roofs, bridges, paving, a room, lettering | `timber_house(batch, roofs, x, y, width, depth, floors, mats, rng, index)` half-timbered, `mats` = {wall, timber, door, window}, `roofs` = {tile, trim}; returns the ridge height (≤ 14 k) · `tiled_roof(batch, tile, trim, center, width, depth, rise, rng)` ridge along X on `center` (x, y, eave z) · `clay_roof(batch, frame, tiles, timber, center, width, depth, rise, rng)` pantiles · `fret_window(batch, wood, paper, x, y, z, width, height, direction, *, pattern=0)` facing compass `direction` · `arched_opening(batch, mat, center, width, height, *, depth=.28, thickness=.2)` · `stone_bridge(batch, stone, dark, *, center, span=27.6, width=4.7, rise=3.9, height_at=None)` hump-backed along X (~700) · `arched_footbridge(batch, wood, stone, *, start, end, rise=1.5, height_at=None)` (~1 k; give it `height_at` on a bank) · `tower(batch, mat, center, radius, low, high, windows, *, count=8)` · `paving(batch, mats, *, center, inner_r, rings, seed)` fan-set rings, 2 cm thick (~350 per ring) · `paving_grid(batch, mats, center, size, stone, joint, rng)` flagstones · `room_shell(collection, mat, *, center, extents, thickness=.2, openings=(), glazed=False)` an interior's floor, walls and `Ceiling` under a `RoomShell` root (env.py; openings = `(wall, offset, width, sill, head)`; `glazed=True` is a glass house — a conservatory, a greenhouse, a glass atrium: every wall panel and the lid become `materials.glass` panes that cast no shadow on an iron-frame grid of `mat` bars, so the sun comes through and the frame's grid shadows the floor, which stays an opaque slab of `mat`; the names stay, a pane being its panel's name + `Glass`) · `lantern(batch, silk, wood, center, *, radius=.35, length=None)` (~550; an `emissive` silk) · `text_mesh(name, collection, body, loc, size, mat, *, rotation=(pi/2,0,0), align="CENTER", extrude=.005)` readable lettering (~300 per letter) — `lib/architecture.py`; `center` / (x, y) sit on the ground z you pass |
| placing copies: instances and clouds | `instance(collection, source, name, x, y, *, height_at, rot_z=0.0, scale=1.0, sink=0.0)` ONE seated collection-instance Empty of `ctx.assets["<snake>"]` (the placement row) · `scatter(collection, source, prefix, points, *, height_at, rng, rot="random", scale=(1,1))` one per point, named `<prefix>_<i>` · `poisson(rng, *, area, min_dist, max_count, mask=None)` points in (x0, y0, x1, y1) at least `min_dist` apart; `mask` of (x, y) → 0..1 keeps a shoreline clear · `along_path(points, n, *, jitter=0.0, rng=None)` · `grid(center, size, spacing, rng, *, jitter=.3)` · `gn_scatter(name, collection, surface, instance_obj, *, density, seed=0, align_normal=True, scale_range=(.8,1.2))` an instance cloud (pebbles, tufts, hundreds+) — one object, exempt from per-asset placement — `lib/scatter.py`; a cloud cannot be measured per copy: anything the plan COUNTS (a bench, a cart, a tree) is an `instance` |
| seating, facing, the zone's own placement check | `seat(obj, height_at, *, sink=0.0, columns=5)` drop a ROOT until its lowest foot column rests on the ground (the gate's own columns: lowest + ±x / ±y of the foot band) · `seat_instance(empty, source, height_at, *, sink=0.0)` · `rest_on(obj, support, *, offset=0.0)` a lantern on a table · `bbox_world(obj, *, evaluated=True)` (min, max) incl. children and instances · `face_toward(obj, target)` / `heading(a, b)` for -Y-front things · `clear_of(objs, min_gap=.02, *, overlap=0.10)` → `[(a, b, fraction)]` AABB overlaps the gate would call interpenetration: run it on your roots before returning · `clear_mask(objs, margin=.3)` → an (x, y) mask for the scatter module's poisson `mask` argument that keeps scatter points out of the placed roots' footprints (+ `margin` m): build it BEFORE you scatter the small stuff · `keep_inside(obj, bbox)` the plan's box · `unique_name(base, i)` `Base_i`, bumped past an existing name (names are global in Blender) · `world_matrix(obj)` the matrix NOW (`obj.matrix_world` is stale until the depsgraph runs) — `lib/place.py`; contact is 2 cm, floating starts at 5 cm outdoors / 2 cm indoors, sunk at 10 cm |
| a person, a crowd | `figure(batch, cloth, skin, center, *, scale=1.0, heading=0.0, pose=0.0)` 1.72 m, facing -Y turned by `heading`, `pose` 0 standing → 1 striding (~530) · `visitor(batch, cloth, mats, center, *, scale=1.0, heading=0.0, pose=0.0, variant=0)` a coat over `mats[variant]` legs, `mats[-1]` the skin · `crowd(batch, mats, points, rng, scale=1.0)` one per (x, y, z) — `lib/figure.py`; no faces, no hands with fingers: a figure reads at 3 m and beyond |
| motion between t = 0 and 1.5 s | `keyframe_path(obj, points, ctx, *, t0=0.0, t1=None, interpolation="LINEAR")` · `drift(obj, delta, ctx)` a boat, a balloon, a leaf · `spin(obj, axis="Z", turns_per_s=.25, ctx=None)` a SIMPLE driver `frame * k` (a wheel, a vane) · `sway(obj, axis, amplitude, period_s, ctx)` a SIMPLE driver, a sine of `frame` (a hanging lantern, a mast) · `scroll(mapping_node, velocity, ctx)` a texture that flows · `shape_key_sweep(obj, key_name, ctx, *, from_value=0.0, to_value=1.0)` a sail filling · `fcurves(id_data)` / `set_interpolation(id_data, interpolation="LINEAR")` the 5.2 slotted-action curves — `lib/motion.py`; keyframes land on `ctx.frame(t)`; a driver that needs Python evaluates to 0 in the build and fails it |

## The six rules that apply to all of them

**1. Collection, name, seed.**  Every factory takes the collection it links into
(`ctx.collection` — never `bpy.context.collection`), a name, and a `seed` or
`rng` wherever it draws: the same seed gives the same scene on every machine,
and `ctx.rng(seed)` is the ONLY randomness a zone uses.  Names are global in
Blender: `place.unique_name("Lantern", i)` — a duplicate silently becomes
`Lantern_0.001` and reads as a stray instance.

**2. Seat everything.**  A placed thing is a ROOT object in the zone's
collection: a `Batch.finish` root, a `scatter.instance` Empty, a
`mesh_object`.  The gate samples its foot columns against what is under them:
a gap over 5 cm (2 cm indoors) is floating, a burial over 10 cm is sunk, an AABB
overlap over 10 % of the smaller box is interpenetration, and a measured
defect caps the score.  Batch recipes stand on the ground z you pass
(`height_at` (x, y)); instances seat themselves; anything else goes through
`place.seat(root, ctx.height_at)`.  `obj["placement"] = "free"` ONLY for a thing
that really hangs or flies — the gate reports the share of free-tagged assets.

**3. Motion is data on `ctx.frame(t)`.**  Keyframes through `lib.motion`, or a
simple driver of `frame`; never `frame_set`, handlers or Python drivers.  The
harness renders `ctx.times` = 0 and 1.5 s and reads the .blend it saved.

**4. The world and the sun belong to `src/env.py`.**  `sun_world` /
`night_world` / `gradient_world` and `lighting.sun` run in `build_env` and nowhere
else (the lint rejects a world in a zone); zones add their own practicals and
read `ctx.env["sun_direction"]`.  Give the sky and the Sun lamp the SAME
azimuth / elevation.

**5. Instances versus clouds.**  A thing the plan counts (a bench, a cart, a
tree) is a `scatter.instance` of its asset collection: one row the gate can
name.  Ground cover by the hundred (blades, pebbles, tufts) is a `gn_scatter` /
`grass_patch` cloud: one object, counted as instances, exempt from per-asset
placement.  A prop is ONE `Batch`, not twenty primitives: the census and the
renderer are per object.

**6. Import only what you CALL.**  `from lib.materials import material, woodgrain`
— not a module's whole export list.  Measured on the three.js library's first
six scenes: 120 imported names were never used, and every later round had to
read them.

## What differs on this renderer

* **Cycles, 32 samples + OpenImageDenoise, AgX at exposure 0**, 1024 x 576,
  the harness's settings (the lint rejects `scene.render` / `scene.cycles`
  writes).  There is NO post chain and NO bloom: an `emissive` skin at
  strength 3-8 reads as a lamp, 20+ as a sun-bright source, and a lamp beside it
  (`lighting.practical`) is what actually lights the wall.  Non-emissive albedos
  belong in **0.02-0.8** linear.
* **The census is the evaluated depsgraph**: modifiers, geometry nodes and
  instances count exactly as they render; a volume-only material is fog, not
  matter (the `haze` box never appears as a placed thing).  Unique triangles
  are capped at 3 M; a tree is 3-9 k, a two-storey house ~10 k, so a hamlet and
  a wood fit with room to spare.
* **Blender 5.2 API facts the library already follows** (and you must too):
  no `use_nodes`; `ShaderNodeMix` (not MixRGB) with RGBA on inputs 6 / 7 and
  output 2; the Principled sockets are `Emission Color` / `Emission Strength` /
  `Transmission Weight` / `Coat Weight` / `Specular IOR Level`; the sky is
  `MULTIPLE_SCATTERING` with `aerosol_density`; boolean solvers are MANIFOLD /
  EXACT / FLOAT; geometry-nodes inputs are
  `getattr(mod.properties.inputs, "Socket_2").value = v`; actions are slotted
  (`lib.motion.fcurves`, not `action.fcurves`); `obj.matrix_world` is stale until
  the depsgraph runs (`lib.place.world_matrix`).
* **One refracting water plane, no planar reflector budget to spend**: Cycles
  refracts and reflects whatever is there.  Keep the ground under the water (a
  basin in `height_at`) so there is a bed to see.
