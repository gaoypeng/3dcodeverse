"""Project layout, meta.json schema, validation, and anomaly checks.

A *project* is one independent asset: its code + reference images / prompt / video /
derived renders+glb + a meta.json. A *source* folder holds many projects.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

META_NAME = "meta.json"

# fields a fresh build must NOT clobber — carried over from an existing meta.json
PRESERVE = ("exec", "license", "provenance", "title", "owner")


def merge_preserved(d: Path, meta: dict) -> dict:
    """Carry over user-set / previously-computed fields (exec, license, …) from disk."""
    f = d / META_NAME
    if f.exists():
        try:
            old = json.loads(f.read_text())
            for k in PRESERVE:
                if old.get(k):
                    meta[k] = old[k]
        except Exception:
            pass
    return meta

# code dialect by file extension (overridable with --dialect)
DIALECT_BY_EXT = {
    ".py": "blender_python",   # default for .py here; pass --dialect cadquery if it's CadQuery
    ".scad": "openscad",
    ".glsl": "shader_glsl", ".frag": "shader_glsl", ".vert": "shader_glsl",
    ".js": "jscad", ".fs": "featurescript",
}
CODE_EXTS = set(DIALECT_BY_EXT)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MESH_EXTS = {".glb", ".gltf", ".obj", ".ply", ".stl"}

# anomaly thresholds (warnings, not hard failures)
MAX_CODE_LINES = 5000
MAX_CODE_BYTES = 1_000_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_project(d: Path) -> bool:
    """A directory is a project if it has a meta.json or a top-level code file."""
    return (d / META_NAME).exists() or any(
        c.suffix.lower() in CODE_EXTS for c in d.iterdir() if c.is_file()
    )


def find_projects(root: Path) -> list[Path]:
    """`root` is itself a project, else each immediate project subdir of it."""
    root = root.resolve()
    if is_project(root):
        return [root]
    return [d for d in sorted(root.iterdir()) if d.is_dir() and is_project(d)]


def _code_file(d: Path) -> Path | None:
    files = [c for c in sorted(d.iterdir()) if c.is_file() and c.suffix.lower() in CODE_EXTS]
    # prefer the textured/full code over a *_geo variant if both exist
    files.sort(key=lambda p: ("_geo" in p.stem, p.name))
    return files[0] if files else None


def validate_project(d: Path, source: str) -> tuple[list[str], list[str], dict | None]:
    """Return (errors, warnings, meta-or-None). Errors block upload; warnings don't."""
    errors: list[str] = []
    warnings: list[str] = []

    code = _code_file(d)
    if code is None:
        errors.append("no code file (.py/.scad/.glsl/...) found")
        return errors, warnings, None

    text = code.read_text(errors="replace")
    lines = text.count("\n") + 1
    if lines > MAX_CODE_LINES:
        warnings.append(f"code is {lines} lines (> {MAX_CODE_LINES} — possibly degenerate)")
    if len(text) > MAX_CODE_BYTES:
        warnings.append(f"code is {len(text)} bytes (> {MAX_CODE_BYTES})")

    has_ref = any(c.suffix.lower() in IMAGE_EXTS for c in d.rglob("*") if c.is_file())
    has_prompt = (d / "prompt.txt").exists() or (d / "prompt.json").exists()
    if not (has_ref or has_prompt):
        warnings.append("no reference image and no prompt — the (input -> code) pair is ungrounded")

    # structure convention: renders + mesh belong in renders/, not loose at the top level
    top_assets = [c.name for c in d.iterdir()
                  if c.is_file() and c.suffix.lower() in (IMAGE_EXTS | MESH_EXTS)]
    if top_assets:
        warnings.append(f"{len(top_assets)} render/mesh file(s) at top level — convention is a renders/ folder")

    meta = build_meta(d, source, code, lines)
    return errors, warnings, meta


def build_meta(d: Path, source: str, code: Path, lines: int, dialect: str | None = None) -> dict:
    """Construct (or refresh) the project's meta.json body. Hashes filled in later."""
    project = d.name
    dialect = dialect or DIALECT_BY_EXT.get(code.suffix.lower(), "unknown")
    modality = ["code"]
    if any(c.suffix.lower() in IMAGE_EXTS for c in d.rglob("*") if c.is_file()):
        modality.append("reference_images")
    if (d / "prompt.txt").exists() or (d / "prompt.json").exists():
        modality.append("prompt")
    if any(c.suffix.lower() in MESH_EXTS for c in d.rglob("*") if c.is_file()):
        modality.append("glb")
    return {
        "id": f"{source}/{project}",
        "source": source,
        "project": project,
        "title": project,
        "dialect": dialect,
        "modality": modality,
        "code": {"file": code.name, "lines": lines},
        "license": "",
        "provenance": {},
        "owner": "",
        "contributor": "",
        "dedup": {},
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
    }
