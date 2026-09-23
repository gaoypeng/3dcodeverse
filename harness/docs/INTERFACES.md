# Cross-package interfaces (as built)

This file lists the signatures other packages may rely on.  Types live in
`codeverse3d/contracts/`; protocols in `models/base.py`, `agents/registry.py`,
`languages/base.py`, `spatial/registry.py`, `tracks/__init__.py` (judges are
duck-typed `.judge(JudgeInput) -> Judgment`; `judges/base.py` defines no protocol).
Where the build deviated from the original plan the deviation is called out as
**Δ** — see `docs/DECISIONS.md` for the why.  When in doubt the code wins; this
file was last checked against it on 2026-09-22, and every `codeverse3d` name it cites is
test-pinned (`tests/core/test_interfaces_names.py`).

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
from codeverse3d.contracts.common import TRACK_INFO, TrackInfo   # {Track: TrackInfo(rubric, label, best_of_n)} — THE track registry
from codeverse3d.contracts.common import ENTRY_FILE, code_file, LANGUAGE_LABEL   # {Language: "src/<entry>"}; code_file(lang) -> "code.<ext>"
from codeverse3d.contracts.spec import RunOptions                # Spec.options: candidates (int|None, ≥1), texture (bool)
GateFinding.as_line(with_gate=False, with_severity=False, with_target=False, with_hint=True) -> str
    # "GATE <gate>: [<sev>] <message> [<target>] FIX: <hint>" — flags opt in; no leading "- "
GateReport.of(gate, findings=(), *, duration_ms=0) -> GateReport   # passed iff no ERROR — every gate whose verdict IS its findings
BuildResult.gates: list[GateReport]   # the build's own reports (scene_probe + shader_preflight; gl_frames);
    # tracks/steps appends them to the round's gates, after the track's, built or not
RenderView.judge: bool | None      # stamped True/False at render time; None = legacy round (every stored view is judged)
RenderSet.out_dir: str             # directory the views (+ views.json/metrics.json) were written to ("" on old rounds)
from codeverse3d.config import get_settings, Flag           # every C3D_* switch is a Settings field (C3D_X -> .x,
    # C3D_S__X -> .s.x, Settings.FLAT for four flat spellings); Flag: on/off/1/0/true/false/yes/no, any case;
    # unset/empty -> the default; garbage -> warning + the DEFAULT, never a crash
get_settings().backends(planner=..., generator=..., judge=..., captioner=...) -> Backends
    # settings defaults (default_planner/... mirror contracts Backends literals; + default_captioner);
    # truthy keyword overrides win, None/"" falls through, unknown role -> TypeError
from codeverse3d.proc import ProcResult, run_subprocess, tail, write_json_atomic
run_subprocess(cmd, *, cwd, timeout_s, env=None, stdin_text=None, preexec_fn=None) -> ProcResult
    # own session/process group, group-kill on timeout, never raises on rc != 0; stdlib-only module
from codeverse3d.proc import read_json_or_none, iter_jsonl_lines, read_jsonl_lenient, append_jsonl_line
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
from codeverse3d.models import get_chat_model            # (model_id) -> ChatModel, lru-cached, thread-safe
resp = m.generate(ChatRequest(messages=[...], system=..., response_schema=..., temperature=..., thinking=..., label=..., max_wait_s=None))
#   max_wait_s: the longest this ONE call may spend, retries included (None = models.retry.RETRY_DEADLINE_S = 1800 s);
#   GeminiModel clips its retry deadline to it; VlmJudge 240 s per sample, planner 300 s
#   resp.raw["key"] = "…ab12" (the key that answered), resp.raw["attempts"] = round-trips issued (hedged siblings included)
resp.parsed / resp.text / resp.usage   # Usage always has cost_usd (models.pricing)
from codeverse3d.models.retry import KeyPool, KeyPoolExhausted
KeyPool(keys, *, max_in_flight=0, slots_dir=None, cooldown_s=3, dead_cooldown_s=3600)   # a cap needs slots_dir:
    # the slots are flock'd files every process shares (Slots(n, root): try_take / take(timeout) / give / busy)
pool.acquire(*, exclude=None, timeout_s=120) -> key    # a slot + a key; raises immediately when every key is dead/cooling past the deadline
pool.try_acquire(*, exclude=None) -> key | None        # never waits (a hedged retry's extra key); holds a slot like acquire
pool.session_key(*, exclude=None, timeout_s=120) -> key   # a vendor CLI session's key: no slot, nothing to release
pool.report(key, "ok"|"429"|"5xx"|"error"|"dead"|"skip", *, retry_after_s=None)   # Δ "dead": health 0, benched dead_cooldown_s,
    # re-probed after; "skip" (a content failure the key did not cause) leaves health and counters untouched
from codeverse3d.models.retry import rotate_with_retries, RETRY_DEADLINE_S
rotate_with_retries(pool, call, *, classify, outcome_of=failure_outcome, max_attempts=6, max_total_s=RETRY_DEADLINE_S, hedge=2, ...)
    # the ONE retry loop; the SDK adapters run it over a one-key pool (parts.retry_one_key: no cooldown, no storm
    # patience, no hedge).  max_total_s = ChatRequest.max_wait_s clipped to RETRY_DEADLINE_S; a raised ModelError carries .attempts
from codeverse3d.models.pricing import estimate_cost       # (provider, model, usage) -> usd (unknown model -> 0.0 + one warning)
from codeverse3d.models.pricing import openai_usage       # (backend, model, *, prompt, cached, completion, reasoning, **extra)
    # -> a priced Usage: the OpenAI SDK adapter and codex both; reasoning moves out of completion into thoughts
from codeverse3d.models.registry import build_chat_model   # (model_id, **constructor kw) -> the bare, UNMETERED model
from codeverse3d.models.schema_utils import to_gemini_schema, to_openai_strict_schema, to_anthropic_schema, parse_json_lenient
from codeverse3d.models.schema_utils import ask_structured   # (model, Schema, *, system, text, images=(), temperature, label)
    # -> (obj | None, Usage, err): one schema-bound call; call failure and parse failure are the same `err` family
```
Backends: `GeminiModel(model, *, keys=None, pool=None, ...)` (one shared KeyPool per
key list), `AnthropicModel(model)` (a response schema is a forced `submit` tool),
`OpenAIModel(model)` (Chat Completions; key and `C3D_OPENAI_BASE_URL` from Settings).
Rules that callers must know:
* **Δ** Gemini key handling: auth/permission errors (401/403, or 400 with
  API_KEY_INVALID / expired / PERMISSION_DENIED / suspended markers) classify as
  outcome `dead` — the call rotates to the next key at once (no retry budget, no
  sleep) and the key is benched for an hour once another key succeeds; it raises
  only when *every* key fails the same way.  A 429 while an untried key remains is
  a **free** rotation (does not consume `max_attempts`); only when all keys are
  throttled do 429s count against the budget with backoff.
* Gemini 3.x cannot disable thinking; keep `max_output_tokens ≥ ~1000` even for
  one-word answers.
* Anthropic 4.6+ models use adaptive thinking + `output_config.effort`.
* **Δ** `to_openai_strict_schema` never adds `null` to defaulted fields: a property
  is nullable iff the *source* pydantic schema is (`T | None`); non-null defaults
  are surfaced as a `[default: …]` description hint.  Wire contract == pydantic contract.
* **Δ** `pricing.lookup_price` prefix fallback matches only pure version/date/channel
  suffixes (`-`/`_`/`@`/`:` + digits/latest/preview/exp/beta/…); sibling models
  (`-nano`, `-mini`, `-codex`, `-lite`) never inherit the parent's price — they are
  unknown (0.0 + warning) unless a row exists.

## cost/  (ledger · profiles · price provenance)
```python
from codeverse3d.cost import record_call, load_ledger
from codeverse3d.cost.ledger import summarise
record_call(usage, *, run="", round=None, stage=None, role=None, label="", backend="", model="",
            outcome="ok", latency_ms=None, n_calls=1, source="live", ledger=None, reprice=False,
            tallies=None) -> CallCost
    # Δ everything left out is resolved from the ambient context + the call label (cost.context);
    # unknown model -> $0 and price_source="unknown" (flagged, never silently dropped); never raises.
    # books every non-attempt row into the open cost.tally tallies (or `tallies`, captured by a caller
    # whose row lands later in a context-less thread).  source: live | session | extra (a round-trip the
    # provider billed and the call discarded — counts) | attempt (forensics — load_ledger leaves it out)
from codeverse3d.cost.ledger import ledger_usage   # (rows) -> Usage: how record.total_usage is read off the ledger
from codeverse3d.cost.instrument import (run_ledger, metered_chat_model, metered_agent,
                                       MeteredAgent, MeteredChatModel)   # always on: no off switch
