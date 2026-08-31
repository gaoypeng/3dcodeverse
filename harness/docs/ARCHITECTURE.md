# 3dcodeverse harness — architecture

Package `codeverse`, CLI `3dcodeverse` (short alias `3dcv`), repo location
`/home/yipeng/3dcodeverse/harness`.  Backend only.  Python 3.13 (one fixed version), a small Node
runtime (`runtime_js/`) for everything Three.js / headless Chrome, and moderngl
for the graphics track.  Reconciled against the code and the live runs on
2026-08-23 (waves 2–3 + fix batch 1); design history and deviations are in
`docs/DECISIONS.md`, how-to in `docs/RUNBOOK.md`, evaluation protocol in
`docs/EVAL.md`, complexity measurement + the score-vs-complexity study in
`docs/COMPLEXITY.md`, binding signatures in `docs/INTERFACES.md`.

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
(optionally best-of-N candidates) → refine rounds → finalise → record**, with an
optional post-hoc **texture pass**.  The record (plus git history of `src/`) is
the flywheel unit.

## 1. Design laws (from the reference post-mortems)

1. **Code is truth; artifacts are derived.**  Raw code in `src/` is the
   deliverable; GLB/renders are regenerated from it (finalise restores the best
   commit and rebuilds).  Textures are a *derived asset pack*
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
9. **Cost is a budget, not a log.**  Hard per-run ceilings (`BudgetGuard`), key
   pools with per-key limiters, every call yields a `Usage`.
10. **Reproducible.**  Prompt/rubric/cookbook hashes and tool versions are
    recorded on every run (`record.prompt_hashes`, `record.environment`).
11. **Export as authored.**  No wrapper re-centres, rescales or grounds the
    object; builds warn, `check_contract`/`check_connectivity` gate, the agent
    fixes the source.

## 2. Package map (as built)

