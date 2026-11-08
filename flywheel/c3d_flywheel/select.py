"""Which round of a finished run to hand over — and the hand-over itself (``c3d-flywheel pick``)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from codeverse3d.contracts.spec import FIXED_PARTS_GATE, PROJECT_GATE_PREFIX
from codeverse3d.proc import read_json_or_none
from pydantic import BaseModel, Field

from c3d_flywheel.legacy import RoundRecord, RunRecord
from c3d_flywheel.record.deliverable import build_deliverable, round_outputs
from c3d_flywheel.record.record import (
    effective_judgment,
    load_record,
    round_complexity,
    stop_reason,
)
from c3d_flywheel.runlayout import RunWorkspace

log = logging.getLogger(__name__)

SELECTION_NAME = "selection.json"
Method = Literal["score", "round"]


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


# --------------------------------------------------------------------------- rows
def _load(run_dir: Path | str, record: RunRecord | None) -> RunRecord:
    return record if record is not None else load_record(RunWorkspace(run_dir))


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
    return name.startswith(PROJECT_GATE_PREFIX) or name == FIXED_PARTS_GATE


def _ranked(rows: list[RoundRow]) -> list[RoundRow]:
    """Judged rounds, best first: no project-check errors, then score, fewer gate errors, earlier round."""
    return sorted((r for r in rows if r.score is not None),
                  key=lambda r: (r.project_errors > 0, -(r.score or 0.0), r.gate_errors, r.index))


# --------------------------------------------------------------------------- a round's own outputs
def round_file(ws: RunWorkspace, rnd: RoundRecord | None, name: str = "object.glb") -> Path | None:
    out = round_outputs(ws, rnd)
    p = out / name if out is not None else None
    return p if p is not None and p.is_file() else None


def round_complexity_block(rnd: RoundRecord | None) -> dict | None:
    block = round_complexity(rnd) if rnd is not None else None
    return block if isinstance(block, dict) else None


# --------------------------------------------------------------------------- pick
def pick(run_dir: Path | str, *, record: RunRecord | None = None) -> int | None:
    ranked = _ranked(round_rows(run_dir, record=_load(run_dir, record)))
    return ranked[0].index if ranked else None


def fallback_round(rounds: Iterable[RoundRecord]) -> int | None:
    return max((r.index for r in rounds
                if r.build is not None and r.build.ok and r.renders is not None and r.renders.views), default=None)


# --------------------------------------------------------------------------- summarise
def load_selection(ws: RunWorkspace) -> Selection | None:
    data = read_json_or_none(ws.root / SELECTION_NAME)
    try:
        return Selection.model_validate(data) if data is not None else None
    except ValueError:
        return None


def summarise(run_dir: Path | str, *, record: RunRecord | None = None) -> RunSummary:
    rec = _load(run_dir, record)
    rows = round_rows(run_dir, record=rec)
    by_index = {r.index: r for r in rows}
    sel = load_selection(RunWorkspace(run_dir))
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
    ws = RunWorkspace(run_dir)
    rec = load_record(ws)
    if not any(r.index == round_index for r in rec.rounds):
        raise ValueError(f"no round {round_index} in {ws.root} (rounds: {[r.index for r in rec.rounds]})")
    build_deliverable(ws, rec, round_index)
    sel = Selection(round=round_index, method=method, scores={r.index: r.score for r in round_rows(ws.root, record=rec)})
    ws.write_json(ws.root / SELECTION_NAME, sel)
    return ws.deliverable
