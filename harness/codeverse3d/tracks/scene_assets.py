"""Scene asset stage: cheap-first generation of one module per asset, fanned out.

* ``threejs`` assets → ``src/assets/<snake>.js`` exporting ``build<Pascal>`` —
  generated **single-shot** (one chat call) + a deterministic node check + ONE
  error-feedback repair; a full agent session is the escalation, not the default
  (agent sessions cost 10× and 10 minutes per file).
* ``blender_glb`` assets are **heroes**: a sub-workspace under ``<ws>/_assets/<snake>``
  runs the Blender runtime and climbs the SAME single-shot ladder (``_ladder``) before
  an agent session; the GLB is copied to ``public/assets/<snake>.glb``.

Near-identical props are merged into one factory with an ``opts.variant``; the
list is capped at ``MAX_ASSETS`` in plan (= priority) order, and harder when the
soft budget is spent.  Assets are judged only when the judge can tell us
something the deterministic check cannot (heroes, or anything big enough to
matter in frame).
"""

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
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import AssetPlan, BBox, PartPlan, ScenePlan, StaticPlan
from codeverse3d.contracts.spec import Constraints, Spec
from codeverse3d.conventions import OBJECT_VIEWS_QUICK, to_pascal, to_snake
from codeverse3d.judges.rubrics import is_degraded
from codeverse3d.languages.scene_threejs import asset_file
from codeverse3d.orchestrator import BudgetExceeded
from codeverse3d.proc import fan_out, read_json_or_none, write_json_atomic, write_text_atomic
from codeverse3d.prompts import render
from codeverse3d.prompts.catalog import language_prompt, language_text
from codeverse3d.tracks.common import (
    RunContext,
    generate_for,
    single_shot_ctx,
)
from codeverse3d.tracks.generation import GenerationTask, is_single_shot
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
#: the plan's largest module is judged whatever its share of the scene — once it is at least
#: this big (a bollard is not a hero, a boat is)
JUDGE_MIN_VOLUME_M3 = 1.0
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


def is_model_outage(e: BaseException) -> bool:
    """Is this a *service* failure (the model is down) rather than a bad answer?

    A 503 capacity storm reaches us only after ``models.retry`` has already spent its
    whole storm budget waiting, so the escalation the harness would normally do —
    a full agent session, ten times the money and ten minutes — hits the same wall.
    """
    from codeverse3d.models.base import ModelError

    if isinstance(e, ModelError):
        return bool(e.retryable) or e.status in (429, 500, 502, 503, 504, 529)
    return False


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
            motion = f", carries {r.clips} motion clip(s) (played automatically; `clone.userData.clipOffset` de-phases copies)" if r and r.clips else ""
            lines.append(f"- {a.name}: preloaded at `ctx.assets['{to_snake(a.name)}']` (clone it; GLB public/assets/{to_snake(a.name)}.glb), "
                         f"base at y=0, {s}{motion}{status}")
    return "\n".join(lines) or "(no assets)"


def run_asset_stage(ctx: RunContext, *, judge_assets: bool = True) -> dict[str, AssetResult]:
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
    """False when unrecorded (pre-fix runs regenerate once, then reuse)."""
    return (read_json_or_none(_spec_hashes_path(ctx)) or {}).get(asset.name) == _asset_spec_hash(asset)


def _record_spec_hashes(ctx: RunContext, results: dict[str, AssetResult], assets: list[AssetPlan]) -> None:
    by_name = {a.name: a for a in assets}
    path = _spec_hashes_path(ctx)
    recorded = read_json_or_none(path) or {}
    for name, r in results.items():
        if r.ok and name in by_name:
            recorded[name] = _asset_spec_hash(by_name[name])
    write_json_atomic(path, recorded)


