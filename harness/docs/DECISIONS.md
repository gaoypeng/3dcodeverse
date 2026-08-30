# Decisions (ADR-style)

Each entry: context → decision → consequence.  All dated 2026-08-23 unless noted;
"Δ" entries are deviations from the first-wave plan (`INTERFACES.md` as originally
written) that were accepted because the code works that way and the tests pin it.

## Laws (standing, from CLAUDE.md)

* **L1 Raw code only.**  Generated code never imports an SDK/helper; the harness owns
  wrappers and exporters (`languages/*/wrappers`, `runtime_js/`).  Consequence: every
  language needs a standalone wrapper that cannot import `codeverse` (Blender's python
  cannot see the package anyway).
* **L2 One contracts package, one conventions module.**  `contracts/` is data-only;
  `conventions.py` is the only place stating frames/units/naming.  Consequence: plan
  frames are the language's native frame (Z-up for blender/cadquery/urdf) and
  `check_contract` converts to the GLB frame (Y-up) using `LANGUAGE_FRAME`.
* **L3 Deterministic first, VLM for perception only.**  Score is computed in code
  from rubric weights; floors and caps are explicit; degraded verdicts are glitches.
* **L4 Typed, no regex-on-id, no god files.**  Files ≤ 2 000 lines, 3 000 absolute (owner, 2026-08-28 — was 1 500, before that ~400); two wrappers
  exceed it by design (`run_bpy.py` 418, `scene_host.mjs` 426).
* **L5 Cheap first.**  Lint → build → gates → montaged views → VLM.  `build` tool
  skips the build when lint has ERRORs.
* **L6 Every round = a git commit; every call = a Usage; every run = record.json.**

## Architecture decisions

* **D1 Single-shot lives in tracks, not agents (Δ).**  `single-shot:<provider>:<model>`
  ids are dispatched by `tracks/generation.generate()`; the agents registry only knows
  real agentic kinds.  Consequence: `Spec.backends.generator` accepts three id shapes;
  `is_single_shot()` is the only test.
* **D2 generate() writes code, build_with_repair() builds (Δ).**  The original
  `generate_files` import still exists, but generation never builds; `run_round`
  composes generate → commit → build+repair → gates → render → judge.  Consequence:
  repair prompts are error-focused (file:line, traceback tail, lint hints,
  keyword-matched cookbook section) and escalate temperature/thinking (single-shot)
  or add a "same error again" notice (agents) on identical error signatures.
* **D3 `RoundPolicy.max_rounds` counts refine rounds after the baseline.**  `3dcv make
  --rounds 2` = baseline + up to 2 refine rounds.  The rubric's `pass_threshold`
  overrides `RoundPolicy.target` when no explicit policy is injected.
* **D4 Parallel refine only when it is safe.**  Fan out only when ≥ 3 file-disjoint
  groups exist and every task maps to a file (threejs parts, scene zones/assets/env);
  single-file languages get one whole-object task.  Consequence: `Workspace` itself
  holds a per-root lock + index.lock retry (workspace.py); the older
  `generation.workspace_lock` layer is redundant and queued for removal.
* **D5 A refine round that changes no file is a plateau, not a crash.**  Emits
  `round.no_change` and stops with the best round so far.
* **D6 Finalise restores the best commit and rebuilds.**  Artifacts always match the
  delivered code; export copies `artifacts/object.glb` trusting that.
* **D7 Stage cache by input hash.**  `StageRunner` stores `stages/<name>.json` keyed by a
  hash of the inputs, so `3dcv resume` re-runs only what changed; the budget guard is
  restored from `run_state.extra.budget_snapshot` (`BudgetSnapshot`: spent usage, billed
  USD, call count, per-STAGE buckets, cumulative ACTIVE seconds — grace is
  deliberately NOT persisted; the per-ROUND bucket went 2026-08-30, nothing read it and
  `CallCost.round` in `telemetry/cost.jsonl` is the per-round record).  Prior spend and
  active minutes still count after a resume, so a raised `--max-minutes` grants only the
  difference.  The `spent_usage` mirror in `run_state.extra` went 2026-08-29 (write) and
  2026-08-30 (read): of the 282 run dirs that have it and no snapshot, only 41 can be
  resumed at all, and `check()` enforces on `billed_usd`, which
  `_reconcile_billed_from_ledger` restores from the ledger regardless.
  `run_state.extra` carries `budget_snapshot` and `spec_fingerprint` only: the
  candidate count lives in `spec.options.candidates`, the candidate table in
  `rounds/candidates.json` (which `record.extra.candidates` is read from).