```
codeverse/
  conventions.py      frames (LANGUAGE_FRAME, GLB_FRAME), units, OBJECT_VIEWS/_QUICK/_CLAY_VIEWS, SCENE_VIEWS,
                      to_snake/to_pascal/slugify, MAX_TRIS_*, BBOX_TOLERANCE_M, CONTACT_GAP_M  (THE source)
  config.py           Settings (CV3D_* env, ~/.config/codeverse/config.yaml; role defaults come from
                      contracts Backends; Settings.backends(**overrides) builds a Spec's Backends;
                      default_candidates=1)
  contracts/          pydantic: common (Track, Language, Usage, Budget, Backends, TRACK_INFO registry,
                      ENTRY_FILE/code_file/LANGUAGE_LABEL tables), spec (+ RunOptions), plan, artifacts
                      (GateFinding.as_line, RenderView.judge, RenderSet.out_dir, + Judgment), run
                      (+ SkillsUsage/SkillRead: what was attached, what was read), chat,
                      agent (typed AgentJob)
  workspace.py        run-dir layout + git snapshots
  doctor.py           the environment checks behind `3dcv doctor` (deps, Blender,
                      node, GPU probe, keys, pool admission, vendor CLIs, MCP, skills)
  proc.py             stdlib-only subprocess + atomic-JSON primitives (ManagedProcess owns every
                      child's lifecycle: group kill on ANY exception, bounded pumps, stdin writer;
                      run_subprocess, tail, write_json_atomic, scrub_secrets) and the tolerant readers/writer
                      (read_json_or_none, iter_jsonl_lines, read_jsonl_lenient, append_jsonl_line) —
                      shared by languages/spatial/cli/cost/flywheel/gallery/bench.  RULE: any
                      stdlib-only file / JSON / JSONL helper lives HERE; grep proc.py before writing a
                      try/except read (the 2026-08-26 review found the same tolerant read written
                      eight times because this module had not grown it).  Also home, since
                      2026-08-28, to the JSONL event log (EventLog), the run lock (ONE writer per
                      run dir: an fcntl.flock at <runs>/.locks/<slug>.lock whose record NAMES the
                      holder, printed by `3dcv status`) and the bounded parallel fan-out
                      (fan_out); sha256_file and version_line live here too — still a leaf:
                      imports nothing from codeverse
  models/             ChatModel (base.py), parts.py; gemini.py (the whole Gemini stack:
                      request/response shapes, dead-key + free 429 rotation, the image model);
                      anthropic.py openai.py (each with its own request/response shapes);
                      retry.py (the scheduling machine: KeyPool with outcomes ok | 429 | 5xx |
                      error | dead | skip + TPM reservation/reconcile, StormGate — ships OFF,
                      docs/COST.md §21 — with_retries / rotate_with_retries, both bounded by
                      max_total_s ≤ RETRY_DEADLINE_S with MAX_WAIT_S ≤ 3 s single waits, and the
                      prompt-token estimate); pricing.py (version-suffix-only fallback), health.py (preflight
                      probe: no retries, no backoff), schema_utils.py (strict schema), registry.py
  agents/             registry.py (the CodingAgent protocol + dispatch — every backend is a
                      vendor CLI; the in-process api-agent died 2026-08-28), backends.py
                      (gemini-cli / claude-code / codex / antigravity), cli_common.py (sessions,
                      the watchdog clocks, the transcript, retry trajectory naming,
                      files_changed attribution), materialize.py
  languages/          LanguageRuntime (base.py); one merged module per language since 2026-08-28 —
                      blender/ cadquery/ threejs/ urdf/ scene_threejs/ glsl_shader/ opengl_python/ are each
                      a single __init__.py (lint → skeleton → runtime, in dependency order) beside their
                      data (wrappers/, starter/ — Path(__file__) assets unchanged; the contract text
                      is prompts/<lang>/contract.md, read through RuntimeDocs)
  spatial/            node.py, render.py, observe.py, tool_common.py (shared tool plumbing),
                      render_scene.py (judge view subset, content-fitted orbit),
                      frame_metrics.py (scene_frames gate), frame_motion.py (measured inter-frame motion),
                      scene_placement.py (scene_placement gate + check_placement tool: floating / sunken /
                      unsupported / interpenetration per placed asset from the probe census's placement
                      table, runtime_js/lib/host_placement.mjs; added 2026-08-26),
                      gl_render.py (GlHost), frame_stats.py (gl_frames),
                      sheet.py (montage_2x2, crop_region), measure.py, connectivity.py,
                      contract.py (authoring-frame hints), sections.py, silhouette.py, probes.py,
                      complexity.py (objective complexity vector -> Measurement.extra, docs/COMPLEXITY.md),
                      joints*.py + joints_collide.py (deterministic penetration), registry.py,
                      tools.py (every @tool registration since 2026-08-28, the joint_sweep body included;
                      spatial siblings are plain imports — lazy() guards only codeverse.languages /
                      codeverse.texturing and tool_common's node renderer), mcp_server.py (MCP name: 3dcv).
                      Render modes are contracts.artifacts.RENDER_MODES (shaded wire normals silhouette
                      clay — no 'depth'); build error_type spellings are languages/_common.MISSING_ENTRY
                      ("MissingEntryFile") and BUILD_TIMEOUT ("BuildTimeout") for every runtime
  skills/             registry.py (typed ROUTES + the router that evaluates them),
                      model.py (Skill/Selection + the SKILL.md loader), prompting.py
                      (per-backend delivery policy + the index/mandate text),
                      targets.py (measured targets + claims), materialize.py,
                      telemetry.py (the read probe), config.py (call-time switches)
  cost/               types.py (CallCost/Stage/Role) ledger.py (append-only telemetry/cost.jsonl + price provenance)
                      context.py (per-call > ambient attribution) instrument.py (MeteredChatModel /
                      MeteredAgent — one row per ChatModel.generate; one session row only for a backend
                      that does NOT meter itself; run_ledger nests + is context-local so bench --parallel works)
                      profiles.py (economy|balanced|quality; cli._common.resolve_dial is THE resolver)
                      caching.py (session_cache/session_key — measurement only, docs/COST.md §13)
                      billing.py (SUBSCRIPTION_BACKENDS/bills_usd — which backends take real dollars,
                      so the ledger bills real money and not list price; docs/COST.md §25)
                      guard.py routing.py reconstruct.py (old runs) audit.py report.py
  judges/             base.py (JudgeInput/Judgment helpers + the pure round-replay pieces `3dcv judge` and
                      calibration share), rubrics.py + rubrics/*.yaml (defect checklists, the wire
                      schema, caps and scoring), prompt_builder.py (image prep, montages, the
                      judge messages), vlm_judge.py (+ the reference/likeness judges),
                      pairwise.py (compare_many), calibration.py.  No Judge Protocol: a judge is
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
  orchestrator.py     the round loop's LIBRARY, not the loop: round POLICIES (RoundPolicy /
                      StopPolicy / BestSelector), refine-task compilation + grouping,
                      StageRunner + RunState (resume), BudgetGuard.  The loop itself is
                      tracks/lifecycle.py:_round_loop → tracks/steps.py:run_round.  A best-of-N
                      candidate IS steps._run_round(kind='candidate') in a _cand/c<k> sub-workspace
                      with two knobs (render=candidates.quick_render, geometry_views=False), its
                      own _cand/c<k>/events.jsonl and a one-sample judge
  tracks/             __init__.py (get_track(track, **options) + the TrackPipeline protocol),
                      lifecycle.py, steps.py, candidates.py (best-of-N + the pure candidate/pairwise
                      decision logic), generation.py (agent + single-shot strategies + the file
                      envelope), repair.py, planner.py, prompting.py (prompt helpers, split from common),
                      common.py (RunContext, Services), static_object.py (+ the detail round and the
                      reference-image gates), articulated_object.py (+ the planned-motion gate),
                      scene.py, scene_assets.py (+ cheap single-shot asset generation),
                      zone_layout.py (L2 zone director: per-zone structured layout calls + deterministic validator),
                      graphics.py (the whole graphics track: planner hooks, prompt context, frame
                      RenderSet, and recipe seeding into the harness-owned, read-only
                      src/recipes.glsl — measured: flash calls a recipe on disk, not one it is
                      shown; AgentJob.read_only, CV3D_SEED_RECIPES),
                      planner.py (the ONE planner loop + the cached EngineeringBrief
                      (CV3D_PLAN_BRIEF), plan budgets and the worked examples),
                      plan_features.py (CV3D_PLAN_FEATURES: one switch per plan-loop change, so each
                      can be A/B'd alone, + pin_plan_blockers() deciding when two arms may share
                      one plan — docs/EVAL.md §8.1),
                      depth.py,
                      skills_hook.py (the round's view of codeverse/skills: attach before generating,
                      probe reads after — a no-op unless CV3D_SKILLS is on)
  flywheel/           record.py (+ best_round_record), export.py, pack.py, sample.py, pairs.py,
                      deliverable.py, telemetry.py, captions.py, quality.py (tiers + dedupe + code/mesh fingerprints), index.py,
                      code_quality.py (the delivered CODE's own vector — magic numbers per 100 LOC,
                      function length, dead functions, duplication, docstrings → record.extra
                      ["code_quality"].index, a flywheel filter beside score and complexity)
  gallery/            THE local run gallery (`3dcv gallery serve|build`): cards.py,
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
                      role_{scope,detail,repair}.j2), texturing/*.md, tracks/*.j2.
                      catalog.py answers "what exists and how does each piece reach the model"
                      — including the ONE language-id → prompts/<dir> mapping, which used to be
                      copied four times and missing in a fifth.  sections.py splits that
                      markdown into chapters so a STAGE can name the recipes it needs.
                      The other half of the split: codeverse/skills/ is what an AGENT chooses
                      to read (SKILL.md + references/ + a _claims file pinning its numbers to
                      live constants).  A file that tries to be both is the bug this prevents.
  cli/                main.py (app wiring, make/resume/mcp + the tools/bench/gallery
                      commands), inspect_cmd.py (status/render/judge on one existing run),
                      flywheel_cmd.py, texture_cmd.py, cost_cmd.py (`3dcv cost`), layout_cmd.py, doctor.py
                      (`--skills` checks the library + its discovery wiring),
                      skills_cmd.py (`3dcv skills list|show|validate|report` — the read-rate report)
bench/                run_bench.py, report.py (renders through codeverse/gallery), compare_backends.py
                      (preflights every model it needs; --wait-for-provider / --no-preflight),
                      _infra.py (outage vs model failure: infra_failed / budget_exhausted, docs/EVAL.md §7),
                      pin_plan.py (seed one plan into both arms so the paired delta stops carrying
                      the planner's spread — permitted only by plan_features.pin_plan_blockers),
                      _compare_report.py (arm table incl. the `dropped` / `over budget` loss columns;
                      dedups the append-only rows per (prompt, arm) so every reader agrees),
                      _jsonl.py (the ONE tolerant reader/append-sealer for the resumable
                      *.jsonl journals — a truncated last line never costs the paid rows),
                      ab_plan.py (paired control/variant A/B for plan + brief switches, --aa
                      calibration mode; pins both children to the cap the §23 admission check
                      reserved), _ab_report.py,
                      ab_gate_rates.py (the same run's DETERMINISTIC readouts, paired per
                      prompt: penetrating pairs, worst depth, floating parts, contract findings),
                      _oneshot.py, _fixed_eval.py, cost_report.py,
                      concurrency_probe.py (in-flight knee sweep), complexity_report.py,
                      prompts/{static_objects_v1 (24), articulated_v1 (12), scenes_v1 (12), compare_v1 (8)}.yaml
runtime_js/           export_glb.mjs (placement policy, instance baking, selfcheck) render_glb.mjs
                      render_scene.mjs probe_scene.mjs check_shaders.mjs gpu_launch.cjs serve.cjs
                      lib/{resolve_three, scene_host, host_coverage, host_census, host_placement, orbit, instances,
                      census, glsl_audit, browser/…}
tests/                agents bench_prompts blender_cadquery compare_bench core cost flywheel_cli gallery graphics
                      install judges languages models orchestrator_tracks prompts reference scene_gates
                      scene_prompts scene_runtime skills spatial_tools texturing threejs_render urdf_joints
                      (24 dirs; 2 077 offline, 1 950 of them pure python)
```

