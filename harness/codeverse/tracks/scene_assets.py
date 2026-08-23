"""Scene asset stage: one generation task per asset, fanned out.

* ``threejs`` assets → ``src/assets/<snake>.js`` exporting ``build<Pascal>``.
* ``blender_glb`` assets → a sub-workspace under ``<ws>/_assets/<snake>`` runs
  the Blender runtime (skeleton → generate → build+repair) and the GLB is
  copied to ``public/assets/<snake>.glb``.

Each asset is optionally judged with ``asset_v1`` on a quick 4-view sheet and
gets at most ONE fix pass (cost target ≤ $2 / scene).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.common import Language, Track
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AssetPlan, BBox, PartPlan, ScenePlan, StaticPlan
from codeverse.contracts.spec import Spec
from codeverse.conventions import OBJECT_VIEWS_QUICK, to_pascal, to_snake
from codeverse.orchestrator.fanout import fan_out
from codeverse.prompts import render
from codeverse.tracks.common import RunContext, language_contract, load_prompt_or
from codeverse.tracks.generation import GenerationTask, generate
from codeverse.tracks.prompting import base_prompt_context
from codeverse.tracks.repair import build_with_repair
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

MAX_ASSETS = 8
ASSET_RUBRIC = "asset_v1"


class AssetResult(BaseModel):
    name: str
    kind: str
    ok: bool
    path: str = Field(default="", description="src/assets/<snake>.js or public/assets/<snake>.glb")
    size_m: tuple[float, float, float] | None = None
    score: float | None = None
    fixed: bool = False
    notes: str = ""


def asset_file(asset: AssetPlan) -> str:
    snake = to_snake(asset.name)
    return f"src/assets/{snake}.js" if asset.kind == "threejs" else f"public/assets/{snake}.glb"


def asset_api_summary(plan: ScenePlan, results: dict[str, AssetResult]) -> str:
    """What zone/composer tasks need to know about each asset (names + sizes + how to get it)."""
    lines = []
    for a in plan.assets:
        r = results.get(a.name)
        size = (r.size_m if r and r.size_m else a.approx_size_m)
        s = f"{size[0]:.2f}×{size[1]:.2f}×{size[2]:.2f} m (w×h×d)"
        status = "" if (r and r.ok) else "  [NOT AVAILABLE — do not reference]"
        if a.kind == "threejs":
            lines.append(f"- {a.name}: `import {{ build{to_pascal(a.name)} }} from './assets/{to_snake(a.name)}.js'` → Group, base at y=0, {s}{status}")
        else:
            lines.append(f"- {a.name}: GLB at `public/assets/{to_snake(a.name)}.glb` (load via loaders.gltf), base at y=0, {s}{status}")
    return "\n".join(lines) or "(no assets)"


def run_asset_stage(ctx: RunContext, *, judge_assets: bool = True) -> dict[str, AssetResult]:
    """Generate every asset (parallel) and return results keyed by asset name."""
    plan: ScenePlan = ctx.plan  # type: ignore[assignment]
    assets = list(plan.assets)
    if len(assets) > MAX_ASSETS:
        ctx.events.emit("assets.capped", n=len(assets), cap=MAX_ASSETS)
        assets = assets[:MAX_ASSETS]
    if not assets:
        return {}
    _ignore_sub_workspaces(ctx.ws)

    def _one(asset: AssetPlan) -> AssetResult:
        if asset.kind == "blender_glb":
            return build_blender_asset(ctx, asset, judge=judge_assets)
        return build_threejs_asset(ctx, asset, judge=judge_assets)

    workers = ctx.settings.limits.max_parallel_agents
    if any(a.kind == "blender_glb" for a in assets):
        workers = min(workers, ctx.settings.limits.max_parallel_builds)
    results = fan_out(assets, _one, max_workers=workers, label="assets", item_name=lambda a: a.name)
    out: dict[str, AssetResult] = {}
    for asset, r in zip(assets, results, strict=True):
        if isinstance(r, Exception):
            from codeverse.orchestrator.budget import BudgetExceeded

            if isinstance(r, BudgetExceeded):
                raise r
            out[asset.name] = AssetResult(name=asset.name, kind=asset.kind, ok=False, notes=f"{type(r).__name__}: {r}")
            ctx.events.emit("asset.failed", asset=asset.name, error=f"{type(r).__name__}: {r}")
        else:
            out[asset.name] = r
    ctx.ws.commit("assets")
    ctx.events.emit("assets.done", ok=[n for n, r in out.items() if r.ok], failed=[n for n, r in out.items() if not r.ok])
    return out


# ----------------------------------------------------------------------------- threejs asset
def build_threejs_asset(ctx: RunContext, asset: AssetPlan, *, judge: bool) -> AssetResult:
    rel = asset_file(asset)
    task = GenerationTask(label=f"asset_{to_snake(asset.name)}", prompt=_asset_prompt(ctx, asset, rel, language=Language.SCENE_THREEJS),
                          system="You write ONE self-contained three.js ESM asset module. Raw three.js only; no DOM; no texture loading.",
                          files_hint=[rel], round=0, kind="asset", temperature=0.5)
    res = generate(ctx.ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model, settings=ctx.settings,
                   budget=ctx.budget, events=ctx.events)
    ok = res.ok and (ctx.ws.root / rel).is_file()
    result = AssetResult(name=asset.name, kind=asset.kind, ok=ok, path=rel if ok else "", notes=res.notes)
    render_asset = getattr(ctx.runtime, "render_asset", None)
    if ok and judge and callable(render_asset):
        result = _judge_and_fix(ctx, asset, result, lambda out_dir: render_asset(ctx.ws, asset.name, out_dir), files=[rel],
                                language=Language.SCENE_THREEJS)
    return result


# ----------------------------------------------------------------------------- blender asset
def build_blender_asset(ctx: RunContext, asset: AssetPlan, *, judge: bool) -> AssetResult:
    """Sub-workspace → Blender runtime → GLB → public/assets/<snake>.glb."""
    snake = to_snake(asset.name)
    sub_ws = Workspace(ctx.ws.root / "_assets" / snake).create()
    runtime = ctx.services.runtime(Language.BLENDER)
    sub_spec = Spec(id=f"{ctx.spec.id}-asset-{snake}", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                    prompt=asset.description, budget=ctx.spec.budget, backends=ctx.spec.backends)
    sub_plan = asset_plan(asset)
    sub_ws.write_json(sub_ws.spec_path, sub_spec)
    sub_ws.write_json(sub_ws.plan_path, sub_plan)
    sub = replace(ctx, spec=sub_spec, ws=sub_ws, runtime=runtime, plan=sub_plan, track=Track.STATIC_OBJECT,
                  contract_text=language_contract(Language.BLENDER, runtime), cookbook_rel="blender/cookbook.md",
                  cookbook_text=load_prompt_or("blender/cookbook.md", ""), extra={})
    runtime.skeleton(sub_ws, sub_plan)
    sub_ws.commit("skeleton")
    if not ctx.single_shot:
        kind = ctx.agent_id.split(":", 1)[0]
        ctx.services.materialize(sub_ws, agent_kind=kind, contract_md=sub.contract_text, cookbook_rel=sub.cookbook_rel,
                                 spatial_tools=True,
                                 mcp_command=["python", "-m", "codeverse.spatial.mcp_server", "--workspace", str(sub_ws.root)])
    task = GenerationTask(label=f"asset_{snake}", prompt=_asset_prompt(sub, asset, "src/model.py", language=Language.BLENDER),
                          system="You write ONE raw bpy script (src/model.py) that builds a single scene asset. No SDKs, no render/export calls.",
                          files_hint=["src/model.py"], round=0, kind="asset", temperature=0.5)
    res = generate(sub_ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model, settings=ctx.settings,
                   budget=ctx.budget, events=ctx.events)
    if not res.ok:
        return AssetResult(name=asset.name, kind=asset.kind, ok=False, notes=f"generation failed: {res.notes}")
    sub_ws.commit("asset generated")
    outcome = build_with_repair(sub, round_index=0, label=f"asset_{snake}", files_hint=["src/model.py"])
    if not outcome.build.ok or not outcome.build.glb_path:
        return AssetResult(name=asset.name, kind=asset.kind, ok=False,
                           notes=f"build failed: {outcome.build.error_type}: {outcome.build.error_message[:200]}")
    rel = asset_file(asset)
    dest = _copy_glb(ctx.ws, Path(outcome.build.glb_path), rel)
    size = _measure_size(ctx, dest)
    result = AssetResult(name=asset.name, kind=asset.kind, ok=True, path=rel, size_m=size)
    if judge:
        def _render(out_dir: Path):
            return ctx.services.render_object(dest, out_dir, views=OBJECT_VIEWS_QUICK, width=512, height=512)

        def _after_fix() -> bool:
            oc = build_with_repair(sub, round_index=1, label=f"asset_{snake}_fix", files_hint=["src/model.py"])
            if oc.build.ok and oc.build.glb_path:
                _copy_glb(ctx.ws, Path(oc.build.glb_path), rel)
                return True
            return False

        result = _judge_and_fix(sub, asset, result, _render, files=["src/model.py"], language=Language.BLENDER,
                                after_fix=_after_fix, parent_events=ctx.events)
        result.size_m = _measure_size(ctx, dest) or size
    return result


def asset_plan(asset: AssetPlan) -> StaticPlan:
    """A one-part StaticPlan for the asset in Blender's Z-up frame (scene sizes are Y-up w×h×d)."""
    w, h, d = asset.approx_size_m
    bbox = BBox(center=(0.0, 0.0, h / 2), extents=(w, d, h))
    return StaticPlan(object_name=to_pascal(asset.name), summary=asset.description, overall_bbox=bbox,
                      parts=[PartPlan(name=to_pascal(asset.name), role="whole asset", description=asset.description, bbox=bbox)],
                      acceptance=[])


