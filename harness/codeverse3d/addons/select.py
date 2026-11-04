"""Which round of a finished run to hand over — and the hand-over itself (``3dcodeverse pick``)."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.run import RoundRecord, RunRecord
from codeverse3d.cost.context import call_context
from codeverse3d.cost.types import Role, Stage
from codeverse3d.judges.base import judged_subset, resolve_paths
from codeverse3d.proc import EventLog, read_json_or_none, sha256_file
from codeverse3d.record.deliverable import build_deliverable, round_outputs
from codeverse3d.record.record import (
    effective_judgment,
    load_record,
    repackage,
    round_complexity,
    rubric_of,
    stop_reason,
)
from codeverse3d.spatial.contract import FIXED_GATE
from codeverse3d.tracks.project_checks import PREFIX as PROJECT_PREFIX
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

SELECTION_NAME = "selection.json"
PAIRWISE_MARGIN = 0.03
PAIRWISE_MIN_CONFIDENCE = 0.6

PickBy = Literal["score", "pairwise"]
Method = Literal["score", "pairwise", "round"]


class RoundRow(BaseModel):

    index: int
    kind: str = ""
    score: float | None = Field(default=None, description="effective judged score; None = unjudged or a degraded verdict")
    passed: bool | None = Field(default=None, description="the judge's own verdict for THIS round (None = unjudged)")
    gate_errors: int = 0
    project_errors: int = Field(default=0, description="ERRORs of the project's own measurements: its checks "
                                "(project:*, Spec.checks) and fixed parts (contract_fixed, Spec.fixed_parts)")
    build_ok: bool | None = None
    commit: str = ""
    cost_usd: float = 0.0
    minutes: float = Field(default=0.0, description="the round's minutes (RoundRecord.minutes): its steps, "
                                                    "minus the time provider errors cost them")


class RunSummary(BaseModel):

    rounds: int = Field(default=0, description="rounds run (the baseline included)")
    stop_reason: str = ""
    baseline_score: float | None = Field(default=None, description="round 0's effective score")
    picked_round: int | None = None
    picked_score: float | None = None
    round: int | None = Field(default=None, description="the round a reader shows: picked_round, else "
                                                        "fallback_round (None = no round built with renders)")
    delta: float | None = Field(default=None, description="picked − baseline, when both were judged")
    method: Method = "score"


class Selection(BaseModel):

    round: int
    method: Method
    scores: dict[int, float | None] = Field(default_factory=dict, description="every round's effective score")
    selected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PairwiseNote(BaseModel):

    a: str = Field(description="label of the top-scored round")
    b: str = Field(description="label of the runner-up")
    winner: Literal["a", "b", "tie"] = "tie"
    confidence: float = 0.0
    accepted: bool = Field(default=False, description="True when the runner-up is picked instead")
    reasons: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    error: str = ""


# --------------------------------------------------------------------------- rows
def _load(run_dir: Path | str, record: RunRecord | None) -> RunRecord:
    return record if record is not None else load_record(Workspace(run_dir))


def round_rows(run_dir: Path | str, *, record: RunRecord | None = None) -> list[RoundRow]:
    rows = []
    for r in _load(run_dir, record).rounds:
        j = effective_judgment(r)
        rows.append(RoundRow(index=r.index, kind=r.kind, score=j.overall if j else None, passed=j.passed if j else None,
                             gate_errors=sum(len(g.errors) for g in r.gates),
                             project_errors=sum(len(g.errors) for g in r.gates if is_project_gate(g.gate)),
                             build_ok=None if r.build is None else r.build.ok, commit=r.commit,
                             cost_usd=round(r.usage.cost_usd, 6), minutes=round(r.minutes, 2)))
    return rows


def is_project_gate(name: str) -> bool:
    """A gate that measures the project's own truth: its registered checks (``project:*``) or its fixed parts."""
    return name.startswith(PROJECT_PREFIX) or name == FIXED_GATE


def _ranked(rows: list[RoundRow]) -> list[RoundRow]:
    """Judged rounds, best first: no project-check errors, then score, fewer gate errors, earlier round."""
    return sorted((r for r in rows if r.score is not None),
                  key=lambda r: (r.project_errors > 0, -(r.score or 0.0), r.gate_errors, r.index))


# --------------------------------------------------------------------------- a round's own outputs
def round_file(ws: Workspace, rnd: RoundRecord | None, name: str = "object.glb") -> Path | None:
    out = round_outputs(ws, rnd)
    p = out / name if out is not None else None
    return p if p is not None and p.is_file() else None


def round_complexity_block(ws: Workspace, rnd: RoundRecord | None) -> dict | None:
    block = round_complexity(rnd) if rnd is not None else None
    return block if isinstance(block, dict) else None


