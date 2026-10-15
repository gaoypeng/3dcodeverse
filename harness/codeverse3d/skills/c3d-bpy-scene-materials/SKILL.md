---
name: c3d-bpy-scene-materials
description: "Use in every scene_blender env, zone, asset or refine session that authors a surface — ground, walls, props, foliage, water, glass, a glowing sign. Cycles renders every node you wire, but the harness's census reads only three Principled sockets, so no gate rewards material work and the judge penalises the lack of it. Gives the Blender 5.2 node facts (no use_nodes, the 4.x socket names, ShaderNodeMix), the lib.materials recipes, variation across copies without identical twins, ground layering and the seam under props, water, glass and emissive done as dielectrics and fixtures, and what to zoom on before you finish."
license: Apache-2.0
compatibility: Blender 5.2.2 LTS headless, scene_blender runtime; Cycles 32 samples + OIDN under AgX (the harness's render settings), Principled BSDF 4.x socket names.
metadata:
  evidence: mixed
  evidence_note: 'The census facts are read from live code on 2026-10-14 (wrappers/_census_glb.py, wrappers/_census.py, the languages/blender lint tables verified against Blender 5.2.2 LTS) and the defect wording from the scene judge rubric; the recipes restate the scene_blender starter library. The corpus half is empty: zero graded scene_blender runs exist on 2026-10-14; the first loop iteration attaches numbers.'
  verified: "2026-10-14"
  target_metric: "flat_principled_materials"
  target_direction: "down"
  target_unit: "materials whose Base Color, Roughness and Metallic are all unlinked constants (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no graded scene_blender runs yet (2026-10-14); the first loop iteration attaches numbers once the census records the count"
---

# Surfaces that read as a place — Principled recipes the census cannot see

## Who sees what

The judge sees Cycles pixels: every noise texture, bump and vertex colour you wire is in the
picture, and the rubric names the failures directly — `placeholder_material` (default-grey or
untextured flat surfaces, with a score ceiling of its own), `flat_ground` (one flat colour with
a hard undressed seam where objects meet it) and `monotonous_vegetation` (one shape stamped many
times with no variation in hue or size) — under its `materials_shaders_effects` criterion. The
deterministic side sees almost nothing: the census GLB the gates read carries each material's Principled `Base Color`,
`Metallic` and `Roughness` default values only, and a LINKED socket exports as its default. So
no gate measures material work; this sheet is about the judge's picture, plus the one census
fact that does bite — a mesh with no material at all is warned as exporting grey.

## Blender 5.2 facts the lint checks

* `bpy.data.materials.new(name)` already carries a node tree with a Principled BSDF: reach into
  `mat.node_tree.nodes["Principled BSDF"]`. A `use_nodes` line is not needed, and the lint
  reports it as deprecated.
* Socket names are the 4.x ones — `Base Color`, `Roughness`, `Metallic`, `IOR`, `Alpha`,
  `Normal`, `Emission Color` plus `Emission Strength`, `Transmission Weight`, `Coat Weight`,
  `Sheen Weight`, `Subsurface Weight`, `Specular IOR Level`. Never write the old `Specular`,
  `Transmission`, `Emission`, `Subsurface` or `Clearcoat` keys: they raise, and the lint names the
  renamed ones for you. `Specular Tint` is a colour, so assign a 4-tuple.
* Mixing colours is `ShaderNodeMix` with its data type set to RGBA (`Factor`, `A`, `B` in,
  `Result` out), not `ShaderNodeMixRGB`, which is gone.
* Per-face materials: set `material_index` on the bmesh faces BEFORE `bm.to_mesh`. Every new
  polygon starts on slot 0, and the census warns about slots no polygon uses — the part was
  authored in four colours and exports in one.

## The recipes — `lib.materials` first

Every call takes a name and returns a material; colours are sRGB hex strings (`rgba` decodes
them to linear). Procedural detail reads OBJECT coordinates, so `noise_scale` is features per
metre whatever the object's size, and the lib's own band for a non-emissive albedo is 0.02-0.8
linear: pure black reads as a hole, pure white as a blown surface.

| want | call |
|---|---|
| any surface with value variance | `material(name, color, roughness=..., metal=..., noise_scale=..., bump=..., emission=...)` — a `noise_scale` above zero is the low-contrast map every large surface needs |
| stone, brick, plaster | `masonry(name, color, mortar=..., scale=...)`, `limestone(name, color, scale=...)`, `ashlar(name, color)`, `mineral_surface(name, color, stone=...)` |
| wood | `woodgrain(mat, dark=..., light=..., direction=...)` on an existing material |
| worn paint, used metal | `chipped_paint(mat, paint, substrate, scale=...)`, `worn_alloy(name, color, seed=..., metal=...)` |
| foliage, snow, ice | `foliage(name, color, variants=3)` returns a LIST of variant materials; `snow(name)`, `ice(name)` |
| water | `water(name, color=..., roughness=..., scale=..., flow=..., ctx=ctx)` for a tinted, moving surface; `clear_water(name)` for shallow, almost colourless water (a basin, a bowl, a stream) |
| glass | `glass(name, color=..., roughness=..., ior=...)` |
| a glowing fixture | `emissive(name, color, strength)` — the look only; the light is a `practical` lamp (c3d-bpy-scene-lighting) |
| a copy that is not a twin | `tint(mat, variant)` — a copy with its Base Color shifted, one per VARIANT and shared by that variant's copies; it leaves a LINKED Base Color alone, so a noise-mapped material gets its variant from `material` called with another colour |

## Variation without twins

Collection instances share the source collection's materials, so per-instance tinting is not
"edit the material". Variation across copies comes from (a) several SOURCE variants —
`build_<snake>(ctx, variant=k)` picking a different `foliage` entry or `tint` — placed in
rotation, (b) per-instance scale and yaw from `lib.scatter.scatter`, and (c) for one material
over many copies, an Object Info node's `Random` output driving a hue shift, which Cycles
evaluates per instance. The `stamped_ring` plan check measures rings of identical copies at one
radius, and the judge calls the result monotonous vegetation.

## Ground: layers, not a colour

`lib.terrain.ground` takes ONE material; make it earn its area. `mineral_surface` or
`material(..., noise_scale=...)` as the base, a second colour mixed in by slope (the Geometry
node's `Normal`, its Z through a ColorRamp into the mix factor) and by height (object Z from
Texture Coordinate) — grass to earth to rock — plus a worn band as a noise-masked third colour
where the plan's paths run. The seam under a building is `flat_ground`'s own wording: a darker,
rougher band of ground tint under big structures, and ground cover (`gn_scatter`, `grass_patch`)
along their feet. A colour attribute on the ground mesh read by a Color Attribute node does the
same job when the bands are easier to paint per vertex than to compute per pixel.

## Water, glass, fog — dielectrics and volumes

* Water is a dielectric: `water` and `clear_water` set transmission, a low roughness and
  `Metallic` at zero — never a metallic mirror plane. Moving water scrolls its mapping node on
  `ctx.frame(t)` through `lib.motion.scroll`, which is why `water(..., ctx=ctx)` wants the context.
* Glass is `glass`: full `Transmission Weight`, low roughness, the lib's default IOR. The bounce
  budget is the harness's, so a stack of transmissive layers along one sight line goes dark —
  keep it to one or two.
* Fog is a VOLUME-ONLY material on a box (`lib.sky.haze`): only the Material Output's Volume
  socket linked. A box with a surface shader as well is matter to the census — a huge placed
  asset enclosing everything — and the `no_fog` check does not count it as atmosphere.

## Emissive and metal under AgX

AgX desaturates bright emitters toward white: a saturated sign needs a modest
`Emission Strength` and a coloured `practical` lamp beside it for the glow on the ground. Metals
need something to reflect — the sky world and the haze are what `worn_alloy` mirrors; a metal
under a black world is a black shape.

## Verify before you finish

`scene_views(cameras="authored", times=[0.0])` and zoom one frame: a wall or a ground that is one
flat value needs `noise_scale`; two copies you cannot tell apart need variants; a grey default is
a mesh that never got a material (the build output warns about it by name). Then `scene_probe`
for triangle counts — materials are free, geometry is not.

Node snippets — noise to bump, the slope mix, the Object Info tint, per-face slots, the
volume-only box, the socket rename table — in `references/principled-recipes.md`.