# ----------------------------------------------------------------------------- threejs asset
def build_threejs_asset(ctx: RunContext, asset: AssetPlan, *, judge: bool) -> AssetResult:
    """Single-shot → deterministic check → ONE repair → (only then) an agent session."""
    rel, pascal = asset_file(asset), to_pascal(asset.name)
    # committed-child reuse (review-3 S2): a resume that re-enters the stage — budget
    # stop after the commit, or a failed sibling — must not re-pay a finished asset.
    # Deterministic node import-check only, no model call; the skeleton stub never
    # passes it (single low-poly box), and `ran` guards the checker-unavailable path.
    if (ctx.ws.root / rel).is_file() and _spec_hash_matches(ctx, asset):
        chk0 = check_threejs_asset(ctx, rel, pascal, expected_size_m=asset.approx_size_m)
        if chk0.ran and chk0.ok:
            ctx.events.emit("asset.generated", asset=asset.name, strategy="reused", ok=True, tris=chk0.tris, errors=[])
            return AssetResult(name=asset.name, kind=asset.kind, ok=True, path=rel, size_m=chk0.size_m,
                               strategy="reused", notes="committed module reused (import check passed)")

    def check(c: RunContext) -> AssetCheck:
        return check_threejs_asset(c, rel, pascal, expected_size_m=asset.approx_size_m)

    strategy, chk, notes = _ladder(ctx, asset, rel, language=Language.SCENE_THREEJS, files=[rel], check=check,
                                   timeout_s=asset_timeout_s(ctx, 120))
    if strategy == "outage":
        return AssetResult(name=asset.name, kind=asset.kind, ok=False, strategy=strategy, notes=notes)
    # a module that will not import is NOT AVAILABLE to zones; a merely imperfect one
    # (size off, still a bit plain) stays usable — we already spent a repair on it
    ok = (ctx.ws.root / rel).is_file() and not (chk is not None and chk.ran and chk.fatal)
    result = AssetResult(name=asset.name, kind=asset.kind, ok=ok, path=rel if ok else "", notes=notes, strategy=strategy,
                         size_m=chk.size_m if chk and chk.size_m else None)
    ctx.events.emit("asset.generated", asset=asset.name, strategy=strategy, ok=ok, tris=(chk.tris if chk else 0),
                    errors=(chk.errors[:2] if chk and not chk.ok else []))
    render_asset = getattr(ctx.runtime, "render_asset", None)
    if ok and callable(render_asset) and _judge_wanted(ctx, asset, chk, judge=judge):
        def _after_fix(gen: RunContext) -> bool:
            # a fix that breaks the module makes it NOT AVAILABLE — the zones must not import it;
            # a SOFT finding (size, tris) is no reason to revert a fix that imports (the hero's rule)
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
                                language=Language.SCENE_THREEJS, after_fix=_after_fix, snapshot=_snapshot)
    return result


def _ladder(ctx: RunContext, asset: AssetPlan, rel: str, *, language: Language, files: list[str],
            check: Callable[[RunContext], AssetCheck], timeout_s: int | None) -> tuple[str, AssetCheck | None, str]:
    """The cheap-first ladder BOTH asset kinds climb: single-shot → deterministic ``check`` →
    ONE error-feedback repair → (only then) the full agent session.

    Returns ``(strategy, last check, notes)``; ``strategy == "outage"`` means the model
    itself was down and the asset was given up before the expensive rung.  The check is
    per kind — a node import for a three.js module, a Blender build + measurement for a
    hero — and the ladder is one, which is what makes the two kinds comparable.
    """
    sub = single_shot_ctx(ctx)
    chk: AssetCheck | None = None
    strategy, notes = "", ""
    outage = False
    if sub is not None:
        for attempt in range(2):  # first shot + ONE error-feedback repair
            # after an OUTAGE the model never wrote a file: the second shot is the first shot
            # again, not a "repair" of the skeleton stub against a 503 it cannot fix
            feedback = repair_feedback(chk, rel) if chk is not None and not outage else ""
            try:
                res = _generate_asset(sub, asset, rel, language=language, attempt=attempt, files=files,
                                      feedback=feedback)
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
        # the model itself is down (503 capacity storm), and the retry layer already spent its
        # storm budget waiting: a full agent session is ten times the money and the same wall.
        # Give the asset up cheaply — the zones read "NOT AVAILABLE" and build around it.
        ctx.events.emit("asset.skipped_outage", asset=asset.name, errors=(chk.errors[:2] if chk else []))
        return "outage", chk, "generator unavailable (model outage); asset skipped"
    if not strategy:  # no chat model, or single-shot failed twice → the full agent session
        res = _generate_asset(ctx, asset, rel, language=language, attempt=0, files=files, timeout_s=timeout_s)
        notes = res.notes
        strategy = "escalated" if chk is not None else "agent"
        # a session that died in the storm mid-work (D68) left partial files: build and check
        # them, but with the CHEAP context — the check's own one build repair then goes
        # through the single-shot path, not another agent session at the wall
        chk = check(sub if res.transient and sub is not None else ctx) if res.ok else chk
        if res.transient and sub is not None and chk is not None and not chk.ok:
            # the session died in a 503 storm mid-work (D68): its partial files failed the
            # check, and another 12 minutes at the wall would too — the cheap rung answers.
            ctx.events.emit("asset.storm_repair", asset=asset.name, errors=chk.errors[:3])
            try:
                res2 = _generate_asset(sub, asset, rel, language=language, attempt=2, files=files,
                                       feedback=repair_feedback(chk, rel))
            except Exception as e:  # noqa: BLE001 — same rule as the ladder's own single-shots
                if isinstance(e, BudgetExceeded):
                    raise
                log.warning("storm repair of %s failed: %s", asset.name, e)
                return strategy, chk, notes
            if res2.ok:
                chk2 = check(sub)
                if chk2.ok:
                    return "escalated+repair", chk2, res2.notes
                chk = chk2
    return strategy, chk, notes


