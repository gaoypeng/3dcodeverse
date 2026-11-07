"""Scene asset stage."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import struct
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.contracts.artifacts import Judgment
from codeverse3d.contracts.common import ENTRY_FILE, TRACK_LANGUAGES, Language, Track
from codeverse3d.contracts.plan import AssetPlan, BBox, PartPlan, ScenePlan, StaticPlan
from codeverse3d.contracts.spec import Constraints, Spec
from codeverse3d.conventions import MAX_TRIS_OBJECT, OBJECT_VIEWS_QUICK, to_pascal, to_snake
from codeverse3d.orchestrator import BudgetExceeded
from codeverse3d.proc import fan_out, read_json_or_none, write_json_atomic
from codeverse3d.prompts import render
from codeverse3d.prompts.catalog import language_text
from codeverse3d.tracks.common import (
    RunContext,
    generate_for,
    single_shot_ctx,
)
from codeverse3d.tracks.generation import GenerationTask, is_model_outage, is_single_shot
from codeverse3d.tracks.planner import plan as run_planner
from codeverse3d.tracks.prompting import (
    base_prompt_context,
    constraints_text,
    current_files,
    language_system_prompt,
    reference_images,
    skeleton_files,
)
from codeverse3d.tracks.repair import build_with_repair
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

MAX_ASSETS = 8
#: cap once the baseline has spent its soft sub-budget
DEGRADED_MAX_ASSETS = 4
#: an asset smaller than this share of the scene bbox volume is not worth a judge call
JUDGE_VOLUME_FRACTION = 0.05
#: the plan's largest module is judged whatever its share, once at least this big
JUDGE_MIN_VOLUME_M3 = 1.0
ASSET_AGENT_TIMEOUT_S = 420
#: no single asset session may take more than this share of the run (the stage waits for its slowest asset)
ASSET_SESSION_SHARE = 0.15
ASSET_RUBRIC = "asset"
#: asset kinds that are a MODULE in the scene workspace; ``blender_glb`` is the hero, shipped as a GLB
MODULE_KINDS: frozenset[str] = frozenset({"threejs", "bpy"})
#: languages whose asset sessions share the scene workspace (tasks are ``edit_only``); a hero owns its sub-workspace
SCENE_LANGUAGES: frozenset[Language] = frozenset(TRACK_LANGUAGES[Track.SCENE])


def asset_timeout_s(ctx: Any, floor_s: int) -> int:
    share = ctx.budget.budget.max_minutes * 60.0 * ASSET_SESSION_SHARE
    return ctx.budget.timeout_s(min(ASSET_AGENT_TIMEOUT_S, share), floor_s=floor_s)


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
    judged: bool = Field(default=False, description="a non-degraded verdict was recorded in `score`")
    score_before: float | None = Field(default=None, description="the verdict the fix pass improved on (score = after)")
    clips: int = Field(default=0, description="glTF animation clips the hero GLB carries (a keyframed Blender part)")




def asset_api_summary(plan: ScenePlan, results: dict[str, AssetResult], alias: dict[str, str] | None = None, *,
                      language: Language = Language.SCENE_THREEJS) -> str:
    alias = alias or {}
    lines = []
    for a in plan.assets:
        kept = alias.get(a.name)
        r = results.get(kept or a.name)
        size = (r.size_m if r and r.size_m and not kept else a.approx_size_m)
        s = f"{size[0]:.2f}×{size[1]:.2f}×{size[2]:.2f} m (w×h×d)"
        status = "" if (r and r.ok) else "  [NOT AVAILABLE — do not reference]"
        if language is Language.SCENE_BLENDER:
            lines.append(_bpy_api_line(a, r, kept, s, status))
        elif kept:
            lines.append(f"- {a.name}: use `build{to_pascal(kept)}(THREE, {{ variant: {variant_index(alias, a.name)} }})` "
                         f"from './assets/{to_snake(kept)}.js' (merged variant), base at y=0, {s}{status}")
        elif a.kind == "threejs":
            lines.append(f"- {a.name}: `import {{ build{to_pascal(a.name)} }} from './assets/{to_snake(a.name)}.js'` → Group, base at y=0, {s}{status}")
        else:
            motion = f", carries {r.clips} motion clip(s) (played automatically; `clone.userData.clipOffset` de-phases copies)" if r and r.clips else ""
            lines.append(f"- {a.name}: preloaded at `ctx.assets['{to_snake(a.name)}']` (clone it; GLB public/assets/{to_snake(a.name)}.glb), "
                         f"base at y=0, {s}{motion}{status}")
    if lines and language is Language.SCENE_BLENDER:
        place = ("place it as a collection-instance Empty (`bpy.data.objects.new(name, None)` with `instance_type = 'COLLECTION'` "
                 "and `instance_collection = ctx.assets['<snake>']`), linked into ctx.collection and seated on "
                 "ctx.height_at(x, y) = ONE placement row.")
        lines.insert(0, "Sizes are width × height × depth in metres, the plan's Y-up order: in your Blender frame x = width, "
                        "y = depth, z = height.  Every entry is a Collection built at the origin with its base on z = 0 and its "
                        "front facing -Y; " + place)
    return "\n".join(lines) or "(no assets)"


def _bpy_api_line(a: AssetPlan, r: AssetResult | None, kept: str | None, size: str, status: str) -> str:
    """One asset's line of the bpy asset API (see ``asset_api_summary``)."""
    snake = to_snake(a.name)
    if kept:
        return (f"- {a.name}: `ctx.assets['{snake}']` — variant {to_snake(kept)} builds (its module `src/assets/{snake}.py` is a "
                f"harness shim onto `build_{to_snake(kept)}`), {size}{status}")
    if a.kind == "blender_glb":
        motion = f", carries {r.clips} keyframed motion clip(s) (its action plays in every instance)" if r and r.clips else ""
        return (f"- {a.name}: `ctx.assets['{snake}']` — the hero GLB `public/assets/{snake}.glb` imported by the harness "
                f"into the Blender frame, {size}{motion}{status}")
    return f"- {a.name}: `ctx.assets['{snake}']` — built by the harness from `src/assets/{snake}.py` (`build_{snake}(ctx, variant=0)`), {size}{status}"


