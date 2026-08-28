"""Scene asset stage: cheap-first generation of one module per asset, fanned out.

* ``threejs`` assets → ``src/assets/<snake>.js`` exporting ``build<Pascal>`` —
  generated **single-shot** (one chat call) + a deterministic node check + ONE
  error-feedback repair; a full agent session is the escalation, not the default
  (see ``scene_asset_gen``: agent sessions cost 10× and 10 minutes per file).
* ``blender_glb`` assets are **heroes**: a sub-workspace under ``<ws>/_assets/<snake>``
  runs the Blender runtime (skeleton → generate → build+repair) with a real agent
  session and the GLB is copied to ``public/assets/<snake>.glb``.

Near-identical props are merged into one factory with an ``opts.variant``; the
list is capped at ``MAX_ASSETS`` in plan (= priority) order, and harder when the
soft budget is spent.  Assets are judged only when the judge can tell us
something the deterministic check cannot (heroes, or anything big enough to
matter in frame).
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
from codeverse.fanout import fan_out
from codeverse.prompts import render
from codeverse.tracks.common import RunContext, language_contract, load_prompt_or
from codeverse.tracks.generation import GenerationTask, generate
from codeverse.tracks.prompting import base_prompt_context
from codeverse.tracks.repair import build_with_repair
from codeverse.tracks.scene_asset_gen import (
    AssetCheck,
    check_threejs_asset,
    repair_feedback,
    select_assets,
    single_shot_ctx,
    variant_index,
    write_dedupe_note,
    write_variant_shims,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

MAX_ASSETS = 8
#: cap once the baseline has spent its soft sub-budget
DEGRADED_MAX_ASSETS = 4
#: an asset smaller than this share of the scene bbox volume is not worth a judge call
JUDGE_VOLUME_FRACTION = 0.05
ASSET_AGENT_TIMEOUT_S = 420
#: no SINGLE asset session may take more than this share of the whole run.  The stage
#: waits for its slowest asset, and budget.timeout_s only bites near the ceiling: on a
#: 25-minute scene (2026-08-27) seven assets finished inside 5.7 min while one escalation
#: ran the full 420 s and held the stage to 10.9 min — 28 % of the run for one asset, and
#: round 0 did not start until 18.9 min.
ASSET_SESSION_SHARE = 0.15
ASSET_RUBRIC = "asset_v1"


def asset_timeout_s(ctx: Any, floor_s: int) -> int:
    """``ASSET_AGENT_TIMEOUT_S`` clipped to one asset's share of the run AND to the
    wall clock actually left (:meth:`BudgetGuard.timeout_s`, which owns the floor)."""
    share = ctx.budget.budget.max_minutes * 60.0 * ASSET_SESSION_SHARE
    return ctx.budget.timeout_s(min(ASSET_AGENT_TIMEOUT_S, share) if share else ASSET_AGENT_TIMEOUT_S,
                                floor_s=floor_s)


class AssetResult(BaseModel):
    name: str
    kind: str
    ok: bool
    path: str = Field(default="", description="src/assets/<snake>.js or public/assets/<snake>.glb")
    size_m: tuple[float, float, float] | None = None
    score: float | None = None
    fixed: bool = False
    notes: str = ""
    strategy: str = Field(default="", description="single-shot | single-shot+repair | agent | escalated")
    judged: bool = False


def is_model_outage(e: BaseException) -> bool:
    """Is this a *service* failure (the model is down) rather than a bad answer?

    A 503 capacity storm reaches us only after ``models.retry`` has already spent its
    whole storm budget waiting, so the escalation the harness would normally do —
    a full agent session, ten times the money and ten minutes — hits the same wall.
    """
    from codeverse.models.base import ModelError

    if isinstance(e, ModelError):
        return bool(e.retryable) or e.status in (429, 500, 502, 503, 504, 529)
    return False


def asset_file(asset: AssetPlan) -> str:
    snake = to_snake(asset.name)
    return f"src/assets/{snake}.js" if asset.kind == "threejs" else f"public/assets/{snake}.glb"


def asset_api_summary(plan: ScenePlan, results: dict[str, AssetResult], alias: dict[str, str] | None = None) -> str:
    """What zone/composer tasks need to know about each asset (names + sizes + how to get it).

    Merged assets (``alias``) point at the surviving factory and the ``opts.variant``
    the zone must pass."""
    alias = alias or {}
    lines = []
    for a in plan.assets:
        kept = alias.get(a.name)
        r = results.get(kept or a.name)
        size = (r.size_m if r and r.size_m and not kept else a.approx_size_m)
        s = f"{size[0]:.2f}×{size[1]:.2f}×{size[2]:.2f} m (w×h×d)"
        status = "" if (r and r.ok) else "  [NOT AVAILABLE — do not reference]"
        if kept:
            lines.append(f"- {a.name}: use `build{to_pascal(kept)}(THREE, {{ variant: {variant_index(alias, a.name)} }})` "
                         f"from './assets/{to_snake(kept)}.js' (merged variant), base at y=0, {s}{status}")
        elif a.kind == "threejs":
            lines.append(f"- {a.name}: `import {{ build{to_pascal(a.name)} }} from './assets/{to_snake(a.name)}.js'` → Group, base at y=0, {s}{status}")
        else:
            lines.append(f"- {a.name}: GLB at `public/assets/{to_snake(a.name)}.glb` (load via loaders.gltf), base at y=0, {s}{status}")
    return "\n".join(lines) or "(no assets)"


def run_asset_stage(ctx: RunContext, *, judge_assets: bool = True) -> dict[str, AssetResult]:
    """Generate every asset (parallel) and return results keyed by asset name."""
    plan: ScenePlan = ctx.plan  # type: ignore[assignment]
    cap = MAX_ASSETS if ctx.budget.soft_ok() else DEGRADED_MAX_ASSETS
    planned = list(plan.assets)
    assets, alias = select_assets(planned, cap)
    if alias:
        ctx.events.emit("assets.deduped", merged=alias, kept=[a.name for a in assets])
        write_dedupe_note(ctx.ws, alias)
    built = {a.name for a in assets} | set(alias)
    if len(planned) > len(built):
        ctx.events.emit("assets.capped", n=len(planned), cap=cap, dropped=[a.name for a in planned if a.name not in built])
    ctx.extra["asset_alias"] = alias
    if not assets:
        return {}
    ctx.ws.ensure_gitignore()   # legacy run dirs may predate _assets/ in the standard lines

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
            # a BudgetExceeded worker is a failed asset like any other: the siblings'
            # finished modules are still shimmed + committed below, and the guard's own
            # boundary check stops the run AFTER the paid work is persisted.
            out[asset.name] = AssetResult(name=asset.name, kind=asset.kind, ok=False, notes=f"{type(r).__name__}: {r}")
            ctx.events.emit("asset.failed", asset=asset.name, error=f"{type(r).__name__}: {r}")
        else:
            out[asset.name] = r
    shims = write_variant_shims(ctx.ws, alias, {n for n, r in out.items() if r.ok})
    ctx.ws.commit("assets")
    ctx.events.emit("assets.done", ok=[n for n, r in out.items() if r.ok], failed=[n for n, r in out.items() if not r.ok],
                    strategies={n: r.strategy for n, r in out.items() if r.strategy}, variant_shims=shims)
    ctx.budget.check()  # stage boundary: stop only after the finished assets are committed
    return out


# ----------------------------------------------------------------------------- threejs asset
def build_threejs_asset(ctx: RunContext, asset: AssetPlan, *, judge: bool) -> AssetResult:
    """Single-shot → deterministic check → ONE repair → (only then) an agent session."""
    rel, pascal = asset_file(asset), to_pascal(asset.name)
    sub = single_shot_ctx(ctx)
    chk: AssetCheck | None = None
    strategy, notes = "", ""
    outage = False
    if sub is not None:
        for attempt in range(2):  # first shot + ONE error-feedback repair
            try:
                res = _generate_asset(sub, asset, rel, language=Language.SCENE_THREEJS, attempt=attempt,
                                      feedback=repair_feedback(chk, rel) if chk is not None else "")
            except Exception as e:  # noqa: BLE001 — a bad answer escalates; a dead model does not
                from codeverse.orchestrator.budget import BudgetExceeded

                if isinstance(e, BudgetExceeded):
                    raise
                outage = is_model_outage(e)
                log.warning("single-shot asset %s failed%s: %s", asset.name, " (model outage)" if outage else "", e)
                ctx.events.emit("asset.generate_failed", asset=asset.name, attempt=attempt, outage=outage,
                                error=f"{type(e).__name__}: {e}"[:300])
                chk = AssetCheck(ok=False, ran=True, fatal=True, errors=[f"the generator failed: {type(e).__name__}: {e}"[:300]])
                if outage:
                    continue      # the answer is not bad, the model is down: try the cheap shot again
                break
            outage = False
            notes = res.notes
            chk = (check_threejs_asset(ctx, rel, pascal, expected_size_m=asset.approx_size_m) if res.ok
                   else AssetCheck(ok=False, ran=True, fatal=True, errors=[f"no file was written ({res.notes or 'empty answer'})"]))
            if chk.ok:
                strategy = "single-shot" if attempt == 0 else "single-shot+repair"
                break
        if not strategy and not outage:
            ctx.events.emit("asset.escalated", asset=asset.name, errors=(chk.errors[:3] if chk else []))
    if not strategy and outage:
        # the model itself is down (503 capacity storm), and the retry layer already spent its
        # storm budget waiting: a full agent session is ten times the money and the same wall.
        # Give the asset up cheaply — the zones read "NOT AVAILABLE" and build around it.
        ctx.events.emit("asset.skipped_outage", asset=asset.name, errors=(chk.errors[:2] if chk else []))
        return AssetResult(name=asset.name, kind=asset.kind, ok=False, strategy="outage",
                           notes="generator unavailable (model outage); asset skipped")
    if not strategy:  # no chat model, or single-shot failed twice → the full agent session
        res = _generate_asset(ctx, asset, rel, language=Language.SCENE_THREEJS, attempt=0,
                              timeout_s=asset_timeout_s(ctx, 120))
        notes = res.notes
        strategy = "escalated" if chk is not None else "agent"
        chk = check_threejs_asset(ctx, rel, pascal, expected_size_m=asset.approx_size_m) if res.ok else chk
    # a module that will not import is NOT AVAILABLE to zones; a merely imperfect one
    # (size off, still a bit plain) stays usable — we already spent a repair on it
    ok = (ctx.ws.root / rel).is_file() and not (chk is not None and chk.ran and chk.fatal)
    result = AssetResult(name=asset.name, kind=asset.kind, ok=ok, path=rel if ok else "", notes=notes, strategy=strategy,
                         size_m=chk.size_m if chk and chk.size_m else None)
    ctx.events.emit("asset.generated", asset=asset.name, strategy=strategy, ok=ok, tris=(chk.tris if chk else 0),
                    errors=(chk.errors[:2] if chk and not chk.ok else []))
    render_asset = getattr(ctx.runtime, "render_asset", None)
    if ok and callable(render_asset) and _judge_wanted(ctx, asset, chk, judge=judge):
        result = _judge_and_fix(ctx, asset, result, lambda out_dir: render_asset(ctx.ws, asset.name, out_dir), files=[rel],
                                language=Language.SCENE_THREEJS)
    return result


def _generate_asset(ctx: RunContext, asset: AssetPlan, rel: str, *, language: Language, attempt: int,
                    feedback: str = "", timeout_s: int | None = None) -> Any:
    label = f"asset_{to_snake(asset.name)}" + ("_retry" if attempt else "")
    prompt = _asset_prompt(ctx, asset, rel, language=language)
    if feedback:
        prompt = prompt + "\n\n" + feedback + "\n## Current file (rewrite it COMPLETELY)\n```\n" + _read(ctx.ws, rel, 24_000) + "\n```\n"
    task = GenerationTask(label=label, prompt=prompt,
                          system=("You write ONE self-contained three.js ESM asset module. Raw three.js only; no DOM; no texture loading."
                                  if language is Language.SCENE_THREEJS else
                                  "You write ONE raw bpy script (src/model.py) that builds a single scene asset. No SDKs, no render/export calls."),
                          files_hint=[rel], round=attempt, kind="asset", temperature=0.5, timeout_s=timeout_s,
                          # threejs assets share the scene workspace (a stray write would hit
                          # zones/env); blender heroes own their whole sub-workspace
                          edit_only=language is Language.SCENE_THREEJS)
    return generate(ctx.ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model, settings=ctx.settings,
                    budget=ctx.budget, events=ctx.events)


# ----------------------------------------------------------------------------- blender asset (hero)
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
    res = _generate_asset(sub, asset, "src/model.py", language=Language.BLENDER, attempt=0,
                          timeout_s=asset_timeout_s(ctx, 180))
    if not res.ok:
        return AssetResult(name=asset.name, kind=asset.kind, ok=False, strategy="agent", notes=f"generation failed: {res.notes}")
    sub_ws.commit("asset generated")
    outcome = build_with_repair(sub, round_index=0, label=f"asset_{snake}", files_hint=["src/model.py"])
    if not outcome.build.ok or not outcome.build.glb_path:
        return AssetResult(name=asset.name, kind=asset.kind, ok=False, strategy="agent",
                           notes=f"build failed: {outcome.build.error_type}: {outcome.build.error_message[:200]}")
    rel = asset_file(asset)
    dest = _copy_glb(ctx.ws, Path(outcome.build.glb_path), rel)
    size = _measure_size(ctx, dest)
    result = AssetResult(name=asset.name, kind=asset.kind, ok=True, path=rel, size_m=size, strategy="agent")
    if _judge_wanted(ctx, asset, None, judge=judge):
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
def _judge_wanted(ctx: RunContext, asset: AssetPlan, chk: AssetCheck | None, *, judge: bool) -> bool:
    """Stop paying for what is already good.

    A VLM verdict on a 0.3 m pebble that already passed the deterministic check
    tells us nothing the scene judge will not see anyway.  Heroes (blender_glb)
    and anything big enough to dominate a frame keep their verdict."""
    if not judge:
        return False
    if not ctx.budget.soft_ok():
        ctx.events.emit("asset.judge_skipped", asset=asset.name, reason="soft_budget")
        return False
    if asset.kind == "blender_glb":
        return True
    frac = _volume_fraction(ctx, asset)
    if chk is not None and chk.ok and chk.ran and frac < JUDGE_VOLUME_FRACTION:
        ctx.events.emit("asset.judge_skipped", asset=asset.name, reason="gates_ok_and_small",
                        volume_fraction=round(frac, 5), tris=chk.tris, meshes=chk.meshes)
        return False
    return True


def _volume_fraction(ctx: RunContext, asset: AssetPlan) -> float:
    """Asset bbox volume / scene bounds volume (1.0 when the scene bounds are unknown)."""
    bounds = getattr(ctx.plan, "bounds", None)
    if bounds is None:
        return 1.0
    scene_v = 1.0
    for e in bounds.extents:
        scene_v *= max(float(e), 1e-3)
    asset_v = 1.0
    for e in asset.approx_size_m:
        asset_v *= max(float(e), 1e-3)
    return asset_v / scene_v if scene_v > 0 else 1.0


def _judge_and_fix(ctx: RunContext, asset: AssetPlan, result: AssetResult, render_fn: Any, *, files: list[str],
                   language: Language, after_fix: Any | None = None, parent_events: Any | None = None) -> AssetResult:
    """Quick-sheet judge with asset_v1 (n_samples=1, cached renders); ONE fix pass when below threshold."""
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
    # persist the verdict on the result FIRST, then book the money non-enforcing like
    # every other judge site (steps.py, candidates.py): the verdict is already paid
    # for, and raising here would discard it.  The stage boundary enforces the ceiling.
    result.score = verdict.overall
    result.judged = True
    ctx.budget.add(verdict.usage, stage="judge", role="judge", label=f"asset_{to_snake(asset.name)}")
    events.emit("asset.judged", asset=asset.name, score=round(verdict.overall, 3), passed=verdict.passed)
    if verdict.passed or not verdict.improvement_plan:
        return result
    instructions = [f"- {i.target}: {i.instruction}" for i in verdict.improvement_plan[:4]]
    prompt = render("tracks/scene_asset.j2", **base_prompt_context(
        ctx, asset_name=asset.name, asset_kind=asset.kind, asset_description=asset.description,
        asset_size=asset.approx_size_m, asset_file=files[0], asset_language=language.value, fix_instructions=instructions,
        current_code=_read(ctx.ws, files[0]) if ctx.single_shot else ""))
    task = GenerationTask(label=f"asset_{to_snake(asset.name)}_fix", prompt=prompt, files_hint=files, round=1, kind="asset_fix",
                          temperature=0.4, edit_only=language is Language.SCENE_THREEJS,
                          timeout_s=asset_timeout_s(ctx, 120))
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

