# Cross-package interfaces (binding for parallel development)

All types live in `codeverse/contracts/` (read them first).  Protocols live in
`models/base.py`, `agents/base.py`, `languages/base.py`, `spatial/registry.py`,
`judges/base.py`, `tracks/base.py`.  Do not change those files; if you need a
field, add it in your own module or propose it in your final report.

## models/  (package A)
```python
from codeverse.models import get_chat_model          # (model_id) -> ChatModel
m = get_chat_model("gemini:gemini-3.7-flash")
resp = m.generate(ChatRequest(messages=[ChatMessage.user("...", images=[ImagePart(path=...)])],
                              system="...", response_schema=StaticPlan.model_json_schema(),
                              temperature=0.4, thinking="medium", label="planner"))
resp.parsed  # dict when response_schema given
resp.text    # always
resp.tool_calls  # when request.tools given
resp.usage   # Usage with cost_usd
from codeverse.models.keypool import KeyPool          # KeyPool(keys, rpm=..) .acquire() -> key, .report(key, ok|429|5xx)
from codeverse.models.pricing import estimate_cost    # (provider, model, usage) -> usd
```
Structured output: Gemini via `response_mime_type="application/json"` + `response_schema`
(strip unsupported keywords: `$defs` → inline, `anyOf` with null → nullable, `const`,
`title`, `default` removed).  Anthropic: force a `submit` tool with `input_schema` or JSON
prefill; OpenAI: `response_format={"type":"json_schema", ...}` (Responses or Chat API).
Retries: 429/5xx/timeouts with jittered backoff, rotating Gemini keys via KeyPool.  Provider
exceptions → `ModelError(retryable=...)`.  Gemini `thinking` map: off→budget 0 (where allowed),
low→1024, medium→4096, high→16384 (`ThinkingConfig(thinking_budget=...)`; for models that
reject the field, omit it and record a warning).

## agents/  (package B)
```python
from codeverse.agents import get_coding_agent        # (agent_id) -> CodingAgent
from codeverse.agents.materialize import materialize_workspace
#   materialize_workspace(ws: Workspace, *, agent_kind: str, contract_md: str, cookbook_rel: str,
#                         spatial_tools: bool, mcp_command: list[str]) -> None
#   writes AGENTS.md + GEMINI.md + CLAUDE.md (same body) and MCP configs:
#     gemini-cli → ws/.gemini/settings.json  {"mcpServers": {"c3v": {"command": ..., "args": [...]}}}
#     claude-code → ws/.mcp.json  {"mcpServers": {"c3v": {...}}}
#     codex → returned via config overrides (-c mcp_servers.c3v.command=... -c mcp_servers.c3v.args=[...])
#     agy → ~/.gemini/antigravity-cli?  (investigate; fall back to no MCP + prompt-described CLI `c3v tools ...`)
res = agent.run(AgentJob(workspace=str(ws.root), prompt=..., model="gemini-3.7-flash", timeout_s=1200,
                         spatial_tools=True, label="baseline"))
res.files_changed, res.usage, res.transcript_path, res.ok, res.exit_reason
from codeverse.agents.watchdog import run_with_watchdog   # (cmd, cwd, env, soft_timeout_s, idle_grace_s, hard_timeout_s, on_line=...) -> CompletedProc
```
MCP command to expose spatial tools: `["python", "-m", "codeverse.spatial.mcp_server", "--workspace", str(ws.root)]`
(package F implements the server; B just wires the config).
`api-agent` tool set (ApiAgent): `read_file`, `write_file`, `edit_file` (exact-string replace),
`list_files`, `run_build` (= spatial tool `build`), plus every `spatial.registry.list_tools()`
tool; loop until the model stops calling tools or max_turns; writes transcript JSONL.

## languages/  (packages C1, C2, D, E)
```python
from codeverse.languages import get_runtime           # (Language) -> LanguageRuntime
rt.lint(ws) -> GateReport            # gate="lint:<language>"
rt.build(ws, timeout_s=None) -> BuildResult   # glb_path = ws.artifacts/"object.glb" on success
rt.skeleton(ws, plan) -> list[Path]
rt.contract_doc() -> str             # from prompts/<lang>/contract.md (package K writes it; provide a
                                     # minimal built-in fallback string if the file is missing)
```
Build wrappers live in `codeverse/languages/<lang>/wrappers/` and are invoked via
subprocess; they write `artifacts/build.json` (BuildResult fields) + `artifacts/census.json`.
Canonical GLB: Y-up, +Z front, meters, named nodes per part (node name = PascalCase part).

