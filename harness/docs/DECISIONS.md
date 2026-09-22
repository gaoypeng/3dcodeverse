# Decisions (ADR-style)

Each entry: context → decision → consequence.  All dated 2026-08-23 unless noted;
"Δ" entries are deviations from the first-wave plan (`INTERFACES.md` as originally
written) that were accepted because the code works that way and the tests pin it.
An entry keeps the decision, the one or two numbers that carried it, and a pointer to the full
account (about 12 lines since 2026-09-22); a number kept inline is one that exists nowhere else.
Pointers: EVAL, PAPER_WRITING = `eval/docs/*.md`; COST, RUNBOOK, ARCHITECTURE, INTERFACES =
`harness/docs/*.md`.

## Laws (standing, from CLAUDE.md)

* **L1 Raw code only.**  Generated code never imports an SDK/helper; the harness owns
  wrappers and exporters (`languages/*/wrappers`, `runtime_js/`).  Consequence: every
  language needs a standalone wrapper that cannot import `codeverse3d` (Blender's python
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
* **D3 `RoundPolicy.max_rounds` counts refine rounds after the baseline.**  `3dcode make
  --rounds 2` = baseline + 2 refine rounds — exactly 2 since D80, which also deleted
  `RoundPolicy.target` (the rubric's `pass_threshold` now sets only the judge's per-round `passed`).
* **D4 Parallel refine only when it is safe.**  Fan out only when at least
  `RoundPolicy.parallel_min_tasks` (default 2) file-disjoint groups exist and every task
  maps to a file (threejs parts, scene zones/assets/env);
  single-file languages get one whole-object task.  Consequence: `Workspace` itself
  holds a per-root lock + index.lock retry (workspace.py).
* **D5 A refine round that changes no file stops the run, not a crash.**  Emits
  `round.no_change`; since D80 the status is `no_change` and the workspace stays at the last round.
* **D6 Finalise leaves the workspace at the LAST round (D80; was: restores the best commit).**  Only
  a round cut mid-way (clock, crash) leaves `src/` past it, so that is restored and rebuilt;
  artifacts match the last round's code, and every round's own build is kept in `artifacts/rNN/`.
* **D7 Stage cache by input hash.**  `StageRunner` stores `stages/<name>.json` keyed by a
  hash of the inputs, so `3dcode resume` re-runs only what changed; the budget guard is
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
  may include `kind=` (round kind).  `3dcode status` and tests follow.
* **D9 `materialize_workspace` returns `None` (Δ 2026-08-30).**  It used to return a
  `Materialized` model carrying the body files, the ignore files, the cookbook path, the MCP
  command and a `warnings` list — and every production caller discarded it
  (`tracks/common.Services.materialize` is itself typed `-> None`), so the one signal on it, an
  unresolved cookbook, was written and never read.  That warning is now a `log.warning` on
  `codeverse3d.agents.materialize`, where a run log shows it; the model is gone.
  The codex `-c` overrides are NOT on it (`codex_overrides` was deleted
  2026-08-29): the codex backend builds `codex_mcp_overrides(mcp_command)` per session.
  The cookbook is copied to `ws/.3dcode/cookbook.md` (gemini-cli cannot read outside the
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
* **D13 Node ESM resolution via an import hook (Δ).**  `NODE_PATH` cannot resolve bare ESM
  specifiers; `run_node(three_hook=True)` adds `--import runtime_js/lib/resolve_three.mjs`;
  browser pages get an import map from `serve.cjs`.  `ThreeJsRuntime` writes
  `src/package.json {"type":"module"}` (idempotent).
* **D14 render_glb gained keyword-only extras (Δ).**  `anim_time, shadow, gpu, timeout_s,
  use_cache`; all original args/defaults unchanged; renders are cached by
  sha(glb)+params under `~/.cache/codeverse3d/renders/`.
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
  use `n_samples ≥ 2` for decisions or re-judge with `3dcode judge --n 3`.
* **D23 Gemini specifics (Δ).**  Gemini 3.x cannot disable thinking.  **Removed 2026-09-21**
  (c5ffcd4, with the function-calling surface the api-agent had served): tools + response_schema
  being mutually exclusive (schema dropped with a warning), and the in-process cache of provider
  opaque state (thought signatures / Anthropic thinking blocks) by tool-call id that stood in for a
  slot on `ToolCallPart`.
* **D24 OpenAI via Chat Completions** (for `OPENAI_BASE_URL` compatibility); Anthropic uses
  adaptive thinking + `output_config.effort` on 4.6+ models.
* **D25 Flywheel shapes (Δ).**  `caption_sample(ws, record, model_id, *, model=None)`;
  `export_samples` keeps both `code.<ext>` and full `src/**`; parquet adds
  track/language/score/passed/generator columns; tar locators filled by `--pack`;
  `eval/bench/` lives at repo root; `3dcode tools` is one command, not a sub-app; runs whose best
  round never built are skipped unless `--include-unbuilt`.
* **D26 Threejs exports strip textures; `userData.tick` cannot survive GLB** — recorded in
  the census (`tick_present`) for the judge/flywheel rather than faked.
* **D27 Blender wrapper resets the active layer collection** after clearing the scene (a
  deleted factory "Collection" leaves `bpy.context.collection == None`, breaking the
  common `bpy.context.collection.objects.link` pattern) — found live, pinned by tests.
* **D28 Superseded by D80:** `StopPolicy` is gone and `RunStatus` names every stop itself
  (`max_rounds`, `budget`, …); a record's `passed` / `plateau` status reads as `stopped`.
* **D29 Best-of-N baseline + pairwise tie-break (second wave).**  `--candidates N` runs N
  baseline candidates in parallel throw-away sub-workspaces (`<ws>/_cand/c<k>`), ranks them
  by quick 4-view judge score → fewer gate errors, breaks near-ties pairwise, copies the
  winner back.  Every candidate is charged to the run budget.  The per-round pairwise
  tie-break went with D80 (and the candidates' near-tie pairwise with it): pairwise is now
  `3dcode pick --by pairwise`, after the run (margin 0.03, confidence ≥ 0.6, `addons/select.py`).
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
  runs after finalise or standalone (`3dcode texture pass`); it never edits `src/` or
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

* **D36 Vote ties follow the representative sample (2026-08-25).**  Defect votes were majority with
  ties → present and acceptance votes majority with ties → False, so at `n_samples=2` ONE
  dissenting sample applied every penalty/cap and failed every must item — n=2 was strictly harsher
  than n=1 and n=3, and the `economy` profile (n=2) was not comparable with the others.  Decision:
  an exact tie (even n only) takes the *representative* sample's answer (the sample whose overall
  is closest to the mean — already the narrative source); ids decided this way are listed in
  `ScoreBreakdown.tie_broken`; `VlmJudge` warns on an even n.  P(item flagged) at n=2 equals n=1's
  while the continuous scores still average over both samples; odd n is unchanged.  **Amended
  2026-08-30 for DEFECTS only:** an exact defect tie reads as ABSENT — "nearer the mean" is a float
  comparison, not evidence, so a 1-1 tie on a defect that caps the run at 0.7 was a coin flip, and
  the rubric puts the burden on the defect ("present only when an image or a gate finding shows
  it").  Acceptance ties still follow the representative: a must-item tie decides pass/fail, and
  that policy stays the owner's.  Moves nothing on disk (419/419 stored verdicts are n=1).  Pinned:
  `tests/judges/test_defects.py`; context: PAPER_WRITING §0.2.
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
  and not node` for the pure-python subset), after `ruff check codeverse3d tests` (and `cd eval && ruff check . && python -m pytest` for the evaluation code);
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
  Added 2026-08-28/29 (pro planner + gemini-cli runs): a Gemini stream that exceeds its
  attempt budget, a 504 "Deadline expired", and a `finish_reason=PROHIBITED_CONTENT`
  (the content filter tripping mid-JSON on a furniture plan) are provider failures too.
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
  anything else imported is a WARN, a `codeverse3d` self-import an ERROR.
* **D44 The 2026-08-29 cleanup: delete, do not relocate (owner).**  An external review of
  `codeverse3d` proposed executor / policy abstractions (a `RoundExecutor`, a `CandidateRunner`, a
  metering seam, a per-track prompt-policy object); all rejected — each moved code between files
  without deleting any (L4: a merge must delete).  Done instead: (a) **candidates are rounds** —
  `run_candidate` / `quick_judge` went; a best-of-N candidate is
  `steps._run_round(kind="candidate")` in its sub-workspace with two knobs (ARCHITECTURE §2,
  INTERFACES orchestrator/); (b) **one ledger writer** — `BudgetGuard` buckets and enforces only,
  `MeteredAgent` / `MeteredChatModel` write `telemetry/cost.jsonl` and `BaseTrack.run` opens the
  run ledger (`per_call_metering`, `_ledger_row`, `run_ledger_path` deleted; COST §12); (c) **a
  garbage env flag is OFF** — `config.env_flag` warns and reads an unparseable value as OFF, so a
  typo in a bench command is a control run, never the variant; (d) one MCP command
  (`default_mcp_command`, `sys.executable`), one JSON-envelope finder, one union-find, one sha256,
  one `ask_structured`, one `RENDER_MODES`.  Retired with their docs: `3dcode migrate-runs`,
  turntables + ffmpeg, the `C3D_SYSPROMPT=v0` arm, `shader_presence` / counterfactual renders, the
  in-process turn-cap backstop, the concurrent-session registry.

* **D45 The 2026-08-30 batch: verified before applied.**  A second external review (six P1s, three
  P2s, eight deletions) and a 12-subsystem, 36-claim comparison of the static_object track against
  `astra3d-brilliana` were reproduced or refuted by an independent auditor BEFORE any edit (review:
  two claims refuted outright, four downgraded; comparison: ten of 36 survived, one at high
  severity).  Decision: apply what reproduced, and prefer the fix that deletes.
  (a) **`material_index` has a warning and a recipe.**  One mesh object per plan part makes
  per-feature colour per-polygon `material_index`, which nothing taught: `h2h_microscope_x1b`'s 31
  materials (19 `tag_faces` calls, all silent no-ops) shipped on slot 0 and lost 0.271 to
  `untextured_flat`, which fired on 4 of 12 h2h runs — all four losses.  The census warns on unused
  slots and an out-of-range index — a build WARNING, not a gate (a slot may be filled a round
  later) — and the blender cookbook carries the recipe (ARCHITECTURE §4,
  `tests/blender_cadquery/test_material_slots.py`).
  (b) **The cost ledger has no process-wide default** (`bind_run` / `set_default_ledger` /
  `default_ledger_path` gone; `bound_run` and `bound_ledger` hold ContextVar tokens —
  `cost/context.py`, COST §12).  Standing rule: **a thread that may bill a model is spawned through
  `proc.fan_out`, never a bare pool** (`texturing/generate.py` was the last one).
  (c) **A paid verdict is never re-bought or reversed.**  Then: `PairwiseNote` on `RoundRecord`,
  replayed by `reconcile_resume` (`candidates.replay_best_round`).  Since D80 the loop buys no
  pairwise verdict at all; `3dcode pick --by pairwise` caches its one verdict per (pair, model) in
  `artifacts/judge/rAA_vs_rBB_pairwise.json` and re-reads it — same law, one place.  The replay and
  its known live-vs-replay divergence (a re-judge promoted twice) went with the best round.
  (d) **One render-cache authority.**  `spatial/tool_common.py` may name a deterministic out_dir;
  only `spatial/render.py` decides a PNG is still good (why the `renderset.json` marker went: the
  `cached_render_glb` docstring in `spatial/tool_common.py`).
  (e) **`bench run --no-resume` is gone**; `--redo-status` is the one fresh rerun and it archives
  first; `compare_backends --no-resume` stays and archives the cell's `run/` first (RUNBOOK §7.u).
  (f) **Anthropic/OpenAI derive each SDK call's timeout from the remaining `max_wait_s`**
  (`min(client timeout, max(20 s, remaining))`, `models/parts.attempt_timeout_s`), not a fixed
  600 s socket timeout under a deadline only checked between attempts.
  (g) **Prompt files have no size limits** (owner, 2026-08-30): `tests/prompts/test_files.py`
  capped a cookbook at 950 lines, a contract at 150 and every prompt at > 500 chars, and the
  blender cookbook hit the cap the day a measured failure earned it a new recipe.  The structural
  checks — chaptered, jinja-inert, states its units and frame, carries a runnable example — stay.
  (h) Deleted: `languages/file_lint.py` (orphaned when `api_tools.py` went 2026-08-28), the
  `Materialized` DTO (D9), `AgentJob.images`, `SkillRead.first_seen_turn`, `RenderSet.turntable`,
  `attribute_changes(own_hints=)`, `BudgetGuard.charge`'s role/label/outcome + `by_round`, the
  `spent_usage` resume fallback, the root-`cost_ledger.jsonl` leg of `existing_ledger_path`, and
  `FakeImageModel` → `tests/texturing/conftest.py`.  Also fixed: `kind="detail"` files as
  `Stage.REFINE`, `Stage.ASSEMBLE` maps to the generator role, and pairwise books `Stage.PAIRWISE`
  instead of hiding in the judge bucket.

* **D46 The judge is grounded in what the gates measure (2026-08-30).**  A 7-study audit of 420
  judged static_object rounds: the judge READ interpenetration off the connectivity gate's text
  instead of seeing it (P(flag | gate ERROR) = 69/69); the measured-absent veto was dead; one
  unverified must item pinned 121/424 verdicts to a flat 0.600; and the gate was blind to a thin
  member's tip (`PENETRATION_MIN_FRACTION`).  Every change was measured offline on the stored
  corpus first (`eval/bench/rejudge_offline.py`, 419 verdicts, $0).  Details: EVAL §6 and the
  constants' docstrings in `spatial/connectivity.py` and `judges/rubrics.py`.
  (a) **`CapRule.measures`** names the checklist defect a gate rule is the measurement of; the
  object rubrics' `floating_part` / `penetration_error` watch `connectivity` only (articulated_v1
  keeps `gate: "*"` — no single gate owns either measurement there: joint_sweep + connectivity).
  The veto is depth-aware: a penetration WARN at `VETO_PENETRATION_DEPTH_M` (8 mm, not 5 — at 5
  the pipe tee's 5.8 mm designed branch socket was marked) or deeper is not "measured absent".
  With (b) the replay moves 230 verdicts, none down, pass 15.0 % → 21.0 %.  `Rubric.content_hash`
  hashes what the YAML declares, so a schema field (`measures`, `graded`) never re-keys recorded
  verdicts.
  (b) **The acceptance cap is graded**: `cap + (1 − cap) · verified/total` must items; pass/fail
  unchanged (`must_missing` still fails); the 0.600 spike falls 130 → 20.
  (c) **A defect vote tie is absent** (D36 amended).  `SCORING_VERSION` = 2 is stamped on every
  breakdown; the replay tool holds identity only to same-version verdicts.
  (d) **The gate measures overlap where it is**: a dense pass on the AABB-overlap region, a
  `local_fraction` trigger beside the global one, and a `through_ratio` (2·depth / thickness of the
  part entered) that is MEASURED AND NAMED, never a severity (a 0.9 ERROR line flipped 9 runs of
  designed joinery).  ERROR keeps its old meaning — deep AND a visible share of the part — because
  ERROR by depth alone flipped 27 runs of joinery pass→fail on a cap the eye cannot confirm.
  (e) **The contact ledger is kept**: one INFO finding (`data.kind = "ledger"`) with every contact,
  every overlap down to sub-threshold welds, the ground gaps, and the plan's `attach_to` pairs as
  CONTACT / OPEN via the one resolver `spatial.contract.planned_joins` (INTERFACES spatial/).
  (f) **Orientation is measured** in the contract gate (lying / stood → ERROR, turned → WARN; 0
  flags over 629 corpus measurements); facing-away and upside-down are not visible to an AABB.
  (g) **World frames are composed by walking the graph's edge matrices**
  (`spatial/measure.world_transform`; EVAL §8.7's mis-measured brilliana sides); a GLB with
  duplicate / unnamed nodes lands a finding.
  (h) **The judge reads the ledger, not WARN prose** (`gates_section`, ARCHITECTURE §6); it rides
  on the v1 `judge_prompt_hash`, so its effect is measured by a paired re-judge, never by replay.
  (i) **`VlmJudge(fixed_order=True)`** and `calibration --fixed-order`: the per-sample shuffle
  measures view-order robustness; the loop's σ-keyed stops need the model's re-judge noise (σ 0.035
  over 53 items × 3, the magnitude `JUDGE_NOISE` already tables).  Rejected on the way: a
  through-ratio ERROR line (d), and a per-part ground gap for every part (a shade is legitimately
  400 mm off the floor).
  (j) **An OPEN planned join stays report-only** (census: EVAL §6).  The class an ERROR exists to
  catch — a child that touches nothing and is unflagged — is empty on 217 runs; an ERROR's whole
  bite would be 46 passing runs whose open rows read as planner noise — the invented `attach_to`
  edge becoming law (D45's failure verbatim), plus a PROTECTED refine round each.  Severity may not
  be smuggled through the kind string either: `rubrics._finding_tokens` matches cap kinds against
  data/message/target, so `kind=open_join` caps nothing while `kind=no_contact` silently adds the
  0.6 cap AND blocks the measured-absent veto.  Re-open only if a future battery's ledger census
  shows a touches-nothing-unflagged row, or a paired A/B (~$5.50, 46 runs × 2 arms) shows the judge
  under-prices the OPEN lines it already reads.

* **D47 Judge payload v3: the 14-view rig through the montage machinery (2026-08-31).**  Supersedes
  D30's ≤ 3-montage cap.  `OBJECT_VIEWS` is the brilliana 14-view rig (three rings ±30° + eye-level
  cardinals + poles); the legacy clay cameras are `OBJECT_CLAY_VIEWS` (clay `top` el 88, the rig's
  90); `OBJECT_RANK` packs montage 1 as {front_right_high, back_left_high, top, bottom} so a cap
  truncation still sees the underside; `MAX_MONTAGES` / `Judge.montages` / every profile's
  `judge_montages` go 3 → 5 (at 3 the low ring + poles are silently dropped); `OBJECT_VIEWS_QUICK`
  stays 4 views, so contact sheets, the texture pass, candidates and scene assets keep their cost.
  Why: of a 4-arm A/B (42 items × n=3 pro, ≈ $47) the only comparison that survives multiplicity
  is same-cap C−A **+0.038** (SE 0.013), with `untextured_flat` cap fires 11 → 3; 14 single views
  (arm B) cost more for a negative delta.  A payload experiment must measure the exact grouping it
  ships (grouping alone moved the mean −0.044; the shipped arm Cprod's numbers are the operative
  ones).  Rejected: appended underside singles (Cplus), and t = 0 (σ 0.037 → 0.022, but the mean
  shifts −0.037 and pearson(gate errors) drops −0.195 → −0.126): re-keying every σ threshold to buy
  half the noise is a bad trade; **t = 0.2 stays**.  Details: EVAL judge log 2026-08-31, COST §14.

* **D48 Conditional cross-section slices + provenance elicitation on gate-ERROR rounds
  (2026-08-31).**  The judge's interpenetration verdicts were reading comprehension of the gate
  text (D47's provenance pass: pure-view TP 0/14 in every arm).  On a round whose connectivity gate
  carries an ERROR, `spatial.sections.judge_slices` cuts the round's GLB (`JudgeInput.glb_path`) on
  the two vertical centre planes, with a typed `SliceManifest` beside the PNGs so citations stay
  auditable; the slices go AFTER the montages/crops, with the F2/F3 rig text and one neutral
  elicitation sentence in the DEFECT CHECKLIST bullet.  `Settings.judge.slices` = "on-error"
  (default) | "off" (`C3D_JUDGE__SLICES`); clean rounds are byte-identical (test-pinned) and
  `judge_prompt_hash` is untouched.  Why: of three iterations under a pre-registered bar (a
  guard-verified image-cited interp majority on 8 of the 14 dirty rows) only this conditional one
  reached it (2/23 citations fabricated, both guard-caught), and the dirty verdict got cheaper
  ($0.154 vs $0.172).  The watch item, answered 2026-08-31: the slice IMAGES carry the move, not
  the sentence; hard-cap loss falls; the shipped arm is the more ACCURATE one (net true marks +6,
  net false −2); two slices-only sentences were rewritten (the
  `[outline only — not filled; NOT a hole]` legend, and an in-plane caveat that points at the
  shaded and geometry views instead of forbidding a mark).  Rejected: a revert (it nets +6 true
  marks), dropping the elicitation (inert on dirty rounds, and it produces the citations the audit
  was read from), a cap-interaction guard (no such cap fired).  Forcing the sentence onto clean
  rows costs −0.092: the on-error gate is load-bearing.  Details: EVAL judge log (2026-08-31 ×2;
  the `untextured_flat` fix both arms prompted, 2026-09-01), COST §14.

* **D49 An articulated plan is checked for geometric self-consistency before any code is written —
  measured, ships OFF (2026-08-28/29).**  **Removed 2026-09-21**: `tracks/plan_checks.py`, the
  re-ask in `tracks/planner.py`, the `C3D_PLAN_GEOMETRY` switch and their tests are deleted.  It
  re-asked the planner (event `plan.geometry`) with box-arithmetic complaints about attachment
  gaps, hinge pivots and swept collisions.  On `compare_art_v4` (14 paired prompts, gemini-cli
  generator, judge pro n = 3) it fired in 7/14 cells for Δ +0.064 [−0.188, +0.315] — with
  gemini-cli generation round-0 sweep errors are near zero either way.  The numbers stand:
  PAPER_WRITING §4.
* **D50 Joint-sweep findings reach the fixer aggregated per link pair (2026-08-28).**  One
  compare_art_v3 run produced 59 penetration findings for a handful of pairs — one per sampled pose
  — and `build_refine_instructions` de-duplicates by (target, kind) keeping the FIRST pose's line,
  so the agent saw a pair's shallowest instance and the loop burnt its budget without converging.
  `spatial/joints_sweep.aggregate_findings` merges a pair's poses into one line (how many poses,
  worst depth and at which q, rest count), keeps the deepest `MAX_PAIR_FINDINGS = 8` ERRORs and
  folds the rest into one WARN `penetration_summary`; `default_joint_sweep` applies it and adds
  `buried_links` (a link whose sampled surface lies ≥ 98 % inside another at rest is an ERROR —
  invisible to every view, reported "missing" by the judge).  The raw sweep report is unchanged.
  It ships on rationale, not on a resolved score delta (the companion repairs bundle:
  PAPER_WRITING §4).
* **D51 The effect library is HARNESS-OWNED STARTER CODE, not an SDK import (2026-09-01).**
  `scene_multifile_graphics` shipped 52 three.js effect modules (21.7k lines) this harness had no
  equivalent of, and every scene run re-derived worse versions inside a 60-minute budget.
  **Decision — an amendment to L1, not an exception**: L1 forbids the AGENT from reaching outside
  its workspace for someone else's abstraction; modules the HARNESS writes into `src/`, verifies on
  its own renderer and forbids the agent to rewrite are what `src/recipes.glsl` already was for
  `glsl_shader`.  So `src/lib/*.js` ships into every scene workspace, is listed in
  `HARNESS_OWNED_SRC[SCENE_THREEJS]` (a DIRECTORY-prefix entry, predicate `is_harness_owned`), and
  a post-session write to it is reverted; generated code stays raw three.js + GLSL.  Shipped 52
  modules / 28.8k lines (44 / 24.8k since D74), ported against the reference's own test per module
  and adapted to OUR renderer contract (exposure 1.0 not 1.1; log-depth off; their
  renderer-contract assertions dropped); every module rendered and LOOKED at (53 before/after
  pairs, 77 night renders), aesthetics measured, not asserted (linear albedo 0.02–0.8, hue variance
  inside every effect, emissives 1.5–4, fresnel on water/glass, fog in the sky's hue family); one
  test file per module under `tests/scene_runtime/lib/` (678 tests at landing; the GPU compile in
  them is real — godrays: 21 programs, 6 custom materials).  Companion: a post chain for scene
  renders (`runtime_js/lib/browser/post.js`, ON; `--no-post` / `C3D_POST=0`; 18 tests) whose bloom
  is SELECTIVE (an emission mask), because on this renderer a daylight sky dome reaches 1.883
  linear while the brightest authored emissive is 1.231 — the reference's 0.85 bright-pass cut a
  daylit grass frame's saturation 0.3132 → 0.0699.  Same-day consolidation: `_probe.compile_scene`
  replaced 32 hand-rolled workarounds (843 lines deleted); plan-mode `write_skeleton` ships
  `src/lib/`; `signage.js`'s `unpkg.com` pin went; the workspace lint and static shader audit skip
  `src/lib/` (3 lint ERRORs + 12 WARNs + a uTime false positive had outranked the agent's own
  shader error); `patchStandard` gained `roughnessBody` / `metalnessBody` / `outputBody` hooks
  (`<color_fragment>` reaches only the albedo, so a 30 % rust crust could only be sold by dematting
  100 % of the material).  **Not A/B-judged** (rendered and eyeballed, deterministic metrics, 2 809
  offline tests green): no paired battery has scored scenes with the library against scenes without
  it, so "it raises scene scores" is unproven (the hook change was isolated: the six
  shader.js-dependent modules — grass, water, canopy, godrays, neon, clouds — against a hooks-cut
  control build give mean luminance identical to five decimals on all 12 frames).  Details:
  ARCHITECTURE §4, `prompts/scene_threejs/effects_catalog.md`, `tests/scene_runtime/lib/_probe.py`.

* **D52 The articulated wave measured its own instrument first, and then only the loss events
  (2026-09-02/03).**  Four articulated levers (the D49 geometry re-ask, a pro planner, a
  deterministic repair bundle, `C3D_FEWER_TURNS`) all measured inside ±0.13 on 12–14 paired
  prompts, which is not evidence that they do nothing.  Decision: calibrate, then change the
  channel.  Details: PAPER_WRITING §2, §4, §5, §7, §9.3; COST §29–§30.
  * **The instrument**: an A/A on `articulated_v2` (`eval/bench/ab_plan.py --aa --pin-plan`, 12
    pairs, fixed judge pro n=3) has 2 SE **±0.130** and printed "revert" from identical arms.
  * **The channel that works — loss events**: `eval/bench/plan_stage_bench.py` runs the plan stage
    alone (≈ $0.03 and ≈ 85 s per call).  `C3D_PLAN_RESTART` (re-sample a collapsed plan from the
    original request) cut planner losses **4.7 % → 0.7 %** over 560 calls (Fisher p = 0.0067) and
    ships ON with a kill switch; the rows are in `eval/bench/data/plan_stage/` (a p-value whose
    data is not in the tree is not reproducible).  Narrowed 2026-09-03 and re-measured three-arm in
    one window: no overall separation, but dangling-link deaths 3/140 → 0/276 (p = 0.038,
    exploratory; the residue is `parent == child` joints).  The rule: measure the loss event, not
    the judge mean, unless the battery is large enough for the judge mean.
  * **A fault the judge channel could not have found**: `is_error = not obs.ok` delivered verdicts
    as broken calls the model retried (and gemini-cli stringified a 275 kB sheet into ~261 k tokens
    of base64); after the fix, calls reported as errors 26.6 % → 1.3 % and generator $/round median
    1.572 → 0.950 (`eval/bench/session_stats.py`; the earlier hand-computed column is withdrawn).
  * **`C3D_LEAN_PROMPT` stayed OFF** (+0.030, 2 SE ±0.076, no cost saving) and the switch was
    DELETED in review (2026-09-03, −569 lines); restore it from the commit named in PAPER_WRITING
    §9.3.
  * **Reading an agent's repo is a sandbox boundary**: `read_tree_at` reads via `ls-tree -r -z` +
    `cat-file --batch`, never `git archive` (a planted `filter.<name>.smudge` executes and no flag
    turns it off); pinned by a test in `tests/flywheel_cli` (`record/_git.py`).
  * **The refine transitions are the corpus this harness is for** (`3dcode flywheel refine`: one
    row per refined round); format conversion lives in `toolkits/llamafactory/`, not in the harness
    (owner's boundary, 2026-09-03).
  * **`<mimic>` changes the sweep, which is what it was for**: on the 8 of 14 prompts that declare
    a coupling the sampler drives a median 3 joints per pose instead of 6; the `joint_sweep`
    failure rate did not move detectably, and the judge- and gate-side readouts predate the
    2026-09-04 `robot_scene` follower fix and need a re-run.

* **D53 Interpenetration is measured twice, and the two probes are asking different questions
  (found 2026-09-04, corrected 2026-09-06, NOT changed).**  `spatial/connectivity` draws 600 points
  per surface, requires a minimum share of them inside the other part, and calls **2 mm** a WARN
  and **10 mm** an ERROR at the REST pose.  `joint_sweep` poses the mechanism through fk, records
  an overlap from **2 mm** (`sweep_collisions(tol_m=)`) and makes a REST overlap an ERROR only
  above **5 mm** (`sweep_findings(rest_max_m=)`, matched by `urdf.REST_PENETRATION_MAX_M`);
  `eval/bench/penetration_thresholds.py` imports both instead of restating them.  Over 374 recorded
  articulated rounds the connectivity ERROR fired **0 times** (WARN 50; depths median 2.0 mm, max
  9.9 mm) while the sweep raised **295 ERRORs over 191 link pairs** (median 5.0 mm, max 34.3), 288
  of them overlaps that exist only in a moved pose.  The probes do not disagree about depth: on the
  4 of 19 rest-pose pairs both recorded they agree exactly (4.4/4.4, 4.4/4.4, 2.2/2.2, 2.5/2.5 mm)
  — an earlier "2-3x deeper" compared the sweep's worst-over-all-poses with rest.  The threshold
  question answered itself offline: a 2 mm connectivity ERROR would fire on 50 pairs, only 7
  corroborated by the sweep (the rest designed static contacts such as
  `shoulder_lock_knob|swivel_post`, `base_underframe|center_top`); at 5 mm, 2 pairs, 0
  corroborated.  Do not align the numbers; what each check is called and documented to do is the
  fix (`spatial/connectivity.py` constants).

* **D54 A gate never reports the machine as a defect, and a render is retried once when the browser
  dies (2026-09-05).**  Three of the first six recorded `scenes_v1` cells built, passed every gate
  that does not need pixels and kept ZERO renders (`Attempted to use detached Frame` — Chrome
  reaping the tab on a box at load 93 with swap full): $6.75 of paid generation, no verdict.  Three
  general rules: (1) a driver that lost its browser is retried ONCE, on a browser of its own
  (`spatial/node.BROWSER_LOST_MARKERS`, one vocabulary for the object and scene funnels; the retry
  runs with `C3D_BROWSER_REUSE=off` — exactly one: a box out of memory stays out of memory); (2)
  two runtime trees never share a browser (the daemon endpoint, its spawn lock and its failure file
  carry a digest of the `runtime_js` that spawned them); (3) a finding that names a dead browser is
  reported apart from a defect (`eval/bench/scene_stats.py`'s `lost to the box` column).  A red
  browser test is checked against `uptime` and `free -g` before it is believed.  D61 later found
  the real cause of "driver output lost".  Details: RUNBOOK §5.

* **D55 `scene_placement` measures matter, and matter writes depth (2026-09-05).**  Two of the
  first six recorded scene cells failed the gate on nothing but their own atmosphere — a haze shell
  and a moonbeam, `MeshBasicMaterial` at opacity 0.035–0.04 with `depthWrite: false`, an idiom
  every recorded scene uses 34–42 times.  A pass that writes no depth supports nothing, swallows
  nothing and overlaps nothing: `host_placement` keeps `nonSolid` meshes out of the column index
  (an asset made only of them is listed exempt `volumetric`).  **Opacity is deliberately not part
  of the rule** (`runtime_js/lib/backdrop.mjs`).  Measured: cozy_cabin FAIL → PASS;
  japanese_garden's 3.46 m sink becomes a 0.044 m embed and the gate now fails on a real defect it
  had been reporting alongside the fog (`SubmergedRock_1 is sunken 0.48 m into ArchedBridge`).  The
  same rule in `nearGeometry` does more: `repairCameraSpec` RETREATS a lens reported "in geometry",
  and cozy_cabin's two cameras were moved back 4 m for standing in a `MoonlightShaft`; re-rendered
  with the rule they go from mean luminance 0.126 / 48 % near-black and 0.149 / 56 % to 0.263 /
  0.5 % and 0.298 / 17 %, off `dark_frame` ERROR — two of that cell's three dark-frame ERRORs were
  manufactured by the harness (`tests/scene_runtime/test_camera_volumetric.py`; what that means for
  the track's history: PAPER_WRITING §9.2).

* **D56 A loaded GLB root is named after its asset (2026-09-05).**  rooftop_garden's `blender_glb`
  asset `LoungeSofa` was built, loaded and in the scene, and `scene_placement` still reported it
  missing, because a glTF root carries whatever the exporter wrote and Blender writes "Scene".  The
  three assembled entry points that load GLBs (`render_scene_js`, the assembler's probe module, the
  plan-written `scene.js`) had drifted into three copies of one loop; they are one emitter,
  `_glb_preload_js`, which stamps `to_pascal(key)` on the loaded root
  (`tests/scene_runtime/test_glb_asset_naming.py`).  Consequence for the Blender-scene question:
  **the cross-language seam is not what fails** — the Blender asset path works end to end and only
  lost a name; an argument for a Blender assembly layer has to be made on other grounds.

* **D57 A frame rate is a measurement of the renderer that produced it (2026-09-05).**  One
  `scenes_v1` battery measured fps on an RTX 6000 Ada for one cell and on SwiftShader for the next
  three (`gpu_launch.cjs` caches a negative GPU verdict for 20 minutes), `render_console` told all
  four to "merge static geometry", and two of the four critical judge issues were frame-rate
  complaints.  `RenderSet.hardware_fps` is the only form a gate or a judge may read;
  `RenderSet.software_rendered` reads the renderer string with the words `gpu_launch.cjs` uses; the
  judge prompt keeps a software number but LABELS it, and an unrecognised renderer string is never
  claimed as software.  An fps comparison across cells is valid only within one backend
  (`contracts/artifacts.py`, `tests/scene_gates/test_software_fps.py`).

* **D58 The scene texture pack is wired into the loop, behind a switch that is off (2026-09-05).**
  Four of the 24 judge issues over the first `scenes_v1` battery's five scored cells say the
  GROUND is a flat untextured colour — its most consistent defect — and that was a harness gap:
  `texturing.plan.scene_texture_pack` and `texture_pack_prompt` existed and nothing in
  `tracks/scene.py` called either (the pack was reachable only through `3dcode texture scene-pack`,
  for a human to paste).  A `textures` stage runs before env and zones (they can only name files
  that exist when their prompts are built) and `_ctx` carries the manifest into both prompts.
  **`C3D_SCENE_TEXTURES` is off by default**: an image-model call per run ($0.15 measured for two
  512 px textures, an estimated $1–2 at ten 1024 px) buys a measurement nobody has made, so the
  material class cannot count as evidence for changing renderer until this arm has run.  (Told
  nothing about the verdict, the pack planner put `medieval_cobblestone` first for
  medieval_market.)  Details: `tests/scene_gates/test_scene_textures_stage.py`, PAPER_WRITING §9.3.

* **D59 A check that reads the scene graph by name walks the whole zone subtree (2026-09-06).**
  All five `scene_placement` ERRORs left in the fixed arm were "zone X is missing planned
  contents", and none was absent content: a zone wraps what it builds in one group
  (`IslandAssembly`) or names the placed object for its behaviour (`PineSway_0`, whose root one
  level down is `SnowyPineTree`).  Each placement row carries its named descendants (`inner`,
  capped at `MAX_INNER_NAMES = 24`) and the check reads them: 5 ERRORs → 0 on the three recorded
  workspaces (`tests/scene_gates/test_zone_contents_nesting.py`).  The third variant of one defect
  (D56's `Scene` root, a wrapper group, a behaviour-named parent), so the rule is: **a by-name
  check on the scene graph names a SUBTREE, never a child.**

* **D60 A harness failure is never handed to the agent as a repair (2026-09-06).**
  `probes.probe_report` labels a driver that produced no output with
  `harness_failure: True` and the hint "this is a harness/driver failure, not your code",
  and `build_with_repair` did not read the flag.  Measured on `eval/bench/out/scene_textures`
  (japanese_garden): three repairs against that message rewrote 5, then **14**, then 3
  files — the 14 included `env.js` and every zone — and the fourth build passed on its
  own.  The 14-file rewrite deleted the texture use the arm existed to measure; that cell
  scored 0.496 where the same prompt scored 0.636 without the detour.  `BuildResult`
  carries the flag now and the loop re-runs the BUILD instead, bounded by
  `MAX_HARNESS_REBUILDS = 2` and costing no model call, since the node driver has already
  retried once itself.  Rare (1 cell of 12 in each of two arms) and expensive when it
  fires.

* **D61 A driver's summary is flushed before it exits (2026-09-06).**  `lib/cli.finish()` wrote the
  JSON summary and then called `process.exit()`, which does not flush node's asynchronous stdout to
  a PIPE: the starter scene's `probe_scene.mjs --compile` summary is 10 462 bytes to a file and was
  exactly 8192 through a pipe.  **This is the real mechanism behind every "driver output lost" and
  "[?] scene did not boot" in the scene batteries, which D54 attributed to the wrong cause**: it
  depends on the census size, not on how busy the machine is, so D54's retries (still right — they
  cost nothing and cover a genuinely transient loss) could never fix a deterministic truncation.
  `finish()` sets `process.exitCode` and exits from the write's completion callback, the watchdog
  too; pinned by a driver whose summary is 64 KiB (`tests/threejs_render/test_cli_flush.py`).

* **D62 The `inside_mesh_bbox` term was NOT narrowed, and here is the counter-example
  (2026-09-06).**  Over the 113 recorded camera checks of `scene_baseline` the ray term
  (`nearest < 0.3 m`) never fired; all ten firings came from the bbox term, each a volumetric or a
  sparse scattered field (`Drift`, `FoliageMass`, `Midges`) whose box spans the scene while the
  lens stood 1.3–10.0 m clear of any geometry.  A rule counting the bbox flag only when a ray also
  lands within 1 m (or none lands) took those ten to zero on the corpus and was then REVERTED: in
  `test_a_lens_inside_geometry_retreats_until_clear` a lens inside a FrontSide `BarCounter` has its
  rays culled by the box itself, landing on the floor 1.2 m away, so a genuinely buried camera
  would stop being repaired — and the corpus holds no buried camera, which is exactly why
  validating on it was not enough.  Done 2026-09-07 with a discriminator that asks about the
  CONTAINING mesh's own volume: D67.

* **D63 A scene hero climbs the same ladder as a module, and its GLB is checked like one
  (2026-09-07).**  Over the 18 recorded `blender_glb` heroes: 15 of 19 first sessions killed at the
  420 s asset clip (booked at $0), 12 of 18 GLBs ONE joined vertex-painted mesh (a one-part plan +
  "one object per plan part"), every fix prompt reading "Style of the whole scene: (none)", no GLB
  check, repairs on the 1 800 s agent default, the fix never re-judged; live on 6768aae the same
  day, 3 of 4 heroes in an asset A/B and 3 of 4 in a scene A/B produced nothing before the clip.
  Decision: ONE ladder for both asset kinds (`scene_assets._ladder`: single-shot → deterministic
  check → one feedback repair → agent session), the static planner's part list for the hero (one
  call; the asset sheet is the fallback), `_soft_findings` from one measurement of the GLB,
  `build_with_repair(timeout_s=)` clipping every repair to the asset window, a re-judged fix UNDONE
  when it judges worse, the scene brief and Blender's tool cards in the hero prompt, a 20 mm ground
  self-check.  Measured after: 15 of 15 heroes built across eight scenes (1 of 4 that morning);
  re-judged (n=3), four recorded hero descriptions score 0.501 vs 0.358 for the same props as
  modules; `asset_v1` passes 9 of 13 judged (0.70–0.88) against 2 of 18 recorded.  Whole-scene
  verdicts still swing 0.0–0.75 on one prompt for zone, camera and judge reasons and do not measure
  this.  Details: `tracks/scene_assets.py` docstrings.

* **D64 A keyframed Blender part reaches the scene as a clip, and the assembled scene
  plays it (2026-09-07).**  The wrapper exports animations (an object nobody keyframed
  exports exactly as before), the preload keeps `gltf.animations` on the root — which
  `.clone()` copies — and the assembled `scene.js` drives one mixer per clone by ABSOLUTE
  time from `update(t)`, so t = 0 / 1.5 s are deterministic frames.  The first attempt gave
  the zone a `clipPlayer` helper and a sentence; the windmill zone placed the clone and
  played nothing, so the harness took the job and the helper was deleted.  Measured: the
  windmill's `MillCapDetail` frames a quarter turn apart (19.3 % of the frame changed; the
  judge: "the windmill sails animate correctly"), the lighthouse lens sweeping its beam
  (61 %).  A zone de-phases a copy with `clone.userData.clipOffset`.

* **D65 The starter `env.js` lights through `sunRig` and assigns its environment map; a small thing
  kilometres away is backdrop (2026-09-07).**  0 of 127 recorded `env.js` (and 0 of 4 written that
  morning) set `scene.environment` while every recorded hero carried Blender metalness 0.7–0.9 into
  the scene: a metalness-0.9 sphere's region reads 0.24 (black with one highlight) without the map
  and 0.55 (metal, reflecting the sky) with `sunRig`'s.  The starter uses the rig (13 lines of its
  own sun + hemisphere deleted) and the env prompt says to keep it.  `sunRig`'s 'SunDisc' at 3.6 km
  blew the content bbox to 2.5 km (`overview_top` ground 0.0008); `backdrop.mjs` now calls anything
  small and > 1.5 km from the origin sky.  Follow-through the same evening: every prompt that
  taught lighting (the cookbook's time-of-day recipe, the contract's minimal `env.js`, `system.md`,
  the lighting skill) still taught bare lights — loop 9's lighthouse stacked a "TwilightKey" on the
  night rig, loop 8's boat passed `fillSky`/`fillGround` the rig ignored; all now hand the row's
  colours to `sunRig` (which gained the two colour overrides; the env bake's ground half follows),
  the recipe's intensity columns are gone, and both snippets are run for real under node.

* **D66 A procedural module is judged on the hero's rig, and the plan's largest one keeps
  its verdict (2026-09-07).**  `scene_assets` had judged threejs assets through
  `runtime.render_asset` since the stage was written and nothing defined it: 0 of 860
  recorded modules were judged while every hero was, so no paired verdict existed.
  `SceneThreeJsRuntime.render_asset` exports the module with the object track's
  `export_glb.mjs` and renders the same quick sheet.  With it live the 5 % share rule alone
  still judged nothing (measured over two scenes: 1e-5 .. 2e-3 of the scene volume), so
  the plan's largest module of at least 1 m³ is judged whatever its share — one extra
  verdict per scene at most; a bollard is not a hero, a boat is.

* **D67 Two placement-side false positives that cost whole rounds, measured on loop 9 and fixed at
  the source (2026-09-07).**  (1) *Scale of a wrapper*: a scatter's census row is its wrapper, so
  the lighthouse's `PathAndFence/PicketFences` (twelve 2.2 m panels) measured 14.73 m against the
  plan's 2.4 m panel — a "6.1x" ERROR the judge repeated ("towering over the camera" — the frame
  shows a knee-high fence) and the repair obeyed by shrinking the run to dots (`GorseBushClusters`
  8.6x, `CoastalRockOutcrops` 9.1x the same).  Rows carry `families` (per named family of ≥ 2
  members below the row, the median largest extent of ONE member) and the scale check measures the
  family the plan's key names (`AssetRow.instance_size`), else the row's box: on the recorded
  round-0 workspace four scale ERRORs → none (0.6 m gorse vs plan 1.5, 2.16 m panels vs 2.4, 4.9 m
  rocks vs 3.8).  (2) *"Inside" is the mesh's volume*: the windmill's `MillDetail` camera stood
  4.9 m from any surface yet was `camera_in_geometry` for three rounds inside the 22 m lattice
  sails' box.  `eyeInsideMesh` decides by ray parity over the mesh's own triangles (six skewed
  rays, majority, both faces — so the FrontSide `BarCounter` repair test still holds), the box
  stays the pre-filter; re-rendered, `MillDetail` inside [] / nearest 12.0 m.  This is the
  discriminator D62 declined to guess at.  Also: the dark-frame hint speaks the rig's vocabulary
  (`sunRig({ mood, intensity, fill })`) instead of asking for a second DirectionalLight (D65).

* **D68 A session that died in a 503 storm is retried through the single-shot path (2026-09-07;
  counts corrected the same night).**  Loops 10–13 ran in an evening-long Gemini 503 storm: of 40
  gemini-cli sessions that ended `timeout` (env, zones, escalated heroes, refines), 0 produced a
  final answer, 14 wrote nothing and 26 wrote partial files, each dying at the wall inside the
  CLI's own retry loop (1–15 "Attempt N failed with status 503"; gemini-cli exposes no turn count),
  while every single-shot call in the same minutes got through (hedged across the 22 keys) and
  `codex:gpt-6-astra` sessions completed.  The stages whose session wrote nothing shipped the
  skeleton env and empty zones and judged 0.00–0.14 (clockmaker: "the hero LongcaseClock is
  completely missing").  The session result says why it died (`AgentResult.transient`), the
  generation result carries `transient` and `storm` (transient AND nothing written), and the ONE
  call every stage makes — `tracks.common.generate_for(ctx, task)`, which replaced seven identical
  eleven-argument `generate(...)` sites — retries a storm-dead task through `single_shot_ctx`, the
  degradation the soft budget already used.  Partial files are kept and gated like any other work;
  a hero's escalated session that died transient with a failing check gets ONE more single-shot
  repair (`asset.storm_repair`).  The owner's rule for the storm (2026-09-07 evening): keep
  querying, keep retrying.  `single_shot_agent_id` / `single_shot_ctx` live in `tracks.common`
  (they were never scene-specific).

* **D69 An interior scene's enclosure has an owner: the environment, from the plan's bounds
  (2026-09-07).**  Six interior runs of the day were judged "not enclosed — a diorama on a flat
  plane" (0.0–0.3): the planner wrote the premise (even a zone called `WindowWall`), the env
  brief's rules were all outdoor, and nobody owned the walls; the harness's round 0 sat at
  0.30–0.57 and needed a 20–30 min refine round to reach 0.73–0.93, while the bare one-file
  baseline built the room first (0.82).  `ScenePlan.interior` names the case;
  `lib/environment.js roomShell()` builds four walls and a ceiling OUTWARD of the bounds' faces (a
  zone that stays in bounds is never in a wall) with openings cut as span / sill / lintel panels;
  the skeleton's `env.js` carries `INTERIOR = { center, extents, openings: [] }` from the plan, so
  a storm-dead env session ships a room, not a plane; the env brief claims the openings and the
  light, the zone brief forbids walls.  Harness-owned starter geometry the agent edits, like
  `worldShell` (D51) — not a wrapper that re-centres or grounds anything (L7).  Details:
  `tests/scene_runtime/lib/test_room_shell.py`.

* **D70 One author for the whole world — `C3D_ONE_WORLD_SESSION`, ON by default since the
  fixed-judge confirmation of the session that is told its window (2026-09-08).**  Under one fixed
  judge (pro, n=2) and one model on both sides (`codex:gpt-6-astra@low`), a bare one-file scene
  scored 0.894 / 0.82 on the clockmaker / boat workshops where the harness's round 0 — zones
  written by separate sessions of at most two small zones each — sat at 0.17 / 0.30–0.57 and needed
  two refine rounds to reach 0.60–0.93; over four prompts the harness still led on mean (0.54 vs
  0.36), but the day's tally over 43 judged rounds was led by what appears BETWEEN authors
  (floating / scale / interpenetration 28, missing planned content 23, cameras 21).  Every zone
  brief goes to ONE session that owns every zone file, its window scaled by the zone count and its
  batch header saying whose coherence it is; env, assets, heroes, gates and rounds are unchanged.
  Measured: in-loop (loop 17, the same three briefs) round 0 clockmaker 0.17 → 0.69, boat 0.51 →
  0.58, NYC 0.44 → 0.43; best round 0.75 → 0.82 PASS, 0.73 → 0.60, 0.60 → 0.77 PASS — two passes
  where the fan-out had none; the first fixed-judge confirmation (`cmp_scene4`) did NOT reproduce
  (0.60 / 0.60 / 0.54 vs the one-shot's 0.81 / 0.83 / 0.60; its session finished four zones in five
  minutes as a block-out ("missing stove", "shelves missing", "primitive tools", 0.32 at round 0):
  one author is coherent but thin, and n=1 per arm per brief is noise-sized), so the header now
  states the window and asks for each zone's full density; then loop 18 passed all three at round 0
  (0.81 / 0.77 / 0.88; the session spends 10–19 min instead of 5) and `cmp_scene5` put the harness
  at 0.89 / 0.89 against the one-shot's 0.92 / 0.77 (whose draws move ±0.1): the bare one-file
  scene is no longer ahead on interiors, and the harness keeps its heroes, gates, library and
  rounds on top.  **Removed 2026-09-21:** the fan-out control arm (`plan_zone_batches`,
  `MAX_ZONES_PER_BATCH`, `SMALL_ZONE_CONTENTS`, the small-neighbours batch header) and the
  `C3D_ONE_WORLD_SESSION` switch; one session owning every zone file is the only path.

* **D71 The starter's outdoor world ships from the library (2026-09-08).**  The starter's `env.js`
  was a one-colour plane under a bare shader dome with linear fog, and the day's three most
  frequent exterior defects ("flat untextured ground", "no aerial perspective / hard world edge",
  pyramid or cone backdrops) were exactly that default.  The starter now builds its ground from
  `terrain.ground()` (textured, displaced, LEVEL inside `CONTENT_RADIUS` — the plan's cameras were
  written for y ≈ 0 — and rolling beyond; `heightAt` IS that function, seeded, so mesh and seat
  cannot drift), sky / ridge / fog from `worldShell()`, the middle distance from `makeOutskirts()`,
  the light from `sunRig()` (D65) and an interior's enclosure from `roomShell()` (D69), with `MOOD`
  keeping them agreeing; the env brief says tune, never replace.  Found on the way: a −1.5e-14
  coordinate read past `terrain.ground()`'s lattice (15 NaN vertices), fixed at the source.
  Measured (loop 20, the four exterior briefs, in-loop): round 0 0.55 / 0.60 / 0.49 / 0.42 against
  0.53 / 0.60 / 0.60 / 0.34 — no lift when the env session RUNS (all four rewrote env.js, kept
  `worldShell` and `sunRig`, dropped the library ground and replaced the shell's exponential fog
  with their own linear fog; 2 of 4 kept the outskirts); what the starter buys is the default a
  session that dies (the storm's 14 of 40) or keeps it ships.  Not a re-centring wrapper (L7).
  Details: `tests/scene_runtime/lib/test_exterior_starter.py`.

* **D72 A camera and a hero are measured where they stand, not scene-wide (2026-09-09).**  Four
  false or missing verdicts from one day's batteries, each repeated by the judge as a major issue
  and each costing a refine round: an "ant's-eye view" measured against the scene's HIGHEST ground;
  a lens under a FrontSide terrain whose frame rendered "fine"; a squat pillar 0.9 m before the
  hero camera, so the judge called the hidden hero "a massive grey box"; and a hero in the scene
  but in no authored frame.  `nearGeometry` measures per lens (`ground_below_m`, `ground_above_m`,
  `near_rays`, `target_hit_m`) and `glbCoverage` renders each loaded GLB as a mask per camera
  (`glb_frac`); the gate reads them as `camera_low` / `camera_high`, `camera_under_ground_mesh`,
  `camera_blocked`, `camera_target_blocked`, `hero_unseen`, `hero_small_in_its_camera` (thresholds:
  INTERFACES spatial/).  The rule behind all of it: **a wrong deterministic finding is worse than
  none, because the judge repeats it** — precision over recall, and every number names the surface
  it was measured against.

* **D73 The plan's cameras are repaired out of a blocked view (2026-09-09).**  (Its first half —
  a refine after a regression builds on the best round — was undone by D80: a refine builds on
  the round before it.)  `PylonSlopeVista` stayed cut by a snow bank for three rounds because
  cameras belong to the plan and no session moved one.  `repairCameraSpec` counts a lens staring at a surface
  (`near_rays`), a sightline cut before half the distance and a terrain overhead as "not
  clear" and searches back, up and sideways out of it; a camera NAMED for a hero whose hero
  centre is outside its frustum is re-aimed at it first (loop 25's `LanternDetail` shot the
  tower wall for three rounds while `hero_unseen` read 0.0 %).  Same rule as D72's: the harness
  fixes what it can measure, deterministically, before a session is paid to guess.

* **D74 Eight effect modules nobody imported are removed from the library (2026-09-21).**
  Measured over the 52 recorded scene workspaces that shipped `src/lib/`: generated scene code
  imported `mirror.js`, `scatter.js`, `season.js` in 0 runs, `creature.js`, `river.js`,
  `waterfall.js` in 1, `indoor.js`, `lights.js` in 2.  No other lib module, no starter file
  and no harness python imports any of them, so each went with its
  `tests/scene_runtime/lib/test_<module>.py` and its `effects_catalog.md` row (~4.1k lines of
  lib, ~3.7k of tests); the library is 44 modules / 24.8k lines.  `windows.js` (0 runs) was on
  the same list and STAYS: `test_neon.py` compiles `patchWindowInteriors` under
  `patchNeonSpill` as its fixture, and `urban.js` / `neon.js` document that composition.
  A catalog row is a cost every scene session reads; a module no session calls is a row that
  buys nothing.  Restore any of them from git history (the commit before this one) if a
  battery needs it — module, test and catalog row together, since
  `test_every_call_the_catalog_advertises_is_a_real_export` pins catalog == `lib_files()`.
* **D75 The core is what a run needs; what READS finished runs is `codeverse3d/addons`
  (2026-09-21).**  `codeverse/flywheel` (the package before D78) mixed the record every run writes (Law 6) with the tools
  that turn a tree of finished runs into something else.  The first is now `codeverse3d/record`
  (`record.py`, `deliverable.py`, `telemetry.py`, `_git.py` — `record.py`'s whole import closure);
  the second is `codeverse3d/addons`: `gallery`, `dataset` (export, pack, pairs, refine, captions,
  index, sample, quality), `costreport` (audit, report, caching — booking money WHILE a run happens
  stays in `codeverse3d/cost`), `calibration.py`, `skill_targets.py`.  The boundary is one-way and
  tested (`tests/core/test_addons_boundary.py`): outside `codeverse3d/cli` nothing imports an
  addon, so `codeverse3d.cost` and `codeverse3d.skills` stopped re-exporting `audit_runs` and
  `check_claims`.  A MOVE, which L4 does not count as a simplification: an organisation decision by
  the owner, shipped with real deletions (`flywheel/code_quality.py` — written into every record,
  read by nothing — the mesh-voxel dedupe and `flywheel dedupe`, the `flywheel gallery` alias).  An
  addon has no run-time hook and so no switch; one that must act during a run gets a `Settings`
  field (no plugin registry — L4).
* **D76 The short command is `3dcode` (2026-09-21).**  `3dcv` → `3dcode` for the console script,
  the workspace dir (`.3dcv/` → `.3dcode/`), the MCP server name (tools are `mcp_3dcode_<name>` /
  `mcp__3dcode__<name>`) and the workspace git author.  Runs recorded earlier stay readable and
  resumable: `.3dcv` is still harness-owned and gitignored, a legacy `3dcv` server entry is
  still cleaned from a workspace `.mcp.json`, `eval/bench/session_stats` reads both tool prefixes.
  NOT renamed then: the `CV3D_` settings prefix and the `cv3d-*` skill names (both renamed by D78,
  which reads the old names), the `3dcv_*` LLaMA-Factory dataset names.  The contributor CLI in
  `toolkits/3dcode_cli` gave up the script name and is `3dcode-data` (its distribution name,
  package and credentials path are unchanged; `VENDORED.md` records the difference from upstream).
* **D77 Evaluation lives next to the harness, not inside it (2026-09-21).**  `harness/bench` →
  `eval/bench` (evaluates the HARNESS: batteries, A/B rigs, reports; the python package is still
  `bench`), `finetune/3dcodeverse_eval` → `eval/llm` (evaluates a bare LLM/VLM; the 2026-09-08
  rewrite replaces the older tracked copy), the evaluation write-ups (EVAL, COMPLEXITY,
  PAPER_WRITING) → `eval/docs`, and the 24 test files of the bench scripts → `eval/tests`.  The
  dependency is one-way and now visible in the tree: `eval/*` imports `codeverse3d`, nothing under
  `harness/` imports `bench`, and the harness suite passes without `eval/`.  Mixed test files were
  split where they straddled the line (`test_targets`, `test_plan_features`, `test_complexity_record`).
  `3dcode bench run|report` stays as the launcher and finds the package at `<repo>/eval`
  (`cli/_common.EVAL_ROOT`).  The two evaluations share one set of battery files
  (`eval/bench/prompts`; `llm` names the ten it can ask one-shot).  Run data — `eval/bench/out`,
  `eval/llm/data/prompts/*.jsonl` — is not in git.
* **D78 One name family: `3dcodeverse` · `codeverse3d` · `C3D` (owner, 2026-09-22).**  A Python
  import name cannot start with a digit, so the package that was `codeverse` is now **`codeverse3d`**
  (the distribution, the repo and the `3dcodeverse` command keep the digit-first form; the short
  command stays `3dcode`).  The abbreviation is **`C3D`** — never "CV", which reads as Computer
  Vision: the settings prefix `CV3D_*` → `C3D_*`, the skill bundles `cv3d-*` → `c3d-*` (the read
  control is `zz-c3d-read-control`), contextvar/tmp names `cv3d_*` → `c3d_*`.  Config files are
  `~/.config/3dcodeverse/config.yaml` and `./3dcodeverse.yaml`.  What keeps working without a change:
  `CV3D_X` in a shell is copied to `C3D_X` at first import with one warning
  (`codeverse3d/__init__._adopt_legacy_env`); the old config file names are read UNDER the new ones;
  a resumed workspace drops its old `cv3d-*` skill folders (the materializer removes every folder it
  did not route).  Needs `pip install -e harness` once.  NOT renamed: the `astra3d` key-file path, the
  local conda env `cv3d-eval` (renaming it would move an environment another session is using), and
  recorded data (`eval/bench/data`, anything under a runs directory).
* **D79 Second cleanup pass (2026-09-22): what changed behaviour.**  Six lanes deleted ~1.2k net lines
  (dead parameters, copies of shared helpers, the compose agent session, the library's duplicated world-space
  bases — the effect modules' compiled shaders are byte-identical, checked by fingerprint) and fixed ~30 bugs.
  The deltas a run shows: the scene judge is told authored cameras by their plan names, not `cam_*`
  (`judge_prompt_hash` moved for every rubric); calibration and pairwise judge the payload the in-run judge
  sees; routes R3/R4 attach c3d-bbox-contract to object tracks only (scene sessions lack its tools);
  `--profile` no longer overrides a user-set judge size or turn cap; claude-code usage counts the whole
  prompt and an overloaded exit is transient; shader_probe compiles without the post chain; host warnings
  reach the log; the effects catalog documents `userData.update`, `ground({rand})` and `figure()` as built.

* **D80 Fixed rounds; every round kept; the pick is a reader's (owner, 2026-09-22).**  A run is the
  baseline plus `--rounds` refine rounds, each built on the round before it.  It stops early only on
  the clock (`budget`), the vendor's quota (`agent_quota`) or a hard failure: a refine round that
  changed nothing (`no_change`), a last round with nothing to ask for (`no_refine_tasks` — 2 of 923
  recorded verdicts had an empty improvement plan) or no verdict even after one re-judge
  (`judge_unavailable`).  Gone: `StopPolicy` (pass / plateau / regression / diminishing returns —
  37 % of 466 recorded runs stopped on one of those before `--rounds` ran out), the rewrite and
  surface-detail rounds, refine-from-best, the in-loop pairwise, `RunState.best_*`,
  `pick_best_round`, `RoundPolicy.target`.  A run is never passed or failed; the judge's `passed`
  is per round.  Every round keeps its build in `artifacts/rNN/` (plus its commit, renders and
  verdict); `codeverse3d/addons/select.py` picks the round to hand over (highest judged score →
  fewer gate errors → earlier; `--by pairwise` asks the pairwise judge inside 0.03) and packages it
  into `deliverable/` + `selection.json`; `3dcode make` / `resume` call it unless `--no-pick`, and
  `3dcode pick <slug> [--round N]` re-picks.  The texture pass runs on the picked round only.
  Records before this read on: `passed` / `plateau` load as `stopped`, a state's `best_*` keys are
  ignored and a resume goes on from the last round.  Readers (gallery, dataset, cost report,
  calibration, eval/bench) all ask `select`; bench rows say `score_picked` / `picked_round`.
* **D81 Skills are ON by default (owner, 2026-09-22).**  `C3D_SKILLS` and `C3D_SKILLS_UNVERIFIED`
  default on; `C3D_SKILLS=0` is the off switch, so an A/B's no-skills arm must name it.  Evidence:
  one session per vendor CLI (gemini-cli ×2, claude-code, codex, agy) activated every routed bundle
  — 20 of 20, all before writing code — and never the control.  Reads are now ground truth: each
  backend records its CLI's own tool calls in `transcript.jsonl` and the read probe reads those
  first (the atime probe was wrong on every session).  gemini-cli pins `skills.enabled`,
  claude-code streams JSON; `.agents/` is harness-owned and git-ignored.  SKILLS_LEDGER §0b.

## Rejected / deferred

* A versioned `Spec`/`RunRecord`/`RunState` load-normaliser (rejected 2026-08-30: of the seven
  compatibility branches it was meant to absorb, only two are schema-shaped — 14 lines — so it
  would add an abstraction and delete nothing, L4).
* Deleting `spatial/sections.py` (rejected 2026-08-30: it IS the registered `cross_section` MCP
  tool, imported at module level by `spatial/tools.py`; removing it takes down the MCP server).
* Registering `single-shot` as a CodingAgent kind (rejected: it has no tools/session).
* A free-form `dict` judge schema (rejected: flash skips criteria).
* Storing URDF meshes Y-up and converting on load (rejected: breaks foreign loaders).
* Per-asset judging for threejs scene assets — done 2026-09-07 (D66: `render_asset` on the scene runtime).
* Proposed and not yet done (owner-level files): `n_samples` in Settings.  (Done since first written: `Workspace._git`
  lock + index.lock retry; the `languages/**` package-data globs.)