def _generate_asset(ctx: RunContext, asset: AssetPlan, rel: str, *, language: Language, attempt: int,
                    feedback: str = "", timeout_s: int | None = None, files: list[str] | None = None) -> Any:
    files = list(files or [rel])
    label = f"asset_{to_snake(asset.name)}" + ("_retry" if attempt else "")
    prompt = _asset_prompt(ctx, asset, rel, language=language, files=files)
    if feedback:
        prompt = prompt + "\n\n" + feedback + "## Current file(s) (rewrite COMPLETELY)\n" + _inline(ctx, files)
    task = GenerationTask(label=label, prompt=prompt,
                          system=_asset_system(ctx, language),
                          files_hint=files, round=attempt, kind="asset", temperature=0.5, timeout_s=timeout_s,
                          images=reference_images(ctx),
                          # threejs assets share the scene workspace (a stray write would hit
                          # zones/env); blender heroes own their whole sub-workspace
                          edit_only=language is Language.SCENE_THREEJS)
    return generate_for(ctx, task)


def _inline(ctx: RunContext, files: list[str]) -> str:
    """The file(s) a single-shot rewrite must return, inlined the way every other prompt
    inlines them (``prompting.current_files``: trimmed to one budget, harness-owned files
    skipped) — a hero has parts, not one file."""
    return "".join(f"--- {rel} ---\n```\n{body}\n```\n" for rel, body in current_files(ctx, files, 24_000).items())


# ----------------------------------------------------------------------------- blender asset (hero)
#: a hero may spend more triangles than an instanced prop (the number the prompt states)
HERO_MAX_TRIS = 40_000
HERO_ENTRY = "src/model.py"
HERO_GROUND_TOL_M = 0.02
#: a hero plan with this many parts or fewer, for a sheet naming more features, is asked again once
HERO_THIN_PLAN_PARTS = 2


