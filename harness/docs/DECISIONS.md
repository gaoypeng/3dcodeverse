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
  wrappers and exporters (`languages/wrappers/`, `languages/opengl_python/wrappers/`, `runtime_js/`).  Consequence: every
  language needs a standalone wrapper that cannot import `codeverse3d` (Blender's python
  cannot see the package anyway).
* **L2 One contracts package, one conventions module.**  `contracts/` is data-only;
  `conventions.py` is the only place stating frames/units/naming.  Consequence: plan
  frames are the language's native frame (Z-up for blender/cadquery/urdf) and
  `check_contract` converts to the GLB frame (Y-up) using `LANGUAGE_FRAME`.
* **L3 Deterministic first, VLM for perception only.**  Score is computed in code
  from rubric weights; floors and caps are explicit; degraded verdicts are glitches.
* **L4 Typed, no regex-on-id, no god files.**  Files ≤ 2 000 lines, 3 000 absolute (owner, 2026-08-28 — was 1 500, before that ~400).
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
  restored from `run_state.extra.budget_snapshot` (`BudgetSnapshot`: since D84 the clock's
  cumulative ACTIVE seconds only — the money fields went with the guard's money; grace is
  deliberately NOT persisted).  Active minutes still count after a resume, so a raised
  `--max-minutes` grants only the difference.
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
* **D10 gemini-cli needs a system-settings file (Δ; superseded by D103 — the gemini-cli backend is deleted).**  api-key auth +
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
  required for the Z-up → Y-up plan-box conversion.  (`cross_section`'s `absolute` / `size` and
  the `compare_silhouette` tool are gone: D88 draws sections through the judge renderer, D82
  deleted the tool.)
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
  rounds (then `judges/calibration.py`, now `addons/calibration.py`, n=3): flash std 0.083 / pearson(gate errors,
  score) −0.33; pro std 0.030 / pearson +0.63 — so `default_judge =
  gemini-3.1-pro-preview` and flash's dynamic range is carried by the checklist.
  Consequence: `VlmJudge(max_images=)` is gone (`max_montages`, `detail_crops`);
  `JudgeInput.geometry_views` routes clay/normals views to a geometry-only montage.
* **D31 Textures are a derived asset pack, never the deliverable.**  The texture
  pass (material plan → tileable tiles → world-metre UVs → `object_textured.glb`)
  runs on the PICKED round, after the run (`3dcode pick --texture` or `3dcode texture pass`); it never edits `src/` or
  `object.glb`, ships only when the before/after judge gate agrees (Δoverall ≥
  −0.01 AND materials criterion improved), and is recorded in
  `record.extra["texturing"]`.  Scenes get a named tile pack under
  `public/textures/` injected into prompts instead of per-part texturing.
* **D32 Graphics track = GL, not a 3D pipeline.**  `graphics` renders through
  moderngl in a subprocess per render (contexts are not thread/fork safe), has no
  GLB/measure step, and gates on frame statistics (`gl_frames`: NaN/black/blown/
  static/flicker).  The harness owns the GLSL header (`#version`, uniforms,
  `out vec4 fragColor`) — agents write shader bodies only; feedback shaders are
  simulated at 30 fps.  Rubric `shader_v2` (threshold 0.70; `shader_v1` stays frozen for old runs).
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
  garbage env flag is OFF** — amended by D86: a typo keeps the switch's DEFAULT, which is the arm
  that never set it (for a default-ON switch "garbage is OFF" produced the variant); (d) one MCP command
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
  test file per module at landing (678 tests; since D96 one shared `lib/test_library.py` import/catalog
  check and grouped GPU compile, plus the named-bug regressions).  Companion: a post chain for scene
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
  (`contracts/artifacts.py`, `tests/scene_gates/test_software_fps.py`).  Superseded by D99: no gate or
  judge reads fps at all.

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
  (they were never scene-specific).  **Superseded by D103 (2026-10-13):** the single-shot storm
  fallback, `GenerationResult.storm` and `asset.storm_repair` are deleted; a transient death gets the
  round loop's one transport retry.

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
  `tests/scene_runtime/lib/test_room_shell.py`.  2026-09-23: a glass house is `ScenePlan.glazed`
  → `roomShell({ glazed: true })` (glass walls and roof on an iron frame grid, panes cast no shadow,
  the outskirts are built).  Every recorded `scn_med_conservatory` round on 3.7-flash (0.00–0.14)
  was a glass house inside the opaque shell ("a solid white box … blocking the exterior view and
  lighting", critical); the 3.8-flash runs that scored 0.56–0.62 had cut each wall into one
  opening and hand-built the same glass and grid.

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
  the same list and STAYS: the shared compile (`lib/test_library.py`) chains `patchNeonSpill` over
  `patchWindowInteriors`, and `urban.js` / `neon.js` document that composition.
  A catalog row is a cost every scene session reads; a module no session calls is a row that
  buys nothing.  Restore any of them from git history (the commit before this one) if a
  battery needs it — module, test and catalog row together, since
  `test_skeleton.py` pins catalog == `lib_files()`.
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
  The launcher is `cd eval && python -m bench.run_bench` / `python -m bench.report` since D89
  (`3dcode bench` put eval on sys.path at runtime).  The two evaluations share one set of battery files
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
  first (the atime probe was wrong on every session).  gemini-cli pins `skills.enabled` (moot since D103),
  claude-code streams JSON; `.agents/` is harness-owned and git-ignored.  SKILLS_LEDGER §0b.

* **D82 One verdict per check (2026-09-22).**  A tool returns the round's own gate, computed by the
  same function on the same inputs: `joint_sweep` → `joints_sweep.sweep_gate(ws)`, `check_placement` →
  `scene_placement.placement_gate(ws, census, plan)` (layouts and unbuilt assets from `stages/`),
  `shader_probe` → the build's one-boot probe (`spatial/probes.run_probe(compile=True)` since D95).  Not the rejected
  GateDef registry: nothing is registered, the tools' private checkers are deleted (on 118 recorded
  scenes the old `check_placement` passed 20 runs the round gate failed).  `BuildResult.gates` carries a
  build's own reports (scene_probe + shader_preflight, gl_frames) and `_run_round` appends them after
  the track's, failed builds included, so the shader route (R21) can fire; `census_gate_report` (always
  empty) went; the URDF build collides the rest pose only (D17) and writes no `articulation.json`;
  `GateReport.of` builds every report whose verdict is its findings.  Deleted as unused (owner):
  `effect_ablation`, `compare_silhouette` / `texture_pass` / `texture_preview` (0/1/0 calls in 616
  sessions; 16 tools remain), `glsl_cookbook.md`.  Small A/B against its base (2026-09-22; 3 urdf + 2
  scene prompts, `--rounds 1`, one run each, inside a 503 storm): articulated picked 0.75 / 0.61 / 0.50
  → 0.63 / 0.70 / 0.59 (mean 0.621 → 0.642), scenes 0.03 / unjudged → 0.09 / 0.47 — nothing broke, and
  n this small cannot resolve a gain (runs in `~/3dcodeverse_runs/ab_verdict_2026-09-22`).
* **D83 A vendor session gets its prompt on stdin, sees only routed skills, and is read back from its
  own record (2026-09-22).**  Every CLI reads the prompt from stdin, byte-identical (a 5-zone scene prompt
  passed gemini's 2 000-line `read_file` cut when it went through `task_prompt.md`; argv caps an argument
  at 128 KiB).  claude-code runs `--setting-sources project` + no bundled skills (26 extra skills
  before), so its effort is the harness's (`agents.claude_effort`, xhigh — what it inherited until
  then); codex `skills.bundled.enabled=false`; gemini `skills.disabled`; agy has no switch.  A session
  with no envelope books usage from the CLI's own record (gemini chat log, claude stream): replayed
  over the 11 gemini sessions of the 2026-09-22 live runs, $0.219 booked → $5.11.  `provider_wait_s`
  is the time a session lost to provider errors (a lower bound for gemini, 0 for codex).  Failure
  classes travel typed (`AgentResult.transient/quota` → `RoundFailed`); a plain timeout is no longer
  retried as a transport death.  One `cli_common.classify_end` decides every vendor's end (owner,
  2026-09-24, N71): a watchdog kill is transient only when nothing was produced and the CLI's own record
  shows provider retries, and a spent usage limit or a 429 is `budget` for all four.
* **D84 One minutes, one record of money (owner, 2026-09-22).**  Every step is a `StepTime{step,
  round, wall_s, lost_s}`; `RunRecord.minutes` = Σ(wall − lost) over run and round steps — lost is a
  session's `provider_wait_s` or an API call's time outside its answered round-trips; parallel work is
  one step.  Every reader (select, gallery, `3dcode show`/`cost`, dataset, eval/bench) reads it; a record
  without steps counts its own clock.  `max_minutes` stays a wall-clock LIMIT (it must stop a storm).
  `telemetry/cost.jsonl` is the only money record: `total_usage` = its sum at list price (every
  `live` / `session` / `extra` row once — `extra` is a billed hedge loser), a round's cost is what it
  booked; `BudgetGuard` is a clock; gone: `billing.py`, `routing.py`, `cost/reconstruct.py` (the 33
  pre-ledger runs are outside the repo), `C3D_COST_LEDGER`, `cost.round`, PROVENANCE (one price table).
* **D85 Machine-wide in-flight slots; the schedulers that never engaged are gone (2026-09-22).**
  `max_in_flight` is N `flock`'d slot files every process shares (`<cache>/slots/gemini/NN.lock`); a
  slot guards one harness API call, a CLI session takes a key but no slot (a session-long slot would
  starve judges).  The kernel frees a dead holder's lock, so §23's staleness worry is gone, and with it
  the `/proc` sibling scan (it hard-coded eval entry points) and ab_plan's refusal.  Measured
  (`tests/models/test_slots.py`): 8 processes / 4 slots peak exactly 4; a SIGKILLed holder's slot back
  in 37–70 ms; deadlines kept to 1 ms.  Deleted: TokenBucket (peak 3.9 % of TPM), StormGate (shipped
  OFF), `with_retries` (one retry loop), `fewer_turns` (OFF since COST §29).
* **D86 One switch grammar (2026-09-22).**  Every `C3D_*` knob is a `Settings` field read through
  `get_settings()`; an on/off switch is a `Flag` (on/off/1/0/true/false/yes/no; a typo warns and
  keeps the default — amends D44(c)); env beats yaml; four nested knobs keep a flat spelling
  (`C3D_MAX_IN_FLIGHT`, `C3D_RENDER_GPU`, `C3D_SEED_RECIPES`, `C3D_AGENT_MAX_TURNS`).  Fixed on the
  way: `=off` was ignored by `C3D_STREAM`, `C3D_IPV4`, `C3D_AXIS_REPAIR`.  `env_flag`, the `*_enabled`
  helpers, `skills/config.py` and 14 hand parsers are gone; `plan_features` derives live names from
  `Settings.model_fields`.  Tests set a switch through the `switch` fixture (settings are cached).
* **D87 The pipeline states each rule once (2026-09-22).**  One plan digest and one round payload
  (`judges/base.plan_summary`, `round_input`) for the live judge, `3dcode judge`, calibration, the texture
  gate and eval — replays of Z-up objects read W×H×D again and graphics replays keep passes and motion.
  One per-language prompt lookup (`prompts/catalog.language_prompt`; `RuntimeDocs`, `contract_doc` gone).
  The runtime owns the file layout (`expected_files` / `files_for`); `tracks/planner.py` owns what
  differs per track at planning (six BaseTrack hooks gone); the GLB ↔ authoring frame mapping is
  `conventions.to_authoring_frame` (L2).  The round history lives only in `rounds/rNN.json` + commits
  (`RunState` caches none of it).  `3dcode show` is the one single-run view (`status` = its STATUS).
* **D88 One build plumbing (2026-09-22).**  The python wrappers live in `languages/wrappers/` beside
  a stdlib-only `_wrapper_common.py` (report, memory cap, seeding, script runner, traceback mapping);
  `run_bpy_links` stopped writing its own (it failed on `sys.exit(0)`, had no seeding);
  `compose_build_result` writes the final BuildResult as `build.json`, the one build status
  (`build_last.json` gone).  JS syntax: one node process per lint, a content-hash cache (scene lint
  2.4–2.7 s → 0.01–0.24 s), `node --check` confirms any rejection.  One section renderer: the
  `cross_section` tool draws through the D48 judge path, whose slices are pinned to the pixel.
  The front_back slice's note said "front left" while +Z (front) is drawn on the right; the note
  now says "front right" (owner: the label must match the image the judge gets).
* **D89 Evaluation states uncertainty one way (2026-09-22).**  `eval/bench/stats.py`: the paired 95 %
  Student-t interval ("separated" = excludes zero; 2·SE understated it by 15–38 % at n = 4–8), the
  exact sign test, `statistics.correlation`.  Every journal is read through `_jsonl.read_jsonl`
  (a truncated last line is skipped) and deduplicated by `_jsonl.latest`; `ArmStats` is the one arm
  aggregate.  Numbers quoted before 2026-09-22 are 2·SE bands.  The battery launcher left the harness
  CLI: `cd eval && python -m bench.run_bench …` / `python -m bench.report …` (same flags).

* **D90 Resume never re-pays a finished stage over existing rounds (2026-09-23).**  Once rounds exist,
  `StageRunner(frozen=True)` serves every RECORDED pre-round stage whatever its key and emits
  `stage.frozen` — 81 of 91 recorded scene runs had drifted keys (e.g. `ScenePlan.interior`, 964a0a7) and
  a resume re-ran the paid env/zones sessions over refined `src/`.  The zones budget check runs after the
  stage is recorded, so a finished zones session is cached even when the clock trips on it (its written
  zones are recorded ok).  `resume --force` always archives the rounds to `rounds/pre_force/` and is THE
  way to regenerate a stage (owner).  Not covered: a stage that never finished still re-runs.  Texture
  packs join the env/zones keys only when they have files; `C3D_ZONE_LAYOUTS=off` skips the stage.
