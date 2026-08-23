"""One dataset sample from one run: typed ``meta.json`` + folder layout.

Sample folder (STORAGE_RULES §3/§8 compatible)::

    <out>/<track>/<language>/<key>/
      code.<ext>        copy of the entry file (parquet ``code`` column)
      src/**            the full raw code tree at the exported commit (lossless)
      robot.urdf        (urdf_blender) copy of src/robot.urdf
      meta.json         SampleMeta (typed, below)
      captions.json     {detailed, instruction, factory} or {} when not captioned
      renders/          view_*.png, sheet.png, turntable.mp4, object.glb (< 20 MB)
      meshes/<link>.glb (urdf_blender) per-link meshes referenced by robot.urdf
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
from codeverse.flywheel.dedupe import code_fingerprint
from codeverse.flywheel.quality import QualityTier, prompt_hash, quality_tier
from codeverse.flywheel.record import best_round_index, effective_judgment, round_summary
from codeverse.workspace import Workspace

#: entry file inside src/ per language
ENTRY_BY_LANGUAGE: dict[Language, str] = {
    Language.BLENDER: "src/model.py",
    Language.CADQUERY: "src/model.py",
    Language.URDF_BLENDER: "src/model.py",
    Language.THREEJS: "src/object.js",
    Language.SCENE_THREEJS: "src/scene.js",
    Language.GLSL_SHADER: "src/shader.frag",
    Language.OPENGL_PYTHON: "src/program.py",
}
#: top-level copy of the entry (``code.<ext>``)
CODE_FILE_BY_LANGUAGE: dict[Language, str] = {
    Language.BLENDER: "code.py",
    Language.CADQUERY: "code.py",
    Language.URDF_BLENDER: "code.py",
    Language.THREEJS: "code.js",
    Language.SCENE_THREEJS: "code.js",
    Language.GLSL_SHADER: "code.frag",
    Language.OPENGL_PYTHON: "code.py",
}
LANGUAGE_LABEL: dict[Language, str] = {
    Language.BLENDER: "Blender Python",
    Language.CADQUERY: "CadQuery (Python)",
    Language.URDF_BLENDER: "URDF + Blender Python",
    Language.THREEJS: "Three.js (ESM)",
    Language.SCENE_THREEJS: "Three.js scene (multi-file ESM + GLSL)",
    Language.GLSL_SHADER: "GLSL fragment shader",
    Language.OPENGL_PYTHON: "OpenGL (moderngl Python + GLSL)",
}
TYPE_LABEL: dict[Track, str] = {
    Track.STATIC_OBJECT: "3D Objects",
    Track.ARTICULATED_OBJECT: "Articulated Objects",
    Track.SCENE: "3D Scenes",
    Track.GRAPHICS: "Procedural Graphics",
}
MAX_GLB_BYTES = 20 * 1024 * 1024
SOURCE_NAME = "3dcodeverse"
SAMPLE_LICENSE = "CC-BY-4.0"


class SampleError(RuntimeError):
    """The run cannot be turned into a sample (no code, no commit, ...)."""


class AcceptanceEntry(BaseModel):
    """One plan acceptance item joined with the judge's verdict for the exported round."""

    id: str
    text: str = ""
    how: str = ""
    priority: str = ""
    passed: bool | None = None


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
    acceptance: list[AcceptanceEntry] = Field(default_factory=list, description="plan checklist + verdicts")
    gate_errors: int = Field(default=0, description="error findings across all gates of the exported round")
    gate_summary: dict[str, int] = Field(default_factory=dict, description="gate name → error count")
    quality_tier: QualityTier = "D"
    rounds: int = 0
    best_round: int | None = None
    rounds_summary: list[dict[str, Any]] = Field(default_factory=list, description="compact per-round digest")
    stop_reason: str = ""
    code_commit: str = ""
    code_source: str = Field(default="commit", description="commit | working_tree")
    code_fingerprint: str = Field(default="", description="sha256 of the normalised src/** tree")
    prompt_hash: str = ""
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
        names = [v.name for v in rnd.renders.views]
        by_stem = len(set(names)) != len(names)  # scene views repeat a camera name per time → use file stems
        for v in rnd.renders.views:
            src = _resolve(ws, v.path)
            if src is None:
                continue
            name = f"view_{src.stem if by_stem else v.name}{src.suffix or '.png'}"
            i = 2
            while f"renders/{name}" in out:
                name = f"view_{src.stem}_{i}{src.suffix or '.png'}"
                i += 1
            _cp(src, name)
        sheet = _resolve(ws, rnd.renders.contact_sheet)
        if sheet is not None:
            _cp(sheet, f"sheet{sheet.suffix or '.png'}")
        tt = _resolve(ws, rnd.renders.turntable)
        if tt is not None:
            _cp(tt, f"turntable{tt.suffix or '.mp4'}")
    glb = ws.artifacts / "object.glb"
    if glb.is_file() and glb.stat().st_size < MAX_GLB_BYTES:
        _cp(glb, "object.glb")
    gif = ws.artifacts / "preview.gif"  # graphics runs: animated loop preview
    if gif.is_file() and gif.stat().st_size < MAX_GLB_BYTES:
        _cp(gif, "preview.gif")
    return out


