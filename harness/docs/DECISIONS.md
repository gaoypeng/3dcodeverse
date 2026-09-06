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
* **D4 Parallel refine only when it is safe.**  Fan out only when at least
  `RoundPolicy.parallel_min_tasks` (default 2) file-disjoint groups exist and every task
  maps to a file (threejs parts, scene zones/assets/env);
  single-file languages get one whole-object task.  Consequence: `Workspace` itself
  holds a per-root lock + index.lock retry (workspace.py).
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
  depth-aware: a penetration WARN measured at `VETO_PENETRATION_DEPTH_M` (8 mm) or deeper is not
  "measured absent" — the WARN-blind veto switched off 163 claims, and since (d) a stile 17 mm
  through a seat is a WARN too.  8 mm, not 5: at 5 the paired re-judge marked the pipe tee's
  5.8 mm branch socket — the canonical designed weld — as the defect (−0.32 on that side), and
  the corpus holds only 9 standing claims in the 5–8 mm band against 16 at 8–10 and 43 with an
  ERROR behind them.  Replayed with (b):
  230 verdicts move (0 down), mean +0.086, pass 15.0 % → 21.0 %, pearson(gate errors, overall)
  −0.219 → −0.301, vetoed on replay: interpenetration 130, floating 72.  articulated_v1 keeps
  `gate: "*"` — no single gate owns either measurement there (joint_sweep + connectivity).
  `Rubric.content_hash` hashes what the YAML declares, not the model's defaults, so a schema field
  (`measures`, `graded`) never re-keys recorded verdicts of an unchanged rubric.
  (b) **The acceptance cap is graded**: `cap + (1 − cap) · verified/total` must items.  Pass/fail
  unchanged (`must_missing` still fails).  Without it 98 of the rounds (a) frees are re-pinned to
  0.600 by the flat cap; with it the 0.600 spike falls 130 → 20.
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
  (j) **An OPEN planned join stays report-only** — the (e) policy, now measured (census
  2026-08-30: 217 static_object runs, 2 106 resolved planned rows — 0 unresolved names, was
  965 — 223 OPEN across 92 runs).  181/223 are a child measurably welded to OTHER parts (mean
  overall 0.630; the judge had already priced 74 %), 41/41 genuinely detached children already
  carry the floating ERROR (mean 0.326, their runs already fail), and the class an upgrade
  exists to catch — touches nothing, unflagged — is EMPTY.  An ERROR's entire marginal bite is
  46 currently-passing runs (mean 0.643, seven at 0.83–0.96) whose 94 open rows all read as
  planner noise on the sheets (0/19 eyeballed rows show a defect the gate+judge miss in a
  passing run: aprons weld into legs, a wheel rim rides its 8 spokes 419 mm from the hub the
  plan named) — the invented `attach_to` edge becoming law, D45's failure verbatim, plus a
  PROTECTED refine round each.  Severity may not be smuggled through the kind string either:
  `rubrics._finding_tokens` matches cap kinds against data/message/target, so `kind=open_join`
  caps nothing while `kind=no_contact` silently adds the 0.6 cap AND blocks the measured-absent
  veto.  Re-open only if a future battery's ledger census shows a touches-nothing-unflagged row
  (free to count), or a paired A/B (~$5.50, 46 runs × 2 arms) shows the judge under-prices the
  OPEN lines it already reads.

* **D47 Judge payload v3: the 14-view rig through the montage machinery (2026-08-31).**
  Supersedes D30's ≤ 3-montage cap.  `OBJECT_VIEWS` becomes the brilliana 14-view rig
  (three rings ±30° + eye-level cardinals + poles); the measured legacy clay cameras
  move to their own `OBJECT_CLAY_VIEWS` (its `top` is el 88, the rig's is 90 — clay
  tiles carry `mode != shaded` and label their own cameras); `OBJECT_RANK` packs
  montage 1 as {front_right_high, back_left_high, top, bottom} so a cap truncation
  still sees the underside; `MAX_MONTAGES` / `Judge.montages` / every profile's
  `judge_montages` go 3 → 5 (at 3 the low ring + poles are silently dropped; flash-C
  is $0.032/verdict, so economy keeps the full payload too).  `OBJECT_VIEWS_QUICK`
  stays 4 views by name, so agent contact sheets / texture pass / candidates / scene
  assets keep their cost.
  Measured: 4-arm A/B, 42 items (18 corpus rounds + 24 h2h sides) × n=3 pro, plus
  3.7-flash and 3.6-flash replicas, ≈ $47.  Arms: A = 8 views, no clay (old
  baseline); A2 = production-faithful 8 views + clay; B = 14 labelled 640 px single
  views; C = 14 views + clay through our montage machinery (this decision).  The
  only comparison that survives multiplicity is same-cap C−A **+0.038** (n=29,
  SE 0.013, t≈2.98); the raw deltas C−B p=.041 and C−A p=.024 do NOT survive Holm — cap flips
  make the raw delta distribution heavy-tailed (sd(d) ~0.10–0.14).  The cap story
  is the visible one: `untextured_flat` cap-rule fires 11(A) / 6(A2) / 8(B) /
  **3(C)** — more sky and underside pixels stop the judge inventing flatness.
  B rejected: $0.198 & 67.5k input tok/verdict (C: $0.155 / 40.9k), its −0.025 vs A2
  is entirely cap flips (~half contradicting the pixels on eyeball), and its
  "best defect-hunter" halo was text-quoting — the provenance pass over every
  defect-PRESENT vote shows interpenetration pure-view TP 0/14 in EVERY arm (the
  14/14 naive TP measures reading comprehension of the shared gate text, not payload
  quality), and B's floating lead reduces to one genuinely visual item.  Flash
  replicas (3.7 and 3.6): payload Δ ≈ 0 — the rig pays only at pro tier, which is
  the verdict judge anyway.  The SHIPPED grouping was itself re-measured (arm Cprod, $7.2, same 42 × n=3):
  montage grouping ALONE moves the mean −0.044 vs C's accidental grouping — as large
  as the rig effect itself — because leading montage 1 with the poles ends C's
  dirty-item leniency (the 11-error windmill: C 0.51 → Cprod 0.04; clean/dirty gap
  0.134 → 0.155) and lands ≈ A2 overall (−0.016, inside noise).  A payload experiment
  must therefore measure the exact grouping it ships; this entry's operative numbers
  are Cprod's.  Underside full-res singles (arm Cplus: Cprod + bottom and
  front_right_low appended as 768 px singles, $6.8): recovers the espresso-class
  underside catch (0.795 with render_artifacts flagged, vs 0.965 blind in Cprod) but
  degrades everything else — worst clean/dirty gap 0.112, highest row-σ 0.040, and
  the windmill relapses to 0.62 with all three samples dropping the floating and
  primitive reads — measured and REJECTED; a variant that replaces the semantically
  odd bottom ground-band crop instead of appending stays queued.  Temperature
  (A2 @ t=0, $6.0): σ 0.037 → 0.022 with 8/42 rows fully deterministic — temperature
  is ~40 % of re-judge noise — but the mean shifts −0.037 and pearson(gate errors)
  drops −0.195 → −0.126: re-keying every σ threshold to buy half the noise is a bad
  trade; **t = 0.2 stays**.