def build_blender_asset(ctx: RunContext, asset: AssetPlan, *, judge: bool) -> AssetResult:
    """Sub-workspace → the static planner's part list → the SAME ladder a threejs asset
    climbs → the GLB checked like a module → ``public/assets/<snake>.glb``.

    Until 2026-09-07 a hero got a one-part plan, a full agent session first, and no check
    of the GLB it produced.  Measured over the 18 recorded heroes (eval/bench/out, 2026-08-25 →
    09-05): 15 first sessions killed at the asset timeout (booked at $0 — a killed CLI
    reports no usage), 12 GLBs one joined mesh with vertex paint (the one-part plan plus
    the Blender contract's "one object per plan part" leave no other way to colour it, so
    wood and iron share one roughness), median asset_v1 0.57 with 2 of 18 passing, and
    every fix prompt reading "Style of the whole scene: (none)" because the sub-spec's
    prompt IS the asset sheet.
    """
    snake = to_snake(asset.name)
    # committed-child reuse (review-3 S2), blender twin: a GLB only exists at this
    # path when a previous session finished the build+copy; a measurable one is done.
    glb = ctx.ws.root / asset_file(asset)
    if glb.is_file() and glb.stat().st_size > 0 and _spec_hash_matches(ctx, asset) \
            and (size := _measure_size(ctx, glb)) is not None:
        ctx.events.emit("asset.generated", asset=asset.name, strategy="reused", ok=True, tris=0, errors=[])
        return AssetResult(name=asset.name, kind=asset.kind, ok=True, path=asset_file(asset), size_m=size,
                           strategy="reused", notes="committed GLB reused")
    w, h, d = asset.approx_size_m
    sub_ws = Workspace(ctx.ws.root / "_assets" / snake).create()
    runtime = ctx.services.runtime(Language.BLENDER)
    # the sub-spec IS the asset sheet (the static machinery — planner, contract gate, tools —
    # reads it); the SCENE's brief and style travel in `extra` for the asset prompts
    sub_spec = Spec(id=f"{ctx.spec.id}-asset-{snake}", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                    prompt=asset.description, references=list(ctx.spec.references),
                    constraints=Constraints(style=ctx.spec.constraints.style,
                                            dimensions_m={"width": w, "height": h, "depth": d}),
                    budget=ctx.spec.budget, backends=ctx.spec.backends)
    sub_ws.write_json(sub_ws.spec_path, sub_spec)
    sub = replace(ctx, spec=sub_spec, ws=sub_ws, runtime=runtime, plan=None, track=Track.STATIC_OBJECT,
                  contract_text=language_text(Language.BLENDER, "contract.md"),
                  cookbook_rel=language_prompt(Language.BLENDER, "cookbook.md"),
                  cookbook_text=language_text(Language.BLENDER, "cookbook.md"),
                  tool_cards=ctx.services.tool_cards(Track.STATIC_OBJECT.value, Language.BLENDER.value),
                  extra={"scene_brief": ctx.spec.prompt, "scene_style": constraints_text(ctx.spec)})
    sub.plan = hero_plan(sub, asset)
    sub_ws.write_json(sub_ws.plan_path, sub.plan)  # the sub-run's plan.json IS this plan (tools, contract gate, build)
    if not (sub_ws.root / HERO_ENTRY).is_file():  # a re-entry keeps the previous session's work
        from codeverse3d.languages.blender import write_blender_skeleton

        # the object skeleton with the hero's ground tolerance: the scene seats every clone,
        # and 20 mm is the sink rule its module twin gets (measured 2026-09-07: 2.9 mm and
        # 4.0 mm cost two single-shot rungs and an agent escalation each)
        write_blender_skeleton(sub_ws, sub.plan, ground_tol_m=HERO_GROUND_TOL_M)
        sub_ws.commit("skeleton")
    if not ctx.single_shot:
        ctx.services.materialize(sub_ws, agent_kind=ctx.agent_kind, contract_md=sub.contract_text, cookbook_rel=sub.cookbook_rel,
                                 spatial_tools=True)
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
    rel = asset_file(asset)
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
    """The static-object planner's part list for a hero, or the one-part sheet when it fails.

    One planner call, the hero's own ``plan.json``: the part names the Blender contract
    demands, per-part bboxes, ``style_notes`` and an acceptance list — what a static_object
    run gets, from the code that gives it to them.
    """
    try:
        model = sub.services.chat_model(sub.spec.backends.planner)
        plan = run_planner(sub.spec, sub.spec.backends.planner, StaticPlan, sub.ws, model=model, events=sub.events,
                           budget=sub.budget)
        features = hero_features(asset.description)
        if len(plan.parts) <= HERO_THIN_PLAN_PARTS and len(features) > HERO_THIN_PLAN_PARTS:
            # a one-part answer for a prop whose sheet names several features (the windmill got
            # 'BrickBase' alone twice on 2026-09-07 while every other hero got 7-12 parts): ask
            # ONCE more with the features as the checklist, which is what sizes the planner's
            # part budget (plan_budget: must_have) and names the parts it must list
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
    """The features an asset sheet names — its clauses of two words or more — as a checklist.

    'an octagonal brick base, a thatched body, a cap with a gallery and four lattice sails'
    → four items; the planner's part budget is sized from must_have (plan_budget)."""
    out: list[str] = []
    for clause in re.split(r"[;,]|\band\b|\bwith\b|\bplus\b", description):
        words = clause.strip(" .:()").split()
        if len(words) >= 2 and clause.strip() not in out:
            out.append(" ".join(words))
        if len(out) >= limit:
            break
    return out