* **D8 Events carry `event`, not `kind` (Δ).**  `EventLog.emit(event, **data)` so payloads
  may include `kind=` (round kind).  `3dcv status` and tests follow.
* **D9 `materialize_workspace` returns `None` (Δ 2026-08-30).**  It used to return a
  `Materialized` model carrying the body files, the ignore files, the cookbook path, the MCP
  command and a `warnings` list — and every production caller discarded it
  (`tracks/common.Services.materialize` is itself typed `-> None`), so the one signal on it, an
  unresolved cookbook, was written and never read.  That warning is now a `log.warning` on
  `codeverse.agents.materialize`, where a run log shows it; the model is gone.
  The codex `-c` overrides are NOT on it (`codex_overrides` was deleted
  2026-08-29): the codex backend builds `codex_mcp_overrides(mcp_command)` per session.
  The cookbook is copied to `ws/.3dcv/cookbook.md` (gemini-cli cannot read outside the
  workspace); the MCP argv defaults to `cli_common.default_mcp_command(ws)`, i.e.
  `sys.executable`, for every backend (the track-side bare-`python` literals are gone).
* **D10 gemini-cli needs a system-settings file (Δ).**  api-key auth +
  `dynamicModelConfiguration` (else silent model substitution) + `folderTrust.enabled=false`
  (else workspace MCP servers are silently disabled even with `--skip-trust`).  Served
  model is checked; mismatch → `exit_reason=model_substituted`.
* **D11 AgentJob carries typed fields only (Δ, revised 2026-08-29).**
  `round`, `kind`, `files_hint`, `language`, `track`, `read_only`, `mcp_command` … are
  typed `AgentJob` fields; the `extra` dict and its `_lift_legacy_extra` shim (and the
  unused `model` field) were deleted 2026-08-29 — nothing in the tree built one.
* **D12 api-agent exposes `build` under its real name; `run_build` is an alias (Δ).**
* **D13 Node ESM resolution via an import hook (Δ).**  `NODE_PATH` cannot resolve bare ESM
  specifiers; `run_node(three_hook=True)` adds `--import runtime_js/lib/resolve_three.mjs`;
  browser pages get an import map from `serve.cjs`.  `ThreeJsRuntime` writes
  `src/package.json {"type":"module"}` (idempotent).
* **D14 render_glb gained keyword-only extras (Δ).**  `anim_time, shadow, gpu, timeout_s,
  use_cache`; all original args/defaults unchanged; renders are cached by
  sha(glb)+params under `~/.cache/codeverse/renders/`.
* **D15 `check_contract(measurement, plan, *, language, tol_m)` (Δ).**  `language` is
  required for the Z-up → Y-up plan-box conversion.  `cross_section` adds `absolute`,
  `size`; `compare_silhouette` returns `ref_aspect, render_aspect, reliable` (IoU on
  bbox-normalised 256² masks).
* **D16 measure: link-hierarchy rule (Δ, fixed after the cabinet run).**  Parts are
  effective top-level nodes (descending through single geometry-less wrappers) with
  subtrees merged — except when the scene carries `metadata["links"]`, in which case
  each link is a part with only its own geometry.  Consequence: URDF links are plan parts
  for `check_contract` / `check_connectivity`; the cabinet run's "plan part missing"
  errors were an artefact of the pre-fix rule.
* **D17 URDF meshes are stored in raw Z-up link frames (Δ).**  `meshes/<link>.glb` are
  correct in any URDF loader (verified with yourdfpy); only the canonical `object.glb`
  is Y-up with joint extras.  Build `ok` is flipped only by rest-pose penetration > 5 mm
  (moved-pose overlaps and floating links are gate findings, not build failures).