def run_asset_stage(ctx: RunContext) -> dict[str, AssetResult]:
    """Generate every asset (parallel) and return results keyed by asset name."""
    plan: ScenePlan = ctx.plan  # type: ignore[assignment]
    cap = MAX_ASSETS if ctx.budget.soft_ok() else DEGRADED_MAX_ASSETS
    planned = list(plan.assets)
    assets, alias = select_assets(planned, cap)
    write_dedupe_note(ctx.ws, alias)
    if alias:
        ctx.events.emit("assets.deduped", merged=alias, kept=[a.name for a in assets])
    built = {a.name for a in assets} | set(alias)
    if len(planned) > len(built):
        ctx.events.emit("assets.capped", n=len(planned), cap=cap, dropped=[a.name for a in planned if a.name not in built])
    ctx.extra["asset_alias"] = alias
    if not assets:
        return {}

    def _one(asset: AssetPlan) -> AssetResult:
        if asset.kind == "blender_glb":
            return build_blender_asset(ctx, asset, judge=True)
        return build_module_asset(ctx, asset, judge=True)

    workers = ctx.settings.limits.max_parallel_agents
    if any(a.kind in ("blender_glb", "bpy") for a in assets):   # both kinds check in a Blender process
        workers = min(workers, ctx.settings.limits.max_parallel_builds)
    results = fan_out(assets, _one, max_workers=workers, label="assets", item_name=lambda a: a.name)
    out: dict[str, AssetResult] = {}
    for asset, r in zip(assets, results, strict=True):
        if isinstance(r, Exception):
            # a failed worker is a failed asset: siblings' modules are still shimmed + committed before the boundary check stops the run
            out[asset.name] = AssetResult(name=asset.name, kind=asset.kind, ok=False, notes=f"{type(r).__name__}: {r}")
            ctx.events.emit("asset.failed", asset=asset.name, error=f"{type(r).__name__}: {r}")
        else:
            out[asset.name] = r
    shims = write_variant_shims(ctx.ws, alias, {n for n, r in out.items() if r.ok}, language=ctx.language)
    _record_spec_hashes(ctx, out, assets)  # the reuse guard's identity registry (rounds/ bookkeeping)
    ctx.ws.commit("assets")
    ctx.events.emit("assets.done", ok=[n for n, r in out.items() if r.ok], failed=[n for n, r in out.items() if not r.ok],
                    strategies={n: r.strategy for n, r in out.items() if r.strategy}, variant_shims=shims)
    ctx.budget.check()  # stage boundary: stop only after the finished assets are committed
    return out


# ------------------------------------------------------------- committed-asset identity
def _asset_spec_hash(asset: AssetPlan) -> str:
    """The reuse guard's identity: the whole plan slice, not just the file name."""
    return hashlib.sha1(asset.model_dump_json().encode()).hexdigest()[:12]


def _spec_hashes_path(ctx: RunContext) -> Path:
    return ctx.ws.root / "rounds" / "asset_spec_hashes.json"


def _spec_hash_matches(ctx: RunContext, asset: AssetPlan) -> bool:
    return (read_json_or_none(_spec_hashes_path(ctx)) or {}).get(asset.name) == _asset_spec_hash(asset)


def _record_spec_hashes(ctx: RunContext, results: dict[str, AssetResult], assets: list[AssetPlan]) -> None:
    by_name = {a.name: a for a in assets}
    path = _spec_hashes_path(ctx)
    recorded = read_json_or_none(path) or {}
    for name, r in results.items():
        if r.ok and name in by_name:
            recorded[name] = _asset_spec_hash(by_name[name])
    write_json_atomic(path, recorded)


# ----------------------------------------------------------------------------- module asset
def build_module_asset(ctx: RunContext, asset: AssetPlan, *, judge: bool) -> AssetResult:
    bpy = asset.kind == "bpy"
    rel = ctx.runtime.asset_file(asset)
    name = to_snake(asset.name) if bpy else to_pascal(asset.name)
    check_fn = check_bpy_asset if bpy else check_threejs_asset
    language = Language.SCENE_BLENDER if bpy else Language.SCENE_THREEJS
    # committed-child reuse: a resume must not re-pay a finished asset (deterministic check, no model call)
    if (ctx.ws.root / rel).is_file() and _spec_hash_matches(ctx, asset):
        chk0 = check_fn(ctx, rel, name, expected_size_m=asset.approx_size_m)
        if chk0.ran and chk0.ok:
            ctx.events.emit("asset.generated", asset=asset.name, strategy="reused", ok=True, tris=chk0.tris, errors=[])
            return AssetResult(name=asset.name, kind=asset.kind, ok=True, path=rel, size_m=chk0.size_m, strategy="reused",
                               notes=f"committed module reused ({'Blender build' if bpy else 'import'} check passed)")

    def check(c: RunContext) -> AssetCheck:
        return check_fn(c, rel, name, expected_size_m=asset.approx_size_m)

    strategy, chk, notes = _ladder(ctx, asset, rel, language=language, files=[rel], check=check,
                                   timeout_s=asset_timeout_s(ctx, 120))
    return _module_result(ctx, asset, rel, strategy, chk, notes, check=check, language=language, judge=judge)


def _module_result(ctx: RunContext, asset: AssetPlan, rel: str, strategy: str, chk: AssetCheck | None, notes: str, *,
                   check: Callable[[RunContext], AssetCheck], language: Language, judge: bool) -> AssetResult:
    """What a MODULE asset's ladder outcome means (both module kinds)."""
    if strategy == "outage":
        return AssetResult(name=asset.name, kind=asset.kind, ok=False, strategy=strategy, notes=notes)
    ok = (ctx.ws.root / rel).is_file() and not (chk is not None and chk.ran and chk.fatal)
    result = AssetResult(name=asset.name, kind=asset.kind, ok=ok, path=rel if ok else "", notes=notes, strategy=strategy,
                         size_m=chk.size_m if chk and chk.size_m else None)
    ctx.events.emit("asset.generated", asset=asset.name, strategy=strategy, ok=ok, tris=(chk.tris if chk else 0),
                    errors=(chk.errors[:2] if chk and not chk.ok else []))
    render_asset = getattr(ctx.runtime, "render_asset", None)
    if ok and callable(render_asset) and _judge_wanted(ctx, asset, chk, judge=judge):
        def _after_fix(gen: RunContext) -> bool:
            # a fix that breaks the module makes it NOT AVAILABLE; a SOFT finding is no reason to revert
            c = check(ctx)
            if c.ran and c.fatal:
                result.ok, result.path = False, ""
            return not (c.ran and c.fatal)

        def _snapshot() -> Callable[[], None]:
            before = (ctx.ws.root / rel).read_text()

            def _restore() -> None:
                (ctx.ws.root / rel).write_text(before)
                result.ok, result.path = True, rel

            return _restore

        result = _judge_and_fix(ctx, asset, result, lambda out_dir: render_asset(ctx.ws, asset.name, out_dir), files=[rel],
                                language=language, after_fix=_after_fix, snapshot=_snapshot)
    return result