## 3. Workspace layout (one run, as observed)

```
runs/<slug>/
  spec.json  plan.json  run_state.json  record.json  events.jsonl
  src/            agent-authored RAW code (git repo; commits: spec, skeleton, pre:/agent:<label>, rNN <kind>)
  public/         (scene) compiled assets public/assets/<snake>.glb; (textured scenes) public/textures/*.png + manifest.json
  _assets/<snake>/  (scene) sub-workspaces for blender_glb assets (gitignored)
  _cand/c<k>/     (--candidates N) throw-away best-of-N sub-workspaces (gitignored, kept for the flywheel):
                  each is a round of kind "candidate" — its own events.jsonl, rounds/r00.json, gates/,
                  judge/r00.json (one-sample judge on quick_render views)
  stages/<name>.json   rounds/rNN.json   rounds/candidates.json   rounds/aborted_rNN.json (a round the
                       budget/a crash cut: what it burned, never resumed from)
  telemetry/cost.jsonl   live ledger: one row per metered call / CLI session, opened by BaseTrack.run
                         (cost_ledger.jsonl at the root is a relative symlink to it, kept for the run-layout
                         alias; runs before 2026-08-23 have the root file only)
  run_state.json  stages + rounds done; extra carries budget_snapshot and spec_fingerprint only
  artifacts/      object.glb object.stl|step robot.urdf meshes/ articulation.json build.json census.json
                  measurement.json … ; graphics: frames/fNN_tT.png frames_sheet.png preview.gif metrics.json
                  texturing: object_textured.glb textures/{<id>.png, texture_plan.json, texturing.json, gate/}
    renders/rNN/  view_<name>.png sheet.png views.json (judge flags) (+ poses/ articulated; <cam>_t<t>.png metrics.json scenes)
    gates/rNN/    lint_<lang>.json connectivity.json contract.json joint_sweep.json motion_direction.json … (+ *_tool.json)
    judge/rNN.json (+ rNN_cli.json from `3dcv judge`)
    tool_renders/rNN_<hash>/
  trajectories/<label>_rNN/  prompt.md transcript.jsonl stdout.json stderr.log result.json
                             (a retried label lands in <label>.a2_rNN — first attempt preserved)
  AGENTS.md GEMINI.md CLAUDE.md .gemini/settings.json .3dcv/cookbook.md .geminiignore .aiexclude
```

