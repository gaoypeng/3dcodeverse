# 3dcodeverse harness — architecture

Package `codeverse`, CLI `c3v`.  Backend only.  Python 3.11+ and a small Node
runtime (`runtime_js/`) for everything Three.js / headless-Chrome.

## 0. What it is

A data-flywheel harness in which LLMs write **raw executable 3D code** — no SDK
layered on top of the language — for three **tracks**:

| track | languages | deliverable |
|---|---|---|
| `static_object` | `blender` (bpy), `cadquery`, `threejs` | code + canonical `object.glb` (+stl/step) |
| `articulated_object` | `urdf_blender` (bpy link meshes + hand-written URDF) | code + `robot.urdf` + `meshes/*.glb` + `object.glb` (node hierarchy) |
| `scene` | `scene_threejs` (multi-file three.js + GLSL; optional bpy-built GLB assets) | `src/**` + renders + probes |

…driven by **any** of these backends behind two protocols:

* `ChatModel` (API): `gemini:*`, `anthropic:*`, `openai:*` — used by the planner,
  the judge, the captioner, and by the in-process `api-agent`.
* `CodingAgent` (agentic session on a workspace): `gemini-cli:*`, `claude-code:*`,
  `codex:*`, `agy:*` (Antigravity), and `api-agent:<chat-model>` (our own
  tool loop so API-only users get parity).

Every run is: **spec → plan → generate → lint → build → gates → measure →
render → judge → refine… → record**.  The record (plus git history of `src/`)
is the flywheel unit.

## 1. Design laws (from the reference post-mortems)

1. **Code is truth; artifacts are derived.**  Raw code in `src/` is the
   deliverable; GLB/renders are regenerated from it.
2. **Concrete beats abstract.**  Compliance of flash-class models is
   probabilistic and set by concreteness: contracts are delivered as *skeleton
   code, exact numbers, copyable snippets* (cookbooks), never as clauses.
3. **Code answers when code can answer.**  Deterministic gates / measurements
   / normalisers run by the harness (never pasted by the agent) catch the
   residue; the VLM is used only for perception and judgement.
4. **Move responsibility out of agent code** where possible: export wrappers,
   camera rigs, placement validation, URDF composition math, scene assembly.
5. **Evidence-based acceptance.**  Every plan carries an acceptance checklist;
   items are proved by measurements/probes/judge evidence, not by prose.
6. **Typed everything.**  One contracts package; no regex-on-id control flow,
   no stringly-typed dict conventions, no god files (target ≤ 400 lines).
7. **Cheap first.**  Static lint → build → deterministic gates → quick 4-view
   sheet → VLM.  Never call a VLM on something a gate already rejected.
8. **Separate generator from judge.**  The judge sees spec + renders +
   measurements + acceptance list; never the generator's reasoning.
9. **Cost is a budget, not a log.**  Hard per-run ceilings; key pools with
   per-key limiters; every call yields a `Usage`.
10. **Reproducible.**  Prompt/rubric/cookbook hashes and tool versions are
    recorded on every round.

## 2. Package map

```
codeverse/
  conventions.py      frames, units, view presets, name normalisation (THE source)
  config.py           Settings (keys, binaries, dirs, defaults) from env/yaml
  contracts/          pydantic: Spec, Plan(s), BuildResult, Measurement, RenderSet,
                      GateReport, Judgment, RoundRecord/RunRecord, Chat*, Agent*
  workspace.py        run-dir layout + git snapshots
  events.py           JSONL event log per run
  models/             ChatModel protocol; gemini.py anthropic.py openai.py;
                      keypool.py (RPM/TPM buckets, rotate on 429); pricing.py; registry.py
  agents/             CodingAgent protocol; gemini_cli.py claude_code.py codex.py
                      antigravity.py api_agent.py (tool loop); materialize.py
                      (AGENTS.md/GEMINI.md/CLAUDE.md + MCP config per workspace);
                      watchdog.py (activity-aware subprocess); registry.py
  languages/          LanguageRuntime protocol; blender/ cadquery/ threejs/ urdf/
                      scene_threejs/ — each: runtime.py (build+export), lint.py,
                      skeleton.py (starter files from plan), wrappers/ (bpy/node
                      scripts the harness runs — NOT imported by agent code)
  spatial/            language-agnostic tools on GLB/URDF/scene: measure.py,
                      connectivity.py, render.py (three headless GPU), sections.py,
                      silhouette.py, joints.py (FK, pose sweep, collisions),
                      probes.py (scene census/fps/errors), registry.py (@tool →
                      python fn + JSON schema + prompt card), mcp_server.py
  judges/             vlm_judge.py (rubric yaml → Judgment, n-sample, code-computed
                      score, floors), pairwise.py, reference.py, metrics.py, rubrics/
  tracks/             base.py (Track protocol), static_object.py,
                      articulated_object.py, scene.py — each composes stages
  orchestrator/       runner.py (stages+resume), rounds.py (refine loop, stop
                      policy, best selection), fanout.py, budget.py
  flywheel/           record.py, export.py (sample folders + parquet), pairs.py
                      (preference/repair pairs), captions.py, dedupe.py
  prompts/            system/*.md, <language>/cookbook.md + skeletons, tracks/*.j2
  cli/main.py         c3v make | resume | judge | render | tools | flywheel | bench | doctor
runtime_js/           node package: export_glb.mjs, render_glb.mjs, render_scene.mjs,
                      probe_scene.mjs, check_shaders.mjs, gpu_launch.cjs, serve.cjs
```