def _blender_check(ctx: RunContext, asset: AssetPlan, files: list[str], *, label: str, timeout_s: int) -> AssetCheck:
    """Build the hero — the agent path gets ONE error-focused repair session, the single-shot
    path none (its repair is the ladder's feedback rewrite) — then the soft findings a
    threejs module gets, from one measurement of the GLB."""
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
    # the plan's LARGEST module (of at least JUDGE_MIN_VOLUME_M3) keeps its verdict whatever its
    # share: no prop reaches 5 % of a scene's volume (measured 2026-09-07 over two scenes:
    # 1e-5 .. 2e-3), so the share rule alone judged nothing, and the biggest prop is the one
    # that dominates frames
    w, h, d = asset.approx_size_m
    dominant = asset.name == _largest_module(ctx) and w * h * d >= JUDGE_MIN_VOLUME_M3
    if chk is not None and chk.ok and chk.ran and frac < JUDGE_VOLUME_FRACTION and not dominant:
        ctx.events.emit("asset.judge_skipped", asset=asset.name, reason="gates_ok_and_small",
                        volume_fraction=round(frac, 5), tris=chk.tris, meshes=chk.meshes)
        return False
    return True


def _largest_module(ctx: RunContext) -> str:
    """The name of the plan's biggest ``threejs`` asset by planned volume ('' when none)."""
    mods = [a for a in (getattr(ctx.plan, "assets", None) or []) if a.kind == "threejs"]
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
    """Quick-sheet judge with asset_v1 (n_samples=1); ONE fix pass when below threshold,
    then the fix is checked (``after_fix(gen_ctx)``) and JUDGED AGAIN — ``score`` describes
    the asset that ships, ``score_before`` the one that was fixed.  The fix climbs the
    same cheap rung as the generation: one chat call when the run has a chat model
    (measured 2026-09-07: an agent fix session for a single-shot hero timed out at $0)."""
    from codeverse3d.judges.base import JudgeInput

    events = ctx.events
    try:
        judge = ctx.services.judge(ASSET_RUBRIC, ctx.spec.backends.judge, n_samples=1)
    except Exception as e:  # noqa: BLE001 — asset judging is optional polish
        events.emit("asset.judge_unavailable", asset=asset.name, error=str(e))
        return result
    out_dir = ctx.ws.renders_dir(0) / "assets" / to_snake(asset.name)

    def _verdict(round_index: int) -> Judgment | None:
        d = out_dir if round_index == 0 else out_dir.with_name(out_dir.name + "_fix")
        d.mkdir(parents=True, exist_ok=True)
        try:
            renders = render_fn(d)
            # the measurement the rubric's proportions criterion reads (a hero's quick sheet used to
            # arrive with "MEASUREMENTS: (none available)"); re-measured after a fix
            measurement = measure_fn() if measure_fn is not None else None
            verdict: Judgment = judge.judge(JudgeInput(spec=ctx.spec, renders=renders, round_index=round_index,
                                                      measurement=measurement,
                                                      plan_summary=f"{asset.name}: {asset.description}",
                                                      extra_context=f"Expected size ≈ {asset.approx_size_m} m (w×h×d)."))
        except Exception as e:  # noqa: BLE001
            events.emit("asset.judge_failed", asset=asset.name, error=f"{type(e).__name__}: {e}")
            return None
        # book the money non-enforcing like every other judge site (steps.py, candidates.py):
        # the verdict is already paid for, and raising here would discard it.  The stage
        # boundary enforces the ceiling.
        ctx.budget.add(verdict.usage, stage="judge")
        write_json_atomic(d / "judge.json", verdict.model_dump(mode="json"))  # replayable, like a round's judge/rNN.json
        if is_degraded(verdict):
            # a degraded verdict is no verdict: score stays None and `judged` False, and the
            # fix pass is skipped (its improvement_plan is empty by construction).
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
    prompt = render("tracks/scene_asset.j2", **base_prompt_context(
        gen, asset_name=asset.name, asset_kind=asset.kind, asset_description=asset.description,
        asset_size=asset.approx_size_m, asset_file=files[0], asset_files=files, asset_language=language.value,
        fix_instructions=instructions, current_code=current, skeleton_files={}, **_scene_context(ctx)))
    task = GenerationTask(label=f"asset_{to_snake(asset.name)}_fix", prompt=prompt, system=_asset_system(gen, language),
                          files_hint=files, round=1, kind="asset_fix", temperature=0.4,
                          edit_only=language is Language.SCENE_THREEJS, timeout_s=asset_timeout_s(ctx, 120))
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
        # a fix that judges WORSE is undone (loop 1's BronzeCenser went 0.526 → 0.43 through
        # its fix and shipped that way); `snapshot()` before the fix returned the restore
        restore()
        result.fixed = False
        events.emit("asset.fix_reverted", asset=asset.name, before=round(result.score or 0.0, 3), after=round(again.overall, 3))
        return result
    result.score_before, result.score = result.score, again.overall
    return result


