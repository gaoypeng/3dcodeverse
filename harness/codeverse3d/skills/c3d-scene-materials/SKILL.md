---
name: c3d-scene-materials
description: "Use in every scene_threejs build or refine session that authors surfaces - terrain, buildings, props, vegetation. The strongest 'plastic toy' tell is a flat albedo; the second is identical twins. States what the judge reads at crop scale (placeholder_material, flat_ground, monotonous_vegetation, undressed_scene, asset_quality_detail) and what the placement gate measures, then offers short optional practices with the shipped library's real API: surface variance, per-copy variation, roughness, edges near the lens, terrain that reads as a landform, vegetation variety, dressing and seating."
license: Apache-2.0
compatibility: three r0.182, headless WebGL; ACES tone mapping, GTAO + soft bloom supplied by the renderer.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Ported 2026-09-01 from the scene_multifile_graphics reference ledger (its 18-reviewer audit of 137 scenes; flat walls and duplicated assets are among its most-cited defects, and trees/rocks its two weakest measured classes). Not yet A/B-validated here; the sceneloop battery attaches our numbers. Condensed to requirements + optional practices 2026-10-15 after 60 argon scenes copied one recipe.'
  verified: "2026-10-15"
  target_metric: "flat_albedo_surfaces"
  target_direction: "down"
  target_unit: "large surfaces with zero value variance (prospective census)"
  target_measurable: "false"
  target_baseline: "n=0 — no measured runs yet (2026-09-01); the sceneloop A/B battery sets it"
---

# Surfaces that read as a place, not a render

The renderer supplies ambient occlusion, soft bloom and ACES tone mapping. What still
separates a photograph from a toy is AUTHORED: material variance and light contrast. Each
heading below is a requirement the judge or a gate reads, followed by a strong option or
two. The option is one way; the brief's material decides.

## No large surface reads as one flat value

Requirement: at crop scale no large surface reads as a single untextured value unless the
brief's material really is flat — new paint, a lacquered floor (judged: `placeholder_material`,
`flat_ground`). A strong option: the shipped procedural materials in `lib/materials.js`
(`plaster`, `brick`, `granite`, `cobble`, `asphalt`, `weatheredWood`, `soil`, `foliage` and
the rest) already carry low-contrast variance; `patchMicroBreakup` and `patchEdgeWear` from
`lib/surface_wear.js` chain onto any material you made yourself. A `noiseTexture` from the
same module as `roughnessMap` breaks a highlight into something other than a perfect oval.

## Copies do not read as twins

Requirement: copies of one asset do not read as identical at the authored cameras'
distance (judged: `monotonous_vegetation`, `asset_quality_detail`). Vary what the thing
really varies — a tree in scale, yaw and hue; a lamp post in nothing but yaw; a crate in
yaw and wear. `tint` from `lib/materials.js` makes a hue/lightness variant of a material
(clone per VARIANT, not per instance); `instanceAsset` from `lib/instancing.js` takes
per-placement scale and rotation.

## Roughness is the realism dial

The judge reads a surface with no authored roughness or colour as `placeholder_material`
("plastic", "one grey material") and a scene whose surfaces all share one finish under
`materials_shaders_effects`. Real surfaces are rarely uniform: wet stone is far glossier
than dry, weathered wood duller than painted, glass near-mirror with the environment map
doing the work. The value is what the brief's weather and age imply; a roughness map lets
it vary across the surface.

## Edges near the lens

Near an authored camera a razor 90° edge catches light like CAD (judged:
`asset_quality_detail`). A chamfer (`ExtrudeGeometry` with `bevelEnabled`), a bevelled
`makePaving` for cobbles, or smoothed normals is one way out; background boxes can stay
sharp.

## Terrain that reads as a landform

Requirement: the ground is the brief's landform, sampled by the one `heightAt`; a flat
plate that ends in shot and a noise blob both fail (`world_edge_visible`, `flat_ground`,
judged). A landform has a reason — a valley floor between two slopes, a bluff over water, a
terrace cut into a hillside, a plaza that is deliberately flat because it is paved. Strong
options when the place has relief: `ground` from `lib/terrain.js` with `relief` and a
seeded `rand`, returning the mesh and the height function in one; or your own heightfield
from domain-warped `fbm2` (`lib/noise.js`) with a power curve so lowlands stay flat and
ridges steepen, normals recomputed after displacement. Colour by slope and height with the
materials the place has — `patchSlopeSplat` and `patchTriplanar` from `lib/terrain_shade.js`
do that in the shader; a worn path or a wheel rut carved into `heightAt` itself lands
everything placed on it at the right height. The alpine sand/grass/rock/snow ramp is one
palette, right for an alpine brief.

## Vegetation variety

The judge's `monotonous_vegetation` is "one shape stamped many times — no variation in
silhouette, size or hue, and placed in a ring or a grid rather than clusters". So: more
than one species where the place has more than one (`makeTree` / `makeShrub` from
`lib/tree.js` for near trees, `makeCanopy` for every broadleaf crown and hedge,
`makeImposters` for a forest at distance), clusters with clearings rather than a lattice
(`scatterGrid` with jitter times a clump mask from `fbm2` is one option), and a ground
cover layer (`makeGrass`, `makeMeadow`) so plants grow out of the ground instead of standing
on it.

## Dressing

The judge's `undressed_scene` is "no small props or clutter — a showroom of a few large
objects". A place has the small things its use leaves behind: tools by the bench, cups on
the table, litter at the kerb, pebbles at the water, cables on the wall. Props against
something (a wall base, a path edge, under a bench) read as belonging; props alone on open
ground read as scatter. `patchSeamBand` and `patchTracks` (`lib/roadway.js`) dress where a
structure meets the ground, so the seam is not a hard undressed line (`flat_ground`).

## Seating is measured

Requirement: every placed copy's feet are within the placement gate's tolerance of what is
under them — the gate reports `floating` and `sunken` with the gap it measured, and
`interpenetration` where boxes overlap; instanced clouds are exempt and are never supports.
Seat on the shared `heightAt` (`seat` from `lib/place.js` seats an object on the surfaces
you pass); sink only what the thing really sinks (a rock, a post, a tree root) and by less
than the gate's `sunk_m`, or the WARN names it.

## Shadows resolve

Requirement: shadow edges resolve at the authored cameras (judged: `lighting_atmosphere`).
Whatever casts the shadows, fit its shadow camera to the span the authored cameras see,
not the whole world — a shadow map stretched over two kilometres has metre-wide pixels and
every shadow is mush. The starter `sunRig` fits its shadow camera to the `bounds` you pass.

## Verify before you finish

Zoom one frame to a large wall or the terrain: if the surface is one flat value, it needs
variance. Find two copies of the same asset: if you cannot tell them apart, variation is
missing. Look at one shadow edge: mush means the shadow camera is too wide. Read the
placement report for `floating` / `sunken` rows before the gate does.

`references/terrain-and-scatter.md` holds the domain-warp and scatter-mask notes.
