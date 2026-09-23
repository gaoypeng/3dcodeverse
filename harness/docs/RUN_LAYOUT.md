# Run directory layout

One run = one directory (`runs/<slug>/`, `eval/bench/out/<battery>/runs/<slug>/`).
It answers three different questions, and since 2026-08-23 it keeps them in
three separate buckets:

| bucket | question | contents |
|---|---|---|
| `deliverable/` | **what did I get?** | ONE round, the one a pick chose (`3dcode pick`; `3dcode make` picks by score after the run): its code snapshot, its artifact (`object.glb` / `robot.urdf` + `meshes/` / `frames/` + `preview.gif` / the scene bundle), its contact sheet, `captions.json`, `manifest.json` |
| `evidence/` (= `artifacts/`) | **why should I believe it?** | every round's own build (`rNN/`), `renders/rNN/`, `gates/rNN/`, `judge/rNN.json`, `measurement.json`, `census.json`, `textures/`, `tool_renders/` |
| `telemetry/` | **what did it cost and how was it configured?** | `cost.jsonl` (the ledger), `settings.json`, `cost.json`, plus `events.jsonl`, `run_state.json`, `stages/`, `trajectories/` |

The run's **identity** stays at the root, where every tool has always looked for it:

```
runs/<slug>/
  spec.json  plan.json  record.json          identity: what was asked, planned, produced
  selection.json                             which round deliverable/ holds and how it was chosen (after a pick)
  run_state.json  events.jsonl               (physical home; also linked from telemetry/)
  src/            live working tree (git; every round is a commit; ends at the LAST round)
  public/         (scene) compiled assets
  deliverable/    (a) the hand-over — one round, written by a pick
  evidence/  ->  artifacts/                  (b) the proof
  artifacts/      the last round's build, rNN/ (each round's own), renders/ gates/ judge/ measurement.json …
  telemetry/      (c) the accounting
    cost.jsonl  settings.json cost.json
    events.jsonl -> ../events.jsonl
    run_state.json -> ../run_state.json
    stages -> ../stages
    trajectories -> ../trajectories
  stages/  rounds/  trajectories/            physical homes (unchanged)
  _assets/ _cand/ AGENTS.md GEMINI.md CLAUDE.md .gemini/settings.json …
```

## Why the aliases point that way

Only **one** physical copy of anything exists; the second name is a relative
symlink.  Which side is physical was not a matter of taste:

* **`artifacts/` stays physical, `evidence/` is the alias.**  Six packages
  (`spatial`, `languages`, `tracks`, `texturing`, `judges`, `flywheel`) write
  into `ws.artifacts`, and ~50 completed runs have absolute `artifacts/...`
  paths frozen inside `record.json`, `rounds/rNN.json` and `views.json`.
  Moving the bytes would either break those paths or force a rewrite of files
  we treat as immutable evidence.  The alias costs nothing and gives the
  directory listing the name that explains what is inside.
* **`events.jsonl` / `run_state.json` / `stages/` / `trajectories/` stay at the
  root, `telemetry/` links to them.**  `events.jsonl` is opened in append mode
  by long-lived writers and `run_state.json` is rewritten atomically
  (tmp + rename) — a rename onto a *symlink* replaces the link, so the symlink
  must be on the alias side, never on the side that gets written.
* **`deliverable/`, `telemetry/settings.json` and `telemetry/cost.json` are real
  files.**  They are *derived*: `telemetry/` is rebuilt from the run's own
  artifacts, trajectories and events every time the run is finalised,
  `deliverable/` from a round's commit and its `artifacts/rNN/` every time a round
  is picked, and both on the fly by `3dcode show` / the exporters for a run that
  predates them — old runs are read as-is, never rewritten.  `deliverable/` is self-contained so it can be zipped and handed to
  someone (the flywheel exporter only falls back to it when git cannot answer).
  `telemetry/cost.jsonl` is the ledger itself, appended while the run happens, and has
  one name: the root `cost_ledger.jsonl` and `telemetry/usage.jsonl` symlinks runs written
  before 2026-09-22 carry are no longer written or read.

`.gitignore` inside the run ignores `deliverable/`, `telemetry/` and `evidence`
(as well as `artifacts/`, `stages/`, `trajectories/`, …) so the derived buckets
never enter the code snapshot.

## Every round, kept (`artifacts/rNN/`)

