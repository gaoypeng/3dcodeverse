"""``<run>/deliverable/``: the hand-over folder for ONE round, regenerated as copies from the run's evidence.

Holds the round's raw code, its artifact (GLB/STL/STEP, URDF + meshes, or judged frames + sheet + GIF),
``sheet.png``, ``captions.json`` and ``manifest.json`` (every other file with role, size and sha256).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from codeverse3d.contracts.common import ENTRY_FILE, Language, Track
from codeverse3d.contracts.run import DeliverableFile, RoundRecord, RunDeliverable, RunRecord
from codeverse3d.proc import write_json_atomic
from codeverse3d.record import git_history
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

#: a round's build output → its role in the hand-over (these and ``meshes/`` are what a round keeps)
_ARTIFACT_ROLES: tuple[tuple[str, str], ...] = (
    ("object.glb", "model"),
    ("object.stl", "model"),
    ("object.step", "model"),
    ("robot.urdf", "urdf"),
    ("preview.gif", "preview"),
    ("frames_sheet.png", "sheet"),
)
#: per-link meshes of an articulated round (``robot.urdf`` references them)
_MESH_DIR = "meshes"


def keep_round_artifacts(ws: Workspace, round_index: int) -> list[str]:
    """Copy the round's build outputs to ``artifacts/rNN/`` (names relative to ``artifacts/``).

    Real copies, never hard links, so a later in-place build cannot change a kept round.
    A scene keeps nothing here (its hand-over is the git commit)."""
    dest = ws.round_artifacts(round_index)
    if dest.exists():  # the same round built again: its last build wins
        shutil.rmtree(dest)
    names = [n for n, _ in _ARTIFACT_ROLES if (ws.artifacts / n).is_file()]
    meshes = sorted((ws.artifacts / _MESH_DIR).glob("*.glb")) if (ws.artifacts / _MESH_DIR).is_dir() else []
    names += [p.relative_to(ws.artifacts).as_posix() for p in meshes]
    for rel in names:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ws.artifacts / rel, dest / rel)
    return names


class _Writer:
    """Collects files into ``deliverable/`` as real copies (a hard link would let edits break the manifest sha256)."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.files: list[DeliverableFile] = []
        self.total = 0

    def add_bytes(self, rel: str, data: bytes, role: str) -> None:
        dest = self.ws.deliverable / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self.total += len(data)
        self.files.append(DeliverableFile(path=f"deliverable/{rel}", role=role, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))

    def add_file(self, src: Path, rel: str, role: str) -> None:
        if src.is_file():
            self.add_bytes(rel, src.read_bytes(), role)


def _code_tree(ws: Workspace, rnd: RoundRecord | None) -> tuple[dict[str, bytes], str]:
    """``src/**`` (+ ``public/**``) at the round's commit, else the working tree."""
    if rnd is not None and rnd.commit:
        try:
            if ws.has_commit(rnd.commit):
                return git_history.read_tree_at(ws, rnd.commit), "commit"
        except git_history.GitReadError as e:
            log.warning("deliverable: git read failed in %s: %s", ws.root, e)
    try:
        return git_history.read_working_tree(ws), "working_tree"
    except OSError as e:  # pragma: no cover - defensive
        log.warning("deliverable: working tree unreadable in %s: %s", ws.root, e)
        return {}, "working_tree"


def round_outputs(ws: Workspace, rnd: RoundRecord | None) -> Path | None:
    """The build outputs retained for this round; never substitute another build."""
    if rnd is None:
        return None
    kept = ws.round_artifacts(rnd.index)
    return kept if kept.is_dir() else None


def _copy_artifacts(ws: Workspace, record: RunRecord, rnd: RoundRecord | None, out: Path | None, w: _Writer) -> None:
    if out is None:
        return
    for name, role in _ARTIFACT_ROLES:
        w.add_file(out / name, name, role)
    if record.spec.language is Language.URDF_BLENDER and (out / _MESH_DIR).is_dir():
        for p in sorted((out / _MESH_DIR).glob("*.glb")):
            w.add_file(p, f"{_MESH_DIR}/{p.name}", "mesh")
    if record.spec.track is Track.GRAPHICS and rnd is not None and rnd.renders is not None:
        for v in rnd.renders.views:  # the judged frames (renders/rNN), one per sampled time
            src = ws.rebase(v.path)
            w.add_file(src, f"frames/{src.name}", "frames")


def _copy_sheet(ws: Workspace, rnd: RoundRecord | None, w: _Writer) -> None:
    if rnd is None or rnd.renders is None or not rnd.renders.contact_sheet:
        return
    sheet = ws.rebase(rnd.renders.contact_sheet)
    if sheet.is_file():
        w.add_file(sheet, f"sheet{sheet.suffix or '.png'}", "sheet")


def _copy_captions(ws: Workspace, record: RunRecord, w: _Writer) -> None:
    caps = record.extra.get("captions") or {}
    if not caps.get("detailed"):
        w.add_file(ws.root / "captions.json", "captions.json", "captions")
        return
    keys = ("detailed", "instruction", "factory")
    w.add_bytes("captions.json", json.dumps({k: str(caps.get(k, "") or "") for k in keys},
                                            indent=2, ensure_ascii=False).encode(), "captions")


def _stable_timestamp(previous: RunDeliverable | None, current: RunDeliverable) -> datetime:
    """Keep the old timestamp when the content is byte-identical."""
    if previous is None or previous.generated_at is None:
        return current.generated_at or datetime.now(UTC)
    same = {(f.path, f.sha256) for f in previous.files} == {(f.path, f.sha256) for f in current.files}
    return previous.generated_at if same else (current.generated_at or datetime.now(UTC))


def build_deliverable(ws: Workspace, record: RunRecord, round_index: int | None, *, clean: bool = True) -> RunDeliverable:
    """(Re)build ``<run>/deliverable/`` for round ``round_index`` and return the manifest (idempotent)."""
    rnd = next((r for r in record.rounds if r.index == round_index), None)
    previous = load_deliverable(ws) if ws.deliverable_manifest_path.is_file() else None
    out = round_outputs(ws, rnd)
    if clean and ws.deliverable.exists():
        shutil.rmtree(ws.deliverable)
    ws.ensure_layout()
    w = _Writer(ws)
    files, code_source = _code_tree(ws, rnd)
    for rel, data in sorted(files.items()):
        w.add_bytes(rel, data, "code" if rel.startswith("src/") else "bundle")
    _copy_artifacts(ws, record, rnd, out, w)
    _copy_sheet(ws, rnd, w)
    _copy_captions(ws, record, w)
    entry = ENTRY_FILE.get(record.spec.language, "")
    manifest = RunDeliverable(
        round=(rnd.index if rnd is not None else None), commit=(rnd.commit if rnd is not None else ""), code_source=code_source,
        entry=f"deliverable/{entry}" if entry in files else "",
        files=sorted(w.files, key=lambda f: f.path), total_bytes=w.total,
        generated_at=datetime.now(UTC),
    )
    manifest.generated_at = _stable_timestamp(previous, manifest)
    write_json_atomic(ws.deliverable_manifest_path, manifest.model_dump(mode="json"))
    return manifest


def load_deliverable(ws: Workspace) -> RunDeliverable | None:
    """``deliverable/manifest.json``, else None."""
    path = ws.deliverable_manifest_path
    if not path.is_file():
        return None
    try:
        return RunDeliverable.model_validate_json(path.read_text())
    except Exception as e:
        log.warning("unreadable deliverable manifest in %s: %s", ws.root, e)
        return None