## 4. Per-language authoring contracts (raw code; the harness owns export)

Authoritative text lives in `codeverse/prompts/<lang>/contract.md` (+ cookbook);
summary:

* **blender** (multi-file): `src/model.py` entry + `src/parts/<snake>.py` each
  defining `def build_<snake>()` (self-contained; optional `src/parts/_x.py`
  helpers) — pure bpy, Z-up, -Y front, meters; PascalCase object names = part
  names.  Small objects may stay single-file.  Harness wrapper (`run_bpy.py` +
  `_census.py`) puts `src/` on sys.path, maps errors to workspace-relative
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
  Export is **as authored** (no re-centring; `--normalise` is a dataset-only flag);
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
  frames), sweeps poses for collisions (deterministic; python-fcl), builds
  hierarchical `object.glb`, and gates motion direction against the plan text.
* **scene_threejs**: `src/scene.js` `createScene({THREE, renderer, loaders}) → {scene, cameras, update(t,dt)}`;
  `src/env.js`, `src/zones/*.js`, `src/assets/*.js`, `src/shaders/*.js`; GLBs at
  `public/assets/<name>.glb`.  The harness assembles `scene.js` deterministically.
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
server (name `3dcv`) for the vendor CLIs, (c) a native tool schema for any embedder
(`ToolDef.schema()`), (d) a prompt card.  The 19 tools: `build`, `measure`,
`render_views`, `render_sheet`, `isolate`, `cross_section`, `check_connectivity`,
`check_contract`, `check_placement`, `compare_silhouette`, `compare_reference`,
`joint_sweep` (articulated), `shader_probe`, `scene_probe`, `scene_views` (scene),
`gl_probe`, `gl_frames` (graphics), `texture_pass`, `texture_preview` (object tracks).

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
`GateReport`, so `3dcv judge <slug>` and calibration see exactly what the in-run judge
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
`PairwiseJudge` (position-swapped, tie on disagreement) also ranks N candidates via
`compare_many`; `ReferenceJudge` for image-conditioned specs.
Rubrics: `static_object_v1` (0.72), `articulated_v1` (requires pose sheet),
`scene_v1` (frame-gate caps), `asset_v1`, `reference_v1`, `shader_v1` (0.70).

