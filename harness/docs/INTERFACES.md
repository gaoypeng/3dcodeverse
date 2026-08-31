# Cross-package interfaces (as built)

This file lists the signatures other packages may rely on.  Types live in
`codeverse/contracts/`; protocols in `models/base.py`, `agents/base.py`,
`languages/base.py`, `spatial/registry.py`, `tracks/__init__.py` (judges are
duck-typed `.judge(JudgeInput) -> Judgment`; `judges/base.py` defines no protocol).
Where the build deviated from the original plan the deviation is called out as
**Δ** — see `docs/DECISIONS.md` for the why.  When in doubt the code wins; this
file was reconciled against it on 2026-08-23 (waves 2–3 + fix batch 1).

## Ids

* Chat models: `<provider>:<model>` — `gemini:*` · `anthropic:*` · `openai:*`
  (`models.registry.parse_model_id`).
* Coding agents: `<kind>:<model>` — `gemini-cli:*` · `claude-code:*` · `codex:*` ·
  `agy:*` (`agents.registry.parse_agent_id`).
* **Δ** Single-shot generation ids: `single-shot:<provider>:<model>` — handled only
  inside `tracks/generation.py` (`is_single_shot`, `single_shot_model_id`); never
  registered in `agents/`.  `Spec.backends.generator` accepts any of the three.
* Image models: `models/gemini.py::GeminiImageModel` (default
  `gemini-3.1-flash-image`, fallback `gemini-2.5-flash-image`); the one entry point
  is `generate_with_usage(prompt, *, size=1024, n=1, seed=None, reference_images=(),
  max_wait_s=None) -> (images, Usage)`, priced per GENERATED image via
  `pricing.per_image_usd(size=<generated px>)` (no `ImageModel` protocol, no `generate`).

## core (contracts · config · proc)

```python
from codeverse.contracts import TRACK_INFO, TrackInfo          # {Track: TrackInfo(rubric, label)} — THE track registry
from codeverse.contracts import ENTRY_FILE, code_file, LANGUAGE_LABEL   # {Language: "src/<entry>"}; code_file(lang) -> "code.<ext>"
from codeverse.contracts import RunOptions                     # Spec.options: candidates (int|None, ≥1), texture (bool)
GateFinding.as_line(with_gate=False, with_severity=False, with_target=False, with_hint=True) -> str
    # "GATE <gate>: [<sev>] <message> [<target>] FIX: <hint>" — flags opt in; no leading "- "
RenderView.judge: bool | None      # stamped True/False at render time; None = legacy round (fall back to select_judge_views)
RenderSet.out_dir: str             # directory the views (+ views.json/metrics.json) were written to ("" on old rounds)
from codeverse.config import get_settings, env_flag       # env_flag(env, fallback) -> bool: on/off/1/0/true/false/yes/no;
    # unset/empty -> fallback; garbage -> warning + fallback (Settings value), never a silent switch
get_settings().backends(planner=..., generator=..., judge=..., captioner=...) -> Backends
    # settings defaults (default_planner/... mirror contracts Backends literals; + default_captioner);
    # truthy keyword overrides win, None/"" falls through, unknown role -> TypeError
from codeverse.proc import ProcResult, run_subprocess, kill_group, tail, write_json_atomic
run_subprocess(cmd, *, cwd, timeout_s, env=None, stdin_text=None, preexec_fn=None) -> ProcResult
    # own session/process group, group-kill on timeout, never raises on rc != 0; stdlib-only module
from codeverse.proc import read_json_or_none, iter_jsonl_lines, read_jsonl_lenient, append_jsonl_line
read_json_or_none(path, *, errors=None) -> dict | None   # None when absent / unreadable / malformed / not a dict
iter_jsonl_lines(path) -> Iterator[tuple[int, str]]       # (1-based line_no, line); missing file -> nothing; blanks skipped
read_jsonl_lenient(path, *, log=None, dicts_only=False) -> list  # bad lines skipped (debug-logged when `log` given)
append_jsonl_line(path, rec, lock) -> None                 # one json.dumps(ensure_ascii=False, default=str) line under `lock`
```
`Spec.options` is plan-hash safe (`plan_stage_inputs` whitelists spec fields).
`Workspace.write_json` delegates to `proc.write_json_atomic`.
`Workspace.stage_artifacts(*names) -> ArtifactStage` (2026-08-27): enter invalidates the
canonical `artifacts/` names, writes land under `artifacts/.staging/<pid-nonce>/`,
`promote()` `os.replace`s on success, exit-without-promote discards — canonical always
means the last OK build.  `Workspace.restore_paths(commit, paths)` = per-path
`git checkout <commit> -- <paths>` under the per-root lock.

## models/
```python
from codeverse.models import get_chat_model            # (model_id) -> ChatModel, lru-cached, thread-safe
resp = m.generate(ChatRequest(messages=[...], system=..., response_schema=..., temperature=..., thinking=..., label=..., max_wait_s=None))
#   max_wait_s: the longest this ONE call may spend, retries included (None = models.retry.RETRY_DEADLINE_S = 900 s);
#   GeminiModel clips its retry deadline to it; api_agent 20-120 s per turn, VlmJudge 240 s per sample, planner 300 s
#   resp.raw["key"] = "…ab12" (the key that answered), resp.raw["attempts"] = round-trips issued (hedged siblings included)
resp.parsed / resp.text / resp.tool_calls / resp.usage   # Usage always has cost_usd (models.pricing)
from codeverse.models.retry import KeyPool, KeyPoolExhausted
KeyPool(keys, *, rpm_per_key=900, tpm_per_key=None, cooldown_s=30, dead_cooldown_s=3600)
pool.acquire(*, tokens_hint=0, exclude=None, timeout_s=120) -> key    # raises immediately when every key is dead/cooling past the deadline
pool.try_acquire(*, tokens_hint=0, exclude=None) -> key | None        # never waits (a hedged retry's extra key); holds a slot like acquire
pool.report(key, "ok"|"429"|"5xx"|"error"|"dead"|"skip", *, tokens=0, retry_after_s=None)   # Δ "dead": health 0, benched dead_cooldown_s,
    # re-probed after; "skip" (a content failure the key did not cause) reconciles tokens only — health and counters untouched
from codeverse.models.retry import with_retries, rotate_with_retries, RETRY_DEADLINE_S
with_retries(fn, *, is_retryable, attempts=6, base_delay=1.0, max_delay=3.0, max_total_s=None, ...)   # max_total_s =
    # ChatRequest.max_wait_s clipped to RETRY_DEADLINE_S on every provider; stops before a backoff would cross it, stamps ModelError.attempts
from codeverse.models.pricing import estimate_cost       # (provider, model, usage) -> usd (unknown model -> 0.0 + one warning)
from codeverse.models.schema_utils import to_gemini_schema, to_openai_strict_schema, to_anthropic_schema, parse_json_lenient
from codeverse.models.schema_utils import ask_structured   # (model, Schema, *, system, text, images=(), temperature, label)
    # -> (obj | None, Usage, err): one schema-bound call; call failure and parse failure are the same `err` family
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

## cost/  (ledger · profiles · price provenance)
```python
from codeverse.cost import record_call, load_ledger, summarise, open_run_ledger
record_call(usage, *, run="", round=None, stage=None, role=None, label="", backend="", model="",
            outcome="ok", latency_ms=None, n_calls=1, source="live", ledger=None, reprice=False) -> CallCost
    # Δ everything left out is resolved from the ambient context + the call label (cost.context);
    # unknown model -> $0 and price_source="unknown" (flagged, never silently dropped); never raises.
