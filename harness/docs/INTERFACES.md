# Cross-package interfaces (as built)

This file lists the signatures other packages may rely on.  Types live in
`codeverse/contracts/`; protocols in `models/base.py`, `agents/base.py`,
`languages/base.py`, `spatial/registry.py`, `judges/base.py`, `tracks/base.py`.
Where the build deviated from the original plan the deviation is called out as
**Δ** — see `docs/DECISIONS.md` for the why.  When in doubt the code wins; this
file was reconciled against it on 2026-08-23 (waves 2–3 + fix batch 1).

## Ids

* Chat models: `<provider>:<model>` — `gemini:*` · `anthropic:*` · `openai:*`
  (`models.registry.parse_model_id`).
* Coding agents: `<kind>:<model>` — `gemini-cli:*` · `claude-code:*` · `codex:*` ·
  `agy:*` · `api-agent:<provider>:<model>` (`agents.registry.parse_agent_id`).
* **Δ** Single-shot generation ids: `single-shot:<provider>:<model>` — handled only
  inside `tracks/generation.py` (`is_single_shot`, `single_shot_model_id`); never
  registered in `agents/`.  `Spec.backends.generator` accepts any of the three.
* Image models: `models/gemini_image.py::GeminiImageModel` (default
  `gemini-3.1-flash-image`, fallback `gemini-2.5-flash-image`) behind the
  `ImageModel` protocol (`generate`, `generate_with_usage` → `(images, Usage)`).

## core (contracts · config · proc)

```python
from codeverse.contracts import TRACK_INFO, TrackInfo          # {Track: TrackInfo(rubric, label)} — THE track registry
from codeverse.contracts import ENTRY_FILE, code_file, LANGUAGE_LABEL   # {Language: "src/<entry>"}; code_file(lang) -> "code.<ext>"
from codeverse.contracts import RunOptions                     # Spec.options: candidates (int|None, ≥1), texture (bool)
GateFinding.as_line(with_gate=False, with_severity=False, with_target=False, with_hint=True) -> str
    # "GATE <gate>: [<sev>] <message> [<target>] FIX: <hint>" — flags opt in; no leading "- "
RenderView.judge: bool | None      # stamped True/False at render time; None = legacy round (fall back to select_judge_views)
RenderSet.out_dir: str             # directory the views (+ views.json/metrics.json) were written to ("" on old rounds)
from codeverse.config import get_settings
get_settings().backends(planner=..., generator=..., judge=..., captioner=...) -> Backends
    # settings defaults (default_planner/... mirror contracts Backends literals; + default_captioner);
    # truthy keyword overrides win, None/"" falls through, unknown role -> TypeError
from codeverse.proc import ProcResult, run_subprocess, kill_group, tail, write_json_atomic
run_subprocess(cmd, *, cwd, timeout_s, env=None, stdin_text=None, preexec_fn=None) -> ProcResult
    # own session/process group, group-kill on timeout, never raises on rc != 0; stdlib-only module
```
`Spec.options` is plan-hash safe (`plan_stage_inputs` whitelists spec fields).
`Workspace.write_json` delegates to `proc.write_json_atomic`.