# ----------------------------------------------------------------------------- helpers
def _asset_system(ctx: RunContext, language: Language) -> str:
    """The system prompt for one asset generation — same language, DIFFERENT task.

    A blender hero runs the full static-object machinery (plan, gates, judge) in its
    sub-workspace, so it gets the blender language base COMPOSED with the prop-for-a-scene
    role overlay.  A three.js asset module is not a scene build at all — the scene_threejs
    base (lighting, cameras, multi-file) would mislead it — so it gets the dedicated
    asset-module prompt instead of that base.
    """
    if language is Language.SCENE_THREEJS:
        return language_text(Language.SCENE_THREEJS, "asset.md").strip()
    return language_system_prompt(Language.BLENDER, role="asset", tools=not is_single_shot(ctx.agent_id))


def _scene_context(ctx: RunContext) -> dict[str, Any]:
    """The SCENE's brief and style for an asset prompt, whichever spec ``ctx`` carries.

    A blender hero runs in a sub-context whose spec IS the asset sheet (the static machinery
    needs that), so ``spec_prompt`` would render the asset description as the scene.  Every
    recorded hero fix prompt (18, 2026-08-25 → 09-05) read "Style of the whole scene: (none)".
    """
    brief = ctx.extra.get("scene_brief")
    return {"spec_prompt": brief, "constraints": ctx.extra.get("scene_style") or "(none)"} if brief else {}


