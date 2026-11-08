# plan.json for `scene`

Fields: `title`, `summary`, `setting`, `mood`, `bounds`, `environment`, `interior`, `glazed`,
`zones[]`, `assets[]`, `effects[]`, `animation[]`, `cameras[]`, `acceptance[]`.  Write EVERY
number in the Y-up GLB frame (Y up, +Z towards the viewer, metres, ground around y = 0) for
both languages; `3dcode` converts the numbers it prints for a `scene_blender` build to Z-up.
State requirements for the brief's place — what is true, where, in what size — not
constructions.  Realistic scale: people 1.7 m, doors 2.1 m, steps 0.17 m, bench seats 0.45 m.

1. `setting` (place, era, weather, time of day) and `mood`.
2. `bounds` `{center, extents}`: the DETAILED area the gates measure against (layouts, placed
   assets, camera clearance), sized to the subject — a street long and narrow, a courtyard
   small.  It is not a cage: the world continues past it, and the environment builds that.
3. `environment`: ONE paragraph of numbers — sun/moon azimuth and elevation (the azimuth is
   the one number written in the build's own frame: compass 0 = +X, 90 = +Z in three.js,
   90 = +Y in bpy); the key light (hex + intensity for three.js; for Cycles a Sun in W/m² with
   a world strength — the starting pairs are in the `3d-lang-scene-blender` skill) and a fill
   hue away from the key's; colours as hex for three.js, (r, g, b) in 0–1 for bpy; atmosphere
   (fog colour + near/far, far ≥ 2 × the plan span; or a volume density with 1 / density ≥
   span / 1.5); the LANDFORM across the bounds with the ground height under each camera
   (camera heights are absolute); ground surfaces with colours; walkways as (x, z) routes with
   widths; how the world continues past the bounds in each direction and what the horizon is
   (for an interior: what the openings show); water height + colour — a river is ONE
   upstream-to-downstream path with non-increasing y, its banks on the terrain height.
4. `interior: true` when the view is from INSIDE a built space — the environment then owns
   floor, walls, ceiling and openings; an interior with no owner for walls and roof is judged
   a diorama on a flat plane.  `glazed: true` as well when the enclosure is glass on a frame:
   judged enclosed in glass from every camera, lit through the glazing with the frame's
   shadows inside, and what is seen through the glass is the brief's world, never a void or a
   flat colour.
5. `zones[]`: `name` PascalCase, `bbox` `{"center": [x, y, z], "extents": [w, h, d]}` (where
   that zone may place; neighbours overlap ≤ 1 m —
   and when built the zones read as ONE continuous place: nothing stops dead at a zone edge,
   scale and materials agree across them, no zone is left a block-out), `contents[]` (asset
   names only), `description` as numbers: what the place is and which cameras see it; the
   layout (what stands where, spacing, what faces what, what stands ON what; scattered clusters
   keep their footprints clear of every camera position); the counts the brief fixes and how
   much loose dressing beyond them (mid props, small props, ground cover — a market is crowded,
   a quarry floor bare); how repeated natural elements vary or why a row is regular.
6. `assets[]`: the things placed more than once or in more than one zone (a tree species, a
   lamp post, a crate); scenery one zone builds once stays in that zone's prose.  `name`, `kind`
   (`threejs` | `bpy` procedural module; `blender_glb` a hero built as an object and shipped as
   a GLB, costlier, may carry ONE keyframed motion whose pose at t = 1.5 s differs visibly from
   t = 0 — named in its description), `description` with construction numbers and colours,
   `approx_size_m` `[width, height, depth]` in metres (the built asset is measured against it — a
   stone lantern `[0.5, 1.6, 0.5]`, a bench `[1.6, 0.9, 0.6]`, a pine `[3, 7, 3]`), `instances_hint`.  An
   asset is judged as a prop at scene distance and is instanced: the description names its
   silhouette, the two or three features a person would name and its condition, not close-up
   hardware; real thickness where the thing has it (a fence is stakes and rails, not a coloured
   quad — crops are judged); fewer, better-shaped parts.  ≤ 15 000 triangles per module,
   ≤ 40 000 for a hero.  Variation lives inside an asset (`variant`), not in near-duplicate
   modules.  Ground, terrain, water, sky and what continues the world are NOT assets.
7. `effects[]`: `name`, `kind` (`glsl_material` | `particles` | `animated_geometry` |
   `postprocess`; for bpy `glsl_material` = a shader-node effect, `particles` = a keyframed
   instance cloud, `postprocess` = a volume such as mist or light shafts), `description`,
   `target` (zone / asset / scene).
8. `animation[]`: each line names what moves and an amplitude visible at 1024 × 576 between
   t = 0 and t = 1.5 s — foliage ±0.10–0.20 rad with a 1.6–3 s period; water a bob ≥ 0.04 m and
   a scrolling uv/normal at ≥ 0.15 m/s; particles ≥ 0.6 m per 1.5 s; boats/vehicles ≥ 1.5 m or
   a visible turn; lantern flicker ×0.75–×1.3.  Motion the brief names is visible from the
   establishing camera.
9. `cameras[]` (1–6): `name` ([A-Za-z0-9_-], a filename), `position` and `look_at` as
   `[x, y, z]`, `fov` (vertical, 5–150), `purpose` (what the shot is of and what the judge should see in it).
   Choose the shot set the place implies — a long vista, a close interior, a walk along a
   street, a top-down court.  Numbers are used literally: compute them from where the subject
   is and how big it is.  `cameras[0]` is the establishing shot (≥ 20 % of the frame is
   content, not sky or ground; others ≥ 10 %); every eye ≥ 0.3 m above the ground under it,
   ≥ 0.5 m from any surface, looking at content.
10. `acceptance[]`, each `{"id": "a1", "text": "...", "how": "visual" | "probe"}`: one per
    must-have, plus nothing floating or sunken (`how: "probe"`), the
    named motion visible (`probe`), no world edge from any camera (`visual`), lighting reads as
    the stated time of day (`visual`).  On this track `priority` is not yours: `validate-plan`
    sets every plan item to `should`, and the `must` items — each capping the run at 0.60 when
    unmet — are only those it derives from `spec.json`'s must-haves and must-nots.  So a hard
    requirement goes into `3dcode init --must-have`, one frame-confirmable fact per flag (no
    number, no named style, no "and"), not into the plan.  `a1` is still the premise — the kind
    of PLACE, in words false anywhere else ("the view is from inside an enclosed workshop").

The concreteness wanted:
* `environment`: "22:00, no moon: sky 0x0c1020; key = stall lamps 0xffc070 at 2.5 every 4 m
  along the lane; fill 0x203048 at 0.25; fog 0x141826 near 20 m far 160 m (span 80 m).  Lane:
  flat wet asphalt 0x24262a at y = 0, 6 m wide, running out of the bounds both ways; two-storey
  shophouses line it and continue past the bounds."
* a zone `description`: "The lane's west side, seen by LaneWalk and Overview: 7 stalls 4 m
  apart facing the lane, each with its lamp and 3–5 people in front; ~25 mid props, ~60 small
  props, no ground cover."  A bare zone is as concrete: "the sand wash, three fallen slabs
  1–2 m and nothing else — it reads as a canyon because it is empty."
* a camera `purpose`: "establishing: down the lane from its north end at eye height (subject:
  the stall row, ~30 m deep), signs stacking into the fog; no sky in frame."

Rules: every `contents` entry is an asset name; asset and zone names unique.