def _ladder(ctx: RunContext, asset: AssetPlan, rel: str, *, language: Language, files: list[str],
            check: Callable[[RunContext], AssetCheck], timeout_s: int | None) -> tuple[str, AssetCheck | None, str]:
    """The cheap-first ladder BOTH asset kinds climb."""
    sub = single_shot_ctx(ctx)
    chk: AssetCheck | None = None
    strategy, notes = "", ""
    outage = False
    if sub is not None:
        for attempt in range(2):  # first shot + ONE error-feedback repair
            # after an outage the model wrote nothing: the second shot is the first again
            feedback = repair_feedback(chk, rel) if chk is not None and not outage else ""
            try:
                # the ladder's window bounds the single-shot call too
                res = _generate_asset(sub, asset, rel, language=language, attempt=attempt, files=files,
                                      feedback=feedback, timeout_s=timeout_s)
            except Exception as e:  # noqa: BLE001 — a bad answer escalates; a dead model does not
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
            chk = (check(sub) if res.ok
                   else AssetCheck(ok=False, ran=True, fatal=True, errors=[f"no file was written ({res.notes or 'empty answer'})"]))
            if chk.ok:
                strategy = "single-shot+repair" if feedback else "single-shot"
                break
        if not strategy and not outage:
            ctx.events.emit("asset.escalated", asset=asset.name, errors=(chk.errors[:3] if chk else []))
    if not strategy and outage:
        # model down and retries exhausted: give the asset up cheaply; zones read "NOT AVAILABLE" and build around it
        ctx.events.emit("asset.skipped_outage", asset=asset.name, errors=(chk.errors[:2] if chk else []))
        return "outage", chk, "generator unavailable (model outage); asset skipped"
    if not strategy:  # no chat model, or single-shot failed twice → the full agent session
        res = _generate_asset(ctx, asset, rel, language=language, attempt=0, files=files, timeout_s=timeout_s)
        notes = res.notes
        strategy = "escalated" if chk is not None else "agent"
        chk = check(ctx) if res.ok else chk
    return strategy, chk, notes


def _generate_asset(ctx: RunContext, asset: AssetPlan, rel: str, *, language: Language, attempt: int,
                    feedback: str = "", timeout_s: int | None = None, files: list[str] | None = None) -> Any:
    files = list(files or [rel])
    # pre-round work is round 0 (the ledger's round dimension); the attempt lives in the label
    label = f"asset_{to_snake(asset.name)}" + (f"_retry{attempt}" if attempt else "")
    prompt = _asset_prompt(ctx, asset, rel, language=language, files=files)
    if feedback:
        prompt = prompt + "\n\n" + feedback + "## Current file(s) (rewrite COMPLETELY)\n" + _inline(ctx, files)
    task = GenerationTask(label=label, prompt=prompt,
                          system=_asset_system(ctx, language),
                          files_hint=files, round=0, kind="asset", temperature=0.5, timeout_s=timeout_s,
                          images=reference_images(ctx),
                          # scene-language modules share the scene workspace (a stray write would
                          # hit zones/env); blender heroes own their whole sub-workspace
                          edit_only=language in SCENE_LANGUAGES)
    return generate_for(ctx, task)


def _inline(ctx: RunContext, files: list[str]) -> str:
    return "".join(f"--- {rel} ---\n```\n{body}\n```\n" for rel, body in current_files(ctx, files, 24_000).items())


# ----------------------------------------------------------------------------- blender asset (hero)
#: a hero may spend more triangles than an instanced prop (the number the prompt states)
HERO_MAX_TRIS = 40_000
HERO_ENTRY = ENTRY_FILE[Language.BLENDER]
HERO_GROUND_TOL_M = 0.02
#: a hero plan with this many parts or fewer, for a sheet naming more features, is asked again once
HERO_THIN_PLAN_PARTS = 2


