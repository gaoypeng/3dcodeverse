"""Dataset hygiene: quality tiers + exact-duplicate detection by (code fingerprint, prompt).

* ``quality_tier(passed, gate_errors, score)`` → ``A | B | C | D``::

      A  judged passed AND the best round has zero gate errors
      B  judged passed (some gate errors remain)
      C  not passed but best score ≥ 0.6
      D  everything else (unjudged, failed, low score)

* ``find_duplicates(rows)`` groups index rows with identical
  ``(code_fingerprint, prompt_hash)``; the canonical row of a group is the
  best (tier, score, fewest gate errors, captioned, then key order) and every other row gets
  ``duplicate_of = canonical id``.
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
    code_fingerprint: str
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


def find_duplicates(rows: Iterable[dict[str, Any]]) -> list[DuplicateGroup]:
    """Group rows by exact (code_fingerprint, prompt_hash); only groups with ≥ 2 rows are returned.

    Rows without a fingerprint are never grouped.  The rows are not modified;
    use ``mark_duplicates`` to stamp ``duplicate_of``.
    """
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        fp = str(r.get("code_fingerprint") or "")
        if not fp:
            continue
        buckets[(fp, str(r.get("prompt_hash") or ""))].append(r)
    groups = []
    for (fp, ph), members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        members = sorted(members, key=_rank)
        groups.append(DuplicateGroup(code_fingerprint=fp, prompt_hash=ph, canonical=str(members[0]["id"]),
                                     duplicates=[str(m["id"]) for m in members[1:]]))
    return groups


def mark_duplicates(rows: list[dict[str, Any]]) -> list[DuplicateGroup]:
    """Set ``row["duplicate_of"]`` ("" for canonical rows) in place; returns the groups."""
    for r in rows:
        r["duplicate_of"] = ""
    groups = find_duplicates(rows)
    dup_of = {d: g.canonical for g in groups for d in g.duplicates}
    for r in rows:
        r["duplicate_of"] = dup_of.get(str(r.get("id")), "")
    return groups
