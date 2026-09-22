"""Which round of a finished run to hand over — and the hand-over itself (``3dcode pick``).

Since 2026-09-22 the run loop keeps every round, ends at the last one and calls no run
passed or failed: the judge scores each round and its verdict shapes the next one, and
choosing among the rounds is a READER's job.  This addon is that reader, and the one every
other reader asks (gallery, dataset, cost report, calibration, the CLI, eval/bench):

* :func:`round_rows`  one typed row per saved round — the effective judged score, the
                      judge's own verdict for that round, gate errors, commit, cost, minutes;
* :func:`pick`        the round to hand over: the highest judged score, ties to fewer gate
                      errors, then the earlier round.  ``by="pairwise"`` lets the position-
                      swapped pairwise judge decide between the top two when they sit within
                      the judge's noise (the verdict is paid once and cached in
                      ``artifacts/judge/``);
* :func:`summarise`   baseline score, the picked round and its score, the delta, rounds run
                      and the stop reason — the round ``selection.json`` names when the run
                      was packaged, else :func:`pick`'s;
* :func:`package`     ``deliverable/`` for one round, the texture pass on it when asked, and
                      ``selection.json`` (round, method, scores) beside ``record.json``.

Nothing here runs during a run: ``3dcode make`` calls :func:`pick` + :func:`package` after the
track returns (unless ``--no-pick``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from codeverse3d.contracts.common import TRACK_INFO, Usage
from codeverse3d.contracts.run import RunRecord
from codeverse3d.cost.context import call_context
from codeverse3d.cost.types import Role, Stage
from codeverse3d.judges.base import judged_subset, resolve_paths
from codeverse3d.proc import EventLog, read_json_or_none
from codeverse3d.record.deliverable import build_deliverable, round_outputs, texture_report_for
from codeverse3d.record.record import effective_judgment, load_record, package_run
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

SELECTION_NAME = "selection.json"
#: |Δscore| at or below this is judge noise: ``by="pairwise"`` asks the pairwise judge
PAIRWISE_MARGIN = 0.03
#: the runner-up replaces the top-scored round only when the pairwise judge is this sure
PAIRWISE_MIN_CONFIDENCE = 0.6

PickBy = Literal["score", "pairwise"]
#: how ``selection.json``'s round was chosen: by score, by the pairwise judge, or named (``--round``)
Method = Literal["score", "pairwise", "round"]


class RoundRow(BaseModel):
    """One saved round, as a reader needs it."""

    index: int
    kind: str = ""
    score: float | None = Field(default=None, description="effective judged score; None = unjudged or a degraded verdict")
    passed: bool | None = Field(default=None, description="the judge's own verdict for THIS round (None = unjudged)")
    gate_errors: int = 0
    build_ok: bool | None = None
    commit: str = ""
    cost_usd: float = 0.0
    minutes: float = Field(default=0.0, description="the round's minutes (RoundRecord.minutes): its steps, "
                                                    "minus the time provider errors cost them")


class RunSummary(BaseModel):
    """A run in one line — no pass/fail: the scores say how it went."""

    rounds: int = Field(default=0, description="rounds run (the baseline included)")
    stop_reason: str = ""
    baseline_score: float | None = Field(default=None, description="round 0's effective score")
    picked_round: int | None = None
    picked_score: float | None = None
    delta: float | None = Field(default=None, description="picked − baseline, when both were judged")
    method: Method = "score"


class Selection(BaseModel):
    """``selection.json``: which round ``deliverable/`` holds, and why."""

    round: int
    method: Method
    scores: dict[int, float | None] = Field(default_factory=dict, description="every round's effective score")
    textured: bool = Field(default=False, description="the deliverable carries a shipped texture pack of this round")
    selected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PairwiseNote(BaseModel):
    """One paid pairwise verdict — does round ``b`` beat round ``a``? — as cached in
    ``artifacts/judge/rAA_vs_rBB_pairwise.json`` so a second ``3dcode pick`` re-reads it."""

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
    """One row per round of the run's ``record.json`` (``record=`` skips re-reading it)."""
    rows = []
    for r in _load(run_dir, record).rounds:
        j = effective_judgment(r)
        rows.append(RoundRow(index=r.index, kind=r.kind, score=j.overall if j else None, passed=j.passed if j else None,
                             gate_errors=sum(len(g.errors) for g in r.gates),
                             build_ok=None if r.build is None else r.build.ok, commit=r.commit,
                             cost_usd=round(r.usage.cost_usd, 6), minutes=round(r.minutes, 2)))
    return rows