* **D48 Conditional cross-section slices + provenance elicitation on gate-ERROR rounds
  (2026-08-31).**  Context: the judge's interpenetration verdicts were reading
  comprehension of the gate text, not seeing (D47's provenance pass: pure-view TP 0/14
  in every arm).  Three measured iterations on the same 42-item battery (n=3 pro),
  under a pre-registered stopping rule — adopt only if the guarded image-cited
  interp majority on the 14 conn-dirty rows reaches 8/14, with a per-vote provenance
  guard that verifies every "visible in slice n" citation against the slice manifest
  and PNGs: **v1** (slices always on + a primed rig text) hit 8/14 but primed the
  defect and damaged clean rounds; **v2** (de-primed: F1 hatch only gate-ERROR pairs,
  F2 neutral legend "measured overlap (gate ERROR)", F3 anti-over-read sentence, F4
  degenerate-slice drop) killed the clean-row damage but fell to 5/14 — the judges
  still *saw* (the catches stayed) but stopped narrating; **v3** (conditional: slices
  + one neutral elicitation sentence ONLY on rounds whose connectivity gate carries an
  ERROR; every clean row byte-identical to production by construction) restored
  **8/14 guard-verified** (2/23 citations fabricated, both guard-caught, 0 rows
  flipped) with honest negatives: on the 4 rows whose error pairs miss both centre
  planes the judges wrote text-only evidence in 10/12 present-votes instead of
  inventing citations.  Both real catches retained (gate_valve wrong_orientation
  3/3, clock_theirs hatch-verified interp); dirty-row mean at CPROD parity excluding
  the one collapse; clean−dirty gap 0.155 → 0.198.  Cost: dirty verdict **$0.154 vs
  $0.172** baseline — cheaper — so amortised ≤ $0 and no profile dial moves.
  Shipped: `spatial.sections.judge_slices` (+ typed `SliceManifest` beside the PNGs —
  citations stay auditable), `JudgeInput.glb_path`, `Settings.judge.slices`
  ("on-error" default | "off", `CV3D_JUDGE__SLICES`), slices appended AFTER the
  montages/crops with the F2/F3 rig text and the elicitation sentence in the DEFECT
  CHECKLIST bullet; clean rounds byte-identical (test-pinned), `judge_prompt_hash`
  untouched.
  **Watch item ANSWERED 2026-08-31 — the channel stays, two sentences change.**  The
  battery ran on a REBUILT corpus (the original scratchpad was destroyed by a /tmp
  cleanup, so this is a fresh selection, not a replay): all 223 `static_object` runs
  under `bench/out` with a GLB + plan re-gated live (92 dirty / 131 clean), then 16
  dirty (12 blender / 4 threejs, 12 batteries, stored 0.052–0.980, 1–37 ERRORs, five
  ≥ 0.88) + 6 clean controls re-rendered on the D47 rig and judged UNPATCHED in three
  arms × n=3 ($11.45, 0 errors).  Payload drift vs the measured shim was not re-run —
  it was closed line by line at the implementation review, and the shim is gone.
  (a) **It is the slice IMAGES, not the sentence.**  Dirty rows 0.468 (off) → 0.429
  (shipped), marked defects 2.50 → 2.75; the sentence-only arm (`slice_payload →
  ([], True)`, the state a crashed drawing already produces) is FLAT at 0.471 / 2.38 —
  it carries +0.0035 of the −0.039.  The mean move is inside noise (paired bootstrap
  95 % CI [−0.114, +0.035]; 9 down / 5 up / 2 tie), i.e. not a uniform penalty.
  (b) **The cap stack was the wrong thing to fear.**  Hard-cap loss FALLS 0.0204 →
  0.0149 (the additive defect penalty is what rises, 0.189 → 0.204), is exactly 0.000
  on 12 of 16 rows in BOTH arms, no row loses > 0.1 to a cap `off` did not apply
  (max +0.032), and no cap brought by a new mark binds anywhere.  b36_v0_02 reproduces
  only weakly — 0.579 → 0.202, of which 0.000 is caps — not the 0.54 → 0.008 that
  wrote this watch item.
  (c) **The shipped arm is the more ACCURATE one, not merely the harsher one.**  All 16
  majority-marked disagreements were adjudicated against the rig renders and the exact
  slice PNGs: +6 true marks gained, 0 true marks lost, 6 false `off` marks deleted, 4
  false gained (net true +6 / net false −2).  Real wins: `h2h_office_chair`'s armrests
  standing clear of the seat (off missed it 0/3 → 3/3), b36_v0_02's stacked-cylinder
  scroll and painted-on f-holes.  Real deletions: the `sv2_violin` volute/C-bouts/
  cut-out f-holes and the `machinist_vise` lofted casting that `off` had called
  primitive.
  (d) **The debt is two sentences, both inside slices-only text** (so clean-round
  identity and `judge_prompt_hash` are untouched): the legend suffix
  `[outline: open section]` named a MESH property and read as a hole report — it alone
  moved `holes_or_inverted_faces` 0 → 2 cases, now `[outline only — not filled; NOT a
  hole]`; and the in-plane caveat was too generic to stop a 2.4 mm open join and
  section-cut islands being marked floating — it now POINTS AT the views that can
  decide ("judge floating_part and holes_or_inverted_faces from the shaded and
  geometry views") rather than forbidding the mark, because a bare prohibition would
  suppress the true marks the channel exists to win.  Rejected: a revert (it nets +6
  true marks), dropping the elicitation (inert on dirty rounds, and it produces the
  citations this audit was read from), a cap-interaction guard (no such cap fired).
  Clean-row invariant now MEASURED, not inherited: all 6 controls rebuild
  byte-identical under both dials.  Forcing the sentence onto clean rows costs −0.092,
  so it must never be promoted to unconditional — the on-error gate is load-bearing.
  Separately fundable, NOT D48's: `untextured_flat` was over-applied by BOTH arms on
  shaded models with a uniform sensible colour, which the rubric item's own text
  exempts — funded and FIXED 2026-09-01 (wording A/B on the same 22 cases, see
  docs/EVAL.md): the item now states the operable test ("differently-angled faces
  render the SAME brightness"; "no texture map is NOT the test") and keeps default
  grey/magenta a defect even when lit.  Both adjudicated-false marks of the
  shaded-uniform class dropped to 0/3, the true default-grey mark held 3/3, and the
  arm's drift is SMALLER than the near-A/A band (elicit−off) — noise, not wording.

* **D49 An articulated plan is checked for geometric self-consistency before any code is
  written — measured, ships OFF (2026-08-28/29).**  Context: compare_art_v3's low scorers
  failed on kinematics the plan already contradicted, and the joint sweep only reported it
  after a full build round.  Decision: `tracks/plan_checks.geometry_complaints` runs three
  box-arithmetic checks on the plan's own numbers (attachment gap ≤ 15 mm; hinge pivot
  within 25 mm of BOTH links; the child's box posed at q=lower/upper as a point lattice
  against every link that is neither its subtree, a housing holding ≥ 80 % of it at rest,
  interlocked with it at rest, nor — for a slide — its parent), and the planner re-asks
  with the numbers (`MAX_GEOMETRY_REASKS = 2`, event `plan.geometry`).  After the cap the
  plan ships; this never raises `PlanningError` (the plan-loop wave's C1 died that way).
  **Measured** (compare_art_v4, 14 prompts, check ON vs OFF as paired arms, flash planner,
  gemini-cli generator, judge pro n = 3): score Δ +0.064 [−0.188, +0.315]; final gate
  errors 0.00 vs 0.25; round-0 distinct joint_sweep targets 0.54 vs 0.25; the re-ask fired
  in 7/14 cells.  No measurable gain — with gemini-cli generation round-0 sweep errors are
  near zero either way — so `CV3D_PLAN_GEOMETRY` ships OFF (=1 enables;
  `plan_features.LIVE_SWITCHES`).
* **D50 Joint-sweep findings reach the fixer aggregated per link pair (2026-08-28).**
  Context: one compare_art_v3 run produced 59 penetration findings for a handful of pairs
  — one per sampled pose — and `build_refine_instructions` de-duplicates by (target, kind)
  keeping the FIRST pose's line, so the agent saw a pair's shallowest instance and the loop
  burnt its budget without converging.  Decision:
  `spatial/joints_sweep.aggregate_findings` merges a pair's poses into one line (how many
  poses, worst depth and at which q, rest count), ranks ERRORs by depth, keeps the deepest
  `MAX_PAIR_FINDINGS = 8` and folds the rest into one WARN `penetration_summary`;
  `default_joint_sweep` applies it, and adds `buried_links` — a link whose sampled surface
  lies ≥ 98 % inside another link at rest is an ERROR (invisible to every view, reported
  "missing" by the judge).  The raw sweep report is unchanged.  Companion measurement: a
  deterministic-repairs bundle (axis flip + buried check) A/B'd at Δ −0.039 ± 0.105 over
  12 paired prompts, the flip firing in 1 cell of 12 — the aggregation ships on rationale,
  not on a resolved score delta.
* **D51 The effect library is HARNESS-OWNED STARTER CODE, not an SDK import
  (2026-09-01).**  Context: `scene_multifile_graphics` shipped 52 three.js effect modules
  (21.7k lines) that this harness had no equivalent of — canopies, water, weather, aging,
  neon, night windows — and every scene run was re-deriving worse versions of them inside
  a 60-minute budget.  Law 1 says generated code is raw language, never an SDK/helper
  import.  **Decision — an amendment, not an exception**: law 1 forbids the AGENT from
  reaching outside its workspace for someone else's abstraction.  Modules the HARNESS
  writes into `src/`, verifies on its own renderer, and forbids the agent to rewrite are
  not that: they are the same thing `src/recipes.glsl` already was for `glsl_shader`
  (D-precedent: `contracts.common.HARNESS_OWNED_SRC`, `AgentJob.read_only`), one order of
  magnitude larger.  So `src/lib/*.js` ships into every scene workspace, is listed in
  `HARNESS_OWNED_SRC[SCENE_THREEJS]` (a new DIRECTORY-prefix entry form, predicate
  `is_harness_owned`), and a post-session write to it is reverted like any other
  harness-owned file.  Generated code stays raw three.js + GLSL: the library IS raw
  three.js, and it is in the workspace, in the agent's git history, readable and
  debuggable.

  **What shipped.**  52 modules / 28.8k lines under
  `codeverse/languages/scene_threejs/starter/src/lib/`, ported module-by-module against
  the reference's own test per module, adapted to OUR renderer contract (their exposure
  1.1 → our 1.0; their log-depth on → ours off, so their logdepth chunks are harmless
  no-ops; their renderer-contract assertions dropped).  Every module was rendered through
  the real host on the showcase harness and LOOKED at, most of them twice (a `before` port
  and an `after` improvement pass): 53 modules carry a before/after pair on disk and 77
  night renders were taken.  Aesthetic changes were measured, not asserted — linear albedo
  band 0.02–0.8, hue variance inside every effect, emissives re-scaled to a bloom-friendly
  1.5–4, fresnel on water/glass, fog in the sky's hue family.  678 node-marked tests
  (`tests/scene_runtime/lib/`, one file per module) pin the physics claims, the shared-name
  contracts and the option-is-a-uniform law; the GPU compile in them is real
  (headless ANGLE/D3D12, e.g. godrays 21 programs / 6 custom materials).

  **Companion side track**: a post chain for scene renders
  (`runtime_js/lib/browser/post.js`, ON by default, `--no-post` / `CV3D_POST=0`):
  RenderPass → finite clamp → GTAO → SELECTIVE emissive bloom → grade → OutputPass, 18
  tests.  The bloom is NOT the reference's luminance bright-pass: measured on this
  renderer every daylight scene tops out at 1.883 linear (the sky dome) while the
  brightest authored emissive in the whole library is 1.231, so a global bright-pass
  cannot separate a neon sign from the sky — at the reference's 0.85 threshold a daylit
  grass frame lost a quarter of its saturation (0.3132 → 0.0699).  The mask is a second
  cheap render of emission only; a scene with nothing emissive pays nothing.

  **Consolidation (same day).**  `_probe.shader_check` could never work — it staged only
  `src/fixture.js` while `check_shaders.mjs` boots the workspace's `src/scene.js` — so 32
  module tests had each hand-rolled the same 17-line workaround.  Replaced by
  `_probe.compile_scene`; **843 lines deleted**, and two further inline copies
  (`test_lights`, `test_foliage_shade`) and `test_shader.compile_fixture` fold into it
  too.  Also fixed: `scene_host.mjs` failed a camera-less boot with `info.error` EMPTY
  (message named no reason at all); plan-mode `write_skeleton` shipped `PATTERN_FILES`
  only, so a PLANNED run — the production path — would have got the catalog in its prompt
  and no `src/lib/` to import from; `signage.js` carried a `unpkg.com` CDN font pin that
  violates the no-network law (dead since `import.meta.resolve` landed); and both the
  workspace lint and the static shader audit now skip `src/lib/`, which was 3 hard lint
  ERRORs (signage's node-only font fallback) + 12 "large file" WARNs + a uTime
  false-positive pair that outranked the agent's own real shader error in the build report.
  `patchStandard` gained three opt-in injection hooks — `roughnessBody` / `metalnessBody`
  (after `<roughnessmap_fragment>` / `<metalnessmap_fragment>`) and `outputBody` (after
  `<opaque_fragment>`, where the colour is still linear) — the gap three module agents
  reported independently, since `<color_fragment>` reaches only the albedo and a 30 %
  rust crust could until now only be sold by dematting 100 % of the material.

  **Evidence label — honest.**  Everything above is RENDERED AND EYEBALLED plus
  deterministic metrics and 2 809 offline tests green.  It is **NOT yet A/B-judged**: no
  paired battery has scored scenes with the library against scenes without it, so the
  claim "this raises scene scores" is unproven.  The shader.js hook change was isolated by
  rendering all six shader.js-dependent modules (grass, water, canopy, godrays, neon,
  clouds) against a control build with the hooks cut out: mean luminance identical to five
  decimals on all 12 frames.  Funding the A/B is the next call.

* **D52 The articulated wave measured its own instrument first, and then only the loss
  events (2026-09-02/03).**  Context: four articulated levers (the D49 geometry re-ask, a
  pro planner, a deterministic repair bundle, `CV3D_FEWER_TURNS`) all measured inside
  ±0.13 on 12-14 paired prompts, which is not evidence that they do nothing.  Decision:
  calibrate, then change the channel.
  * **The instrument.**  A/A on `articulated_v2` (`bench/ab_plan.py --aa --pin-plan`,
    identical arms, generator `gemini-cli:gemini-3.7-flash`, fixed judge
    `gemini:gemini-3.1-pro-preview`, `n_samples=3`, rubric `articulated_v1`, 3 rounds,
    60 min/cell): 12 paired prompts (2 pairs dropped at the pinned plan to provider read
    timeouts, redone; 0 budget_exhausted), mean Δ **−0.046**, median −0.048, paired sd
    **0.225**, SE 0.065, **2 SE ±0.130**, sign 3 up / 8 down, p 0.227, separated from
    noise **NO**; the rig's own n_for_power is ~506 pairs for ±0.02.  Every phase-4/5
    result sat inside that band, and the rig printed "revert" from identical arms.
  * **The channel that works.**  `bench/plan_stage_bench.py` runs the plan stage
    ALONE (≈$0.03 and ≈85 s per call), so a 280-call arm is affordable where a 14-cell
    battery is not.  `CV3D_PLAN_RESTART` (a plan that names links it never lists is
    re-sampled from the original request instead of edited in context) measured on 560
    calls, both arms in the same window: planner losses **4.7 % (13/277) → 0.7 % (2/276)**,
    Fisher exact two-sided **p = 0.0067**; the restart fires on 14 % of calls and recovers
    39 of 40; cost per call unchanged ($0.0345 vs $0.0337).  Ships ON with a kill switch.
    The rule this sets: measure the loss event (a run that produced nothing, a retried
    tool call, a pose the mechanism cannot reach), not the judge mean, unless the battery
    is large enough for the judge mean.  The 560 rows live in
    `bench/data/plan_stage/*.jsonl` with `bench/plan_stage_report.py`: a p-value whose
    data is not in the tree is not reproducible (review, 2026-09-03).
  * **The trigger, narrowed and re-measured (2026-09-03).**  Review's point was that the
    trigger fired on any dangling link reference while the measured class is narrower, and
    that a restart spent one of the two validation re-ask slots.  Both changed; three arms
    in ONE window (700 calls, `bench/data/plan_stage/trigger_{off,wide,narrow}.jsonl`) say:
    overall loss 2.9 % (off, 4/140) / 2.2 % (old trigger, 6/275) / 1.8 % (narrowed, 5/276,
    the fifth being a harness budget ceiling that the first pass had filed as weather),
    **no pair separating** (Fisher 0.45–0.75) — this window's control loses 2.9 %, not the
    4.7 % above, so the headline is a property of that window as much as of the switch.
    The separation is inside the class the mechanism targets: dangling-link deaths **3/140
    off vs 0/276 narrowed, p = 0.038** — exploratory, in that the three overall-rate tests
    were run first and came back null — with the old trigger still at 4/275 — and all four
    of those carry `restarts=1` and died at the validation cap, which is exactly the slot
    the restart used to consume.  The narrowed trigger fires on 28 of 280 calls against 38
    and recovers 27 of 28 against 33 of 38.  Ships narrowed: same effect on the class it
    exists for, a quarter fewer plans thrown away.  The residue is a class nothing here
    addresses — a joint whose parent and child are the same link (1/2/4 across the arms,
    flat, never restarted, repeated through all three re-asks).  That is the next
    measurement, not a regression of this one.
  * **What the judge channel could not have found.**  `is_error = not obs.ok` (§30 of
    docs/COST.md) made 62 % of 1 445 `joint_sweep` calls and 23 % of 2 144 `build` calls
    arrive as broken calls the model retried, and — because gemini-cli stringifies an
    errored result — turned a 275 kB articulation sheet into ~261 k prompt tokens of
    base64 instead of ~516 as an image; ~10 % of requests carried ~225 k uncacheable
    tokens, 67 % of the uncached bill.  That is a mechanism fault with no score signature
    at n=14, found by reading the recorded tool stats.  **Measured after the fix**
    (`aa_articulated` before, `wave2_lean` after, same battery and config): calls reported
    as errors 26.6 % (482/1 814) → **1.3 %** (25/1 863; the remainder are genuine
    `Observation.error` cases), cache hit 69 % → **90 %**, uncached prompt tokens per
    main-role request 38 521 → **13 480**, generator dollars per round median 1.572 → **0.950**
    (−40 %).  `bench/session_stats.py` computes both columns from the recorded sessions —
    the first, hand-computed after column (1.4 %, 13 736, $0.967 over "108 sessions") is
    NOT reproduced by it and is withdrawn: no tested mechanism explains its shape (1.87x on
    calls and requests against 1.06x on sessions), while the before column reproduces to
    the digit.  docs/COST.md §30 carries that and the selector.
  * **`CV3D_LEAN_PROMPT` stays OFF, now with a number.**  Paired battery on
    `articulated_v2` (12 pairs, plan pinned, same fixed judge): mean Δ **+0.030**, paired
    sd 0.131, **2 SE ±0.076**, 8 up / 3 down (p 0.227), one regression — inconclusive by
    the rig's own rule — and no cost saving ($3.51 vs $3.66 per scored cell), which is the
    second time a prompt/turn-shape change has moved tokens without moving dollars
    (docs/COST.md §29).  The switch itself was DELETED in review (2026-09-03, −569 lines):
    an inconclusive lever with no cost saving is not worth a second prompt path through
    three templates, and the measurement above is the record of what it was worth.  To
    re-run it, restore the branch commit named in docs/PAPER_WRITING.md §9.3.
  * **Reading an agent's repo is a sandbox boundary, and `git archive` is not inside it.**
    Every flywheel read of a workspace already ran under `GIT_SAFE_FLAGS` (three `-c`
    overrides — hooks, fsmonitor, the global attributes file — plus no
    system/global config from `git_safe_env`; `.git/config` itself is still read in full,
    which is the whole reason the tests plant there) and `--no-ext-diff --no-textconv` for
    diffs, because a
    `.gitattributes` the agent writes can name a `diff.<name>.textconv` command that git
    RUNS on our side.  `read_tree_at` still used `git archive`, which renders every blob
    through `convert_to_working_tree` — so a planted `filter.<name>.smudge` executes, and
    unlike textconv there is no flag that turns it off.  It reads through `ls-tree -r -z`
    + `cat-file --batch` now: same bytes, no filter path.  Pinned by a test that plants a
    smudge filter and fails on the old implementation (`tests/flywheel_cli`).
  * **The refine transitions are the corpus this harness is for.**  `3dcv flywheel refine`
    emits one row per round the loop asked to change: the gate findings and judge
    complaint that condemned round i, the instructions the harness wrote, both code
    snapshots, and whether the score moved.  A one-shot corpus cannot contain that pair;
    the loop produces it as a by-product of running.  Format conversion (LLaMA-Factory
    messages) lives in `toolkits/llamafactory/`, not in the harness — the harness writes
    the measurement, a toolkit writes whatever a trainer wants (owner's boundary,
    2026-09-03).
  * **`<mimic>` is used and it changes the sweep, which is what it was for.**  In the same
    battery the planner and agent declared couplings in 8 of 14 prompts (98 of 176 built
    URDFs), and on those the sampler drives a median of 3 joints per pose against the 6 it
    would have driven before — 1 on the umbrella and the step ladder, 2 on the folding
    workbench, the mechanisms the support was built for.  The pre-mimic batteries contain
    zero couplings, so nothing else changed shape.  `bench/coupling_stats.py --per-prompt`
    recomputes all of it from the recorded URDFs (both counts are properties of the file).
    The score effect on the three coupled prompts is inside the noise band at n=2 per side,
    as expected; the pose count is the readout that resolves.  What does NOT resolve, and
    is recorded so nobody claims it: the `joint_sweep` gate's failure rate is not
    detectably different with couplings (11/37, 30 %) and without (12/39, 31 %) — a hand
    count, no script prints the per-prompt subset — and the spread across pre-mimic
    batteries (19–31 %) is wider than that difference.  Fewer poses, all reachable — but
    the gate still finds overlaps in them, and the two arms are not the same artefacts
    measured twice (a pre-mimic plan declares no coupling at all).  Caveat (2026-09-04):
    every judge- and gate-side readout above was produced before
    `spatial/joints_export.robot_scene` resolved `<mimic>` followers — the sheets the judge
    saw posed them at rest while the sweep posed them through `fk`.  The pose counts come
    from the URDFs and stand; the scores and gate rates need a re-run on the corrected
    export.

* **D53 Interpenetration is measured twice, and the two probes are asking different
  questions (found 2026-09-04, corrected 2026-09-06, NOT changed).**  `spatial/connectivity`
  draws 600 points per surface, requires a minimum share of them inside the other part, and
  calls **2 mm** a WARN and **10 mm** an ERROR at the REST pose.  `joint_sweep` poses the
  mechanism through fk and probes densely: it records an overlap from **2 mm**
  (`sweep_collisions(tol_m=)`, the default every caller takes) and treats a REST overlap as
  an ERROR only above **5 mm** (`sweep_findings(rest_max_m=)`, which
  `urdf.REST_PENETRATION_MAX_M` matches).  So at rest the two share a WARN line and differ
  2x on ERROR — not 10x, and `bench/penetration_thresholds.py` now imports both numbers
  instead of restating them.

  Measured over 374 recorded articulated rounds: the connectivity ERROR has fired **0
  times**, its WARN 50, and the depths it records are median 2.0 mm, max 9.9 mm — its ERROR
  threshold is unreachable in practice.  The sweep raises **295 ERROR findings across 191
  distinct link pairs**, median 5.0 mm, max 34.3.

  **A correction to how that gap was first read.**  This entry used to say the sweep
  measures "2-3x deeper on the same link pairs (8.8 vs 3.0 mm, 5.9 vs 3.8, 8.0 vs 3.0)".
  That comparison was not about one pose: the sweep number was its worst over ALL sampled
  poses and the connectivity number was the rest pose.  Restricted to the pairs whose worst
  sweep pose IS the rest pose — `data["pose"]` empty, which is the filter that makes the
  comparison mean what it says — there are 19 such pairs in the corpus, 4 of them also
  recorded by connectivity in the same round, and on those four **the two probes agree
  exactly**: 4.4/4.4, 4.4/4.4, 2.2/2.2, 2.5/2.5 mm.  The probes do not disagree about depth.
  What differs is the question: 288 of the sweep's pairs are overlaps that exist only in a
  moved pose, which a rest-pose check cannot see by construction.

  **And the threshold experiment answered itself, offline: do not change it.**  Using the
  dense probe as the reference — for every pair connectivity records at or over a candidate
  threshold, did the SAME round's sweep flag that pair? — an ERROR at 2 mm would fire on 50
  pairs of which only **7 are corroborated**, while the 43 uncorroborated ones are static
  contacts the design intends (`shoulder_lock_knob|swivel_post`, `base_underframe|center_top`)
  that a 600-point surface draw reads as penetration; the dense probe would still be alone on
  **288** pairs.  At 5 mm: 2 pairs, 0 corroborated.  A shared threshold would create
  uncorroborated failures and still miss what the sweep finds.  What was wrong is that the
  two read as one check with two dials; the fix belongs in what each is called and documented
  to do, not in the numbers.

* **D54 A gate never reports the machine as a defect, and a render is retried once when
  the browser dies (2026-09-05).**  The first recorded `scenes_v1` battery produced six
  cells of which three built, passed every gate that does not need pixels, and kept ZERO
  renders: `driver: Attempted to use detached Frame '<id>'` — Chrome reaping the render
  tab on a box at load 93 with swap full and all eight GPUs at ~100 %.  The judge was
  skipped for want of images, so $6.75 of already-paid generation produced no verdict, and
  `bench/scene_stats.py` attributed the whole thing to `render_console`.  Three separate
  rules came out of it, and they are the general form, not three patches:
  1. **A driver that lost its browser is retried once, on a browser of its own.**  The
     scene funnel (`spatial/render_scene.run_scene_script`) had no retry at all; the object
     path had one since 2026-08-28 but its marker tuple knew only the "Target closed"
     spelling.  One vocabulary, `spatial/node.BROWSER_LOST_MARKERS`, now serves both, and
     the retry runs with `CV3D_BROWSER_REUSE=off` because the shared browser advertised in
     the cache is the suspect.  Exactly one retry: a box out of memory stays out of memory.
  2. **Two runtime trees never share a browser.**  The daemon endpoint, its spawn lock and
     its failure file carry a digest of the `runtime_js` that spawned them, so a worktree
     and the main checkout cannot advertise over each other inside one `CV3D_CACHE_DIR`.
     RUNBOOK had asked operators to remember this since the coupled battery lost an arm to
     it; nothing enforced it.
  3. **A finding that names a dead browser is reported apart from a defect.**
     `scene_stats.py` puts it in a `lost to the box` column and does not count the gate as
     failed.  A battery whose report shows that column non-zero is not yet a statement
     about the generator.
  Consequence: a red browser test is checked against `uptime` and `free -g` before it is
  believed, and `test_studio_render_is_reproducible_and_stamps_the_rig_version` is
  documented as flaky-by-construction under CPU contention (SwiftShader is not
  bit-reproducible when the box is busy) rather than weakened.

* **D55 `scene_placement` measures matter, and matter writes depth (2026-09-05).**  Two of
  the first six recorded scene cells failed the gate on nothing but their own atmosphere:
  "BlackPine_5 is sunken 3.46 m into AtmosphereHaze" and "WindowSnowView/Mesh_49 and
  Environment/MoonlightShaft overlap (100 % of the smaller box)".  Both scenes were
  correct; both offenders are `MeshBasicMaterial` at opacity 0.035-0.04 with
  `depthWrite: false`, and all nine recorded scenes use that idiom 34-42 times each.  A
  pass that writes no depth occludes nothing, so it cannot support an object, nothing can
  sink into it, and passing through it is what it is for.  `host_placement.nonSolid` keeps
  such meshes out of the column index entirely; an asset made only of them is listed with
  the exempt reason `volumetric` rather than dropped.  **Opacity is deliberately not part
  of the rule** — glass sits at 0.3-0.6 and keeps writing depth, and a greenhouse pane
  really is a surface; a solid wall the model mistakenly marked `depthWrite: false` stops
  being a support, which is the cheaper error and matches what the frame shows.  Measured
  on the recorded workspaces: cozy_cabin FAIL → PASS, japanese_garden's 3.46 m becomes a
  0.044 m embed and the gate now fails on a real defect it had been reporting alongside
  the fog (`SubmergedRock_1 is sunken 0.48 m into ArchedBridge`).

  The same rule belongs in `nearGeometry`, and there it does more than quiet a report.
  `repairCameraSpec` is driven by `camera_in_geometry`, so a false "inside" RETREATS the
  authored lens: of the battery's seven recorded camera repairs, three were triggered by
  fog, and cozy_cabin's two were each moved back 4 m and up 2 m for standing in a
  `MoonlightShaft` — one of them ending NEARER geometry than it started (2.358 → 0.916 m).
  Re-rendering that workspace under the battery's own `--camera-repair` flag with the rule
  in place: `ArmchairHearthEye` goes from mean luminance 0.126 / 48 % near black to
  **0.263 / 0.5 %**, `WindowFrostSnow` from 0.149 / 56 % to **0.298 / 17 %**, and both
  cross from `dark_frame` ERROR to passing, while the two cameras that were never moved
  are unchanged (one of them still genuinely dark).  So **two of that cell's three
  dark-frame ERRORs were manufactured by the harness**: fog → a false "camera in
  geometry" → a 4 m retreat → a dark frame → a second gate's ERROR → a judge complaint.
  Since "the frame is too dark" is this track's most recorded defect (153 findings over 32
  runs, per the scene prompt itself), some unknown share of that history is the
  instrument rather than the model.  Across all 113 recorded camera checks in the battery
  the RAY term (`nearest < 0.3 m`) never fired once; every `camera_in_geometry` finding
  came from the bbox term, and every one of those was a volumetric or a scatter field.