**Calibration (2026-08-23, `judges/calibration.py`, n=3 on the e2e rounds):**
flash `gemini-3.7-flash` mean overall std 0.083, pearson(gate errors, score) −0.33;
pro `gemini-3.1-pro-preview` std 0.030, pearson +0.63, and separation on a
crafted-vs-crude-vs-wrong triplet 0.60 / 0.00 / 0.25 (flash 0.91 / 0.00 / 0.20 —
its range is carried by the defect checklist).  Hence **default judge =
`gemini-3.1-pro-preview`** (`Settings.default_judge`); flash stays the cheap
in-loop option with `n_samples ≥ 2` for decisions.

## 7. Round loop (all tracks)

```
plan (structured output, one re-ask) → skeleton (buildable placeholder) → materialise workspace
[scene only] assets (parallel; blender_glb assets get a sub-workspace + asset_v1 judge + one fix pass; a degraded asset verdict leaves
            score None / judged False, emits asset.judge_degraded and skips the fix pass)
             → env → zones (parallel) → assemble (deterministic scene.js)
round 0 "baseline": generate → build_with_repair → measure → gates → render → post-render gates → judge
   (object tracks, ≥ 8 plan parts, a language with one file per part, an agent backend: the baseline FANS OUT
    per part — phase 0 = one scoped session per attachment subtree (its parts + the planned boxes of the
    neighbours it must weld to + the shared detail budget, its own files only), phase 1 = ONE "assemble"
    session that owns the entry file, placement and the connectivity/contract gates.  $CV3D_SCOPED_PARTS=off
    restores the single whole-object session; single-shot always uses it.)
   (--candidates N: N parallel baselines in <ws>/_cand/c<k>, quick 4-view judge, crashed candidate retried once,
    selection build_ok → quick score → fewer gate errors with pairwise tie-break; winner copied back, normal r00 follows)
repeat while StopPolicy says continue (≤ max_rounds refine rounds, plateau_window=2 / min_delta=0.02,
                                       target = rubric threshold, budget ok):
   money stops, sized by the judge's MEASURED noise σ (cost/routing.JUDGE_NOISE: pro 0.030, flash 0.083):
     regression — last round scored < best − 1σ ⇒ never another round of the same shape: the first one
                  switches strategy (ONE whole-artifact rewrite, kind "rewrite"), a second stops the run
     diminishing_returns — from r03 on, only start when the last gain > 1.5σ AND best < target
   refine tasks = gate ERRORS (fix hints, authoring-frame numbers) ∪ failed must-acceptance ∪ judge improvement plan
   (≤ 6 tasks, ≤ 6 compacted instruction lines each; reference runs add an IoU task when silhouette IoU < 0.6)
   fan out when ≥ 2 file-disjoint groups AND every task maps to files (threejs/blender parts, scene zones/assets/env)
   generate (no HARNESS turn cap by default — claude-code runs under AgentJob.max_turns=60 (+6-turn wrap-up),
             the other vendor CLIs have no turn cap at all; 28 was A/B'd and rejected, +$0.02/−0.21 score, docs/COST.md §17;
             a cap a caller sets (CV3D_AGENT_MAX_TURNS / task; no profile sets one) still buys a wrap-up session
             that lands a final build + summary instead of being killed) → build+repair (error-focused,
             escalates on identical signatures) → gates → … → judge (SKIPPED only where the verdict is never
             bought at all: no judge/renders, budget already exceeded, or judge_on_gate_errors=False)
   BestSelector: highest score, tie → fewer gate errors; |Δ| < pairwise_margin (0.03) → position-swapped
   PairwiseJudge decides (replace only at confidence ≥ 0.6; note persisted in rNN.json)
   every round emits cost.round {stage → $, judge $, agent turns, wasted flag}; a round that raises mid-way
   still reports what it burned (rounds/aborted_rNN.json + record.extra["aborted_rounds"])
   a plateau / diminishing_returns stop on a CLEAN artifact (built, judged, no gate ERROR, score ≥ 0.45 and
   within σ of the best) is converted into ONE round of kind "detail" instead: surface detail only — bevels,
   panel lines, fasteners, wear, material variation — with the silhouette, placement and part list frozen and
   a deterministic `detail_drift` gate that ERRORs if any part box moved > 5 mm.  Measured on 88 refine-round
   pairs: the part count never changed once and mean Δgeometry_detail was +0.003, so detail needed its own
   round; the rounds that added geometry DURING repair lost 0.075 assembly_fit.  $CV3D_DETAIL_ROUNDS=0 is off.
stop reasons: pass | plateau | budget | max_rounds | no_change | no_refine_tasks | regression | diminishing_returns
finalise: restore best commit, rebuild so artifacts match delivered code, finalize_record → record.json
```
Track-specific gates: static `connectivity` + `contract` (+ `reference_silhouette`),
articulated + `joint_sweep` + `motion_direction` (URDF axis vs plan motion text),
scene `scene_placement` (`spatial/scene_placement.py`, appended after the census gate in
`ScenePipeline.gates`: per placed asset, foot-column gap to the surface beneath, burial depth,
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

## 8. Texturing (derived asset pack)

`texturing.run.texture_pass(ws, spec, plan, model_id=…)` — after (or outside) a run:
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

## 9. What actually happens in a run (live runs, 2026-08-23)

Wave-1 e2e runs (planner/judge `gemini-3.7-flash`, n=1, budget $2.5–3 / 45–60 min):

| run | track / language / generator | scores | wall | cost |
|---|---|---|---|---|
| `e2e_chair_blender` | static / blender / api-agent | 0.674 → 0.744 (pass) | 11.8 min | $0.84 |
| `e2e_cabinet_urdf` | articulated / urdf_blender / api-agent | 0.677 → 0.930 (pass) | 12.5 min | $0.84 |
| `e2e_bench_threejs` | static / threejs / gemini-cli | 0.642 → 0.885 (pass) | 35.7 min | $0.68 |
| `e2e_garden_scene` | scene / scene_threejs / api-agent | 0.556 → 0.578 (budget) | 45.3 min | $2.90 |

Wave-2/3 live checks: best-of-2 blender stool (single-shot flash) — candidates ran
in parallel, one crashed on a 503 storm and was retried, scores within the 0.03
margin so pairwise picked the winner at 0.915 confidence; r00 0.563 → r01 0.612,
total $0.24 / 7.6 min.  Graphics `neon_rain` (single-shot flash, glsl_shader):
plan → 328-line shader compiled first try, `gl_frames` clean, judged 0.786 →
stop=pass after round 0, $0.045.  Per-stage timings: plan 7–60 s; Blender build
0.1–0.6 s; GL build + 12 frames ~2–5 s; GPU view-rig render ~1.5 s at 8 views (D47's 14-view rig scales with view count, not re-timed); judge verdict
$0.02–0.03 (flash) / ~$0.2 (pro); api-agent generation 3–6 min per object round.

## 10. Known limits (as of 2026-08-23)

* Judge: flash is lenient/noisy on fine distinctions (overall std ≈ 0.08–0.12 at
  n=3); its dynamic range comes from the defect checklist.  Use pro (default) or
  `n_samples ≥ 3` for decisions.  Detail crops are position-heuristic (no 2D part
  boxes yet).
* Scene judging is deliberately narrow: the judge sees the scene's OWN cameras (first,
  and the detail crop is taken from one of them) plus at most three overview-rig tiles;
  the harness's eye-level rig is a diagnostic only.  A `must` acceptance item the harness
  cannot verify caps a run at 0.60 AND fails it, so on the scene track only the spec's
  `must_have` list keeps that priority (the plan's own checklist is `should`).
* Articulated: candidate selection uses the quick 4-view sheet (not pose views);
  mimic joints ignored; sweep is O(links² × poses).
* Scenes: fps is a relative cost; camera-in-geometry can miss open-back enclosures.
* threejs: textures are stripped on GLB export (the texture pass re-adds them as a
  derived pack); `userData.tick` cannot survive export.
* agy exposes no per-workspace MCP, cost or served model.
* Anthropic / OpenAI backends are mock-tested only (no keys on this box).
* Budget checks run between steps: a refine round that finishes its judge and then
  trips the budget is not promoted to best — give scenes `--max-minutes 60`.
* Gemini flash 503 storms happen; dead keys and 429s rotate freely now, but a
  sustained outage can still fail a round (`3dcv resume` re-uses cached stages).
* A few single-file wrappers were once over the old ~400-line guideline; the rule
  is now a 2 000-line cap (3 000 absolute).

## 11. Flywheel

`record.json` per run + git history; `3dcv flywheel export` writes sample folders
(`<out>/<track>/<language>/<slug>/{code.<ext>, src/**, robot.urdf, meta.json,
captions.json, renders/}`) with **quality tiers** (A passed & 0 gate errors, B
passed, C best ≥ 0.6, D else), acceptance checklists, gate summaries and
`(code fingerprint, prompt)` dedupe (`--drop-duplicates`; side-car captions via
`--captions-dir`); `metadata.parquet`/`.jsonl` + sqlite index carry tier, gate
errors, cost, fingerprints, `duplicate_of`; `--pack` tars with byte-range locators.
`flywheel pairs` emits preference pairs (round i < j by judge Δ ≥ τ) and round-level
repair pairs (in-session trajectory mining died with the api-agent, 2026-08-28 —
vendor CLIs log raw stdout, not structured tool turns); `flywheel caption` adds
{detailed, instruction, factory} captions (image-grounded, brand-free, `--out` for
side-car mode); `flywheel gallery` is an alias of `3dcv gallery build --embed`
(the flywheel package has no renderer of its own).

`codeverse/gallery/` is the **local** answer to the same question: `3dcv gallery
serve` indexes `runs/` + every `bench/out/*/runs`, serves the page **and the run
directories** on 127.0.0.1 (so every link opens: sheet, renders, `src/`, `object.glb`
in an orbit viewer built on the vendored three.js, `record.json`), and re-reads a
run's record per request so a battery that is still writing shows up live.  A run
with no record yet is a *pending* card, a half-written one a *broken* card.  Two
gates keep it safe: a URL can only name a `(battery, slug)` the scanner found under
a declared root, and `paths.safe_join` refuses anything that escapes that run
directory.  `3dcv gallery build [--embed]` writes the same page as one file.