def build_blender_asset(ctx: RunContext, asset: AssetPlan, *, judge: bool) -> AssetResult:
    snake = to_snake(asset.name)
    # committed-child reuse: a measurable GLB at this path means the build already finished
    glb = ctx.ws.root / ctx.runtime.asset_file(asset)
    if glb.is_file() and glb.stat().st_size > 0 and _spec_hash_matches(ctx, asset) \
            and (size := _measure_size(ctx, glb)) is not None:
        ctx.events.emit("asset.generated", asset=asset.name, strategy="reused", ok=True, tris=0, errors=[])
        return AssetResult(name=asset.name, kind=asset.kind, ok=True, path=ctx.runtime.asset_file(asset), size_m=size,
                           strategy="reused", notes="committed GLB reused")
    w, h, d = asset.approx_size_m
    sub_ws = Workspace(ctx.ws.root / "_assets" / snake).create()
    runtime = ctx.services.runtime(Language.BLENDER)
    # the sub-spec IS the asset sheet; the scene's brief and style travel in `extra`
    sub_spec = Spec(id=f"{ctx.spec.id}-asset-{snake}", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                    prompt=asset.description, references=list(ctx.spec.references),
                    constraints=Constraints(style=ctx.spec.constraints.style,
                                            dimensions_m={"width": w, "height": h, "depth": d}),
                    budget=ctx.spec.budget, backends=ctx.spec.backends)
    sub_ws.write_json(sub_ws.spec_path, sub_spec)
    sub = replace(ctx, spec=sub_spec, ws=sub_ws, runtime=runtime, plan=None, track=Track.STATIC_OBJECT,
                  contract_text=language_text(Language.BLENDER, "contract.md"),
                  tool_cards=ctx.services.tool_cards(Track.STATIC_OBJECT.value, Language.BLENDER.value),
                  extra={"scene_brief": ctx.spec.prompt, "scene_style": constraints_text(ctx.spec)})
    sub.plan = hero_plan(sub, asset)
    sub_ws.write_json(sub_ws.plan_path, sub.plan)  # the sub-run's plan.json IS this plan (tools, contract gate, build)
    if not (sub_ws.root / HERO_ENTRY).is_file():  # a re-entry keeps the previous session's work
        from codeverse3d.languages.blender import write_blender_skeleton

        # the hero's ground tolerance (20 mm sink rule, as its module twin)
        write_blender_skeleton(sub_ws, sub.plan, ground_tol_m=HERO_GROUND_TOL_M)
        sub_ws.commit("skeleton")
    if not ctx.single_shot:
        # language contract alone: harness_contract.md's ±1 cm bbox acceptance contradicts the prop sheet's ±5 %
        ctx.services.materialize(sub_ws, agent_kind=ctx.agent_kind, contract_md=sub.contract_text)
    files = sub.runtime.expected_files(sub.plan)
    timeout_s = asset_timeout_s(ctx, 180)
    label = f"asset_{snake}"

    def check(c: RunContext) -> AssetCheck:
        return _blender_check(c, asset, files, label=label, timeout_s=timeout_s)

    strategy, chk, notes = _ladder(sub, asset, HERO_ENTRY, language=Language.BLENDER, files=files, check=check,
                                   timeout_s=timeout_s)
    if strategy == "outage":
        return AssetResult(name=asset.name, kind=asset.kind, ok=False, strategy=strategy, notes=notes)
    ok = chk is not None and bool(chk.glb) and not chk.fatal
    ctx.events.emit("asset.generated", asset=asset.name, strategy=strategy, ok=ok, tris=(chk.tris if chk else 0),
                    errors=(chk.errors[:2] if chk and not chk.ok else []))
    if not ok:
        why = "; ".join(chk.errors[:2]) if chk and chk.errors else (notes or "no build")
        return AssetResult(name=asset.name, kind=asset.kind, ok=False, strategy=strategy, notes=f"build failed: {why}"[:300])
    sub_ws.commit("asset built")
    rel = ctx.runtime.asset_file(asset)
    dest = _copy_glb(ctx.ws, Path(chk.glb), rel)
    result = _stamp_glb(AssetResult(name=asset.name, kind=asset.kind, ok=True, path=rel, size_m=chk.size_m, strategy=strategy,
                                    notes=notes), ctx, dest)
    if _judge_wanted(ctx, asset, chk, judge=judge):
        def _render(out_dir: Path):
            return ctx.services.render_object(dest, out_dir, views=OBJECT_VIEWS_QUICK, width=512, height=512)

        def _after_fix(gen: RunContext) -> bool:
            oc = build_with_repair(gen, round_index=1, label=f"{label}_fix", files_hint=files,
                                   max_attempts=0 if gen.single_shot else 1, timeout_s=timeout_s)
            if not (oc.build.ok and oc.build.glb_path):
                return False
            _copy_glb(ctx.ws, Path(oc.build.glb_path), rel)
            sub_ws.commit("asset fix")
            _stamp_glb(result, ctx, dest)
            return True

        def _snapshot() -> Callable[[], None]:
            keep = sub_ws.artifacts / "object.before_fix.glb"
            shutil.copyfile(dest, keep)
            src = {f: _read(sub_ws, f) for f in files}

            def _restore() -> None:
                shutil.copyfile(keep, dest)
                for f, body in src.items():
                    if body:
                        (sub_ws.root / f).write_text(body)
                sub_ws.commit("asset fix reverted")
                _stamp_glb(result, ctx, dest)

            return _restore

        result = _judge_and_fix(sub, asset, result, _render, files=files, language=Language.BLENDER, after_fix=_after_fix,
                                measure_fn=lambda: ctx.services.measure(dest), snapshot=_snapshot)
    return result


def hero_plan(sub: RunContext, asset: AssetPlan) -> StaticPlan:
    """The static-object planner's part list for a hero, or the one-part sheet when it fails."""
    try:
        model = sub.services.chat_model(sub.spec.backends.planner)
        plan = run_planner(sub.spec, sub.spec.backends.planner, StaticPlan, sub.ws, model=model, events=sub.events,
                           budget=sub.budget)
        features = hero_features(asset.description)
        if len(plan.parts) <= HERO_THIN_PLAN_PARTS and len(features) > HERO_THIN_PLAN_PARTS:
            # a one-part answer for a multi-feature sheet: ask once more with the features as the checklist
            sub.events.emit("asset.plan_thin", asset=asset.name, n_parts=len(plan.parts), features=features)
            spec2 = sub.spec.model_copy(update={"constraints": sub.spec.constraints.model_copy(update={"must_have": features})})
            plan = run_planner(spec2, spec2.backends.planner, StaticPlan, sub.ws, model=model, events=sub.events,
                               budget=sub.budget)
    except Exception as e:  # noqa: BLE001 — PlanningError / outage: the sheet still says what the prop is
        if isinstance(e, BudgetExceeded):
            raise
        log.warning("hero planner failed for %s, using the one-part sheet: %s", asset.name, e)
        sub.events.emit("asset.plan_failed", asset=asset.name, error=f"{type(e).__name__}: {e}"[:300])
        return asset_plan(asset)
    sub.events.emit("asset.planned", asset=asset.name, n_parts=len(plan.parts))
    return plan


def hero_features(description: str, limit: int = 8) -> list[str]:
    """The features an asset sheet names — its clauses of two words or more — as a checklist."""
    out: list[str] = []
    for clause in re.split(r"[;,]|\band\b|\bwith\b|\bplus\b", description):
        words = clause.strip(" .:()").split()
        if len(words) >= 2 and clause.strip() not in out:
            out.append(" ".join(words))
        if len(out) >= limit:
            break
    return out


def _blender_check(ctx: RunContext, asset: AssetPlan, files: list[str], *, label: str, timeout_s: int) -> AssetCheck:
    outcome = build_with_repair(ctx, round_index=0, label=label, files_hint=files,
                                max_attempts=0 if ctx.single_shot else 1, timeout_s=timeout_s)
    b = outcome.build
    if not b.ok or not b.glb_path:
        errors = [f"{b.error_type}: {b.error_message}"[:300] if b.error_message else "the build produced no GLB"]
        errors += [f"{f.target or 'lint'}: {f.message}"[:200] for f in outcome.lint.errors][:3]
        return AssetCheck(ok=False, ran=True, fatal=True, errors=errors)
    chk = AssetCheck(ok=True, ran=True, glb=str(b.glb_path))
    try:
        m = ctx.services.measure(Path(b.glb_path))
    except Exception as e:  # noqa: BLE001 — a GLB that cannot be measured is still a GLB
        chk.warnings.append(f"measure unavailable: {type(e).__name__}")
        return chk
    chk.size_m = (round(m.extents[0], 3), round(m.extents[1], 3), round(m.extents[2], 3))
    chk.min_y, chk.tris, chk.meshes, chk.materials = round(m.bbox_min[1], 3), m.tri_count, m.n_meshes, m.materials
    _soft_findings(chk, asset.approx_size_m, max_tris=HERO_MAX_TRIS)
    return chk