with run_ledger(ws.root, run=slug):        # binds the run, points record_call at <run>/telemetry/cost.jsonl
    ...                                    # (its one name: no root alias since 2026-09-22)
    # NESTS by holding ContextVar tokens (bound_run / bound_ledger) — context-local ONLY, no
    #   process-wide default — so bench.run_bench / compare_backends hold one ledger per prompt
    #   across N threads, and a thread that may bill a model goes through proc.fan_out.
    #   BaseTrack.run opens `run_ledger(ws.root, run=ws.root.name)` ITSELF, so a track run started
    #   outside the CLI/bench still meters; the CLI/bench ledger for the same run dir is the same file.
    # models.get_chat_model returns a MeteredChatModel → ONE row per ChatModel.generate
    # agents.get_coding_agent returns a MeteredAgent (registry wrap, from the first call) → ambient
    #   round/stage for the session and one source='session' row (n_calls, latency, outcome,
    #   AgentResult.turns) — every backend is a vendor CLI whose calls the harness cannot see.
    #   These two (+ the image model's own row) are the ONLY ledger writers; BudgetGuard keeps no money.
    #   The rule is the BACKEND, never "did a row get written while it ran": a CLI session with one
    #   in-process tool call used to be dropped entirely.
from codeverse3d.cost.context import CallContext, call_context, bound_run, context_from_label, SELF_DESCRIBING
from codeverse3d.cost.ledger import bound_ledger, process_ledger_path   # (Δ 2026-08-30) both are context
    #   managers holding a ContextVar token; there is no process-wide default any more — a thread that
    #   may bill a model is spawned through proc.fan_out (which copies context), never a bare pool.
    # precedence: explicit > a label naming a job of its OWN (SELF_DESCRIBING = plan/judge/pairwise/
    #   texture/caption) > ambient (call_context) > the rest of the label > nothing.  A judge or a
    #   texture pass that bills a model INSIDE an agent session is filed under its own stage, not the
    #   session's; a generation label yields to the session (best-of-N: kind="candidate" beats
    #   label="baseline"); Stage.OTHER means "the label said nothing" and displaces nothing.
    # labels understood: judge:<rubric>:r<NN>:s<k> · planner · pairwise:… · texture… · caption… (api-agent:<label>:t<turn> in historical ledgers only)
from codeverse3d.addons.costreport.caching import session_cache, session_key
session_cache(rows) -> [SessionCache]      # per session: cold first call, cached share, saved_usd, cold_usd
from codeverse3d.cost.profiles import get_profile, PROFILES   # economy | balanced | quality
get_settings().apply_profile(name, *, force=False) -> Profile
    # sets default_{generator,planner,judge,captioner}, default_candidates and Settings.judge.samples
    # (never the judge payload or the turn cap); a value the user stated in
    # config.yaml / C3D_* survives unless force (3dcode make --profile forces).
from codeverse3d.cli._common import resolve_dial, ResolvedDial   # THE resolver, one per `3dcode make`
resolve_dial(settings, profile_flag=None, *, rounds=None, candidates=None,
             max_minutes=None, texture=False) -> ResolvedDial
    # profile/generator/planner/judge/captioner, judge_samples/judge_max_px/judge_montages/
    # judge_detail_crops, agent_max_turns, rounds, candidates, texture, max_minutes.
    # `--profile X` and `C3D_PROFILE=X` resolve to the SAME dial (they used to disagree on
    # candidates + texture); an explicit flag beats both.  Spec.options.profile always records the
    # resolved name so `3dcode resume` re-applies it.
from codeverse3d.models.pricing import price_provenance    # (provider, model) -> PriceRow(price, match, status, checked)
```
`3dcode cost <slug|path>…` · `3dcode cost --runs-dir <root>` · `3dcode cost cache <slug>` ·
`3dcode cost prices [--stale] [--days N] [--unverified]` · `3dcode cost profiles` · `3dcode cost estimate`.
Every run is read from its ledger alone (`addons.costreport.audit.read_run` / `read_cell` /
`find_runs`); `cost/reconstruct.py` and its rebuild of pre-ledger runs went on 2026-09-22.

## agents/
```python
from codeverse3d.agents import get_coding_agent           # (agent_id) -> CodingAgent  .run(job) .available() .id .kind .model
from codeverse3d.agents.materialize import materialize_workspace, codex_mcp_overrides
materialize_workspace(ws, *, agent_kind, contract_md, cookbook_text, spatial_tools, mcp_command=None) -> None
# cookbook_text = RunContext.cookbook_text, the catalog's text (Δ 2026-09-22: was cookbook_rel, resolved workspace-first,
# so an agent-written <ws>/<lang>/cookbook.md shadowed the harness cookbook); "" → "No cookbook is available" + a warning
# mcp_command defaults to cli_common.default_mcp_command(ws) = [sys.executable, -m codeverse3d.spatial.mcp_server --workspace …]
# (None/[] = the default — no ValueError); tracks/common.Services.materialize takes no mcp_command
# writes AGENTS.md + GEMINI.md + CLAUDE.md (same body), ws/.3dcode/cookbook.md (Δ written in: gemini-cli cannot read
# outside the workspace), .geminiignore/.aiexclude, and MCP wiring:
#   gemini-cli → ws/.gemini/settings.json only gets context.fileFiltering.respectGitIgnore=false; the 3dcode server
#     and mcp.allowed live in the per-session system settings (Δ that file is agent-writable — audit 2026-08-27)
#   claude-code → trajectories/<label>_rNN/mcp.json      codex → codex_mcp_overrides(mcp_command) built PER SESSION:
#     -c mcp_servers.3dcode.command=… -c mcp_servers.3dcode.args=[…] -c mcp_servers.3dcode.default_tools_approval_mode="approve"
#     (Δ without the approval mode every MCP tool call is elicited and auto-cancelled)
#   agy → no per-workspace MCP; body documents `3dcode tools <name> --json … --workspace .` as the fallback
res = agent.run(AgentJob(workspace=..., prompt=..., label="baseline", timeout_s=1800, max_turns=60,
                         spatial_tools=True, write_roots=["src","public"]))   # typed kwargs only: no extra=, no model=