# ----------------------------------------------------------------------------- judge + fix
def _judge_and_fix(ctx: RunContext, asset: AssetPlan, result: AssetResult, render_fn: Any, *, files: list[str],
                   language: Language, after_fix: Any | None = None, parent_events: Any | None = None) -> AssetResult:
    """Quick-sheet judge with asset_v1; ONE fix pass when below threshold."""
    from codeverse.judges.base import JudgeInput

    events = parent_events or ctx.events
    try:
        judge = ctx.services.judge(ASSET_RUBRIC, ctx.spec.backends.judge, n_samples=1)
    except Exception as e:  # noqa: BLE001 — asset judging is optional polish
        events.emit("asset.judge_unavailable", asset=asset.name, error=str(e))
        return result
    out_dir = ctx.ws.renders_dir(0) / "assets" / to_snake(asset.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        renders = render_fn(out_dir)
        verdict: Judgment = judge.judge(JudgeInput(spec=ctx.spec, renders=renders, round_index=0,
                                                  plan_summary=f"{asset.name}: {asset.description}",
                                                  extra_context=f"Expected size ≈ {asset.approx_size_m} m (w×h×d)."))
    except Exception as e:  # noqa: BLE001
        events.emit("asset.judge_failed", asset=asset.name, error=f"{type(e).__name__}: {e}")
        return result
    ctx.budget.charge(verdict.usage)
    result.score = verdict.overall
    events.emit("asset.judged", asset=asset.name, score=round(verdict.overall, 3), passed=verdict.passed)
    if verdict.passed or not verdict.improvement_plan:
        return result
    instructions = [f"- {i.target}: {i.instruction}" for i in verdict.improvement_plan[:4]]
    prompt = render("tracks/scene_asset.j2", **base_prompt_context(
        ctx, asset_name=asset.name, asset_kind=asset.kind, asset_description=asset.description,
        asset_size=asset.approx_size_m, asset_file=files[0], asset_language=language.value, fix_instructions=instructions,
        current_code=_read(ctx.ws, files[0]) if ctx.single_shot else ""))
    task = GenerationTask(label=f"asset_{to_snake(asset.name)}_fix", prompt=prompt, files_hint=files, round=1, kind="asset_fix",
                          temperature=0.4)
    res = generate(ctx.ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model, settings=ctx.settings,
                   budget=ctx.budget, events=ctx.events)
    if res.ok:
        result.fixed = after_fix() if after_fix is not None else True
    return result


# ----------------------------------------------------------------------------- helpers
def _asset_prompt(ctx: RunContext, asset: AssetPlan, rel: str, *, language: Language) -> str:
    prompt = render("tracks/scene_asset.j2", **base_prompt_context(
        ctx, asset_name=asset.name, asset_kind=asset.kind, asset_description=asset.description, asset_size=asset.approx_size_m,
        asset_file=rel, asset_language=language.value, fix_instructions=[], current_code=""))
    ctx.record_prompt("scene_asset", prompt)
    return prompt


def _copy_glb(ws: Workspace, src: Path, rel: str) -> Path:
    dest = ws.root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest


def _measure_size(ctx: RunContext, glb: Path) -> tuple[float, float, float] | None:
    try:
        m = ctx.services.measure(glb)
    except Exception as e:  # noqa: BLE001 — size is advisory for zone prompts
        log.warning("measure_glb failed for %s: %s", glb, e)
        return None
    return (round(m.extents[0], 3), round(m.extents[1], 3), round(m.extents[2], 3))


def _read(ws: Workspace, rel: str, limit: int = 30_000) -> str:
    p = ws.root / rel
    return p.read_text(errors="replace")[:limit] if p.is_file() else ""


def _ignore_sub_workspaces(ws: Workspace) -> None:
    gi = ws.root / ".gitignore"
    text = gi.read_text() if gi.is_file() else ""
    if "_assets/" not in text:
        gi.write_text(text.rstrip("\n") + "\n_assets/\n")