* **D91 The pre-round pipeline is data (Phase B, owner's clarity trade, +39 lines).**  Each track declares
  its pre-round stages as a `stages` tuple of `StageNode`s; `run_stages` walks them over `StageRunner`;
  ARCHITECTURE §7.1 is generated from the declarations (a test fails on drift).  The round
  (`steps._run_round`, a 15-step chain nothing can cache) and the loop (`_round_loop`, a 7-exit state
  machine) stay code by design — a generic DAG framework measured +190 lines and four rejected
  abstractions (review 2, `~/3dcodeverse_runs/arch_review2_2026-09-22/REVIEW.md`).  Scene refuses
  `--candidates N>1` (`TrackInfo.best_of_n`; a profile's value becomes 1); a run whose baseline session
  wrote code before the clock stop gets one judged salvage round.
* **D92 A reader reads the round it names (2026-09-23).**  `select.round_file` / `round_complexity_block`
  / `judge_for`: calibration, `3dcode judge`, the dataset export, the gallery, `texture pass` (the PICKED
  round), h2h, complexity and skill_targets read that round's own GLB / GIF / meshes / metrics and the
  live run's judge class — none falls back to the last build any more (D80's promise).  Old runs keep
  their raw `best_round` across a record rewrite (a legacy read-only field) so their per-round files are
  still found.  `find_run_dirs` lists a symlinked run once.
* **D93 No agent-visible text states a harness limit (owner, 2026-09-23).**  Build/render time, triangle,
  draw-call, instance, size and count caps, fps targets and gate thresholds are gone from contracts,
  system prompts, cookbooks, templates, skills (their `_claims` went with them), tool headers and starter
  comments — the inventory found most of them wrong (repair "300k" vs 600k, scenes "2 M" vs 3 M, builds
  "120 s / 20 s" vs 300 s, hero assets "60k–600k" vs a 40k check).  Enforcement is unchanged, and an
  error that fires on an actual violation still states its number; geometric tolerances stay.
  DETAIL BUDGET became numberless advice.  Inventory: `…/arch_review2_2026-09-22/limits/INVENTORY.md`.
* **D94 PascalCase accepts acronyms (owner).**  LED, TV, TVStand, USBPort, CPU_2 are valid names:
  `conventions.PASCAL_RE` = `^[A-Z][A-Za-z0-9]*(?:_\d+)?$`, `to_pascal` keeps a valid name as written,
  the stdlib wrapper copies are pinned to conventions by a test; snake keys (`tv_stand`) are unchanged.
* **D95 Review-2 clean-up (2026-09-23): what it deleted and what it guards.**  ≈ −600 production lines
  over 90+ commits: joints/spatial re-export facades, dead language code, the second cookbook-section
  parser, `probe_and_preflight` (one `run_probe(compile=)`), an `SdkModel` base, a metered `_Proxy` base,
  the write-only `RunTelemetry.files` / `.environment`, the `wasted_usage` money channel, the second
  round-journal reader in `skills_hook`, dead skills surfaces.  One rule each for: harness-owned
  paths (a single-shot write can no longer overwrite `src/lib/`, paths are normalised first), JSONL
  resume reads in eval/llm, correlation.  Guard: `tests/prompts/manifest.py` hashes every prompt and judge
  payload the fakes drive; a code-only change must leave it byte-identical, a text change re-blesses it
  (`python -m tests.prompts.manifest --bless`) in the same commit.  claude-code's effort stays the
  harness's `agents.claude_effort`.  Test fakes: one `FakeChatModel`, one scripted `FakeAgent` (−800 test lines).

* **D96 A test earns its place (owner, 2026-09-23).**  The suite had 64k lines for 52k of product code.
  A test stays when it runs production lines no other test runs (per-test coverage contexts), pins a
  contract or safety property (manifest, harness-owned paths, resume/record compatibility, the ledger,
  typed failures, a D-rule), is the regression test of a named bug, or asserts a value no other test
  does.  Everything else went: python tests −31 % (coverage 91.62 → 91.57 %, 14 defensive lines lost),
  `tests/scene_runtime` 25.6k → 6.0k lines — the effect library is one shared import/catalog check and
  one grouped GPU compile plus its named-bug regressions (17 of 17 mutations still caught).

* **D97 Natural-element effects, one ownership rule, and the graphics lab (2026-09-23).**  Fourteen
  modules join `starter/src/lib/` as source (D51): fire / firefield / smoke / cloudvolume (bounded ray
  marches over baked 3D noise — prescribed transport, not simulation), ocean / stream / waterfall / ice,
  meadow / tree / rock / sand / paving, and `lifecycle.js`: `snapshotResources` + `attachDisposal` are the
  one rule every object factory in `src/lib/` disposes by (construction-owned geometry, materials, instance buffers released
  once; textures and caller additions borrowed).  `shader.js` gained `clonePatchedMaterial`,
  `withRendererState`, alpha/normal/transmission hooks and inverse-transpose normals; finish patches now
  change roughness per pixel under their own mask, not the material's.  Scenes may load
  `src/shaders/*.{vert,frag,glsl}` as text (audited by `shader_report`).  Named bugs: three r182 compiles
  `PCFSoftShadowMap` as BASIC (the renderer uses `PCFShadowMap`); the census missed shader-instanced
  geometry; GlHost doubled a relative out dir.  Tests follow D96 (a BUILD row + a GPU group each).  The
  catalog and planner say the effects are zone code, never assets, with no limits stated (D93) — the
  "never assets" half is superseded by D100.
  `examples/graphics_lab/` is a dev lab (sixteen hand-composed studies rendered through the production
  scene and GL hosts); its `output/` is ignored and none of its media is committed.  2026-09-23: the lab's
  session notes, provenance tooling, audit logs and the unused cinematic judge were deleted (owner); the lab
  keeps build/capture/viewer/serve/scenes.
* **D98 One statement per rule, across python/JS and prompts (review 2 C2/C4/C5, 2026-09-23).**  The
  single-shot envelope is stated once, in `system/singleshot_format.md` (the brief points at it).  Where
  python and JS each decided a rule, the side the recorded runs proved right became the one home:
  placement exempt words = `runtime_js/lib/placement_words.json` (gate + settle, plurals included); "camera
  named for a hero" = the host's `namesHero`, carried as `camera_checks[].hero_for`; the URDF a sweep reads
  = the built `artifacts/robot.urdf` only; an unset PBR factor = `texturing/materials.py` (a scene pack's
  roughness stays its own 0.8 surface default).  The hero asset's context file omits `harness_contract.md`
  on purpose (its ±1 cm object acceptance contradicts the prop sheet).  D93 extended: a gate's detection
  threshold is not stated as a geometric rule in always-on text (bbox ±0.01 m, camera 0.3/0.5 m, placement
  2 cm/2.5x…); construction values (weld overlap, clearances, wall thickness) and the skills' pinned
  gate explanations stay.
* **D99 The scene draw budget is a census count gated in code; fps is a record only (owner, 2026-09-24).**
  The regression: D93 took "≤ 200 draw calls" / "≤ 40 per zone" / "fps ≥ 30" / "≤ 300 meshes" out of the
  scene prompts, but nothing in code enforced draws — only a `hardware_fps < 20` WARN, which never becomes a
  task.  On the same scenes_v2 prompts the new tree's agents wrote 3–6x the meshes (P3 95a9820 → P4
  93af8d6e: cliff_monastery 506 → 1 450 meshes and 69 → 4.9 fps, conservatory 311 → 1 776, canal_town
  1 950 → 12 665; the conservatory's round 0 drew 46 567 calls at 1 fps).  Decision 1: `scene_probe` gates
  the census's `totals.draws` — one per visible Mesh / Points / Line / Sprite per material group, an
  InstancedMesh once (`host_census.mjs` `objectDraws`) — the number the probe (and the `scene_probe` tool)
  reports, independent of camera, culling, GPU and load; the render's `fps.draw_calls` is one frame from one
  camera and adds culling and shadow passes (0.54 / 0.91 / 1.81x the mesh count at p5 / p50 / p95 on
  the corpus).  WARN above
  `conventions.DRAWS_WARN_SCENE` = 5 000, ERROR above `MAX_DRAWS_SCENE` = 15 000; the ERROR fails the build,
  so `build_with_repair` hands it to the round's repair session with the count, the threshold, the three
  heaviest top-level groups and the fix (InstancedMesh / `mergeGeometries`, `lib/instancing.js` /
  `lib/merge.js`).  Tuned on 753 recorded rendered scene rounds (~/3dcodeverse_bench_out, ~/3dcodeverse_runs;
  mesh count, which the re-probed draws match to < 1 %): log(meshes) vs log(fps) r = −0.88 (triangles −0.54);
  median fps 232 below 500 meshes, 15 at 1–2 k, 10 at 2–4 k, 4.5 at 5–7.5 k, 2 past 10 k, 0.6 past 20 k.
  Pre-D93 harness rounds (n = 255): p50 2 943, p90 6 203, p99 12 348, max 17 291 — 20 % WARN, 0.4 % (one
  1-fps conservatory) ERROR; post-D93 (n = 218): p90 11 212, p95 20 450, max 55 990 — 6.9 % ERROR, every one
  at ≤ 4.4 fps.  One-shot baselines never pass 4 761.  The `render_console` fps WARN, `perf_detail`,
  `RenderSet.hardware_fps` / `software_rendered` are deleted (D57's labelling has no reader left).
  Decision 2: fps leaves the judge — no `PROBE: … fps` line, and `scene_v1` no longer asks for "acceptable /
  healthy fps" (Law 3: the VLM judges pictures; performance is gated in code, animation by the
  frame-difference metric).  `RenderSet.fps` stays as a recorded measurement (and in the `scene_views` tool's
  output).  `SCORING_VERSION` stays 3: it stamps the arithmetic downstream of the model, which is unchanged,
  and a stored verdict still replays; the judge INPUT changed (manifest re-blessed), so a scene judged before
  and after this commit differs by the fps line and two rubric phrases.  A draw WARN is still a gate WARN in
  the judge's list, which the judge is told never makes a defect present.

* **D100 A three.js scene asset may import the effect library (owner, 2026-09-24).**  `src/assets/<snake>.js`
  may `import { makeTree } from '../lib/tree.js'` and compose a factory with its own parts: `src/lib/` is
  copied into every workspace and committed with `src/`, so the deliverable and the dataset sample (both
  read the round's commit) carry it and the output stays self-contained — a copied-out deliverable boots
  alone.  Until now `asset.md` said "no imports beyond three" and `plan_scene.j2` routed every effect to
  zone/env code; the code never rejected the import (lint, node check and GLB export all resolve
  `../lib/`), but the asset check did: a factory's triangles counted against the per-asset budget (one
  lean birch is 80 k, the wood alone 75 k) and its below-grade roots read as "sinks", so every such asset
  failed twice and escalated to an agent session.  Now `lib/lifecycle.js` `attachDisposal` records the
  tree it claims (`isLibraryBuilt`, a WeakSet); `asset_check.mjs` loads the workspace's own lifecycle
  module (the instance the asset's imports load) and reports what a factory built as `lib_tris` — held to
  the one-object ceiling `MAX_TRIS_OBJECT`, not `ASSET_MAX_TRIS` — and seats a factory by its origin.  The
  asset's own geometry keeps every rule.  A `blender_glb` hero cannot import it.  The asset prompt carries
  only the catalog rows of the factories its sheet names (by camelCase call or module file,
  `scene_assets.library_rows`), not the whole catalog.  Not changed: the scene placement gate still
  measures an asset's lowest vertex, so a library tree's roots inside a non-exempt asset name can read
  as sunken there, as they already did for zone-placed trees.

* **D101 The scene track is language-dispatched through `SceneRuntime` (2026-09-24).**  In preparation for
  `scene_blender` (`~/3dcodeverse_runs/scene_blender_2026-09-24/DESIGN.md` §11 phase 0):
  `languages/base.py` `SceneRuntime` (a `LanguageRuntime` plus `zone_file`, `asset_file`, `assemble`,
  `probe`, `render_scene`) is the only way the scene stages (`tracks/scene.py`, `tracks/scene_assets.py`),
  `Services.render_scene` / `assemble_scene` (which now take the runtime), the scene tools (`scene_probe`,
  `scene_views`, `check_placement`, registered for `SCENE_LANGUAGES`), `3dcode render` and the fixed
  evaluator reach a scene language; none of them imports `scene_threejs` any more.  `SceneThreeJsRuntime`
  implements it by delegation, so behaviour is byte-identical (prompt manifest unchanged, not re-blessed).
  The design's `probe_args` member was left out: the probe's driver flags are the runtime's own business
  inside `probe`/`build`, and nothing outside a runtime would read them.  `shader_probe` stays three.js-only.

* **D102 scene_blender phase 1: contracts, the Blender build and the geometry gates on a census GLB (2026-09-24).**
  DESIGN (`~/3dcodeverse_runs/scene_blender_2026-09-24/DESIGN.md`) §11 phase 1, owner decisions D1–D12.
  `Language.SCENE_BLENDER` is the scene track's second language (`src/scene.py` entry, Z-up frame in
  `conventions.LANGUAGE_FRAME`, `HARNESS_OWNED_SRC` `src/lib/`), with `SceneBlenderRuntime` (`languages/scene_blender`).
  `src/scene.py` is assembled DATA (`ZONES` / `ASSETS` / `HEROES` / `CAMERAS`, the plan's cameras converted
  to the Blender frame; the plan stays Y-up, D12).  The build is one Blender process (`wrappers/run_bpy_scene.py`):
  env, heroes (GLB import, D6), assets and every zone each run in isolation.  A builder that raises is an
  ERROR on its `file:line`, and everything it created is removed, so the zone is dropped while the rest is
  still measured.  The geometry gates stay the JS ones.  The wrapper writes `artifacts/census.glb` with the
  harness writer (`wrappers/_census_glb.py`, from the evaluated depsgraph: collection instances become
  clones, other instance clouds become `EXT_mesh_gpu_instancing`, fog volumes are not matter, custom props
  become `extras`).  `probe_scene.mjs --glb` boots `runtime_js/lib/glb_scene.mjs` on it with no settle
  and no camera repair (D3).  `probes.run_probe(glb=, facts=)` merges the bpy facts the GLB cannot carry
  (fog, background, lamps, drivers, handlers, unique/instanced triangles).  D2: no draw gate offline; an
  ERROR above `MAX_TRIS_SCENE` UNIQUE triangles instead.  D10: Python drivers and added `bpy.app.handlers`
  are a lint ERROR and a census ERROR.  `AssetPlan.kind` gains `bpy` (D5).  The schema a planner is
  handed keeps scene_threejs's enum (`SCENE_ASSET_KINDS`), so every scene_threejs payload stays
  byte-identical (manifest not re-blessed).  scene_blender's tool list is held out of the manifest
  (`UNBLESSED_LANGUAGES`) and its prompt files are pending (`PROMPTS_PENDING`) until phase 3.  The
  object lint's bpy traps are shared (`blender.call_traps` / `store_traps` / `source_traps`), not copied.
  Measured on the design's probe scenes: bpy 520 760 / 619 968 triangles = census 520 760 / 619 968;
  build 5.0 s / 7.5 s (wrapper 3.4–5.3 s, of which GLB 0.1 / 0.8 s; JS probe 1–2.4 s).
  `render_scene` raises NotImplementedError until phase 2.

* **D102b scene_blender pictures come from Blender; the camera checks and the metrics schema stay the JS host's
  (2026-09-24, lane B of D102; written as "D103, the lead renumbers" — renumbered D102b, as its commit names it).**  DESIGN §11 phase 2, owner D1/D2/D3/D8.
  `spatial/render_blender.render_blender_scene` is what the language's `render_scene` calls: (1)
  `runtime_js/fit_orbit.mjs` fits the orbit rig to the build's `census.json` with the unchanged `orbit.mjs`; (2)
  `languages/wrappers/render_bpy_scene.py` opens `artifacts/scene.blend` and renders the authored cameras × every
  time plus the first three overviews at t0 — the judge's views and the motion pair, nothing else — with
  harness-owned settings (Cycles CUDA 32 spp adaptive + OIDN, fixed seed, AgX / exposure 0, RGB PNG; frames
  `1 + round(30 t)`); (3) `render_scene.mjs --external-frames` loads the census GLB through the language's host
  entry and computes every camera check on it, but its luminance statistics from the Blender PNG
  (`host_metrics.imageFrameStats` = the one `frameStats` on an image), no settle / repair / post; (4)
  `render_scene.render_set_from_metrics` (split out of `render_scene`, byte-identical for three.js) builds the
  RenderSet.  metrics.json has the three.js schema plus `language` and `blender` (engine, device, samples, the
  fallback, per-frame and per-step times).  GPU sharing (D8): `Settings.render.blender_gpu_slots` (2) flock'd
  slots under `<cache_dir>/slots/blender_gpu/`; a render waits `blender_gpu_wait_s` (60 s) and then renders on the
  CPU, and a GPU run that dies without a result is rendered once more on the CPU; a timeout is the scene's, no
  retry.  `blender_engine` (cycles | eevee — EEVEE runs under `gl_render.GPU_ENV`) and `blender_samples` (the
  profile dial: economy 16 · balanced 32 · quality 64).  The frame and placement gates keep one set of
  thresholds and carry a per-language fix-hint table (`frame_metrics.HINTS` keyed by `metrics["language"]`,
  `scene_placement.LANGUAGE_WORDS` by `census["language"]`); D2's render-time WARN is `slow_frame` (median
  judged frame > 10 s GPU / 33 s CPU, naming the content group with the most triangles).  `3dcode doctor` gains
  `blender cycles` / `blender eevee` / `blender gpu slots`.  Measured on this box, 1024x576, 7 frames (2 authored ×
  2 times + 3 overviews), GPU shared with other work: Cycles CUDA 32 spp 0.9-1.6 s/frame on the 520 k-tri probe
  scene (13.3 s for the whole render incl. 2.4-3.6 s geometry pass), 1.4-4.2 s/frame on the heavy one (21.1 s);
  16 spp 10.5 / 18.9 s; 64 spp 14.1 / 36.2 s; CPU 32 spp 33.7 / 80.9 s; EEVEE 64 24.3 / 66.4 s (first frame
  9-20 s of shader compile).  Not done here: the eye-level rig views get no Blender pixels (DESIGN §5.2: nobody
  reads them), so their exposure is unmeasured; `no_fog` overviews unlink only the world volume (a bounded fog
  object still renders).