res.ok, res.exit_reason  # completed | timeout | error | budget | model_substituted
res.turns                # claude-code num_turns · codex turn.completed count · agy num_turns · gemini-cli 0 (not on the wire)
res.files_changed (git-derived, attributed per session), res.usage, res.transcript_path, res.tool_calls, res.errors
res.transient            # a provider failure a retry may get through (5xx / overloaded / 429 / dropped connection)
res.quota                # Δ 2026-09-22 the vendor's usage limit is spent — never also transient
res.provider_wait_s      # Δ seconds of duration_s lost to provider errors (cli_common.provider_wait; codex: 0.0)
# Δ 2026-09-22 the prompt reaches EVERY CLI on stdin (cli_common.invoke; no -p / --print / positional text,
#   codex "-"), byte for byte; the >100 kB task_prompt.md stub is gone.  The transcript's invoke row keeps
#   argv + stdin_bytes.  res.usage is the CLI's envelope; with none (killed / given up) gemini-cli's comes
#   from its chat record (read_gemini_chats) and claude-code's from its stream's per-message usage
#   (output a floor); result.json "usage_from" says which.  A session sees only the routed skill bundles:
#   claude --setting-sources project + CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1 + --settings skillOverrides;
#   codex -c skills.bundled.enabled=false; gemini-cli skills.disabled (its two built-ins); agy: no switch.
from codeverse3d.agents.cli_common import is_transient_failure, is_quota_failure, is_rate_limited, provider_wait
# THE failure vocabulary (one place; the backends apply it to what the CLI said about the call that ended
# its session) and provider_wait(failures=[(t, backoff_s)], end, progress=[t] | None) -> seconds
```
`AgentJob` carries typed job context: `round`, `kind`, `language`, `track`,
`mcp_command` (override), `files_hint` (workspace-relative files/dirs the task is
expected to touch — used to attribute `files_changed`; harness-owned paths are dropped;
sessions on one workspace are serialised, so there is no sibling filter),
`edit_only` / `always_writable` (a scoped refine may overwrite only its hinted files +
the entry file), `read_only: list[str]` (harness-owned files INSIDE the write roots the
session may read but never write — `contracts.common.HARNESS_OWNED_SRC[language]`, i.e.
`src/recipes.glsl` for glsl_shader and `src/lib/` for scene_threejs; `tracks/generation.py`
sets it on every job of the run and `agents/cli_common._enforce_scope` reverts a
post-session write to one and fails that session.  **Δ** an entry ending in `/` is a
DIRECTORY prefix — `contracts.common.is_harness_owned(rel, owned)` is the predicate, and
it is what names the effect library without listing every module).
Each session writes `trajectories/<label>_rNN/` (files: docs/RUN_LAYOUT.md); **Δ** a re-run of the
same label+round lands in `<label>.a2_rNN` (then `.a3` …) — the first attempt is never overwritten;
`result.json` records `attempt` + `job_label`.  Each session makes two git commits (`pre:`/`agent:<label>`).
gemini-cli specifics: system settings file via `GEMINI_CLI_SYSTEM_SETTINGS_PATH`
(written per session into the trajectory dir: api-key auth, `dynamicModelConfiguration=true`
else silent model substitution → `exit_reason="model_substituted"`, `folderTrust.enabled=false`
else workspace MCP silently dropped, plus `mcpServers.3dcode` + `mcp.allowed=["3dcode"]` — **Δ** it is
applied LAST and `mcp.allowed` replaces, so an agent-planted server in ws/.gemini/settings.json is Blocked).  **Δ** `Usage.input_tokens` = `tokens.prompt` (TOTAL prompt incl.
cached; `tokens.input` is the uncached count) and each served model is priced at its
own rate.  KeyPoolExhausted never escapes `run()` (→ `exit_reason=budget`); retries
prefer a different key (10 s wait) before re-using the same one.
## languages/
```python
from codeverse3d.languages import get_runtime            # (Language | str) -> LanguageRuntime
rt.language; rt.entry_globs
rt.lint(ws) -> GateReport                              # gate = "lint:<language>"
rt.build(ws, *, timeout_s=None, **per_runtime) -> BuildResult   # Δ BuildResult.error_file is WORKSPACE-relative for
rt.skeleton(ws, plan) -> list[Path]                    #   every runtime ("src/model.py", "src/parts/leg.py", "src/helpers.py")
rt.expected_files(plan) -> list[str]                   # Δ 2026-09-22 THE file layout, asked by every track: the files a
rt.files_for(plan, target) -> list[str]                #   whole-artifact session writes (entry first) / the files that own a refine
    # target ([] = no owner → one whole-artifact task).  languages/base.RuntimeLayout answers for the object + graphics
    # runtimes (entry + extra_files, + part_file per part for blender / three.js); the scene runtime answers with
    # languages/scene_threejs.{SCENE_FILES, zone_file, asset_file, scene_files_for} (files_for(..., alias=) — the dedupe
    # note).  Gone: tracks/prompting.expected_files / file_for_target_factory / SCENE_FILES, graphics EXPECTED_FILES,
    # BaseTrack.round_files_hint, StaticObjectTrack.entry_files, scene.zone_file, scene_assets.asset_file,
    # BlenderRuntime.file_for_part (now part_file)
from codeverse3d.prompts.catalog import language_prompt, language_text
language_prompt(language, name) -> str   # "<dir>/<name>" under prompts/ (urdf_blender → urdf/): THE per-language prompt
language_text(language, name) -> str     # its text, "" for a file the language does not ship (Δ 2026-09-22: replaces
    # LanguageRuntime.contract_doc / RuntimeDocs, tracks/common.language_contract / cookbook_rel_for / load_prompt_or,
    # tracks/prompting.effects_catalog_text; planner.plan / build_system_prompt take no runtime=)
BuildResult.error_type spellings (languages/_common.py): MISSING_ENTRY = "MissingEntryFile" (every runtime, threejs'
    export_glb.mjs included) · BUILD_TIMEOUT = "BuildTimeout" (compose_build_result, threejs, urdf)