## models/
```python
from codeverse.models import get_chat_model            # (model_id) -> ChatModel, lru-cached, thread-safe
resp = m.generate(ChatRequest(messages=[...], system=..., response_schema=..., temperature=..., thinking=..., label=...))
resp.parsed / resp.text / resp.tool_calls / resp.usage   # Usage always has cost_usd (models.pricing)
from codeverse.models.keypool import KeyPool, KeyPoolExhausted
KeyPool(keys, *, rpm_per_key=900, tpm_per_key=None, cooldown_s=30, dead_cooldown_s=3600)
pool.acquire(*, tokens_hint=0, exclude=None, timeout_s=120) -> key    # raises immediately when every key is dead/cooling past the deadline
pool.report(key, "ok"|"429"|"5xx"|"error"|"dead", *, tokens=0, retry_after_s=None)   # Δ "dead": health 0, benched dead_cooldown_s, re-probed after
from codeverse.models.pricing import estimate_cost       # (provider, model, usage) -> usd (unknown model -> 0.0 + one warning)
from codeverse.models.schema_utils import to_gemini_schema, to_openai_strict_schema, to_anthropic_schema, parse_json_lenient
```
Backends: `GeminiModel(model, *, keys=None, pool=None, ...)` (one shared KeyPool per
key list), `AnthropicModel(model, *, json_mode="tool"|"output_config")`,
`OpenAIModel(model, *, base_url=None)` (Chat Completions; `CV3D_OPENAI_BASE_URL`).
Rules that callers must know:
* **Δ** Gemini key handling: auth/permission errors (401/403, or 400 with
  API_KEY_INVALID / expired / PERMISSION_DENIED / suspended markers) classify as
  outcome `dead` — the call rotates to the next key at once (no retry budget, no
  sleep) and the key is benched for an hour once another key succeeds; it raises
  only when *every* key fails the same way.  A 429 while an untried key remains is
  a **free** rotation (does not consume `max_attempts`); only when all keys are
  throttled do 429s count against the budget with backoff.
* **Δ** Gemini: `tools` + `response_schema` together → schema dropped, text parsed
  leniently into `.parsed`, warning in `resp.raw["warnings"]`.  Gemini 3.x cannot
  disable thinking; keep `max_output_tokens ≥ ~1000` even for one-word answers.
* Anthropic 4.6+ models use adaptive thinking + `output_config.effort`.
  Tool-call ids must be echoed unchanged (they key provider caches).
* **Δ** `to_openai_strict_schema` never adds `null` to defaulted fields: a property
  is nullable iff the *source* pydantic schema is (`T | None`); non-null defaults
  are surfaced as a `[default: …]` description hint.  Wire contract == pydantic contract.
* **Δ** `pricing.lookup_price` prefix fallback matches only pure version/date/channel
  suffixes (`-`/`_`/`@`/`:` + digits/latest/preview/exp/beta/…); sibling models
  (`-nano`, `-mini`, `-codex`, `-lite`) never inherit the parent's price — they are
  unknown (0.0 + warning) unless a row exists.

