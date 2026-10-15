# `bench/data/loop/<name>/ledger.jsonl` — the decisions of a tuning loop

One directory per loop (`bench.loop init --name <name>`), one append-only journal of every
`accept` / `rollback` the loop recorded (`python -m bench.loop accept|rollback --out … --iter N`).
It lives in the repo, not under the ignored `bench/out/`, because an accepted variant version is a
claim about the prompts (`codeverse3d/prompts/variants/<v>/variant.toml` `version`), and a claim
whose evidence is not in the tree is not reproducible.  The runs themselves stay under
`bench/out/<loop>/iter_NN/` (big, never committed); the row names what was measured and on what.

Written through `bench/_jsonl.seal_for_append` (a SIGKILL mid-append costs one row, never the file)
and read back as `bench.loop.LedgerRow`, one JSON object per line:

| field | meaning |
|---|---|
| `ts` | ISO-8601 UTC, when the decision was recorded |
| `iteration` | the `iter_NN` the decision is about |
| `languages` | the loop's languages (`scene_threejs`, `scene_blender`) |
| `variant` | the variant name (`argon`) |
| `version_from` → `version_to` | `variant.toml` version before and after: an `accept` bumps it by one; a `rollback` keeps it (working edits discarded) or, when the iteration was itself the latest accept, returns to `version_from` of that accept |
| `variant_hash` | the overlay fingerprint after the decision: `prompts.variants.variant_hash` — the number every run record of the iteration carries (`extra.variant.hash`, `SettingsSnapshot.variant_hash`; `README.md` is left out of it, so a README edit never moves it) — and the same `hash` the snapshot's `snapshot.json` records, so a snapshot pairs with the runs it was measured on.  Only an overlay the variant layer does not know (a tmp tree in the tests) falls back to the loop's own sha256[:12] over every file |
| `battery_sha` | sha256[:12] over the iteration's `battery_*.yaml` files — the prompts the verdict was measured on |
| `verdicts` | language → `keep` / `revert` / `inconclusive` from `compare_<lang>.json` (empty when the iteration was never compared: iteration 0, or an accept on diagnosis alone) |
| `decision` | `accept` or `rollback` |
| `cost_usd` | the iteration's spend, summed over its `results.jsonl` rows |
| `tree_commit`, `tree_dirty` | the harness checkout the iteration ran on (`git rev-parse HEAD`; dirty = tracked files modified under `harness/`) |
| `note` | `--note …`, free text: what the iteration changed |

The verdict rule behind `verdicts` is `bench/stats.decide` on the paired picked scores plus the
deterministic-gate veto (`bench/_loop_diag.VETO_RISE`: floating + sunken + interpenetration ERRORs per
run must not rise by more than one on average) — `eval/docs/EVAL.md` §10.  The snapshot each accept
takes (`bench/out/<loop>/snapshots/<v>_v<N>/`) is what a later `rollback` restores; `init` snapshots
the version it found so the first rollback has somewhere to go.

Before `accept` (and `init`) bump, snapshot or write anything, the skills overlay is PREFLIGHTED:
`routes.toml` must type-check (`codeverse3d.skills.parse_routes`, the parser `variants_table` runs on
it) and every bundle directory must pass `codeverse3d.skills.validate_bundle`.  A run never refuses a
broken overlay — a table that does not type-check is logged and the BASE table routes, an invalid
bundle is skipped — so an iteration whose overlay carries a typo measures nothing about the variant;
the loop is the one place that says no, and a row only ever records an overlay that would route.
