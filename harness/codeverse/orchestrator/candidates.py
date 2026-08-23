"""Choosing between alternatives: best-of-N candidates and pairwise tie-breaks.

Pure decision logic (no I/O, no models) shared by the tracks:

* ``CandidateRecord`` — what one baseline candidate produced (commit, quick
  score, gate errors, cost); ``rank_candidates`` orders them.
* ``decide_best`` — when a new round's score is within ``margin`` of the
  current best the absolute delta is judge noise (flash judges compress their
  score range), so a position-swapped pairwise comparison decides; the new
  round replaces the best only when it wins with enough confidence.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from codeverse.contracts.common import Usage

log = logging.getLogger(__name__)


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


class PairwiseNote(BaseModel):
    """What a tie-break compared and what it concluded (persisted in round notes)."""

    a: str = Field(description="label of the incumbent (current best)")
    b: str = Field(description="label of the challenger (new round / other candidate)")
    winner: Literal["a", "b", "tie"] = "tie"
    confidence: float = 0.0
    accepted: bool = Field(default=False, description="True when the challenger replaces the incumbent")
    reasons: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    error: str = ""

    def line(self) -> str:
        verdict = {"a": f"{self.a} wins", "b": f"{self.b} wins", "tie": "tie"}[self.winner]
        return f"pairwise {self.a} vs {self.b}: {verdict} (confidence {self.confidence:.2f}) → {'replace' if self.accepted else 'keep'}"


Decision = Literal["score", "pairwise", "keep"]

#: ``compare()`` → any object with ``winner`` ('a'|'b'|'tie'), ``confidence``, ``reasons``, ``usage``.
CompareFn = Callable[[], Any]


def within_margin(a: float | None, b: float | None, margin: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= margin


def decide_best(
    incumbent_score: float | None,
    challenger_score: float | None,
    *,
    margin: float,
    min_confidence: float,
    compare: CompareFn | None,
    labels: tuple[str, str] = ("best", "new"),
) -> tuple[Decision, PairwiseNote | None]:
    """Should the challenger replace the incumbent?

    * scores differ by more than ``margin`` → ``"score"`` (caller uses the
      ordinary ranking rule);
    * within the margin and ``compare`` given → run it; ``"pairwise"`` when the
      challenger wins with ``confidence ≥ min_confidence``, else ``"keep"``;
    * within the margin without a comparator → ``"keep"`` (ties go to the
      incumbent: fewer regenerations, stable best commit).
    """
    if incumbent_score is None or challenger_score is None:
        return "score", None
    if not within_margin(incumbent_score, challenger_score, margin):
        return "score", None
    note = PairwiseNote(a=labels[0], b=labels[1])
    if compare is None:
        return "keep", note
    try:
        res = compare()
    except Exception as e:  # noqa: BLE001 — a judge outage must not pick a worse round
        log.warning("pairwise tie-break failed: %s", e)
        note.error = f"{type(e).__name__}: {e}"
        return "keep", note
    note.winner = getattr(res, "winner", "tie")
    note.confidence = float(getattr(res, "confidence", 0.0) or 0.0)
    note.reasons = list(getattr(res, "reasons", []) or [])[:6]
    note.usage = getattr(res, "usage", None) or Usage()
    note.error = getattr(res, "error", "") or ""
    note.accepted = note.winner == "b" and note.confidence >= min_confidence
    return ("pairwise" if note.accepted else "keep"), note


__all__ = ["CandidateRecord", "CompareFn", "Decision", "PairwiseNote", "decide_best", "rank_candidates", "within_margin"]