```
| runtime | entry_globs | notes / artifacts |
|---|---|---|
| `BlenderRuntime` | `src/model.py`, `src/parts/*.py` | **Δ multi-file**: `part_file(name) -> "src/parts/<snake>.py"` (`def build_<snake>()`); lint = `layout.lint_workspace` over every src/*.py; skeleton writes model.py + per-part files; object.glb/stl, build.json, census.json |
| `CadQueryRuntime` | `src/model.py` | trailing selector on the stack → parent solid exported + warning (ExportError when no solid exists); helper-module errors map to `src/<file>.py:line`; object.glb/stl/step |
| `ThreeJsRuntime` | `src/object.js`, `src/parts/*.js` | **Δ export as authored** (census `placement_offset`); InstancedMesh baked to `<Name>_<i>` meshes (`instanced_meshes_baked`); exported `selfcheck(THREE, root)` is called (throw → SelfCheckError); NaN geometry errors name mesh/part → routed to `src/parts/<snake>.js` |
| `UrdfBlenderRuntime` | `src/model.py`, `src/robot.urdf` | object.glb (Y-up, node=link, joint extras), meshes/<link>.glb (raw Z-up link frames); link name `world` is reserved (lint ERROR + UrdfError); robot GLB root gets `__root` suffix on name clash; the build collides the REST pose only (RestPenetration > 5 mm, D17) — every pose is the round's `joint_sweep` gate — and writes no articulation.json / census.articulation (Δ 2026-09-22) |
| `SceneThreeJsRuntime` | `src/scene.js`, `src/zones/*.js`, `src/assets/*.js`, `src/env.js`, `src/shaders/*.js` | glb_path=None; ok iff `spatial.probes.run_probe(ws, *, compile=True, timeout_s=None) -> (scene_probe, shader_preflight, census)` (one `probe_scene.mjs --compile` boot; `probe_scene` is the same call without `compile`) passes both; both reports ride `BuildResult.gates`, and the `shader_probe` tool reports the same call |
| `GlslShaderRuntime` | `src/shader.frag`, `src/common.glsl`, `src/buffer_a.frag` (+ the harness-owned `src/recipes.glsl` when seeded) | harness owns `#version`/uniforms/`out` (wrap.HEADER: u_time/u_resolution/u_mouse/u_frame/u_prev/u_noise + iTime/iChannel* aliases); `wrap.compose(shader, common, recipes_src=…)` pastes header < recipes < common < shader; build renders judge frames via GlHost; compile errors → GlslCompileError at mapped src file:line (recipes.glsl included); lint ERROR `redefines_recipe` when an agent file defines a recipes.glsl name; artifacts frames/, frames_sheet.png, preview.gif, metrics.json |
| `OpenGLPythonRuntime` | `src/program.py`, `src/*.glsl` | `setup(ctx,w,h)->state` + `render(ctx,state,t,frame,fbo)` run in a moderngl subprocess (`wrappers/run_gl.py`); exceptions map to src/program.py:line, in-string GLSL errors carry both line numbers |

Wrappers are standalone (never import codeverse3d).  The three python build wrappers live
together in `languages/wrappers/` — `run_bpy.py`, `run_bpy_links.py` (Blender's python 3.11),
`run_cq.py` — beside the sibling modules they import from their own directory:
`_wrapper_common.py` (stdlib only: rlimit, seeding, running the script with `src/` on
`sys.path`, traceback → `src/<file>:<line>`, `sys.exit(0)` is not a failure, the atomic
report) and `_census.py` (the Blender census, blender AND urdf_blender); copy the directory
whole.  Each writes its report as `build.json` + `census.json`, which the runtime reads
once through `_common.compose_build_result` (blender and cadquery: `_common.run_wrapper_build`, the
invalidate → missing entry → subprocess prologue around it) and replaces with the final `BuildResult` — for
EVERY runtime `artifacts/build.json` is the BuildResult (urdf_blender after its post-wrapper
checks).  `opengl_python/wrappers/run_gl.py` is the moderngl runner; threejs/scene
export+render live in `runtime_js/` (`export_glb.mjs`, `render_glb.mjs`,
`render_scene.mjs`, `probe_scene.mjs`, `lib/instances.mjs`; `check_shaders.mjs` is the effect-library tests' compile driver).
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
are the pure helpers `3dcode doctor` reuses for its `node` row.

## spatial/

`tool_common.glb_path` refuses (`ToolUsageError`) when `artifacts/build.json` says the last
build failed — the one build status: every runtime publishes its final BuildResult there and
a tool's lint refusal writes a `LintError` one (`build_last.json` is gone, 2026-09-22).  Tools
never measure or texture a stale GLB; missing/unreadable status stays permissive (hand-placed GLBs).
```python
from codeverse3d.spatial.render import render_glb
from codeverse3d.contracts.artifacts import RENDER_MODES   # ('shaded','wire','normals','silhouette','clay') — THE mode tuple (no 'depth')
render_glb(glb, out_dir, *, views=None, mode="shaded|wire|normals|silhouette|clay", width=768, height=768,
           isolate=None, explode=0.0, sheet=True, background=..., anim_time=None, shadow=True, gpu=None,
           timeout_s=None, use_cache=True) -> RenderSet
from codeverse3d.spatial.render_scene import render_scene
render_scene(ws, out_dir, *, cameras=None, orbit=True, times=(0.0, 1.5), width=1024, height=576, sheet=True,
             bounds=None) -> RenderSet
    # Δ bounds default from ws plan.json → orbit rig frustum-fits the CONTENT box (not ground/sky);
    # contact sheet = judge subset only; views.json entries get judge: true|false
from codeverse3d.spatial.render_scene import select_judge_views, JUDGE_MAX_VIEWS, plan_bounds
select_judge_views(rs, max_n=10) -> RenderSet          # priority: authored@t0, 2 overview@t0, 2 authored@t_last, rest
from codeverse3d.spatial.frame_metrics import frame_gate_from_renders, frame_findings, frame_summary_text, FRAME_GATE
frame_gate_from_renders(renders) -> GateReport         # gate "scene_frames"; data.kind ∈ dark_frame | blown_frame | flat_frame |
    # camera_in_geometry | camera_underground | camera_low | camera_high | content_small; authored cameras → ERROR, orbit rig → WARN
    # Δ camera_low / camera_high measure camera_checks.ground_below_m (the ray straight down from the eye, nearGeometry);
    #   the scene-wide census ground_y is the fallback when nothing lies beneath the lens
    # Δ camera_blocked (ERROR: ≥ 6 of the 9 sight rays end within near_limit_m 1.5) · camera_target_blocked (WARN: the line of
    #   sight to the plan's lookAt is cut before half the distance, names the cutter) · camera_under_ground_mesh (WARN, ERROR
    #   when the frame agrees: a terrain-scale ground surface straight above the eye, ground_above_m; the camera repair lifts it)
    # Δ hero_unseen (ERROR: a loaded GLB in the scene fills < 0.5 % of every authored frame — camera_checks[].glb_frac, one mask
    #   render per GLB per camera, occlusion included) · hero_small_in_its_camera (WARN: < 2 % in the camera named for it)
    # Δ scene_placement stamped_ring (WARN): census groups[].stamps — per family of ≥ 8 copies {n, radius_m, radius_cv,
    #   gap_cv, size_cv}; ≥ 8 same-size copies evenly on a ring at ≥ 0.6 × the plan's half-extent (host_census stampStats)
    # Δ census.camera_repair rows carry moved_side_m, blocked_before, cut_before, under_before, aimed_at (D73: a camera named
    #   for a hero — a name word of its GLB file in the camera name — is re-aimed at the hero's centre when that centre is
    #   outside its frustum, before the retreat search); the overview rig
    #   (noFog views) hides see-through sky layers whose box lies below the eye; roomShell tags userData.placement='free'
from codeverse3d.spatial.measure import measure_glb      # link-hierarchy rule: metadata["links"] → each link is its own part
    # Δ Measurement.extra["complexity"] = ComplexityVector.model_dump() (additive, best-effort, never raises)
from codeverse3d.spatial.complexity import (ComplexityVector, COMPLEXITY_WEIGHTS, COMPLEXITY_VERSION,
                                          complexity_of_glb, complexity_of_parts, band_of)
complexity_of_glb(glb) -> ComplexityVector     # part_count, assembly_depth, tri_count, materials, silhouette,
    # feature_density, symmetry_groups, hollowness, + index 0-1 (documented weights) and band
    # (trivial|simple|moderate|complex|intricate).  Deterministic, no VLM/render.  eval/docs/COMPLEXITY.md
from codeverse3d.spatial.connectivity import check_connectivity   # (glb, *, gap_m=…, …, language="", planned_edges=()) — planned_edges: (child, parent | (copies…)); Δ language selects the
from codeverse3d.spatial.contract import check_contract           # frame of fix hints; both gates emit hints in the AUTHORING frame
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
from codeverse3d.spatial.measure import measure_glb, world_transform, node_name_findings   # (Δ 2026-08-30) world frames are composed
    # by walking the graph's edge matrices (trimesh's get() dropped a root pivot's rotation); a GLB with duplicate / unnamed nodes
    # lands a finding in Measurement.extra["findings"] — trimesh re-parents renamed nodes and the numbers are approximate
from codeverse3d.spatial.scene_placement import placement_findings, placement_gate, placement_gate_safe, placement_census, placement_table_text
placement_findings(table, *, indoor=False) -> GateReport   # gate "scene_placement"; data.kind ∈ floating | sunken |
    # unsupported | interpenetration | summary | probe_failed; target "Zone/Asset" (routes to src/zones/<zone>.js);
    # messages carry the scene_v1 floating_part cap words; reads artifacts/census.json["placement"] (host_placement.mjs)
placement_gate(ws, census, plan) -> GateReport | None   # THE verdict (Δ 2026-09-22): ScenePipeline.gates and the check_placement
    # tool both return it — placement_gate_safe fed the zone layouts and the unbuilt assets from ws/stages/{layouts,assets}.json
placement_gate_safe(census, *, plan=None, layouts=None, unavailable=()) -> GateReport | None   # the pure core: None without a table, WARN on failure, never raises; `unavailable` = assets the stage could not build (not "missing planned contents")
from codeverse3d.spatial.scene_placement import setting_text          # (plan: dict | model) -> the indoor/outdoor setting line
from codeverse3d.spatial.silhouette import compare_silhouette    # the reference gate + compare_reference's IoU (no tool of its own)
from codeverse3d.spatial.sections import judge_slices, SliceManifest, JUDGE_SLICE_PLANES   # (D48)
judge_slices(glb, error_pairs, out_dir, planes=("front_back","left_right")) -> SliceManifest   # two vertical centre slices,
    # red hatch ONLY on error_pairs (the connectivity gate's ERROR penetration pairs), plain darkened blend otherwise,
    # degenerate slices dropped (F4) → 0–2 PNGs (slice_<name>.png, manifest.json beside them; JudgeSlice.png is a bare
    # file name); needs the mesh extra (shapely + matplotlib), ImportError propagates; a bad GLB → manifest.errors, no raise
cross_section(glb, axis, at, out_png, *, parts=None) -> Observation   # the tool's body: any axis, `at` a fraction of
    # the SELECTED parts' bbox; loops / filled area / hollow ratio + the same renderer's image (overlaps plain, never
    # hatched); a plane that cuts nothing answers in text with no image
from codeverse3d.spatial.joints_model import load_urdf, fk, UrdfError   # RESERVED_LINK_NAMES={'world'}; Robot/Link/Joint
from codeverse3d.spatial.joints_poses import pose_samples, limit_poses
from codeverse3d.spatial.joints_export import urdf_to_glb, render_poses, ARTICULATION_SHEET_NAME
from codeverse3d.spatial.joints_sweep import sweep_gate, sweep_collisions, find_urdf, motion_direction_check, SWEEP_GATE
sweep_gate(ws) -> (GateReport "joint_sweep", Robot | None)   # THE verdict (Δ 2026-09-22): pose_samples → sweep_collisions →
    # aggregate_findings(sweep_findings) + buried_links.  tracks/articulated_object.default_joint_sweep (the round's gate,
    # + pose renders) and the joint_sweep TOOL (spatial/tools.py: gate_observation of it + a narrowed pose sheet) both
    # report exactly this; the tool used to run its own sweep (fail on any overlap > 2 mm or any gap, no buried links)
from codeverse3d.spatial.joints_collide import components   # (names, edges) -> list[set[str]]: THE union-find (connectivity + sweep)
# joints_collide.py: deterministic penetration (oriented islands + fixed-direction parity ray test; python-fcl is a
# core dependency, the trimesh fallback is deterministic too)
from codeverse3d.spatial.gl_render import GlHost, GlResult, GlHostError, write_contact_sheet, write_gif
GlHost(gpu="auto|on|off", timeout_s=240, fps=30, max_steps=240)
  .render_fragment_shader(frag_src, out_dir, *, width=1280, height=720, times=..., buffer_a_src=None, feedback=False)
  .run_program(program_path, out_dir, *, width, height, times) -> GlResult{ok, stage, frames[GlFrame], renderer, gpu, …}
from codeverse3d.spatial.frame_stats import sequence_stats, frame_gate    # gate "gl_frames"; data.kind ∈ nan | black | blown |
                                                                        # static | flicker | low_detail | duplicate | no_frames
from codeverse3d.spatial.registry import tool, get_tool, list_tools, tool_cards, ToolContext, Observation
Observation{ok: VERDICT, failed: the tool could not run, text, numbers, images, duration_ms}   # is_error == failed,
    # never `not ok`.  failed is set by Observation.error(...) and by exactly three tools that compose
    # their own result: build (no readable GLB), scene_probe (driver died), render_observation (no view
    # and no console error — with one it is a verdict)
from codeverse3d.spatial.mcp_server import observation_content, max_images_for, MAX_TEXT_CHARS
    # payload bound at the MCP boundary: text truncated; images 4 (ok) | 1 (FAIL verdict) | 0 (failed)
import codeverse3d.spatial.tools   # registers every tool (the list and their tracks: docs/ARCHITECTURE.md §5)
python -m codeverse3d.spatial.mcp_server --workspace <ws> [--track X] [--language Y] [--round N] [--list]   # MCP name: 3dcode
```

## judges/
```python
from codeverse3d.judges.rubrics import load_rubric, Rubric   # Rubric{…, defects: [DefectItem{id, text, penalty, cap}], caps[{…, when:, kinds}]}
from codeverse3d.judges.vlm_judge import VlmJudge
VlmJudge(rubric="static_object_v1", model_id=None (settings.default_judge = gemini-3.1-pro-preview), n_samples=1,
         temperature=0.2, *, thinking="low", max_attempts=3, max_montages=None, detail_crops=None, max_px=None,
         sample_budget_s=None, fixed_order=False, chat_model=None, cache_dir=None, label="judge")
    # Δ max_images is GONE → montage budget.  The four None params resolve through Settings.judge
    # (montages=5 since D47, detail_crops=2, max_px=1024) and SAMPLE_BUDGET_S=900.0
VlmJudge.judge(inp) -> Judgment                # clay/normals views travel ONLY as JudgeInput.geometry_views (no kwarg)
VlmJudge.slice_payload(inp) -> (list[(label, png_path)], provenance_elicitation: bool)   # (D48) ([], False) unless
    # Settings.judge.slices=="on-error" AND spec.track in judges.base.SLICE_TRACKS AND inp.glb_path exists AND the
    # connectivity gate has ≥1 ERROR; renders judge_slices into the judge cache (keyed by glb identity + error pairs)
from codeverse3d.judges.base import JudgeInput   # (spec, renders, measurement=None, gates=[], acceptance=[], plan_summary="",
                                               #  part_names=[] (an object plan's parts: ReferenceJudge's mismatch diff),
                                               #  round_index=0, previous=None, extra_context="", geometry_views=None,
                                               #  glb_path=None (D48: the round's canonical GLB; object tracks + 3dcode judge fill it))
from codeverse3d.judges.base import round_input, plan_summary
round_input(spec, plan, rnd, *, renders, gates, previous, extra_context, geometry_views, glb_path) -> JudgeInput
    # THE payload of one round: tracks/steps._judge builds it from the round in hand, cli/_judge.build_judge_input
    # (`3dcode judge`, addons/calibration) from the stored one — acceptance + digest + part_names from the plan, glb_path only on
    # SLICE_TRACKS.  Δ 2026-09-22: replays used a dict digest of plan.json (a Z-up object read W×D×H, a shader lost
    # style / passes / key visuals / motion); cli/_judge.stored_plan types plan.json when record.json has no plan
plan_summary(plan, language) -> str   # the digest for every track (object: "Overall W×H×D m" in the GLB frame);
    # also the texture gate's (texturing/generate.judge_gate) and eval/bench/judge_calib_graphics.py's
from codeverse3d.judges.prompt_builder import plan_montages, render_montage, Montage    # ≤5 2×2 montages (shaded/geometry/poses) + ≤2 detail
    # crops @≤1024px replace the sheet + the 14-view rig (D47); clay/normals views (RenderView.mode) auto-route to the GEOMETRY montage
from codeverse3d.judges.prompt_builder import build_judge_messages, connectivity_error_pairs, PROVENANCE_ELICITATION
build_judge_messages(inp, rubric, *, …, extra_images=None (PREpended: references), extra_text="",
                     slice_images=None, provenance_elicitation=False) -> (system, [ChatMessage])   # (D48) slice_images
    # (label, path) are APPENDED after the montages/crops + described by slice_rig_section in the view-rig text;
    # provenance_elicitation appends one sentence to the DEFECT CHECKLIST bullet.  Defaults build the byte-identical
    # pre-D48 payload; judge_prompt_hash(rubric) is unchanged either way (per-round content stays outside the hash)
connectivity_error_pairs(gates) -> list[(a, b)]   # the gate's ERROR penetration pairs via finding.target/data.other
from codeverse3d.judges.rubrics import aggregate_samples, SCORING_VERSION   # (Judgment.degraded: a glitch, not a score)  ScoreBreakdown adds defects,
    # defect_votes (majority; a defect tie → absent, an acceptance tie → representative sample, D36 as amended
    # 2026-08-30), tie_broken, defect_penalty, overall_after_defects, overridden (defects the measured-absent
    # veto switched off), scoring_version (= SCORING_VERSION, 2; older records carry 0), judge_prompt_hash (D37);
    # overall = caps(weighted_mean − Σpenalty)
    # CapRule gains measures: list[str] (the checklist defect ids a gate rule is the MEASUREMENT of —
    # penetration_error.measures = [interpenetration]) and graded: bool (an acceptance rule caps at
    # cap + (1−cap)·verified/total instead of a flat cap); rubrics._rule_evidence is now _rule_hit -> CapApplied | None
from codeverse3d.judges.rubrics import apply_caps           # (rubric, overall, gates, acceptance_results, acceptance_items=None, *,
                                                       #  console_errors=None, views=None, defects_present=None) -> CapResult;
                                                       # cap rules add when="missing_views" and ledger lines "defect:<id>"
from codeverse3d.judges.pairwise import PairwiseJudge    # .compare(spec, renders_a, renders_b, *, rubric=…) -> PairwiseResult
from codeverse3d.addons.calibration import calibrate, CalibrationTable   # (run_dirs, *, model_id, n_samples=3, out_dir, geometry_mode,
    # rounds, …) -> rows + pearson/spearman(errors vs score), mean_std, cost; CLI: python -m codeverse3d.addons.calibration RUN… --n 3
from codeverse3d.judges.vlm_judge import ReferenceJudge  # image-conditioned specs
from codeverse3d.judges.vlm_judge import judge_for  # (track, rubric, *, references) -> type[VlmJudge]: the ONE class rule
    # for the live run (BaseTrack.make_judge) and every replay (3dcode judge, calibration): measured rubric or photos on an
    # object track -> ReferenceJudge, photos on graphics/scene -> LikenessJudge, else VlmJudge
```
The rubric list is ARCHITECTURE §6's.  Wire schema order is
observe-then-score (summary/issues/defects/acceptance before criteria) — measured to
restore flash's dynamic range.  `passed = overall ≥ threshold ∧ no floor ∧ all
must-acceptance`; gate authors set `GateFinding.data["kind"]` so caps match precisely.

## orchestrator/ + tracks/
```python
from codeverse3d.tracks import get_track
rec = get_track(spec.track, **options).run(spec, ws, resume=False) -> RunRecord   # Δ kwargs forwarded to the constructor:
# services=, judge=, agent=, model=, runtime=, policy=RoundPolicy, settings=, planner_model=, n_candidates=
# (CLI --candidates > spec.options.candidates > settings.default_candidates; 1 where not TRACK_INFO[track].best_of_n: scene)
# StaticObject | Articulated | Scene | Graphics
BaseTrack.run(spec, ws, *, resume=False, force=False) -> RunRecord   # get_track returns a tracks.lifecycle.BaseTrack
BaseTrack.stages: Stages = (SKELETON, MATERIALIZE)   # the pre-round graph (docs/ARCHITECTURE.md §7.1); Stages =
    # tuple[StageNode | tuple[StageNode, ...], ...], an inner tuple = siblings side by side
StageNode(name, run: (track, ctx) -> Any, key: (ctx) -> dict | None = None, when: (Settings) -> bool | None = None,
          seeds_src=False)   # key None = uncached; the result lands in ctx.extra[name]
run_stages(track, ctx, runner, stages) -> None;  plan_stage_key(spec, track) -> dict   # the plan's key (eval pin_plan too)
StageRunner(ws, events, state=None, *, frozen=False)   # frozen (rounds exist): a recorded stage is served whatever its key
from codeverse3d.orchestrator import RoundPolicy, build_refine_instructions, compact_instructions, gate_error_count
RoundPolicy(max_rounds=4, max_refine_tasks=6, max_instructions_per_task=6, parallel_min_tasks=2,
            n_candidates=1, judge_samples=1)   # lifecycle.build_context binds n_candidates with ONE dataclasses.replace
    # Δ 2026-09-22 FIXED rounds: a run is the baseline + max_rounds refine rounds, EACH built on the round before
    # it; only the clock (BudgetGuard) or a hard failure ends it early.  Gone with the judgement stops: StopPolicy /
    # StopDecision, pick_best_round, judge_sigma, best_score, last_gain, REWRITE_KIND / DETAIL_KIND /
    # KIND_FOR_STRATEGY / detail_blocked, RunState.best_* / update_best, and the RoundPolicy fields plateau_window,
    # min_delta, target, pairwise_*, judge_model, regression_sigma, marginal_*, detail_*.
from codeverse3d.orchestrator import BudgetGuard, BudgetSnapshot
BudgetGuard(budget, start_time=None, *, soft_fraction=1.0)            # THE CLOCK, nothing else (Δ 2026-09-22)
    .check() / .ok() / .elapsed_minutes() / .timeout_s(want_s, *, floor_s, soft) / .soft_exceeded()
    .grant_grace(minutes=) / .snapshot() -> BudgetSnapshot{active_s} / .restore(snap) / .summary() -> {elapsed_min,
    max_minutes}
    # Δ 2026-09-22: charge / add / mark / stage_summary / spent / billed_usd / by_stage and usage_delta are gone —
    # the ledger (telemetry/cost.jsonl) is the only record of money; a run's total is its sum at list price
    # (record.package_run / BaseTrack._record), a round's cost the rows booked inside it (cost.tally.tally)
from codeverse3d.cost.tally import tally, timed, book_usage, book_time   # a block's money + lost seconds
with tally() as t: ...                     # t.usage = the ledger rows booked inside (nests; fan_out copies it)
with timed("judge", steps, round_index=i): ...   # appends StepTime{step, round, wall_s, lost_s} (docs/COST.md §31)
RunRecord.minutes / RoundRecord.minutes    # Σ (wall − lost) over the steps; no steps → its own clock
from codeverse3d.tracks.candidates import CandidateRecord, rank_candidates   # the one in-loop choice left
from codeverse3d.tracks.candidates import run_best_of_n, quick_render   # N parallel baselines in <ws>/_cand/c<k>
# each candidate IS steps._run_round(kind="candidate") in its sub-workspace: render=quick_render(ctx, round_index, build,
# measurement, *, pipeline) (4 views + the articulated pose views), geometry_views=False, its own events.jsonl and a
# one-sample judge → _cand/c<k>/judge/r00.json (a degraded verdict stays score None); crashed candidate retried once;
# selection by build_ok → quick score → fewer gate errors → the earlier candidate (no pairwise since 2026-09-22);
# winner copied back, normal r00 pipeline follows; rounds/candidates.json IS record.extra["candidates"] (n, selected,
# candidates[])
from codeverse3d.tracks.articulated_object import default_motion_checks, expected_direction   # gate "motion_direction"
from codeverse3d.tracks.static_object import silhouette_gate, reference_refine_tasks  # gate "reference_silhouette" (IoU<0.6 → WARN + refine task)
from codeverse3d.tracks.depth import depth_budget, DepthBudget, DETAIL_ADVICE, scope_groups, PartScope, \
    interfaces_text, scoped_generation_enabled         # complexity-aware budgets + per-part scoped generation
depth_budget(plan, *, build_timeout_s=300) -> DepthBudget   # min/target/max triangles + max_build_s sized from
    # the plan's LEAF count (parts × instances × children); only budget_gate reads it — no prompt states it.
    # DETAIL_ADVICE: the numberless "spend detail inside the planned parts" bullet the generate/refine
    # templates show as {{ detail_advice }}
scope_groups(plan, *, files_for, max_groups=6, parts_per_scope=3, min_parts=8) -> [PartScope]
    # [] = one session owns the object (small plan, no per-part file ownership, or $C3D_SCOPED_PARTS=off);
    # otherwise attachment-subtree groups whose files are disjoint, so the sessions run in parallel
interfaces_text(plan, scope) -> str    # the planned boxes of the neighbours this scope must weld to
from codeverse3d.tracks.prompting import base_prompt_context, reference_images, scope_context, budget_for  # Δ split out of
from codeverse3d.tracks.prompting import select_cookbook_chapters, is_always_chapter
    # select_cookbook_chapters(ctx, brief, *, budget=9000, always=COOKBOOK_ALWAYS) -> list[Section]: the header +
    # always-on chapters + the brief's chapters (whole, cookbook order, inside budget)
from codeverse3d.tracks.graphics import seed_recipes, graphics_brief, cookbook_functions, EXTRA_KEY, RECIPES_REL
    # seed_recipes(ctx) -> list[str]: glsl_shader + Settings.limits.seed_recipes only.  Writes the selected chapters'
    # function definitions (minus always-on chapters and the raymarching template) + the helpers they call to
    # the HARNESS-OWNED src/recipes.glsl (RECIPES_REL; header "// harness-owned: … READ-ONLY …"; a resume appends
    # only names the file lacks); returns the names written THIS call; ctx.extra["seeded_recipes"] =
    # [{name, signature, purpose}] for every seeded recipe on disk (the prompt block); emits recipes.seeded
    # {file, names, present, chapters, trimmed}.  Never writes src/common.glsl — except the untouched skeleton,
    # which loses the helpers recipes.glsl now provides (trim_skeleton_common; recipes are pasted first).
    # GraphicsTrack.stages runs it (the uncached "recipes" stage) after the skeleton and commits "recipes"
    # when it wrote something.
from codeverse3d.tracks.common import RunContext, Services   # common.py; RunContext.single_shot / .agent_kind
    # (Δ 2026-08-30) Services.connectivity(glb, language="", planned_edges=()) forwards the plan's attach_to pairs;
    #   spatial.contract.planned_joins(plan, measurement) -> [(child, (parent copies…))] spells them in GLB part names via
    #   spatial.contract.match_parts (instance copies Leg_0..n join the nearest parent copy — no regex on ids)
from codeverse3d.judges.prompt_builder import gates_section, contact_ledger   # (Δ 2026-08-30) contact_ledger(gates) finds the
    # connectivity report's INFO ledger; with it gates_section renders MEASURED STRUCTURE + one overlap line + planned joins
    # (LEDGER_MAX_CONTACTS 24, LEDGER_MAX_JOINS 20, ground line for parts within GROUND_BAND_MM of the lowest point whose gap
    # exceeds GROUND_GAP_REPORT_MM) and drops the per-pair penetration WARN prose; without it the pre-change text, byte for byte
from codeverse3d.tracks.generation import write_files        # (ws, files, *, allowed_roots, only=None, frozen=(), on_skip=None):
    # frozen = harness-owned paths a single-shot envelope may not rewrite (skipped with a reason, never an error)
from codeverse3d.agents.cli_common import find_json_object, default_mcp_command   # THE one JSON-envelope finder behind
    # parse_gemini_json / parse_claude_json / parse_agy_json; default_mcp_command(ws, *, language, track, round_index)
from codeverse3d.tracks.generation import generate, run_agent_task, parse_multifile, is_single_shot
GenerationTask.phase: int = 0   # tasks run in parallel WITHIN a phase, phases in ascending order
    # (tracks.steps.run_generation_tasks).  Only user: the scoped baseline — phase 0 = one session per
    # part group (`baseline_<parts>`, own files only), phase 1 = ONE `assemble` session that owns the
    # entry file and the placement gates.  Every other caller is phase 0, i.e. unchanged.
generate(ws, *, agent_id, task, ..., budget=BudgetGuard) -> GenerationResult
    # GenerationResult.storm: every session died on a transient streak (AgentResult.transient) and wrote nothing
    # Δ 2026-09-22 GenerationResult.transient / .quota carry the backend's typed class; a call that RAISED is
    #   GenerationResult.from_error(label, e) — transient = tracks.generation.is_model_outage(e) (ModelError
    #   retryable or 429/5xx; moved from scene_assets, the one exception classifier)
from codeverse3d.tracks.steps import RoundFailed   # RoundFailed(message, *, transient=False, quota=False);
    # RoundFailed.of(results, default) — the round loop reads .quota (→ agent_quota) and .transient (→ re-run
    # the round once), never the message (looks_transport / looks_quota deleted)
tracks.common.generate_for(ctx: RunContext, task: GenerationTask) -> GenerationResult
    # THE call every stage makes (env, zones, rounds, repairs, asset ladder, judged fix): generate()
    # with everything ctx knows, and a storm-dead task retried through single_shot_ctx(ctx) (D68)
tracks.common.single_shot_agent_id(agent_id, chat_model_id='') -> str · single_shot_ctx(ctx) -> RunContext | None
    # moved from tracks.scene_assets 2026-09-07 (never scene-specific)
    # GenerationResult adds turns / sessions / turn_capped.  A turn cap is applied ONLY if the machine
    # asks: $C3D_AGENT_MAX_TURNS > settings.limits.agent_max_turns >
    # DEFAULT_AGENT_MAX_TURNS (0 = leave AgentJob.max_turns at the backend's own default — a 28-turn
    # default was measured and rejected, docs/COST.md §17).  A session that hits a cap that IS set is
    # asked for a final build + summary (WRAPUP_PROMPT) instead of being killed.
    # EVERY session (attempt 1, <label>.a2 retry, <label>.wrapup) is charged as it ends.
from codeverse3d.tracks.repair import build_with_repair   # RepairOutcome(.ok/.max_attempts, attempts, usage); build_with_repair(ctx, *, round_index, label, files_hint=None, max_attempts=None, timeout_s=None) — timeout_s clips every repair session (a scene asset's window)
# scene assets (tracks/scene_assets.py): build_threejs_asset / build_blender_asset climb ONE ladder (_ladder: single-shot → check → one feedback repair → agent session); a hero's parts come from the static planner (hero_plan); SceneThreeJsRuntime.render_asset(ws, name, out_dir) renders a module on the hero's quick rig; the assembled scene.js plays every GLB clone's clips (clone.userData.clipOffset de-phases a copy)
# languages/blender.write_blender_skeleton(ws, plan, *, ground_tol_m=0.002) — the self-check's stands-on-z=0 tolerance (a scene hero passes 0.02)
from codeverse3d.tracks.steps import run_round, skip_judge_reason, record_aborted_round
skip_judge_reason(ctx, *, renders, ignore_budget=False) -> str    # "" = judge it.  ONLY states where the
    # verdict is never bought at all: no judge / no renders / budget already exceeded
    # (docs/COST.md §17 — "no file change" and "build not repaired" were removed)
run_round(ctx, *, index, kind, tasks, pipeline, ..., previous=None, findings=()) -> RoundRecord
    # previous / findings: the previous round's verdict (the judge reads it) and gates (the skill router's input)
    # steps._run_round (the same round, no aborted-round record) also takes render: RenderFn | None (swaps the
    # pipeline's render; candidates pass quick_render) and geometry_views=False (skips the clay/normals views)
    # rec.usage = the ledger rows the round booked (its tally) + extra_usage; rec.steps = generate (per phase) /
    # build / gates / render / judge; on ANY exception it records what the round burned and the steps it ran
    # (rounds/aborted_rNN.json, ctx.extra["aborted_rounds"], RunState.steps) and re-raises
from codeverse3d.tracks.planner import plan, ensure_acceptance, plan_example, plan_temperature
plan(spec, model_id, plan_model, ws, *, model=None, events=None, budget=None) -> Plan   # no runtime= (the contract is the catalog's)
    # Δ 2026-09-22: every track, graphics included, through ONE loop that dispatches on spec.track — PLAN_TEMPLATES,
    # plan_example(track), plan_temperature(track) (graphics 0.5, else 0.4; record/telemetry reports it), the output
    # floor (graphics PLAN_TOKENS_MAX, else PLAN_OUTPUT_FLOOR), plan_budget's unit (passes) and ensure_acceptance
    # (graphics: must_have / must_not + a motion probe item, never dimensions / triangles / ground).  The six BaseTrack
    # planner hooks and plan()'s template / example / temperature / max_output_tokens / finalise / event_stats are gone
```
`run_round` = generate → commit → `build_with_repair` → measure → gates → render →
post-render gates → the build's own `BuildResult.gates` (also when the build failed) → judge → commit.  Post-render gates: static `reference_silhouette`
(when references), articulated `joint_sweep` + `motion_direction`, scene
`render_console` + `scene_frames` (via `ctx.services.frame_gate` ->
`frame_gate_from_renders`; advisory, a failure is logged not raised), graphics `gl_frames`.  Reference specs get a
`ReferenceJudge` (rubric `reference_v1` for static_object; `LikenessJudge` on graphics/scene —
`vlm_judge.judge_for`) and images attached to generation prompts.

## texturing/  (derived asset pack; code stays truth — object.glb is never touched)
```python
from codeverse3d.texturing.run import texture_pass, texture_requested, load_report
texture_requested(spec) -> bool   # THE owner of "does this run texture?" (Spec.options.texture, or
    # the legacy "texture" tag).  Asked by the CLI hand-over after the run (make/resume → addons.select.package(
    # texture=…), which textures the PICKED round — Δ 2026-09-22, finalise no longer does).  No agent session can
    # buy a pass: the texture_pass / texture_preview tools were deleted 2026-09-22.
texture_pass(ws, spec, plan, *, model_id, image_model=None, judge=True, judge_obj=None, judge_model_id=None, rubric=None,
             glb_in=None, sheet=None, views=OBJECT_VIEWS_QUICK, size=1024, plan_model=None, render=None, cache_dir=None,
             events=None, normalise=True) -> TextureReport   # always writes record.json extra["texturing"] when a record exists
# 1 vision call material plan (cacheable) → tileable textures (mirror cross-fade, seam_score ≤ 0.08) → world-metre UV
# unwrap (planar/box/cylinder per part, tile_size_m) → artifacts/object_textured.glb → seam gate + before/after judge
# gate (ship iff Δoverall ≥ −0.01 AND materials criterion improved); record.extra["texturing"], events texture.*
from codeverse3d.texturing.plan import material_plan, default_plan, TexturePlan
from codeverse3d.texturing.generate import generate_textures   # (Δ) FakeImageModel / procedural_texture
    # moved to tests/texturing/conftest.py 2026-08-30 — no production path could construct them
from codeverse3d.texturing.plan import scene_texture_pack, texture_pack_prompt   # 6–12 named tiles + manifest.json
# under public/textures/ for scene prompts (URL /public/textures/<name>.png)
```

## record/ + addons/ + cli/
```python
from codeverse3d.record.record import finalize_record, load_record, iter_runs, effective_judgment, effective_score
    # Δ 2026-09-22: best_round_index / best_round_record are gone, and fill_derived no longer derives a best round,
    # baseline or final score — which round counts is addons.select's question
from codeverse3d.record.deliverable import keep_round_artifacts, round_outputs, build_deliverable, load_deliverable
    # keep_round_artifacts(ws, round_index) -> [names]: steps._run_round copies a BUILT round's hand-over files
    #   (object.glb object.stl object.step robot.urdf preview.gif frames_sheet.png meshes/*.glb) to artifacts/rNN/
    # round_outputs(ws, rnd) -> Path | None: artifacts/rNN/, else artifacts/ for the best round a pre-2026-09-22
    #   record.json names, else None
    # build_deliverable(ws, record, round_index, *, clean=True) -> RunDeliverable: deliverable/ for ONE round —
    #   its commit's code + its kept files + its sheet (+ graphics: its judged frames; + a shipped texture pack
    #   whose report names this round's GLB); never rebuilds.  Only addons.select.package calls it.
from codeverse3d.addons import select       # which round of a FINISHED run to hand over (cli/ + addons only)
select.round_rows(run_dir, *, record=None) -> [RoundRow{index, kind, score (effective), passed (the judge's own
    # verdict for THAT round), gate_errors, build_ok, commit, cost_usd, minutes}]
select.pick(run_dir, *, by="score"|"pairwise", pairwise_model=None, record=None, judge=None) -> int | None
    # highest effective score → fewer gate errors → the earlier round; None when no round was judged.  "pairwise":
    # the top two within PAIRWISE_MARGIN=0.03 go to judges.pairwise.PairwiseJudge (position-swapped); the runner-up
    # wins at confidence ≥ 0.6; one paid verdict per (pair, model), cached at artifacts/judge/rAA_vs_rBB_pairwise.json
select.summarise(run_dir, *, record=None) -> RunSummary{rounds, stop_reason, baseline_score (r00's effective
    # score), picked_round, picked_score, delta, method}: selection.json's round when the run was packaged, else pick()
select.package(run_dir, round_index, *, texture=False, method="round", image_model=None) -> Path (deliverable/)
    # + selection.json {round, method, scores, textured, selected_at}; texture=True runs the pass on that round's
    # kept GLB unless a report already covers those bytes; a failed pass never fails the hand-over
select.round_file(ws, rnd, name="object.glb") -> Path | None   # the round's OWN output (round_outputs), never the
    # canonical artifacts/<name> (the last build's): calibration, `3dcode judge`, `texture pass`, export, eval/bench
select.round_complexity_block(ws, rnd) -> dict | None   # its own measurement's vector (legacy: measurement.json only
    # when the canonical build is this round's); gallery cards and dataset meta.  Never record.extra["complexity"]
from codeverse3d.addons.gallery.index import hero_view          # (ws, rec, picked) -> (rel, label, n_views): the card image
from codeverse3d.record.record import complexity_block, round_complexity   # objective complexity of what was built
    # finalize_record fills record.extra["complexity"] = the LAST measured round's vector + plan_parts /
    # parts_per_plan_part / by_round; readers that show one round (gallery, dataset) read that round's own
    # round_complexity (a dataset sample's rounds_summary rows carry it)
from codeverse3d.addons.dataset.export import export_samples   # (runs_dir, out_dir, *, min_score=None, only_passed=False,
    # include_unbuilt=False, captions_dir=None, drop_duplicates=False) -> ExportReport{…, n_duplicates, duplicates, tiers}
from codeverse3d.addons.dataset.quality import quality_tier, prompt_hash, find_duplicates   # tiers (of the exported round's verdict):
                                                          # A passed & 0 gate errors, B passed, C score ≥ 0.6, D else; dedupe = (code fingerprint, prompt)
from codeverse3d.addons.dataset.pairs import build_pairs       # (runs_dir, out_jsonl, *, min_delta=0.05) -> n
from codeverse3d.addons.dataset.refine import build_refine, transitions, RefineTransition, REFINE_KINDS, outcome_of
    # build_refine(runs_dir, out_jsonl, **kw) -> (rows written, Counter of drop reasons); writes via a .part file
    # transitions(runs_dir, *, threshold=MIN_PREFERENCE_DELTA, max_diff_bytes=200_000, with_code=False,
    #             drops=None) -> Iterator[RefineTransition];  outcome_of(delta, threshold) -> the label
    # one row per round i -> i+1 the harness asked to change; outcome improved|regressed|unchanged|unscored
    # (threshold: pairs.MIN_PREFERENCE_DELTA); dropped rows carry the reason (no_predecessor / no_commit /
    # predecessor_build_failed / predecessor_unjudged / git_read_failed)
from codeverse3d.record._git import read_tree_at, diff_between, changed_files_between, GitReadError
    # read_tree_at(ws, commit, *, paths=None) -> {path: bytes} via ls-tree + cat-file --batch — NEVER
    # `git archive`, which renders content through a planted filter.<name>.smudge and has no --no-filters
    # (tests/flywheel_cli); symlinks (mode 120000) are skipped; `paths` reads only those files
from codeverse3d.record.record import unique_files, SUBRUN_DIRS, BATTERY_MARKERS
    # unique_files(root, name) -> [Path]: every file called `name` under root ONCE per file on disk (follows
    # the run/telemetry/trajectories symlink and collapses it; skips SUBRUN_DIRS = {_cand, _assets}) — the
    # one walker behind eval/bench/session_stats.py and eval/bench/coupling_stats.py (costreport.audit.find_runs
    # skips SUBRUN_DIRS the same way)
    # diff_between(ws, before, after, *, max_bytes=None) -> (text, untruncated size, was_truncated)
    # changed_files_between(ws, before, after) -> [path];  "is the commit there?" is Workspace.has_commit
    # both under GIT_SAFE_DIFF_FLAGS (--no-ext-diff --no-textconv) on top of workspace.GIT_SAFE_FLAGS
from codeverse3d.workspace import GIT_SAFE_FLAGS, GIT_SAFE_DIFF_FLAGS, git_safe_env
    # every read of an agent-written repo goes through these.  They do NOT disable .git/config —
    # git reads it in full; `-c` only OVERRIDES three keys (hooksPath, fsmonitor, attributesFile)
    # and the diff flags cover ext-diff/textconv, which is why a NAMED filter./diff. driver in .git/config
    # is still live and why read_tree_at avoids every content-rendering command.  git_safe_env drops
    # the SYSTEM and GLOBAL config (GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL=/dev/null) and the
    # inherited environment (HOME, PATH, GIT_TERMINAL_PROMPT)
from codeverse3d.addons.dataset.captions import caption_sample # Δ (ws, record, model_id, *, model=None, out_dir=None) -> Captions;
                                                       # out_dir → side-car <out_dir>/<slug>.json, run untouched
from codeverse3d.addons.gallery.index import build_index, default_roots   # THE local gallery (page: build_static, server: serve, GalleryApp)
                                                       # build_index(roots) -> GalleryIndex (sections of RunEntry; never raises per run)
                                                       # build_static(roots, out_html, *, embed=False) -> (path, n, index)
                                                       # render_static(index, *, embed=…, extra_html="") — eval/bench/report.py's page
                                                       # GalleryApp(roots, reload=False).route(path, query) -> Response  (pure, testable)
                                                       # serve(roots, *, host=None, host_explicit=False, port=8765, reload=False)
from codeverse3d.addons.gallery.urls import safe_join          # (root, rel) -> Path inside root, else PathError
from codeverse3d.addons.gallery.urls import content_type        # .glb→model/gltf-binary, .py/.js/.frag→text/plain; charset=utf-8
from codeverse3d.addons.dataset.index import build_index, query, summary   # sqlite + parquet: adds quality_tier, gate_errors, cost_usd,
                                                                   # rounds, status, code_fingerprint, prompt_hash, duplicate_of, has_captions
3dcodeverse make [--profile economy|balanced|quality] [--no-pick]|resume [--no-pick]|pick|status|show|render|judge|tools|mcp
             |texture {pass,scene-pack,show}|cost {<slug>,show,cache,prices,profiles,estimate}
             |flywheel {export,pairs,refine,caption,index}|gallery {serve,build}
             |skills {list,show,validate,report}|doctor    # alias: 3dcode; batteries: eval/ (python -m bench.run_bench)
```

## Events and records
`EventLog.emit(event, **data)` writes `{"t", "event", ...}` (**Δ** key is `event`).  A function whose `events=` is
optional normalises it once with `events = proc.NULL_EVENTS if events is None else events` (never `events or …`).
Event names: `run.start`, `stage.start/done`, `plan.done`, `workspace.materialized`,
`round.start`, `generate.done`, `build.done`, `gates.done`, `judge.done`,
`round.done`, `refine.planned`, `recipes.seeded` (graphics: names written this call, present on disk, chapters), `asset.judged`, `assets.done`,
`zones.done`, `assemble.done`, `round.no_change`, `candidates.start`,
`candidate.start/done/failed/retry/selected`, `resume.reconciled` (rounds, dropped, `restored_last_round`; Δ 2026-09-22 no
`state_was_stale`: run_state.json keeps no copy of the round journal to go stale),
`texture.start/plan/generated/applied/gate/done`, `budget.exceeded`,
`finalise.rebuild`, `stop` (reason, rounds), `run.done` (status, rounds, last_score) / `run.failed`;
after the run: `pick.pairwise`, `pick.packaged`, `texture.skipped` / `texture.failed` (addons.select).
Cost events: `judge.skipped` (reason), `generate.turn_cap` (label, max_turns, turns, cost_usd);
`run.done` carries the run's `cost_usd` (the ledger's) and `minutes`.  Δ 2026-09-22: `cost.round` is
gone — no reader, and its per-round {stage → $} restated the ledger (`summarise(rows)` by round and stage).
Δ 2026-09-22: `best.updated`, `strategy.switch`, `round.refine_from_best`, `pairwise.done` and
`budget.overrun` are gone, and so is `RoundRecord.pairwise` (the in-run tie-break note; an old
rNN.json that carries one still loads, the key ignored).  `PairwiseNote` lives in `addons.select`.
`RunRecord` (record.json): spec, plan, workspace, status, rounds[RoundRecord{…, usage, duration_s, steps}],
total_usage, environment, steps (run-level StepTime rows; `minutes` = the property over all of them),
prompt_hashes{contract, cookbook, generate, refine}, error, extra{stop_reason,
rubric, budget{elapsed_min, max_minutes} (the clock), aborted_rounds?, candidates? (the
rounds/candidates.json payload), texturing?, captions?}.  `total_usage` is the sum of telemetry/cost.jsonl at
list price — every billed row once, aborted rounds, retried sessions and billed-but-discarded round-trips
included; a texture pass a pick buys joins it when `select.package` re-packages the run.  Δ 2026-09-22: no
`cost_by_stage` / `rounds_summary` (written, never read), no `baseline_score` / `final_score` (old records
carry them: ignored — `addons.select.summarise` answers), `best_round` only as a LEGACY read-only field (an old
record's pointer to the round its `artifacts/` holds, `record.deliverable.round_outputs`; kept across a record
rewrite, never written by a new run, absent from its JSON), and `status` is only
the stop reason: max_rounds | budget | agent_quota | no_change | no_refine_tasks | judge_unavailable |
failed (an old `passed` / `plateau` loads as `stopped`).
