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
3. `environment`: ONE paragraph of numbers — sun/moon azimuth (compass 0 = +X, 90 = +Z for
   three.js; 90 = +Y when written for bpy) and elevation; the key light (hex + intensity for
   three.js; a Sun of W/m² + world strength for Cycles: pairs that landed in the exposure band —
   day 3–5 W/m² at world 0.4–0.5, dusk 1–1.5 at 0.2, moon 0.1–0.3 at 0.1) and a fill hue away
   from the key's; atmosphere (fog colour + near/far, far ≥ 2 × the plan span; or a volume
   density with 1 / density ≥ span / 1.5); the LANDFORM across the bounds with the ground height
   under each camera (camera heights are absolute); ground surfaces with colours; walkways as
   (x, z) routes with widths; how the world continues past the bounds in each direction and
   what the horizon is (for an interior: what the openings show); water height + colour.
4. `interior: true` when the view is from INSIDE a built space — the environment then owns
   floor, walls, ceiling and openings; `glazed: true` as well when the enclosure is glass on a
   frame.  Six interior runs with no owner for walls and roof were judged "a diorama on a flat
   plane" (0.0–0.3).
5. `zones[]`: `name` PascalCase, `bbox` (where that builder may place; neighbours overlap ≤ 1 m),
   `contents[]` (asset names only), `description` as numbers: what the place is and which
   cameras see it; the layout (what stands where, spacing, what faces what, what stands ON
   what); the counts the brief fixes and how much loose dressing beyond them (mid props,
   small props, ground cover — a market is crowded, a quarry floor bare); how repeated natural
   elements vary or why a row is regular.
6. `assets[]`: `name`, `kind` (`threejs` | `bpy` procedural module; `blender_glb` a hero built
   as an object and shipped as a GLB, costlier, may carry ONE 2 s keyframed motion),
   `description` with construction numbers and colours, `approx_size_m` = width × height ×
   depth (the built asset is measured against it), `instances_hint`.  ≤ 15 000 triangles per
   module, ≤ 40 000 for a hero.  Variation lives inside an asset (`variant`), not in near-
   duplicate modules.  Ground, terrain, water, sky and what continues the world are NOT assets.
7. `effects[]`: `name`, `kind` (`glsl_material` | `particles` | `animated_geometry` |
   `postprocess`), `description`, `target` (zone / asset / scene).
8. `animation[]`: each line names what moves and an amplitude visible at 1024 × 576 between
   t = 0 and t = 1.5 s — foliage ±0.10–0.20 rad; water scroll ≥ 0.15 m/s or a bob ≥ 0.04 m;
   particles ≥ 0.6 m per 1.5 s; boats/vehicles ≥ 1.5 m or a visible turn; lantern flicker
   ×0.75–×1.3.  Motion the brief names is visible from the establishing camera.
9. `cameras[]` (1–6): `name` ([A-Za-z0-9_-], a filename), `position`, `look_at`, `fov`
   (vertical, 5–150), `purpose`.  Numbers are used literally: compute them from where the
   subject is and how big it is.  `cameras[0]` is the establishing shot (≥ 20 % of the frame is
   content, not sky or ground; others ≥ 10 %); every eye ≥ 0.3 m above the ground under it,
   ≥ 0.5 m from any surface, looking at content.
10. `acceptance[]`: one per must-have, plus nothing floating or sunken (`how: "probe"`), the
    named motion visible (`probe`), no world edge from any camera (`visual`), lighting reads as
    the stated time of day (`visual`).  ANY unmet `must` caps the run at 0.60, so `must` only
    restates a hard requirement of the brief that one frame can confirm and names no incidental
    detail; `a1` is the premise — the kind of PLACE, in words false anywhere else ("the view is
    from inside an enclosed workshop").  A `must` with a number, a named style or an "and" is
    split or demoted to `should`.

Rules: every `contents` entry is an asset name; asset and zone names unique.
