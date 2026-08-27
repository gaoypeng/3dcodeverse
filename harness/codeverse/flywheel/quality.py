"""Dataset hygiene: quality tiers + duplicate detection by (code hash, prompt).

* ``quality_tier(passed, gate_errors, score)`` → ``A | B | C | D``::

      A  judged passed AND the best round has zero gate errors
      B  judged passed (some gate errors remain)
      C  not passed but best score ≥ 0.6
      D  everything else (unjudged, failed, low score)

* ``find_duplicates(rows)`` groups index rows with identical
  ``(code_sha256, prompt_hash)`` — RAW bytes.  The canonical row of a group is the
  best (tier, score, fewest gate errors, captioned, then key order) and every other row gets
  ``duplicate_of = canonical id``.  This is the only set anything may DROP.
* ``find_near_duplicates(rows)`` groups on the NORMALISED ``code_fingerprint``
  instead and only stamps ``near_duplicate_of``: ``x = "a b"`` and ``x="ab"``
  normalise the same but are different programs, so they are marked, never removed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field

QualityTier = Literal["A", "B", "C", "D"]
TIER_ORDER: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}
TIER_C_MIN_SCORE = 0.6


def prompt_hash(prompt: str) -> str:
    """Stable 16-hex id of a (stripped) user prompt — shared by samples, pairs and the index."""
    return hashlib.sha256(prompt.strip().encode()).hexdigest()[:16]


def quality_tier(*, passed: bool | None, gate_errors: int, score: float | None) -> QualityTier:
    """Tier rule (see module docstring).  ``passed`` None = never judged."""
    if passed:
        return "A" if gate_errors == 0 else "B"
    if score is not None and score >= TIER_C_MIN_SCORE:
        return "C"
    return "D"


class DuplicateGroup(BaseModel):
    code_hash: str = Field(description="raw code_sha256 (find_duplicates) or normalised code_fingerprint (near)")
    prompt_hash: str
    canonical: str = Field(description="sample id kept")
    duplicates: list[str] = Field(default_factory=list, description="sample ids marked duplicate_of=canonical")


def _rank(row: dict[str, Any]) -> tuple:
    """Lower is better: tier, -score, gate errors, un-captioned, key (stable)."""
    score = row.get("score")
    return (
        TIER_ORDER.get(str(row.get("quality_tier") or "D"), 9),
        -(score if isinstance(score, (int, float)) else -1.0),
        int(row.get("gate_errors") or 0),
        0 if row.get("has_captions") else 1,
        str(row.get("key") or row.get("id") or ""),
    )


def _group(rows: Iterable[dict[str, Any]], column: str) -> list[DuplicateGroup]:
    """Groups of ≥ 2 rows sharing ``(row[column], prompt_hash)``; blank keys never group.
    The rows are not modified — ``mark_duplicates`` stamps them."""
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = str(r.get(column) or "")
        if not key:
            continue
        buckets[(key, str(r.get("prompt_hash") or ""))].append(r)
    groups = []
    for (key, ph), members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        members = sorted(members, key=_rank)
        groups.append(DuplicateGroup(code_hash=key, prompt_hash=ph, canonical=str(members[0]["id"]),
                                     duplicates=[str(m["id"]) for m in members[1:]]))
    return groups


def find_duplicates(rows: Iterable[dict[str, Any]]) -> list[DuplicateGroup]:
    """Exact duplicates: identical RAW ``(code_sha256, prompt_hash)`` — the only DROP set."""
    return _group(rows, "code_sha256")


def find_near_duplicates(rows: Iterable[dict[str, Any]]) -> list[DuplicateGroup]:
    """Normalised duplicates (``code_fingerprint``) — MARKED as ``near_duplicate_of``, never dropped."""
    return _group(rows, "code_fingerprint")


def mark_duplicates(rows: list[dict[str, Any]]) -> list[DuplicateGroup]:
    """Stamp ``duplicate_of`` (raw) and ``near_duplicate_of`` (normalised) in place;
    returns the exact groups — the only ones a caller may drop."""
    groups = find_duplicates(rows)
    dup_of = {d: g.canonical for g in groups for d in g.duplicates}
    near_of = {d: g.canonical for g in find_near_duplicates(rows) for d in g.duplicates}
    for r in rows:
        rid = str(r.get("id"))
        r["duplicate_of"] = dup_of.get(rid, "")
        r["near_duplicate_of"] = near_of.get(rid, "")
    return groups