The run ends at its LAST round (`src/` and the canonical `artifacts/` files), and since
2026-09-22 no round is chosen during the run.  So every round keeps what a hand-over of
it needs, the moment it builds (`record.deliverable.keep_round_artifacts`, called by
`tracks/steps._run_round` after the round's commit):

```
artifacts/rNN/      real copies, never hard links (the next build replaces the canonical files)
  object.glb  object.stl  object.step            (object tracks; whichever the language exports)
  robot.urdf  meshes/<link>.glb                  (articulated)
  frames_sheet.png  preview.gif                  (graphics)
```

The rest of a round is already per round: its code is its git commit (a scene's hand-over
— `src/` + `public/`, GLB assets included — is nothing else), its renders and contact sheet
are `renders/rNN/` (a graphics round's judged frames among them), its gates `gates/rNN/`,
its verdict `judge/rNN.json` and `rounds/rNN.json`.  The census, `build.json`, the `.blend`
and the measurement are evidence of the LAST build only.  A round that did not build keeps
nothing.  A run recorded before 2026-09-22 has no `rNN/`: its canonical files are the build
of the best round its `record.json` still names (`round_outputs` reads it that way).

## `deliverable/`

Built for ONE round by `codeverse3d.addons.select.package(run_dir, round)` →
`record.deliverable.build_deliverable(ws, record, round)` — from that round's commit and its
`artifacts/rNN/`, never a rebuild.  `3dcode make` / `resume` package `select.pick(by="score")`
after the run (highest effective judged score, ties → fewer gate errors → the earlier round;
`--no-pick` skips it); `3dcode pick <slug> [--by pairwise] [--round N] [--texture]` re-packages
any round:

```
deliverable/
  src/**            raw code at the round's commit (public/** too, for scenes)
  object.glb  object.stl  object.step  object_textured.glb
  robot.urdf  meshes/<link>.glb                 (articulated)
  frames/*.png  frames_sheet.png  preview.gif   (graphics: the round's judged frames)
  textures/<id>.png                              (when a texture pass of THIS round shipped)
  sheet.png         the round's contact sheet
  captions.json     {detailed, instruction, factory} when captioned
  manifest.json     every file with role, size, sha256
```

Beside `record.json`, `selection.json` says which round that is and why: `{round, method
(score | pairwise | round), scores (every round's effective score), textured, selected_at}`.
`addons.select.summarise` reads it, so every reader reports the round that was handed over.

Every file is hashed with sha256 and is a real copy (never a hard link), so editing the
hand-over folder can never touch the evidence.  A file over 128 MB (or a bucket over
512 MB) is left out and named in `manifest.skipped`.  Rebuilding is idempotent: the manifest
keeps its previous `generated_at` when the content is unchanged, so re-running the packager
produces no diff.

## `telemetry/`

* **`settings.json`** (`SettingsSnapshot`) — model id, thinking level,
  temperature and judge sample count per role; rounds + budget; candidates;
  seed; texture flag; rubric name **and its content hash**; prompt / cookbook
  hashes; tool versions (python, blender, node, three, chrome, puppeteer);
  harness version + git sha; key-pool size; **price-table hash**; resolved
  render and limit settings.  Sampling values are what the track stamped as it ran,
  `record.extra["sampling"]` (the planner's `plan_temperature(track)`, the judge's own
  temperature / thinking / samples; `source="run"`) — a vendor CLI owns its knobs, and a
  record written before 2026-09-22 carries no stamp, so they are left empty, never guessed
  (they were read off the call sites' signature defaults, which reported the default judge
  sample count whatever the run used).
* **`cost.jsonl`** — THE ledger: one priced row per model call or CLI session, in the
  **`codeverse3d.cost` ledger format** (`CallCost`: tokens, unit prices, price
  provenance, stage, role, outcome), written by the metered models and agents while the
  run happens.  It is the only record of money; a run without one (recorded before
  2026-08-23) has no rows — nothing is reconstructed.
* **`cost.json`** (`CostSummary`) — the run-layout view of those rows, computed by the
  ledger's own `summarise`: the total (= `record.total_usage`, the ledger's sum at list
  price — post-run work a pick buys joins it the next time the run is packaged), tokens, per
  stage (`plan / assets / env / zones / baseline / refine / repair / judge / pairwise /
  texture / …`), per role, per model, per round, and the run's minutes against
  `max_minutes`.

## `record.json`

One additive block, `None` on older records:

* `record.telemetry` — `RunTelemetry(settings, cost)` (a record written before 2026-09-22 also carries
  `environment` and `files`, which nothing read; they are ignored on load).

The hand-over is described by `deliverable/manifest.json` (`RunDeliverable(round, commit,
code_source, entry, files[path, role, bytes, sha256], total_bytes, skipped, generated_at)`) and
`selection.json`, not by the record: since 2026-09-22 the record names no best round and
carries no `deliverable` block.  A `record.json` written before that still validates — its
`best_round` / `baseline_score` / `final_score` / `deliverable` keys are ignored, a
`passed` / `plateau` status reads `stopped`, and a manifest that says `best_round` loads as
`round`.

## Reading a run

```
3dcode show <slug>                 # STATUS / DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS
3dcode show <slug> --section cost  # just the token price + key step settings
3dcode status <slug>               # = show --section status: the rounds, run_state, the lock holder, events
```

`3dcode show` works on both layouts: when a run has no `telemetry/` yet the cost
and settings block is computed on the fly (read-only) from the trajectories,
judge verdicts and events already on disk.  Its STATUS section needs no `record.json`, so it
also reads a run in flight (the other three sections wait for the record).

For the money itself across many runs — waste, $ per run, price provenance — use the cost
package's own command, `3dcode cost show <runs-dir>`.
`3dcode show` is the single-run view; both read the same ledger rows.

## Back-compatibility contract

* Old runs load unchanged — `load_record`, `3dcode status`, `3dcode judge`,
  `3dcode render`, `flywheel export/pairs/index`, `gallery` all work with no
  `deliverable/` or `telemetry/` present.
* Consumers resolve artifacts through
  `record.deliverable.deliverable_path(ws, name)`: `deliverable/<name>` first,
  `artifacts/<name>` second.  A reader that wants a given ROUND's file asks
  `addons.select.round_file(ws, rnd, name)` (`round_outputs`), never `artifacts/<name>`.
* `flywheel export` reads the packaged code snapshot only when git cannot answer
  (`meta.code_source == "deliverable"`), and adds a compact `meta.telemetry`
  digest when the run has one (`{}` otherwise).
* the gallery (`3dcode gallery build`) links the packaged
  `deliverable/object.glb` and `cost.json` when they exist, else the picked round's
  `artifacts/rNN/` copy, else `artifacts/`; the per-stage cost line appears only for
  runs that carry telemetry.