from codeverse.cost.instrument import (run_ledger, metered_chat_model, metered_agent, meters_own_calls,
                                       MeteredAgent, MeteredChatModel, metering_enabled)
with run_ledger(ws.root, run=slug):        # binds the run, points record_call at <run>/telemetry/cost.jsonl
    ...                                    # (+ a <run>/cost_ledger.jsonl symlink for the run-layout alias)
    # NESTS by holding ContextVar tokens (bound_run / bound_ledger) — context-local ONLY, no
    #   process-wide default — so bench.run_bench / compare_backends hold one ledger per prompt
    #   across N threads, and a thread that may bill a model goes through proc.fan_out.
    #   BaseTrack.run opens `run_ledger(ws.root, run=ws.root.name)` ITSELF, so a track run started
    #   outside the CLI/bench still meters; the CLI/bench ledger for the same run dir is the same file.
    # models.get_chat_model returns a MeteredChatModel → ONE row per ChatModel.generate
    # agents.get_coding_agent returns a MeteredAgent (registry wrap, from the first call) → ambient
    #   round/stage for the session and — iff NOT meters_own_calls(agent), a declaration no shipped
    #   backend makes — one source='session' row (n_calls, latency, outcome, AgentResult.turns).
    #   These two are the ONLY ledger writers; BudgetGuard writes no row.
    #   The rule is the BACKEND, never "did a row get written while it ran": a CLI session with one
    #   in-process tool call used to be dropped entirely, an in-process session whose turns ran in
    #   another thread used to be counted twice.  A backend may declare `meters_own_calls`.
from codeverse.cost.context import CallContext, call_context, bound_run, context_from_label, SELF_DESCRIBING
from codeverse.cost.ledger import bound_ledger, process_ledger_path   # (Δ 2026-08-30) both are context
    #   managers holding a ContextVar token; there is no process-wide default any more — a thread that
    #   may bill a model is spawned through proc.fan_out (which copies context), never a bare pool.
    # precedence: explicit > a label naming a job of its OWN (SELF_DESCRIBING = plan/judge/pairwise/
    #   texture/caption) > ambient (call_context) > the rest of the label > nothing.  A judge or a
    #   texture pass that bills a model INSIDE an agent session is filed under its own stage, not the
    #   session's; a generation label yields to the session (best-of-N: kind="candidate" beats
    #   label="baseline"); Stage.OTHER means "the label said nothing" and displaces nothing.
    # labels understood: judge:<rubric>:r<NN>:s<k> · planner · pairwise:… · texture… · caption… (api-agent:<label>:t<turn> in historical ledgers only)
from codeverse.cost.caching import Block, order_blocks, prefix_report, session_cache, session_key
session_cache(rows) -> [SessionCache]      # per session: cold first call, cached share, saved_usd, cold_usd
from codeverse.cost.profiles import get_profile, PROFILES   # economy | balanced | quality
get_settings().apply_profile(name, *, force=False) -> Profile
    # sets default_{generator,planner,judge,captioner}, default_candidates, Settings.judge
    # (max_px/montages/detail_crops/samples) and limits.agent_max_turns; a value the user stated in
    # config.yaml / CV3D_* survives unless force (3dcv make --profile forces).
from codeverse.cli._common import resolve_dial, ResolvedDial   # THE resolver, one per `3dcv make`
resolve_dial(settings, profile_flag=None, *, rounds=None, candidates=None,
             max_minutes=None, texture=False) -> ResolvedDial
    # profile/generator/planner/judge/captioner, judge_samples/judge_max_px/judge_montages/
    # judge_detail_crops, agent_max_turns, rounds, candidates, texture, max_minutes.
    # `--profile X` and `CV3D_PROFILE=X` resolve to the SAME dial (they used to disagree on
    # candidates + texture); an explicit flag beats both.  Spec.options.profile always records the
    # resolved name so `3dcv resume` re-applies it.