def _asset_prompt(ctx: RunContext, asset: AssetPlan, rel: str, *, language: Language, files: list[str] | None = None) -> str:
    files = list(files or [rel])
    prompt = render("tracks/scene_asset.j2", **base_prompt_context(
        ctx, asset_name=asset.name, asset_kind=asset.kind, asset_description=asset.description, asset_size=asset.approx_size_m,
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
    tris: int = 0
    meshes: int = 0
    materials: int = 0
    glb: str = Field(default="", description="the built GLB (blender heroes), '' for a module")

    def report(self, limit: int = 6) -> str:
        return "\n".join(f"- {e}" for e in (self.errors + self.warnings)[:limit])


_CHECK_JS = r"""
import * as THREE from 'three';
const [, , fileUrl, exportName] = process.argv;
const out = { ok: false, errors: [], warnings: [], size_m: null, tris: 0, meshes: 0, materials: 0 };
try {
  const mod = await import(fileUrl);
  const fn = mod[exportName] ?? mod.default;
  if (typeof fn !== 'function') {
    out.errors.push(`missing export: this file must \`export function ${exportName}(THREE, opts = {})\` (found: ${Object.keys(mod).join(', ') || 'nothing'})`);
  } else {
    const g = fn(THREE, {});
    if (!g || !g.isObject3D) {
      out.errors.push(`${exportName}(THREE) must return a THREE.Group / Object3D (got ${Object.prototype.toString.call(g)})`);
    } else {
      const box = new THREE.Box3().setFromObject(g);
      const mats = new Set();
      let bad = 0;
      g.traverse((o) => {
        if (!o.isMesh) return;
        out.meshes += 1;
        mats.add(o.material?.uuid ?? o.material);
        const pos = o.geometry?.attributes?.position;
        if (!pos) return;
        const arr = pos.array;
        for (let i = 0; i < arr.length; i++) if (!Number.isFinite(arr[i])) { bad += 1; break; }
        const idx = o.geometry.index;
        out.tris += ((idx ? idx.count : pos.count) / 3) * (o.isInstancedMesh ? o.count : 1);
      });
      out.materials = mats.size;
      if (bad) out.errors.push(`${bad} mesh(es) have NaN/Infinity vertex positions`);
      if (out.meshes === 0) out.errors.push('the returned group contains no meshes');
      if (box.isEmpty()) out.errors.push('the returned group has an empty bounding box');
      else {
        const s = new THREE.Vector3(); box.getSize(s);
        if (!Number.isFinite(s.x + s.y + s.z)) out.errors.push('the bounding box is not finite');
        else { out.size_m = [+s.x.toFixed(3), +s.y.toFixed(3), +s.z.toFixed(3)]; out.min_y = +box.min.y.toFixed(3); }
      }
      out.tris = Math.round(out.tris);
    }
  }
} catch (e) {
  out.errors.push(`${e?.name || 'Error'}: ${e?.message || String(e)}`);
  const stack = String(e?.stack || '').split('\n').slice(1, 4).filter((l) => l.includes(fileUrl.replace('file://', '')));
  if (stack.length) out.errors.push('at ' + stack.map((l) => l.trim()).join(' / '));
}
out.ok = out.errors.length === 0;
console.log(JSON.stringify(out));
"""


def _checker_path(ctx: RunContext) -> Path:
    """The checker script lives in the harness cache, never in the workspace."""
    p = Path(ctx.settings.cache_dir) / "scene_asset_check.mjs"
    if not p.is_file() or p.read_text() != _CHECK_JS:
        # run_asset_stage fans the assets out over a thread pool, so every thread
        # used to write the SAME '<cache>/scene_asset_check.mjs.tmp' and the loser's
        # replace() raised FileNotFoundError -- swallowed below into ok=True, ran=False.
        write_text_atomic(p, _CHECK_JS)
    return p


def check_threejs_asset(ctx: RunContext, rel: str, pascal: str, *, timeout_s: float = 60.0,
                        expected_size_m: tuple[float, float, float] | None = None) -> AssetCheck:
    """Import ``rel`` in node, call ``build<Pascal>(THREE, {})`` and inspect the group.

    Never raises: an unavailable node runtime yields ``ran=False, ok=True`` so the
    strategy falls back to "the scene build will catch it"."""
    path = ctx.ws.root / rel
    if not path.is_file():
        return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"{rel} was not written"])
    try:
        from codeverse3d.spatial.node import run_node

        res = run_node(_checker_path(ctx), [path.resolve().as_uri(), f"build{pascal}"], cwd=ctx.ws.root,
                       three_hook=True, timeout_s=timeout_s, check=False)
        data = res.last_json
        if data is None:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
            return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"the module failed to load: {' / '.join(tail)[:300]}"])
        chk = AssetCheck(ok=bool(data.get("ok")), ran=True, errors=list(data.get("errors") or []),
                         warnings=list(data.get("warnings") or []),
                         size_m=tuple(data["size_m"]) if data.get("size_m") else None,  # type: ignore[arg-type]
                         min_y=data.get("min_y"), tris=int(data.get("tris") or 0),
                         meshes=int(data.get("meshes") or 0), materials=int(data.get("materials") or 0))
    except Exception as e:  # noqa: BLE001 — the checker is an optimisation, never a blocker
        log.warning("asset check could not run for %s: %s", rel, e)
        return AssetCheck(ok=True, ran=False, warnings=[f"checker unavailable: {type(e).__name__}"])
    chk.fatal = not chk.ok  # everything the JS side reports is a hard failure to import/build
    _soft_findings(chk, expected_size_m)   # …the rest is wrong-but-usable: worth a repair, not a veto
    return chk


