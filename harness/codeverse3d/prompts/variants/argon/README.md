# argon — the prompt overlay for gemini-4-argon on the scene track

This directory is a **variant** (D105, 2026-10-14): an overlay on `codeverse3d/prompts/`.
A file placed here shadows the base file of the same relative path for every run that
activates the variant — `scene_threejs/cookbook.md` here replaces the base cookbook,
`tracks/scene_zone.j2` the zone template — and a file that is not here is read from the base.
A template here may `include` or `extends` a base file (the variant's loader falls through to
the base directory).

The overlay ships **empty**: iteration 0 of the tuning loop (`eval/bench/loop.py`) measures the
base prompts under the variant plumbing, so the first accepted iteration is a measured delta,
not a guess.  The loop writes files here, bumps `version` in `variant.toml` on accept and
restores the previous snapshot on rollback.

* `variant.toml` — name (must equal this directory's name), version, the generator ids the
  variant was tuned for (informative), an optional `[knobs]` table (`skills_max`,
  `skills_only`, applied like a profile: a field the user stated wins).
* The skills half lives in `codeverse3d/skills/variants/argon/` (bundles that shadow base
  bundles by name, `routes.toml` with boosts / disabled rules / extra rows).
* `variant_hash("argon")` (`codeverse3d/prompts/variants.py`) covers both halves, this README
  excluded; the run record names the variant, its version and that hash.

How a run picks it: `3dcode make --variant argon`, `C3D_VARIANT=argon`, or
`C3D_VARIANTS='{"agy:gemini-4-argon": "argon"}'` (an exact generator id, looked up as typed
and then by `<kind>:<served model>` so the effort suffix does not matter).  Never a pattern
over the model id (Law 4).
