---
name: c3d-bpy-scene-materials
description: "Use in every scene_blender env, zone, asset or refine session that authors a surface — ground, walls, props, foliage, water, glass, a glowing sign, steam. Cycles renders every node you wire, but the harness's census reads only three Principled sockets, so no gate rewards material work and the judge penalises the lack of it. Gives the Blender 5.2 node facts (no use_nodes, the 4.x socket names, ShaderNodeMix), what the judge names and what it measures, the node practices for multi-scale surfaces in raw bpy (no library ships in raw mode), variation across copies without identical twins, ground that varies where the brief's surface does, water, glass and emissive done as dielectrics and fixtures, soft particles as volumes, and what to zoom on before you finish."
license: Apache-2.0
compatibility: Blender 5.2.2 LTS headless, scene_blender runtime; Cycles 32 samples + OIDN under AgX (the harness's render settings), Principled BSDF 4.x socket names.
metadata:
  evidence: mixed
  evidence_note: 'The argon_raw (D113) copy of the base bundle with every library call replaced by the node practice it stands for. The census facts are read from live code on 2026-10-14 (wrappers/_census_glb.py, wrappers/_census.py, the languages/blender lint tables verified against Blender 5.2.2 LTS) and the defect wording from the scene judge rubric. The corpus half is the argon loop iteration 0 of 2026-10-15 (30 scene_blender runs): 8 scenes built hard-edged particles from surface shaders.'
  verified: "2026-10-17"
  target_metric: "flat_principled_materials"
  target_direction: "down"
  target_unit: "materials whose Base Color, Roughness and Metallic are all unlinked constants (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — the census does not yet record the count; the argon loop iteration 0 (2026-10-15) is the first sample once it does"
---

# Surfaces that read as a place — Principled recipes the census cannot see

## Who sees what

The judge sees Cycles pixels: every noise texture, bump and vertex colour you wire is in the
picture, and the rubric names the failures directly — `placeholder_material` (default-grey or
untextured flat surfaces, with a score ceiling of its own), `flat_ground` (one flat colour with
a hard undressed seam where objects meet it), `monotonous_vegetation` (one shape stamped many
times with no variation in hue or size) and `hard_edged_particles` (steam, smoke or sparks with a
silhouette) — under its `materials_shaders_effects` criterion. The deterministic side sees almost
nothing: the census GLB the gates read carries each material's Principled `Base Color`,
`Metallic` and `Roughness` default values only, and a LINKED socket exports as its default. So
no gate measures material work; this sheet is about the judge's picture, plus the one census
fact that does bite — a mesh with no material at all is warned as exporting grey.

## Blender 5.2 facts the lint checks

* `bpy.data.materials.new(name)` already carries a node tree with a Principled BSDF: reach into
  `mat.node_tree.nodes["Principled BSDF"]`. A `use_nodes` line is not needed, and the lint
  reports it as deprecated.
* Socket names are the 4.x ones — `Base Color`, `Roughness`, `Metallic`, `IOR`, `Alpha`,
  `Normal`, `Emission Color` plus `Emission Strength`, `Transmission Weight`, `Coat Weight`,
  `Sheen Weight`, `Subsurface Weight`, `Specular IOR Level`. The old `Specular`, `Transmission`,
  `Emission`, `Subsurface` and `Clearcoat` keys raise, and the lint names the renamed ones for
  you. `Specular Tint` is a colour, so assign a 4-tuple.
* Mixing colours is `ShaderNodeMix` with its data type set to RGBA (`Factor`, `A`, `B` in,
  `Result` out); `ShaderNodeMixRGB` is gone.
* Per-face materials: set `material_index` on the bmesh faces BEFORE `bm.to_mesh`. Every new
  polygon starts on slot 0, and the census warns about slots no polygon uses — the part was
  authored in four colours and exports in one.

## Surfaces in raw nodes — what reads as photoreal

No library ships: every material is a node tree you wire on `mat.node_tree`. What the judge reads
as real is variation at MORE THAN ONE SCALE, in object or generated coordinates so it is metres
whatever the object's size:

| scale | what carries it | nodes |
|---|---|---|
| metres — tone, wear, damp, sun-bleach | a low-frequency mask mixing two close colours | Noise texture (`Scale` ~0.2-1 per metre) into `ShaderNodeMix` (RGBA) |
| decimetres — courses, planks, patches | a pattern the material has | Brick / Wave / Voronoi texture, or a ColorRamp over noise |
| centimetres — grain, pores, grit | fine bump and roughness breakup | Noise / Voronoi (`Scale` 20-200) into `ShaderNodeBump` and through a Map Range into `Roughness` |

Keep non-emissive albedos inside 0.02-0.8 linear: pure black reads as a hole, pure white as a
blown surface. A copy is not a twin when its variant picks other colours or an Object Info
`Random` drives a small hue shift (below). A glowing fixture is `Emission Color` with a modest
`Emission Strength` — the look only; the light is a lamp (c3d-bpy-scene-lighting). Steam, smoke,
dust and mist are a volume-only material (below). Node snippets in
`references/principled-recipes.md`.

## Variation without twins

Collection instances share the source collection's materials, so per-instance tinting is not
"edit the material". Variation across copies comes from (a) several SOURCE variants —
`build_<snake>(ctx, variant=k)` building with other colours and another silhouette — placed in
rotation, (b) per-instance scale and yaw on each instance Empty, and (c) for one material
over many copies, an Object Info node's `Random` output driving a hue shift, which Cycles
evaluates per instance. The `stamped_ring` plan check measures rings of identical copies at one
radius, and the judge calls the result monotonous vegetation.

## Ground: what `flat_ground` means

Requirement (judged): the ground does not read as one flat untextured colour where the brief's
surface would vary, and the seam where structures meet it does not read as a hard undressed line.
A ground mesh usually carries ONE material, so the variation lives inside it or in the mesh's own
attributes. Options, by what the brief's place has: a slope mix (the Geometry node's `Normal`,
its Z through a ColorRamp into the mix factor) and a height mix (object Z from Texture
Coordinate) for natural ground; a worn band as a noise-masked colour along the routes people
take; a darker or rougher apron, or ground cover (a geometry-nodes Instance on Points cloud), at the
feet of big structures; a colour attribute on the ground mesh read by a Color Attribute node when
the bands are easier to paint per vertex than to compute per pixel; paving (a Brick texture, or
modelled setts) where the ground is built, not grown. A polished floor or a plaza that IS one
material is not a defect when its material reads as that material.

