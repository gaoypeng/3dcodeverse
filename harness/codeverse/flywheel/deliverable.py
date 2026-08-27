"""``<run>/deliverable/`` — the hand-over folder: what the user actually asked for.

Everything in here is a *copy*, regenerated from the run's own evidence, so the
folder can be zipped and sent on its own:

* ``src/**`` (+ ``public/**`` for scenes) — the raw code at the BEST round's commit;
* the canonical artifact — ``object.glb`` (+ ``.stl`` / ``.step`` / textured GLB),
  or ``robot.urdf`` + ``meshes/*.glb``, or ``frames/*.png`` + ``preview.gif``;
* ``sheet.png`` — the best round's contact sheet (one picture of the result);
* ``captions.json`` when the run is captioned;
* ``manifest.json`` — every OTHER file with its role, size and sha256 (mirrored
  into ``record.deliverable``).  It does not list itself: a file cannot carry its
  own hash, and the returned object must be exactly what is on disk.

Renders per round, gates, judge verdicts and measurements stay in the evidence
bucket (``artifacts/`` = ``evidence/``); tokens, prices and settings stay in
``telemetry/``.  Nothing else belongs here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.run import DeliverableFile, RoundRecord, RunDeliverable, RunRecord
from codeverse.flywheel import _git
from codeverse.proc import write_json_atomic
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024

#: artifact file → role, copied when present (object tracks / articulated / graphics)
_ARTIFACT_ROLES: tuple[tuple[str, str], ...] = (
    ("object.glb", "model"),
    ("object.stl", "model"),
    ("object.step", "model"),
    ("object_textured.glb", "model"),
    ("robot.urdf", "urdf"),
    ("preview.gif", "preview"),
    ("frames_sheet.png", "sheet"),
)


class DeliverableError(RuntimeError):
    """The run has nothing that can be handed over."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Writer:
    """Collects files into ``deliverable/`` and remembers role/size/hash."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.files: list[DeliverableFile] = []
        self.skipped: dict[str, str] = {}
        self.total = 0

    def add_bytes(self, rel: str, data: bytes, role: str) -> None:
        if len(data) > MAX_FILE_BYTES:
            self.skipped[rel] = f"file too large ({len(data)} bytes)"
            return
        if self.total + len(data) > MAX_TOTAL_BYTES:
            self.skipped[rel] = "deliverable size cap reached"
            return
        dest = self.ws.deliverable / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self.total += len(data)
        self.files.append(DeliverableFile(path=f"deliverable/{rel}", role=role, bytes=len(data), sha256=_sha256(data)))

    def add_file(self, src: Path, rel: str, role: str) -> bool:
        if not src.is_file():
            return False
        size = src.stat().st_size
        if size > MAX_FILE_BYTES:
            self.skipped[rel] = f"file too large ({size} bytes)"
            return False
        if self.total + size > MAX_TOTAL_BYTES:
            self.skipped[rel] = "deliverable size cap reached"
            return False
        dest = self.ws.deliverable / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        _place(src, dest)
        self.total += size
        self.files.append(DeliverableFile(path=f"deliverable/{rel}", role=role, bytes=size,
                                          sha256=_sha256(src.read_bytes())))
        return True


def _place(src: Path, dest: Path) -> None:
    """ALWAYS a real copy, never a hard link: a user editing the delivered file
    would otherwise mutate the canonical evidence in ``artifacts/`` and silently
    break the sha256 recorded in the manifest.  The size caps above already bound
    the copy cost."""
    shutil.copy2(src, dest)


def _resolve(ws: Workspace, path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = ws.root / p
    return p if p.is_file() else None


def _code_tree(ws: Workspace, rnd: RoundRecord | None) -> tuple[dict[str, bytes], str]:
    """``src/**`` (+ ``public/**``) at the best round's commit, else the working tree."""
    if rnd is not None and rnd.commit:
        try:
            if _git.commit_exists(ws, rnd.commit):
                return _git.read_tree_at(ws, rnd.commit), "commit"
        except _git.GitReadError as e:
            log.warning("deliverable: git read failed in %s: %s", ws.root, e)
    try:
        return _git.read_working_tree(ws), "working_tree"
    except OSError as e:  # pragma: no cover - defensive
        log.warning("deliverable: working tree unreadable in %s: %s", ws.root, e)
        return {}, "working_tree"


def _copy_artifacts(ws: Workspace, record: RunRecord, w: _Writer) -> None:
    tex = record.extra.get("texturing") or {}
    for name, role in _ARTIFACT_ROLES:
        if name == "object_textured.glb" and not tex.get("shipped"):
            # a texture pass that did not ship is not a deliverable, even if a stray
            # canonical file exists (same gate flywheel/sample.copy_textured applies)
            continue
        w.add_file(ws.artifacts / name, name, role)
    meshes = ws.artifacts / "meshes"
    if record.spec.language is Language.URDF_BLENDER and meshes.is_dir():
        for p in sorted(meshes.glob("*.glb")):
            w.add_file(p, f"meshes/{p.name}", "mesh")
    frames = ws.artifacts / "frames"
    if frames.is_dir():
        for p in sorted(frames.glob("*.png")):
            w.add_file(p, f"frames/{p.name}", "frames")
    if tex.get("shipped"):
        tex_dir = ws.root / str(tex.get("textures_dir") or "artifacts/textures")
        for p in sorted(tex_dir.glob("*.png")) if tex_dir.is_dir() else []:
            w.add_file(p, f"textures/{p.name}", "texture")


def _copy_sheet(ws: Workspace, rnd: RoundRecord | None, w: _Writer) -> None:
    sheet = _resolve(ws, rnd.renders.contact_sheet) if (rnd is not None and rnd.renders is not None) else None
    if sheet is not None:
        w.add_file(sheet, f"sheet{sheet.suffix or '.png'}", "sheet")


def _copy_captions(ws: Workspace, record: RunRecord, w: _Writer) -> None:
    caps = record.extra.get("captions") or {}
    if not caps.get("detailed"):
        side = ws.root / "captions.json"
        if side.is_file():
            w.add_file(side, "captions.json", "captions")
            return
        return
    keys = ("detailed", "instruction", "factory")
    w.add_bytes("captions.json", json.dumps({k: str(caps.get(k, "") or "") for k in keys},
                                            indent=2, ensure_ascii=False).encode(), "captions")


def _stable_timestamp(previous: RunDeliverable | None, current: RunDeliverable) -> datetime:
    """Keep the old timestamp when the content is byte-identical, so re-running the
    packager (or the migration) is a no-op instead of a diff."""
    if previous is None or previous.generated_at is None:
        return current.generated_at or datetime.now(UTC)
    same = {(f.path, f.sha256) for f in previous.files} == {(f.path, f.sha256) for f in current.files}
    return previous.generated_at if same else (current.generated_at or datetime.now(UTC))


def build_deliverable(ws: Workspace, record: RunRecord, *, clean: bool = True) -> RunDeliverable:
    """(Re)build ``<run>/deliverable/`` and return the typed manifest.

    Idempotent: the folder is rebuilt from scratch every time, so a second call
    on an unchanged run produces byte-identical content."""
    from codeverse.flywheel.record import best_round_index

    idx = best_round_index(record)
    rnd = next((r for r in record.rounds if r.index == idx), None)
    previous = load_deliverable(ws) if ws.deliverable_manifest_path.is_file() else None
    if clean and ws.deliverable.exists():
        shutil.rmtree(ws.deliverable)
    ws.ensure_layout()
    w = _Writer(ws)
    files, code_source = _code_tree(ws, rnd)
    for rel, data in sorted(files.items()):
        w.add_bytes(rel, data, "code" if rel.startswith("src/") else "bundle")
    _copy_artifacts(ws, record, w)
    _copy_sheet(ws, rnd, w)
    _copy_captions(ws, record, w)
    entry = ENTRY_FILE.get(record.spec.language, "")
    manifest = RunDeliverable(
        best_round=idx, commit=(rnd.commit if rnd is not None else ""), code_source=code_source,
        entry=f"deliverable/{entry}" if entry in files else "",
        files=sorted(w.files, key=lambda f: f.path), total_bytes=w.total, skipped=w.skipped,
        generated_at=datetime.now(UTC),
    )
    manifest.generated_at = _stable_timestamp(previous, manifest)
    write_json_atomic(ws.deliverable_manifest_path, manifest.model_dump(mode="json"))
    return manifest  # exactly what is on disk: len(files) and total_bytes == sum(f.bytes)


def load_deliverable(ws: Workspace, record: RunRecord | None = None) -> RunDeliverable | None:
    """``record.deliverable`` when present, else ``deliverable/manifest.json``, else None."""
    if record is not None and record.deliverable is not None:
        return record.deliverable
    path = ws.deliverable_manifest_path
    if not path.is_file():
        return None
    try:
        return RunDeliverable.model_validate_json(path.read_text())
    except Exception as e:
        log.warning("unreadable deliverable manifest in %s: %s", ws.root, e)
        return None


def deliverable_path(ws: Workspace, name: str) -> Path | None:
    """``deliverable/<name>`` when it exists, else ``artifacts/<name>``, else None.

    The one resolver every consumer (export, gallery, show) uses so both layouts
    work: new runs answer from the hand-over folder, old runs from artifacts/."""
    for candidate in (ws.deliverable / name, ws.artifacts / name):
        if candidate.is_file():
            return candidate
    return None