def asset_plan(asset: AssetPlan) -> StaticPlan:
    """A one-part StaticPlan for the asset in Blender's Z-up frame (scene sizes are Y-up w×h×d)."""
    w, h, d = asset.approx_size_m
    bbox = BBox(center=(0.0, 0.0, h / 2), extents=(w, d, h))
    return StaticPlan(object_name=to_pascal(asset.name), summary=asset.description, overall_bbox=bbox,
                      parts=[PartPlan(name=to_pascal(asset.name), role="whole asset", description=asset.description, bbox=bbox)],
                      acceptance=[])


# ----------------------------------------------------------------------------- judge + fix
def _judge_wanted(ctx: RunContext, asset: AssetPlan, chk: AssetCheck | None, *, judge: bool) -> bool:
    """Stop paying for what is already good."""
    if not judge:
        return False
    if not ctx.budget.soft_ok():
        ctx.events.emit("asset.judge_skipped", asset=asset.name, reason="soft_budget")
        return False
    if asset.kind == "blender_glb":
        return True
    frac = _volume_fraction(ctx, asset)
    # the plan's LARGEST module (at least JUDGE_MIN_VOLUME_M3) keeps its verdict whatever its share
    w, h, d = asset.approx_size_m
    dominant = asset.name == _largest_module(ctx) and w * h * d >= JUDGE_MIN_VOLUME_M3
    if chk is not None and chk.ok and chk.ran and frac < JUDGE_VOLUME_FRACTION and not dominant:
        ctx.events.emit("asset.judge_skipped", asset=asset.name, reason="gates_ok_and_small",
                        volume_fraction=round(frac, 5), tris=chk.tris, meshes=chk.meshes)
        return False
    return True


def _largest_module(ctx: RunContext) -> str:
    """The name of the plan's biggest module asset (``MODULE_KINDS``) by planned volume ('' when none)."""
    mods = [a for a in (getattr(ctx.plan, "assets", None) or []) if a.kind in MODULE_KINDS]
    if not mods:
        return ""
    return max(mods, key=lambda a: a.approx_size_m[0] * a.approx_size_m[1] * a.approx_size_m[2]).name


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
                   language: Language, after_fix: Callable[[RunContext], bool] | None = None,
                   measure_fn: Callable[[], Any] | None = None,
                   snapshot: Callable[[], Callable[[], None]] | None = None) -> AssetResult:
    """Quick-sheet judge with asset (n_samples=1)."""
    from codeverse3d.judges.base import JudgeInput

    events = ctx.events
    try:
        judge = ctx.services.judge(ctx.track, ASSET_RUBRIC, ctx.spec.backends.judge, n_samples=1)
    except Exception as e:  # noqa: BLE001 — asset judging is optional polish
        events.emit("asset.judge_unavailable", asset=asset.name, error=str(e))
        return result
    out_dir = ctx.ws.renders_dir(0) / "assets" / to_snake(asset.name)

    def _verdict(round_index: int) -> Judgment | None:
        d = out_dir if round_index == 0 else out_dir.with_name(out_dir.name + "_fix")
        d.mkdir(parents=True, exist_ok=True)
        try:
            renders = render_fn(d)
            # the measurement the rubric's proportions criterion reads; re-measured after a fix
            measurement = measure_fn() if measure_fn is not None else None
            verdict: Judgment = judge.judge(JudgeInput(spec=ctx.spec, renders=renders, round_index=round_index,
                                                      measurement=measurement,
                                                      plan_summary=f"{asset.name}: {asset.description}",
                                                      extra_context=f"Expected size ≈ {asset.approx_size_m} m (w×h×d)."))
        except Exception as e:  # noqa: BLE001
            events.emit("asset.judge_failed", asset=asset.name, error=f"{type(e).__name__}: {e}")
            return None
        write_json_atomic(d / "judge.json", verdict.model_dump(mode="json"))  # replayable, like a round's judge/rNN.json
        if verdict.degraded:
            # a degraded verdict is no verdict: score stays None and the fix pass is skipped
            events.emit("asset.judge_degraded", asset=asset.name)
            return None
        events.emit("asset.judged", asset=asset.name, round=round_index, score=round(verdict.overall, 3), passed=verdict.passed)
        return verdict

    verdict = _verdict(0)
    if verdict is None:
        return result
    result.score = verdict.overall
    result.judged = True
    if verdict.passed or not verdict.improvement_plan:
        return result
    instructions = [f"- {i.target}: {i.instruction}" for i in verdict.improvement_plan[:4]]
    gen = single_shot_ctx(ctx) or ctx
    current = _inline(gen, files) if gen.single_shot else ""
    prompt = render(asset_template(language), **base_prompt_context(
        gen, asset_name=asset.name, asset_kind=asset.kind, asset_description=asset.description,
        asset_size=asset.approx_size_m, asset_file=files[0], asset_files=files, asset_language=language.value,
        fix_instructions=instructions, current_code=current, skeleton_files={}, library_rows="", **_scene_context(ctx)))
    task = GenerationTask(label=f"asset_{to_snake(asset.name)}_fix", prompt=prompt, system=_asset_system(gen, language),
                          files_hint=files, round=0, kind="asset_fix", temperature=0.4,
                          edit_only=language in SCENE_LANGUAGES, timeout_s=asset_timeout_s(ctx, 120))
    restore = snapshot() if snapshot is not None else None   # BEFORE the fix rewrites the files
    res = generate_for(gen, task)
    if not res.ok:
        return result
    result.fixed = after_fix(gen) if after_fix is not None else True
    if not result.fixed:
        if restore is not None:
            restore()   # a fix that does not build is undone too: the asset that was judged stays the asset
        return result
    again = _verdict(1)
    if again is None:
        return result
    if again.overall < (result.score or 0.0) and restore is not None:
        # a fix that judges WORSE is undone
        restore()
        result.fixed = False
        events.emit("asset.fix_reverted", asset=asset.name, before=round(result.score or 0.0, 3), after=round(again.overall, 3))
        return result
    result.score_before, result.score = result.score, again.overall
    return result