def copy_textured(ws: Workspace, record: RunRecord, dest: Path) -> list[str]:
    """When the texture pass shipped (``record.extra['texturing'].shipped``):
    copy the generated ``textures/*.png`` and ``object_textured.glb`` into the sample."""
    tex = record.extra.get("texturing") or {}
    if not tex.get("shipped"):
        return []
    out: list[str] = []
    tex_dir = ws.root / str(tex.get("textures_dir") or "artifacts/textures")
    if tex_dir.is_dir():
        for p in sorted(tex_dir.glob("*.png")):
            (dest / "textures").mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest / "textures" / p.name)
            out.append(f"textures/{p.name}")
    glb = _resolve(ws, str(tex.get("glb_textured") or "")) or (
        ws.artifacts / "object_textured.glb" if (ws.artifacts / "object_textured.glb").is_file() else None)
    if glb is not None and glb.stat().st_size < MAX_GLB_BYTES:
        (dest / "renders").mkdir(parents=True, exist_ok=True)
        shutil.copy2(glb, dest / "renders" / "object_textured.glb")
        out.append("renders/object_textured.glb")
    return out


def copy_link_meshes(ws: Workspace, dest: Path) -> list[str]:
    """(urdf_blender) copy ``artifacts/meshes/*.glb`` so the sample's robot.urdf resolves."""
    src = ws.artifacts / "meshes"
    if not src.is_dir():
        return []
    out: list[str] = []
    total = 0
    for p in sorted(src.glob("*.glb")):
        total += p.stat().st_size
        if total > MAX_GLB_BYTES:
            break
        (dest / "meshes").mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest / "meshes" / p.name)
        out.append(f"meshes/{p.name}")
    return out


def acceptance_entries(record: RunRecord, rnd: RoundRecord | None) -> list[AcceptanceEntry]:
    """Plan checklist items joined with the round's judge verdicts (ids only when no plan)."""
    j = effective_judgment(rnd) if rnd is not None else None
    verdicts = dict(j.acceptance_results) if j is not None else {}
    items = list(getattr(record.plan, "acceptance", []) or []) if record.plan is not None else []
    out = [AcceptanceEntry(id=a.id, text=a.text, how=a.how, priority=a.priority, passed=verdicts.get(a.id)) for a in items]
    known = {a.id for a in items}
    out += [AcceptanceEntry(id=k, passed=v) for k, v in verdicts.items() if k not in known]
    return out


def gate_error_summary(rnd: RoundRecord | None) -> dict[str, int]:
    return {g.gate: len(g.errors) for g in rnd.gates} if rnd is not None else {}


def build_meta(
    ws: Workspace, record: RunRecord, *, key: str, entry: str, files: list[str], renders: list[str],
    rnd: RoundRecord | None, code_source: str, code: dict[str, bytes] | None = None,
    captions: dict[str, Any] | None = None,
) -> SampleMeta:
    spec = record.spec
    j = effective_judgment(rnd) if rnd is not None else None  # degraded verdicts count as unjudged
    caps = captions if captions is not None else (record.extra.get("captions") or {})
    code_files = [f for f in files if f.startswith("src/")]
    gates = gate_error_summary(rnd)
    n_gate_errors = sum(gates.values())
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
        acceptance=acceptance_entries(record, rnd),
        gate_errors=n_gate_errors,
        gate_summary=gates,
        quality_tier=quality_tier(passed=j.passed if j else None, gate_errors=n_gate_errors, score=j.overall if j else None),
        rounds=len(record.rounds),
        best_round=rnd.index if rnd is not None else None,
        rounds_summary=[round_summary(r) for r in record.rounds],
        stop_reason=str(record.extra.get("stop_reason", "") or ""),
        code_commit=rnd.commit if rnd is not None else "",
        code_source=code_source,
        code_fingerprint=code_fingerprint(code) if code else "",
        prompt_hash=prompt_hash(spec.prompt),
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