def _ranked(rows: list[RoundRow]) -> list[RoundRow]:
    """Judged rounds, best first: highest score, then fewer gate errors, then the earlier round."""
    return sorted((r for r in rows if r.score is not None), key=lambda r: (-(r.score or 0.0), r.gate_errors, r.index))


# --------------------------------------------------------------------------- pick
def pick(run_dir: Path | str, *, by: PickBy = "score", pairwise_model: str | None = None,
         record: RunRecord | None = None, judge: Any | None = None) -> int | None:
    """The round to hand over, or None when no round was judged.

    ``by="pairwise"``: when the top two scores are within :data:`PAIRWISE_MARGIN`, the
    pairwise judge (``pairwise_model``, default the run's judge; ``judge`` injects one)
    compares their renders and the runner-up wins only with confidence ≥
    :data:`PAIRWISE_MIN_CONFIDENCE`.  A verdict already paid for this pair is re-read,
    never re-bought."""
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
    """Does round ``b`` beat round ``a``?  Position-swapped pairwise judge, one paid verdict
    per (pair, model), cached at ``artifacts/judge/rAA_vs_rBB_pairwise.json``.  A judge
    outage keeps ``a`` (never a worse pick for a failed call)."""
    cache = ws.judge_path(a, f"_vs_r{b:02d}_pairwise")
    stored = read_json_or_none(cache) or {}
    if stored.get("model") == model and isinstance(stored.get("note"), dict):
        return PairwiseNote.model_validate(stored["note"])
    rounds = {r.index: r for r in rec.rounds}
    renders = [judged_subset(resolve_paths(ws, rounds[i].renders)) for i in (a, b)]
    note = PairwiseNote(a=f"r{a:02d}", b=f"r{b:02d}")
    if renders[0] is None or renders[1] is None:
        note.error = "a round has no renders to compare"
        return note
    try:
        if judge is None:
            from codeverse3d.judges.pairwise import PairwiseJudge

            judge = PairwiseJudge(model)
        with call_context(stage=Stage.PAIRWISE, role=Role.JUDGE, label="pick"):
            res = judge.compare(rec.spec, renders[0], renders[1],
                                rubric=str(rec.extra.get("rubric") or TRACK_INFO[rec.spec.track].rubric))
    except Exception as e:  # noqa: BLE001 — an outage must not move the pick
        log.warning("pairwise pick failed: %s", e)
        note.error = f"{type(e).__name__}: {e}"
        return note
    note.winner, note.confidence = res.winner, float(res.confidence or 0.0)
    note.reasons, note.usage, note.error = list(res.reasons or [])[:6], res.usage or Usage(), res.error or ""
    note.accepted = note.winner == "b" and note.confidence >= PAIRWISE_MIN_CONFIDENCE
    ws.write_json(cache, {"model": model, "note": note.model_dump(mode="json")})
    EventLog(ws.events_path).emit("pick.pairwise", a=a, b=b, winner=note.winner, confidence=note.confidence,
                                  accepted=note.accepted, cost_usd=round(note.usage.cost_usd, 4))
    return note


# --------------------------------------------------------------------------- summarise
def load_selection(ws: Workspace) -> Selection | None:
    data = read_json_or_none(ws.root / SELECTION_NAME)
    try:
        return Selection.model_validate(data) if data is not None else None
    except ValueError:  # a hand-edited file must not break every reader
        return None