def _soft_findings(chk: AssetCheck, expected: tuple[float, float, float] | None, *, max_tris: int = ASSET_MAX_TRIS) -> None:
    """Findings that make the asset WRONG but not broken → errors worth one repair."""
    if not chk.ok:
        return
    if chk.min_y is not None and chk.min_y < -0.02:
        chk.errors.append(f"the group sinks {abs(chk.min_y):.2f} m below y=0 — its lowest point must sit at y=0")
    if chk.tris > max_tris:
        chk.errors.append(f"{chk.tris} triangles exceeds the {max_tris} budget per asset — lower the segment counts")
    if chk.meshes == 1 and chk.materials <= 1 and chk.tris < 200:
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
    """The assets to actually build, in plan (= priority) order, plus the merge map.

    Dedupe is a *rescue*, not a default: while the plan fits under ``cap`` every planned
    prop is built (single-shot assets are nearly free, and a distinct prop is worth more
    than a variant flag).  Only an over-long list is folded — turning "drop the 9th
    asset" into "the 9th asset is a variant of the 3rd" — and whatever is still over the
    cap is truncated by priority."""
    alias: dict[str, str] = {}
    if len(assets) > cap:
        assets, alias = dedupe_assets(assets)
    if len(assets) > cap:
        kept = {a.name for a in assets[:cap]}
        assets = assets[:cap]
        alias = {k: v for k, v in alias.items() if v in kept}
    return assets, alias


def dedupe_assets(assets: list[AssetPlan]) -> tuple[list[AssetPlan], dict[str, str]]:
    """Fold near-identical props into ONE parameterised factory.

    Two assets merge when their head nouns belong to the same family AND their
    largest dimensions are within 3× — "HeroBoulder" (2.5 m) and "TalusRock"
    (0.5 m) are different props; "SteppingStone" and "PondRock" are one factory
    with a variant.  Plan order is priority: the first one survives.
    Returns ``(kept_assets, {dropped_name: kept_name})``."""
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
    """Persist the merge map the stage USED: `prepare` reads it back, because the cap that
    made it (`MAX_ASSETS` or `DEGRADED_MAX_ASSETS`) depends on the soft budget at the time."""
    write_json_atomic(ws.root / "stages" / "asset_aliases.json", {"alias": alias})


def read_dedupe_note(ws: Workspace) -> dict[str, str] | None:
    """The merge map the asset stage used, or None when no stage has run in this workspace."""
    data = read_json_or_none(ws.root / "stages" / "asset_aliases.json")
    return None if data is None else dict(data.get("alias") or {})


def write_variant_shims(ws: Workspace, alias: dict[str, str], available: set[str]) -> list[str]:
    """Replace a merged asset's skeleton stub with a shim onto the surviving factory.

    Zone code is TOLD to call ``build<Kept>(THREE, {variant: n})``, but a zone that
    imports the old name anyway must get the real prop, not the placeholder blockout
    box the skeleton left behind."""
    written: list[str] = []
    for dropped, kept in sorted(alias.items()):
        if kept not in available:
            continue
        rel = f"src/assets/{to_snake(dropped)}.js"
        path = ws.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"// {dropped} is a variant of {kept} (assets were merged into one factory).\n"
            f"import {{ build{to_pascal(kept)} }} from './{to_snake(kept)}.js';\n\n"
            f"export function build{to_pascal(dropped)}(THREE, opts = {{}}) {{\n"
            f"  return build{to_pascal(kept)}(THREE, {{ variant: {variant_index(alias, dropped)}, ...opts }});\n}}\n")
        written.append(rel)
    return written