## 3. Workspace layout (one run)

```
runs/<slug>/
  spec.json  plan.json  run_state.json  record.json  events.jsonl
  src/            agent-authored RAW code (git repo; one commit per round)
  public/         (scene) compiled assets e.g. GLBs built from bpy
  artifacts/      object.glb object.stl robot.urdf meshes/ measurement.json
    renders/rNN/  view_*.png sheet.png turntable.mp4
    gates/rNN/    *.json
    judge/rNN.json
  trajectories/<stage>_rNN/  prompt.md transcript.jsonl result.json
  AGENTS.md GEMINI.md CLAUDE.md .mcp.json .gemini/settings.json  (materialised)
```

## 4. Per-language authoring contracts (raw code; the harness owns export)

* **blender**: `src/model.py` — pure bpy, Z-up, -Y front, meters; builds named
  objects (PascalCase = part names) into the current scene; no camera/lights/
  render/export calls.  Harness: `blender -b --factory-startup --python
  wrappers/run_bpy.py -- --script src/model.py --out artifacts/` → clears scene,
  runs script with RLIMIT, collects census, applies transforms, exports GLB+STL.
* **cadquery**: `src/model.py` — `import cadquery as cq` only; module-level
  `result` = `cq.Assembly` (named parts) or `Workplane`.  Harness exports
  STEP/STL per part → GLB with named nodes.
* **threejs** (static): `src/parts/<snake>.js` each `export function
  build<Pascal>(THREE) → THREE.Group` at world pose (Y-up, +Z front, meters);
  `src/object.js` `export function build(THREE) → THREE.Group` assembling parts;
  optional `userData.tick(dt)`.  Harness exports GLB via node GLTFExporter.
* **urdf_blender**: `src/model.py` (bpy) builds one object per link named
  `<link>`; `src/robot.urdf` written by the agent (native URDF, meshes as
  `meshes/<link>.glb`, joint frames per URDF semantics).  Harness exports per-link
  meshes, lints URDF, composes FK, sweeps poses, builds `object.glb` with nodes.
* **scene_threejs**: `src/scene.js` `export function createScene({THREE, renderer,
  loaders}) → {scene, cameras, update(t,dt)}`; `src/env.js`, `src/zones/*.js`,
  `src/assets/*.js`, `src/shaders/*.js`; Blender-built assets compiled to
  `public/assets/<name>.glb` and loaded via GLTFLoader; GLSL inline via
  ShaderMaterial / onBeforeCompile using the cookbook's copyable boilerplate.

## 5. Spatial tool registry

`@tool` registers a python function with a pydantic args model → (a) direct
call from tracks, (b) MCP server for gemini-cli / claude-code / codex, (c) native
tool schema for `api-agent`, (d) a prompt card.  Observations are structured
(`{text, numbers, images[]}`); images are small labelled PNGs / contact sheets.

Core tools: `build`, `measure`, `render_views`, `render_sheet`, `isolate`,
`cross_section`, `check_connectivity`, `check_contract`, `compare_silhouette`,
`joint_sweep`, `shader_probe`, `scene_probe`, `read_cookbook`.

## 6. Judging

`VlmJudge(rubric)`: contact sheet + labelled views + measurement table +
acceptance checklist → JSON (criterion scores, issues, improvement plan,
acceptance verdicts).  Score is **computed in code** from rubric weights with
hard floors; n-sample (view-order shuffled) mean/std; deterministic caps from
gate findings are listed in the verdict.  Rubrics: `static_object_v1`,
`articulated_v1`, `scene_v1`, `asset_v1`, `reference_v1`.  `PairwiseJudge`
(position-swapped) for best-of-N and preference data.

## 7. Round loop (all tracks)

```
baseline  → build/gates/measure/render/judge            (the "raw model" delta)
repeat ≤ max_rounds:
   plan = judge.improvement_plan ∪ gate.errors ∪ failed acceptance
   dispatch targeted tasks (per part / zone / asset; parallel when file-disjoint)
   build → repair loop on build errors (≤ max_repair_attempts, error-focused)
   gates → measure → render → judge
   keep best (score, then fewer errors); stop on pass / plateau / budget
finalise: restore best commit, export, caption, record
```

## 8. Flywheel

`record.json` per run + git history; `c3v flywheel export` writes sample
folders (`code.<ext>` / multi-file, `meta.json`, `captions.json`, `renders/`)
compatible with the owner's STORAGE_RULES, a parquet index, preference pairs
(round i < round j by judge Δ ≥ τ), repair pairs (error → fix diff), and
cross-backend best-of pairs.