* **D18 Authoring recipe for URDF (corrected in fix batch 1 — this is the ENFORCED
  recipe).**  Link meshes are built/exported in **WORLD coordinates** at q=0 (the
  authored pose = the plan's rest pose); every non-root link frame = its **pivot**;
  joint origin = pivot_child − frame_parent; visual AND collision `<origin xyz>` =
  −pivot (root `0 0 0`), verified by the FK-consistency check; `rpy="0 0 0"`
  everywhere.  Skeleton URDFs shift plan limits by `JointPlan.rest` (URDF limits =
  plan lower−rest .. upper−rest) so q=0 is the authored rest pose, and annotate the
  shift inline.  Lint accepts PascalCase or snake_case link names (plain
  identifiers); `world` is a reserved link name (trimesh's glTF reader stomps on it).
  The old prompts taught "visual origins zero", which the harness rejects — the
  contract/cookbook examples now all build clean through the real pipeline.
* **D19 Scene cameras are plain dicts `{name, position, lookAt, fov}`** (matches
  `scene_host.mjs` validation); `scene.js` is assembled deterministically by the harness
  from zones/env/assets; log-depth is opt-in; missing `/assets/*.glb` is a WARN, a
  missing module is an error.
* **D20 `probe_scene` returns `SceneProbeResult`** that unpacks as `(gate, census)`;
  use `.model_dump()`, not `dict()`.
* **D21 Judge wire schema has fixed keys (Δ).**  One property per criterion/acceptance id so
  flash-class models score every criterion; `apply_caps(rubric, overall, gates,
  acceptance_results, acceptance_items=None, *, console_errors=None) -> CapResult`; rubric
  caps use `when:` (PyYAML parses bare `on` as `True`).  Missing render files raise
  (pipeline bug), only model/parse failures produce degraded verdicts.
* **D22 Judge defaults (updated wave 3).**  `Settings.default_judge =
  gemini:gemini-3.1-pro-preview`; tracks construct `VlmJudge(rubric, model_id,
  n_samples=1)` (`RoundPolicy.judge_samples`).  Flash is the cheap in-loop option —
  use `n_samples ≥ 2` for decisions or re-judge with `3dcv judge --n 3`.
* **D23 Gemini specifics (Δ).**  tools + response_schema are mutually exclusive (schema
  dropped with a warning); Gemini 3.x cannot disable thinking; provider opaque state
  (thought signatures / Anthropic thinking blocks) is cached in-process by tool-call id
  because `ToolCallPart` has no slot.
* **D24 OpenAI via Chat Completions** (for `OPENAI_BASE_URL` compatibility); Anthropic uses
  adaptive thinking + `output_config.effort` on 4.6+ models.
* **D25 Flywheel shapes (Δ).**  `caption_sample(ws, record, model_id, *, model=None)`;
  `export_samples` keeps both `code.<ext>` and full `src/**`; parquet adds
  track/language/score/passed/generator columns; tar locators filled by `--pack`;
  `bench/` lives at repo root; `3dcv tools` is one command, not a sub-app; runs whose best
  round never built are skipped unless `--include-unbuilt`.
* **D26 Threejs exports strip textures; `userData.tick` cannot survive GLB** — recorded in
  the census (`tick_present`) for the judge/flywheel rather than faked.
* **D27 Blender wrapper resets the active layer collection** after clearing the scene (a
  deleted factory "Collection" leaves `bpy.context.collection == None`, breaking the
  common `bpy.context.collection.objects.link` pattern) — found live, pinned by tests.
* **D28 `StopPolicy` "max_rounds" maps to `RunStatus.PLATEAU`** (no dedicated status in
  contracts); the true reason is in `record.extra["stop_reason"]` / `RunState.stop_reason`.
* **D29 Best-of-N baseline + pairwise tie-break (second wave).**  `--candidates N` runs N
  baseline candidates in parallel throw-away sub-workspaces (`<ws>/_cand/c<k>`), ranks them
  by quick 4-view judge score → fewer gate errors, breaks near-ties pairwise, copies the
  winner back; after each round a score within `pairwise_margin` (0.03) of the best is
  treated as judge noise and a position-swapped `PairwiseJudge` decides (confidence ≥ 0.6
  to replace).  Every candidate is charged to the run budget.
* **D30 Judge protocol v2 + pro default (third wave).**  The judge sees ≤ 3 labelled
  2×2 montages (+ ≤ 2 detail crops, ≤ 1024 px) instead of sheet + 9 views; the wire
  schema is observe-then-score (defect checklist + acceptance before criteria);
  every rubric carries binary defects with penalties/caps.  Measured on the e2e
  rounds (`judges/calibration.py`, n=3): flash std 0.083 / pearson(gate errors,
  score) −0.33; pro std 0.030 / pearson +0.63 — so `default_judge =
  gemini-3.1-pro-preview` and flash's dynamic range is carried by the checklist.
  Consequence: `VlmJudge(max_images=)` is gone (`max_montages`, `detail_crops`);
  `JudgeInput.geometry_views` routes clay/normals views to a geometry-only montage.
* **D31 Textures are a derived asset pack, never the deliverable.**  The texture
  pass (material plan → tileable tiles → world-metre UVs → `object_textured.glb`)
  runs after finalise or standalone (`3dcv texture pass`); it never edits `src/` or
  `object.glb`, ships only when the before/after judge gate agrees (Δoverall ≥
  −0.01 AND materials criterion improved), and is recorded in
  `record.extra["texturing"]`.  Scenes get a named tile pack under
  `public/textures/` injected into prompts instead of per-part texturing.
* **D32 Graphics track = GL, not a 3D pipeline.**  `graphics` renders through
  moderngl in a subprocess per render (contexts are not thread/fork safe), has no
  GLB/measure step, and gates on frame statistics (`gl_frames`: NaN/black/blown/
  static/flicker).  The harness owns the GLSL header (`#version`, uniforms,
  `out vec4 fragColor`) — agents write shader bodies only; feedback shaders are
  simulated at 30 fps.  Rubric `shader_v1` (threshold 0.70).
* **D33 Export as authored (fix batch 1, all languages).**  No wrapper re-centres
  or grounds the object (threejs `--normalise` is an opt-in dataset flag); the
  build warns (`placement_offset`), the contract/connectivity gates report in the
  *authoring frame*, and the agent fixes the source.  Consequence: exact-per-plan
  parts always pass `check_contract` even when the union is off-centre.
* **D34 Key pool outcomes include `dead` (fix batch 1).**  Auth/permission errors
  (401/403, API_KEY_INVALID…) rotate away immediately and bench the key for an
  hour once another key works; 429s rotate to untried keys without consuming the
  retry budget.  A single dead key no longer fails 1/N of all Gemini calls.
* **D35 Strict schemas mirror pydantic nullability (fix batch 1).**
  `to_openai_strict_schema` no longer adds `null` to defaulted fields (defaults
  become description hints); `pricing.lookup_price` prefix fallback matches only
  version/date/channel suffixes, so sibling models never inherit a parent's price.

* **D36 Vote ties follow the representative sample (2026-08-25).**  Context: defect
  votes were majority with ties → present and acceptance votes majority with ties →
  False, so at `n_samples=2` ONE dissenting sample applied every penalty/cap and
  failed every must item — n=2 was strictly harsher than n=1 and n=3, and the
  `economy` profile (n=2) was not comparable with the others.  Decision: an exact
  tie (even n only) takes the *representative* sample's answer (the sample whose
  overall is closest to the mean — already the narrative source); ids decided this
  way are listed in `ScoreBreakdown.tie_broken`; `VlmJudge` warns on an even n.
  Consequence: P(item flagged) at n=2 equals n=1's while the continuous scores still
  average over both samples; odd n is unchanged.  **Amended 2026-08-30 for DEFECTS only:**
  an exact defect tie now reads as ABSENT.  The representative is whichever sample's
  overall sits nearer the mean — a float comparison, not evidence — and a 1-1 tie on a
  defect that caps the run at 0.7 was therefore a coin flip; the rubric already puts the
  burden on the defect ("present only when an image or a gate finding shows it").
  Acceptance ties still follow the representative: a must-item tie decides pass/fail, and
  that policy stays the owner's.  Moves nothing on disk (419/419 stored verdicts are n=1).
* **D37 The judge protocol is hashed (2026-08-25).**  Context: only the rubric YAML was
  hashed; the role prompt, the view-rig rules and the wire schema (whose field order
  IS the observe-then-score protocol — EVAL.md §6 measured σ 0.01 → 0.08 when it
  changed) were not, so a prompt edit left no trace in any record.  Decision:
  `prompt_builder.judge_prompt_hash(rubric)` = hash(system prompt + rig rules +
  wire-schema structure); stored in every `ScoreBreakdown.judge_prompt_hash` and,
  for runs, `record.prompt_hashes["judge"]` (`BaseTrack.after_plan`).  Per-run content
  is excluded so runs under one protocol share the hash.
* **D38 `harness_git_sha` resolves through git, not a `.git` probe (2026-08-25).**
  Context: `flywheel/record._harness_git_sha` looked for `harness/.git`; the repo's
  `.git` is one level up, so every record shipped an empty sha and EVAL.md §1.7's
  provenance was never met.  Decision: `git ls-files --error-unmatch` on the module
  itself (a wheel / venv copy must not borrow an unrelated repo's sha), then
  `rev-parse HEAD`, `-dirty` appended when tracked files under `harness/` are modified.
* **D39 The offline suite is run whole, locally; there is no CI (2026-08-25/26).**
  Context: `ci.yml` listed 7 of 24 directories, so 828 pure-python tests — every
  docs-vs-code drift guard among them — never ran on a PR.  Decision: the local pre-push
  suite is `pytest tests -m "not live"` over every test directory (add `and not blender
  and not node` for the pure-python subset), after `ruff check codeverse bench tests`;
  there is no CI — the owner removed the GitHub workflow on 2026-08-26 (6ac06a9), so this
  names a command every push is preceded by, not a job.

* **D40 A harness cell that waited instead of iterating is flagged, not trusted
  (2026-08-25).**  Context: under a day-long gemini-3.7-flash 503 storm 37 of 40 harness
  runs stopped on the 45-minute ceiling with 0–2 completed rounds while the 3 that met a
  calm window finished 2–4 rounds and scored 0.92–0.95; the paired mean over the stormed
  cells (0.516) measured the weather.  Decision: `compare_backends.flag_degraded` marks a
  harness cell that stopped for budget with money left, ≤ 1 completed round, after
  ≥ 40 min; the report counts them (`degraded` / `cut` columns, † per prompt) and
  `paired_compare` adds an `all −degraded` row.  Scores stay — the artifact is real.
* **D41 Failures of the arm are zeros; failures of the provider are dropped (2026-08-25).**
  Context: a `oneshot+repair` cell whose repair call died in a 503 storm was scored 0 on
  its pre-repair file; a harness run whose planner failed validation twice was recorded
  `error` / score None and vanished from the mean (5 of 14 articulated prompts).  Decision:
  a one-shot arm whose LAST attempt was lost to the provider is `infra_failed` (redone);
  a harness `PlanningError` is `no_code` / 0.0, like a one-shot answer in the wrong
  format; "Error creating WebGL context" (a saturated shared GPU) is an infra marker.
* **D42 `compare_backends` follows the battery's track (2026-08-25).**  Context: the
  fixed evaluator was static_object-only.  Decision: `FixedEvaluator(track=, language=)`
  picks the runtime from the battery and the rubric per cell (`_fixed_eval.rubric_for`, the
  `TRACK_INFO` row, through `judge_for(spec)` — the PR's `RUBRIC_BY_TRACK` table was folded
  into it on merge); articulated cells add
  the joint-sweep gate and the pose sheet read from the built URDF (no plan, every arm
  alike); one-shot arms get a language-aware minimal contract (the D18 recipe as rules,
  no example) and must answer with both files in the `=== FILE ===` envelope.
* **D43 Python lints never raise on unparseable source (2026-08-25).**  Context: a
  generated bpy file made CPython 3.11's `ast.parse` raise `SystemError` and the round died
  in `build_once`.  Decision: `_ast_lint.safe_parse` catches SyntaxError / RecursionError /
  SystemError / MemoryError / ValueError; every python lint reports a lint ERROR with a
  flatten-the-literal hint instead.  The forbidden-import floor is one set:
  `_ast_lint.BASE_FORBIDDEN_IMPORTS` (pickle, threading, importlib, webbrowser, ftplib,
  smtplib, …) — since 2026-08-29 urdf's `model.py` lint is on it too (those became
  ERRORs there) plus an explicit allow-list `{bpy, bmesh, mathutils, math, random, numpy}`;
  anything else imported is a WARN, a `codeverse` self-import an ERROR.
* **D44 The 2026-08-29 cleanup: delete, do not relocate (owner).**  Context: an external
  review of `codeverse` proposed executor / policy abstractions (a `RoundExecutor`, a
  `CandidateRunner`, a metering seam, a per-track prompt-policy object).  Decision: all
  rejected — each moved code between files without deleting any (L4: a merge must delete).
  What was done instead: (a) **candidates are rounds** — `run_candidate` / `quick_judge`
  went, a best-of-N candidate is `steps._run_round(kind="candidate")` in its sub-workspace
  with two knobs (`render=quick_render`, `geometry_views=False`), its own `events.jsonl`
  and a one-sample judge; (b) **one ledger writer** — `BudgetGuard` buckets and enforces
  only, `MeteredAgent` / `MeteredChatModel` write `telemetry/cost.jsonl`, and
  `BaseTrack.run` opens the run ledger itself (`per_call_metering`, `_ledger_row`,
  `run_ledger_path` deleted; `CV3D_COST_LEDGER=off` now really writes nothing);
  (c) **a garbage env flag is OFF** — `config.env_flag` reads on/off/1/0/true/false/yes/no;
  unset or empty is the caller's fallback (usually the Settings value), but an unparseable
  value warns and reads as OFF, so a typo in a bench command is a control run, never a
  silent arm (and never the variant, which a fallback of `True` would have handed it); (d) one MCP command (`default_mcp_command`, `sys.executable`),
  one JSON-envelope finder, one union-find, one sha256, one `ask_structured`, one
  `RENDER_MODES`; the retired mechanisms (`3dcv migrate-runs`, turntables + ffmpeg, the
  `CV3D_SYSPROMPT=v0` arm, `shader_presence` / counterfactual renders, the in-process
  turn-cap backstop, the concurrent-session registry) are gone with their docs.

* **D45 The 2026-08-30 batch: verified before applied.**  Context: a second external review
  listed six P1s, three P2s and eight deletions; separately a 12-subsystem comparison against
  `astra3d-brilliana` produced 36 claims about the static_object track.  Every item was
  reproduced or refuted by an independent auditor BEFORE any edit — of the review, two claims
  were refuted outright and four downgraded; of the comparison, ten of 36 survived and only one
  at high severity.  Decision: apply what reproduced, and prefer the fix that deletes.
  (a) **`material_index` has a warning and a recipe.**  One mesh object per plan part means
  per-feature colour inside a part is per-polygon `material_index` — a string that appeared
  NOWHERE in `codeverse/`, the prompts, the skills or the docs, while `_census.py` warned only
  about a mesh with NO material.  `h2h_microscope_x1b` authored 31 materials and 19 `tag_faces`
  calls that all silently no-opped (`bmesh.ops.create_cube` returns `{'verts'}`, so
  `res["faces"]` raises), shipped an 11-slot arm in one off-white, and lost 0.271 to
  `untextured_flat` (which fired on 4 of 12 h2h runs — all four losses).  The census now records
  `n_material_slots` + `material_indices_used` off the evaluated mesh and warns on unused slots
  and on an out-of-range index; the blender cookbook carries the recipe and the trap.
  Deliberately a build WARNING, not a gate: an agent may legitimately append a slot it fills a
  round later.
  (b) **The cost ledger has no process-wide default.**  `bind_run` / `set_default_ledger` /
  `default_ledger_path` are gone; `bound_run` and `bound_ledger` hold ContextVar tokens.  The old
  save-and-restore republished a sibling's run the moment the first parallel bench cell exited,
  so later rows landed in a finished run's file — the interleave 1756866 deleted `_active_ledgers`
  for, left standing on this half.  Standing rule: **a thread that may bill a model is spawned
  through `proc.fan_out`, never a bare pool** (`texturing/generate.py` was the last one).
  (c) **A paid verdict is never re-bought or reversed.**  `PairwiseNote` moved into
  `contracts/run.py` and onto `RoundRecord`; `reconcile_resume` replays it
  (`candidates.replay_best_round`).  Reconciliation may re-rank; it may not undo a comparison the
  run paid for.  **The guarantee is forward-only** — 0 of the 1 466 `rounds/rNN.json` on disk carry
  the field, so for every existing run `replay_best_round` degenerates to the pre-2026-08-30
  `BestSelector().pick`, which is why nothing on disk changed behaviour.  Of the 30 recorded dirs
  whose stored best disagrees with a plain re-rank, exactly one is re-enterable by `3dcv resume`
  (20 are `plateau`, refused without `--force`; 9 name generator kinds deleted 2026-08-28).
  KNOWN DIVERGENCE, unresolved: on the re-judge path `_promote_best` runs TWICE for one index
  (`lifecycle._round_loop`) — once with `judgment=None`, which lets `choose_best_round`'s
  `score is None` branch move the incumbent onto a round an earlier pairwise kept out, and again
  after `rejudge_round`.  `replay_best_round` models one promotion per index, so after a judge
  outage the live loop and the replay can pick different rounds (reproduced: live r1, replay r0).
  Replay's answer is the one that honours the earlier verdict; the live loop is what reverses it
  inside the unscored window.  Fixing the LIVE side is the change this law implies, and it is not
  made here.
  (d) **One render-cache authority.**  `spatial/tool_common.py` may name a deterministic out_dir;
  only `spatial/render.py` decides a PNG is still good.  The deleted `renderset.json` marker keyed
  on GLB size+mtime alone: it served stale views after a same-size rebuild, ignored a rig edit,
  and returned paths inside the ORIGINAL workspace for a copied run.
  (e) **`bench run --no-resume` is gone** — it dropped the recorded rows and then resumed the
  workspace anyway (`resume = ws.exists()` never read it), carrying the old spec, spend and clock.
  `--redo-status` is the one fresh rerun and it archives first.  `compare_backends --no-resume` is
  documented and has three readers, so it stayed — and now archives the cell's `run/` first.
  (f) **Anthropic/OpenAI derive each SDK call's timeout from the remaining `max_wait_s`**
  (`min(client timeout, max(20 s, remaining))`) instead of a fixed 600 s socket timeout under a
  deadline only checked between attempts.
  (g) **Prompt files have no size limits.**  `tests/prompts/test_files.py` capped a cookbook at
  950 lines, a contract at 150 and every prompt at >500 chars.  The blender cookbook hit the cap
  the day a measured failure earned it a new recipe, and the test's answer was to make the recipe
  worse.  Prompt text is not a resource to ration by line count (owner, 2026-08-30); the
  structural checks — chaptered, jinja-inert, states its units and frame, carries a runnable
  example — stay.
  (h) Deleted: `languages/file_lint.py` (orphaned when `api_tools.py` went 2026-08-28 — a vendor
  CLI's writes cannot be intercepted), the `Materialized` DTO (its one signal is now a
  `log.warning`), `AgentJob.images`, `SkillRead.first_seen_turn`, `RenderSet.turntable` (360
  records carry the key, 0 non-null), `attribute_changes(own_hints=)`, `BudgetGuard.charge`'s
  role/label/outcome + `by_round` (`CallCost` carries all three per call), the `spent_usage`
  resume fallback, the root-`cost_ledger.jsonl` leg of `existing_ledger_path` (zero real files
  exist; 677 symlinks), and `FakeImageModel` → `tests/texturing/conftest.py`.
  Also fixed: `kind="detail"` files as `Stage.REFINE`, `Stage.ASSEMBLE` maps to the generator
  role, and pairwise books `Stage.PAIRWISE` instead of hiding in the judge bucket.

* **D46 The judge is grounded in what the gates measure (2026-08-30).**  Context: a 7-study
  audit of 420 judged static_object rounds.  The judge did not SEE interpenetration, it READ the
  connectivity gate's text — P(flag | gate ERROR) = 69/69, 110 of 237 flags citing only WARNs the
  rubric excuses, a coin flip (117/97) where the gate was silent; the measured-absent veto had
  fired for it 0 times (cap rule `penetration_error`, defect `interpenetration`, matched on id)
  and an unrelated failing gate switched it off for floating too (53/62 blocked cases, mostly
  the invented-plan contract); one unverified must-item pinned 121/424 verdicts to a flat 0.600;
  and the gate itself was blind where a thin member's tip is a small share of a large part's
  surface (`PENETRATION_MIN_FRACTION`, a telescope spreader 4 mm into three legs at 0.7–1.0 % of
  600 samples).  Decisions, each measured offline on the stored corpus before it shipped
  (`bench/rejudge_offline.py`, 419 verdicts, $0):
  (a) **`CapRule.measures`** names the checklist defect a gate rule is the measurement of, and the
  object rubrics' `floating_part` / `penetration_error` watch `connectivity` only.  The veto is
  depth-aware: a penetration WARN measured at `VETO_PENETRATION_DEPTH_M` (5 mm) or deeper is not
  "measured absent" — of the 163 claims a WARN-blind veto switched off, 79 sat on a 5 mm+ overlap
  (median 4.8 mm), and since (d) a stile 17 mm through a seat is a WARN too.  Replayed with (b):
  213 verdicts move (0 down), mean +0.078, pass 15.0 % → 19.3 %, pearson(gate errors, overall)
  −0.219 → −0.291, vetoed on replay: interpenetration 93, floating 72.  articulated_v1 keeps
  `gate: "*"` — no single gate owns either measurement there (joint_sweep + connectivity).
  `Rubric.content_hash` hashes what the YAML declares, not the model's defaults, so a schema field
  (`measures`, `graded`) never re-keys recorded verdicts of an unchanged rubric.
  (b) **The acceptance cap is graded**: `cap + (1 − cap) · verified/total` must items.  Pass/fail
  unchanged (`must_missing` still fails).  Without it 98 of the rounds (a) frees are re-pinned to
  0.600 by the flat cap; with it the 0.600 spike falls 130 → 19.
  (c) **A defect vote tie is absent** (D36 amended); acceptance ties unchanged.  `SCORING_VERSION`
  = 2 is stamped on every breakdown; the replay tool holds identity only to same-version verdicts.
  (d) **The gate measures overlap where it is**: a dense pass on the AABB-overlap region, a
  `local_fraction` trigger beside the global one, and a `through_ratio` (2·depth / thickness of
  the part entered).  The ratio is MEASURED AND NAMED, never a severity: a 0.9 ERROR line was
  tried and flipped 9 runs of designed joinery (a boom 5 mm into a mast at 0.96, arch stretchers
  in 8 mm ribs at 0.91–0.97) because the number saturates at the container's mid-plane.  Severity
  stays on depth — and ERROR keeps its old meaning, deep AND a visible share of the part
  (`PENETRATION_MIN_FRACTION`): a deep overlap only the dense pass can see (a chair's rear stile
  17 mm through its seat, 1 % of either surface) is a WARN with every number, because ERROR by
  depth alone flipped 27 runs of such joinery pass→fail on a cap the eye cannot confirm.  Final
  corpus re-run over 217 GLBs: 441 pairs newly WARN, 0 newly ERROR, 50 WARN→ERROR (pairs the old
  gate already reported, now measured 9.4 → 11.3 mm), 14 runs whose gate now fails; median gate
  time 344 → 378 ms, p90 1381 → 1085 ms.
  (e) **The contact ledger is kept**: one INFO finding (`data.kind = "ledger"`) per report with every
  contact's gap, every overlap down to sub-threshold welds, the ground gap per part, and the plan's
  `attach_to` pairs measured regardless of the AABB prefilter (CONTACT / OPEN).  One resolver,
  `spatial.contract.planned_joins`: a child is offered against EVERY parent copy its box touches
  and the gate keeps what the exact distances say (each CONTACT row, else the nearest) — resolving
  the tie by list order printed "OPEN: Brace_1→Leg_0" for a join the plan never meant, and
  reducing per child hid a second, genuinely open one.  An open planned join is reported, not
  failed — that policy is the owner's.
  (f) **Orientation is measured** in the contract gate: an extents-permutation test in the plan
  frame (lying / stood → ERROR, turned → WARN), 0 flags over 629 corpus measurements, fires on
  brilliana's c-clamp and gate-valve.  Facing-away and upside-down are not visible to an AABB.
  (g) **World frames are composed by walking the graph's edge matrices**: trimesh 4.12 left a
  scene-root `pivot` rotation out of its descendants and re-parented a renamed duplicate node, so
  brilliana's three THREE-exported h2h sides measured lying on their side; a GLB with
  duplicate / unnamed nodes now lands a finding.  Our own exporters name every node uniquely
  (0 of 14 recorded threejs GLBs differ).
  (h) **The judge reads the ledger, not WARN prose**: `gates_section` renders one measured
  overlap line, a MEASURED STRUCTURE block with the plan's joins as contact / OPEN, and the
  lowest point above the floor — from the stored `GateReport`, so `3dcv judge` and calibration
  see what the in-run judge saw, and a pre-ledger round renders byte-identical (1 254
  "interpenetrate by ≈d mm" sentences on 241 stored rounds → 0; 171 rounds carry an OPEN
  planned join).  It rides on the v1 `judge_prompt_hash` — per-run text is not hashed — so its
  effect is measured by a paired re-judge, never by replay.
  (i) **`VlmJudge(fixed_order=True)`** and `calibration --fixed-order`: the per-sample shuffle
  measures view-order robustness; the loop's σ-keyed stops need the model's re-judge noise.
  Measured on 53 items × 3 ($7.79): σ 0.035 mean / 0.027 median / 0.060 p90 — the magnitude
  `JUDGE_NOISE` already tables, so the corpus's 0.072 round-to-round spread is generation, not
  the judge.  Rejected on the way: a through-ratio ERROR line (d), and a per-part ground gap
  for every part (a shade is legitimately 400 mm off the floor).

## Rejected / deferred

* A versioned `Spec`/`RunRecord`/`RunState` load-normaliser (rejected 2026-08-30: of the seven
  compatibility branches it was meant to absorb, only two are schema-shaped — 14 lines — so it
  would add an abstraction and delete nothing, L4).
* Deleting `spatial/sections.py` (rejected 2026-08-30: it IS the registered `cross_section` MCP
  tool, imported at module level by `spatial/tools.py`; removing it takes down the MCP server).
* Registering `single-shot` as a CodingAgent kind (rejected: it has no tools/session).
* A free-form `dict` judge schema (rejected: flash skips criteria).
* Storing URDF meshes Y-up and converting on load (rejected: breaks foreign loaders).
* Per-asset judging for threejs scene assets (deferred: needs a `render_asset` hook).
* Proposed and not yet done (owner-level files): `ToolCallPart.extra` for provider
  state; `n_samples` in Settings.  (Done since first written: `Workspace._git`
  lock + index.lock retry; the `languages/**` package-data globs.)
