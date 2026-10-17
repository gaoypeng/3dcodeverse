# No library ships — what the renderer and Blender 5.2 do with the raw bpy you write

There is no harness library in this workspace: no catalog of calls, nothing under `src/` but the
modules you and the other builders write.  Terrain, sky, lamps, materials, vegetation, buildings,
water, placement and motion are raw `bpy` / `bmesh` / `mathutils` (an import of `lib` is a lint
ERROR).  What stays true whatever you build:

* **Collection, name, seed.**  Link everything into `ctx.collection` (never
  `bpy.context.collection`); names are global in Blender, so number copies `f"{base}_{i}"` — a
  duplicate silently becomes `Lantern_0.001` and reads as a stray instance; `ctx.rng(seed)` is the
  only randomness the lint accepts.
* **Seating is measured** at each root's lowest evaluated vertices (its children and instanced
  geometry included): a gap over 5 cm (2 cm indoors) is floating, a burial over 10 cm sunk, an
  AABB overlap over 10 % of the smaller box interpenetration.  Compute it from world-space
  vertices and `ctx.height_at`; `obj["placement"] = "free"` only for what really hangs or flies.
* **Motion is data on `ctx.frame(t)`**: keyframes (LINEAR, on the slotted action's channelbag
  f-curves) or a simple driver of `frame`; never `frame_set`, handlers or Python drivers.
* **The world belongs to `src/env.py`** (`bpy.data.worlds.new` / `scene.world =` anywhere else is a
  lint ERROR); a sky and a Sun lamp agree when they share one azimuth / elevation.
* **Instances versus clouds.**  A thing the plan counts is ONE root (a mesh, a parent Empty over
  parts, a collection-instance Empty); hundreds of blades, pebbles or leaves can be one
  geometry-nodes instance cloud, counted as instances and exempt from per-asset placement.

## What differs on this renderer

* **Cycles, 32 samples + OpenImageDenoise, AgX at exposure 0**, 1024 x 576, the harness's
  settings (the lint rejects `scene.render` / `scene.cycles` writes).  There is NO post chain and
  NO bloom: an emissive skin at strength 3-8 reads as a lamp, 20+ as a sun-bright source, and a
  lamp beside it is what actually lights the wall.  Non-emissive albedos outside **0.02-0.8**
  linear push the frame gate toward dark / blown.
* **The census is the evaluated depsgraph**: modifiers, geometry nodes and instances count
  exactly as they render; a volume-only material is fog, not matter.  Unique triangles are capped
  at 3 M; a tree is 3-9 k, a two-storey house ~10 k, so a hamlet and a wood fit with room to spare.
* **Blender 5.2 API facts** (the contract lists them): no `use_nodes`; `ShaderNodeMix` (not
  MixRGB) with RGBA on inputs 6 / 7 and output 2; the Principled sockets are `Emission Color` /
  `Emission Strength` / `Transmission Weight` / `Coat Weight` / `Specular IOR Level`; the sky is
  `MULTIPLE_SCATTERING` with `aerosol_density`; boolean solvers are MANIFOLD / EXACT / FLOAT;
  geometry-nodes inputs are `getattr(mod.properties.inputs, "Socket_2").value = v`; actions are
  slotted (`action.layers[0].strips[0].channelbag(action.slots[0]).fcurves`, not
  `action.fcurves`); `obj.matrix_world` is stale until the depsgraph runs.
* **One refracting water plane, no planar reflector budget to spend**: Cycles refracts and
  reflects whatever is there.  Water that refracts needs a bed below its level in `height_at`
  (whatever shape the brief's water has) so there is something to see through it.