from codeverse.models.pricing import price_provenance    # (provider, model) -> PriceRow(price, match, status, checked)
```
`3dcv cost <slug|path>…` · `3dcv cost --runs-dir <root>` · `3dcv cost cache <slug>` ·
`3dcv cost prices [--stale] [--days N] [--unverified]` · `3dcv cost profiles` · `3dcv cost estimate`.
A run with a live ledger is read from it (`RunLedger.source == "live"`); older runs are
reconstructed, so all 61 recorded runs keep auditing.

## agents/
```python
from codeverse.agents import get_coding_agent           # (agent_id) -> CodingAgent  .run(job) .available() .id .kind .model
from codeverse.agents.materialize import materialize_workspace, codex_mcp_overrides
materialize_workspace(ws, *, agent_kind, contract_md, cookbook_rel, spatial_tools, mcp_command=None) -> None
# mcp_command defaults to cli_common.default_mcp_command(ws) = [sys.executable, -m codeverse.spatial.mcp_server --workspace …]
# (None/[] = the default — no ValueError); tracks/common.Services.materialize takes no mcp_command
# writes AGENTS.md + GEMINI.md + CLAUDE.md (same body), ws/.3dcv/cookbook.md (Δ copied in: gemini-cli cannot read
# outside the workspace), .geminiignore/.aiexclude, and MCP wiring:
#   gemini-cli → ws/.gemini/settings.json only gets context.fileFiltering.respectGitIgnore=false; the 3dcv server
#     and mcp.allowed live in the per-session system settings (Δ that file is agent-writable — audit 2026-08-27)
#   claude-code → trajectories/<label>_rNN/mcp.json      codex → codex_mcp_overrides(mcp_command) built PER SESSION:
#     -c mcp_servers.3dcv.command=… -c mcp_servers.3dcv.args=[…] -c mcp_servers.3dcv.default_tools_approval_mode="approve"
#     (Δ without the approval mode every MCP tool call is elicited and auto-cancelled)
#   agy → no per-workspace MCP; body documents `3dcv tools <name> --json … --workspace .` as the fallback
res = agent.run(AgentJob(workspace=..., prompt=..., label="baseline", timeout_s=1800, max_turns=60,
                         spatial_tools=True, write_roots=["src","public"]))   # typed kwargs only: no extra=, no model=
res.ok, res.exit_reason  # completed | timeout | error | budget | model_substituted
res.turns                # claude-code num_turns · codex turn.completed count · agy num_turns · gemini-cli 0 (not on the wire)
res.files_changed (git-derived, attributed per session), res.usage, res.transcript_path, res.tool_calls, res.errors
```
`AgentJob` carries typed job context: `round`, `kind`, `language`, `track`,
`mcp_command` (override), `files_hint` (workspace-relative files/dirs the task is
expected to touch — used to attribute `files_changed`; harness-owned paths are dropped;
sessions on one workspace are serialised, so there is no sibling filter),
`edit_only` / `always_writable` (a scoped refine may overwrite only its hinted files +
the entry file), `read_only: list[str]` (harness-owned files INSIDE the write roots the
session may read but never write — `contracts.common.HARNESS_OWNED_SRC[language]`, i.e.
`src/recipes.glsl` for glsl_shader; `tracks/generation.py` sets it on every job of the
run and `agents/cli_common._enforce_scope` reverts a post-session write to one and
fails that session).  **Δ legacy lift**: the job keys (`round`, `kind`, `language`,
`track`, `files_hint`, `mcp_command`) passed inside `extra={...}` are lifted into
the typed fields at validation (extra itself is left untouched), so old constructors
and serialized jobs keep working; `extra` stays for one-off backend hints.
Trajectories: `ws/trajectories/<label>_rNN/{prompt.md, transcript.jsonl, stdout.json,
stderr.log, result.json}`; **Δ** a re-run of the same label+round lands in
`<label>.a2_rNN` (then `.a3` …) — the first attempt is never overwritten; `result.json`
records `attempt` + `job_label`.  Each run makes two git commits (`pre:`/`agent:<label>`).
gemini-cli specifics: system settings file via `GEMINI_CLI_SYSTEM_SETTINGS_PATH`
(written per session into the trajectory dir: api-key auth, `dynamicModelConfiguration=true`
else silent model substitution → `exit_reason="model_substituted"`, `folderTrust.enabled=false`
else workspace MCP silently dropped, plus `mcpServers.3dcv` + `mcp.allowed=["3dcv"]` — **Δ** it is
applied LAST and `mcp.allowed` replaces, so an agent-planted server in ws/.gemini/settings.json is Blocked).  **Δ** `Usage.input_tokens` = `tokens.prompt` (TOTAL prompt incl.
cached; `tokens.input` is the uncached count) and each served model is priced at its
own rate.  KeyPoolExhausted never escapes `run()` (→ `exit_reason=budget`); retries
prefer a different key (10 s wait) before re-using the same one.
## languages/
```python
from codeverse.languages import get_runtime            # (Language | str) -> LanguageRuntime
rt.language; rt.entry_globs
rt.lint(ws) -> GateReport                              # gate = "lint:<language>"
rt.build(ws, *, timeout_s=None, **per_runtime) -> BuildResult   # Δ BuildResult.error_file is WORKSPACE-relative for
rt.skeleton(ws, plan) -> list[Path]                    #   every runtime ("src/model.py", "src/parts/leg.py", "src/helpers.py")
rt.contract_doc() -> str                               # cookbook path: tracks/common.cookbook_rel_for (no runtime hook)
BuildResult.error_type spellings (languages/_common.py): MISSING_ENTRY = "MissingEntryFile" (every runtime, threejs'
    export_glb.mjs included) · BUILD_TIMEOUT = "BuildTimeout" (compose_build_result, threejs, urdf)
