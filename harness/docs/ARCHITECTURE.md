# 3dcodeverse harness — architecture

Package `codeverse3d`, CLI `3dcodeverse` (short alias `3dcode`), repo location
`/home/yipeng/3dcodeverse/harness`.  Backend only.  Python 3.13 (one fixed version), a small Node
runtime (`runtime_js/`) for everything Three.js / headless Chrome, and moderngl
for the graphics track.  Checked against the code on 2026-09-22; design history and deviations are in
`docs/DECISIONS.md`, how-to in `docs/RUNBOOK.md`, evaluation protocol in
`eval/docs/EVAL.md`, complexity measurement + the score-vs-complexity study in
`eval/docs/COMPLEXITY.md`, binding signatures in `docs/INTERFACES.md`.

## 0. What it is

A data-flywheel harness in which LLMs write **raw executable 3D code** — no SDK
layered on top of the language — for four **tracks**:

| track | languages | deliverable |
|---|---|---|
| `static_object` | `blender` (bpy), `cadquery`, `threejs` | code + canonical `object.glb` (+stl/step) |
| `articulated_object` | `urdf_blender` (bpy link meshes + hand-written URDF) | code + `robot.urdf` + `meshes/*.glb` (raw link frames) + `object.glb` (node hierarchy with joint extras) |
| `scene` | `scene_threejs` (multi-file three.js + GLSL; optional bpy-built GLB assets) | `src/**` + `public/assets/*.glb` + renders + probes |
| `graphics` | `glsl_shader` (Shadertoy-style fragment shader), `opengl_python` (raw moderngl + GLSL, multi-pass) | shader/program source + judged animation frames + sheet + GIF + frame metrics |

…driven by **any** of these backends behind two protocols:

* `ChatModel` (API): `gemini:*`, `anthropic:*`, `openai:*` — planner, judge,
  captioner, single-shot generation.
* `CodingAgent` (agentic session on a workspace): always a vendor CLI —
  `gemini-cli:*`, `claude-code:*`, `codex:*`, `agy:*` (Antigravity); the in-process
  `api-agent` was deleted 2026-08-28.  Plus `single-shot:<chat-model>` (one envelope
  of files, no tools) handled inside `tracks/generation.py`.

