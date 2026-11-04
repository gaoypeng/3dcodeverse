# 3dcodeverse harness — architecture

Package `codeverse3d`, CLI `3dcodeverse`, repo location `harness/` in the
3dcodeverse checkout.  Backend only.  Python 3.13 (one fixed version), a small Node runtime
(`runtime_js/`) for everything Three.js / headless Chrome, and moderngl for the graphics track.
How-to is in `docs/RUNBOOK.md`, the skill library in
`docs/SKILLS.md`, the evaluation protocol in `eval/docs/EVAL.md`.

## 0. What it is

A data-flywheel harness in which LLMs write **raw executable 3D code** — no SDK layered on top
of the language — for four **tracks**:

| track | languages | deliverable |
|---|---|---|
| `static_object` | `blender` (bpy), `cadquery`, `threejs` | code + canonical `object.glb` (+stl/step) |
| `articulated_object` | `urdf_blender` (bpy link meshes + hand-written URDF) | code + `robot.urdf` + `meshes/*.glb` (raw link frames) + `object.glb` (node hierarchy with joint extras) |
| `scene` | `scene_threejs` (multi-file three.js + GLSL; optional bpy-built GLB assets), `scene_blender` (multi-file bpy, every module the agent's) | `src/**` + `public/assets/*.glb` + renders + probes |
| `graphics` | `glsl_shader` (Shadertoy-style fragment shader), `opengl_python` (raw moderngl + GLSL, multi-pass) | shader/program source + judged animation frames + sheet + GIF + frame metrics |

…driven by any of these backends behind two protocols:

* `ChatModel` (API): `gemini:*`, `anthropic:*`, `openai:*` — planner, judge, captioner,
  single-shot generation.
* `CodingAgent` (agentic session on a workspace): always a vendor CLI — `agy:*` (Antigravity,
  Google's models, Gemini-API mode with pool keys + MCP), `claude-code:*`, `codex:*`.  Plus
  `single-shot:<chat-model>` (one envelope of files, no tools) handled inside
  `tracks/generation.py`.  The harness implements no agent loop of its own.

Every run is: **spec → plan → skeleton → [scene stages] → baseline round (optionally best-of-N
candidates) → `max_rounds` refine rounds → finalise → record**.  Which round to hand over is
decided AFTER the run by the `addons/select` reader (`3dcodeverse pick`; `3dcodeverse make` calls it by
score): it writes `deliverable/` for that round.  The record, the git history of `src/` and every
round's kept build are the flywheel unit.

## 1. Design laws

1. **Code is truth; artifacts are derived.**  Raw code in `src/` is the deliverable; GLB/renders
   are regenerated from it.  Every round is a commit and keeps its own build under
   `artifacts/rNN/`, so any round can be handed over without a rebuild.
2. **Requirements, never templates.**  The harness states what must be true, where, and how it is
   measured — interface (files, functions, frames / units / naming), sandbox rules, measured
   thresholds — never a mandated shape, count, layout or code structure the brief did not ask
   for.  No language ships a cookbook; each `contract.md` keeps ONE executable interface example
   (`tests/prompts/test_snippets_run.py` runs it), the craft is a few short optional practices in
   the skills, and no code block in a prompt or skill is over ~12 lines.
3. **Code answers when code can answer.**  Deterministic gates / measurements run by the harness;
   the VLM is used only for perception and judgement, and its score is computed in code from
   rubric weights.
4. **Move responsibility out of agent code**: export wrappers, camera rigs, placement validation,
   URDF composition math, scene assembly, GLSL headers.
5. **Evidence-based acceptance.**  Every plan carries an acceptance checklist; items are proved by
   measurements / probes / judge votes, not prose.
6. **Typed everything.**  One contracts package; no regex-on-id control flow, no stringly-typed
   dicts, no god files (cap 2 000 lines per file, 3 000 absolute).  A merge must delete code, not
   merely move it.
7. **Cheap first.**  Lint → build → deterministic gates → montaged views → VLM.
8. **Separate generator from judge.**  The judge sees spec + renders + measurements + acceptance
   list; never the generator's reasoning.
9. **Money is measured, time is bounded.**  The wall clock is the hard per-run ceiling
   (`BudgetGuard`), key pools have per-key limiters, and every call is one row of the run's
   ledger, `telemetry/cost.jsonl` — the only record of money.
10. **Reproducible.**  Prompt / rubric / skill hashes and tool versions are recorded on every run
    (`record.prompt_hashes`, `record.environment`).
11. **Export as authored.**  No wrapper re-centres, rescales or grounds the object; builds warn,
    `check_contract` / `check_connectivity` gate, the agent fixes the source.

## 2. Package map (as built)

```
codeverse3d/
  conventions.py      frames (LANGUAGE_FRAME, GLB_FRAME), units, OBJECT_VIEWS/_QUICK/_CLAY_VIEWS, SCENE_VIEWS,
                      to_snake/to_pascal/slugify, MAX_TRIS_*, BBOX_TOLERANCE_M, CONTACT_GAP_M  (THE source)
  config.py           Settings (C3D_* env, ~/.config/3dcodeverse/config.yaml; Settings.backends(**overrides)
                      builds a Spec's Backends).  ONE switch grammar: every C3D_* knob is a field, read through
                      get_settings() where it is used; an on/off switch is a Flag, Settings.FLAT holds the flat
                      spellings of nested knobs (docs/INSTALL.md §8.3)
  contracts/          pydantic: common (Track, Language, Usage, Budget, Backends, TRACK_INFO registry,
                      ENTRY_FILE/code_file/LANGUAGE_LABEL tables), spec (+ RunOptions), plan, artifacts
                      (GateFinding, GateReport, BuildResult.gates, RenderView, RenderSet, Judgment), run
                      (+ SkillsUsage/SkillRead), chat, agent (typed AgentJob)
  workspace.py        run-dir layout + git snapshots
  doctor.py           the environment checks behind `3dcodeverse doctor` (deps, Blender, node, GPU probe, keys,
                      pool admission, vendor CLIs, MCP, skills)
  proc.py             stdlib-only subprocess + atomic-JSON primitives (ManagedProcess owns every child's
                      lifecycle: group kill on ANY exception, bounded pumps, stdin writer; run_subprocess,
                      write_json_atomic, scrub_secrets), the tolerant readers/writer (read_json_or_none,
                      iter_jsonl_lines, read_jsonl_lenient, append_jsonl_line), the JSONL event log (EventLog),
                      the run lock (ONE writer per run dir: fcntl.flock at <runs>/.locks/<slug>.lock naming the
                      holder, printed by `3dcodeverse status`), fan_out, sha256_file, version_line.  RULE: any
                      stdlib-only file / JSON / JSONL helper lives HERE.  A leaf: imports nothing from codeverse3d
  models/             ChatModel (base.py), parts.py; gemini.py (request/response shapes, dead-key + 429 rotation);
                      anthropic.py openai.py; retry.py (KeyPool with outcomes ok | 429 | 5xx | error | dead | skip,
                      the max_in_flight cap as machine-wide flock'd slots, rotate_with_retries — the one retry
                      loop); pricing.py, health.py (preflight probe: no retries), schema_utils.py (strict schema),
                      registry.py
  agents/             registry.py (the CodingAgent protocol + dispatch — every backend is a vendor CLI),
                      backends.py (claude-code / codex / antigravity: argv, env, each CLI's own record read back;
                      agy's API-mode HOME + pool key), cli_common.py (sessions, the watchdog clocks, the prompt on
                      stdin, the transcript, files_changed attribution, THE failure vocabulary, provider_wait),
                      materialize.py (workspace context files + hard rules)
  languages/          LanguageRuntime + RuntimeLayout (base.py: every runtime states its file layout —
                      expected_files / files_for — and the tracks ask it); one module per language:
                      blender/ cadquery/ threejs/ urdf/ scene_threejs/ scene_blender/ glsl_shader/ opengl_python/
                      are each a single __init__.py (lint → skeleton → runtime) beside their data (starter/,
                      opengl_python/wrappers/run_gl.py; the contract text is prompts/<lang>/contract.md); wrappers/
                      holds the python build wrappers (run_bpy, run_bpy_links, run_cq, run_bpy_scene), the
                      scene_blender render driver (render_bpy_scene) and what they share (_wrapper_common,
                      _census, _census_glb, _finite — the NaN / inf check on everything a Blender wrapper writes).
                      scene_blender/starter/src/ is its example scene (scene.py, env.py, zones/{hamlet, lakeside,
                      orchard}.py, assets/{crate, lantern_post}.py)
  spatial/            node.py, render.py, observe.py, tool_common.py (shared tool plumbing),
                      render_scene.py (judge view subset, content-fitted orbit), render_blender.py (scene_blender:
                      Blender pixels under a GPU slot + CPU fallback, the same metrics.json),
                      frame_metrics.py (scene_frames gate), frame_motion.py (measured inter-frame motion),
                      scene_placement.py (scene_placement gate = the check_placement tool's verdict: floating /
                      sunken / unsupported / interpenetration / cantilevered per placed asset; thresholds =
                      Settings.placement), gl_render.py (GlHost), frame_stats.py (gl_frames),
                      sheet.py (montage_2x2, crop_region), measure.py, connectivity.py, contract.py
                      (authoring-frame hints), sections.py (the cross_section tool + the judge slices), silhouette.py,
                      probes.py, complexity.py (objective complexity vector → Measurement.extra),
                      joints_model.py joints_poses.py joints_export.py joints_sweep.py joints_collide.py
                      (deterministic penetration; joints_sweep.sweep_gate is THE joint_sweep verdict — the round's
                      gate and the tool's), registry.py, tools.py (every @tool registration), mcp_server.py
                      (MCP name: 3dcode).  Render modes are contracts.artifacts.RENDER_MODES (shaded wire normals
                      silhouette clay); build error_type spellings are languages/_common.MISSING_ENTRY and
                      BUILD_TIMEOUT for every runtime
  skills/             registry.py (typed ROUTES + the router; select takes a variant's extra rows / boosts /
                      disabled ids), model.py (Skill/Selection + the SKILL.md loader), prompting.py (per-backend
                      discovery root + the mandate text), materialize.py, telemetry.py (the read probe: the CLIs'
                      own tool calls first, atime as fallback); switches are Settings fields (C3D_SKILLS*, on by
                      default).  variants/<name>/ is the skills half of a variant overlay: bundles that shadow base
                      bundles by name, their _claims/, and routes.toml (docs/SKILLS.md)
  cost/               types.py (CallCost/Stage/Role) ledger.py (append-only telemetry/cost.jsonl + price
                      provenance) context.py (per-call > ambient attribution) instrument.py (MeteredChatModel /
                      MeteredAgent — one row per ChatModel.generate; one session row for a backend that does NOT
                      meter itself; run_ledger is context-local so parallel batteries work) tally.py (a block's
                      money = its ledger rows, its lost seconds → StepTime) guard.py (a call's cost estimated
                      before it is sent — `3dcodeverse cost estimate`)
  judges/             base.py (JudgeInput; round_input + plan_summary, the ONE payload builder the in-run judge,
                      `3dcodeverse judge`, calibration and eval all use), rubrics.py + rubrics/*.yaml (defect
                      checklists, the wire schema, caps and scoring), prompt_builder.py (image prep, montages, the
                      judge messages), vlm_judge.py (+ the reference/likeness judges), pairwise.py.  No Judge
                      Protocol: a judge is duck-typed `.judge(JudgeInput) -> Judgment`
  reference.py        a user `--image` against the render: proportions and the render-vs-reference mismatch diff
  orchestrator.py     the round loop's LIBRARY, not the loop: RoundPolicy, refine-task compilation + grouping,
                      StageRunner + RunState (resume), BudgetGuard (the clock).  The loop itself is
                      tracks/lifecycle.py:_round_loop → tracks/steps.py:run_round; a best-of-N candidate IS
                      steps._run_round(kind='candidate') in a _cand/c<k> sub-workspace
  tracks/             __init__.py (get_track(track, **options) → a lifecycle.BaseTrack), lifecycle.py, steps.py,
                      candidates.py (best-of-N baseline candidates and their ranking), generation.py (agent +
                      single-shot strategies + the file envelope), repair.py, planner.py (the ONE planner loop + the
                      cached EngineeringBrief, plan budgets, and the ONE owner of what differs per track when
                      planning: template, example, temperature, output floor, acceptance), prompting.py (prompt
                      helpers), common.py (RunContext, Services), static_object.py (+ the reference-image gates),
                      articulated_object.py (+ the planned-motion gate), scene.py, scene_assets.py (+ cheap
                      single-shot asset generation), zone_layout.py (per-zone structured layout calls +
                      deterministic validator), graphics.py (the whole graphics track), plan_features.py
                      (eval-only: which C3D_* names are live, and pin_plan_blockers() deciding when two arms may
                      share one plan), depth.py, skills_hook.py (the round's view of codeverse3d/skills: attach
                      before generating, probe reads after — a no-op under C3D_SKILLS=0)
  record/             what every run WRITES: record.py (finalize_record, load_record, iter_runs,
                      effective_judgment), deliverable.py (every round's kept build under artifacts/rNN/, and
                      deliverable/ for ONE round — built by addons/select), telemetry.py, git_history.py (files at
                      a round's commit)
  addons/             optional tools that READ finished runs; outside cli/ nothing imports them
                      (tests/core/test_addons_boundary.py).  dataset/ = export.py, pack.py, pairs.py, refine.py,
                      captions.py, index.py, sample.py, quality.py (tiers + code fingerprints + duplicate groups)
                      — `3dcodeverse flywheel …`; costreport/ = audit.py, report.py, caching.py — `3dcodeverse cost`;
                      calibration.py (judge repeatability over recorded runs); skill_targets.py (measured targets
                      + claims per skill bundle); select.py (round_rows / pick / summarise / package: which round
                      of a finished run to hand over — `3dcodeverse pick`, and `3dcodeverse make` after the run)
  addons/gallery/     THE local run gallery (`3dcodeverse gallery serve|build`): cards.py, compare.py (side-by-side
                      arms), index.py (run roots → typed RunEntry, tolerant of half-written records), model.py,
                      page.py (cards + table + filters + per-filter summary), detail.py (/run/<battery>/<slug>),
                      code.py (src browser), viewer.py (GLB orbit viewer on the vendored three.js), urls.py
                      (server vs file:// targets + content types + the traversal guard), server.py (stdlib
                      http.server, loopback-only), theme.py (CSS + the index-page JS)
  prompts/            EVERY piece of prompt material the harness writes, and the only place it lives:
                      <lang>/{system,contract}.md (+ effects_catalog.md, asset.md where a language has them),
                      system/* (harness contract, single-shot envelope, role_{scope,asset,repair}.j2),
                      tracks/*.j2.  catalog.py answers "what exists and how does each piece reach the model",
                      including the ONE per-language lookup language_prompt(language, name) / language_text.
                      variants.py: per-model OVERLAYS — variants/<name>/<rel> shadows <rel> for load_text /
                      render / language_text while that variant is active (a ContextVar BaseTrack.run sets from
                      RunOptions.variant, else C3D_VARIANT / C3D_VARIANTS, resolved by exact generator id);
                      variant.toml = name/version/generators, variant_hash lands in the record.
                      codeverse3d/skills/ is what an AGENT chooses to read; a file that tries to be both is the
                      bug this split prevents
  cli/                main.py (app wiring, make/resume/mcp + the doctor/tools/gallery commands), inspect_cmd.py
                      (render/judge on one existing run), layout_cmd.py (THE single-run view: `show` = STATUS /
                      DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS; `status` = `show --section status`),
                      flywheel_cmd.py, cost_cmd.py (`3dcodeverse cost`), skills_cmd.py (`3dcodeverse skills
                      list|show|validate|report`)
eval/bench/           the battery launcher and reports (`python -m bench.run_bench` / `bench.report`,
                      compare_backends.py, ab_plan.py, complexity_report.py, prompts/*.yaml); never imported by
                      the harness — eval/docs/EVAL.md
runtime_js/           export_glb.mjs (placement policy, instance baking, selfcheck) render_glb.mjs render_scene.mjs
                      probe_scene.mjs check_shaders.mjs gpu_launch.cjs serve.cjs lib/{resolve_three, scene_host,
                      host_coverage, host_census, host_placement, orbit, instances, census, glsl_audit, browser/…}
                      browser/post.js — scene post chain: GTAO + selective (emissive-mask) bloom + an optional
                      colour grade; ON for scene renders, `C3D_POST=0` / `--no-post` to disable
tests/                one directory per package area (the count is `pytest --collect-only`'s; the bench tests are
                      ../eval/tests)
```

## 3. Workspace layout

One run is one directory, `runs/<slug>/`: `src/` is the code (git, one commit per round),
`artifacts/` the last round's build plus every round's own `artifacts/rNN/`, and a pick writes
`deliverable/` + `selection.json`; `telemetry/` holds the cost ledger and settings
(`workspace.py` names the buckets; `3dcodeverse show <slug>` walks a run).

## 4. Per-language authoring contracts (raw code; the harness owns export)

Authoritative text lives in `codeverse3d/prompts/<lang>/contract.md` (+ `system.md`); summary:

* **blender** (multi-file): `src/model.py` entry + `src/parts/<snake>.py` each defining
  `def build_<snake>()` (self-contained; optional `src/parts/_x.py` helpers) — pure bpy, Z-up,
  -Y front, meters; PascalCase object names = part names.  Small objects may stay single-file.
  The wrapper (`languages/wrappers/run_bpy.py`) puts `src/` on sys.path, maps errors to
  workspace-relative `src/parts/<x>.py:line`, collects a census (incl. material-slot use, read
  off the evaluated mesh) and exports GLB (Y-up) + STL.
* **cadquery**: `src/model.py` — `import cadquery as cq` (+math) only; module-level `result` =
  `cq.Assembly` or `Workplane`.  A chain ending in a selector exports the parent solid with a
  warning (ExportError when no solid exists); helper-module errors map to `src/<file>.py:line`.
* **threejs** (static): `src/parts/<snake>.js` each `export function build<Pascal>(THREE) → THREE.Group`
  at world pose (Y-up, +Z front, meters); `src/object.js` `export function build(THREE)`.  Export
  is **as authored**; `InstancedMesh` is baked to plain `<Name>_<i>` meshes; an exported
  `selfcheck(THREE, root)` is called and a throw fails the build as `SelfCheckError`.
* **urdf_blender**: `src/model.py` builds one object per link named `<link>` in **WORLD
  coordinates** at the rest pose (q=0 = authored pose); `src/robot.urdf` hand-written: link frame
  = its pivot, joint origin = pivot_child − frame_parent, `rpy="0 0 0"`, visual AND collision
  `<origin xyz>` = −pivot (root `0 0 0`) — verified by the FK-consistency check; URDF limits =
  plan lower−rest .. upper−rest.  Link name `world` is reserved.  The harness exports
  `meshes/<link>.glb` (raw Z-up link frames), fails the build on a rest-pose penetration > 5 mm,
  builds hierarchical `object.glb`; the round's `joint_sweep` gate sweeps every pose
  (deterministic; python-fcl) and `motion_direction` checks the axis against the plan text.
* **scene_threejs**: `src/scene.js` `createScene({THREE, renderer, loaders}) → {scene, cameras, update(t,dt)}`;
  `src/env.js`, `src/zones/*.js`, `src/assets/*.js`, `src/shaders/*.js`; GLBs at
  `public/assets/<name>.glb`.  The harness assembles `scene.js` deterministically.  The
  harness-owned, read-only **effect library** `src/lib/*.js` ships into every workspace and is
  listed in `HARNESS_OWNED_SRC` so agent writes to it are reverted; the want → call table the
  prompts carry is `prompts/scene_threejs/effects_catalog.md`.  `src/lib/` is committed with
  `src/`, so the deliverable and the dataset sample carry it.
* **scene_blender**: the same shape in bpy (`src/scene.py`, `src/env.py`, `src/zones/*.py`,
  `src/assets/*.py`), every module the agent's; judged frames render in Cycles.
* **glsl_shader**: `src/shader.frag` (+ optional `src/common.glsl`, `src/buffer_a.frag` for
  feedback) — the agent never writes `#version`, uniform declarations or `out vec4`; the harness
  header provides `u_time/u_resolution/u_mouse/u_frame/u_prev/u_noise/u_buffer_a` (+ Shadertoy
  `iTime/iResolution/iChannel*` aliases).  Feedback shaders are simulated at 30 fps.
* **opengl_python**: `src/program.py` with `setup(ctx, w, h) -> state` and
  `render(ctx, state, t, frame, fbo)` — moderngl only, no window libs, no wall clock, no file
  writes; GLSL may live in `src/*.glsl` or in-string (errors carry both line numbers).  Runs in a
  fresh subprocess per render; GPU d3d12 first, llvmpipe fallback.

## 5. Spatial tool registry

`@tool(name, ArgsModel, description, *, tracks=(), languages=(), cost_hint)` registers
`fn(ctx, args) -> Observation` → (a) direct call from tracks, (b) the stdio MCP server (name
`3dcode`) for the vendor CLIs, (c) a native tool schema for any embedder (`ToolDef.schema()`),
(d) a prompt card.  The 16 tools: `build` (every track), `measure`, `render_views`,
`render_sheet`, `isolate`, `cross_section`, `check_connectivity`, `check_contract`,
`compare_reference` (object tracks), `joint_sweep` (articulated), `shader_probe`, `scene_probe`,
`scene_views`, `check_placement` (scene), `gl_probe`, `gl_frames` (graphics).

An `Observation` carries two different answers: `ok` is the **verdict** and `failed` says the
tool **could not run** (exception, missing artefact, unusable arguments).  MCP `is_error` is
`failed` alone: a negative verdict is a result whose text leads with FAIL, because a vendor CLI
retries an errored call.  The MCP server bounds every payload it hands over (`MAX_TEXT_CHARS`,
`max_images_for`): no image at all on a `failed` result, one on a FAIL verdict.

## 6. Judging

`VlmJudge(rubric, model_id, n_samples)` sends **montages, not loose views**: ≤ 5 labelled 2×2
montages (shaded / geometry-only clay-or-normals / poses) + ≤ 2 detail crops at ≤ 1024 px,
shuffled per sample.  The wire schema is **observe-then-score** (summary, strengths, issues,
**defect checklist**, acceptance *before* criteria).  Every rubric carries binary `defects`
(id/text/penalty/cap); defect and acceptance votes are majority and
`overall = caps(weighted_mean − Σ penalties)`.  Floors, deterministic caps from gate findings
(`data["kind"]`), console errors, missing must-acceptance and `missing_views` rules apply on
top; degraded verdicts are glitches, not scores.

The judge's structure facts come from the gates, rendered from the stored `GateReport` so
`3dcodeverse judge <slug>` and calibration see exactly what the in-run judge saw: on the static track
the connectivity gate's **contact ledger** (parts / contacts / floating / deepest overlap, the
plan's PLANNED JOINS as contact or OPEN with the gap, the lowest point above the floor); on an
object-track round whose connectivity gate carries an ERROR, the payload grows **cross-section
slices** (`Settings.judge.slices = "on-error"`): `spatial.sections.judge_slices` cuts the round's
GLB on the two vertical centre planes, red hatch only on the gate's ERROR penetration pairs,
appended after the montages with a factual rig-text block.  A clean round's payload is
byte-identical to the unconditional one (test-pinned).

`PairwiseJudge` (position-swapped, tie on disagreement; only `3dcodeverse pick --by pairwise` asks
it); `ReferenceJudge` for image-conditioned specs.  Rubrics (pass threshold 0.70 unless named):
`static_object` (0.72), `articulated` (requires pose views: the `missing_pose_sheet` cap), `scene`
(placement caps floating 0.60 / sunken 0.65 / interpenetration 0.70 / scale 0.75 / free_exempt
0.75 measured by `scene_placement`, frame-gate caps dark/blown/flat/content_small, graded
must-acceptance), `asset`, `reference`, `shader` (graphics, `gl_frames` caps; also the
`LikenessJudge` default).

The default judge is `gemini:gemini-4-argon` (`Settings.default_judge`) with
`judge.thinking=medium`. **Calibration** (`addons/calibration.py`) re-judges recorded rounds
with repeated samples. The pro/flash measurements in `eval/docs/EVAL.md` §6 describe those
models; they do not establish Argon's variance or accuracy.

## 7. Run pipeline (all tracks)

A run is three parts, and only the first is a graph.

**7.1 The pre-round stages — declared, one tuple per track.**  `BaseTrack.stages` is a tuple of
`StageNode`s, walked by `lifecycle.run_stages` over `StageRunner`.  The block below is drawn from
those declarations (`tests/install/test_docs.py` fails when it drifts, printing the block to
paste):

```stage-graph
static_object       plan → skeleton → materialize* → round 0
articulated_object  plan → skeleton → materialize* → round 0
scene               plan → skeleton → materialize* → (assets ∥ env ∥ layouts?) → asset_api* → zones → clock* → assemble → round 0
graphics            plan → skeleton → materialize* → round 0
```

`→` in order; `(a ∥ b)` siblings side by side, each its own cached stage, timed as ONE step of
the run's minutes, a failing sibling raised after every sibling has finished and cached; `?` runs
under a `Settings` switch (`layouts`: `zone_layouts`, on); `*` is not cached — it runs every
session.  Every other stage is cached under its key (`stages/<name>.json`): what its result
depends on, named in the declaration.  **Once rounds exist** a recorded stage is served whatever
its key (`stage.frozen`), and the skeleton never runs at all; `3dcodeverse resume --force` is the one
way to regenerate a stage under them: it archives the round journal + `record.json` to
`rounds/pre_force/` and the run starts again at round 0.  The plan itself is `run()`'s, before
the graph (`plan_stage_key`).

What the scene stages do: `assets` — both kinds climb one ladder (single-shot → check → one
repair → agent; blender_glb heroes get a sub-workspace with the static planner's parts + asset
judge + one re-judged fix pass, undone when worse); `env` — `env_file` (`src/env.js` /
`src/env.py`): a `shared_tree` session credited with its own file only; when that file was not
written, ONE single-shot recovery runs before the zones, checked by `SceneRuntime.check_env` (the
env module alone lints, builds and lights what it builds), one repair on failure, the placeholder
restored if it still fails.  An env that lights nothing is a `scene_probe` ERROR, so the ROUND's
build fails and its repair is handed the env.  `layouts` — per-zone layouts (planner calls, never
fatal); `asset_api` — what the zones are told about the assets; `zones` — ONE session owns every
zone file; `clock` — the run stops here when the hard budget is spent, after the zones are
recorded; `assemble` — `scene.js`, deterministic.

**7.2 The round — `steps._run_round`, a fixed chain in code.**  Nothing in a round can be cached
(every step reads the git tree the step before it wrote).  **7.3 The round loop —
`BaseTrack._round_loop`, a state machine in code**: each round's tasks come from the previous
round's verdict.

```
round 0 "baseline": generate → build_with_repair → measure → gates → render → post-render gates → judge
   object tracks: ONE session drafts the WHOLE object — every part file + the entry — each file written once and
   then edited; a part still the skeleton's box is a `placeholder_part` contract ERROR
   --candidates N: N parallel baselines in <ws>/_cand/c<k>, quick 4-view judge, a crashed candidate retried once,
   selection build_ok → quick score → fewer gate errors → the earlier candidate; the winner is copied back
then max_rounds refine rounds, EACH built on the round before it — a FIXED count: no pass, plateau or regression stop
   refine tasks = the previous round's gate ERRORS (fix hints, authoring-frame numbers) ∪ failed must-acceptance
   ∪ its judge improvement plan (≤ 6 tasks, ≤ 6 compacted instruction lines each; a placeholder part's ERROR first)
   fan out when the track allows it (scene: zones/assets/env) AND ≥ 2 file-disjoint groups AND every task maps to
   files; the object tracks never fan out: ONE session edits the whole object in place, told to make targeted edits,
   never whole-file rewrites; its window ends RoundPolicy.judge_reserve_s before the run's hard ceiling so the
   round is still built and judged; a single-shot refine is fenced to the files its tasks name
   generate → build+repair (error-focused, escalates on identical signatures) → gates → render → judge
   → commit src/ (the round's commit) and copy its build to artifacts/rNN/
   a round left without a verdict (judge outage / degraded) is re-judged once before the next is planned, and so
   is the LAST round before the run ends (still none → judge_unavailable)
   a round that raises mid-way still reports what it burned (rounds/aborted_rNN.json)
   a round whose every task failed raises RoundFailed, TYPED: .quota → agent_quota; .transient (a provider
   failure) or .stalled (the watchdog found a session hung) → the SAME round re-runs once, baseline included;
   neither → no_change (a refine round) / the run fails (the baseline)
   a refine round launches only while RoundPolicy.can_open — judge_reserve_s plus min_session_s left on the hard
   clock (else round.skipped_budget → budget); a budget stop after refine sessions changed src/ gets ONE bounded
   grace and a no-generation round that builds, renders and judges those edits (BaseTrack._salvage)
stop reasons: max_rounds | budget (the wall clock — BudgetGuard / max_minutes) | agent_quota | no_change |
              no_refine_tasks | judge_unavailable | failed
finalise: the workspace ENDS AT THE LAST ROUND; finalize_record → record.json.  No best round, no deliverable/
after the run (`3dcodeverse make`, unless --no-pick): addons/select.pick(by="score") — highest effective score, ties →
          fewer gate errors → the earlier round — then package(): deliverable/ + selection.json
```

Track-specific gates: static `connectivity` + `contract` (+ `reference_silhouette`); articulated
+ `joint_sweep` + `motion_direction`; scene `scene_placement` (per placed asset — a direct child
of a zone root — foot-column gap to the surface beneath, burial depth, water, contacts and 3-D
interpenetration pairs from `runtime_js/lib/host_placement.mjs`; every other root is scenery,
never a row, but still supports, buries, holds and clips the zone assets) + `render_console` +
`scene_frames` (`spatial/frame_metrics.py`: dark/blown/flat frames, camera in geometry, content
too small, `no_motion` — the first and last animation frame of one camera diffed, the per-camera
"% of pixels changed" handed to the judge as a fact — and `unused_glb_asset`, from the geometry
UUIDs the scene host records per loaded GLB); graphics `gl_frames`
(NaN/black/blown/static/flicker).  A build's OWN gate reports ride `BuildResult.gates` — scene
`scene_probe` + `shader_preflight`, graphics `gl_frames` — and `steps._run_round` appends them to
the round's gates after the track's (a failed build's too: a shader compile error is what routes
the shader-traps skill).  `scene_probe` also holds the scene draw budget: the census's
`totals.draws` WARNs past `conventions.DRAWS_WARN_SCENE` and fails the build past
`MAX_DRAWS_SCENE`; no gate or judge reads fps.

## 8. Cost and time

`docs/RUNBOOK.md` §2 gives expected cost and time per track.  `3dcodeverse cost <slug>` prints a
run's recorded spend, `3dcodeverse cost estimate` a call's expected price.

## 9. Known limits

* Judge: recorded flash measurements are lenient/noisy on fine distinctions (overall std
  ≈ 0.08–0.12 at n=3); its dynamic range comes from the defect checklist. Calibrate the
  selected model with repeated samples before using small score differences for decisions.
  Detail crops are position-heuristic (no 2D part boxes).
* Scene judging is deliberately narrow: the judge sees the scene's OWN cameras plus at most four
  overview-rig tiles; the harness's eye-level rig is a diagnostic only.  A `must` acceptance item
  the harness cannot verify caps a round at 0.60 AND fails its verdict, so on the scene track
  only the spec's `must_have` list keeps that priority (the plan's own checklist is `should`).
* Articulated: candidate selection uses the quick 4-view rig plus the sweep's pose views; mimic
  joints are honoured; the sweep is O(links² × poses).
* Scenes: fps is a record only (the draw count is what is gated); camera-in-geometry can miss
  open-back enclosures.
* threejs: textures are stripped on GLB export; `userData.tick` cannot survive export.
* agy has no per-workspace MCP: each session gets a HOME of its own with the 3dcode server in it.
  A killed agy session books no usage; a killed codex session books an ESTIMATE (ledger
  `source="estimate"`); a killed claude-code session books its per-message usage, whose output
  side is a floor.
* Vendor sessions see only the routed skill bundles, except agy (5 built-ins; no per-session
  switch) and any user-level skill root codex might grow.
* Anthropic / OpenAI backends are mock-tested only.
* Budget checks run between steps: a round the clock cuts mid-way is rolled back (its spend
  stays in `aborted_rounds`) — give scenes `--max-minutes 60`.
* A sustained Gemini outage can still fail a round (`3dcodeverse resume` re-uses cached stages).  A
  storm-dead agent session gets the round loop's ONE transport retry (fresh key), no single-shot
  fallback.

## 10. Flywheel

`record.json` per run + git history; `3dcodeverse flywheel export` writes sample folders
(`<out>/<track>/<language>/<slug>/{code.<ext>, src/**, robot.urdf, meta.json, captions.json,
renders/}`) of the PICKED round (the last built round when none was judged) with **quality
tiers** from that round's verdict (A passed & 0 gate errors, B passed, C score ≥ 0.6, D else),
acceptance checklists, gate summaries and `(code fingerprint, prompt)` dedupe
(`--drop-duplicates`; side-car captions via `--captions-dir`); `metadata.parquet`/`.jsonl` carry
tier, gate errors, cost, fingerprints, `duplicate_of`; `--pack` tars with byte-range locators.
`flywheel pairs` emits preference pairs (round i < j by judge Δ ≥ τ) and round-level repair
pairs; `flywheel refine` emits the loop's OWN transitions — one row per (round i → round i+1)
where the harness asked for a change, carrying what condemned the round, the instructions
written in response, both code snapshots and whether the score moved
(`addons/dataset/refine.RefineTransition`).  That is the supervision the harness produces that a
one-shot corpus cannot.  `finetune/llamafactory/` turns those rows into training files; the
harness writes the measurement, not the trainer's format.  `flywheel caption` adds {detailed,
instruction, factory} captions (`--out` for side-car mode); the gallery is `3dcodeverse gallery build
--embed`.

`codeverse3d/addons/gallery/` is the **local** answer to the same question: `3dcodeverse gallery
serve` indexes `runs/` + every `eval/bench/out/*/runs`, serves the page **and the run
directories** on 127.0.0.1 (sheet, renders, `src/`, `object.glb` in an orbit viewer built on the
vendored three.js, `record.json`), and re-reads a run's record per request so a battery that is
still writing shows up live (RUNBOOK §4).  A URL can only name a `(battery, slug)` the scanner
found under a declared root, and `urls.safe_join` refuses anything that escapes that run
directory.  `3dcodeverse gallery build [--embed]` writes the same page as one file.
