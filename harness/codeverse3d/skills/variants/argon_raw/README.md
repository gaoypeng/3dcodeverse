# argon_raw — the skills overlay for scene_blender in raw mode

The skills half of the `argon_raw` variant (D113, 2026-10-17; the prompts half is
`codeverse3d/prompts/variants/argon_raw/`).  Raw mode ships no harness bpy library, so the three
base bundles that taught the library's calls are shadowed here by copies that teach the raw bpy
each call stood for, with the same names, frontmatter rules and measured numbers:

* `c3d-bpy-scene-placement` — seating from world-space vertices, the pairwise box check;
* `c3d-bpy-scene-lighting` — sky, Sun / area lamps and fog volumes through `bpy.data`;
* `c3d-bpy-scene-materials` — multi-scale procedural surfaces wired as nodes.

Their pinned numbers are the base bundles' (`_claims/` is looked up here first, then in the base
library, and these copies keep every claimed sentence).  `routes.toml` has no rows: the base
table routes the shadowed bundles by name.  The run record's `extra.variant.hash` covers this
directory (README excluded) together with the prompts overlay.
