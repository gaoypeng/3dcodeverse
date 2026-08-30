# Run directory layout

One run = one directory (`runs/<slug>/`, `bench/out/<battery>/runs/<slug>/`).
It answers three different questions, and since 2026-08-23 it keeps them in
three separate buckets:

| bucket | question | contents |
|---|---|---|
| `deliverable/` | **what did I get?** | the code snapshot of the best round, the canonical artifact (`object.glb` / `robot.urdf` + `meshes/` / `frames/` + `preview.gif` / the scene bundle), the best contact sheet, `captions.json`, `manifest.json` |
| `evidence/` (= `artifacts/`) | **why should I believe it?** | `renders/rNN/`, `gates/rNN/`, `judge/rNN.json`, `measurement.json`, `articulation.json`, `census.json`, `textures/`, `tool_renders/` |
| `telemetry/` | **what did it cost and how was it configured?** | `settings.json`, `cost.json`, `usage.jsonl`, plus `events.jsonl`, `run_state.json`, `stages/`, `trajectories/` |

The run's **identity** stays at the root, where every tool has always looked for it:

```
runs/<slug>/
  spec.json  plan.json  record.json          identity: what was asked, planned, produced
  run_state.json  events.jsonl               (physical home; also linked from telemetry/)
  src/            live working tree (git; every round is a commit)
  public/         (scene) compiled assets
  deliverable/    (a) the hand-over
  evidence/  ->  artifacts/                  (b) the proof
  artifacts/      renders/ gates/ judge/ measurement.json …
  telemetry/      (c) the accounting
    settings.json cost.json usage.jsonl
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
  files.**  They are *derived*: rebuilt from the run's own git history,
  artifacts, trajectories and events every time the run is finalised, and on
  the fly by `3dcv show` / the exporters for a run that predates them — old
  runs are read as-is, never rewritten.  `deliverable/` is self-contained so it can be zipped and handed to
  someone (the flywheel exporter only falls back to it when git cannot answer).
  `telemetry/usage.jsonl` is the one file that can be either: a real
  reconstructed ledger, or a symlink to the run's live `telemetry/cost.jsonl`
  when it has one (the root `cost_ledger.jsonl` is itself a symlink alias; a
  real root file exists only in runs before 2026-08-23) — same rule as
  everywhere else, one physical copy.

`.gitignore` inside the run ignores `deliverable/`, `telemetry/` and `evidence`
(as well as `artifacts/`, `stages/`, `trajectories/`, …) so the derived buckets
never enter the code snapshot.

## `deliverable/`

Built by `codeverse.flywheel.deliverable.build_deliverable(ws, record)` from the
**best round** (`record.best_round`, else the highest judged score):

```
deliverable/
  src/**            raw code at the best round's commit (public/** too, for scenes)
  object.glb  object.stl  object.step  object_textured.glb
  robot.urdf  meshes/<link>.glb                 (articulated)
  frames/fNN_tT.png  frames_sheet.png  preview.gif   (graphics)
  textures/<id>.png                              (when the texture pass shipped)
  sheet.png         the best round's contact sheet
  captions.json     {detailed, instruction, factory} when captioned
  manifest.json     every file with role, size, sha256
```

Every file is hashed with sha256.  Small files (all code) are real copies, so
editing the hand-over folder can never touch the evidence; binaries of 256 KB and
up (frame stacks, GLBs, GIFs) are **hard-linked** to their evidence copy when the
filesystem allows — same bytes, one block on disk, and `cp` / `tar` / `rsync`
still produce a standalone copy (the six graphics bench runs grow from 140 MB to
144 MB, not to 210 MB).  A file over 128 MB (or a bucket over 512 MB) is left
out and named in `manifest.skipped`.  Rebuilding is idempotent: the manifest
keeps its previous `generated_at` when the content is unchanged, so re-running
the packager produces no diff.

## `telemetry/`

* **`settings.json`** (`SettingsSnapshot`) — model id, thinking level,
  temperature and judge sample count per role; rounds + budget; candidates;
  seed; texture flag; rubric name **and its content hash**; prompt / cookbook
  hashes; tool versions (python, blender, node, three, chrome, puppeteer);
  harness version + git sha; key-pool size; **price-table hash**; resolved
  render and limit settings.  Sampling values are read from the call sites'
  typed defaults (`ApiAgentOptions`, `VlmJudge.__init__`, `planner.plan`); a
  value a call site hard-codes per task is left empty with `source` saying so,
  never guessed.  A track may publish real values as
  `record.extra["sampling"][<role>]` and they win (`source: observed`).
* **`usage.jsonl`** — one priced row per model call, in the
  **`codeverse.cost` ledger format** (`CallCost`: tokens, unit prices, price
  provenance, stage, role, outcome).  There is exactly one ledger in the
  harness: when the run wrote a live one (`<run>/telemetry/cost.jsonl`; the root
  `cost_ledger.jsonl` is a symlink alias, a real root file only in pre-2026-08-23 runs)
  `telemetry/usage.jsonl` is a symlink to it, otherwise
  `cost.reconstruct.reconstruct_run` rebuilds the rows from the trajectories,
  the recorded judge verdicts and the priced events.  This bucket never
  re-implements pricing or the double-counting rules — it only gives the ledger
  a stable place in the run directory (`telemetry.files["usage_source"]` says
  `live` / `reconstructed` / `unavailable`).
* **`cost.json`** (`CostSummary`) — the run-layout view of those rows: total vs
  budget, wall clock vs `max_minutes`, tokens, per stage (`plan / assets / env /
  zones / baseline / refine / repair / judge / pairwise / texture / …`), per
  role, per model, per round, plus `ledger_usd`, `unattributed_usd` (the
  ledger's residual row) and `post_run_usd` (priced calls outside the run total,
  e.g. a texture pass that ran after the loop).  `record.total_usage` stays the
  authority on what the run cost.

## `record.json`

Two additive blocks, both `None` on older records:

* `record.telemetry` — `RunTelemetry(settings, cost, environment, files)`;
* `record.deliverable` — `RunDeliverable(best_round, commit, code_source, entry,
  files[path, role, bytes, sha256], total_bytes, skipped, generated_at)`.

Everything else is unchanged, so a `record.json` written before this layout
still validates and every consumer keeps working.

## Reading a run

```
3dcv show <slug>                 # DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS
3dcv show <slug> --section cost  # just the token price + key step settings
3dcv status <slug>               # unchanged (+ a pointer to `3dcv show`)
```

`3dcv show` works on both layouts: when a run has no `telemetry/` yet the cost
and settings block is computed on the fly (read-only) from the trajectories,
judge verdicts and events already on disk.

For the money itself across many runs — waste, $ per passing artifact, price
provenance — use the cost package's own command, `3dcv cost show <runs-dir>`.
`3dcv show` is the single-run view; both read the same ledger rows.

## Back-compatibility contract

* Old runs load unchanged — `load_record`, `3dcv status`, `3dcv judge`,
  `3dcv render`, `flywheel export/pairs/gallery/index` all work with no
  `deliverable/` or `telemetry/` present.
* Consumers resolve artifacts through
  `flywheel.deliverable.deliverable_path(ws, name)`: `deliverable/<name>` first,
  `artifacts/<name>` second.
* `flywheel export` reads the packaged code snapshot only when git cannot answer
  (`meta.code_source == "deliverable"`), and adds a compact `meta.telemetry`
  digest when the run has one (`{}` otherwise).
* the gallery (`3dcv gallery build` / `flywheel gallery`) links the packaged
  `deliverable/object.glb` and `cost.json` when they exist and falls back to
  `artifacts/` otherwise; the per-stage cost line appears only for runs that
  carry telemetry.