## Water, glass, fog — dielectrics and volumes

* Water is a dielectric: `Transmission Weight` 1, a low roughness and `Metallic` at zero; a
  metallic mirror plane reads as chrome, not water. Moving water scrolls its Mapping node's
  `Location` input with keyframes on `ctx.frame(t)`.
* Glass: full `Transmission Weight`, roughness near zero, `IOR` about 1.45-1.5. The bounce
  budget is the harness's, so a stack of transmissive layers along one sight line goes dark —
  one or two read.
* Fog is a VOLUME-ONLY material on any mesh (a box is enough): only the Material Output's Volume
  socket linked. A mesh with a surface shader as well is matter to the census — a
  huge placed asset enclosing everything — and the `no_fog` check does not count it as atmosphere.
* Soft particles are volumes too. A surface shader always has a silhouette; 8 of the 30 argon
  Blender scenes of 2026-10-15 built steam from translucent ico-spheres, metaballs, stacked tori,
  discs or polygon sparks and were graded `hard_edged_particles`. A volume-only material on a
  cloud of spheres (one mesh, one object, tagged `placement = "free"`, keyframed so the two
  judged frames differ) rendered with no edge, and 40 such puffs added 0.7 s to a 32-sample GPU
  frame; motes and sparks read as tiny emissive spheres. Under a dim world a volume with zero `emission` comes out grey.

## Emissive and metal under AgX

AgX desaturates bright emitters toward white: a saturated sign needs a modest
`Emission Strength` and a coloured area lamp beside it for the glow on the ground. Metals need
something to reflect — the sky world and the haze are what a worn metal mirrors; a metal under a
black world is a black shape.

## Verify before you finish

`scene_views(cameras="authored", times=[0.0])` and zoom one frame: a wall or a ground that is one
flat value where the brief's would not be needs variation; two copies you cannot tell apart
need variants; a grey default is a mesh that never got a material (the build output warns about
it by name). Then `scene_probe` for triangle counts — materials are free, geometry is not.

Node snippets — noise to bump, the slope mix, the Object Info tint, per-face slots, the
volume-only material, the socket rename table — in `references/principled-recipes.md`.
