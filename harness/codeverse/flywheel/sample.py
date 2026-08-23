"""One dataset sample from one run: typed ``meta.json`` + folder layout.

Sample folder (STORAGE_RULES §3/§8 compatible)::

    <out>/<track>/<language>/<key>/
      code.<ext>        copy of the entry file (parquet ``code`` column)
      src/**            the full raw code tree at the exported commit (lossless)
      robot.urdf        (urdf_blender) copy of src/robot.urdf
      meta.json         SampleMeta (typed, below)
      captions.json     {detailed, instruction, factory} or {} when not captioned
      renders/          view_*.png, sheet.png, turntable.mp4, object.glb (< 20 MB)
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse import __version__
from codeverse.contracts.common import Language, Track
from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.flywheel import _git
from codeverse.flywheel.record import best_round_index
from codeverse.workspace import Workspace

#: entry file inside src/ per language
ENTRY_BY_LANGUAGE: dict[Language, str] = {
    Language.BLENDER: "src/model.py",
    Language.CADQUERY: "src/model.py",
    Language.URDF_BLENDER: "src/model.py",
    Language.THREEJS: "src/object.js",
    Language.SCENE_THREEJS: "src/scene.js",
}
#: top-level copy of the entry (``code.<ext>``)
CODE_FILE_BY_LANGUAGE: dict[Language, str] = {
    Language.BLENDER: "code.py",
    Language.CADQUERY: "code.py",
    Language.URDF_BLENDER: "code.py",
    Language.THREEJS: "code.js",
    Language.SCENE_THREEJS: "code.js",
}
LANGUAGE_LABEL: dict[Language, str] = {
    Language.BLENDER: "Blender Python",
    Language.CADQUERY: "CadQuery (Python)",
    Language.URDF_BLENDER: "URDF + Blender Python",
    Language.THREEJS: "Three.js (ESM)",
    Language.SCENE_THREEJS: "Three.js scene (multi-file ESM + GLSL)",
}
TYPE_LABEL: dict[Track, str] = {
    Track.STATIC_OBJECT: "3D Objects",
    Track.ARTICULATED_OBJECT: "Articulated Objects",
    Track.SCENE: "3D Scenes",
}
MAX_GLB_BYTES = 20 * 1024 * 1024
SOURCE_NAME = "3dcodeverse"
SAMPLE_LICENSE = "CC-BY-4.0"


class SampleError(RuntimeError):
    """The run cannot be turned into a sample (no code, no commit, ...)."""


class SampleMeta(BaseModel):
    """``meta.json`` — everything a consumer needs without opening record.json."""

    id: str
    key: str
    name: str
    type: str
    track: str
    language: str
    language_label: str
    entry: str
    multi_file: bool
    files: list[str] = Field(description="all files in the sample folder (relative)")
    renders: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    source: str = SOURCE_NAME
    source_run: str = ""
    license: str = SAMPLE_LICENSE
    harness_version: str = __version__
    prompt: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    generator: str = ""
    planner: str = ""
    judge_backend: str = ""
    judge_rubric: str = ""
    score: float | None = None
    passed: bool | None = None
    baseline_score: float | None = None
    acceptance_results: dict[str, bool] = Field(default_factory=dict)
    rounds: int = 0
    best_round: int | None = None
    code_commit: str = ""
    code_source: str = Field(default="commit", description="commit | working_tree")
    status: str = ""
    cost_usd: float = 0.0
    usage: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    finished_at: datetime | None = None
    has_captions: bool = False
    captioner: str = ""
    prompt_hashes: dict[str, str] = Field(default_factory=dict)


def sample_key(ws: Workspace) -> str:
    return ws.root.name


def sample_id(record: RunRecord, key: str) -> str:
    return f"{SOURCE_NAME}/{record.spec.track.value}/{record.spec.language.value}/{key}"


def sample_rel_dir(record: RunRecord, key: str) -> Path:
    return Path(record.spec.track.value) / record.spec.language.value / key


def _resolve(ws: Workspace, p: str | None) -> Path | None:
    if not p:
        return None
    path = Path(p)
    if not path.is_absolute():
        path = ws.root / path
    return path if path.is_file() else None


def code_files_for_round(ws: Workspace, rnd: RoundRecord | None) -> tuple[dict[str, bytes], str]:
    """Raw code tree for a round → ``(files, source)`` where source is
    ``commit`` or ``working_tree`` (fallback when the round has no usable commit)."""
    if rnd is not None and rnd.commit and _git.commit_exists(ws, rnd.commit):
        return _git.read_tree_at(ws, rnd.commit), "commit"
    return _git.read_working_tree(ws), "working_tree"


def write_code_tree(dest: Path, files: dict[str, bytes], language: Language) -> tuple[str, list[str]]:
    """Write ``src/**`` + ``code.<ext>`` (+ ``robot.urdf``); return (entry, written paths)."""
    written: list[str] = []
    for rel, data in sorted(files.items()):
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        written.append(rel)
    entry_src = ENTRY_BY_LANGUAGE[language]
    code_name = CODE_FILE_BY_LANGUAGE[language]
    if entry_src in files:
        (dest / code_name).write_bytes(files[entry_src])
        written.append(code_name)
    if language is Language.URDF_BLENDER and "src/robot.urdf" in files:
        (dest / "robot.urdf").write_bytes(files["src/robot.urdf"])
        written.append("robot.urdf")
    return code_name, written


def copy_renders(ws: Workspace, rnd: RoundRecord | None, dest: Path) -> list[str]:
    """Copy the round's views, contact sheet, turntable and a small object.glb."""
    out: list[str] = []
    rdir = dest / "renders"
    rdir.mkdir(parents=True, exist_ok=True)

    def _cp(src: Path | None, name: str) -> None:
        if src is None:
            return
        shutil.copy2(src, rdir / name)
        out.append(f"renders/{name}")

    if rnd is not None and rnd.renders is not None:
        for v in rnd.renders.views:
            src = _resolve(ws, v.path)
            if src is not None:
                suffix = src.suffix or ".png"
                _cp(src, f"view_{v.name}{suffix}")
        sheet = _resolve(ws, rnd.renders.contact_sheet)
        if sheet is not None:
            _cp(sheet, f"sheet{sheet.suffix or '.png'}")
        tt = _resolve(ws, rnd.renders.turntable)
        if tt is not None:
            _cp(tt, f"turntable{tt.suffix or '.mp4'}")
    glb = ws.artifacts / "object.glb"
    if glb.is_file() and glb.stat().st_size < MAX_GLB_BYTES:
        _cp(glb, "object.glb")
    return out


def build_meta(
    ws: Workspace, record: RunRecord, *, key: str, entry: str, files: list[str], renders: list[str],
    rnd: RoundRecord | None, code_source: str,
) -> SampleMeta:
    spec = record.spec
    j = rnd.judgment if rnd is not None else None
    caps = record.extra.get("captions") or {}
    code_files = [f for f in files if f.startswith("src/")]
    name = ""
    if record.plan is not None:
        name = getattr(record.plan, "object_name", "") or getattr(record.plan, "title", "")
    return SampleMeta(
        id=sample_id(record, key),
        key=key,
        name=name,
        type=TYPE_LABEL[spec.track],
        track=spec.track.value,
        language=spec.language.value,
        language_label=LANGUAGE_LABEL[spec.language],
        entry=entry,
        multi_file=len(code_files) > 1,
        files=sorted(files),
        renders=sorted(renders),
        environment=dict(record.environment),
        source_run=str(ws.root),
        prompt=spec.prompt,
        constraints=spec.constraints.model_dump(mode="json", exclude_none=True),
        tags=list(spec.tags),
        generator=spec.backends.generator,
        planner=spec.backends.planner,
        judge_backend=(j.judge_backend if j else "") or spec.backends.judge,
        judge_rubric=j.rubric if j else "",
        score=j.overall if j else None,
        passed=j.passed if j else None,
        baseline_score=record.baseline_score,
        acceptance_results=dict(j.acceptance_results) if j else {},
        rounds=len(record.rounds),
        best_round=rnd.index if rnd is not None else None,
        code_commit=rnd.commit if rnd is not None else "",
        code_source=code_source,
        status=record.status.value,
        cost_usd=round(record.total_usage.cost_usd, 6),
        usage=record.total_usage.model_dump(mode="json"),
        created_at=spec.created_at,
        finished_at=record.finished_at,
        has_captions=bool(caps.get("detailed")),
        captioner=str((caps.get("provenance") or {}).get("captioner", "")),
        prompt_hashes=dict(record.prompt_hashes),
    )


def best_round_record(record: RunRecord) -> RoundRecord | None:
    idx = best_round_index(record)
    return next((r for r in record.rounds if r.index == idx), None)