* **D56 A loaded GLB root is named after its asset (2026-09-05).**  rooftop_garden's plan
  asked for one `blender_glb` asset; the Blender sub-run built it, the assembler wrote the
  loader, the browser loaded it and the census confirmed it was in the scene
  (`meshes_in_scene: 1, in_scene: true`) — and `scene_placement` still reported
  "zone PergolaLounge is missing planned contents: LoungeSofa", because the object was
  called `Scene`.  A glTF root carries whatever the exporter wrote and Blender writes
  "Scene".  The three assembled entry points that load GLBs (`render_scene_js`, the
  assembler's probe module, and the plan-written `scene.js`) had drifted into three copies
  of the same loop with three different error messages; they are now one emitter,
  `_glb_preload_js`, which stamps `to_pascal(key)` on the loaded root.  Consequence for the
  Blender-scene question: **the cross-language seam is not what fails.**  The Blender asset
  path works end to end and only lost a name; an argument for a Blender assembly layer has
  to be made on other grounds.

* **D57 A frame rate is a measurement of the renderer that produced it (2026-09-05).**
  Inside one `scenes_v1` battery `fps` was measured on two different backends — 11.5 fps on
  an RTX 6000 Ada for one cell, 2.0 / 5.1 / 7.1 on SwiftShader for the next three, because
  `gpu_launch.cjs` caches a negative GPU verdict for 20 minutes and the box's GPUs were at
  ~100 % from other work.  `render_console` raised "low frame rate … merge static geometry"
  for all four, and the judge was handed "PROBE: measured N fps" as a fact; two of the four
  **critical** judge issues across the scored cells were frame-rate complaints.  Decision:
  `RenderSet.hardware_fps` is the only form a gate or a judge may read, and
  `RenderSet.software_rendered` reads the renderer string with the same words
  `gpu_launch.cjs` uses to decide whether a GPU attempt is trusted at all.  The judge prompt
  keeps a software number but LABELS it, rather than dropping it silently — a genuinely
  heavy scene should still be visible to a reader — and an unrecognised renderer string is
  never claimed as software.  Consequence: an fps comparison across cells is only valid
  within one backend, which is a property of any battery run on a shared machine, not of
  this one.

* **D58 The scene texture pack is wired into the loop, behind a switch that is off
  (2026-09-05).**  Of 24 judge issues over the five scored cells of the first `scenes_v1`
  battery, four say the GROUND is a flat untextured colour, in near-identical words —
  "single flat brown color", "single flat color with no cobblestone texture", "flat,
  untextured blueish plane with no material blending", "a hard, unblended circular seam".
  It is the most consistent defect in the battery.  It is also a harness gap:
  `texturing.plan.scene_texture_pack` writes a tileable pack and `texture_pack_prompt`
  renders the list plus the loading idiom — its docstring says "Prompt snippet for
  zone/env generation" — and NOTHING in `tracks/scene.py` called either.  The pack was
  reachable only through `3dcv texture scene-pack`, whose output `cli/texture_cmd.py`
  prints for a human to paste, and `Spec.options.texture` does nothing on this track.
  Decision: a `textures` stage runs before env and zones (they can only name files that
  exist when their prompts are built) and `_ctx` — the one place both prompts get their
  context — carries the manifest.  **`CV3D_SCENE_TEXTURES` is off by default**: it costs
  an image-model call per run, measured at $0.15 for two 512 px textures (seam score
  0.001) and an estimated $1-2 per run at ten 1024 px ones, and what that buys is a
  measurement nobody has made.  Consequence for the Blender question: the material class
  cannot be counted as evidence for changing renderer until this arm has run.  (The
  pack planner, given medieval_market's plan and told nothing about its judgment, planned
  `medieval_cobblestone` first — against a judge complaint reading "no cobblestone
  texture or material blending".)

* **D53 Interpenetration is measured twice, and the two probes are asking different
  questions (found 2026-09-04, corrected 2026-09-06, NOT changed).**  `spatial/connectivity`
  draws 600 points per surface, requires a minimum share of them inside the other part, and
  calls **2 mm** a WARN and **10 mm** an ERROR at the REST pose.  `joint_sweep` poses the
  mechanism through fk and probes densely: it records an overlap from **2 mm**
  (`sweep_collisions(tol_m=)`, the default every caller takes) and treats a REST overlap as
  an ERROR only above **5 mm** (`sweep_findings(rest_max_m=)`, which
  `urdf.REST_PENETRATION_MAX_M` matches).  So at rest the two share a WARN line and differ
  2x on ERROR — not 10x, and `bench/penetration_thresholds.py` now imports both numbers
  instead of restating them.

  Measured over 374 recorded articulated rounds: the connectivity ERROR has fired **0
  times**, its WARN 50, and the depths it records are median 2.0 mm, max 9.9 mm — its ERROR
  threshold is unreachable in practice.  The sweep raises **295 ERROR findings across 191
  distinct link pairs**, median 5.0 mm, max 34.3.

  **A correction to how that gap was first read.**  This entry used to say the sweep
  measures "2-3x deeper on the same link pairs (8.8 vs 3.0 mm, 5.9 vs 3.8, 8.0 vs 3.0)".
  That comparison was not about one pose: the sweep number was its worst over ALL sampled
  poses and the connectivity number was the rest pose.  Restricted to the pairs whose worst
  sweep pose IS the rest pose — `data["pose"]` empty, which is the filter that makes the
  comparison mean what it says — there are 19 such pairs in the corpus, 4 of them also
  recorded by connectivity in the same round, and on those four **the two probes agree
  exactly**: 4.4/4.4, 4.4/4.4, 2.2/2.2, 2.5/2.5 mm.  The probes do not disagree about depth.
  What differs is the question: 288 of the sweep's pairs are overlaps that exist only in a
  moved pose, which a rest-pose check cannot see by construction.

  **And the threshold experiment answered itself, offline: do not change it.**  Using the
  dense probe as the reference — for every pair connectivity records at or over a candidate
  threshold, did the SAME round's sweep flag that pair? — an ERROR at 2 mm would fire on 50
  pairs of which only **7 are corroborated**, while the 43 uncorroborated ones are static
  contacts the design intends (`shoulder_lock_knob|swivel_post`, `base_underframe|center_top`)
  that a 600-point surface draw reads as penetration; the dense probe would still be alone on
  **288** pairs.  At 5 mm: 2 pairs, 0 corroborated.  A shared threshold would create
  uncorroborated failures and still miss what the sweep finds.  What was wrong is that the
  two read as one check with two dials; the fix belongs in what each is called and documented
  to do, not in the numbers.

* **D54 A gate never reports the machine as a defect, and a render is retried once when
  the browser dies (2026-09-05).**  The first recorded `scenes_v1` battery produced six
  cells of which three built, passed every gate that does not need pixels, and kept ZERO
  renders: `driver: Attempted to use detached Frame '<id>'` — Chrome reaping the render
  tab on a box at load 93 with swap full and all eight GPUs at ~100 %.  The judge was
  skipped for want of images, so $6.75 of already-paid generation produced no verdict, and
  `bench/scene_stats.py` attributed the whole thing to `render_console`.  Three separate
  rules came out of it, and they are the general form, not three patches:
  1. **A driver that lost its browser is retried once, on a browser of its own.**  The
     scene funnel (`spatial/render_scene.run_scene_script`) had no retry at all; the object
     path had one since 2026-08-28 but its marker tuple knew only the "Target closed"
     spelling.  One vocabulary, `spatial/node.BROWSER_LOST_MARKERS`, now serves both, and
     the retry runs with `CV3D_BROWSER_REUSE=off` because the shared browser advertised in
     the cache is the suspect.  Exactly one retry: a box out of memory stays out of memory.
  2. **Two runtime trees never share a browser.**  The daemon endpoint, its spawn lock and
     its failure file carry a digest of the `runtime_js` that spawned them, so a worktree
     and the main checkout cannot advertise over each other inside one `CV3D_CACHE_DIR`.
     RUNBOOK had asked operators to remember this since the coupled battery lost an arm to
     it; nothing enforced it.
  3. **A finding that names a dead browser is reported apart from a defect.**
     `scene_stats.py` puts it in a `lost to the box` column and does not count the gate as
     failed.  A battery whose report shows that column non-zero is not yet a statement
     about the generator.
  Consequence: a red browser test is checked against `uptime` and `free -g` before it is
  believed, and `test_studio_render_is_reproducible_and_stamps_the_rig_version` is
  documented as flaky-by-construction under CPU contention (SwiftShader is not
  bit-reproducible when the box is busy) rather than weakened.

* **D55 `scene_placement` measures matter, and matter writes depth (2026-09-05).**  Two of
  the first six recorded scene cells failed the gate on nothing but their own atmosphere:
  "BlackPine_5 is sunken 3.46 m into AtmosphereHaze" and "WindowSnowView/Mesh_49 and
  Environment/MoonlightShaft overlap (100 % of the smaller box)".  Both scenes were
  correct; both offenders are `MeshBasicMaterial` at opacity 0.035-0.04 with
  `depthWrite: false`, and all nine recorded scenes use that idiom 34-42 times each.  A
  pass that writes no depth occludes nothing, so it cannot support an object, nothing can
  sink into it, and passing through it is what it is for.  `host_placement.nonSolid` keeps
  such meshes out of the column index entirely; an asset made only of them is listed with
  the exempt reason `volumetric` rather than dropped.  **Opacity is deliberately not part
  of the rule** — glass sits at 0.3-0.6 and keeps writing depth, and a greenhouse pane
  really is a surface; a solid wall the model mistakenly marked `depthWrite: false` stops
  being a support, which is the cheaper error and matches what the frame shows.  Measured
  on the recorded workspaces: cozy_cabin FAIL → PASS, japanese_garden's 3.46 m becomes a
  0.044 m embed and the gate now fails on a real defect it had been reporting alongside
  the fog (`SubmergedRock_1 is sunken 0.48 m into ArchedBridge`).

  The same rule belongs in `nearGeometry`, and there it does more than quiet a report.
  `repairCameraSpec` is driven by `camera_in_geometry`, so a false "inside" RETREATS the
  authored lens: of the battery's seven recorded camera repairs, three were triggered by
  fog, and cozy_cabin's two were each moved back 4 m and up 2 m for standing in a
  `MoonlightShaft` — one of them ending NEARER geometry than it started (2.358 → 0.916 m).
  Re-rendering that workspace under the battery's own `--camera-repair` flag with the rule
  in place: `ArmchairHearthEye` goes from mean luminance 0.126 / 48 % near black to
  **0.263 / 0.5 %**, `WindowFrostSnow` from 0.149 / 56 % to **0.298 / 17 %**, and both
  cross from `dark_frame` ERROR to passing, while the two cameras that were never moved
  are unchanged (one of them still genuinely dark).  So **two of that cell's three
  dark-frame ERRORs were manufactured by the harness**: fog → a false "camera in
  geometry" → a 4 m retreat → a dark frame → a second gate's ERROR → a judge complaint.
  Since "the frame is too dark" is this track's most recorded defect (153 findings over 32
  runs, per the scene prompt itself), some unknown share of that history is the
  instrument rather than the model.  Across all 113 recorded camera checks in the battery
  the RAY term (`nearest < 0.3 m`) never fired once; every `camera_in_geometry` finding
  came from the bbox term, and every one of those was a volumetric or a scatter field.

* **D56 A loaded GLB root is named after its asset (2026-09-05).**  rooftop_garden's plan
  asked for one `blender_glb` asset; the Blender sub-run built it, the assembler wrote the
  loader, the browser loaded it and the census confirmed it was in the scene
  (`meshes_in_scene: 1, in_scene: true`) — and `scene_placement` still reported
  "zone PergolaLounge is missing planned contents: LoungeSofa", because the object was
  called `Scene`.  A glTF root carries whatever the exporter wrote and Blender writes
  "Scene".  The three assembled entry points that load GLBs (`render_scene_js`, the
  assembler's probe module, and the plan-written `scene.js`) had drifted into three copies
  of the same loop with three different error messages; they are now one emitter,
  `_glb_preload_js`, which stamps `to_pascal(key)` on the loaded root.  Consequence for the
  Blender-scene question: **the cross-language seam is not what fails.**  The Blender asset
  path works end to end and only lost a name; an argument for a Blender assembly layer has
  to be made on other grounds.

* **D57 A frame rate is a measurement of the renderer that produced it (2026-09-05).**
  Inside one `scenes_v1` battery `fps` was measured on two different backends — 11.5 fps on
  an RTX 6000 Ada for one cell, 2.0 / 5.1 / 7.1 on SwiftShader for the next three, because
  `gpu_launch.cjs` caches a negative GPU verdict for 20 minutes and the box's GPUs were at
  ~100 % from other work.  `render_console` raised "low frame rate … merge static geometry"
  for all four, and the judge was handed "PROBE: measured N fps" as a fact; two of the four
  **critical** judge issues across the scored cells were frame-rate complaints.  Decision:
  `RenderSet.hardware_fps` is the only form a gate or a judge may read, and
  `RenderSet.software_rendered` reads the renderer string with the same words
  `gpu_launch.cjs` uses to decide whether a GPU attempt is trusted at all.  The judge prompt
  keeps a software number but LABELS it, rather than dropping it silently — a genuinely
  heavy scene should still be visible to a reader — and an unrecognised renderer string is
  never claimed as software.  Consequence: an fps comparison across cells is only valid
  within one backend, which is a property of any battery run on a shared machine, not of
  this one.

* **D58 The scene texture pack is wired into the loop, behind a switch that is off
  (2026-09-05).**  Of 24 judge issues over the five scored cells of the first `scenes_v1`
  battery, four say the GROUND is a flat untextured colour, in near-identical words —
  "single flat brown color", "single flat color with no cobblestone texture", "flat,
  untextured blueish plane with no material blending", "a hard, unblended circular seam".
  It is the most consistent defect in the battery.  It is also a harness gap:
  `texturing.plan.scene_texture_pack` writes a tileable pack and `texture_pack_prompt`
  renders the list plus the loading idiom — its docstring says "Prompt snippet for
  zone/env generation" — and NOTHING in `tracks/scene.py` called either.  The pack was
  reachable only through `3dcv texture scene-pack`, whose output `cli/texture_cmd.py`
  prints for a human to paste, and `Spec.options.texture` does nothing on this track.
  Decision: a `textures` stage runs before env and zones (they can only name files that
  exist when their prompts are built) and `_ctx` — the one place both prompts get their
  context — carries the manifest.  **`CV3D_SCENE_TEXTURES` is off by default**: it costs
  an image-model call per run, measured at $0.15 for two 512 px textures (seam score
  0.001) and an estimated $1-2 per run at ten 1024 px ones, and what that buys is a
  measurement nobody has made.  Consequence for the Blender question: the material class
  cannot be counted as evidence for changing renderer until this arm has run.  (The
  pack planner, given medieval_market's plan and told nothing about its judgment, planned
  `medieval_cobblestone` first — against a judge complaint reading "no cobblestone
  texture or material blending".)

* **D59 A check that reads the scene graph by name walks the whole zone subtree
  (2026-09-06).**  All five `scene_placement` ERRORs left in the fixed arm were
  "zone X is missing planned contents", and none of them was absent content.  A zone
  typically wraps what it builds in one group — floating_islands puts Windmill,
  FloatingRock and SkyPine inside `IslandAssembly`; cozy_cabin's `PineSway_0` and
  snowy_hut's `LanternPost1` are the ZONE's names for objects whose own roots, one level
  further down, are `SnowyPineTree` and `HutPorchLantern`.  The placement table lists
  direct children of a zone, so the contract check saw one row per wrapper and called the
  plan unmet.  Each row now carries its named descendants (`inner`, capped at
  `MAX_INNER_NAMES = 24`) and the check reads them; re-probing the three recorded
  workspaces takes the contract from 5 ERRORs to 0.  This is the third variant of one
  defect — a glTF root called `Scene` (D56), a wrapper group, a behaviour-named parent —
  so the rule is now: **a by-name check on the scene graph names a SUBTREE, never a
  child.**

* **D60 A harness failure is never handed to the agent as a repair (2026-09-06).**
  `probes.probe_report` labels a driver that produced no output with
  `harness_failure: True` and the hint "this is a harness/driver failure, not your code",
  and `build_with_repair` did not read the flag.  Measured on `bench/out/scene_textures`
  (japanese_garden): three repairs against that message rewrote 5, then **14**, then 3
  files — the 14 included `env.js` and every zone — and the fourth build passed on its
  own.  The 14-file rewrite deleted the texture use the arm existed to measure; that cell
  scored 0.496 where the same prompt scored 0.636 without the detour.  `BuildResult`
  carries the flag now and the loop re-runs the BUILD instead, bounded by
  `MAX_HARNESS_REBUILDS = 2` and costing no model call, since the node driver has already
  retried once itself.  Rare (1 cell of 12 in each of two arms) and expensive when it
  fires.

* **D61 A driver's summary is flushed before it exits (2026-09-06).**  `lib/cli.finish()`
  wrote the JSON summary and then called `process.exit()`.  Node's stdout to a PIPE is
  asynchronous and `process.exit` does not flush it, so any summary larger than the pipe
  buffer was cut mid-JSON and the caller saw no parsable last line.  Measured on the
  starter scene, `probe_scene.mjs --compile`: to a file the summary is **10 462 bytes and
  parses**, through a pipe it was **exactly 8192 and did not**.

  **This is the real mechanism behind every "driver output lost" and "[?] scene did not
  boot" in the scene batteries, and D54 attributed them to the wrong cause.**  They do not
  depend on how busy the machine is — they depend on how big the census is, which is why
  they read as weather across two batteries.  desert_canyon spent three repair attempts on
  it in `scene_baseline`; `scene_textures/japanese_garden` spent three more and lost the
  texture use that arm existed to measure.  The retries D54 added are still right (they
  cost nothing and cover a genuinely transient loss) but they could never fix this one:
  a deterministic truncation reproduces on every attempt.

  `finish()` now sets `process.exitCode` and exits from the write's completion callback;
  the watchdog goes through it too.  Pinned by a driver whose summary is 64 KiB.

* **D62 The `inside_mesh_bbox` term was NOT narrowed, and here is the counter-example
  (2026-09-06).**  Over the 113 recorded camera checks of `scene_baseline` the ray term
  (`nearest < 0.3 m`) never fired once and all ten firings came from the bbox term with the
  lens 1.3-10.0 m clear of anything — sparse scattered fields (`Drift`, `FoliageMass`,
  `Midges`) whose box spans the scene while their geometry is nowhere near the eye.  A
  candidate rule ("the bbox flag counts only when a ray also lands within 1 m, or when no
  ray lands at all") took those ten firings to zero and added none, so it was written and
  validated against the corpus.

  **It was then reverted, because the corpus could not see what it broke.**
  `test_a_lens_inside_geometry_retreats_until_clear` puts a lens inside a `BarCounter`
  whose material is FrontSide: the rays are culled by the box itself, escape, and land on
  the floor 1.2 m away — so `nearest` is neither null nor under 1 m, and a genuinely buried
  camera stops being repaired.  The recorded corpus contains no buried camera at all, which
  is exactly why validating on it was not enough.  The measurement stands and is worth
  redoing with a discriminator that asks the right question (how far the eye is from the
  CONTAINING mesh's own surface, not from anything at all); the rule does not ship on this
  evidence.

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