Every run is: **spec → plan → skeleton → [scene stages] → baseline round
(optionally best-of-N candidates) → `max_rounds` refine rounds → finalise → record**.
Which round to hand over is decided AFTER the run, by the `addons/select` reader
(`3dcode pick`; `3dcode make` calls it by score): it writes `deliverable/` for that
round and runs the optional **texture pass** on it.  The record (plus git history of
`src/` and every round's kept build) is the flywheel unit.

## 1. Design laws (from the reference post-mortems)

1. **Code is truth; artifacts are derived.**  Raw code in `src/` is the
   deliverable; GLB/renders are regenerated from it.  Every round is a commit and
   keeps its own built files under `artifacts/rNN/`, so any round can be handed over
   without a rebuild; the workspace ends at the last round.  Textures are a *derived asset pack*
   (`object_textured.glb` + `artifacts/textures/`); `object.glb` is never touched.
2. **Concrete beats abstract.**  Contracts are delivered as skeleton code, exact
   numbers and copyable snippets (cookbooks), never as clauses.
3. **Code answers when code can answer.**  Deterministic gates / measurements run
   by the harness; the VLM is used only for perception and judgement, and its
   score is computed in code from rubric weights.
4. **Move responsibility out of agent code**: export wrappers, camera rigs,
   placement validation, URDF composition math, scene assembly, GLSL headers.
5. **Evidence-based acceptance.**  Every plan carries an acceptance checklist;
   items are proved by measurements/probes/judge votes, not prose.
6. **Typed everything.**  One contracts package; no regex-on-id control flow, no
   stringly-typed dicts, no god files (cap 2 000 lines per file, 3 000 absolute; owner 2026-08-28).
   A merge must delete code, not merely move it.
7. **Cheap first.**  Lint → build → deterministic gates → montaged views → VLM.
8. **Separate generator from judge.**  The judge sees spec + renders +
   measurements + acceptance list; never the generator's reasoning.
9. **Money is measured, time is bounded.**  The wall clock is the hard per-run ceiling
   (`BudgetGuard`), key pools have per-key limiters, and every call is one row of the
   run's ledger, `telemetry/cost.jsonl` — the only record of money.
10. **Reproducible.**  Prompt/rubric/cookbook hashes and tool versions are
    recorded on every run (`record.prompt_hashes`, `record.environment`).
11. **Export as authored.**  No wrapper re-centres, rescales or grounds the
    object; builds warn, `check_contract`/`check_connectivity` gate, the agent
    fixes the source.

## 2. Package map (as built)

```
codeverse3d/
  conventions.py      frames (LANGUAGE_FRAME, GLB_FRAME), units, OBJECT_VIEWS/_QUICK/_CLAY_VIEWS, SCENE_VIEWS,
                      to_snake/to_pascal/slugify, MAX_TRIS_*, BBOX_TOLERANCE_M, CONTACT_GAP_M  (THE source)
  config.py           Settings (C3D_* env, ~/.config/3dcodeverse/config.yaml; role defaults come from
                      contracts Backends; Settings.backends(**overrides) builds a Spec's Backends;
                      default_candidates=1).  ONE switch grammar: every C3D_* knob is a field, read
                      through get_settings() where it is used — no module reads the environment by
                      hand; an on/off switch is a Flag, Settings.FLAT holds the flat spellings of
                      nested knobs (the grammar and the names: docs/INSTALL.md §8.3)
  contracts/          pydantic: common (Track, Language, Usage, Budget, Backends, TRACK_INFO registry,
                      ENTRY_FILE/code_file/LANGUAGE_LABEL tables), spec (+ RunOptions), plan, artifacts
                      (GateFinding.as_line, GateReport.of, BuildResult.gates, RenderView.judge,
                      RenderSet.out_dir, + Judgment), run
                      (+ SkillsUsage/SkillRead: what was attached, what was read), chat,
                      agent (typed AgentJob)
  workspace.py        run-dir layout + git snapshots
  doctor.py           the environment checks behind `3dcode doctor` (deps, Blender,
                      node, GPU probe, keys, pool admission, vendor CLIs, MCP, skills)
  proc.py             stdlib-only subprocess + atomic-JSON primitives (ManagedProcess owns every
                      child's lifecycle: group kill on ANY exception, bounded pumps, stdin writer;
                      run_subprocess, tail, write_json_atomic, scrub_secrets) and the tolerant readers/writer
                      (read_json_or_none, iter_jsonl_lines, read_jsonl_lenient, append_jsonl_line) —
                      shared by languages/spatial/cli/cost/record/addons and eval/bench.  RULE: any
                      stdlib-only file / JSON / JSONL helper lives HERE; grep proc.py before writing a
                      try/except read (the 2026-08-26 review found the same tolerant read written
                      eight times because this module had not grown it).  Also home, since
                      2026-08-28, to the JSONL event log (EventLog), the run lock (ONE writer per
                      run dir: an fcntl.flock at <runs>/.locks/<slug>.lock whose record NAMES the
                      holder, printed by `3dcode status`) and the bounded parallel fan-out
                      (fan_out); sha256_file and version_line live here too — still a leaf:
                      imports nothing from codeverse3d
  models/             ChatModel (base.py), parts.py; gemini.py (the whole Gemini stack:
                      request/response shapes, dead-key + free 429 rotation, the image model);
                      anthropic.py openai.py (each with its own request/response shapes);
                      retry.py (the scheduling machine: KeyPool with outcomes ok | 429 | 5xx |
                      error | dead | skip and the max_in_flight cap as machine-wide flock'd
                      slots (Slots, docs/COST.md §23) — no RPM/TPM buckets since 2026-09-22,
                      docs/COST.md §19 — and rotate_with_retries, the one retry loop, which
                      the SDK adapters run over a one-key pool; bounded by max_total_s ≤
                      RETRY_DEADLINE_S with MAX_WAIT_S ≤ 3 s single waits); pricing.py
                      (version-suffix-only fallback), health.py (preflight
                      probe: no retries, no backoff), schema_utils.py (strict schema), registry.py
  agents/             registry.py (the CodingAgent protocol + dispatch — every backend is a
                      vendor CLI; the in-process api-agent died 2026-08-28), backends.py
                      (gemini-cli / claude-code / codex / antigravity: argv, env, each CLI's own
                      record read back — envelope, chat record / stream, retry reports), cli_common.py
                      (sessions, the watchdog clocks, the prompt on stdin, the transcript, retry
                      trajectory naming, files_changed attribution, THE failure vocabulary,
                      provider_wait), materialize.py
  languages/          LanguageRuntime + RuntimeLayout (base.py: every runtime states its file layout —
                      expected_files / files_for — and the tracks ask it); one merged module per language since 2026-08-28 —
                      blender/ cadquery/ threejs/ urdf/ scene_threejs/ scene_blender/ glsl_shader/ opengl_python/ are each
                      a single __init__.py (lint → skeleton → runtime, in dependency order) beside their
                      data (starter/, opengl_python/wrappers/run_gl.py; the contract text is
                      prompts/<lang>/contract.md, found like every per-language prompt file by
                      prompts/catalog.language_prompt — a runtime holds no prompt text); wrappers/
                      holds the python build wrappers (run_bpy, run_bpy_links, run_cq, run_bpy_scene), the
                      scene_blender render driver (render_bpy_scene) and what they
                      share (_wrapper_common: script run + error mapping + report; _census: the Blender census;
                      _census_glb: scene_blender's census GLB writer, D102)
  spatial/            node.py, render.py, observe.py, tool_common.py (shared tool plumbing),
                      render_scene.py (judge view subset, content-fitted orbit),
                      render_blender.py (scene_blender: Blender pixels under a GPU slot + CPU fallback,
                      the JS host's camera checks on them, the same metrics.json),
                      frame_metrics.py (scene_frames gate), frame_motion.py (measured inter-frame motion),
                      scene_placement.py (scene_placement gate = the check_placement tool's verdict,
                      placement_gate(ws, census, plan) for both: floating / sunken /
                      unsupported / interpenetration per placed asset from the probe census's placement
                      table, runtime_js/lib/host_placement.mjs; added 2026-08-26),
                      gl_render.py (GlHost), frame_stats.py (gl_frames),
                      sheet.py (montage_2x2, crop_region), measure.py, connectivity.py,
                      contract.py (authoring-frame hints), sections.py (the cross_section tool and the
                      D48 judge slices: one matplotlib section renderer), silhouette.py, probes.py,
                      complexity.py (objective complexity vector -> Measurement.extra, eval/docs/COMPLEXITY.md),
                      joints*.py + joints_collide.py (deterministic penetration; joints_sweep.sweep_gate is
                      THE joint_sweep verdict — the round's gate and the tool's), registry.py,
                      tools.py (every @tool registration since 2026-08-28; every import is a plain
                      one — codeverse3d.languages is imported inside the tool bodies),
                      mcp_server.py (MCP name: 3dcode).
                      Render modes are contracts.artifacts.RENDER_MODES (shaded wire normals silhouette
                      clay — no 'depth'); build error_type spellings are languages/_common.MISSING_ENTRY
                      ("MissingEntryFile") and BUILD_TIMEOUT ("BuildTimeout") for every runtime
  skills/             registry.py (typed ROUTES + the router that evaluates them),
                      model.py (Skill/Selection + the SKILL.md loader), prompting.py
                      (per-backend delivery policy + the index/mandate text),
                      materialize.py,
                      telemetry.py (the read probe: the CLIs' own tool calls first, atime as
                      fallback); its switches are Settings fields (C3D_SKILLS*, ON by default since
                      2026-09-22)
  cost/               types.py (CallCost/Stage/Role) ledger.py (append-only telemetry/cost.jsonl + price provenance)
                      context.py (per-call > ambient attribution) instrument.py (MeteredChatModel /
                      MeteredAgent — one row per ChatModel.generate; one session row only for a backend
                      that does NOT meter itself; run_ledger nests + is context-local so bench --parallel works)
                      profiles.py (economy|balanced|quality; cli._common.resolve_dial is THE resolver)
                      tally.py (a block's money = its ledger rows, its lost seconds → StepTime; COST §31)
                      guard.py (a call's cost estimated before it is sent — `3dcode cost estimate`)
  judges/             base.py (JudgeInput; `round_input` + `plan_summary(plan, language)`, the ONE payload
                      builder and plan digest the in-run judge, `3dcode judge`, calibration, the texture
                      gate and eval all use; the pure replay helpers), rubrics.py + rubrics/*.yaml (defect checklists, the wire
                      schema, caps and scoring), prompt_builder.py (image prep, montages, the
                      judge messages), vlm_judge.py (+ the reference/likeness judges),
                      pairwise.py.
                      No Judge Protocol: a judge is
                      duck-typed `.judge(JudgeInput) -> Judgment`
  reference.py        reference GROUNDING — give the pipeline a picture of what it is building:
                      synthesis, THE plausibility gate that makes a synthesized
                      reference safe to use, Spec attachment + honesty guards, proportions
                      (does the picture agree with the brief?), render-vs-reference diff,
                      the content-addressed cache and ground_spec (the one call the CLI makes)
  texturing/          plan.py (VLM material plan + the scene texture pack), generate.py
                      (generation + the tileable seam fix + the seam/judge gate), apply.py
                      (world-metre unwrap, PBR map set, application + material normalisation),
                      materials.py (named material library), run.py (texture_pass); __init__.py is
                      docstring-only — import from the submodules
  orchestrator.py     the round loop's LIBRARY, not the loop: the round knobs (RoundPolicy —
                      no stop knob besides max_rounds), refine-task compilation + grouping,
                      StageRunner + RunState (resume), BudgetGuard (the clock).  The loop itself is
                      tracks/lifecycle.py:_round_loop → tracks/steps.py:run_round.  A best-of-N
                      candidate IS steps._run_round(kind='candidate') in a _cand/c<k> sub-workspace
                      with two knobs (render=candidates.quick_render, geometry_views=False), its
                      own _cand/c<k>/events.jsonl and a one-sample judge
  tracks/             __init__.py (get_track(track, **options) → a lifecycle.BaseTrack),
                      lifecycle.py, steps.py, candidates.py (best-of-N baseline candidates and
                      their ranking), generation.py (agent + single-shot strategies + the file
                      envelope), repair.py, planner.py, prompting.py (prompt helpers, split from common),
                      common.py (RunContext, Services), static_object.py (+ the reference-image
                      gates), articulated_object.py (+ the planned-motion gate),
                      scene.py, scene_assets.py (+ cheap single-shot asset generation),
                      zone_layout.py (L2 zone director: per-zone structured layout calls + deterministic validator),
                      graphics.py (the whole graphics track: prompt context, frame
                      RenderSet, and recipe seeding into the harness-owned, read-only
                      src/recipes.glsl — measured: flash calls a recipe on disk, not one it is
                      shown; AgentJob.read_only, C3D_SEED_RECIPES),
                      planner.py (the ONE planner loop + the cached EngineeringBrief
                      (C3D_PLAN_BRIEF), plan budgets and the worked examples — and the ONE owner of
                      what differs per track when planning: template, example, temperature, output
                      floor, acceptance, all dispatched on spec.track),
                      plan_features.py (eval-only: which C3D_* names are live, derived from
                      Settings, and pin_plan_blockers() deciding when two arms may share one
                      plan — eval/docs/EVAL.md §8.1),
                      depth.py,
                      skills_hook.py (the round's view of codeverse3d/skills: attach before generating,
                      probe reads after — a no-op under C3D_SKILLS=0; on by default since 2026-09-22)
  record/             what every run WRITES: record.py (finalize_record, load_record, iter_runs,
                      effective_judgment), deliverable.py (every round's kept build under
                      artifacts/rNN/, and deliverable/ for ONE round — built by addons/select),
                      telemetry.py, git_history.py (files at a round's commit)
  addons/             optional tools that READ finished runs; outside cli/ nothing imports them
                      (tests/core/test_addons_boundary.py).  dataset/ = export.py, pack.py, pairs.py,
                      refine.py, captions.py, index.py, sample.py, quality.py (tiers + code fingerprints +
                      duplicate groups) — `3dcode flywheel …`; costreport/ = audit.py, report.py,
                      caching.py (session_cache/session_key — measurement only, docs/COST.md §13) —
                      `3dcode cost`; calibration.py (judge repeatability over recorded runs);
                      skill_targets.py (measured targets + claims per skill bundle);
                      select.py (round_rows / pick / summarise / package: which round of a
                      finished run to hand over, deliverable/ + selection.json for it —
                      `3dcode pick`, and `3dcode make` after the run); gallery/ = below
  addons/gallery/     THE local run gallery (`3dcode gallery serve|build`): cards.py,
                      compare.py (side-by-side arms), index.py (run roots →
                      typed RunEntry, tolerant of half-written records), model.py, page.py (cards +
                      table + filters + per-filter summary), detail.py (/run/<battery>/<slug>),
                      code.py (src browser), viewer.py (GLB orbit viewer on the vendored three.js),
                      urls.py (server vs file:// targets + content types + the traversal guard),
                      server.py (stdlib http.server, loopback-only; page.py also renders the
                      static single-file form), theme.py (CSS + the index-page JS)
  prompts/            EVERY piece of prompt material the harness writes, and the only place it
                      lives: <lang>/{system,contract,cookbook}.md (incl. glsl_shader/,
                      opengl_python/), system/* (harness contract, single-shot envelope,
                      role_{scope,asset,repair}.j2), texturing/*.md, tracks/*.j2.
                      catalog.py answers "what exists and how does each piece reach the model"
                      — including the ONE per-language lookup, language_prompt(language, name) /
                      language_text: contract.md, cookbook.md, system.md, effects_catalog.md, asset.md
                      (the prompts/<dir> mapping used to be copied four times and missing in a fifth;
                      until 2026-09-22 the contract came through the runtime, the cookbook through
                      tracks/common, the system prompt and the catalog through tracks/prompting).  sections.py splits that
                      markdown into chapters so a STAGE can name the recipes it needs.
                      The other half of the split: codeverse3d/skills/ is what an AGENT chooses
                      to read (SKILL.md + references/ + a _claims file pinning its numbers to
                      live constants).  A file that tries to be both is the bug this prevents.
  cli/                main.py (app wiring, make/resume/mcp + the tools/gallery
                      commands), inspect_cmd.py (render/judge on one existing run), layout_cmd.py
                      (THE single-run view: `show` = STATUS / DELIVERABLE / QUALITY EVIDENCE / COST &
                      SETTINGS; `status` = `show --section status`, which also reads a run in flight),
                      flywheel_cmd.py, texture_cmd.py, cost_cmd.py (`3dcode cost`), doctor.py
                      (`--skills` checks the library + its discovery wiring),
                      skills_cmd.py (`3dcode skills list|show|validate|report` — the read-rate report)
eval/bench/                run_bench.py + report.py (the battery launcher, `python -m bench.run_bench` / `bench.report`;
                      the report renders through codeverse3d/addons/gallery), compare_backends.py
                      (preflights every model it needs; --wait-for-provider / --no-preflight),
                      _infra.py (outage vs model failure: infra_failed / budget_exhausted, eval/docs/EVAL.md §7),
                      pin_plan.py (seed one plan into both arms so the paired delta stops carrying
                      the planner's spread — permitted only by plan_features.pin_plan_blockers),
                      _compare_report.py (arm table incl. the `dropped` / `over budget` loss columns;
                      ArmStats, the one arm aggregate — the A/B summary reads it too),
                      _jsonl.py (the ONE tolerant reader, dedup rule — `latest`, the last row per
                      key — and append-sealer for the resumable *.jsonl journals: a truncated last
                      line never costs the paid rows), stats.py (the one t-interval, exact sign
                      test and correlation every report states),
                      ab_plan.py (paired control/variant A/B for plan + brief switches, --aa
                      calibration mode; pins both children to the cap the §23 admission check
                      reserved), _ab_report.py,
                      ab_gate_rates.py (the same run's DETERMINISTIC readouts, paired per
                      prompt: penetrating pairs, worst depth, floating parts, contract findings),
                      _oneshot.py, _fixed_eval.py,
                      concurrency_probe.py (in-flight knee sweep), complexity_report.py,
                      prompts/*.yaml (the batteries: eval/docs/EVAL.md §2)
runtime_js/           export_glb.mjs (placement policy, instance baking, selfcheck) render_glb.mjs
                      render_scene.mjs probe_scene.mjs check_shaders.mjs (the lib tests' compile driver) gpu_launch.cjs serve.cjs
                      lib/{resolve_three, scene_host, host_coverage, host_census, host_placement, orbit, instances,
                      census, glsl_audit, browser/…}
                      browser/post.js — scene post chain: GTAO + SELECTIVE bloom (an emissive
                      mask, not a luminance bright-pass: on our renderer the sky dome at 1.88
                      linear outshines every authored emissive, so a threshold cannot separate
                      them) + a colour grade that is the identity unless the scene sets
                      `scene.userData.grade`.  ON for scene renders, `C3D_POST=0` / `--no-post`
                      to disable; object renders never come through this driver.
tests/                agents blender_cadquery core cost flywheel_cli gallery graphics
                      install judges languages models orchestrator_tracks prompts reference scene_gates
                      scene_prompts scene_runtime skills spatial_tools texturing threejs_render urdf_joints
                      (22 dirs; the count is `pytest --collect-only`'s; the bench tests are ../eval/tests)
```

## 3. Workspace layout

One run is one directory, `runs/<slug>/`: `src/` is the code (git, one commit per round),
`artifacts/` the last round's build plus every round's own `artifacts/rNN/`, and a pick
writes `deliverable/` + `selection.json`.  The full tree, the three buckets and the
back-compatibility rules are in `docs/RUN_LAYOUT.md`, its one home.

## 4. Per-language authoring contracts (raw code; the harness owns export)

Authoritative text lives in `codeverse3d/prompts/<lang>/contract.md` (+ cookbook);
summary:

* **blender** (multi-file): `src/model.py` entry + `src/parts/<snake>.py` each
  defining `def build_<snake>()` (self-contained; optional `src/parts/_x.py`
  helpers) — pure bpy, Z-up, -Y front, meters; PascalCase object names = part
  names.  Small objects may stay single-file.  Harness wrapper (`languages/wrappers/run_bpy.py`
  + `_wrapper_common.py` + `_census.py`) puts `src/` on sys.path, maps errors to workspace-relative
  `src/parts/<x>.py:line`, collects census, exports GLB (Y-up) + STL.  The census
  records `n_material_slots` + `material_indices_used` (read off the EVALUATED mesh,
  so modifier-added indices count) and warns when a mesh carries slots no polygon
  uses, or sits on an index past the last slot — one part is one mesh object, so
  per-feature colour is per-polygon `material_index`, and an assignment that
  silently no-ops ships the whole part in one flat colour (2026-08-30).
* **cadquery**: `src/model.py` — `import cadquery as cq` (+math) only; module-level
  `result` = `cq.Assembly` or `Workplane`.  A chain ending in a selector exports the
  parent solid with a warning (ExportError when no solid exists); helper-module
  errors map to `src/<file>.py:line`.
* **threejs** (static): `src/parts/<snake>.js` each `export function build<Pascal>(THREE) → THREE.Group`
  at world pose (Y-up, +Z front, meters); `src/object.js` `export function build(THREE)`.
  Export is **as authored** (no re-centring);
  `InstancedMesh` is baked to plain `<Name>_<i>` meshes (trimesh ignores
  EXT_mesh_gpu_instancing); an exported `selfcheck(THREE, root)` is called and a
  throw fails the build as `SelfCheckError`; NaN-geometry errors name mesh + part
  and route to the part file.
* **urdf_blender**: `src/model.py` builds one object per link named `<link>` in
  **WORLD coordinates** at the rest pose (q=0 = authored pose); `src/robot.urdf`
  hand-written: link frame = its pivot, joint origin = pivot_child − frame_parent,
  `rpy="0 0 0"`, visual AND collision `<origin xyz>` = −pivot (root `0 0 0`) —
  verified by the FK-consistency check; URDF limits = plan lower−rest .. upper−rest.
  Link name `world` is reserved.  Harness exports `meshes/<link>.glb` (raw Z-up link
  frames), fails the build on a rest-pose penetration > 5 mm (D17; the rest pose is the
  only one the build collides), builds hierarchical `object.glb`; the round's
  `joint_sweep` gate sweeps every pose (deterministic; python-fcl) and a gate checks
  motion direction against the plan text.
* **scene_threejs**: `src/scene.js` `createScene({THREE, renderer, loaders}) → {scene, cameras, update(t,dt)}`;
  `src/env.js`, `src/zones/*.js`, `src/assets/*.js`, `src/shaders/*.js`; GLBs at
  `public/assets/<name>.glb`.  The harness assembles `scene.js` deterministically.
  Plus the harness-owned, read-only **effect library** `src/lib/*.js` (the starter's other
  `.js` files are the example scene), shipped into every workspace by BOTH skeleton paths and
  listed in `HARNESS_OWNED_SRC` so agent writes to it are reverted (D51, D97).  The want → call
  table the prompts carry is `prompts/scene_threejs/effects_catalog.md`.  A three.js asset module
  may compose it too (D100): its prompt carries the catalog rows of the factories its sheet names
  (`scene_assets.library_rows`), and the asset check (`runtime_js/lib/asset_check.mjs`) counts what
  a factory built — `lib/lifecycle.js` `isLibraryBuilt` — as `lib_tris` against the one-object
  ceiling, not the per-asset budget, and seats a factory by its origin.  `src/lib/` is committed
  with `src/`, so the deliverable and the dataset sample carry it.
* **glsl_shader**: `src/shader.frag` (+ optional `src/common.glsl`,
  `src/buffer_a.frag` for feedback; the harness-owned, read-only `src/recipes.glsl`
  is pasted above them when the track seeded recipes) — the agent never writes
  `#version`, uniform declarations or `out vec4`; the harness header provides
  `u_time/u_resolution/u_mouse/u_frame/u_prev/u_noise/u_buffer_a` (+ Shadertoy
  `iTime/iResolution/iChannel*` aliases).  Feedback shaders are simulated at 30 fps.
* **opengl_python**: `src/program.py` with `setup(ctx, w, h) -> state` and
  `render(ctx, state, t, frame, fbo)` — moderngl only, no window libs, no wall
  clock, no file writes; GLSL may live in `src/*.glsl` or in-string (errors carry
  both line numbers).  Runs in a fresh subprocess per render (contexts are not
  thread-safe); GPU d3d12 first, llvmpipe fallback.

## 5. Spatial tool registry

`@tool(name, ArgsModel, description, *, tracks=(), languages=(), cost_hint)` registers
`fn(ctx, args) -> Observation` → (a) direct call from tracks, (b) the stdio MCP
server (name `3dcode`) for the vendor CLIs, (c) a native tool schema for any embedder
(`ToolDef.schema()`), (d) a prompt card.  The 16 tools: `build` (every track), `measure`,
`render_views`, `render_sheet`, `isolate`, `cross_section`, `check_connectivity`,
`check_contract`, `compare_reference` (object tracks), `joint_sweep` (articulated),
`shader_probe`, `scene_probe`, `scene_views`, `check_placement` (scene), `gl_probe`,
`gl_frames` (graphics).  Texturing is not a tool: `3dcode pick --texture` or
`3dcode texture pass` after the run (2026-09-22: 1 call in 616 recorded sessions).

An `Observation` carries two different answers: `ok` is the **verdict** and `failed`
says the tool **could not run** (exception, missing artefact, unusable arguments).
`Observation.error` builds every failure caught at the `ToolDef.call` boundary; three
tools set `failed` on an observation they compose themselves; the list of those places
lives with the field, in `spatial/registry.Observation.error`.  MCP `is_error` is `failed` alone: a
negative verdict is a result whose text leads with FAIL, because a vendor CLI retries an
errored call, and over 224 recorded gemini-cli sessions `is_error = not ok` made 62 % of
1 445 `joint_sweep` calls, 23 % of 2 144 `build`s and ~15 % of the connectivity/contract
calls look broken (a mean 119 k prompt tokens ≈ $0.030 blended a retry — docs/COST.md §30
for the selector, and for what an errored result costs when it carries an image).
The MCP server also bounds every payload it hands over (`MAX_TEXT_CHARS`,
`max_images_for`): no image at all on a `failed` result, one on a FAIL verdict.

## 6. Judging (protocol v2)

`VlmJudge(rubric, model_id, n_samples)` sends **montages, not loose views**: ≤ 5
labelled 2×2 montages (shaded / geometry-only clay-or-normals / poses) + ≤ 2 detail
crops at ≤ 1024 px, shuffled per sample — sized for the 14-view object rig + the
clay montage (D47; was ≤ 3 under the 8-view rig).  The wire schema is **observe-then-score**
(summary, strengths, issues, **defect checklist**, acceptance *before* criteria) —
criteria-first measurably compressed flash to 0.6–0.7.  Every rubric carries binary
`defects` (id/text/penalty/cap); defect and acceptance votes are majority (an exact
tie — even `n_samples` only — reads as absent for a defect and follows the
representative sample for an acceptance item, D36 as amended 2026-08-30) and
`overall = caps(weighted_mean − Σ penalties)`.  On the static track the judge's structure
facts come from the connectivity gate's **contact ledger** (2026-08-30): `gates_section`
renders one measured overlap line instead of the per-pair "interpenetrate by ≈d mm"
WARN prose (1 254 such sentences on 241 stored rounds → 0), a MEASURED STRUCTURE block
(parts / contacts / floating / deepest overlap, the plan's PLANNED JOINS as contact or
OPEN with the gap, the lowest point above the floor) and a CONNECTIVITY PASSED paragraph
that also forbids the interpenetration checklist claim.  It is rendered from the stored
`GateReport`, so `3dcode judge <slug>` and calibration see exactly what the in-run judge
saw, and a round recorded before the ledger existed renders byte-identical to before
(`ObjectPipeline.judge_context` stays empty on purpose — one source).  On an
object-track round whose connectivity gate carries an ERROR, the payload
conditionally grows **cross-section slices** (D48, `Settings.judge.slices =
"on-error"`): `spatial.sections.judge_slices` cuts the round's GLB
(`JudgeInput.glb_path`) on the two vertical centre planes — per-part fills with a
legend, red hatch ONLY on the gate's ERROR penetration pairs, plain darkened blends
for every other in-plane overlap, degenerate slices dropped — and the ≤ 2 PNGs are
appended AFTER the montages and crops with a factual rig-text block (an in-plane gap
is not evidence of disconnection) plus one provenance-elicitation sentence in the
defect-checklist bullet ("say where it is visible … or state that it rests on the
measured text alone").  A clean round's payload is **byte-identical** to the
unconditional one (test-pinned; `judge_prompt_hash` unchanged), the render is local
CPU, and the typed `SliceManifest` beside the PNGs keeps every "visible in slice n"
citation auditable.  Floors,
deterministic caps from gate findings (`data["kind"]`), console errors, missing
must-acceptance and
`missing_views` rules apply on top; degraded verdicts are glitches, not scores.
`PairwiseJudge` (position-swapped, tie on disagreement; only `3dcode pick --by pairwise` asks it); `ReferenceJudge` for
image-conditioned specs.
Rubrics (pass threshold 0.70 unless named): `static_object_v1` (0.72), `articulated_v1`
(requires pose views: the `missing_pose_sheet` cap), `scene_v1` (frame-gate caps
dark/blown/flat/content_small), `asset_v1`, `reference_v1`, `shader_v2` (graphics, `gl_frames`
caps; also the `LikenessJudge` default); `shader_v1` stays for calibration replays only.

**Calibration** (`addons/calibration.py`: re-judges recorded rounds, n samples each) is why the
default judge is `gemini-3.1-pro-preview` (`Settings.default_judge`) and flash is the cheap
option that needs `n_samples ≥ 2` for decisions; the numbers are in `eval/docs/EVAL.md` §6.

## 7. Run pipeline (all tracks)

A run is three parts, and only the first is a graph.

**7.1 The pre-round stages — declared, one tuple per track.**  `BaseTrack.stages` (the tracks'
`stages` class attribute) is a tuple of `StageNode`s, walked by `lifecycle.run_stages` over
`StageRunner`.  The block below is drawn from those declarations (`tests/install/test_docs.py`
fails when it drifts, printing the block to paste):

```stage-graph
static_object       plan → skeleton → materialize* → round 0
articulated_object  plan → skeleton → materialize* → round 0
scene               plan → skeleton → materialize* → textures? → (assets ∥ env ∥ layouts?) → asset_api* → zones → clock* → assemble → round 0
graphics            plan → skeleton → recipes* → materialize* → round 0
```

`→` in order; `(a ∥ b)` siblings side by side, each its own cached stage, timed as ONE step of the
run's minutes, a failing sibling raised after every sibling has finished and cached; `?` runs under a
`Settings` switch (`textures`: `scene_textures`, off by default; `layouts`: `zone_layouts`, on); `*`
is not cached — it runs every session.  Every other stage is cached under its key
(`stages/<name>.json`): what its result depends on, named in the declaration.  **Once rounds exist**
a recorded stage is served whatever its key (`stage.frozen`), and the skeleton (`seeds_src`) never
runs at all; `3dcode resume --force` is the one way to regenerate a stage under them: it archives
the round journal + `record.json` to `rounds/pre_force/` whether or not the spec changed, and the run
starts again at round 0 with no stage frozen.  The plan itself is `run()`'s, before the graph (`plan_stage_key`).  What the
scene stages do: `assets` — both kinds climb one ladder (single-shot → check → one repair → agent;
blender_glb heroes get a sub-workspace with the static planner's parts + asset_v1 judge + one
re-judged fix pass, undone when worse; a degraded asset verdict leaves score None / judged False,
emits asset.judge_degraded and skips the fix pass); `env` — `src/env.js`; `layouts` — L2 zone
layouts (planner calls, never fatal); `asset_api` — what the zones are told about the assets;
`zones` — ONE session owns every zone file (D70; a session whose charge crossed the ceiling after
it finished records the zones it wrote as written, the `BudgetExceeded` as their note); `clock` — the
run stops here when the hard budget is spent, after the zones are recorded; `assemble` — `scene.js`, deterministic.

**7.2 The round — `steps._run_round`, a fixed chain in code.**  Not nodes: nothing in a round can
be cached (every step reads the git tree the step before it wrote), and articulated's axis repair
ties gates to render.  **7.3 The round loop — `BaseTrack._round_loop`, a state machine in code**:
each round's tasks come from the previous round's verdict, with seven exits and two retry-once sets.
Best-of-N, refine planning and the asset ladder are sized at run time; they stay code too.

```
round 0 "baseline": generate → build_with_repair → measure → gates → render → post-render gates → judge
   (object tracks, ≥ 8 plan parts, a language with one file per part, an agent backend: the baseline FANS OUT
    per part — phase 0 = one scoped session per attachment subtree (its parts + the planned boxes of the
    neighbours it must weld to + the shared detail advice, its own files only), phase 1 = ONE "assemble"
    session that owns the entry file, placement and the connectivity/contract gates.  $C3D_SCOPED_PARTS=off
    restores the single whole-object session; single-shot always uses it.)
   (--candidates N: N parallel baselines in <ws>/_cand/c<k>, quick 4-view judge, crashed candidate retried once,
    selection build_ok → quick score → fewer gate errors → the earlier candidate; winner copied back, normal r00 follows)
then max_rounds refine rounds, EACH built on the round before it — FIXED rounds (owner, 2026-09-22): no pass,
plateau, regression or diminishing-returns stop, no rewrite or surface-detail round, no refine-from-best:
   refine tasks = the previous round's gate ERRORS (fix hints, authoring-frame numbers) ∪ failed must-acceptance
   ∪ its judge improvement plan
   (≤ 6 tasks, ≤ 6 compacted instruction lines each; reference runs add an IoU task when silhouette IoU < 0.6)
   fan out when ≥ 2 file-disjoint groups AND every task maps to files (threejs/blender parts, scene zones/assets/env)
   generate (no HARNESS turn cap by default — claude-code runs under AgentJob.max_turns=60 (+6-turn wrap-up),
             the other vendor CLIs have no turn cap at all; 28 was A/B'd and rejected, +$0.02/−0.21 score, docs/COST.md §17;
             a cap the machine sets (C3D_AGENT_MAX_TURNS / limits.agent_max_turns; no profile sets one) still buys a wrap-up session
             that lands a final build + summary instead of being killed) → build+repair (error-focused,
             escalates on identical signatures) → gates → … → judge (SKIPPED only where the verdict is never
             bought at all: no judge or no renders — a round that finishes past the clock is still judged)
   → commit src/ (the round's commit) and copy its build to artifacts/rNN/
   a round left without a verdict (judge outage / degraded) is re-judged once before the next is planned,
   and so is the LAST round before the run ends (still none → judge_unavailable, `--rounds 0` included)
   a round's cost is the ledger rows it booked (a cost.tally) and its steps are timed (RoundRecord.steps); a round
   that raises mid-way still reports what it burned (rounds/aborted_rNN.json + record.extra["aborted_rounds"])
   a round whose every task failed raises RoundFailed, TYPED (2026-09-22 — the loop never reads its message):
   .quota (a backend saw the vendor's usage limit) → agent_quota; .transient (a session died of a provider
   failure — AgentResult.transient, or a ModelError outage) → the SAME round re-runs once, baseline included;
   neither → no_change (a refine round) / the run fails (the baseline)
stop reasons: max_rounds | budget (the wall clock — BudgetGuard / max_minutes) | agent_quota | no_change (a refine
              round's sessions changed nothing) | no_refine_tasks | judge_unavailable | failed
              (a record from before 2026-09-22 may say passed / plateau: it loads as `stopped`)
finalise: the workspace ENDS AT THE LAST ROUND — src/ a cut round left dirty is put back and that round rebuilt;
          finalize_record → record.json.  No best round, no deliverable/, no texture pass in the run.
after the run (`3dcode make`, unless --no-pick): addons/select.pick(by="score") — highest effective score, ties →
          fewer gate errors → the earlier round — then package(): deliverable/ + selection.json (+ --texture)
```
Track-specific gates: static `connectivity` + `contract` (+ `reference_silhouette`),
articulated + `joint_sweep` + `motion_direction` (URDF axis vs plan motion text),
scene `scene_placement` (`spatial/scene_placement.py`, the one gate `ScenePipeline.gates` adds: per placed
asset, foot-column gap to the surface beneath, burial depth,
water, contacts and 3-D interpenetration pairs from `runtime_js/lib/host_placement.mjs` — the
first deterministic placement check on the track; before 2026-08-26 the scene_v1
`floating_part` cap could never fire and floating/sunken was left to the VLM) +
`render_console` + `scene_frames` (`spatial/frame_metrics.py`, wired in
`ScenePipeline.post_render_gates`: dark/blown/flat frames, camera in geometry, content
too small, and **`no_motion`** — the frames of one camera at the first and last animation
time are diffed in `spatial/frame_motion.py` and the per-camera "% of pixels changed"
goes to the judge as a fact, because two tiles in different montage images are not
comparable by eye and every scene judged before it was told "nothing moves" while its
water was rippling, and **`unused_glb_asset`** — the scene host wraps `loaders.gltf` and
records each GLB's geometry UUIDs, so the census can say whether any of it reached the
rendered frame; `Object3D.clone()` shares geometry, so a cloned hero still counts and only
a discarded load does not), graphics `gl_frames` (NaN/black/blown/static/flicker).
A build's OWN gate reports ride `BuildResult.gates` — scene `scene_probe` + `shader_preflight`
(one browser boot), graphics `gl_frames` — and `steps._run_round` appends them to the round's
gates after the track's (a failed build's too: a shader compile error is what routes the
shader-traps skill); before 2026-09-22 the scene's two never reached a round, and an always-empty
`scene_census` gate parsed census keys the probe had stopped writing.  `scene_probe` also holds the scene draw budget
(D99): the census's `totals.draws` WARNs past `conventions.DRAWS_WARN_SCENE` and fails the build past
`MAX_DRAWS_SCENE`, so the round's repair session gets it; no gate or judge reads fps.

## 8. Texturing (derived asset pack)

`texturing.run.texture_pass(ws, spec, plan, model_id=…)` — never inside a run: `3dcode pick
--texture` (and `make --texture`, which picks after the run) texture the PICKED round's kept
`artifacts/rNN/object.glb`, and so does a standalone `3dcode texture pass` (the round
`addons.select` reports as picked):
one VLM **material plan** (parts → shared texture ids, family, projection,
`tile_size_m`; cached) → text-to-image tiles (`GeminiImageModel`,
gemini-3.1-flash-image; mirror cross-fade makes them tileable, `seam_score ≤ 0.08`
gate) → **world-metre UV unwrap** (planar/box/cylinder per part; texel density
identical across parts) → `artifacts/object_textured.glb` → before/after judge gate
(**ship iff** Δoverall ≥ −0.01 AND the materials criterion improved).  Results land
in `record.extra["texturing"]` + `events texture.*`; `object.glb` and `src/` are
untouched.  Scenes get `scene_texture_pack` instead: 6–12 named tiles +
`public/textures/manifest.json`, injected into zone prompts as an "Available
textures" block.  Chair live check: plan $0.005 + 1 image $0.067 + gate $0.017,
judge 0.686 → 0.701, materials 0.60 → 0.69, shipped.

## 9. What a run costs and how long it takes

Measured, not restated here: `docs/COST.md` is the home of every money figure (§15: one live
run per profile; §28: where the time goes), and RUNBOOK §2 gives the operator's expected cost
and time per track.  (This section used to hold a 2026-08-23 table of wave-1 runs, made with
the since-deleted in-process `api-agent` and the since-removed pass / plateau stops.)

## 10. Known limits

* Judge: flash is lenient/noisy on fine distinctions (overall std ≈ 0.08–0.12 at
  n=3); its dynamic range comes from the defect checklist.  Use pro (default) or
  `n_samples ≥ 3` for decisions.  Detail crops are position-heuristic (no 2D part
  boxes yet).
* Scene judging is deliberately narrow: the judge sees the scene's OWN cameras (first,
  and the detail crop is taken from one of them) plus at most three overview-rig tiles;
  the harness's eye-level rig is a diagnostic only.  A `must` acceptance item the harness
  cannot verify caps a round at 0.60 AND fails its verdict, so on the scene track only the spec's
  `must_have` list keeps that priority (the plan's own checklist is `should`).
* Articulated: candidate selection uses the quick 4-view rig plus the sweep's pose views;
  mimic joints are honoured (the sweep drives independent joints only and resolves
  followers through the chain); sweep is O(links² × poses).
* Scenes: fps is a record only (machine- and load-dependent; the draw count is what is gated);
  camera-in-geometry can miss open-back enclosures.
* threejs: textures are stripped on GLB export (the texture pass re-adds them as a
  derived pack); `userData.tick` cannot survive export.
* agy exposes no per-workspace MCP, cost or served model; a killed agy session books no
  usage (its tokens are in the envelope only).  A killed codex session books an ESTIMATE
  (usage is per turn and `--ephemeral` leaves no rollout; `CodexEvents.estimated_usage` prices
  the stream it left, ledger `source="estimate"`, $ within 0.62–1.34× of actual on 356 recorded
  sessions); a killed claude-code session books its
  per-message usage, whose output side is a floor.  A killed or given-up gemini-cli
  session books its chat record (exact).
* Vendor sessions see only the routed skill bundles, except agy (5 built-ins; no
  per-session switch) and any user-level skill root codex or gemini-cli might grow.
* Anthropic / OpenAI backends are mock-tested only (no keys on this box).
* Budget checks run between steps: a round the clock cuts mid-way is rolled back (its
  spend stays in `aborted_rounds`) — give scenes `--max-minutes 60`.
* Gemini flash 503 storms happen; dead keys and 429s rotate freely now, but a
  sustained outage can still fail a round (`3dcode resume` re-uses cached stages).

## 11. Flywheel

`record.json` per run + git history; `3dcode flywheel export` writes sample folders
(`<out>/<track>/<language>/<slug>/{code.<ext>, src/**, robot.urdf, meta.json,
captions.json, renders/}`) of the PICKED round (`addons/select`; the last built round
when none was judged) with **quality tiers** from that round's verdict (A passed & 0 gate
errors, B passed, C score ≥ 0.6, D else), acceptance checklists, gate summaries and
`(code fingerprint, prompt)` dedupe (`--drop-duplicates`; side-car captions via
`--captions-dir`); `metadata.parquet`/`.jsonl` + sqlite index carry tier, gate
errors, cost, fingerprints, `duplicate_of`; `--pack` tars with byte-range locators.
`flywheel pairs` emits preference pairs (round i < j by judge Δ ≥ τ) and round-level
repair pairs (in-session trajectory mining died with the api-agent, 2026-08-28 —
vendor CLIs log raw stdout, not structured tool turns); `flywheel refine` emits the loop's OWN transitions — one row per (round i → round
i+1) where the harness asked for a change, carrying what condemned the round, the
instructions written in response, both code snapshots and whether the score moved
(the row is `addons/dataset/refine.RefineTransition`; INTERFACES has the fields and the
drop reasons).  That is the supervision the harness produces that a
one-shot corpus cannot: what a failing artefact looked like, what was wrong with it
in the harness's own words, and what the fix changed.  `toolkits/llamafactory/`
turns those rows into training files; the harness writes the measurement, not the
trainer's format.  `flywheel caption` adds
{detailed, instruction, factory} captions (image-grounded, brand-free, `--out` for
side-car mode); the gallery is `3dcode gallery build --embed`
(the dataset addon has no renderer of its own).

`codeverse3d/addons/gallery/` is the **local** answer to the same question: `3dcode gallery
serve` indexes `runs/` + every `eval/bench/out/*/runs`, serves the page **and the run
directories** on 127.0.0.1 (so every link opens: sheet, renders, `src/`, `object.glb`
in an orbit viewer built on the vendored three.js, `record.json`), and re-reads a
run's record per request so a battery that is still writing shows up live (how to use it:
RUNBOOK §4).  Two gates keep it safe: a URL can only name a `(battery, slug)` the scanner found under
a declared root, and `urls.safe_join` refuses anything that escapes that run
directory.  `3dcode gallery build [--embed]` writes the same page as one file.
