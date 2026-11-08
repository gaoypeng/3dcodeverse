"""Code fingerprints, quality tiers and duplicate groups."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

_COMMENT = re.compile(r"^\s*(#|//|\*|/\*).*$")
_TRAILING_COMMENT = re.compile(r"\s+(#|//).*$")


def normalise_code(text: str) -> str:
    out = []
    for line in text.splitlines():
        if _COMMENT.match(line):
            continue
        line = _TRAILING_COMMENT.sub("", line)
        line = re.sub(r"\s+", "", line)
        if line:
            out.append(line)
    return "\n".join(out)


def _digest(files: Mapping[str, str | bytes], content: Callable[[str | bytes], bytes]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        for part in (path.encode(), content(files[path])):
            h.update(part)
            h.update(b"\0")
    return h.hexdigest()


def code_fingerprint(files: Mapping[str, str | bytes]) -> str:
    return _digest(files, lambda data: normalise_code(
        data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data).encode())


def code_sha256(files: Mapping[str, str | bytes]) -> str:
    return _digest(files, lambda data: data.encode("utf-8") if isinstance(data, str) else data)


# ===================================================================== quality
QualityTier = Literal["A", "B", "C", "D"]
TIER_ORDER: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}
TIER_C_MIN_SCORE = 0.6


def prompt_id(prompt: str) -> str:
    return hashlib.sha256(prompt.strip().encode()).hexdigest()[:16]


def quality_tier(*, passed: bool | None, gate_errors: int, score: float | None) -> QualityTier:
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
    score = row.get("score")
    return (
        TIER_ORDER.get(str(row.get("quality_tier") or "D"), 9),
        -(score if isinstance(score, (int, float)) else -1.0),
        int(row.get("gate_errors") or 0),
        0 if row.get("has_captions") else 1,
        str(row.get("key") or row.get("id") or ""),
    )


def _group(rows: Iterable[dict[str, Any]], column: str) -> list[DuplicateGroup]:
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
    return _group(rows, "code_sha256")


def find_near_duplicates(rows: Iterable[dict[str, Any]]) -> list[DuplicateGroup]:
    return _group(rows, "code_fingerprint")


def mark_duplicates(rows: list[dict[str, Any]]) -> list[DuplicateGroup]:
    groups = find_duplicates(rows)
    dup_of = {d: g.canonical for g in groups for d in g.duplicates}
    near_of = {d: g.canonical for g in find_near_duplicates(rows) for d in g.duplicates}
    for r in rows:
        rid = str(r.get("id"))
        r["duplicate_of"] = dup_of.get(rid, "")
        r["near_duplicate_of"] = near_of.get(rid, "")
    return groups
