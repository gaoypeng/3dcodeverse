# argon_raw — scene_blender in raw mode for gemini-4-argon

This directory is a **variant** (D105) for the owner's game-world demo of 2026-10-17 (D113):
ALL scene modeling code is written by `agy:gemini-4-argon`.  It is an overlay on
`codeverse3d/prompts/`: a file placed here shadows the base file of the same relative path for
every run that activates the variant, and a file that is not here is read from the base.

What it changes:

* `variant.toml` `[knobs] scene_lib = false` — **raw mode**: the scene_blender skeleton writes no
  `src/lib/`, its stubs import nothing of it, a `lib` import is a lint ERROR, and no prompt
  carries the library's interface or catalog rows.  A user-stated `C3D_SCENE_LIB` still wins.
* `scene_blender/system.md`, `contract.md`, `asset.md`, `effects_catalog.md` and the bpy stage
  templates (`tracks/scene_{env,zone,asset,refine}_bpy.j2`) and planner (`tracks/plan_scene_bpy.j2`)
  — copies of the base files with every library reference replaced by what the gate measures
  and what the author must do in raw bpy / bmesh, plus the realism requirements (multi-scale
  procedural surfaces, organic silhouettes, a world that continues past the content).
* `system/harness_contract.md` — the base contract minus its line that every scene language
  ships a library.
* The skills half lives in `codeverse3d/skills/variants/argon_raw/` (lib-free copies of the three
  `c3d-bpy-scene-*` bundles; `routes.toml` with no rows).
* `variant_hash("argon_raw")` (`codeverse3d/prompts/variants.py`) covers both halves, this README
  excluded; the run record names the variant, its version and that hash.

How a run picks it: `3dcode make "..." --track scene --language scene_blender --variant argon_raw`,
or `C3D_VARIANT=argon_raw`.  Never a pattern over the model id (Law 4).  It is meant for
scene_blender only: a scene_threejs run under it reads the base three.js prompts and its library.