# ----------------------------------------------------------------------------- helpers
def asset_template(language: Language) -> str:
    """The asset-session template of a scene language (``tracks/scene.SCENE_TEMPLATES``)."""
    from codeverse3d.tracks.scene import (
        SCENE_TEMPLATES,  # scene imports this module: resolved at call time
    )

    return SCENE_TEMPLATES.get(language, SCENE_TEMPLATES[Language.SCENE_THREEJS]).asset


def _asset_system(ctx: RunContext, language: Language) -> str:
    """The system prompt for one asset generation — same language, DIFFERENT task."""
    if language is Language.SCENE_THREEJS:
        return language_text(Language.SCENE_THREEJS, "asset.md").strip()
    if language is Language.SCENE_BLENDER:
        return (language_text(Language.SCENE_BLENDER, "asset.md").strip() + "\n\n"
                + render("system/role_asset.j2", language=language.value).strip())
    return language_system_prompt(Language.BLENDER, role="asset", tools=not is_single_shot(ctx.agent_id))


def _scene_context(ctx: RunContext) -> dict[str, Any]:
    """The SCENE's brief and style for an asset prompt, whichever spec ``ctx`` carries."""
    brief = ctx.extra.get("scene_brief")
    return {"spec_prompt": brief, "constraints": ctx.extra.get("scene_style") or "(none)"} if brief else {}


#: what names a library row: a camelCase factory after a backtick (a bare lowercase word is ordinary prose), and the ``lib/`` module
_CATALOG_RES: dict[Language, tuple[re.Pattern[str], re.Pattern[str]]] = {
    Language.SCENE_THREEJS: (re.compile(r"`([a-z]+[A-Z]\w*)[(`]"), re.compile(r"`lib/(\w+\.js)`")),
}


def library_rows(description: str, language: Language = Language.SCENE_THREEJS) -> str:
    if language not in _CATALOG_RES:
        return ""
    call_re, module_re = _CATALOG_RES[language]
    words = set(re.findall(r"\w+(?:\.\w+)*", description))
    return "\n".join(line for line in language_text(language, "effects_catalog.md").splitlines()
                     if line.startswith("| ")
                     and words & (set(call_re.findall(line)) | set(module_re.findall(line))))


def _asset_prompt(ctx: RunContext, asset: AssetPlan, rel: str, *, language: Language, files: list[str] | None = None) -> str:
    files = list(files or [rel])
    prompt = render(asset_template(language), **base_prompt_context(
        ctx, asset_name=asset.name, asset_kind=asset.kind, asset_description=asset.description, asset_size=asset.approx_size_m,
        library_rows=library_rows(asset.description, language),
        asset_file=rel, asset_files=files, asset_language=language.value, fix_instructions=[], current_code="",
        # a single-shot hero has to see the multi-file skeleton it is filling in, like a static object
        skeleton_files=skeleton_files(ctx) if (ctx.single_shot and len(files) > 1) else {},
        **_scene_context(ctx)))
    ctx.record_prompt("scene_asset", prompt)
    return prompt


def _stamp_glb(result: AssetResult, ctx: RunContext, glb: Path) -> AssetResult:
    """What the shipped GLB says about itself: its measured size and the clips it carries."""
    result.size_m = _measure_size(ctx, glb) or result.size_m
    result.clips = glb_clip_count(glb)
    return result


def glb_clip_count(glb: Path) -> int:
    """How many animation clips a GLB carries (its JSON chunk; no loader needed)."""
    try:
        b = glb.read_bytes()
        if b[:4] != b"glTF":
            return 0
        (ln,) = struct.unpack_from("<I", b, 12)
        return len(json.loads(b[20:20 + ln]).get("animations") or [])
    except (OSError, ValueError, struct.error):
        return 0


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


# ===================================================================== cheap asset generation
#: max triangles for ONE asset instance (the scene contract's budget)
ASSET_MAX_TRIS = 15_000


# ----------------------------------------------------------------------------- deterministic check
class AssetCheck(BaseModel):
    """Result of importing the asset module and calling its builder."""

    ok: bool = False
    ran: bool = Field(default=False, description="False = the checker itself could not run (node missing)")
    fatal: bool = Field(default=False, description="the module does not import / build at all — zones must not use it")
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    size_m: tuple[float, float, float] | None = None
    min_y: float | None = None
    tris: int = Field(default=0, description="triangles the asset built itself (the per-asset budget)")
    lib_tris: int = Field(default=0, description="triangles the effect-library factories it composes built (D100)")
    meshes: int = 0
    materials: int = 0
    glb: str = Field(default="", description="the built GLB (blender heroes), '' for a module")

    def report(self, limit: int = 6) -> str:
        return "\n".join(f"- {e}" for e in (self.errors + self.warnings)[:limit])


def check_threejs_asset(ctx: RunContext, rel: str, pascal: str, *, timeout_s: float = 60.0,
                        expected_size_m: tuple[float, float, float] | None = None) -> AssetCheck:
    """Import ``rel`` in node, call ``build<Pascal>(THREE, {})`` and inspect the group."""
    path = ctx.ws.root / rel
    if not path.is_file():
        return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"{rel} was not written"])
    try:
        from codeverse3d.spatial.node import run_node, runtime_js_dir

        lifecycle = ctx.ws.src / "lib" / "lifecycle.js"
        args = [path.resolve().as_uri(), f"build{pascal}"] + ([lifecycle.resolve().as_uri()] if lifecycle.is_file() else [])
        res = run_node(runtime_js_dir() / "lib" / "asset_check.mjs", args, cwd=ctx.ws.root,
                       three_hook=True, timeout_s=timeout_s, check=False)
        data = res.last_json
        if data is None:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
            return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"the module failed to load: {' / '.join(tail)[:300]}"])
        chk = AssetCheck(ok=bool(data.get("ok")), ran=True, errors=list(data.get("errors") or []),
                         warnings=list(data.get("warnings") or []),
                         size_m=tuple(data["size_m"]) if data.get("size_m") else None,  # type: ignore[arg-type]
                         min_y=data.get("min_y"), tris=int(data.get("tris") or 0), lib_tris=int(data.get("lib_tris") or 0),
                         meshes=int(data.get("meshes") or 0), materials=int(data.get("materials") or 0))
    except Exception as e:  # noqa: BLE001 — the checker is an optimisation, never a blocker
        log.warning("asset check could not run for %s: %s", rel, e)
        return AssetCheck(ok=True, ran=False, warnings=[f"checker unavailable: {type(e).__name__}"])
    chk.fatal = not chk.ok  # everything the JS side reports is a hard failure to import/build
    _soft_findings(chk, expected_size_m)   # …the rest is wrong-but-usable: worth a repair, not a veto
    return chk


