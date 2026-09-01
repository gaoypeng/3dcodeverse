"""Near-duplicate detection: code fingerprints + coarse mesh fingerprints.

* ``code_fingerprint(files)``  sha256 of whitespace/comment-normalised code.
* ``code_sha256(files)``       sha256 of the raw bytes (the exact-content hash).
* ``mesh_fingerprint(glb)``    bbox extents (cm), triangle bucket and a 16³ voxel
  occupancy set computed with trimesh — compare with Jaccard.
* ``near_duplicates(items)``   union-find groups: identical code fingerprint OR
  mesh Jaccard ≥ threshold with compatible extents.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

_COMMENT = re.compile(r"^\s*(#|//|\*|/\*).*$")
_TRAILING_COMMENT = re.compile(r"\s+(#|//).*$")
VOXEL_RES = 16


def normalise_code(text: str) -> str:
    """Drop comment-only lines, trailing comments, blank lines and all whitespace."""
    out = []
    for line in text.splitlines():
        if _COMMENT.match(line):
            continue
        line = _TRAILING_COMMENT.sub("", line)
        line = re.sub(r"\s+", "", line)
        if line:
            out.append(line)
    return "\n".join(out)


def code_fingerprint(files: Mapping[str, str | bytes]) -> str:
    """Order-independent sha256 over ``(path, normalised code)`` pairs."""
    h = hashlib.sha256()
    for path in sorted(files):
        data = files[path]
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        h.update(path.encode())
        h.update(b"\0")
        h.update(normalise_code(text).encode())
        h.update(b"\0")
    return h.hexdigest()


def code_sha256(files: Mapping[str, str | bytes]) -> str:
    """Order-independent sha256 over the RAW ``(path, bytes)`` pairs — the
    exact-content counterpart of :func:`code_fingerprint` (no normalisation,
    no lossy decode)."""
    h = hashlib.sha256()
    for path in sorted(files):
        data = files[path]
        raw = data.encode("utf-8") if isinstance(data, str) else data
        h.update(path.encode())
        h.update(b"\0")
        h.update(raw)
        h.update(b"\0")
    return h.hexdigest()


class MeshFingerprint(BaseModel):
    extents_cm: tuple[int, int, int]
    tri_count: int
    tri_bucket: int = Field(description="log2 bucket of the triangle count")
    voxels: list[int] = Field(description="occupied cell indices in a VOXEL_RES³ grid over the bbox")
    digest: str

    def jaccard(self, other: MeshFingerprint) -> float:
        a, b = set(self.voxels), set(other.voxels)
        if not a and not b:
            return 1.0
        return len(a & b) / len(a | b)

    def extents_compatible(self, other: MeshFingerprint, tol: float = 0.1) -> bool:
        for x, y in zip(self.extents_cm, other.extents_cm, strict=True):
            if max(x, y) == 0:
                continue
            if abs(x - y) / max(x, y) > tol:
                return False
        return True


def mesh_fingerprint(glb: Path | str, *, res: int = VOXEL_RES, samples: int = 50_000, seed: int = 0) -> MeshFingerprint:
    """Coarse, pose-sensitive shape fingerprint of a GLB (seeded surface samples → voxel grid).

    Deterministic for identical geometry; near-identical meshes give Jaccard ≈ 1."""
    import trimesh

    loaded = trimesh.load(str(glb), force="scene")
    mesh = loaded.to_mesh() if isinstance(loaded, trimesh.Scene) else loaded
    if mesh is None or mesh.is_empty or len(mesh.faces) == 0:
        raise ValueError(f"mesh_fingerprint: no triangles in {glb}")
    ext = mesh.bounding_box.extents
    pts, _ = trimesh.sample.sample_surface(mesh, samples, seed=seed)
    lo = mesh.bounds[0]
    span = np.where(ext > 1e-9, ext, 1.0)
    idx = np.clip(((pts - lo) / span * res).astype(int), 0, res - 1)
    cells = sorted(set((idx[:, 0] * res * res + idx[:, 1] * res + idx[:, 2]).tolist()))
    tri = int(len(mesh.faces))
    digest = hashlib.sha1(
        (",".join(map(str, cells)) + f"|{[int(round(e * 100)) for e in ext]}").encode()
    ).hexdigest()[:16]
    return MeshFingerprint(
        extents_cm=tuple(int(round(e * 100)) for e in ext),  # type: ignore[arg-type]
        tri_count=tri,
        tri_bucket=int(math.log2(max(tri, 1))),
        voxels=cells,
        digest=digest,
    )


class DedupeItem(BaseModel):
    id: str
    code_fp: str = ""
    mesh_fp: MeshFingerprint | None = None


def near_duplicates(items: Iterable[DedupeItem], *, mesh_threshold: float = 0.9) -> list[list[str]]:
    """Groups (size ≥ 2) of ids that are normalised-code duplicates or near-identical meshes."""
    items = list(items)
    parent = {it.id: it.id for it in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_code: dict[str, str] = {}
    for it in items:
        if it.code_fp:
            if it.code_fp in by_code:
                union(by_code[it.code_fp], it.id)
            else:
                by_code[it.code_fp] = it.id
    meshed = [it for it in items if it.mesh_fp is not None]
    for i, a in enumerate(meshed):
        for b in meshed[i + 1 :]:
            fa, fb = a.mesh_fp, b.mesh_fp
            assert fa is not None and fb is not None
            if fa.extents_compatible(fb) and fa.jaccard(fb) >= mesh_threshold:
                union(a.id, b.id)
    groups: dict[str, list[str]] = {}
    for it in items:
        groups.setdefault(find(it.id), []).append(it.id)
    return sorted((sorted(g) for g in groups.values() if len(g) > 1), key=lambda g: g[0])


# ===================================================================== quality
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