# --------------------------------------------------------------------------- pick
def pick(run_dir: Path | str, *, by: PickBy = "score", pairwise_model: str | None = None,
         record: RunRecord | None = None, judge: Any | None = None) -> int | None:
    rec = _load(run_dir, record)
    ranked = _ranked(round_rows(run_dir, record=rec))
    if not ranked:
        return None
    top = ranked[0]
    if by != "pairwise" or len(ranked) < 2 or (top.score or 0.0) - (ranked[1].score or 0.0) > PAIRWISE_MARGIN:
        return top.index
    note = pairwise_verdict(Workspace(run_dir), rec, top.index, ranked[1].index,
                            model=pairwise_model or rec.spec.backends.judge, judge=judge)
    return ranked[1].index if note.accepted else top.index


def pairwise_verdict(ws: Workspace, rec: RunRecord, a: int, b: int, *, model: str, judge: Any | None = None) -> PairwiseNote:
    cache = ws.judge_path(a, f"_vs_r{b:02d}_pairwise")
    rounds = {r.index: r for r in rec.rounds}
    renders = [judged_subset(resolve_paths(ws, rounds[i].renders)) for i in (a, b)]
    note = PairwiseNote(a=f"r{a:02d}", b=f"r{b:02d}")
    if renders[0] is None or renders[1] is None:
        note.error = "a round has no renders to compare"
        return note
    seen = hashlib.sha256("\n".join(sha256_file(v.path) if Path(v.path).is_file() else v.path
                                  for rs in renders for v in rs.views).encode()).hexdigest()
    stored = read_json_or_none(cache) or {}
    try:
        if judge is None:
            from codeverse3d.judges.pairwise import PairwiseJudge

            judge = PairwiseJudge(model)
        if (stored.get("model") == model and stored.get("renders") == seen
                and stored.get("thinking") == judge.thinking and isinstance(stored.get("note"), dict)):
            return PairwiseNote.model_validate(stored["note"])
        with call_context(stage=Stage.PAIRWISE, role=Role.JUDGE, label="pick"):
            res = judge.compare(rec.spec, renders[0], renders[1],
                                rubric=rubric_of(rec))
    except Exception as e:  # noqa: BLE001
        log.warning("pairwise pick failed: %s", e)
        note.error = f"{type(e).__name__}: {e}"
        return note
    note.winner, note.confidence = res.winner, float(res.confidence or 0.0)
    note.reasons, note.usage, note.error = list(res.reasons or [])[:6], res.usage or Usage(), res.error or ""
    note.accepted = note.winner == "b" and note.confidence >= PAIRWISE_MIN_CONFIDENCE
    ws.write_json(cache, {"model": model, "renders": seen, "thinking": judge.thinking,
                         "note": note.model_dump(mode="json")})
    EventLog(ws.events_path).emit("pick.pairwise", a=a, b=b, winner=note.winner, confidence=note.confidence,
                                  accepted=note.accepted, cost_usd=round(note.usage.cost_usd, 4))
    return note


def fallback_round(rounds: Iterable[RoundRecord]) -> int | None:
    return max((r.index for r in rounds
                if r.build is not None and r.build.ok and r.renders is not None and r.renders.views), default=None)


# --------------------------------------------------------------------------- summarise
def load_selection(ws: Workspace) -> Selection | None:
    data = read_json_or_none(ws.root / SELECTION_NAME)
    try:
        return Selection.model_validate(data) if data is not None else None
    except ValueError:
        return None


def summarise(run_dir: Path | str, *, record: RunRecord | None = None) -> RunSummary:
    rec = _load(run_dir, record)
    rows = round_rows(run_dir, record=rec)
    by_index = {r.index: r for r in rows}
    sel = load_selection(Workspace(run_dir))
    if sel is not None and sel.round in by_index:
        picked, method = sel.round, sel.method
    else:
        picked, method = pick(run_dir, record=rec), "score"
    baseline = by_index[0].score if 0 in by_index else None
    score = by_index[picked].score if picked is not None else None
    return RunSummary(rounds=len(rows), stop_reason=stop_reason(rec),
                      baseline_score=baseline, picked_round=picked, picked_score=score, method=method,
                      round=picked if picked is not None else fallback_round(rec.rounds),
                      delta=round(score - baseline, 6) if score is not None and baseline is not None else None)


# --------------------------------------------------------------------------- package
def package(run_dir: Path | str, round_index: int, *, method: Method = "round") -> Path:
    ws = Workspace(run_dir)
    rec = load_record(ws)
    if not any(r.index == round_index for r in rec.rounds):
        raise ValueError(f"no round {round_index} in {ws.root} (rounds: {[r.index for r in rec.rounds]})")
    rec = repackage(ws) or load_record(ws)
    manifest = build_deliverable(ws, rec, round_index)
    sel = Selection(round=round_index, method=method, scores={r.index: r.score for r in round_rows(ws.root, record=rec)})
    ws.write_json(ws.root / SELECTION_NAME, sel)
    EventLog(ws.events_path).emit("pick.packaged", round=round_index, method=method,
                                  files=len(manifest.files))
    return ws.deliverable