## agents/
```python
from codeverse.agents import get_coding_agent           # (agent_id) -> CodingAgent  .run(job) .available() .id .kind .model
from codeverse.agents.materialize import materialize_workspace, Materialized, codex_mcp_overrides
materialize_workspace(ws, *, agent_kind, contract_md, cookbook_rel, spatial_tools, mcp_command) -> Materialized
# writes AGENTS.md + GEMINI.md + CLAUDE.md (same body), ws/.3dcv/cookbook.md (Δ copied in: gemini-cli cannot read
# outside the workspace), .geminiignore/.aiexclude, and MCP wiring:
#   gemini-cli → ws/.gemini/settings.json {"mcpServers": {"3dcv": {...}}} + context.fileFiltering.respectGitIgnore=false
#   claude-code → ws/.mcp.json      codex → Materialized.codex_overrides:
#     -c mcp_servers.3dcv.command=… -c mcp_servers.3dcv.args=[…] -c mcp_servers.3dcv.default_tools_approval_mode="approve"
#     (Δ without the approval mode every MCP tool call is elicited and auto-cancelled)
#   agy → no per-workspace MCP; body documents `3dcv tools <name> --json … --workspace .` as the fallback
res = agent.run(AgentJob(workspace=..., prompt=..., label="baseline", timeout_s=1800, max_turns=60,
                         spatial_tools=True, write_roots=["src","public"], extra={...}))
res.ok, res.exit_reason  # completed | timeout | error | budget | model_substituted
res.files_changed (git-derived, attributed per session), res.usage, res.transcript_path, res.tool_calls, res.errors
```
`AgentJob` carries typed job context: `round`, `kind`, `language`, `track`,
`mcp_command` (override), `files_hint` (workspace-relative files/dirs the task is
expected to touch — used to attribute `files_changed` between concurrent sessions in
one workspace; harness-owned paths and sibling sessions' hinted files are dropped)
and `job.api: ApiAgentOptions(max_usd, temperature, thinking, allow_shell)` (api-agent
only).  **Δ legacy lift**: the same keys passed inside `extra={...}` are lifted into
the typed fields at validation (extra itself is left untouched), so old constructors
and serialized jobs keep working; `extra` stays for one-off backend hints.
Trajectories: `ws/trajectories/<label>_rNN/{prompt.md, transcript.jsonl, stdout.json,
stderr.log, result.json}`; **Δ** a re-run of the same label+round lands in
`<label>.a2_rNN` (then `.a3` …) — the first attempt is never overwritten; `result.json`
records `attempt` + `job_label`.  Each run makes two git commits (`pre:`/`agent:<label>`).
gemini-cli specifics: system settings file via `GEMINI_CLI_SYSTEM_SETTINGS_PATH`
(api-key auth, `dynamicModelConfiguration=true` else silent model substitution →
`exit_reason="model_substituted"`, `folderTrust.enabled=false` else workspace MCP
silently dropped).  **Δ** `Usage.input_tokens` = `tokens.prompt` (TOTAL prompt incl.
cached; `tokens.input` is the uncached count) and each served model is priced at its
own rate.  KeyPoolExhausted never escapes `run()` (→ `exit_reason=budget`); retries
prefer a different key (10 s wait) before re-using the same one.
api-agent tools: `read_file`, `write_file`, `edit_file`, `list_files`, `run_shell`
+ every spatial tool (`build` keeps its real name; `run_build` is an alias).
**Δ** `run_shell` is a *policy filter*, not an OS sandbox: allow-listed programs only,
no inline-code/REPL/loader flags (`-c/-e/-p/-i/--eval/--require/--import/…`),
`python -m` restricted to a small allow-list, every path-like argument
realpath-confined to the workspace (and never `.git/`).  `FileTools.call` never
raises — bad arguments / non-UTF-8 files come back as `ToolOutcome(is_error=True)`.

## languages/
```python
from codeverse.languages import get_runtime            # (Language | str) -> LanguageRuntime
rt.language; rt.entry_globs
rt.lint(ws) -> GateReport                              # gate = "lint:<language>"
rt.build(ws, *, timeout_s=None, **per_runtime) -> BuildResult   # Δ BuildResult.error_file is WORKSPACE-relative for
rt.skeleton(ws, plan) -> list[Path]                    #   every runtime ("src/model.py", "src/parts/leg.py", "src/helpers.py")
rt.contract_doc() -> str ; rt.cookbook_path() -> Path
```
| runtime | entry_globs | notes / artifacts |
|---|---|---|
| `BlenderRuntime` | `src/model.py`, `src/parts/*.py` | **Δ multi-file**: `file_for_part(name) -> "src/parts/<snake>.py"` (`def build_<snake>()`), `file_for_target(target) -> list[str]`; lint = `layout.lint_workspace` over every src/*.py; skeleton writes model.py + per-part files; object.glb/stl, build.json, census.json |
| `CadQueryRuntime` | `src/model.py` | trailing selector on the stack → parent solid exported + warning (ExportError when no solid exists); helper-module errors map to `src/<file>.py:line`; object.glb/stl/step |
| `ThreeJsRuntime` | `src/object.js`, `src/parts/*.js` | **Δ export as authored** (`--normalise` opt-in, never passed by the runtime; census `placement_offset`/`normalised_offset`); InstancedMesh baked to `<Name>_<i>` meshes (`instanced_meshes_baked`); exported `selfcheck(THREE, root)` is called (throw → SelfCheckError); NaN geometry errors name mesh/part → routed to `src/parts/<snake>.js` |
| `UrdfBlenderRuntime` | `src/model.py`, `src/robot.urdf` | object.glb (Y-up, node=link, joint extras), meshes/<link>.glb (raw Z-up link frames); link name `world` is reserved (lint ERROR + UrdfError); robot GLB root gets `__root` suffix on name clash |
| `SceneThreeJsRuntime` | `src/scene.js`, `src/zones/*.js`, `src/assets/*.js`, `src/env.js`, `src/shaders/*.js` | glb_path=None; ok iff `probe_scene` + `check_shaders` pass |
| `GlslShaderRuntime` | `src/shader.frag`, `src/common.glsl`, `src/buffer_a.frag` | harness owns `#version`/uniforms/`out` (wrap.HEADER: u_time/u_resolution/u_mouse/u_frame/u_prev/u_noise + iTime/iChannel* aliases); build renders judge frames via GlHost; compile errors → GlslCompileError at mapped src file:line; artifacts frames/, frames_sheet.png, preview.gif, metrics.json |
| `OpenGLPythonRuntime` | `src/program.py`, `src/*.glsl` | `setup(ctx,w,h)->state` + `render(ctx,state,t,frame,fbo)` run in a moderngl subprocess (`wrappers/run_gl.py`); exceptions map to src/program.py:line, in-string GLSL errors carry both line numbers |

Wrappers are standalone (never import codeverse): `blender/wrappers/run_bpy.py`
(+ sibling `_census.py` — copy both), `cadquery/wrappers/run_cq.py`,
`urdf/wrappers/run_bpy_links.py`, `opengl_python/wrappers/run_gl.py`; threejs/scene
export+render live in `runtime_js/` (`export_glb.mjs`, `render_glb.mjs`,
`render_scene.mjs`, `probe_scene.mjs`, `check_shaders.mjs`, `lib/instances.mjs`).
**Placement policy (all languages)**: nothing re-centres or drops to ground at
export; the build warns and the contract/connectivity gates report it.
**Δ Node ESM resolution**: bare `import 'three'` needs
`run_node(..., three_hook=True)` (`--import runtime_js/lib/resolve_three.mjs`).

## spatial/
```python
from codeverse.spatial.render import render_glb, render_scene, render_turntable
render_glb(glb, out_dir, *, views=None, mode="shaded|wire|normals|silhouette|clay", width=768, height=768,
           isolate=None, explode=0.0, sheet=True, background=..., anim_time=None, shadow=True, gpu=None,
           timeout_s=None, use_cache=True) -> RenderSet
render_scene(ws, out_dir, *, cameras=None, orbit=True, times=(0.0, 1.5), width=1024, height=576, sheet=True,
             bounds=None, sheet_max_views=10) -> RenderSet
    # Δ bounds default from ws plan.json → orbit rig frustum-fits the CONTENT box (not ground/sky);
    # contact sheet = judge subset only; views.json entries get judge: true|false
from codeverse.spatial.render_scene import select_judge_views, JUDGE_MAX_VIEWS, read_metrics, plan_bounds
select_judge_views(rs, max_n=10) -> RenderSet          # priority: authored@t0, 2 overview@t0, 2 authored@t_last, rest
from codeverse.spatial.frame_metrics import frame_gate_from_renders, frame_findings, frame_summary_text, FRAME_GATE
frame_gate_from_renders(renders) -> GateReport         # gate "scene_frames"; data.kind ∈ dark_frame | blown_frame | flat_frame |
    # camera_in_geometry | camera_underground | camera_low | camera_high | content_small; authored cameras → ERROR, orbit rig → WARN
from codeverse.spatial.measure import measure_glb      # link-hierarchy rule: metadata["links"] → each link is its own part
from codeverse.spatial.connectivity import check_connectivity   # (glb, *, gap_m=…, …, language="") — Δ language selects the
from codeverse.spatial.contract import check_contract           # frame of fix hints; both gates emit hints in the AUTHORING frame
                                                                # (labelled "blender frame: Z-up, -Y front" etc.), GLB vectors in data
from codeverse.spatial.silhouette import compare_silhouette
from codeverse.spatial.joints import load_urdf, fk, sweep_collisions, urdf_to_glb, render_poses   # RESERVED_LINK_NAMES={'world'}
# joints_collide.py: deterministic penetration (oriented islands + fixed-direction parity ray test; python-fcl is a
# core dependency, the trimesh fallback is deterministic too)
from codeverse.spatial.gl_render import GlHost, GlResult, GlHostError, write_contact_sheet, write_gif
GlHost(gpu="auto|on|off", timeout_s=240, fps=30, max_steps=240)
  .render_fragment_shader(frag_src, out_dir, *, width=1280, height=720, times=..., buffer_a_src=None, feedback=False)
  .run_program(program_path, out_dir, *, width, height, times) -> GlResult{ok, stage, frames[GlFrame], renderer, gpu, …}
from codeverse.spatial.frame_stats import sequence_stats, frame_gate    # gate "gl_frames"; data.kind ∈ nan | black | blown |
                                                                        # static | flicker | low_detail | duplicate | no_frames
from codeverse.spatial.registry import tool, get_tool, list_tools, tool_cards, ToolContext, Observation
import codeverse.spatial.tools   # registers: build, measure, render_views, render_sheet, isolate, cross_section,
    # check_connectivity, check_contract, compare_silhouette, joint_sweep [articulated], shader_probe, scene_probe,
    # scene_views [scene], read_cookbook, gl_probe + gl_frames [graphics], texture_pass + texture_preview [object tracks]
python -m codeverse.spatial.mcp_server --workspace <ws> [--track X] [--language Y] [--round N] [--list]   # MCP name: 3dcv
```

## judges/
```python
from codeverse.judges.rubrics import load_rubric, Rubric   # Rubric{…, defects: [DefectItem{id, text, penalty, cap}], caps[{…, when:, kinds}]}
from codeverse.judges.vlm_judge import VlmJudge
VlmJudge(rubric="static_object_v1", model_id=None (settings.default_judge = gemini-3.1-pro-preview), n_samples=1,
         temperature=0.2, *, thinking="low", max_attempts=3, max_montages=3, detail_crops=2, max_px=1024,
         chat_model=None, cache_dir=None, label="judge")            # Δ max_images is GONE → montage budget
VlmJudge.judge(inp, *, geometry_views: RenderSet | None = None) -> Judgment   # also reads inp.geometry_views
from codeverse.judges.base import JudgeInput   # (spec, renders, measurement=None, gates=[], acceptance=[], plan_summary="",
                                               #  round_index=0, previous=None, extra_context="", geometry_views=None)
from codeverse.judges.montage import plan_montages, render_montage, Montage    # ≤3 2×2 montages (shaded/geometry/poses) + ≤2 detail
    # crops @≤1024px replace sheet+9 views; clay/normals views (RenderView.mode) auto-route to the GEOMETRY montage
from codeverse.judges.scoring import is_degraded, aggregate_samples   # ScoreBreakdown adds defects, defect_votes (majority,
    # ties→present), defect_penalty, overall_after_defects; overall = caps(weighted_mean − Σpenalty)
from codeverse.judges.caps import apply_caps           # (rubric, overall, gates, acceptance_results, acceptance_items=None, *,
                                                       #  console_errors=None, views=None, defects_present=None) -> CapResult;
                                                       # cap rules add when="missing_views" and ledger lines "defect:<id>"
from codeverse.judges.pairwise import PairwiseJudge    # .compare(spec, renders_a, renders_b, *, rubric=…) -> PairwiseResult
PairwiseJudge.compare_many(spec, candidates: list[RenderSet], *, rubric) -> RankingResult{order, points, confidence, pairs, usage, .best}
from codeverse.judges.calibration import calibrate, CalibrationTable   # (run_dirs, *, model_id, n_samples=3, out_dir, geometry_mode,
    # rounds, …) -> rows + pearson/spearman(errors vs score), mean_std, cost; CLI: python -m codeverse.judges.calibration RUN… --n 3
from codeverse.judges.reference import ReferenceJudge  # image-conditioned specs
from codeverse.judges.metrics import judge_agreement, plateau, best_index
```
Rubrics: `static_object_v1` (0.72), `articulated_v1` (requires pose views via
`missing_pose_sheet` cap), `scene_v1` (frame-gate caps dark/blown/flat/content_small),
`asset_v1`, `reference_v1`, `shader_v1` (0.70; gl_frames caps).  Wire schema order is
observe-then-score (summary/issues/defects/acceptance before criteria) — measured to
restore flash's dynamic range.  `passed = overall ≥ threshold ∧ no floor ∧ all
must-acceptance`; gate authors set `GateFinding.data["kind"]` so caps match precisely.

## orchestrator/ + tracks/
```python
from codeverse.tracks import get_track
rec = get_track(spec.track, **options).run(spec, ws, resume=False) -> RunRecord   # Δ kwargs forwarded to the constructor:
# services=, judge=, agent=, model=, runtime=, policy=RoundPolicy, settings=, planner_model=, n_candidates=
# (CLI --candidates > run_state.extra > settings.default_candidates); StaticObject | Articulated | Scene | Graphics
from codeverse.orchestrator.rounds import RoundPolicy, StopPolicy, BestSelector, build_refine_instructions, compact_instructions
RoundPolicy(max_rounds=4, plateau_window=2, min_delta=0.02, target=0.8, judge_on_gate_errors=True, max_refine_tasks=6,
            max_instructions_per_task=6, parallel_min_tasks=2, n_candidates=1, pairwise_margin=0.03,
            pairwise_min_confidence=0.6, judge_samples=1).with_candidates(n)
from codeverse.orchestrator.candidates import CandidateRecord, rank_candidates, decide_best   # pure decision logic
from codeverse.tracks.candidates import run_best_of_n, choose_best_round   # N parallel baselines in <ws>/_cand/c<k>
# (quick 4-view judge, crashed candidate retried once; selection by build_ok → quick score → fewer gate errors, pairwise
# within margin); winner copied back, normal r00 pipeline follows; rounds/candidates.json + record.extra["candidates"]
from codeverse.tracks.motion import default_motion_checks, expected_direction   # gate "motion_direction" (articulated)
from codeverse.tracks.reference import silhouette_gate, reference_refine_tasks  # gate "reference_silhouette" (IoU<0.6 → WARN + refine task)
from codeverse.tracks.prompting import base_prompt_context, reference_images, file_for_target_factory   # Δ split out of
from codeverse.tracks.common import RunContext, Services   # common.py (lazy re-exports keep old imports working)
from codeverse.tracks.generation import generate, run_agent_task, parse_multifile, is_single_shot
from codeverse.tracks.repair import build_with_repair
from codeverse.tracks.steps import run_round
from codeverse.tracks.planner import plan, plan_model_for, ensure_acceptance    # graphics uses tracks/graphics_steps.plan_graphics
```
`run_round` = generate → commit → `build_with_repair` → measure → gates → render →
post-render gates → judge → commit.  Post-render gates: static `reference_silhouette`
(when references), articulated `joint_sweep` + `motion_direction`, scene
`render_console` (`scene_frames` via `frame_gate_from_renders` is built but not yet
wired into `ScenePipeline.post_render_gates`), graphics `gl_frames`.  Reference specs get a
`ReferenceJudge` (rubric `reference_v1` for static_object) and images attached to
generation prompts.

## texturing/  (derived asset pack; code stays truth — object.glb is never touched)
```python
from codeverse.texturing.run import texture_pass, load_report
texture_pass(ws, spec, plan, *, model_id, image_model=None, judge=True, judge_model_id=None, rubric=None,
             glb_in=None, views=OBJECT_VIEWS_QUICK, size=1024, ..., events=None, update_record=True) -> TextureReport
# 1 vision call material plan (cacheable) → tileable textures (mirror cross-fade, seam_score ≤ 0.08) → world-metre UV
# unwrap (planar/box/cylinder per part, tile_size_m) → artifacts/object_textured.glb → seam gate + before/after judge
# gate (ship iff Δoverall ≥ −0.01 AND materials criterion improved); record.extra["texturing"], events texture.*
from codeverse.texturing.plan import material_plan, default_plan, TexturePlan
from codeverse.texturing.generate import generate_textures, FakeImageModel
from codeverse.texturing.scene_pack import scene_texture_pack, texture_pack_prompt   # 6–12 named tiles + manifest.json
# under public/textures/ for scene prompts (URL /public/textures/<name>.png)
```

## flywheel/ + cli/
```python
from codeverse.flywheel.record import finalize_record, load_record, iter_runs, best_round_index
from codeverse.flywheel.export import export_samples   # (runs_dir, out_dir, *, min_score=None, only_passed=False, best_round=True,
    # overwrite=True, include_unbuilt=False, captions_dir=None, drop_duplicates=False) -> ExportReport{…, n_duplicates, duplicates, tiers}
from codeverse.flywheel.quality import quality_tier, prompt_hash, find_duplicates   # tiers: A passed & 0 gate errors, B passed,
                                                                                    # C best ≥ 0.6, D else; dedupe = (code fingerprint, prompt)
from codeverse.flywheel.pairs import build_pairs       # (runs_dir, out_jsonl, *, min_delta=0.05, trajectories=True) -> n
from codeverse.flywheel.trajectories import mine_run   # in-session repair pairs from api-agent transcripts (replay-verified)
from codeverse.flywheel.captions import caption_sample # Δ (ws, record, model_id, *, model=None, out_dir=None) -> Captions;
                                                       # out_dir → side-car <out_dir>/<slug>.json, run untouched
from codeverse.flywheel.gallery import write_gallery, gallery_items, render_gallery   # self-contained HTML gallery (tier badges,
                                                                                      # thumbs); bench/report.py reuses it
from codeverse.flywheel.index import build_index, query, summary   # sqlite + parquet: adds quality_tier, gate_errors, cost_usd,
                                                                   # rounds, status, code_fingerprint, prompt_hash, duplicate_of, has_captions
3dcodeverse make|resume|status|render|judge|tools|mcp|texture {pass,scene-pack,show}
             |flywheel {export,pairs,caption,index,dedupe,gallery}|bench {run,report}|doctor    # alias: 3dcv
```

## Events and records
`EventLog.emit(event, **data)` writes `{"t", "event", ...}` (**Δ** key is `event`).
Event names: `run.start`, `stage.start/done`, `plan.done`, `workspace.materialized`,
`round.start`, `generate.done`, `build.done`, `gates.done`, `judge.done`,
`round.done`, `best.updated`, `refine.planned`, `asset.judged`, `assets.done`,
`zones.done`, `assemble.done`, `round.no_change`, `candidates.start`,
`candidate.start/done/failed/retry/selected`, `pairwise.done`,
`texture.start/plan/generated/applied/gate/done`, `budget.exceeded`,
`finalise.rebuild`, `stop`, `run.done` / `run.failed`.
`RunRecord` (record.json): spec, plan, workspace, status, rounds[RoundRecord],
best_round, baseline_score, final_score, total_usage, environment,
prompt_hashes{contract, cookbook, generate, refine}, error, extra{stop_reason,
rubric, budget, rounds_summary, n_candidates?, candidates?, texturing?, captions?}.