```
| runtime | entry_globs | notes / artifacts |
|---|---|---|
| `BlenderRuntime` | `src/model.py`, `src/parts/*.py` | **Δ multi-file**: `file_for_part(name) -> "src/parts/<snake>.py"` (`def build_<snake>()`); lint = `layout.lint_workspace` over every src/*.py; skeleton writes model.py + per-part files; object.glb/stl, build.json, census.json |
| `CadQueryRuntime` | `src/model.py` | trailing selector on the stack → parent solid exported + warning (ExportError when no solid exists); helper-module errors map to `src/<file>.py:line`; object.glb/stl/step |
| `ThreeJsRuntime` | `src/object.js`, `src/parts/*.js` | **Δ export as authored** (`--normalise` opt-in, never passed by the runtime; census `placement_offset`/`normalised_offset`); InstancedMesh baked to `<Name>_<i>` meshes (`instanced_meshes_baked`); exported `selfcheck(THREE, root)` is called (throw → SelfCheckError); NaN geometry errors name mesh/part → routed to `src/parts/<snake>.js` |
| `UrdfBlenderRuntime` | `src/model.py`, `src/robot.urdf` | object.glb (Y-up, node=link, joint extras), meshes/<link>.glb (raw Z-up link frames); link name `world` is reserved (lint ERROR + UrdfError); robot GLB root gets `__root` suffix on name clash |
| `SceneThreeJsRuntime` | `src/scene.js`, `src/zones/*.js`, `src/assets/*.js`, `src/env.js`, `src/shaders/*.js` | glb_path=None; ok iff `probe_scene` + `check_shaders` pass |
| `GlslShaderRuntime` | `src/shader.frag`, `src/common.glsl`, `src/buffer_a.frag` (+ the harness-owned `src/recipes.glsl` when seeded) | harness owns `#version`/uniforms/`out` (wrap.HEADER: u_time/u_resolution/u_mouse/u_frame/u_prev/u_noise + iTime/iChannel* aliases); `wrap.compose(shader, common, recipes_src=…)` pastes header < recipes < common < shader; build renders judge frames via GlHost; compile errors → GlslCompileError at mapped src file:line (recipes.glsl included); lint ERROR `redefines_recipe` when an agent file defines a recipes.glsl name; artifacts frames/, frames_sheet.png, preview.gif, metrics.json |
| `OpenGLPythonRuntime` | `src/program.py`, `src/*.glsl` | `setup(ctx,w,h)->state` + `render(ctx,state,t,frame,fbo)` run in a moderngl subprocess (`wrappers/run_gl.py`); exceptions map to src/program.py:line, in-string GLSL errors carry both line numbers |

Wrappers are standalone (never import codeverse): `blender/wrappers/run_bpy.py`
(+ sibling `_census.py` — copy both), `cadquery/wrappers/run_cq.py`,
`urdf/wrappers/run_bpy_links.py`, `opengl_python/wrappers/run_gl.py`; threejs/scene
export+render live in `runtime_js/` (`export_glb.mjs`, `render_glb.mjs`,
`render_scene.mjs`, `probe_scene.mjs`, `check_shaders.mjs`, `lib/instances.mjs`).
**Placement policy (all languages)**: nothing re-centres or drops to ground at
export; the build warns and the contract/connectivity gates report it.
**Δ Node ESM resolution**: bare `import 'three'` needs
`run_node(..., three_hook=True)` (`--import runtime_js/lib/resolve_three.mjs`;
`module.registerHooks` on node >= 22.15, `module.register` +
`lib/resolve_three_async.mjs` down to the 20.6 floor — same resolutions).
**Δ Node floor**: `spatial.node.NODE_MIN = (20, 6, 0)` is the single source of truth
(`runtime_js/package.json` `engines.node` mirrors it).  `run_node` calls
`require_node_version()` before spawning, so an old node raises `NodeError` with the fix
in the message instead of failing obscurely; `parse_node_version` / `node_version_error`
are the pure helpers `3dcv doctor` reuses for its `node` row.

## spatial/

`tool_common.glb_path` refuses (`ToolUsageError`) when the newest of
`build_last.json`/`build.json` says the last build failed — tools never measure or
texture a stale GLB; missing/unreadable status stays permissive (hand-placed GLBs).
```python
from codeverse.spatial.render import render_glb
from codeverse.contracts.artifacts import RENDER_MODES   # ('shaded','wire','normals','silhouette','clay') — THE mode tuple (no 'depth')
render_glb(glb, out_dir, *, views=None, mode="shaded|wire|normals|silhouette|clay", width=768, height=768,
           isolate=None, explode=0.0, sheet=True, background=..., anim_time=None, shadow=True, gpu=None,
           timeout_s=None, use_cache=True) -> RenderSet
from codeverse.spatial.render_scene import render_scene
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
    # Δ Measurement.extra["complexity"] = ComplexityVector.model_dump() (additive, best-effort, never raises)
from codeverse.spatial.complexity import (ComplexityVector, COMPLEXITY_WEIGHTS, COMPLEXITY_VERSION,
                                          complexity_of_glb, complexity_of_parts, band_of)
complexity_of_glb(glb) -> ComplexityVector     # part_count, assembly_depth, tri_count, materials, silhouette,
    # feature_density, symmetry_groups, hollowness, + index 0-1 (documented weights) and band
    # (trivial|simple|moderate|complex|intricate).  Deterministic, no VLM/render.  docs/COMPLEXITY.md
from codeverse.spatial.connectivity import check_connectivity   # (glb, *, gap_m=…, …, language="", planned_edges=()) — planned_edges: (child, parent | (copies…)); Δ language selects the
from codeverse.spatial.contract import check_contract           # frame of fix hints; both gates emit hints in the AUTHORING frame
                                                                # (labelled "blender frame: Z-up, -Y front" etc.), GLB vectors in data
    # (Δ 2026-08-30) connectivity measures overlap WHERE it is (a dense pass on the AABB-overlap region), so a thin
    #   member's tip is no longer diluted under PENETRATION_MIN_FRACTION; every interpenetration finding carries
    #   data{kind="penetration", other, entering, container, depth_m, fraction_inside, local_fraction, inside_count,
    #   through_ratio (2·depth/thickness of the part entered — MEASURED, never a severity), thickness_m}; severity is
    #   depth alone (2 mm WARN / 10 mm ERROR).  One INFO "contact ledger" finding closes every report:
    #   data{parts, contacts [[a,b,gap_mm]], overlaps [[a,b,depth_mm,through]] incl. sub-threshold welds, ground_gap_mm{part},
    #   planned [[a,b,gap_mm,"contact"|"open"]] for the plan's attach_to pairs (spatial/contract.planned_joins — a child against
    #   every parent copy its box touches; the gate keeps each CONTACT row, else the nearest), planned_unresolved}.
    # (Δ 2026-08-30) check_contract adds ONE orientation finding on a StaticPlan (data.kind="orientation", pose lying|stood → ERROR,
    #   turned → WARN; planned_up_m, measured_up_m, best_axis): an extents-permutation test in the plan frame, 0 flags over
    #   629 corpus measurements, fires on brilliana's c-clamp / gate-valve.
from codeverse.spatial.measure import measure_glb, world_transform, node_name_findings   # (Δ 2026-08-30) world frames are composed
    # by walking the graph's edge matrices (trimesh's get() dropped a root pivot's rotation); a GLB with duplicate / unnamed nodes
    # lands a finding in Measurement.extra["findings"] — trimesh re-parents renamed nodes and the numbers are approximate
from codeverse.spatial.scene_placement import check_placement, placement_findings, placement_gate_safe, placement_census, placement_table_text
check_placement(ws, *, indoor=None, force_probe=False) -> GateReport   # gate "scene_placement"; data.kind ∈ floating | sunken |
    # unsupported | interpenetration | summary | probe_failed; target "Zone/Asset" (routes to src/zones/<zone>.js);
    # messages carry the scene_v1 floating_part cap words; reads artifacts/census.json["placement"] (host_placement.mjs)
placement_gate_safe(census, *, plan=None) -> GateReport | None       # round gate: None without a table, WARN on failure, never raises
from codeverse.spatial.scene_placement import setting_text          # (plan: dict | model) -> the indoor/outdoor setting line
from codeverse.spatial.silhouette import compare_silhouette
from codeverse.spatial.sections import judge_slices, SliceManifest, JUDGE_SLICE_PLANES   # (D48)
judge_slices(glb, error_pairs, out_dir, planes=("front_back","left_right")) -> SliceManifest   # two vertical centre slices,
    # red hatch ONLY on error_pairs (the connectivity gate's ERROR penetration pairs), plain darkened blend otherwise,
    # degenerate slices dropped (F4) → 0–2 PNGs (slice_<name>.png, manifest.json beside them; JudgeSlice.png is a bare
    # file name); needs the mesh extra (shapely + matplotlib), ImportError propagates; a bad GLB → manifest.errors, no raise
from codeverse.spatial.joints import load_urdf, fk, sweep_collisions, urdf_to_glb, render_poses   # RESERVED_LINK_NAMES={'world'}
from codeverse.spatial.joints_collide import components   # (names, edges) -> list[set[str]]: THE union-find (connectivity + sweep)
# the joint_sweep TOOL body lives in spatial/tools.py (no joint_sweep_observation helper; render_poses takes no renderer=)
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
    # scene_views + check_placement [scene], gl_probe + gl_frames [graphics], texture_pass + texture_preview [object tracks]
python -m codeverse.spatial.mcp_server --workspace <ws> [--track X] [--language Y] [--round N] [--list]   # MCP name: 3dcv
```

## judges/
```python
from codeverse.judges.rubrics import load_rubric, Rubric   # Rubric{…, defects: [DefectItem{id, text, penalty, cap}], caps[{…, when:, kinds}]}
from codeverse.judges.vlm_judge import VlmJudge
VlmJudge(rubric="static_object_v1", model_id=None (settings.default_judge = gemini-3.1-pro-preview), n_samples=1,
         temperature=0.2, *, thinking="low", max_attempts=3, max_montages=3, detail_crops=2, max_px=1024, sample_budget_s=240,
         chat_model=None, cache_dir=None, label="judge")            # Δ max_images is GONE → montage budget
VlmJudge.judge(inp) -> Judgment                # clay/normals views travel ONLY as JudgeInput.geometry_views (no kwarg)
VlmJudge.slice_payload(inp) -> (list[(label, png_path)], provenance_elicitation: bool)   # (D48) ([], False) unless
    # Settings.judge.slices=="on-error" AND spec.track in judges.base.SLICE_TRACKS AND inp.glb_path exists AND the
    # connectivity gate has ≥1 ERROR; renders judge_slices into the judge cache (keyed by glb identity + error pairs)
from codeverse.judges.base import JudgeInput   # (spec, renders, measurement=None, gates=[], acceptance=[], plan_summary="",
                                               #  round_index=0, previous=None, extra_context="", geometry_views=None,
                                               #  glb_path=None (D48: the round's canonical GLB; object tracks + 3dcv judge fill it))
from codeverse.judges.prompt_builder import plan_montages, render_montage, Montage    # ≤3 2×2 montages (shaded/geometry/poses) + ≤2 detail
    # crops @≤1024px replace sheet+9 views; clay/normals views (RenderView.mode) auto-route to the GEOMETRY montage
from codeverse.judges.prompt_builder import build_judge_messages, connectivity_error_pairs, PROVENANCE_ELICITATION
build_judge_messages(inp, rubric, *, …, extra_images=None (PREpended: references), extra_text="",
                     slice_images=None, provenance_elicitation=False) -> (system, [ChatMessage])   # (D48) slice_images
    # (label, path) are APPENDED after the montages/crops + described by slice_rig_section in the view-rig text;
    # provenance_elicitation appends one sentence to the DEFECT CHECKLIST bullet.  Defaults build the byte-identical
    # pre-D48 payload; judge_prompt_hash(rubric) is unchanged either way (per-round content stays outside the hash)
connectivity_error_pairs(gates) -> list[(a, b)]   # the gate's ERROR penetration pairs via finding.target/data.other
from codeverse.judges.rubrics import is_degraded, aggregate_samples, SCORING_VERSION   # ScoreBreakdown adds defects,
    # defect_votes (majority; a defect tie → absent, an acceptance tie → representative sample, D36 as amended
    # 2026-08-30), tie_broken, defect_penalty, overall_after_defects, overridden (defects the measured-absent
    # veto switched off), scoring_version (= SCORING_VERSION, 2; older records carry 0), judge_prompt_hash (D37);
    # overall = caps(weighted_mean − Σpenalty)
    # CapRule gains measures: list[str] (the checklist defect ids a gate rule is the MEASUREMENT of —
    # penetration_error.measures = [interpenetration]) and graded: bool (an acceptance rule caps at
    # cap + (1−cap)·verified/total instead of a flat cap); rubrics._rule_evidence is now _rule_hit -> CapApplied | None
from codeverse.judges.rubrics import apply_caps           # (rubric, overall, gates, acceptance_results, acceptance_items=None, *,
                                                       #  console_errors=None, views=None, defects_present=None) -> CapResult;
                                                       # cap rules add when="missing_views" and ledger lines "defect:<id>"
from codeverse.judges.pairwise import PairwiseJudge    # .compare(spec, renders_a, renders_b, *, rubric=…) -> PairwiseResult
PairwiseJudge.compare_many(spec, candidates: list[RenderSet], *, rubric) -> RankingResult{order, points, confidence, pairs, usage, .best}
from codeverse.judges.calibration import calibrate, CalibrationTable   # (run_dirs, *, model_id, n_samples=3, out_dir, geometry_mode,
    # rounds, …) -> rows + pearson/spearman(errors vs score), mean_std, cost; CLI: python -m codeverse.judges.calibration RUN… --n 3
from codeverse.judges.vlm_judge import ReferenceJudge  # image-conditioned specs
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
# (CLI --candidates > spec.options.candidates > settings.default_candidates); StaticObject | Articulated | Scene | Graphics
TrackPipeline.run(spec, ws, *, resume=False, force=False) -> RunRecord
from codeverse.orchestrator import RoundPolicy, StopPolicy, StopDecision, BestSelector, judge_sigma, \
    best_score, last_gain, REWRITE_KIND, build_refine_instructions, compact_instructions
RoundPolicy(max_rounds=4, plateau_window=2, min_delta=0.02, target=0.8, judge_on_gate_errors=True, max_refine_tasks=6,
            max_instructions_per_task=6, parallel_min_tasks=2, n_candidates=1, pairwise_margin=0.03,
            pairwise_min_confidence=0.6, judge_samples=1,
            judge_model="", regression_sigma=1.0, regression_allow_switch=True,   # money stops (docs/COST.md §5)
            marginal_sigma=1.5, marginal_from_round=3,                            # r03+ must beat 1.5σ
            agent_max_turns=0, agent_wrapup_turns=6,     # 0 = the backend's own AgentJob.max_turns (claude-code 60
            # +6 wrap-up; gemini-cli/codex/agy no turn cap); a 28-turn cap was A/B'd and rejected (+$0.02, −0.21
            # score, docs/COST.md §17); wrapup applies to a cap a caller sets
            detail_rounds=None, detail_min_score=0.45, detail_bbox_tol_m=0.005   # surface-detail round, tri-state:
            # None = the track default (lifecycle.DEFAULT_DETAIL_ROUNDS=1 when supports_detail_round, else 0),
            # 0 = off, N = N; $CV3D_DETAIL_ROUNDS overrides all (the A/B switch).  Injected policies keep it.
            )   # lifecycle.build_context binds n_candidates + judge_model with ONE dataclasses.replace;
                # RoundPolicy.target is ALWAYS overridden by the rubric pass threshold in BaseTrack.after_plan
policy.sigma / .regression_delta / .marginal_delta   # judge_sigma() reads cost.routing.JUDGE_NOISE — THE σ table
StopPolicy(policy).evaluate(history, budget_ok=True) -> StopDecision(reason, strategy="same"|"switch"|"detail", detail)
    # (.reason is the StopReason); strategy "switch" = ONE whole-artifact rewrite round (kind REWRITE_KIND),
    # "detail" = ONE surface-detail round (kind DETAIL_KIND) — a plateau/diminishing stop is converted into it
    # only when detail_blocked(history, policy) == "" (clean gates, built, judged, within σ of best, budget left)
from codeverse.orchestrator import DETAIL_KIND, KIND_FOR_STRATEGY, detail_blocked   # THE strategy → kind dict
from codeverse.orchestrator import BudgetGuard, usage_delta
BudgetGuard(budget, start_time=None, *, soft_fraction=1.0)            # no run= / ledger=
    .charge(usage, *, stage="other", enforce=True)   # (Δ 2026-08-30: role/label/round_index/outcome gone)
    # THE door every dollar goes through: accumulate → bucket by stage → enforce the ceilings.
    # The per-call record (role, label, round, outcome) is the LEDGER row; the guard bucketing them a
    # second time fed nothing but itself.
    # It writes NO ledger row: cost.instrument.MeteredAgent (one source='session' row per CLI session) and
    # MeteredChatModel (one row per call) are the only writers of telemetry/cost.jsonl.
    # add(...) = charge(enforce=False) — "not enforced" never means "not seen".
    .by_stage / .stage_summary() / .mark()   # what a round burned, live (by_round[i] deleted — no reader;
    #                                          per-round money is CallCost.round in telemetry/cost.jsonl)
from codeverse.tracks.candidates import CandidateRecord, rank_candidates, decide_best   # pure decision logic
from codeverse.tracks.candidates import run_best_of_n, choose_best_round, quick_render   # N parallel baselines in <ws>/_cand/c<k>
# each candidate IS steps._run_round(kind="candidate") in its sub-workspace: render=quick_render(ctx, round_index, build,
# measurement, *, pipeline) (4 views + the articulated pose views), geometry_views=False, its own events.jsonl and a
# one-sample judge → _cand/c<k>/judge/r00.json (a degraded verdict stays score None); crashed candidate retried once;
# selection by build_ok → quick score → fewer gate errors, pairwise within margin (booked stage=Stage.PAIRWISE);
# winner copied back, normal r00 pipeline follows; rounds/candidates.json IS record.extra["candidates"] (n, selected,
# candidates[], pairwise)
from codeverse.tracks.articulated_object import default_motion_checks, expected_direction   # gate "motion_direction"
from codeverse.tracks.static_object import silhouette_gate, reference_refine_tasks  # gate "reference_silhouette" (IoU<0.6 → WARN + refine task)
from codeverse.tracks.depth import depth_budget, DepthBudget, scope_groups, PartScope, interfaces_text, \
    scoped_generation_enabled                          # complexity-aware budgets + per-part scoped generation
depth_budget(plan, *, build_timeout_s=300) -> DepthBudget   # min/target/max triangles + max_build_s sized from
    # the plan's LEAF count (parts × instances × children); .as_prompt() is the DETAIL BUDGET block every
    # generate/refine/detail template shows in place of a flat "≤ 300k tris"
scope_groups(plan, *, files_for, max_groups=6, parts_per_scope=3, min_parts=8) -> [PartScope]
    # [] = one session owns the object (small plan, no per-part file ownership, or $CV3D_SCOPED_PARTS=off);
    # otherwise attachment-subtree groups whose files are disjoint, so the sessions run in parallel
interfaces_text(plan, scope) -> str    # the planned boxes of the neighbours this scope must weld to
from codeverse.tracks.static_object import drift_gate, detail_instructions, DRIFT_GATE   # gate "detail_drift":
    # ERROR when a detail round moved/resized/removed a part or changed the overall extents (tol from policy)
from codeverse.tracks.prompting import base_prompt_context, reference_images, file_for_target_factory, \
    scope_context, budget_for, detail_budget_text      # Δ split out of
from codeverse.tracks.prompting import select_cookbook_chapters, is_always_chapter
    # select_cookbook_chapters(ctx, brief, *, budget=9000, always=COOKBOOK_ALWAYS) -> list[Section]: the header +
    # always-on chapters + the brief's chapters (whole, cookbook order, inside budget)
from codeverse.tracks.graphics import seed_recipes, graphics_brief, cookbook_functions, EXTRA_KEY, RECIPES_REL
    # seed_recipes(ctx) -> list[str]: glsl_shader + seed_recipes_enabled() only.  Writes the selected chapters'
    # function definitions (minus always-on chapters and the raymarching template) + the helpers they call to
    # the HARNESS-OWNED src/recipes.glsl (RECIPES_REL; header "// harness-owned: … READ-ONLY …"; a resume appends
    # only names the file lacks); returns the names written THIS call; ctx.extra["seeded_recipes"] =
    # [{name, signature, purpose}] for every seeded recipe on disk (the prompt block); emits recipes.seeded
    # {file, names, present, chapters, trimmed}.  Never writes src/common.glsl — except the untouched skeleton,
    # which loses the helpers recipes.glsl now provides (trim_skeleton_common; recipes are pasted first).
    # GraphicsTrack.prepare() runs it after the skeleton and commits "recipes" when it wrote something.
from codeverse.tracks.common import RunContext, Services   # common.py; RunContext.single_shot / .agent_kind
    # (Δ 2026-08-30) Services.connectivity(glb, language="", planned_edges=()) forwards the plan's attach_to pairs;
    #   spatial.contract.planned_joins(plan, measurement) -> [(child, (parent copies…))] spells them in GLB part names via
    #   spatial.contract.match_parts (instance copies Leg_0..n join the nearest parent copy — no regex on ids)
from codeverse.judges.prompt_builder import gates_section, contact_ledger   # (Δ 2026-08-30) contact_ledger(gates) finds the
    # connectivity report's INFO ledger; with it gates_section renders MEASURED STRUCTURE + one overlap line + planned joins
    # (LEDGER_MAX_CONTACTS 24, LEDGER_MAX_JOINS 20, ground line for parts within GROUND_BAND_MM of the lowest point whose gap
    # exceeds GROUND_GAP_REPORT_MM) and drops the per-pair penetration WARN prose; without it the pre-change text, byte for byte
from codeverse.tracks.generation import write_files        # (ws, files, *, allowed_roots, only=None, frozen=(), on_skip=None):
    # frozen = harness-owned paths a single-shot envelope may not rewrite (skipped with a reason, never an error)
from codeverse.agents.cli_common import find_json_object, default_mcp_command   # THE one JSON-envelope finder behind
    # parse_gemini_json / parse_claude_json / parse_agy_json; default_mcp_command(ws, *, language, track, round_index)
from codeverse.tracks.generation import generate, run_agent_task, parse_multifile, is_single_shot
GenerationTask.phase: int = 0   # tasks run in parallel WITHIN a phase, phases in ascending order
    # (tracks.steps.run_generation_tasks).  Only user: the scoped baseline — phase 0 = one session per
    # part group (`baseline_<parts>`, own files only), phase 1 = ONE `assemble` session that owns the
    # entry file and the placement gates.  Every other caller is phase 0, i.e. unchanged.
generate(ws, *, agent_id, task, ..., budget=BudgetGuard, max_turns=0, wrapup_turns=6) -> GenerationResult
    # GenerationResult adds turns / sessions / turn_capped.  A turn cap is applied ONLY if a caller
    # asks: task.max_turns > max_turns > $CV3D_AGENT_MAX_TURNS > settings.limits.agent_max_turns >
    # DEFAULT_AGENT_MAX_TURNS (0 = leave AgentJob.max_turns at the backend's own default — a 28-turn
    # default was measured and rejected, docs/COST.md §17).  A session that hits a cap that IS set is
    # asked for a final build + summary (WRAPUP_PROMPT) instead of being killed.
    # EVERY session (attempt 1, <label>.a2 retry, <label>.wrapup) is charged as it ends.
from codeverse.tracks.repair import build_with_repair   # RepairOutcome(.ok/.repaired/.max_attempts, attempts, usage)
from codeverse.tracks.steps import run_round, skip_judge_reason, emit_round_cost, record_aborted_round
skip_judge_reason(ctx, *, gates, renders) -> str    # "" = judge it.  ONLY states where the verdict is
    # never bought at all: no judge / no renders / budget already exceeded / gate errors with
    # policy.judge_on_gate_errors=False.  rejudge_round honours the last one too, so a policy skip is
    # never re-bought (docs/COST.md §17 — "no file change" and "build not repaired" were removed)
run_round(ctx, *, index, kind, tasks, pipeline, ..., previous_best=None, render=None, geometry_views=True) -> RoundRecord
    # render: RenderFn | None swaps the pipeline's render (candidates pass quick_render); geometry_views=False skips
    # the clay/normals views
    # emits cost.round {stages{}, judge_usd, total_usd, agent_turns, wasted, waste_reason}; on ANY exception it
    # records what the round burned (rounds/aborted_rNN.json, ctx.extra["aborted_rounds"]) and re-raises
from codeverse.tracks.planner import plan, plan_model_for, ensure_acceptance    # graphics uses tracks/graphics.plan_graphics
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
from codeverse.texturing.run import texture_pass, texture_requested, load_report
texture_requested(spec) -> bool   # THE owner of "does this run texture?" (Spec.options.texture, or
    # the legacy "texture" tag).  Asked by tracks.lifecycle.finalise AND by the texture_pass spatial
    # tool, which refuses in a run that did not ask — the tool is registered for every object track,
    # so `texture: false` used to be bypassable from inside an agent session.
texture_pass(ws, spec, plan, *, model_id, image_model=None, judge=True, judge_model_id=None, rubric=None,
             glb_in=None, views=OBJECT_VIEWS_QUICK, size=1024, ..., events=None, update_record=True) -> TextureReport
# 1 vision call material plan (cacheable) → tileable textures (mirror cross-fade, seam_score ≤ 0.08) → world-metre UV
# unwrap (planar/box/cylinder per part, tile_size_m) → artifacts/object_textured.glb → seam gate + before/after judge
# gate (ship iff Δoverall ≥ −0.01 AND materials criterion improved); record.extra["texturing"], events texture.*
from codeverse.texturing.plan import material_plan, default_plan, TexturePlan
from codeverse.texturing.generate import generate_textures   # (Δ) FakeImageModel / procedural_texture
    # moved to tests/texturing/conftest.py 2026-08-30 — no production path could construct them
from codeverse.texturing.plan import scene_texture_pack, texture_pack_prompt   # 6–12 named tiles + manifest.json
# under public/textures/ for scene prompts (URL /public/textures/<name>.png)
```

## flywheel/ + cli/
```python
from codeverse.flywheel.record import finalize_record, load_record, iter_runs, best_round_index, best_round_record
from codeverse.gallery.index import hero_view          # (ws, rec) -> (rel, label, n_views): the card image, rebased via ws
from codeverse.flywheel.record import complexity_block, round_complexity   # objective complexity of what shipped
    # finalize_record fills record.extra["complexity"] = the BEST round's vector + plan_parts /
    # parts_per_plan_part / by_round; every rounds_summary row gains "complexity" (the index or None)
from codeverse.flywheel.export import export_samples   # (runs_dir, out_dir, *, min_score=None, only_passed=False, best_round=True,
    # overwrite=True, include_unbuilt=False, captions_dir=None, drop_duplicates=False) -> ExportReport{…, n_duplicates, duplicates, tiers}
from codeverse.flywheel.quality import quality_tier, prompt_hash, find_duplicates   # tiers: A passed & 0 gate errors, B passed,
                                                                                    # C best ≥ 0.6, D else; dedupe = (code fingerprint, prompt)
from codeverse.flywheel.pairs import build_pairs       # (runs_dir, out_jsonl, *, min_delta=0.05) -> n
from codeverse.flywheel.captions import caption_sample # Δ (ws, record, model_id, *, model=None, out_dir=None) -> Captions;
                                                       # out_dir → side-car <out_dir>/<slug>.json, run untouched
from codeverse.gallery import build_index, default_roots, build_static, serve, GalleryApp   # THE local gallery
                                                       # build_index(roots) -> GalleryIndex (sections of RunEntry; never raises per run)
                                                       # build_static(roots, out_html, *, embed=False) -> (path, n, index)
                                                       # render_static(index, *, embed=…, extra_html="") — bench/report.py's page
                                                       # GalleryApp(roots, reload=False).route(path, query) -> Response  (pure, testable)
                                                       # serve(roots, *, host=None, host_explicit=False, port=8765, reload=False)
from codeverse.gallery.urls import safe_join          # (root, rel) -> Path inside root, else PathError
from codeverse.gallery.urls import content_type        # .glb→model/gltf-binary, .py/.js/.frag→text/plain; charset=utf-8
from codeverse.flywheel.index import build_index, query, summary   # sqlite + parquet: adds quality_tier, gate_errors, cost_usd,
                                                                   # rounds, status, code_fingerprint, prompt_hash, duplicate_of, has_captions
3dcodeverse make [--profile economy|balanced|quality]|resume|status|show|render|judge|tools|mcp
             |texture {pass,scene-pack,show}|cost {<slug>,show,cache,prices,profiles,estimate}
             |flywheel {export,pairs,caption,index,dedupe,gallery}|gallery {serve,build}
             |bench {run,report}|doctor    # alias: 3dcv
```

## Events and records
`EventLog.emit(event, **data)` writes `{"t", "event", ...}` (**Δ** key is `event`).
Event names: `run.start`, `stage.start/done`, `plan.done`, `workspace.materialized`,
`round.start`, `generate.done`, `build.done`, `gates.done`, `judge.done`,
`round.done`, `best.updated`, `refine.planned`, `recipes.seeded` (graphics: names written this call, present on disk, chapters), `asset.judged`, `assets.done`,
`zones.done`, `assemble.done`, `round.no_change`, `candidates.start`,
`candidate.start/done/failed/retry/selected`, `pairwise.done`,
`texture.start/plan/generated/applied/gate/done`, `budget.exceeded`,
`finalise.rebuild`, `stop`, `run.done` / `run.failed`.
Cost events: **`cost.round`** (per round: `stages{stage → $}`, `judge_usd`, `total_usd`,
`agent_turns` (= AgentResult.turns), `score`, `previous_best`, `wasted`, `waste_reason` ∈ aborted | build_failed |
unjudged | regression | zero_delta, `run_usd`) — emitted for aborted rounds too;
`judge.skipped` (reason), `generate.turn_cap` (label, max_turns, turns, cost_usd),
`strategy.switch` (regression → whole-artifact rewrite), `budget.overrun` (a post-loop
texture pass crossed the ceiling; the loop is already finished, so not `budget.exceeded`).
`RoundRecord` gained `pairwise: PairwiseNote | None` (Δ 2026-08-30) — the paid tie-break
verdict, stored so `reconcile_resume` REPLAYS it (`candidates.replay_best_round`) instead
of re-ranking on score and reversing a comparison the run bought.  `PairwiseNote` moved
from `codeverse.tracks.candidates` to `codeverse.contracts.run` (it is pure data).
`RunRecord` (record.json): spec, plan, workspace, status, rounds[RoundRecord],
best_round, baseline_score, final_score, total_usage, environment,
prompt_hashes{contract, cookbook, generate, refine}, error, extra{stop_reason,
rubric, budget, cost_by_stage, aborted_rounds?, rounds_summary, candidates? (the
rounds/candidates.json payload), texturing?, captions?}.  `total_usage` is the BudgetGuard total whenever it
exceeds the sum of the rounds (aborted rounds, retried sessions, texture pass).
