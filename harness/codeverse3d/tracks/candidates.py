"""Best-of-N baseline (track side).

``run_best_of_n`` — N baseline candidates are generated IN PARALLEL, each in its own
throw-away sub-workspace (``<ws>/_cand/c<k>``: own git repo, copy of the skeleton + spec +
plan, own AGENTS.md/MCP config), built (+repair), gated, quick-rendered (4 views) and
judged (n_samples=1) — the ordinary round (``steps._run_round``, kind ``candidate``) with
two knobs turned: ``quick_render`` in place of ``pipeline.render`` and no clay views.  Each
candidate's round trace goes to its own ``events.jsonl``; the run log carries
``candidate.*``.  The winner — built, then the highest quick score, then fewer gate errors,
then the earlier candidate — is copied back into the run workspace and the ordinary
round-0 pipeline (build → gates → 14-view render → full judge) runs on it.  That is the
ONE choice the loop still makes (2026-09-22: no pairwise tie-break, no best round).
Every candidate is charged to the run budget and persisted in ``rounds/candidates.json``.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.contracts.artifacts import BuildResult, Measurement, RenderSet
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.conventions import OBJECT_VIEWS_QUICK
from codeverse3d.orchestrator import BudgetExceeded, gate_error_count
from codeverse3d.proc import EventLog, fan_out
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.generation import GenerationTask
from codeverse3d.tracks.steps import (
    RoundFailed,
    RoundPipeline,
    _run_round,
    run_round,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

CAND_DIR = "_cand"
QUICK_PX = 512


# ----------------------------------------------------------------------------- best-of-N
def run_best_of_n(track: Any, ctx: RunContext, tasks: Sequence[GenerationTask], pipeline: RoundPipeline, *,
                  files_hint: Sequence[str] = ()) -> RoundRecord:
    """Baseline round with ``ctx.policy.n_candidates`` parallel candidates; returns the r00 record."""
    n = max(1, ctx.policy.n_candidates)
    ctx.events.emit("candidates.start", round=0, n=n, parallel=min(n, ctx.settings.limits.max_parallel_agents))
    ctx.ws.ensure_gitignore()   # legacy run dirs may predate _cand/ in the standard lines
    subs = [make_candidate_context(track, ctx, k) for k in range(n)]

    def _one(k: int) -> CandidateRecord:
        sub, label = subs[k], f"c{k}"
        ctx.events.emit("candidate.start", candidate=k, workspace=str(sub.ws.root))
        # kind="candidate" is what the cost ledger files the sessions under (Stage.CANDIDATE)
        tasks_k = [t.model_copy(update={"label": f"{t.label}_{label}", "kind": "candidate"}) for t in tasks]
        rec = _run_round(sub, index=0, kind="candidate", tasks=tasks_k, pipeline=pipeline, files_hint=list(files_hint),
                         render=partial(quick_render, pipeline=pipeline), geometry_views=False)
        build_ok = rec.build is not None and rec.build.ok
        cand = CandidateRecord(index=k, label=label, workspace=str(sub.ws.root), commit=rec.commit, build_ok=build_ok,
                               score=rec.score, gate_errors=gate_error_count(rec), usage=rec.usage,
                               duration_s=rec.duration_s, sheet=(rec.renders.contact_sheet if rec.renders else "") or "",
                               notes=rec.notes)
        ctx.events.emit("candidate.done", candidate=k, score=cand.score, build_ok=build_ok, gate_errors=cand.gate_errors,
                        duration_s=cand.duration_s, cost_usd=round(rec.usage.cost_usd, 4), commit=rec.commit[:10])
        return cand

    results = fan_out(list(range(n)), _one, max_workers=ctx.settings.limits.max_parallel_agents, label="candidates",
                      item_name=lambda k: f"c{k}")
    # one retry for candidates that crashed (transient 503s, parse failures) — not for budget stops
    retry = [k for k, r in enumerate(results) if isinstance(r, Exception) and not isinstance(r, BudgetExceeded)]
    if retry and ctx.budget.ok():
        for k in retry:
            ctx.events.emit("candidate.retry", candidate=k, error=f"{type(results[k]).__name__}: {results[k]}"[:300])
            subs[k] = make_candidate_context(track, ctx, k)
        again = fan_out(retry, _one, max_workers=ctx.settings.limits.max_parallel_agents, label="candidates-retry",
                        item_name=lambda k: f"c{k}")
        for k, r in zip(retry, again, strict=True):
            results[k] = r
    records: list[CandidateRecord] = []
    budget_stop: BudgetExceeded | None = None
    for k, r in enumerate(results):
        if isinstance(r, Exception):
            # a candidate that tripped the ceiling is a failed candidate, not the
            # round's verdict: the sibling's finished work is still selected, adopted
            # and persisted below; the guard's boundary check stops the run afterwards.
            if isinstance(r, BudgetExceeded) and budget_stop is None:
                budget_stop = r
            records.append(CandidateRecord(index=k, label=f"c{k}", workspace=str(subs[k].ws.root),
                                           notes=f"{type(r).__name__}: {r}"[:300]))
            ctx.events.emit("candidate.failed", candidate=k, error=f"{type(r).__name__}: {r}"[:300])
            continue
        records.append(r)
    if not any(r.commit for r in records):
        if budget_stop is not None:
            raise budget_stop  # nothing usable survived: the budget stop stands
        raise RoundFailed("; ".join(f"{r.label}: {r.notes}" for r in records) or "every candidate failed")
    best = rank_candidates(records)[0]
    records[best].selected = True
    usage = sum((r.usage for r in records), Usage())
    ctx.ws.write_json(ctx.ws.root / "rounds" / "candidates.json",
                      {"n": n, "selected": best, "candidates": [r.model_dump(mode="json") for r in records]})
    ctx.events.emit("candidate.selected", round=0, candidate=best, label=records[best].label, score=records[best].score,
                    build_ok=records[best].build_ok, scores={r.label: r.score for r in records},
                    cost_usd=round(usage.cost_usd, 4))
    adopt_candidate(ctx, subs[best].ws)
    ctx.ws.commit(f"r00 baseline: candidate {records[best].label} selected (best of {n})")
    others = ", ".join(f"{r.label}={r.score:.3f}" if r.score is not None else f"{r.label}=n/a"
                       for r in records if r.index != best)
    notes = [f"best-of-{n}: selected {records[best].label} "
             f"(quick score {records[best].score if records[best].score is None else round(records[best].score, 3)}; others: {others or '-'})"]
    ctx.budget.check()  # boundary: the winner is adopted + persisted; a real ceiling stop lands here
    return run_round(ctx, index=0, kind="baseline", tasks=[], pipeline=pipeline, files_hint=list(files_hint),
                     extra_usage=usage, extra_notes=notes)


def make_candidate_context(track: Any, ctx: RunContext, k: int) -> RunContext:
    """A self-contained sub-workspace (skeleton + spec + plan + agent files) and its context."""
    sub_ws = Workspace(ctx.ws.root / CAND_DIR / f"c{k}")
    if sub_ws.root.exists():
        shutil.rmtree(sub_ws.root)
    sub_ws.create()
    for rel in ("src", "public"):
        src = ctx.ws.root / rel
        if src.is_dir():
            shutil.rmtree(sub_ws.root / rel, ignore_errors=True)
            shutil.copytree(src, sub_ws.root / rel, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
    for path in (ctx.ws.spec_path, ctx.ws.plan_path):
        if path.is_file():
            shutil.copyfile(path, sub_ws.root / path.name)
    if not ctx.single_shot:
        ctx.services.materialize(sub_ws, agent_kind=ctx.agent_kind, contract_md=track.agent_contract_md(ctx),
                                 cookbook_rel=ctx.cookbook_rel, spatial_tools=True)
    sub_ws.commit("skeleton")
    # own event log (the run log keeps only candidate.*) and a one-sample judge for ranking
    return replace(ctx, ws=sub_ws, extra={}, events=EventLog(sub_ws.events_path),
                   judge=track.make_judge(ctx, n_samples=1))


def quick_render(ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None, *,
                 pipeline: RoundPipeline) -> RenderSet | None:
    """Cheap renders for ranking a candidate — by the route THIS track actually has.

    An object has a GLB and gets the reduced-view, reduced-resolution rig, which is the
    whole point of a "quick" render.  A shader has no GLB at all, and returning None there
    meant the candidate was never judged, scored None, and best-of-N fell through to index
    0 — so every extra candidate was generated, paid for and discarded unlooked-at.

    Measured 2026-08-25 across the teaser battery: static_object and articulated_object
    candidates all carried real scores, and all SIX graphics runs recorded
    ``scores: {c0: null, c1: null}`` with ``selected: 0``, two of them at n=3.  No
    ``candidate.judge_failed`` event fired anywhere — nothing errored, the judge was simply
    never reached.

    So when there is no GLB, fall back to ``pipeline.render``, which is the renderer the
    track uses for its real rounds (frames for graphics, authored cameras for a scene).
    That costs more than the quick rig; it costs less than paying for N generations and
    keeping the first one blind.
    """
    out_dir = ctx.ws.renders_dir(round_index) / "quick"
    try:
        if build.glb_path:
            rs = ctx.services.render_object(Path(build.glb_path), out_dir, views=OBJECT_VIEWS_QUICK,
                                            width=QUICK_PX, height=QUICK_PX)
            # ArticulatedPipeline.gates() parks its sweep views here for render() to pick up
            pose = ctx.extra.pop("pose_views", None)
            if pose:
                rs.views = list(rs.views) + list(pose)
            return rs
        return pipeline.render(ctx, round_index, build, measurement)
    except Exception as e:  # noqa: BLE001 — a candidate without renders is ranked by gates only
        log.warning("quick render failed for %s: %s", ctx.ws.root, e)
        ctx.events.emit("candidate.render_failed", workspace=str(ctx.ws.root), error=f"{type(e).__name__}: {e}")
        return None


def adopt_candidate(ctx: RunContext, sub_ws: Workspace) -> None:
    """Copy the winner's src/ + public/ into the run workspace and keep its trajectories."""
    for rel in ("src", "public"):
        src = sub_ws.root / rel
        dest = ctx.ws.root / rel
        if dest.exists():
            shutil.rmtree(dest)
        if src.is_dir():
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
        else:
            dest.mkdir(parents=True, exist_ok=True)
    if sub_ws.trajectories.is_dir():
        shutil.copytree(sub_ws.trajectories, ctx.ws.trajectories, dirs_exist_ok=True)


# ===================================================================== decision logic
class CandidateRecord(BaseModel):
    """One best-of-N baseline candidate (generated in its own sub-workspace)."""

    index: int
    label: str
    commit: str = Field(default="", description="commit in the candidate's sub-workspace")
    workspace: str = ""
    build_ok: bool = False
    score: float | None = None
    gate_errors: int = 0
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0
    sheet: str = Field(default="", description="quick contact sheet (4 views) used for the quick judge")
    notes: str = ""
    selected: bool = False

    def sort_key(self) -> tuple[int, float, int, int]:
        return (1 if self.build_ok else 0, self.score if self.score is not None else -1.0, -self.gate_errors, -self.index)


def rank_candidates(records: Sequence[CandidateRecord]) -> list[int]:
    """Candidate indices best-first: built > higher quick score > fewer gate errors > earlier."""
    return [r.index for r in sorted(records, key=lambda r: r.sort_key(), reverse=True)]


__all__ = ["CAND_DIR", "CandidateRecord", "adopt_candidate", "make_candidate_context", "quick_render",
           "rank_candidates", "run_best_of_n"]