#: probe zone of a bpy asset check: one collection instance at the origin, so the census GLB carries exactly the asset's geometry
BPY_CHECK_ZONE = "check"


def _bpy_check_workspace(ws: Workspace, rel: str, snake: str) -> Workspace:
    from codeverse3d.languages.scene_blender import render_scene_py

    scratch = Workspace(ws.root / "_assets" / f"{snake}_check")
    if scratch.root.exists():
        shutil.rmtree(scratch.root)
    for sub in ("assets", "zones"):
        (scratch.src / sub).mkdir(parents=True, exist_ok=True)
    scratch.artifacts.mkdir(parents=True, exist_ok=True)
    (scratch.root / ENTRY_FILE[Language.SCENE_BLENDER]).write_text(render_scene_py([], [snake], [], []))
    shutil.copyfile(ws.root / rel, scratch.src / "assets" / f"{snake}.py")
    (scratch.src / "zones" / f"{BPY_CHECK_ZONE}.py").write_text(
        f'"""The harness\'s probe zone: one instance of {snake}, so the census GLB measures it."""\n'
        "import bpy\n\n\n"
        "def build(ctx):\n"
        f"    ob = bpy.data.objects.new({to_pascal(snake)!r}, None)\n"
        '    ob.instance_type = "COLLECTION"\n'
        f"    ob.instance_collection = ctx.assets[{snake!r}]\n"
        "    ctx.collection.objects.link(ob)\n"
        "    return ctx.collection\n")
    return scratch


def check_bpy_asset(ctx: RunContext, rel: str, snake: str, *, expected_size_m: tuple[float, float, float] | None = None,
                    timeout_s: float | None = None) -> AssetCheck:
    """Build ``src/assets/<snake>.py`` alone in Blender and inspect what it made."""
    path = ctx.ws.root / rel
    if not path.is_file():
        return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"{rel} was not written"])
    run_wrapper = getattr(ctx.runtime, "run_wrapper", None)
    if not callable(run_wrapper):
        return AssetCheck(ok=True, ran=False, warnings=["checker unavailable: the runtime builds no module on its own"])
    try:
        scratch = _bpy_check_workspace(ctx.ws, rel, snake)
        run = run_wrapper(scratch, scratch.artifacts, zones={BPY_CHECK_ZONE: to_pascal(BPY_CHECK_ZONE)}, blend=False,
                          timeout_s=float(timeout_s or ctx.settings.limits.build_timeout_s))
    except Exception as e:  # noqa: BLE001 — the checker is an optimisation, never a blocker
        log.warning("bpy asset check could not run for %s: %s", rel, e)
        return AssetCheck(ok=True, ran=False, warnings=[f"checker unavailable: {type(e).__name__}"])
    report = run.report or {}
    for e in report.get("errors") or []:
        if e.get("stage") == "asset" and e.get("name") == snake:
            where = f"{e.get('error_file') or rel}:{e.get('error_line')}" if e.get("error_line") else str(e.get("error_file") or rel)
            return AssetCheck(ok=False, ran=True, fatal=True,
                              errors=[f"{where}: {e.get('error_type')}: {str(e.get('error_message'))[:300]}"])
    if run.result.harness_failure:
        # the wrapper's own instrumentation failed (``CensusGlbError``), not the module: never a blocker
        log.warning("bpy asset check could not finish for %s: %s", rel, run.result.error_type)
        return AssetCheck(ok=True, ran=False, warnings=[f"checker unavailable: {run.result.error_type}"])
    if report.get("nonfinite"):
        return AssetCheck(ok=False, ran=True, fatal=True,
                          errors=[f"{report.get('error_file') or rel}: {report.get('error_type')}: "
                                  f"{str(report.get('error_message'))[:600]}"])
    if not run.result.ok:
        # the wrapper did not finish (unbounded loop, allocation past the rlimit): this module's doing
        return AssetCheck(ok=False, ran=True, fatal=True,
                          errors=[f"the module's build did not finish: {run.result.error_type}: {run.result.error_message}"[:300]])
    if any(e.get("stage") == "zone" for e in report.get("errors") or []):
        return AssetCheck(ok=False, ran=True, fatal=True,
                          errors=[f"build_{snake}(ctx) returned no Collection the scene could instance (ctx.assets[{snake!r}] is missing)"])
    chk = AssetCheck(ok=True, ran=True, tris=int((run.facts or {}).get("unique_tris") or 0))
    try:
        m = ctx.services.measure(scratch.artifacts / "census.glb")
    except Exception as e:  # noqa: BLE001 — a module that built but cannot be measured is still usable
        chk.warnings.append(f"measure unavailable: {type(e).__name__}")
        return chk
    chk.size_m = (round(m.extents[0], 3), round(m.extents[1], 3), round(m.extents[2], 3))
    chk.min_y, chk.meshes, chk.materials = round(m.bbox_min[1], 3), m.n_meshes, m.materials
    chk.tris = chk.tris or m.tri_count
    _soft_findings(chk, expected_size_m)
    return chk


def _soft_findings(chk: AssetCheck, expected: tuple[float, float, float] | None, *, max_tris: int = ASSET_MAX_TRIS) -> None:
    """Findings that make the asset WRONG but not broken → errors worth one repair."""
    if not chk.ok:
        return
    if chk.min_y is not None and chk.min_y < -0.02:
        chk.errors.append(f"the group sinks {abs(chk.min_y):.2f} m below y=0 — its lowest point must sit at y=0")
    if chk.tris > max_tris:
        chk.errors.append(f"{chk.tris} triangles exceeds the {max_tris} budget per asset — lower the segment counts")
    if chk.tris + chk.lib_tris > MAX_TRIS_OBJECT:
        chk.errors.append(f"the effect-library factories it calls build {chk.lib_tris} triangles, past the {MAX_TRIS_OBJECT} "
                          "ceiling for one object — pass their lighter options (fewer leaves, fewer leaf segments) or call fewer")
    if chk.meshes == 1 and chk.materials <= 1 and chk.tris + chk.lib_tris < 200:
        chk.errors.append("the asset is still a single low-poly box (one mesh, one material): build it from several "
                          "parts with distinct materials so it reads as the described object")
    if expected and chk.size_m:
        for axis, got, want in zip("whd", chk.size_m, expected, strict=False):
            if want > 0.05 and (got > want * 2.5 or got < want * 0.4):
                chk.errors.append(f"measured {axis}={got:.2f} m but the plan says {want:.2f} m — rescale to the planned size (±5 %)")
                break
    chk.ok = not chk.errors