* **D103 gemini-cli is deleted; Google's models run through agy (owner, 2026-10-13).**  gemini-cli and its
  retry layers were the heaviest vendor-specific burden in `agents/` (a per-session system settings file,
  chat-record reading, stderr retry-line parsing, a key-rotating retry loop inside the backend, a storm
  fallback above it), and agy 1.3.4 now takes a Gemini API key and an MCP server, so Google's models lose
  nothing.  Deleted: `GeminiCliAgent`, its system settings file, `read_gemini_chats`, `GeminiRetries`, the
  2–3-attempt retry loop with `retry_window_left` / `_retry_key` / `RETRY_KEY_WAIT_S` / `QUOTA_MAX_ATTEMPTS`,
  `Settings.binaries.gemini_cli`, `cli_common.invoke`'s `attempt` / `soft_timeout_s` (captures are always
  `stdout.json` / `stderr.log`), `find_json_object`'s multi-line scan (only gemini-cli printed indented
  JSON), the doctor rows `gemini-cli` and `gemini-cli skills setting`.  `agents/registry.KINDS` =
  `("claude-code", "codex", "agy")`; the default generator (and balanced / quality) is `agy:gemini-4-argon`.
  agy runs in **Gemini-API mode only** (owner, same day: no subscription sign-in, no Claude models, and
  the default is `agy:gemini-4-argon`, `DEFAULT_AGY_MODEL` for a bare `agy`; no keys → `available()` is
  false, the session fails at once) — one key per session from THE shared pool (`models.gemini.shared_pool`,
  `KeyPool.lease_session_key`, no in-flight slot; a machine-wide flock lease per key keeps parallel runs
  one session per key until the keys run out, then shares — added after a 14-run parallel wave put 4
  sessions on one key; `shared_pool` also starts each process at a random key), the outcome reported back (429 → cooldown, 401/403/
  `API_KEY_INVALID` → dead, 5xx, ok, skip); a fresh HOME under `<cache_dir>/agy_homes/…` holding
  `.gemini/antigravity-cli/settings.json` = `{"modelProvider": "gemini"}` (without it agy ignores
  `GEMINI_API_KEY` and demands a login) and `.gemini/config/mcp_config.json` with the `3dcode` server
  (`env.HOME` = the real home), deleted after the session once the tool trace is read from its
  conversation db; argv `--model gemini-api://<bare id> --effort <low|medium|high>` (any id goes verbatim,
  e.g. gemini-4-argon; effort from an agy-style `-low/-medium/-high` suffix, default `high` — the Gemini
  API's own default, what gemini-cli sessions ran at); priced at Gemini list price with output = output −
  thinking and thoughts = thinking (agy's `output_tokens` already include thinking: billed once);
  `PUPPETEER_CACHE_DIR` = the real cache for the agent's shell.  Deleted with the subscription mode:
  `available_models` / `resolve_model` (the `agy models` probe that added an effort suffix), the AGENTS.md
  shell-fallback paragraph, and the $0 booking.  No retry loop in the backend: agy retries its own
  model calls (≤ 30 s waits since 1.3.4, stops at once on a spent daily quota), and a transient death goes
  to the round loop's ONE transport retry (`tracks/lifecycle`), which takes a fresh key.  Workspace
  materialisation writes `AGENTS.md` + `CLAUDE.md` only — no `GEMINI.md` (agy reads it AND `AGENTS.md`,
  so the body was loaded twice), no `.gemini/settings.json` — and `.antigravityignore` replaces
  `.geminiignore` / `.aiexclude`.  doctor: CLI rows agy / claude / codex and a new `agy gemini keys` row
  (OK "N-key pool"; FAIL without keys).  Prompt manifest re-blessed
  (context files, generator id).  Consequences: **no single-shot storm fallback** — `tracks.common.
  generate_for` is plain `generate()`, `GenerationResult.storm` is gone, and so are the asset ladder's
  storm-repair rung (strategy `escalated+repair`) and the events `generate.storm_fallback` /
  `asset.storm_repair` (D68 superseded; `repair.storm` and the lifecycle transport retry stay); agy gets
  the 3dcode MCP tools (it had none); the GEMINI.md double load is fixed; effort defaults to
  `high`.  Live facts (2026-10-13, agy 1.3.4): API mode lists gemini-3.8/3.7/3.6-flash and 3.1-pro; the
  subscription sign-in no longer serves gemini-3.7-flash (only 3.8-flash, claude-sonnet/opus-4-6, gpt-oss).
  The profiles' $ / score expectations were measured on gemini-cli:gemini-3.6-flash and are not re-measured
  on agy:gemini-4-argon ($2.00 / $10.00 per M, ~2.7× 3.6-flash).

* **D104 Blender 5.2.2 LTS is THE supported Blender; the judged-frame fast path is Cycles CUDA + GPU OIDN +
  persistent data (owner, 2026-10-14).**  The owner replaced 5.0.1 with 5.2.2 LTS and dropped 4.2 / 5.0
  compatibility ("不需要兼容5.0.1了"): `BLENDER_MIN = (5, 2)`, `resolve_blender()` looks for `blender-5.2`
  then `blender`, doctor fails anything older.  Verified on 5.2.2 (this box, RTX 5090, driver 610.88): the
  whole offline suite (1634) passes unchanged; the API facts the lints and prompts state were re-probed —
  Principled inputs ('Anisotropic' and 'Specular Tint' still exist; 'Thin Wall', 'Thin Film Thickness/IOR'
  are new), boolean solvers are FLOAT / EXACT / MANIFOLD ('FAST' raises), sky types SINGLE_SCATTERING /
  MULTIPLE_SCATTERING / PREETHAM / HOSEK_WILKIE, `Material/World/Light.use_nodes` are deprecated (removed in
  6.0; every new datablock already has a node tree), geometry-nodes modifier inputs moved to
  `getattr(mod.properties.inputs, 'Socket_2').value = v` (`mod['Socket_2'] = v` raises TypeError — 5.2
  Python API notes), Blender's python is 3.13 and the user site is no longer loaded (the wrappers are
  stdlib + bpy + numpy only, as before).  Render measurements (1024x576, 460 objects): Cycles CUDA 32 spp
  + GPU OIDN 0.64 s/frame; with `render.use_persistent_data` the synced scene + BVH survive between the
  cameras of one job — 8 frames 5.3 s → 2.2 s, 0.21 s/frame after the first; 64 spp 0.69 s, 128 spp
  0.86 s; CPU 2.3 s; EEVEE in background 5.2 s/frame + a 12 s shader compile (slower than Cycles GPU here,
  so Cycles stays the default); Workbench 0.12 s.  OptiX is unavailable under WSL2 (libnvoptix: init error
  7804) and setting `compute_device_type = 'OPTIX'` then back to CUDA crashes Blender — the harness never
  probes it (`render_bpy_scene.setup_render`, doctor).  Shipped: persistent data on for every Cycles job,
  `Settings.render.blender_frame_time_limit_s` (20 s cap per frame via `cycles.time_limit`, 0 = none) in
  the job and in `metrics.blender`, the doctor times one 256x144 32-spp frame on the fast path, two lint
  traps shared by both bpy languages (`solver = 'FAST'` → ERROR; `x["Socket_…"] = …` → ERROR with the 5.2
  idiom), and the cookbook / contract / docs say 5.2 LTS.  Not re-measured on 5.2.2: the skills' "verified
  on 5.0.1" geometry numbers (subsurf shrink, primitive extents) — properties of the mesh code, not of the
  renderer; the live gate tests that exercise them pass on 5.2.2.

* **D105 The argon scene-tuning program: per-model variants, scene_blender first-class, scene judge v2 with
  strict placement standards, and the eval loop with argon as generator AND judge (owner, 2026-10-14/15).**
  The owner wants the harness specialised per model and looped — "变体按单个模型分", scene_blender "都可以补齐",
  a judge that "彻底解决 floating、穿模", "连通性…标准…严格一些", ~30 generated prompts per iteration that get
  harder, both scene languages at once, "argon 用来生成 code … 同时也要用来作为 judge", and a standalone repo.
  Decisions.  (a) **Variants** (`prompts/variants.py`): an overlay `codeverse3d/prompts/variants/<name>/`
  shadows prompt files (a jinja ChoiceLoader; the base environment is untouched so base rendering is
  byte-identical), `codeverse3d/skills/variants/<name>/` shadows bundles and `routes.toml` adds typed
  boosts / disables / rows (a standing row never reaches the gate-fired floor 90), `variant.toml` names
  version + generators + knobs ⊂ {skills_max, skills_only}.  Selected EXPLICITLY — `3dcode make --variant`,
  `Settings.variant`, or `Settings.variants` by the exact generator id then `<kind>:<served model>` — never a
  regex over the id (L4); frozen on `RunOptions.variant`; recorded in `environment.variant`, `extra.variant`
  {name, version, hash}, `prompt_hashes.variant` and `SettingsSnapshot.variant*`; the plan stage key names
  the variant only when set.  One variant per process (knobs are process-wide, the overlays are
  context-local).  The `argon` variant ships EMPTY (v1) so the loop's iteration 0 measures the base.  The
  judge is NOT variant-scoped: it is the instrument.  (b) **scene_blender** runs end to end: prompts
  (system / contract / cookbook / asset / effects_catalog), bpy sibling templates chosen by
  `tracks.scene.SCENE_TEMPLATES[language]` (the three.js payloads stay byte-identical), a planner template per
  (track, language) whose schema carries the language's asset kinds (`bpy`, `blender_glb`), a bpy asset ladder
  checked in Blender, a harness-owned starter library (13 modules under `languages/scene_blender/starter/src/lib/`
  — D51 extended to scene_blender; the skeleton is built from it), one transport retry per stage session, and
  three bundles `c3d-bpy-scene-{placement,lighting,materials}` on routes R29–R34 (R34 is the three.js gate-fired
  twin on the seating kinds).  (c) **scene judge v2** (`scene_v2.yaml`; `scene_v1` frozen for replays):
  defects floating_asset .10/.60, sunken_asset .08/.65, interpenetration .08/.70, wrong_scale .06/.75,
  hard_edged_particles, missing_planned_element, hero_misplaced; placement caps per measured kind incl.
  free_exempt .75; graded must-acceptance; a CALIBRATION block and six anchors per criterion so the judge uses
  the whole range; overview views are declared to hide ceilings; CONTACT CROPs at the worst placement findings
  (the gate writes `data.bbox`); a fourth overview `overview_low_front` (el 7°, `fitContactCamera`) and a scene
  montage cap of 4; the measured-absent veto is scoped to the rule's kinds with `veto_warn_keys` (a WARN-band
  hover keeps the judge's tick, a `no_fog` ERROR no longer cancels the floating veto) — SCORING_VERSION 4;
  `Settings.judge.thinking` (argon thinks only from 'medium') + `3dcode judge --thinking`; the fixed evaluator
  runs the placement gate on scene cells.  `judge_prompt_hash` moved for every rubric (RIG_RULES is shared).
  (d) **Strict placement standards** (`Settings.placement`, read at call time): floating ERROR 0.08 m or > 2
  (was 0.15 / > 3), sunk ERROR 0.20 m + 20 % (was 0.30 + 25 %), interpenetration WARN 0.10 / ERROR 0.35 (was
  0.20 / 0.60; the host's OVERLAP_MIN_FRAC is 0.10); new kinds `free_exempt` (share of 'free'-tagged assets
  > 25 % WARN / > 50 % ERROR — the tearoom run had tagged 47 %) and `cantilevered` (resting on ≤ 1 of ≥ 4
  foot columns); the summary carries measured counts and the thresholds; a hero's size is one instance, not
  the union of its clones; `scene_regate` / `scene_stats` count typed kinds and accept Blender records.
  Object-track connectivity thresholds are unchanged (follow-up).  (e) **The loop** (`eval/bench/loop.py`,
  EVAL.md §10): init / generate / run / diagnose / compare / judge-calib / accept / rollback / status.
  Batteries are generated by the planner model from typed rung rules (five rungs: zones, asset types,
  instance counts, lighting, interior / water / particles, a moving hero, scale constraints), ids stable per
  rung so iterations pair; `run` = one child `run_bench --variant` per language with `C3D_JUDGE__THINKING`
  and stop handlers; the judge of the loop is `gemini:gemini-4-argon` at medium on `scene_v2`; the verdict is
  `stats.decide` plus a placement-ERROR veto; the ledger is `eval/bench/data/loop/<name>/ledger.jsonl`;
  `accept` bumps the variant version and snapshots the overlays, `rollback` restores them.  (f) **Standalone**:
  the head-to-head scripts and batteries that compared this harness with other checkouts were deleted; no
  path under a home directory remains in the tree.  Consequences: the prompt manifest gained the scene_blender
  and variant scenarios and was re-blessed (judge payloads + the new `"variant": ""` spec field); ARCHITECTURE
  §2 names the starter tree; every C3D_VARIANT* switch is plan-side (no `--pin-plan`).  Known limits: agy's
  MCP config has no tool-timeout key; the materials bundle has no deterministic target (SKILLS_LEDGER §6.1);
  the loop's first live `generate` / 30-run `run` are iteration 0.  **Amended 2026-10-15 (iteration 0, first
  wave):** 24 of the 41 Blender `interpenetration` ERRORs were members of ONE room shell (lintel × lid, trim ×
  trim) — the placement host now exempts every row under a `RoomShell…` root as the enclosure (both scene
  languages; the same name the overview rig lifts; superseded by D111: a room shell is a scenery root of a
  marked scene and the name rule is deleted), and `lib.architecture.room_shell` clamps an opening's
  sill/head into the wall (a 3.2 m door head in a 3 m room had inverted the lintel into a slab above the lid).
  Runs of iteration 0 that built before the fix were gated by the old rule (noted in the loop ledger).
  **Amended 2026-10-15 (iteration 0 read, iteration 1 written):** 60 argon scenes (30 per language, rung 1,
  argon@medium judge): picked mean 0.758 three.js / 0.754 Blender, baseline 0.672 / 0.647 (the refine
  round is worth +0.09 and the 90-min clock cut it in 55 % / 43 % of the runs).  What the gates and the
  judge measured, and what changed for it: (a) placement — three.js 7/30 runs with interpenetration
  ERRORs + 5/30 sunken, Blender 13/30 + 5/30; every agy zone session had called `check_placement` and
  then edited without re-checking; 11 of the 29 three.js rows were props inside a `…MidProps` Group
  (measured as one union box), 9 of the 13 Blender runs had scattered small props INTO placed carts /
  racks / shrubs.  `lib.place.clear_mask` (Blender) + a `keepClear` recipe (three.js) mask placed
  footprints out of scatter; the cookbooks' placement chapters state "one placed thing = one direct
  child" and the slope rule; (b) `hard_edged_particles` 27 % / 17 % of runs, and the brief's "soft
  particle effect" must-item failed with it — Blender had NO soft particle at all (translucent
  Principled puffs, metaballs, tori): `lib.materials.volume` (a Principled Volume, no surface; 40
  puffs = +0.7 s per 32-spp frame on 5.2.2) and a "Soft particles" chapter in BOTH cookbooks, inlined
  into every zone brief (`ZONE_RECIPES`; the inlining budget rose 24 k → 30 k chars because the last
  chapter was being clipped on scene_blender); the three.js fireflies recipe now carries a radial
  `softDot` sprite instead of the bare `PointsMaterial` the judge penalises; (c) three.js zones
  DROPPED at assembly (a run scored 0.0 with three empty zones): `walk(fig)` at build time, `import {
  concrete }` of a name `lib/materials.js` does not export, `mergeGeometries` → null — the JS lint now
  verifies every named import against the target module's exports (`_js_lint.check_named_imports`,
  ERROR), the other two are prompt rules; (d) the brief's camera line "tracks / dollies along … at
  1.6 m" was a must-have no fixed-camera render can satisfy (unverified in 5/60 runs, a
  `missing_must_acceptance` cap on the whole score): the loop's brief generator writes a FIXED
  eye-level viewpoint and migrates the cached rung prompts in place (ids unchanged, so the pairs
  hold); (e) 172 `asset.generate_failed` outages in the first ten minutes (30 runs planning and
  single-shotting assets at once against the 64-slot pool; 8 zone stages fell back to single-shot and
  shipped the crashes in (c)): `run_bench --stagger` (loop default 40 s, the two languages offset by
  half of it).  The argon overlay (variant v1 → v2 on accept) now shadows `tracks/scene_zone.j2` and
  `tracks/scene_zone_bpy.j2` with a "Before you return" block stating (a)–(c) with their numbers, and
  `routes.toml` boosts the two placement skills to the top of the standing list (R14 75 → 89, R29
  80 → 89).  Not changed on purpose: the judge (frozen for the compare), the run shape (2 rounds, 90
  min, 15 + 15 parallel), the refine briefs (iteration 2's lever).  Skills deep-read rate stays low
  (three.js 0.013, Blender 0.175): the chapters now travel inside the brief instead.

* **D106 The scene track states REQUIREMENTS, never templates (owner, 2026-10-15).**  Iteration 0 of the
  argon loop produced 60 scenes of one shape — a block in the middle of a flat plate, ringed 360° by
  identical hills, three zones tiled in a ~40 m square, nothing beyond the bounds — and the owner ruled
  that copying must be impossible to reproduce it: the harness states what must be true, where, and how it
  is measured, and mandates no shape, count, construction, layout, camera formula or code structure the
  brief did not ask for.  Kept, and only these: (1) INTERFACE — file / function names the wrapper calls,
  ctx fields, frames / units / naming from `conventions.py`, what the harness hands in; (2) sandbox rules;
  (3) MEASURED requirements with their thresholds, phrased as requirement + measurement (never as the one
  construction that satisfies them), plus the library as an OPTIONAL toolbox under its real API names.
  "always / never / must" wording outside those three classes is gone.  DELETED: both scene cookbooks
  (`prompts/scene_threejs/cookbook.md` 1 015 lines, `prompts/scene_blender/cookbook.md` 1 365 lines —
  interface and language facts moved into `contract.md` / `system.md`, the craft condensed into SHORT
  optional practices in the skills: no code block over ~12 lines in any prompt or skill, prose + the
  library's API names, numbers only where a gate or the judge measures them); `ENV_RECIPES` /
  `ZONE_RECIPES` / `ZONE_RECIPES_MAX_CHARS` / `refine_recipes` / `cookbook_sections` and every
  `recipes=` kwarg in `tracks/scene.py` and `tracks/prompting.py` (the same four ENV + six ZONE chapters
  inlined into every session, "copy their numbers and snippets"); the plate-and-ring env SKELETONS
  (three.js `starter/src/env.js` CONTENT_RADIUS + radial flat mask + `worldShell` ridge +
  `makeOutskirts` under "TUNE these; do not replace", kept byte for byte by 29 of 30 scenes;
  scene_blender's `flat = span/2 + 15` feeding `terrain.make_height_at`, 21 of 30) — both are now
  placeholders: the interface exports, a level plate named Ground sized from the plan, one light rig, a
  fog, `INTERIOR` as data, and a comment listing the measured requirements; the zone / asset stubs no
  longer grid contents inside the bbox or say "keep everything inside it"; the RING GATE (`no_backdrop`
  ERROR whose hint prescribed "24-40 SOLID pieces at 0.6 × fog far", written by 57 of 59 plans) is now
  `world_edge`, a WARN stating the measure (standing geometry ≥ 2 m past 1.25 × the half-extent) with
  open closures; the `underdressed` ERROR with its 12-25 / 25-60 / 300-800 per-zone counts is an INFO
  `density_note` (the counts the brief states are its acceptance items); `out_of_bounds` is a WARN on
  PLANNED contents only, margin max(5 m, 10 % of the span) — unplanned scenery past the bounds is never
  reported; the three-depth-layer COMPOSITION rule (planner brief, both cookbooks, `scene_v2.yaml`,
  `prompt_builder`, `c3d-scene-composition`'s "Build in this order" + bounds-centre camera formula +
  ring arithmetic) and the camera recipe (D = 1.5 r / tan(fov/2), 3-4 cameras, "sees 2+ zones / ≥ 70 %
  of the bounds", frame-gate hints "distance ≈ 1.2 × span, fill the bounds"); "bounds = the playable
  area, as small as the brief allows" + "3-6 zones tile the bounds" + "terrain within ±1.5 m of y = 0"
  (`lib/terrain` `flat_radius` default 45 → 0); the argon `c3d-scene-composition +14` boost.  REPLACED
  BY: planner briefs that state the measurements (`cameras[0]` content ≥ 20 %, lens ≥ 0.5 m clear, no
  world edge from any authored camera, seat thresholds, exposure band, motion visible at t = 0..1.5 s,
  `stamped_ring` ≥ 8 same-size copies on a circle, unmet `must` caps 0.60) and define `bounds` as the
  DETAILED area the harness measures against, not a cage — the world continues past it; zone / env / asset
  / refine briefs and the ten scene skills rewritten as requirement + measurement + a few strong optional
  practices; the brief's `beyond` field (what continues past the detailed area, a must item); `scene_v2`
  anchors and defects rewritten to "what the BRIEF's place would have" plus a `diorama_look` defect
  (penalty 0.05, no cap); the loop's brief generator writes ONE fixed viewpoint at a height the place
  justifies and 15 prompts per language (was 30).  Consequences: `materialize_workspace` writes no
  cookbook section / file for a language that ships none; `prompts/_claims.toml` rows re-pointed at the
  contracts; `tests/prompts/manifest.json` re-blessed for every scene scenario; the judge's calibration
  (`bench.loop judge-calib`) must be re-run before the next `accept`, since what the judge is asked
  changed.  NEXT (done, D107): the other six cookbooks (blender, cadquery, threejs, urdf_blender,
  glsl_shader, opengl_python) follow the same rule — the graphics `seed_recipes` path and
  `select_cookbook_chapters` stay until then.  Open: `content_small` still grades `cameras[0]` as ≥ 20 % content excluding ground
  and water (a beach / moor / lake viewpoint fails by nature); planner temperature 0.4 unmeasured for
  plan diversity.

* **D107 Every track states REQUIREMENTS, never templates — no language ships a cookbook (owner,
  2026-10-15).**  D106's rule applied to the six remaining languages.  DELETED: `prompts/{blender,
  cadquery,threejs,urdf,glsl_shader,opengl_python}/cookbook.md` (999 / 413 / 523 / 434 / 293 / 166
  lines) and `glsl_shader/examples/aurora_ridge.frag`; their INTERFACE and LANGUAGE facts (what the
  wrapper calls, frames / units / naming, API traps that are build or lint errors, what the census /
  renderer measures) moved into `contract.md` / `system.md` as short statements, their CRAFT into a few
  short optional practices in the track's skills; recipe libraries, snippet collections and
  prescriptive numbers are gone.  KEPT exactly: the object tracks' bbox / dims / connectivity / joint /
  part-naming contracts, sandbox rules, triangle budgets — and ONE executable interface example per
  `contract.md`, which `tests/prompts/test_snippets_run.py` (blender / cadquery / threejs / urdf /
  glsl_shader) and `tests/graphics/test_gl_runtime.py` (opengl_python) run through the real runtime,
  pinning the interface (builds, exports, on the ground, moves), never a shape, a part name or a count.
  The PLUMBING went in the same change, so a cookbook is not a concept any more rather than a tolerated
  absence: `RunContext.cookbook_text` and every reader; `materialize_workspace(cookbook_text=…)`, the
  `.3dcode/cookbook.md` copy and the "## Cookbook" body section; `format_error_report`'s cookbook
  argument and `repair.relevant_cookbook_section` (the repair prompt now carries the error report, the
  lint findings' fix hints and the routed skills' pointers — nothing language-specific beyond that);
  `prompting.select_cookbook_chapters` / `is_always_chapter` / `COOKBOOK_SYNONYMS` / `COOKBOOK_ALWAYS`
  and the `cookbook_excerpt` template variable (generate_static / generate_articulated /
  generate_graphics / generate_static_part / repair lose their "copyable snippets live in
  .3dcode/cookbook.md" lines); `prompts/sections.py` whole (`find_section` and the splitter
  nothing called); the graphics recipe SEEDING
  (`tracks/graphics.seed_recipes` and its helpers, the uncached "recipes" stage, the harness-owned
  `src/recipes.glsl`, `compose(recipes_src=…)`, the `redefines_recipe` lint, `Settings.limits.seed_recipes`
  / `C3D_SEED_RECIPES`, the `recipes.seeded` event); `record.prompt_hashes["cookbook"]` (no new
  session records it; a pre-D107 run resumed after it carries its earlier sessions' hash
  forward, which is correct for those rounds).  PLANNER (the same rule, applied to the plan
  budget): the PLAN BUDGET block states a floor and a detail minimum ONLY when the request is
  evidence of them (a brief, or ≥ `EVIDENCE_MIN_CHECKLIST` checklist items) — the only case
  `plan_quality_complaint` re-asks on them; without evidence it states the ceiling and "nothing
  here sets a count" (the "boxes at different sizes" complaint rides along as soft, so "a stool"
  may be four boxes); the `target_leaves` line and every "(2 or more)" / "3-6 elements" count are
  gone; the graphics pass floor is 1 (`GRAPHICS_MIN` 3 → 1, `min_assemblies` 0) and the
  "pass count correlates +0.40" sentence, the three-dust-layer example and "numbers everywhere"
  left `plan_graphics.j2` (the graphics twin of D106's three-depth-layer rule); the graphics
  plan example is a tide pool with two passes (it was the CLAUDE.md demo brief, with a
  "Grade: vignette, aberration, tonemap + gamma" pass that every skeleton then carried as a TODO);
  `plan_static.j2` lost "do NOT nest a leg / prefer nesting" and the mandated dimension /
  ground acceptance items (the harness adds those itself); the brief's and the plan schema's
  "3-8 / 6-12 / 8-16" ranges are now the validators' real maxima ("up to N").  JUDGES
  (non-scene) softened where a criterion rewarded a construction rather than the object: the static
  0.9 calibration point ("every part crisp with bevels/chamfers/tapers/profiles") → "the edge
  treatment, profile and hardware the real object has"; `primitive_only` / `detail_below_part_count`
  (static, articulated, asset, reference) fire only where the real object has the shaping that is
  missing; articulated's geometry-detail anchors count the hardware the real mechanism SHOWS
  (concealed hardware is not expected); shader_v2's depth criterion credits the scales the subject
  has (a flat style the brief allows is not docked for depth) and `candy_saturation` excuses a palette
  the brief asked for; the judge's improvement-plan example no longer prescribes tapering legs from
  45 to 30 mm; the static proportion anchor states the contract gate's real tolerance
  (max(10 mm, 10 % of the extent) per axis) instead of an unmeasured ±5 %.  Every measured cap,
  floor and penalty is unchanged.  SKILLS: no code fence over 12 lines in SKILL.md or a reference
  (`tests/skills/test_library.py`, was 20 in the body only); `test_freshness` no longer treats prompt
  markdown as a source of callable names and fails any bundle that mentions a cookbook.
  CONSEQUENCES, read as known changes, not bugs: (0) a run made before D107 still rebuilds and
  resumes: the glsl build composes its `src/recipes.glsl` when the file exists
  (`glsl_shader.LEGACY_RECIPES`, never a stray file), and `ensure_materialized` re-materialises a
  workspace that still holds `.3dcode/cookbook.md` (`agents.materialize.needs_rematerialize`),
  which also deletes the file; (1) the glsl baseline loses the seeded recipes
  (measured 2026-08-26, refs_v2_graphics aurora brief, flash: seeding was the lever against the
  comb-of-bars aurora) — the next graphics battery is the measurement; (2) a single-shot repair prompt
  has no language-specific excerpt any more, so a lint ERROR's `fix_hint` is the whole guidance;
  (3) `tests/prompts/manifest.json` must be re-blessed once, after every lane's prompt / skill /
  skeleton change, for every non-scene scenario (and the scene ones whose judge text moved); (4) the
  judge calibration (`bench.loop judge-calib` / `addons.calibration`) is re-run before comparing an
  object-track battery with one judged before this change.
  PER TRACK, what replaced each cookbook: **blender** — contract.md states every measured rule with its
  number and gate (size tolerance, ground / footprint, the 2 mm contact band and 10 mm interpenetration
  ERROR, the 5 % island rule, triangle and time budget, export as authored), an "API traps the lint
  reports" list and a Materials section; four facts re-measured on 5.2.2 (a curve with
  `bevel_depth = 0` exports no node at all, `primitive_cube_add(scale=…)` without `size=1` doubles every
  extent, a level-2 subsurfed cube loses 16 % per extent, a hidden boolean cutter stays out of the GLB);
  the craft went to c3d-blender-forms / c3d-repeats-and-mirrors / c3d-part-contact / c3d-bbox-contract
  as options; the plan example is an object nobody asks for (`HerbDryingRack`), the articulated one
  carries its own summary, size and acceptance (it inherited the chair's).  **cadquery** — contract.md
  "Kernel facts" (workplane normals, `.box` centring, revolve r ≥ 0, the hemisphere trap, OCC
  refusals) and the measured thresholds; the import list now matches the lint, so `numpy`, `random`
  and the stdlib data helpers are stated as ALLOWED (a widening of what the prompt permits, not of
  the sandbox), and the INFO the lint raised on every `.fillet()` / `.chamfer()` is gone (the
  build's OCC refusal is the signal: uncaught it fails the whole build, which the contract now
  says; the floating-part ERROR is stated too); the interface example is an abstract Body + Knob
  × 2 like the other two languages'.  **threejs** — contract.md gains the geometry-axis facts, GLB
  naming, the export-material facts and a "What is measured" section; the part stub is a plain
  `BoxGeometry` with no recipe block or preset material.  **urdf_blender** — contract.md states the
  1 mm FK tolerance, the real limit / axis severities, and the clearance thresholds per pose; the
  harness's axis fix hint (`articulated_object.axis_fix_hint`, `joints_sweep`) no longer offers
  "or swap lower/upper".  **glsl_shader** — the recipe seeding is gone (above); contract.md states the
  frame-gate caps as `frame_stats.py` computes them; c3d-glsl-craft is short practices with no
  `references/` (telemetry reports its depth as unmeasurable); the skeleton's `src/common.glsl`
  is a comment and PI / TAU (it seeded hash / noise / fbm / the cosine palette / a gamma tonemap
  into every workspace — the recipe library in miniature).  **opengl_python** — the 195-line
  example became a 20-line full-screen pass and a "moderngl facts that decide whether the build runs"
  list; the skeleton is a stub.  SHARED: `prompts/system/harness_contract.md` (every agent workspace)
  no longer says "overlap by ≥ 2 mm" (the WARN line), "≥ 15 mm clearance", wall thicknesses, sample
  heights, "constants on top, one function per part" or a mandated tool order — it states what the
  gates measure (the floating rule as the gate measures it — the ground-touching component, not
  "touches another part"; scene bounds as the detailed area, not a cage; the build timeout and,
  on the static track, the triangle / build-time budget that every object contract now states
  with the `detail_budget` constants, pinned in `prompts/_claims.toml`); skills route R20 (c3d-threejs-shader-traps on a planned custom shader) is scene-only,
  since a ShaderMaterial does not survive the static track's GLB export; `examples/graphics_lab`'s
  recipe studies (`build_recipes.py`, `glsl_cases/`) went with the seeding.  THE RUNNABLE-EXAMPLE
  RULE: each `contract.md` keeps exactly one example that a test executes through the real runtime
  and that pins the interface only; the ~12-line fence cap holds everywhere except three executable
  interface examples the owner should confirm as exemptions — urdf's frame arithmetic (17 xml
  lines: the origins the wrapper verifies; its python block is 10), opengl_python's full-screen
  pass (19 lines: a moderngl program cannot be shorter and still build) and D106's two
  scene_blender modules.  REVIEW PASS (same day, three reviewers): the contact "weld" band
  (0.5-1.5 mm, "aim ~1 mm") is gone everywhere — flush is contact; the blender skeleton's
  self-check fails only past the ground gate's ERROR line and the layout text no longer says
  "glue only"; wrong facts corrected against the code (the wrappers SEED `random` / numpy /
  `mathutils.noise` and three.js `Math.random`; `mat.blend_method` exists on 5.2.2; the removed
  APIs the lint does not catch are listed as such; URDF `<axis>` is in the joint frame, world-aligned
  when every rpy is 0); the snippet collections in c3d-threejs-forms / c3d-blender-forms
  references became prose under the API names with only their trap lines.

* **D108 A stage that waits must not starve the next one: single-shot calls are bounded by their task,
  the zone window comes from the hard budget, unwritten zones are recovered (2026-10-15).**  Iteration 2
  of the argon loop shipped two Blender scenes with three EMPTY zones (0.18 each, both rounds).  The
  chain, measured: one single-shot asset call streamed 1 087 chunks for ~20 minutes before the retry
  layer gave up (31 of 2 697 asset calls ran past their 420 s task timeout, all the way to the 1 800 s
  retry ceiling; p50 96 s, p95 260 s) — `generate_files` bounded the call by the RUN's remaining
  clock, never by the task's own `timeout_s`; the asset stage took 47-49 min instead of ~4; the zone
  stage started 0.4 min before the scene track's 0.55 soft cap, so it was neither degraded to
  single-shot nor given a real window — an agent session clipped to the 180 s floor that read 24-27
  files and was killed before writing a zone; `zones.done` listed all three as failed and nothing acted;
  the refine round, whose targets named the three empty zones, spent its 15 minutes on asset files.
  Fixes: (a) `generation.single_shot_wait_s` — a single-shot call waits at most its task's
  `timeout_s`, never below `SINGLE_SHOT_MIN_WAIT_S` = 420 s (a stage degraded by a spent soft budget
  carries the budget's 120-180 s floor, shorter than a legitimate call) and never past the retry
  ceiling; tasks without a timeout (the object tracks) are unchanged; (b) `scene.zone_window_s` — the
  zone session's window is `ZONE_TIMEOUT_S` per zone clipped to the HARD budget minus `ROUND0_RESERVE_S`
  (600 s: assemble, build, render, gates, judge), floor `ZONE_MIN_S` (300 s); the zones are the scene,
  refine gets what they leave; past the soft cap the stage still degrades to single-shot;
  (c) `SceneTrack._recover_zones` — every zone the batched session left unwritten gets ONE single-shot
  generation of its own brief, side by side (`zones.recover` / `zones.recovered` events, a `.recover`
  label), before assembly; a recovery that fails leaves the stub as before.  Also measured, fixed in the
  same change: the judge's fog-free Blender views only unlinked the WORLD volume while `lib.sky.haze`
  builds a volume-only BOX (30/30 and 14/15 envs), so the placement views were rendered through the
  haze — `render_bpy_scene.hide_fog_boxes` hides volume-only meshes ≥ 8 m in `no_fog` views (soft
  particles stay; `fog_hidden` in the frame record).  Pinned: `tests/orchestrator_tracks/test_zone_starvation.py`,
  `tests/scene_runtime/test_render_blender.py::…fog_box…` (real Blender).

* **D110 Objects are baseline-first: ONE session drafts the whole object, ONE session refines it in place per
  round; agy's argon stays at effort high; a typed `placeholder_part` gate (owner, 2026-10-15).**  Every
  object language — static_object blender / threejs / cadquery and articulated_object urdf_blender — takes
  the draft-then-improve shape (the concept from astra3d-brilliana's Blender path, reimplemented; nothing
  copied): round 0 is `BaseTrack.baseline_tasks`, one agent session that writes every part file and the
  entry (`generate_static.j2` / `generate_articulated.j2`); the round is built, measured, gated, rendered and
  judged; every refine round is ONE session over the whole object (`StaticObjectTrack.allow_refine_fanout =
  False`, articulated inherits it) carrying the gate ERRORs — a placeholder part first of all — then failed
  must-acceptance items, then the judge's plan (≤ `max_refine_tasks`, compacted to
  `max_instructions_per_task` lines), with `measurement_vs_plan` kept.  EVIDENCE (argon loop iteration 0,
  15 runs per language, `--rounds 2 --max-minutes 60`): the scoped baseline (`d104409e`, 2026-08-24, motivated
  by correlations only — no A/B, no DECISIONS entry) was built for the parallel in-process api-agent and has
  run SERIALLY since `50734755` / `66175ec1` (the per-workspace session lock, api-agent deleted): 3-5 scope
  sessions × median 18.2 min + an assemble session against a 58-minute generation window, so blender /
  threejs reached the end of round 0 at the budget (61.0 min median), 0 of 30 runs finished a refine round,
  13 and 14 of 15 shipped placeholder or missing parts (33 % / 21 % of part files still the skeleton box)
  and scored 0.509 / 0.588 mean.  The single-session languages in the same iteration: cadquery judged round
  0 at 26.8 min, 0.934 median, refine beat it in 13 of 15 runs (picked 0.966); urdf_blender 29.6 min, 0.923
  → 0.969.  The two blender runs whose scopes all finished scored 0.907 / 0.932.  Session time follows
  generated tokens (ρ 0.94-0.96, ~72 tok/s); output tokens were 4.3-6.4 × the final files' tokens, and 85 % of
  the characters blender / threejs sessions wrote went through whole-file writes.  A parallel-sandbox trial (scopes side by side against placeholder
  neighbours) cut round 0 to 46 min and dropped the dining chair from 0.907 to 0.404 (floating parts) — no
  parallel work before a real draft exists.  THE DRAFT BRIEF states requirements with their measurements
  (D106/D107): every planned part at its box and joined before any detail — a session stopped at its window
  must still leave the whole object (`check_contract` / `check_connectivity` measure it); each skeleton file
  replaced in one write and then changed only with targeted edits.  The refine brief (`refine_object.j2`)
  says edit in place, never rewrite a whole file to change a few lines.  The measurement the briefs cite is
  the chair pair (iteration 0's dining chair rewrote files whole — 8 rewrites over 9 files, ~136k characters
  written; the baseline-first smoke's chair wrote each file once, ~49k), not the 4.3-6.4 × ratio: the smoke's
  rewrite-free draft still generated ~4.2 × its final code in output tokens, so that ratio is not a rewrite
  cost.  No agent refine is fenced to a part subset; a SINGLE-SHOT refine still is (`edit_only`, the
  `edit_only_these` branch kept for it alone) — it sees only the files inlined for it and could otherwise
  rewrite one it was never shown (review 2026-10-15).  REFINE WINDOW: an object refine session gets
  `static_object.object_session_window_s` — the agent default clipped to end `OBJECT_ROUND_RESERVE_S` (180 s;
  build + gates + renders + judge measured 0.7 min) plus the watchdog's `HARD_GRACE_S` before the run's hard
  ceiling.  Without it a busy session ran to the run clock: the smoke's blender refine (started at 25.8 of
  60 min, 30-min window + 5-min grace) was killed at 60.0 min after $4.63 and the round dropped unbuilt and
  unjudged, as were all three refines of iteration 0's chair.  PLACEHOLDER GATE:
  `check_contract` emits an ERROR `data.kind = "placeholder_part"` (`contracts.artifacts.PLACEHOLDER_PART`)
  when `spatial.contract.is_placeholder` holds — every measured row of the part ≤ 12 triangles
  (`PLACEHOLDER_MAX_TRIS`, what blender / urdf `primitive_cube_add`, three.js `BoxGeometry` and cadquery
  `Workplane.box` export), watertight with a volume ≥ 0.999 of its own box (`PLACEHOLDER_MIN_FILL` — a real
  12-triangle hexahedron does not fill it: review 2026-10-15 found a tapered cadquery fretboard at its plan box,
  fill 0.879, flagged; over 942 loop part rows the 68 true placeholders were all watertight at fill 1.000) and
  its extents within 1 mm (`PLACEHOLDER_TOL_M`) of the plan box, a single
  instance also at the plan centre (copies are placed by the symmetry hint, so only their size is compared);
  0 triangles (not measured) never fires.  KNOWN LIMIT, pinned: a real bare 12-triangle box built to the
  plan's millimetre fires too — measurement cannot tell it from the skeleton's, and the corpus held none
  (every hit was a part file byte-identical to the `skeleton` commit); keying on that git fact instead was
  not taken, the gate sees only the measurement.  Computed from `Measurement.parts`, no grep of names or sources
  (Law 4); the box checks for that part are skipped (it would pass them by construction).
  `orchestrator.build_refine_instructions` sorts it ahead of every other gate ERROR.  CONSEQUENCE, a known
  change: a contract ERROR fires the `contract_violation` cap (0.75), so a run that ships placeholder parts
  now scores ≤ 0.75 where the gates used to call those parts perfect.  EFFORT (owner, 2026-10-15, final:
  "argon 强度就直接设置high"): agy with gemini-4-argon runs at `--effort high` on every track — no default
  was lowered.  Plumbing only: `AgentJob.effort: AgentEffort | None` is filled from
  `GenerationTask.thinking` by `tracks.generation.session_effort` ONLY when a caller explicitly asks
  (`thinking` is now `Literal["", "off", "low", "medium", "high"]`, default "" = nobody asked; a single-shot
  call then thinks at `SINGLE_SHOT_THINKING` = medium, unchanged); agy's precedence is the id's
  `-low/-medium/-high` suffix, then `job.effort`, then `AGY_API_EFFORT` = high.  The misleading
  `thinking="medium"` on the object draft / refine, the graphics refine and the first repair attempt —
  which agy never honoured — are removed, so code and behaviour agree; the rebuild / later repair tasks ask
  for "high", which is agy's default anyway.  No caller asks for less than high today.  The effort used is on
  the session's `invoke` transcript line, in `result.json` (`AgentResult.effort`) and on its `cost.jsonl`
  row (`effort`).  claude-code takes `--effort` from `Settings.agents.claude_effort` (default xhigh) and
  codex `-c model_reasoning_effort=` from `codex_reasoning_effort` / an `@<effort>` id (default high); both
  ignore `job.effort` (behaviour unchanged) and now record what they passed.  RECORDED MEASUREMENT, NOT
  ACTED ON: the graphics loop's iteration 1 ran agy at medium (`agy:gemini-4-argon-medium`,
  `eval/bench/out/loop_argon_graphics/iter_01`) against iteration 0 at high: glsl_shader (n = 9) picked
  0.837 → 0.909, minutes 55.7 → 48.3, $40.50 → $37.51; opengl_python (n = 5) 0.932 → 0.939, 59.6 → 45.6
  min, $22.55 → $18.17 — unpaired, n ≤ 9, inside judge noise on opengl; argon stays at high.  DELETED (a
  deletion, not a move): `StaticObjectTrack.baseline_tasks` / `scopes` / `scoped_baseline_tasks` /
  `scope_system_prompt` / `part_template` / `assemble_template`; `tracks/depth.py` `PartScope`,
  `scope_groups`, `_files_for`, `_attachment_units`, `_pack`, `interfaces_text`, `scoped_generation_enabled`,
  `MIN_PARTS_FOR_SCOPED`; `prompting.scope_context`, `parts_table_for`, `part_details_for`;
  `prompts/tracks/generate_static_part.j2`, `assemble_static.j2`, `system/role_scope.j2`;
  `Settings.scoped_parts` (`C3D_SCOPED_PARTS` is now a dead key an A/B rig refuses, `plan_features`);
  `GenerationTask.phase` and the phase loop in `steps.run_generation_tasks` (its one user); the
  `generate.scoped` event; the scoped tests (`test_generation_depth.py`).  Kept exactly: the planner, part
  naming and one-file-per-part layout, the skeleton, the contract and connectivity gates, `detail_budget`,
  the picker.  Pinned: `tests/orchestrator_tracks/test_baseline_first.py` (one draft task owning every file,
  one refine task with the placeholder ERROR first, the single-shot fence, the refine window, articulated
  end to end with fakes),
  `tests/spatial_tools/test_placeholder_gate.py` (the rule; each language's REAL skeleton fires and a rebuilt
  part does not: blender, cadquery, threejs; a tapered cadquery extrude at its plan box does not; a bare box
  exactly at it does), `tests/agents/test_antigravity.py::test_effort_reaches…` /
  `…object_and_scene_tasks_run_argon_at_high`, `tests/agents/test_agent_job_wiring.py`.

* **D111 Stage honesty and measured scenery: the env stage counts only its own file, placement rows come
  from the plan's zone roots, a probe timeout is the code's (2026-10-16).**  Evidence: argon loop iteration 4,
  scene_threejs (`eval/bench/out/loop_argon/iter_04/scene_threejs`, 15 runs, read-only investigation of
  r1_01 / r1_02 / r1_08 / r1_10).  (1) The env session wrote `src/env.js` in 3 of 15 runs; in the other 12 it
  read for 7-10 min, was killed idle, and was credited with the `src/assets/*` files the asset single-shots
  wrote beside it (whole-tree diff, `attribute_changes`), so `stages/env.json` read `ok:true`; the same asset
  writes kept its idle clock alive (the 12 ended at 421-616 s on a 420 s window; the 3 that wrote ran into the 720 s hard cap).  Round 0 was unlit (dark_frame,
  no_fog, no_background; round-0 mean 0.394) and the single refine round wrote the whole environment from the
  skeleton; in r1_01 a zone author built a replacement world inside its zone file.  (2) `host_placement.
  collectAssets` made every direct child of every top-level root a placed asset, so a wall merged per material
  became overlapping body / flashing rows and a two-mesh bollard a cap "interpenetrating" its post.  Replayed
  with the stored scenes (probe unmarked vs marked): 9 / 10 / 3 / 15 placement ERRORs on roots outside the plan's
  zones in r1_01 / r1_02 / r1_03 / r1_10, and r1_02's and r1_10's final ERRORs were all of that kind.  r1_01 is
  mixed: its final round's 10 ERRORs were 5 real clips of the zone asset `BakeryStoop/BakeryStorefrontFacade`
  into `AlleyFallbackEnvelope` (337 meshes that a ZONE added straight to the scene, `scene.add`, not the env),
  the `zone_empty` the envelope caused by filling the 400-row table before `ScooterParkingNook`, and scenery
  pairs.  (3) r1_08's refine terrain made the zones' per-vertex raycast
  seating exceed the 120 s probe; the probe said "harness/driver failure, not your code", and the round spent
  3 x 240 s rebuilding identical code and was never scored.  (4) `build_refine_instructions` deduplicated on
  (target, `gate:<gate>`), so `no_fog` hid `no_background`.  CHANGED: `AgentJob` / `GenerationTask`
  `shared_tree` (typed; needs a `files_hint`, excludes `edit_only`): such a session's `files_changed` is its
  `files_hint` only (`cli_common.attribute_changes(only=)`, also `generation._attributed_fallback`) and the
  watchdog polls only those files (`cli_common.activity_paths`; `_latest_mtime` takes a file).  The scene env
  task sets it; `SceneTrack._env_stage` is ok only when `env_file(ctx)` is in `files_changed` or rewritten
  since the stage began (`_touched`, the zone stage's test) and otherwise runs ONE single-shot
  `_recover_env` before the zones (events `env.recover` / `env.recovered`; the env window the budget still
  allows) — both scene languages, through `env_file`.  Plain sessions keep the whole diff: a refine that also
  fixed a file outside its hint must not turn into `ok=False` and a RoundFailed.  The assembler marks every
  zone root `userData[conventions.ZONE_ROOT_KEY]` (`render_scene_js` `addZone`, the assembler's own probe), the
  scene_blender census GLB writes the same key into each zone node's `extras` (`--zone-key` argv →
  `_census_glb.write_census_glb(zone_key=)`; GLTFLoader turns it into `userData`); `host_placement` measures
  only the children of marked roots as rows — every other root is scenery: no rows, not in the free share, never
  paired with other scenery, listed in the table's `scenery` {n, names} and the summary.  Its children are still
  measured as SCENERY PIECES (`measurePiece`, the zone assets' own exemptions: a ground / water / enclosing piece
  is a backdrop): a support and something to sink into, something a zone asset is mounted on (`attached`, so
  neither the gate nor the boot settle calls a sconce on an env facade floating) and something a zone asset can
  clip (a zone-vs-scenery interpenetration names the zone asset `a`, the one its author can move — r1_01's five
  storefront clips stay measured).  A scene with no mark at all (one the harness did not assemble) keeps the old
  rule.  A table cut by its ROW CAP (`total` > rows; a time-budget cut still writes a row per asset) gives an INFO
  `zone_unmeasured` instead of `zone_empty` for a zone whose census group is built (`content` / `ground`, not
  `empty`), and instead of `missing_content` for the zone at the cut (the last row's).  The `missing_content`
  hint says where a row comes from: a direct child of the zone's group (scene_blender: a root object linked into
  the zone's collection).  A probe timeout is typed: `SceneRenderError.timed_out` + `phase`, the phase
  `probe_scene.mjs`'s watchdog reports (`armWatchdog`'s `onFire` returns it; `host_page.openHost(onBoot=)` marks
  the import); `run_scene_script` retries a watchdog timeout once on a browser of its own (a wedged shared browser
  times out the same way), and only `SceneRenderError.scene_timed_out` — the phase is `scene` (import,
  createScene, the boot settle), `census` or `update` (`render_scene.SCENE_PHASES`) — makes `probes.run_probe`
  file an ERROR `probe_timeout` on the entry file with no "not your code" hint, so `build_with_repair` sends it to
  a repair session instead of `MAX_HARNESS_REBUILDS` rebuilds.  The box keeps every other timeout: `host`
  (browser launch, page ready), `report`, `compile` (the shader preflight), `close`, the process clock (node
  itself starved) — and every census-GLB boot (scene_blender: Blender already ran the author's code; the GLB is
  the harness's export).  The `scene_probe` tool probes under the build's budget (`probe(timeout_s=None)`; it was
  60 s against the build's 120 s, so a timeout now the code's would have condemned a scene the build accepts).
  The refine dedup key carries the finding's `data["kind"]`.  BEHAVIOUR that changes with the mark: env scenery
  is no longer moved by the boot settle — it renders as authored (r1_03 replay: the settle had lifted the env's
  `PerimeterPromenadeDecks` 1.42 m, and the zone's landing deck now clips it, a new zone-vs-scenery finding); a
  floating env prop is no longer measured at all; planned content a zone adds with `scene.add` is not that zone's
  (`missing_content`).  Accepted, not fixed: a pre-D111 three.js run resumed or replayed keeps its unmarked
  `scene.js` (the assemble stage is keyed on the plan) and is measured by the old rule WITHOUT the RoomShell
  exemption (Law 4: no name rule comes back for old runs); an env session writes nothing to its own file
  inside its 420 s window → the same idle kill every agy session gets (agy prints nothing until it exits, so
  its only activity is its own writes), then `_recover_env`; an env session that COMPLETES without writing gets
  the generic silent-bail rerun before `_recover_env`, as the zones stage does (0 of 15 in iteration 4: all
  ended `timeout`).  DELETED: the `RoomShell…` name-prefix enclosure exemption in `host_placement`
  (D105 follow-up of 2026-10-15) — a room shell is a scenery root of a marked scene.  NOT changed: no prompt
  text, no template, no default env (requirement-only prompts untouched; argon effort stays high); the idle
  watchdog for read-only refine sessions; `probeColumn`'s ground-like burial rule (no zone-asset repro).
  `tests/prompts/manifest.json` re-blessed for the job `scope` payloads only (the new `shared_tree` field in
  every `AgentJob` dump, true on the scene env jobs) — no prompt, system, context or judge payload changed.
  Model-visible outside the manifest: the assembled `src/scene.js` (a file refine sessions read) gains the one
  harness-owned mark line per zone, and the `missing_content` / `probe_timeout` finding texts.
  Measure next: env-stage write rate (3/15 → ≥ 13/15 from `git log -- src/env.js`), round-0 mean, share of
  round-1 diffs that are 300+-line env rewrites, `build.harness_retry` events, placement ERRORs on non-zone
  roots (0).  Pinned: `tests/agents/test_shared_tree.py`, `tests/orchestrator_tracks/test_env_stage_honest.py`,
  `tests/scene_runtime/test_zone_roots.py` (env parapet merged by material + two-mesh bollards + room shell →
  no rows, the zone's sunken / interpenetration findings identical to a zone-only scene; sconces and a sign flush
  on an env facade attached and left by the settle, a kiosk pushed into it an interpenetration on the kiosk; the
  unmarked control),
  `tests/scene_blender/test_build.py::test_the_zone_mark_survives_the_census_glb`,
  `tests/scene_runtime/test_probe_timeout.py` (phases; the owned-browser retry; the process clock; the census
  GLB; one budget for the tool and the build; a real driver whose createScene blocks reports `phase: scene`),
  `tests/orchestrator_tracks/test_orchestrator.py::test_two_env_errors_of_one_gate_are_two_refine_tasks`,
  `tests/scene_gates/test_placement.py::test_a_zone_past_a_truncated_tables_cut_is_unmeasured_not_empty` /
  `::test_the_zone_at_a_truncated_tables_cut_is_partly_measured`.

* **D112 A session that takes steps is never idle-killed; the env recovery is checked; the clock leaves room to
  judge (2026-10-16).**  Evidence: argon loop iteration 5, both scene languages (the first iteration on D111;
  `eval/bench/out/loop_argon/iter_05/{scene_threejs,scene_blender}`, read-only investigation of 26 env stages and
  r1_07 / r1_08 / r1_09 threejs, r1_10 blender).  Round 0 rose (threejs mean 0.325 → 0.623, blender 0.584 → 0.675)
  only because D111's honest env stage handed the env to `_recover_env`; what was left lost on seven defects:
  (S1) agy prints nothing until it exits and D111 watched a `shared_tree` session's own file only, so a session
  READING was idle from second 0 and its soft window was a hard kill — 22 of 26 env sessions died at 420-423 s
  with nothing written (every iteration-4 session that lived ≥ 646 s wrote: 13 / 13), 4 of 21 refines at 901 s;
  (S2) `_recover_env` was `ok` when the file was touched: 5 of 21 recovered modules failed the assembly probe
  (blender r1_02's `SyntaxError` at `curb_ mats = …`, `materials.js does not export 'gravel'`, `pavingPatch is not
  defined`, `mineral_surface() got an unexpected keyword argument 'roughness'`, a `list` with no `.name`), and the
  two that never reached assembly were broken too (blender r1_10's `noise_ centered = …` did not parse, r1_04's
  passed a tuple rotation — a `TypeError`): S4 had put the placeholder back in both, which is why those two
  rounds 0 showed no sky;
  (S3) `is_interior` was `bool(flag) or infer_indoor(text)`, so `interior: False` lost to the word list
  (`GiantTrunkCorridor` → "corridor", "luminance inside 0.42-0.62" → "inside"): 6 of 26 outdoor plans measured at
  the 2 cm indoor tolerance — r1_07's three 4-5 cm boulder gaps became floating ERRORs and a 0.60 cap; (S4) in
  two blender runs an escalated asset session held the workspace while `env.recover` committed — r1_10's burlap
  session (+391 lines, `24282df`) and r1_04's woven olive basket (+482, `a336df6`) — and its scope check restored
  `src/env.py` to its own `head_before` (the 49-line placeholder); (S7) the placeholder's
  "no lamps and no world" was a WARN, so round 0 built, the repair fixed an asset, and every frame was 96-99 %
  black (0.109); (S6) the harness's own `lib/sky.js` set `scene.background = null` and its own gate raised
  `no_background` (threejs r1_03 / r1_09 / r1_10 — the first refine target each time); (S5) a watchdog kill was
  transient whenever agy's log held any provider retry — the same 901 s / zero-file kill was retried in r1_05 /
  r1_09 (one 4 s 503) and ended r1_07 / r1_08 as no_change; and r1_09's transport retry, launched with 15 min left
  and cut at the budget edge, committed the camera fix that capped the run and was never built or judged.
  CHANGED, in the investigation's order:
  (1) `cli_common.activity_paths(s, steps)` / `invoke(..., activity=)`: what the CLI records per step is activity;
  `AntigravityAgent.run` passes its session HOME's `AGY_STEP_DIRS` (`.gemini/antigravity-cli/brain`,
  `…/conversations` — by name: the watchdog's walk skips `.gemini`; the presence lock / updater / caches are
  start-up writes, not steps) for EVERY agy session, `shared_tree` or not.  A session that takes steps now runs to
  its hard limit (window + `HARD_GRACE_S` 300 s, still min'd with the run's deadline); one that records nothing for
  `IDLE_GRACE_S` past its window is still killed there.  Object / articulated / graphics agy sessions: the same —
  a reading refine was killed at its window (when nothing under `src/` / `public/` moved), now it may use the
  grace; every hard limit is unchanged (`timeout_s + HARD_GRACE_S`, the job's `hard_deadline_s`, agy's own
  `--print-timeout` a minute past it), so the worst case per session is the one it always had.  `SCAN_S` (the
  mtime poll, 5 s) is now a module constant tests turn down.  (2) `scene_placement.is_interior`: a bool flag —
  `ScenePlan.interior` or a dict's `interior` — wins either way; the words only when it is absent / None.  (3)
  `SceneRuntime.check_env(ws) -> str` (new, both languages): the env module alone — its own lint findings
  (scene_threejs: `lint` filtered to `src/env.js`, which includes the import-vs-export check; scene_blender:
  `lint_scene_source(role="env")`), then a build with nothing beside it (`probe_scene.mjs` on a deleted-after-use
  `src/_c3v_env_probe.js`; `run_wrapper(entry=src/_c3d_env_check.py, zones={})`), then `probes.unlit`; a check the
  harness cannot run is `""`.  `SceneRuntime.lib_interface(ws) -> str` (new): every `src/lib` module's exported
  names with their parameter lists as written (`_js_lint.export_signatures`; `ast` for bpy) — 6.9 k / 12.2 k chars,
  facts, no call examples.  `SceneTrack._recover_env`: single-shot → `check_env` → on failure ONE repair
  single-shot (`env.recover.repair`) whose prompt carries the check's words and the file as written; both carry
  the lib interface (`scene_env*.j2` blocks rendered only when the recovery passes them — the session's prompt is
  unchanged); a module still failing is replaced by what it overwrote (zones import the env's interface; a broken
  env breaks every one) and the stage reports failed; `env.checked` events.  (4) S4: `_recover_env` runs inside
  `cli_common.exclusive_workspace(ws)` — the session lock.  Chosen over "restore out-of-scope paths to HEAD":
  every commit is `git add -A`, so a harness commit inside a session's window also absorbs that session's
  in-flight writes, and restoring to HEAD would keep exactly the out-of-scope writes the revert exists to undo,
  on every track; with the lock no harness commit lands inside a window and `head_before` stays the whole truth
  (cost: an escalated asset session and the recovery wait for each other — they were serialised against the env
  session anyway).  S7: `probes.unlit` — no lamp and no world (bpy), or no light, no `scene.background` and no
  `scene.environment` (three.js: the analogue; a sky with no light keeps its WARN) — is an ERROR `unlit` on the
  env file: the build fails and the round's repair is handed the env before anything is rendered or judged (so the bare skeleton's build now fails on exactly that one ERROR — by design; a test fixture scene that lit nothing got a background).
  (5) S6: `makeSky` keeps a `scene.background` the caller set and gives an unset one the dome's own graded
  horizon colour (`skyRadiance` on the side away from the sun) — the gate is unchanged (it is right: an unset
  background renders the raw clear colour wherever the dome does not cover); `celestial.js`, the
  `effects_catalog.md` row and the `c3d-scene-atmosphere` skill say so.  (6) A floating row states its gap to the
  millimetre, the tolerance applied (`indoor/outdoor tolerance 0.0N m`) and why it is an ERROR (`past 0.08 m` /
  `N rows float, more than 2`); an unsupported row its gap and "within the … tolerance" — so the refine prompt's
  task lines (`GateFinding.as_line`) carry them; both refine templates state that `artifacts/scene_probe.json` is
  the probe driver's raw dump and `check_placement` returns the rows.  (7) `RoundPolicy.judge_reserve_s` (240 s:
  201 s measured in r1_09) and `min_session_s` (180 s); `RoundPolicy.can_open(budget)` gates every refine round,
  a refine round's transport / stall retry (`round.skipped_budget` → BUDGET) and the scene stage retry — round 0's
  retry is not gated (it has no round to fall back on); refine / rebuild tasks
  carry `GenerationTask.reserve_s` (their session's hard deadline ends that much before the budget);
  `BaseTrack._salvage` (was `_salvage_baseline`) also salvages a refine round: a budget stop after its sessions
  changed `src/` / `public/` since the last round's commit gets the same ONE bounded grace (max(5 min, 15 %)) and a
  no-generation round that builds, gates, renders and judges those edits.  The object tracks' own
  `OBJECT_ROUND_RESERVE_S` (180 s) is deleted: `object_session_window_s` uses `judge_reserve_s` (one reserve) —
  an object refine window now ends 60 s earlier, and no object refine round launches with < 7 min left.
  (8) `classify_end` has no `provider_retried`: a watchdog kill is never `transient` (the key is not cooled); it is
  `stalled` (`SessionEnd` / `AgentResult` / `GenerationResult` / `RoundFailed.stalled`) when the watchdog found it
  IDLE and it answered nothing, and a stalled round / stage session gets the transport retry's ONE more session
  when `can_open` holds; a kill at the hard limit (worked its window, wrote nothing) is not retried.  eval's
  one-shot drops a stalled cell as infra (`_oneshot.py`, beside transient / quota).  NOT changed: argon effort
  (high everywhere); `ENV_TIMEOUT_S` / `REFINE_TIMEOUT_S` (the investigation: widening a window buys nothing
  until the first write); the session-written env is not run through `check_env` (1 of 17 failed in iteration 4);
  the `run_state.json` control-file revert during `shared_tree` sessions (19 of 25 env sessions; no score effect
  measured); deferred, single cases: an establishing-camera FOV check at plan time (r1_09 content_small) and a
  ground-mesh-vs-`height_at` env check (blender r1_10 round 1).  Known, not fixed: an asset single-shot rewriting
  its skeleton stub while ANOTHER asset's escalated (edit_only) session runs is outside that session's files and is
  reverted by its scope check — the same lock-free-writer class as S4, which the single-shots (minutes of model
  call, many in parallel) cannot take the session lock for without serialising the asset stage.  Model-visible:
  `tests/prompts/manifest.json` re-blessed for the refine templates' scene_probe.json / check_placement sentence (13
  refine prompts) and the `makeSky` catalog row (12 prompts); outside the manifest: the recovery prompts' lib
  interface and check blocks, the placement finding texts, the `unlit` finding, the atmosphere skill sentence.
  Measure next (iteration 6): env files written by the session (4/26 → ≥ 20/26), recovered envs failing assembly
  (5 → ≤ 1), round-0 lighting mean (0.71 → ≥ 0.82), zero-file refines (4/21 → ≤ 1), no_change stops (5 → ≤ 2),
  `thresholds.indoor == plan.interior` (100 %), round-0 dark_frame from an unlit census (0), `no_background` on
  `makeSky` scenes (0), refine calls before the first write (median 23-30 today), `budget.salvage` rounds judged.
  Pinned: `tests/agents/test_agy_activity.py` (a fake agy recording steps under its HOME survives past its window
  to the hard limit, shared_tree and not; one recording nothing dies at the window; a hang ends the same with or
  without a logged 503), `tests/scene_gates/test_placement.py::test_an_outdoor_flag_is_not_overruled_by_indoor_sounding_words`
  / `::test_a_floating_row_states_its_gap_to_the_millimetre_and_the_tolerance_applied`,
  `tests/orchestrator_tracks/test_env_stage_honest.py` (check + one repair with the error and the lib index; twice
  failed → placeholder back; the recovery waits for a session holding the workspace),
  `tests/scene_runtime/test_env_check.py` / `tests/scene_blender/test_env_check.py` (real Chrome / Blender:
  a missing export, a ReferenceError, a SyntaxError, a NameError, unlit), `tests/scene_blender/test_layout.py::
  test_an_unlit_scene_fails_the_build_in_both_languages`, `tests/scene_runtime/lib/test_environment.py::
  test_the_sky_dome_keeps_or_sets_the_background_the_gate_reads`, `tests/orchestrator_tracks/test_refine_evidence.py`,
  `tests/orchestrator_tracks/test_transport_retry.py` (a hung refine retried once; a hard-limit kill not; no refine
  launched under the reserve; the refine deadline leaves the reserve; stopped refine edits judged; nothing to
  salvage when nothing changed).
  Review fixes (two reviews of the change, same day): (a) the `unlit` ERROR failed EVERY build while the env was
  the placeholder, mid-session too — an escalated asset session beside the env stage, a zone session after a
  failed env stage — and those sessions cannot write the env module (their scope check reverts it); the round's
  build still fails on it (its repair gets the env), but the MCP `build` tool reports an unlit-only failure as a
  `SCENE UNLIT — …` line above BUILD OK (`spatial.tools._env_unlit_only`; any other ERROR beside it is still BUILD
  FAILED); (b) `run_best_of_n`'s every-candidate-failed `RoundFailed` dropped `stalled` (a hung `--candidates 2`
  round 0 failed the run where one candidate got the retry) — carried now; (c) `RoundFailed.of` sets `stalled` only
  when EVERY result hung or died of transport: the loop retries a round whole, and a sibling that worked to its
  hard limit is not to be re-run; (d) `RoundPolicy.can_open(budget, *, single_shot=)` / `need_s`: a single-shot
  generation (the economy profile) needs the judge reserve only, not the agent session minimum (it would have
  stopped as BUDGET with 4-7 min left for a one-minute call); (e) bpy `check_env` quoted the innermost `src/`
  frame — `src/lib/mesh.py:69` for r1_04, a file the repair single-shot never sees — and now leads with the env's
  own calling line from the traceback (`scene_blender.env_frame`: "src/env.py:287 `sky_batch.box(` (raised in
  src/lib/mesh.py:69)"); (f) `_js_lint.export_signatures` listed `export const asphalt = (o = {}) => …` as a bare
  name (25 of 208 lib exports, all 18 material factories) — a const bound to an arrow / function expression is
  listed with its parameters (7 bare names left, all constants); (g) eval: `stalled` is infra wherever `transient`
  was (`_infra._looks_infra` — a harness run whose round 0 hung twice; compare_backends' bare arm with no file;
  `_bare_agent` does not cache a hung session as done), as the one-shot already did — before D112 the same hang
  was transient whenever the CLI had logged a provider retry.  Pinned:
  `tests/orchestrator_tracks/test_transport_retry.py::test_a_split_round_is_stalled_only_when_every_session_hung` /
  `::test_hung_best_of_n_candidates_get_the_round_loops_retry` / `::test_a_single_shot_refine_needs_only_the_judge_reserve`,
  `tests/spatial_tools/test_tools.py::test_a_session_build_reports_an_unlit_env_above_build_ok`,
  `tests/scene_blender/test_env_check.py::test_check_env_leads_with_the_env_line_when_a_lib_helper_raised`,
  `tests/scene_runtime/test_env_check.py::test_an_arrow_function_export_is_listed_with_its_parameters`, eval's
  `test_infra_failures.py::test_a_hung_*` and `test_bare_agent.py::test_a_hung_session_is_not_cached_so_a_redo_runs_it`.

* **D113 Raw mode for scene_blender: an opt-in run where no harness library ships and the model writes every
  line of bpy — the `argon_raw` variant (2026-10-17).**  WHY: the owner's game-world demo must have ALL scene
  modeling code written by `agy:gemini-4-argon`; every scene_blender workspace shipped the ~3 000-line harness bpy
  library (`src/lib/*.py`: `vegetation.tree`, `architecture.timber_house`, `terrain.boulder`, `figure`, the material
  recipes, the sky worlds …), so part of every scene's geometry was harness code.  WHAT: `Settings.scene_lib`
  (`C3D_SCENE_LIB`, default **on** — every existing run and the manifest's base and `argon` payloads are
  byte-identical) and, OFF, raw mode: (1) `write_skeleton(..., lib=False)` writes no `src/lib/`, and the env /
  asset / zone stubs are raw bpy / bmesh that import and name nothing of it (the env placeholder still builds ONE
  level plate named Ground and nothing else); the plan-less example is refused (`ValueError`: it is built on the
  library); (2) the lint drops `lib` from the workspace packages — an import of it is a `bad_import` ERROR saying
  no library ships, shared code goes in `src/env.py` (zones `from env import`; an asset module is self-contained:
  its check builds it without `env`); a write under `src/lib/` is still reverted (`HARNESS_OWNED_SRC`);
  (3) ONE decision, per run: `prompts.variants.run_knob("scene_lib", settings)` — a stated value wins; inside a run
  (the `activate` ContextVar, per thread and copied into fan-out) the ACTIVE variant's `[knobs]` value, else the
  default; outside any run the settings object as it stands.  `SceneBlenderRuntime.ships_lib` asks it at CALL time
  (`lib_interface` → `""`), `scene_assets.ships_lib(ctx)` asks the run's real runtime (a stand-in runtime: the
  same rule over `ctx.settings`) and gates the catalog rows (`library_rows` → `""`) and the asset API's placing
  line (a collection-instance Empty, not `lib.scatter.instance`), and the two placement hints that named
  `lib.place.seat` say what it does (`scene_placement.RAW_BPY_WORDS`); the skeleton stage's cache key carries
  `scene_lib: False` in raw mode only (a pre-round resume that changed the mode rewrites the skeleton, and a raw
  skeleton removes a library one's `src/lib/`; library-mode keys are unchanged); (4) `scene_lib` is a variant knob
  (`prompts/variants.KNOB_FIELDS`, bool) of a new kind, `RUN_KNOBS`: `knobs_applied` reports it but never writes it
  onto a shared `Settings` — the bench runs `--parallel N` runs as threads of ONE process sharing the singleton, and
  a written knob, put back by whichever run ended first, flipped its siblings between raw and library mode mid-run
  (the review's two-thread race; `skills_max` / `skills_only` keep the old write-and-restore) — and it is applied
  by the MCP server at start (`mcp_server.apply_run_knobs`: the spec's frozen variant,
  else the process switch by the spec's generator), because the server is its own process and its `build` lint
  would otherwise pass a `lib` import the round's lint fails; (5) the variant `argon_raw` (v1, generators
  `agy:gemini-4-argon`, `[knobs] scene_lib = false`) shadows, for scene_blender, `scene_blender/{system,contract,
  asset,effects_catalog}.md`, `tracks/scene_{env,zone,asset,refine}_bpy.j2`, `tracks/plan_scene_bpy.j2` and
  `system/harness_contract.md` with copies that replace every library reference by what the gate measures and the
  raw bpy that meets it (seating from the root's lowest world-space vertices, boxes kept apart, soft particles as
  volume-only materials, motion on the slotted action's channelbag f-curves or simple `frame` drivers), plus the
  owner's realism REQUIREMENTS stated as what is judged / measured (multi-scale procedural surfaces, organic
  silhouettes, a world that continues past the content); the contract keeps ONE executable example per module
  type (env + one zone) in raw bpy / bmesh; the effects catalog is a statement that no library ships plus the
  generic Blender 5.2 / renderer facts; the skills overlay shadows `c3d-bpy-scene-{placement,lighting,materials}`
  with lib-free copies that keep every claimed sentence (their `_claims` are the base's), `routes.toml` with no
  rows.  HOW: `3dcode make "..." --track scene --language scene_blender --variant argon_raw` (or
  `C3D_VARIANT=argon_raw`; `C3D_SCENE_LIB=0` alone gives raw mode under the BASE prompts, which still describe the
  library — not a supported combination).  Model-visible: a new manifest scenario
  `scene.scene_blender.agent.variant_argon_raw` (43 payloads, blessed); no existing payload moved.  Pinned:
  `tests/scene_blender/test_raw_mode.py` (raw skeleton, refused example, the knob at call time, two overlapping
  runs in threads keeping their own mode, the real runtime deciding for the track and the skeleton key, the lint in both
  modes, the knob's typing and precedence, no overlay file names the library, every overlaid template renders
  with the scene track's context, the whole run offline, the MCP server applying the run's knobs, and in Blender
  the raw placeholder env + an empty zone),
  `tests/prompts/test_snippets_run.py::test_the_argon_raw_contract_examples_build_through_the_runtime_in_raw_mode`,
  `tests/core/test_doctor.py::test_doctor_skills_reports_every_shipped_overlay` (two shipped variants, one row each).

* **D114 A NaN in the model's bpy scene is the MODEL's build error; a `.blend` keeps the images made in memory
  (2026-10-17).**  WHY: (1) a live scene_blender run (`world_demo/scenes_v1`, `w6_sky_island_ruins` round 2):
  `src/assets/mossy_arch.py` left NaN vertices (`MossyArchStone_v0`, ~455 of 3 900 evaluated), the zone
  (`middle_cascade_zone.py`) placed its arches at a `z` computed from those vertices — NaN locations; rebuilt
  from the round's commit, the old writer put the NaN into the GLB's JSON (accessor `min`/`max`, and
  `"matrix":[NaN,…]` from the placement), the probe host's
  `JSON.parse` failed, `offline_report` marks any host load failure `harness_failure`, so `build_with_repair`
  re-ran the identical build twice (`build.harness_retry` ×2) and gave up — the model never got a repair task
  naming the object and the round scored None (~$2.8, 20 min).  The static and URDF paths had the quieter twin:
  the official glTF exporter DROPS a NaN translation (the part exports at the origin, measured on 5.2.2) and
  writes NaN vertices into the buffer.  (2) `run_bpy.py --blend` and `run_bpy_scene.py`'s `scene.blend` were saved
  with `save_as_mainfile(copy=True)` and nothing packed: an image the script made in memory (`images.new` +
  `pixels`; a character's 32 generated textures) reopens as a blank generated image — black.  `scene.blend` is
  what `render_bpy_scene.py` opens for EVERY judged frame, so on scene_blender this was a judged-frames bug: any
  procedural texture baked through `pixels` was judged black, while the census GLB (no images) and the gates
  never saw it.  WHAT: (1) `wrappers/_finite.py` (numpy + duck-typed bpy, unit-testable): `own_channels`
  (location / the rotation channel the object's `rotation_mode` uses / scale / the deltas /
  `matrix_parent_inverse`), `transform_culprit` (walks up the parent chain: the Empty whose rotation is NaN is
  named, "makes 'Arch' non-finite"; nothing on the way → `matrix_world`, a constraint or driver), `Offenders`
  (one row per object × kind — transform / vertices / instances of a scatter cloud — with the FILE that built the
  object) whose `report()` is the build error: `error_type` `NonFiniteTransform` (`NonFiniteGeometry` when only
  vertices), a message naming each object, its file and channel values plus the usual causes, `error_file` the
  first known file, the rows under `nonfinite`.  `_census_glb` checks every node matrix, every mesh's vertices
  and every cloud's instance matrices as it goes — an instanced mesh's NaN vertices never hide a NaN in what
  places it (one report, not two repair attempts; a geometry-nodes cloud is named by the mesh it scatters) —
  skips the offender and raises `NonFiniteError` before writing anything; `run_bpy_scene` reports it as the build's error (not `CensusGlbError`, which stays the harness's), so
  `run_wrapper` → `build` returns a failed, NOT `harness_failure` result and the repair loop hands it to the model
  like any script error (the env check quotes it; the bpy asset check reports it file-first, not as "did not finish").  The file: `Stages` maps
  every object a builder that returned created (`as_pointer`) to its file — `src/zones/<z>.py`,
  `src/assets/<a>.py`, `src/env.py`, the entry's own top-level `src/scene.py`.  `json.dumps(allow_nan=False)`
  is the last line (a writer bug, then, is a `CensusGlbError`, never a NaN on disk); a non-finite float custom
  property is dropped from `extras`.  `run_bpy.py` scans what it would export (`scan_objects`: world matrix +
  evaluated vertices) and exports nothing on a hit; `run_bpy_links.py` scans each link's group before baking it.
  Measured: an `inf` written to a transform channel is clamped by RNA to FLT_MAX (finite), so NaN is the case
  that reaches a writer; the w6 round-2 source rebuilt with this change fails as `NonFiniteTransform` at
  `src/assets/mossy_arch.py`, 9 objects named (the arch's vertices + each NaN-placed arch with its zone file),
  `harness_failure` false.  (2) `run_bpy.save_blend` packs every non-viewer image that `is_dirty` (8-bit → PNG,
  float → OpenEXR, measured to reopen with the same pixels; an untouched generated image regenerates and is left
  alone — note a generated image's `type` is `UV_TEST`, not `IMAGE`) before `save_as_mainfile(copy=True)`; both
  `object.blend` and `scene.blend` save through it.  Model-visible: only the new error text on a NaN build; no
  prompt payload moved.  Pinned: `tests/scene_blender/test_nonfinite.py` (the detector, the report, the writer
  refusing a NaN matrix / custom property / JSON; in Blender a zone with a NaN location and a NaN-rotated parent
  failing with both objects and `src/zones/ruins.py` named, no `census.glb`, and `build_with_repair` dispatching
  a repair — no `build.harness_retry` — whose report leads with the file; `scene.blend` reopening a generated
  texture with its pixels; an instanced NaN-vertex mesh AND its NaN placement both reported, a geometry-nodes
  scatter named by its mesh), `tests/scene_blender/test_prompts.py` (the asset check's NaN error),
  `tests/blender_cadquery/test_blender_nonfinite.py` (NaN location / NaN vertex fail
  the static build, `object.blend` keeps an 8-bit and a float generated image),
  `tests/urdf_joints/test_live_blender.py::test_a_nan_in_a_link_is_the_scripts_error`.

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