Threejs ESM resolution: agent code does `import * as THREE from 'three'`; the harness provides
`three` via an import map (browser) or `NODE_PATH=runtime_js/node_modules` (node).  Never CDN.

## spatial/  (packages C1 render, E joints, F the rest)
```python
from codeverse.spatial.render import render_glb, render_scene
render_glb(glb: Path, out_dir: Path, *, views: Sequence[ViewPreset] | None = None, mode: str = "shaded",
           width: int = 768, height: int = 768, isolate: list[str] | None = None, explode: float = 0.0,
           sheet: bool = True, background: str = "studio") -> RenderSet
render_scene(ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None, orbit: bool = True,
             times: Sequence[float] = (0.0, 1.5), width: int = 1024, height: int = 576,
             sheet: bool = True) -> RenderSet       # also fills console_errors, fps
from codeverse.spatial.sheet import contact_sheet   # (images: list[tuple[label, path]], out: Path, cols=4, tile=384) -> Path
from codeverse.spatial.measure import measure_glb   # (glb: Path) -> Measurement
from codeverse.spatial.connectivity import check_connectivity  # (glb, gap_m=CONTACT_GAP_M) -> GateReport
from codeverse.spatial.contract import check_contract          # (measurement, plan, tol_m) -> GateReport
from codeverse.spatial.sections import cross_section           # (glb, axis, at, out_png) -> Observation
from codeverse.spatial.silhouette import compare_silhouette     # (render_png, reference_png) -> dict(iou=..., ...)
from codeverse.spatial.joints import load_urdf, fk, pose_samples, sweep_collisions, urdf_to_glb  # package E
from codeverse.spatial.tools import *    # registers @tool: build, measure, render_views, render_sheet, isolate,
                                         # cross_section, check_connectivity, check_contract, compare_silhouette,
                                         # joint_sweep, shader_probe, scene_probe, read_cookbook
python -m codeverse.spatial.mcp_server --workspace <ws>    # stdio MCP server over the registry
```
`build` tool = `get_runtime(ctx.language).lint+build` then `measure_glb`; returns a compact
Observation (errors first, with file:line + traceback tail; on success the key numbers).

## judges/  (package G)
```python
from codeverse.judges.vlm_judge import VlmJudge     # VlmJudge(rubric="static_object_v1", model_id=..., n_samples=1)
j = VlmJudge(...); verdict: Judgment = j.judge(JudgeInput(...))
from codeverse.judges.pairwise import PairwiseJudge # .compare(spec, renders_a, renders_b) -> PairwiseResult{winner, confidence, reasons}
from codeverse.judges.reference import ReferenceJudge
from codeverse.judges.rubrics import load_rubric     # (name) -> Rubric{name, criteria[{id, weight, description, floor}], pass_threshold}
```
Rubric YAMLs: `static_object_v1`, `articulated_v1`, `scene_v1`, `asset_v1`, `reference_v1`.

## orchestrator/ + tracks/  (package H)
```python
from codeverse.tracks import get_track
rec = get_track(spec.track).run(spec, ws, resume=False)   # RunRecord
from codeverse.orchestrator.runner import StageRunner      # .stage(name, fn, inputs_hash) with run_state.json cache
from codeverse.orchestrator.rounds import RoundPolicy, StopPolicy, BestSelector
from codeverse.orchestrator.fanout import fan_out           # (items, fn, max_workers) -> list[result | Exception]
from codeverse.orchestrator.budget import BudgetGuard       # .charge(usage) raises BudgetExceeded
from codeverse.tracks.generation import generate_files, SINGLE_SHOT_FORMAT, parse_multifile  # API single-shot codegen
```

## flywheel/ + cli/  (package I)
```python
from codeverse.flywheel.record import finalize_record      # (ws, RunRecord) -> writes record.json
from codeverse.flywheel.export import export_samples       # (runs_dir, out_dir, min_score) -> ExportReport
from codeverse.flywheel.pairs import build_pairs           # (runs_dir, out_jsonl, min_delta) -> n
from codeverse.flywheel.captions import caption_sample     # (ws, model_id) -> Captions{detailed, instruction, factory}
c3v make|resume|status|render|judge|tools|mcp|flywheel|bench|doctor
```