def repair_feedback(chk: AssetCheck, rel: str) -> str:
    """The error-feedback block for the ONE single-shot repair attempt."""
    return (f"## The file you wrote did NOT pass the deterministic asset check\n"
            f"The harness imported `{rel}` in node and called its builder. Fix exactly these, "
            f"return the COMPLETE file again:\n{chk.report()}\n")


# ----------------------------------------------------------------------------- dedupe
#: head noun → canonical family (near-identical scene props collapse into one factory)
_FAMILY = {
    "rock": "rock", "boulder": "rock", "stone": "rock", "pebble": "rock", "rubble": "rock", "cobble": "rock",
    "tree": "tree", "sapling": "tree", "pine": "tree", "conifer": "tree", "palm": "tree",
    "bush": "bush", "shrub": "bush", "hedge": "bush", "scrub": "bush", "fern": "bush", "grass": "bush",
    "planter": "planter", "pot": "planter", "urn": "planter", "trough": "planter", "tub": "planter",
    "lamp": "lamp", "lantern": "lamp", "light": "lamp", "lightpost": "lamp", "sconce": "lamp",
    "bench": "bench", "seat": "bench", "stool": "bench", "chair": "bench",
    "crate": "crate", "box": "crate", "carton": "crate", "barrel": "crate",
    "post": "post", "pole": "post", "pillar": "post", "column": "post", "bollard": "post", "stake": "post",
    "fence": "fence", "railing": "fence", "rail": "fence", "balustrade": "fence",
    "table": "table", "desk": "table",
}


def _family(name: str) -> str:
    head = to_snake(name).split("_")[-1].rstrip("s") or to_snake(name)
    return _FAMILY.get(head, head)


def select_assets(assets: list[AssetPlan], cap: int) -> tuple[list[AssetPlan], dict[str, str]]:
    """The assets to actually build, in plan (= priority) order, plus the merge map."""
    alias: dict[str, str] = {}
    if len(assets) > cap:
        assets, alias = dedupe_assets(assets)
    if len(assets) > cap:
        kept = {a.name for a in assets[:cap]}
        assets = assets[:cap]
        alias = {k: v for k, v in alias.items() if v in kept}
    return assets, alias


def dedupe_assets(assets: list[AssetPlan]) -> tuple[list[AssetPlan], dict[str, str]]:
    """Fold near-identical props into ONE parameterised factory."""
    kept: list[AssetPlan] = []
    alias: dict[str, str] = {}
    for a in assets:
        fam = _family(a.name)
        twin = next((k for k in kept if k.kind == a.kind and _family(k.name) == fam
                     and _ratio(max(k.approx_size_m), max(a.approx_size_m)) <= 3.0), None)
        if twin is None:
            kept.append(a)
            continue
        alias[a.name] = twin.name
    if alias:
        kept = [_with_variants(k, [n for n, t in alias.items() if t == k.name],
                               {n: a for n, a in ((x.name, x) for x in assets)}) for k in kept]
    return kept, alias


def _ratio(a: float, b: float) -> float:
    lo, hi = sorted((abs(a) or 1e-6, abs(b) or 1e-6))
    return hi / lo


def _with_variants(asset: AssetPlan, dropped: list[str], by_name: dict[str, AssetPlan]) -> AssetPlan:
    """Tell the kept asset it must also serve as its merged twins (opts.variant)."""
    if not dropped:
        return asset
    lines = [f"variant {i + 1} = {n} ({by_name[n].description.strip()})" for i, n in enumerate(dropped) if n in by_name]
    extra = (f" This ONE factory must also cover: {'; '.join(lines)}. Read `opts.variant` (integer, 0 = the base "
             f"asset above, 1..{len(lines)} = the variants in that order) and `opts.scale` (number, default 1) and "
             f"change proportions/detail/colour accordingly — zone code calls the same builder with different opts.")
    return asset.model_copy(update={"description": asset.description.rstrip() + extra})


def variant_index(alias: dict[str, str], name: str) -> int:
    """1-based variant index of a merged asset within its surviving factory."""
    kept = alias.get(name)
    if not kept:
        return 0
    return sorted(n for n, t in alias.items() if t == kept).index(name) + 1


def write_dedupe_note(ws: Workspace, alias: dict[str, str]) -> None:
    """Persist the merge map the stage USED."""
    write_json_atomic(ws.root / "stages" / "asset_aliases.json", {"alias": alias})


def read_dedupe_note(ws: Workspace) -> dict[str, str]:
    """The merge map the asset stage used, or ``{}`` when no stage has run in this workspace."""
    data = read_json_or_none(ws.root / "stages" / "asset_aliases.json")
    return dict((data or {}).get("alias") or {})


def write_variant_shims(ws: Workspace, alias: dict[str, str], available: set[str], *,
                        language: Language = Language.SCENE_THREEJS) -> list[str]:
    """Replace a merged asset's skeleton stub with a shim onto the surviving factory."""
    written: list[str] = []
    for dropped, kept in sorted(alias.items()):
        if kept not in available:
            continue
        variant = variant_index(alias, dropped)
        if language is Language.SCENE_BLENDER:
            rel = f"src/assets/{to_snake(dropped)}.py"
            text = (f'"""{dropped} is a variant of {kept} (assets were merged into one factory): the harness\'s shim."""\n'
                    f"from assets.{to_snake(kept)} import build_{to_snake(kept)}\n\n\n"
                    f"def build_{to_snake(dropped)}(ctx, variant={variant}):\n"
                    f"    return build_{to_snake(kept)}(ctx, variant=variant)\n")
        else:
            rel = f"src/assets/{to_snake(dropped)}.js"
            text = (f"// {dropped} is a variant of {kept} (assets were merged into one factory).\n"
                    f"import {{ build{to_pascal(kept)} }} from './{to_snake(kept)}.js';\n\n"
                    f"export function build{to_pascal(dropped)}(THREE, opts = {{}}) {{\n"
                    f"  return build{to_pascal(kept)}(THREE, {{ variant: {variant}, ...opts }});\n}}\n")
        path = ws.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(rel)
    return written