def summarise(run_dir: Path | str, *, record: RunRecord | None = None) -> RunSummary:
    """The run in one line.  The picked round is the one ``selection.json`` names (what was
    packaged), else what :func:`pick` chooses by score."""
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
    return RunSummary(rounds=len(rows), stop_reason=str(rec.extra.get("stop_reason") or rec.status.value),
                      baseline_score=baseline, picked_round=picked, picked_score=score, method=method,
                      delta=round(score - baseline, 6) if score is not None and baseline is not None else None)


# --------------------------------------------------------------------------- package
def package(run_dir: Path | str, round_index: int, *, texture: bool = False, method: Method = "round",
            image_model: Any | None = None) -> Path:
    """Write ``deliverable/`` for round ``round_index`` (from its commit and ``artifacts/rNN/``,
    no rebuild) and ``selection.json``; returns the deliverable directory.

    ``texture=True`` runs the texture pass on that round's own GLB first, unless a pass
    already started from those exact bytes (a paid pass is never bought twice); a pack
    ships into the deliverable only when its judge gate shipped it, and the record is
    re-packaged so its total takes the pass in (the run's money is its ledger's,
    ``record.package_run``).  Otherwise the record is not rewritten: a record from before
    2026-09-22 keeps the ``best_round`` its canonical build is found by."""
    ws = Workspace(run_dir)
    rec = load_record(ws)
    rnd = next((r for r in rec.rounds if r.index == round_index), None)
    if rnd is None:
        raise ValueError(f"no round {round_index} in {ws.root} (rounds: {[r.index for r in rec.rounds]})")
    if texture:
        _texture(ws, rec, round_index, image_model=image_model)
        rec = load_record(ws)  # the pass wrote extra["texturing"]
        package_run(ws, rec)
        ws.write_json(ws.record_path, rec)
    manifest = build_deliverable(ws, rec, round_index)
    sel = Selection(round=round_index, method=method, scores={r.index: r.score for r in round_rows(ws.root, record=rec)},
                    textured=any(f.path == "deliverable/object_textured.glb" for f in manifest.files))
    ws.write_json(ws.root / SELECTION_NAME, sel)
    EventLog(ws.events_path).emit("pick.packaged", round=round_index, method=method, textured=sel.textured,
                                  files=len(manifest.files))
    return ws.deliverable


def _texture(ws: Workspace, rec: RunRecord, index: int, *, image_model: Any | None) -> None:
    """The texture pass on round ``index``'s GLB (it records itself on ``record.json``)."""
    from codeverse3d.texturing.run import texture_pass, texture_supported

    if not texture_supported(rec.spec.track):
        raise ValueError(f"the texture pass is for object tracks; {rec.spec.track.value} runs have no GLB to texture")
    rnd = next(r for r in rec.rounds if r.index == index)
    out = round_outputs(ws, rnd)
    glb = out / "object.glb" if out is not None else None
    if glb is None or not glb.is_file():
        raise ValueError(f"round {index} kept no object.glb to texture (artifacts/r{index:02d}/)")
    if texture_report_for(ws, glb) is not None:
        EventLog(ws.events_path).emit("texture.skipped", reason="already_textured_this_artifact", round=index)
        return
    sheet = ws.rebase(rnd.renders.contact_sheet) if rnd.renders is not None and rnd.renders.contact_sheet else None
    events = EventLog(ws.events_path)
    try:
        texture_pass(ws, rec.spec, rec.plan, model_id=rec.spec.backends.planner, image_model=image_model, judge=True,
                     judge_model_id=rec.spec.backends.judge, glb_in=glb, sheet=sheet, events=events)
    except Exception as e:  # noqa: BLE001 — a derived asset pack: the hand-over goes out without it
        log.warning("texture pass failed on round %d: %s", index, e)
        events.emit("texture.failed", round=index, error=f"{type(e).__name__}: {e}")
