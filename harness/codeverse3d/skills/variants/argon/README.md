# argon — the skills overlay for gemini-4-argon on the scene track

The skills half of the `argon` variant (D105, 2026-10-14; the prompts half is
`codeverse3d/prompts/variants/argon/`).  Three things may live here:

* **Bundles** — `c3d-<name>/SKILL.md` (+ `references/`), shadowing a base bundle of the same
  name or adding a new one.  `codeverse3d.skills.all_skills()` is the base library updated by
  this directory while the variant is active; a bundle here is validated like a base one.
* **`_claims/<name>.toml`** — a shadowed bundle's pinned numbers; looked up here first, then in
  the base `_claims/`.
* **`routes.toml`** — `[[boost]]` rows (a priority delta per skill, clamped so a standing row
  never reaches the gate-fired floor of 90 and a fired row never drops under it), `[[disable]]`
  rows (base rule ids to skip) and `[[route]]` rows (extra typed routes, appended after the base
  table).  Parsed with `tomllib` into typed records; a row that does not type-check is logged
  and the base table routes.

It ships empty: iteration 0 of `eval/bench/loop.py` measures the base library and table.
The run record's `extra.variant.hash` covers this directory (README excluded) together with
the prompts overlay.
