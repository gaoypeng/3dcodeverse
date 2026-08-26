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
* **L4 Typed, no regex-on-id, no god files.**  Files ≤ 1 500 lines (owner, 2026-08-26 — was ~400); two wrappers
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
  single-file languages get one whole-object task.  Consequence: `generation.workspace_lock`
  + retried `git diff` protect the shared git index (Workspace itself has no lock).
* **D5 A refine round that changes no file is a plateau, not a crash.**  Emits
  `round.no_change` and stops with the best round so far.
* **D6 Finalise restores the best commit and rebuilds.**  Artifacts always match the
  delivered code; export copies `artifacts/object.glb` trusting that.
* **D7 Stage cache by input hash.**  `StageRunner` stores `stages/<name>.json` keyed by a
  hash of the inputs, so `3dcv resume` re-runs only what changed; spent budget is
  restored from `run_state.extra.spent_usage`.
* **D8 Events carry `event`, not `kind` (Δ).**  `EventLog.emit(event, **data)` so payloads
  may include `kind=` (round kind).  `3dcv status` and tests follow.
* **D9 `materialize_workspace` returns `Materialized` (Δ).**  Callers need codex `-c`
  overrides and the agy fallback text; callers that ignore the return are unaffected.
  The cookbook is copied to `ws/.3dcv/cookbook.md` (gemini-cli cannot read outside the
  workspace); MCP argv uses `sys.executable`.
* **D10 gemini-cli needs a system-settings file (Δ).**  api-key auth +
  `dynamicModelConfiguration` (else silent model substitution) + `folderTrust.enabled=false`
  (else workspace MCP servers are silently disabled even with `--skip-trust`).  Served
  model is checked; mismatch → `exit_reason=model_substituted`.
* **D11 AgentJob.extra is the side-channel (Δ).**  Keys `round`, `kind`, `language`, `track`
  (+ `mcp_command`, `max_usd`, `temperature`, `thinking`, `allow_shell` for api-agent)
  rather than new AgentJob fields, to keep `contracts/agent.py` frozen.
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

## Rejected / deferred

* Registering `single-shot` as a CodingAgent kind (rejected: it has no tools/session).
* A free-form `dict` judge schema (rejected: flash skips criteria).
* Storing URDF meshes Y-up and converting on load (rejected: breaks foreign loaders).
* Per-asset judging for threejs scene assets (deferred: needs a `render_asset` hook).
* Proposed and not yet done (owner-level files): `ToolCallPart.extra` for provider
  state; `Workspace._git` lock + index.lock retry; package-data globs for
  `languages/**/starter/**` and `languages/**/*.md`; `n_samples` in Settings.
