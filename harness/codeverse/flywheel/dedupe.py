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
from collections.abc import Iterable, Mapping
from pathlib import Path

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
