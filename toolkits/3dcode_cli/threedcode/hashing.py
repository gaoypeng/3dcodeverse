"""Dedup fingerprints, computed on the contributor's own machine.

- code_hash : sha256 of normalized code (exact-code duplicates)
- geom_hash : sampled mesh hash of a GLB     (geometric duplicates)  [needs `[dedup]` extra]
- phash     : perceptual hash of a render    (visual duplicates)     [needs `[dedup]` extra]

geom/phash are best-effort: if the optional deps aren't installed (or no mesh/render is
present) they're simply omitted — the toolkit's base install stays light, no Blender/GPU.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .schema import IMAGE_EXTS, MESH_EXTS

_WS = re.compile(r"\s+")


def code_hash(text: str) -> str:
    """sha256 of code with comments + whitespace normalized, so cosmetic edits collide."""
    no_block = re.sub(r'""".*?"""|\'\'\'.*?\'\'\'', "", text, flags=re.S)
    no_line = "\n".join(ln.split("#", 1)[0] for ln in no_block.splitlines())
    return "sha256:" + hashlib.sha256(_WS.sub(" ", no_line).strip().encode()).hexdigest()


def geom_hash(glb: Path) -> str | None:
    try:
        import numpy as np
        import trimesh
    except ImportError:
        return None
    try:
        mesh = trimesh.load(str(glb), force="mesh")
        if mesh.is_empty or mesh.vertices.shape[0] == 0:
            return None
        # DETERMINISTIC translate/scale-invariant signature: center, scale-normalize,
        # round, then sort vertex rows (random sampling would hash differently every run).
        v = np.asarray(mesh.vertices, dtype=float)
        v -= v.mean(0)
        scale = float(np.linalg.norm(v, axis=1).max()) or 1.0
        v = np.round(v / scale, 3)
        v = v[np.lexsort(v.T)]
        return "geom:" + hashlib.sha256(v.tobytes()).hexdigest()[:32]
    except Exception:
        return None


def phash(image: Path) -> str | None:
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        return None
    try:
        return "phash:" + str(imagehash.phash(Image.open(image).convert("RGB")))
    except Exception:
        return None


def project_hashes(project_dir: Path, meta: dict) -> dict:
    out: dict[str, str] = {}
    code_file = project_dir / meta["code"]["file"]
    if code_file.exists():
        out["code_hash"] = code_hash(code_file.read_text(errors="replace"))

    meshes = sorted(c for c in project_dir.rglob("*") if c.suffix.lower() in MESH_EXTS)
    if meshes:
        gh = geom_hash(meshes[0])
        if gh:
            out["geom_hash"] = gh

    images = sorted(c for c in project_dir.rglob("*") if c.suffix.lower() in IMAGE_EXTS)
    if images:
        ph = phash(images[0])
        if ph:
            out["phash"] = ph
    return out
