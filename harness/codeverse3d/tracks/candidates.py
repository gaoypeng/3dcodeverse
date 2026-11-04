"""Best-of-N baseline (track side)."""

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
from codeverse3d.cost.tally import tally, timed
from codeverse3d.orchestrator import BudgetExceeded, gate_error_count
from codeverse3d.proc import EventLog, fan_out
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.generation import GenerationTask, is_model_outage
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
    subs = [make_candidate_context(track, ctx, k) for k in range(n)]

    def _one(k: int) -> CandidateRecord:
        sub, label = subs[k], f"c{k}"
        ctx.events.emit("candidate.start", candidate=k, workspace=str(sub.ws.root))
        tasks_k = [t.model_copy(update={"label": f"{t.label}_{label}", "kind": "candidate"}) for t in tasks]
        with tally() as spent:
            rec = _run_round(sub, index=0, kind="candidate", tasks=tasks_k, pipeline=pipeline,
                             files_hint=list(files_hint), render=partial(quick_render, pipeline=pipeline),
                             geometry_views=False, spent=spent)
        build_ok = rec.build is not None and rec.build.ok
        cand = CandidateRecord(index=k, label=label, workspace=str(sub.ws.root), commit=rec.commit, build_ok=build_ok,
                               score=rec.score, gate_errors=gate_error_count(rec), usage=rec.usage,
                               duration_s=rec.duration_s, sheet=(rec.renders.contact_sheet if rec.renders else "") or "",
                               notes=rec.notes)
        ctx.events.emit("candidate.done", candidate=k, score=cand.score, build_ok=build_ok, gate_errors=cand.gate_errors,
                        duration_s=cand.duration_s, cost_usd=round(rec.usage.cost_usd, 4), commit=rec.commit[:10])
        return cand

    with timed("candidates", ctx.state.steps, round_index=0):
        results = fan_out(list(range(n)), _one, max_workers=ctx.settings.limits.max_parallel_agents,
                          label="candidates", item_name=lambda k: f"c{k}")
    # one retry for candidates that crashed (transient 503s, parse failures) — not for budget stops
    retry = [k for k, r in enumerate(results) if isinstance(r, Exception) and not isinstance(r, BudgetExceeded)]
    if retry and ctx.budget.ok():
        for k in retry:
            ctx.events.emit("candidate.retry", candidate=k, error=f"{type(results[k]).__name__}: {results[k]}"[:300])
            subs[k] = make_candidate_context(track, ctx, k)
        with timed("candidates", ctx.state.steps, round_index=0):
            again = fan_out(retry, _one, max_workers=ctx.settings.limits.max_parallel_agents, label="candidates-retry",
                            item_name=lambda k: f"c{k}")
        for k, r in zip(retry, again, strict=True):
            results[k] = r
    records: list[CandidateRecord] = []
    budget_stop: BudgetExceeded | None = None
    for k, r in enumerate(results):
        if isinstance(r, Exception):
            if isinstance(r, BudgetExceeded) and budget_stop is None:
                budget_stop = r
            records.append(CandidateRecord(index=k, label=f"c{k}", workspace=str(subs[k].ws.root),
                                           notes=f"{type(r).__name__}: {r}"[:300]))
            ctx.events.emit("candidate.failed", candidate=k, error=f"{type(r).__name__}: {r}"[:300])
            continue
        records.append(r)
    if not any(r.commit for r in records):
        if budget_stop is not None:
            raise budget_stop
        died = [r for r in results if isinstance(r, Exception)]
        raise RoundFailed("; ".join(f"{r.label}: {r.notes}" for r in records) or "every candidate failed",
                          transient=any(e.transient if isinstance(e, RoundFailed) else is_model_outage(e) for e in died),
                          quota=any(isinstance(e, RoundFailed) and e.quota for e in died),
                          stalled=any(isinstance(e, RoundFailed) and e.stalled for e in died))
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
    ctx.budget.check()
    return run_round(ctx, index=0, kind="baseline", tasks=[], pipeline=pipeline, files_hint=list(files_hint),
                     extra_usage=usage, extra_notes=notes)


def make_candidate_context(track: Any, ctx: RunContext, k: int) -> RunContext:
    """A self-contained sub-workspace (skeleton + spec + plan + agent files) and its context."""
    sub_ws = Workspace(ctx.ws.root / CAND_DIR / f"c{k}")
    if sub_ws.root.exists():
        shutil.rmtree(sub_ws.root)
    sub_ws.create()
    _copy_code(ctx.ws, sub_ws)
    for path in (ctx.ws.spec_path, ctx.ws.plan_path):
        if path.is_file():
            shutil.copyfile(path, sub_ws.root / path.name)
    if not ctx.single_shot:
        ctx.services.materialize(sub_ws, agent_kind=ctx.agent_kind, contract_md=track.agent_contract_md(ctx))
    sub_ws.commit("skeleton")
    # own event log (the run log keeps only candidate.*) and a one-sample judge for ranking
    return replace(ctx, ws=sub_ws, extra={}, events=EventLog(sub_ws.events_path),
                   judge=track.make_judge(ctx, n_samples=1))


def quick_render(ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None, *,
                 pipeline: RoundPipeline) -> RenderSet | None:
    """Cheap renders for ranking a candidate — by the route THIS track actually has."""
    out_dir = ctx.ws.renders_dir(round_index) / "quick"
    try:
        if build.glb_path:
            rs = ctx.services.render_object(Path(build.glb_path), out_dir, views=OBJECT_VIEWS_QUICK,
                                            width=QUICK_PX, height=QUICK_PX)
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
    _copy_code(sub_ws, ctx.ws)
    if sub_ws.trajectories.is_dir():
        shutil.copytree(sub_ws.trajectories, ctx.ws.trajectories, dirs_exist_ok=True)


def _copy_code(src_ws: Workspace, dest_ws: Workspace) -> None:
    """``src/`` + ``public/`` of one workspace replace the other's (an absent one leaves it empty)."""
    for rel in ("src", "public"):
        dest = dest_ws.root / rel
        shutil.rmtree(dest, ignore_errors=True)
        if (src_ws.root / rel).is_dir():
            shutil.copytree(src_ws.root / rel, dest, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
        else:
            dest.mkdir(parents=True, exist_ok=True)


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
