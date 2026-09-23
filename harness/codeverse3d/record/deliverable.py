"""``<run>/deliverable/`` — the hand-over folder for ONE round, and what every round keeps
so that any of them can be handed over later without a rebuild.

Everything in the folder is a *copy*, regenerated from the run's own evidence, so it can
be zipped and sent on its own:

* ``src/**`` (+ ``public/**`` for scenes, their GLB assets included) — the raw code at the
  round's commit;
* the round's artifact — ``object.glb`` (+ ``.stl`` / ``.step`` / textured GLB), or
  ``robot.urdf`` + ``meshes/*.glb``, or ``frames/*.png`` (the judged frames) +
  ``frames_sheet.png`` + ``preview.gif`` — from ``artifacts/rNN/`` (:func:`keep_round_artifacts`);
* ``sheet.png`` — the round's contact sheet (one picture of the result);
* ``captions.json`` when the run is captioned;
* ``manifest.json`` — every OTHER file with its role, size and sha256.  It does not list
  itself: a file cannot carry its own hash, and the returned object must be exactly what
  is on disk.

WHICH round is not decided here: since 2026-09-22 the run keeps every round and ends at the
last one, and ``codeverse3d.addons.select`` picks a round and packages it.  Renders per
round, gates, judge verdicts and measurements stay in the evidence bucket (``artifacts/`` =
``evidence/``); tokens, prices and settings stay in ``telemetry/``.
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
from codeverse3d.proc import read_json_or_none, sha256_file, write_json_atomic
from codeverse3d.record import _git
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024

#: a round's build output → its role in the hand-over.  Exactly these (and ``meshes/``)
#: are what a round keeps under ``artifacts/rNN/``: the census, build.json, the .blend and
#: the export scratch dirs are evidence of the build, not part of what was built.
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
    """Copy the round's build outputs to ``artifacts/rNN/`` (names relative to
    ``artifacts/``); ``[]`` when the round built nothing to keep.

    Only the files a hand-over needs, and real copies: the next round's build replaces the
    canonical ones, and a kept round must never change under a writer that happens to
    write in place (a hard link would share that inode).  A scene keeps nothing here — its
    hand-over (``src/`` + ``public/``, GLB assets included) is the round's git commit — and
    a graphics round keeps its sheet and GIF; its judged frames are in ``renders/rNN/``."""
    dest = ws.round_artifacts(round_index)
    if dest.exists():  # the same round built again (a transport retry): its last build wins
        shutil.rmtree(dest)
    names = [n for n, _ in _ARTIFACT_ROLES if (ws.artifacts / n).is_file()]
    meshes = sorted((ws.artifacts / _MESH_DIR).glob("*.glb")) if (ws.artifacts / _MESH_DIR).is_dir() else []
    names += [p.relative_to(ws.artifacts).as_posix() for p in meshes]
    for rel in names:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ws.artifacts / rel, dest / rel)
    return names


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
        self.files.append(DeliverableFile(path=f"deliverable/{rel}", role=role, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))

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
                                          sha256=sha256_file(src)))
        return True


def _place(src: Path, dest: Path) -> None:
    """ALWAYS a real copy, never a hard link: a user editing the delivered file
    would otherwise mutate the canonical evidence in ``artifacts/`` and silently
    break the sha256 recorded in the manifest.  The size caps above already bound
    the copy cost."""
    shutil.copy2(src, dest)


def _code_tree(ws: Workspace, rnd: RoundRecord | None) -> tuple[dict[str, bytes], str]:
    """``src/**`` (+ ``public/**``) at the round's commit, else the working tree."""
    if rnd is not None and rnd.commit:
        try:
            if ws.has_commit(rnd.commit):
                return _git.read_tree_at(ws, rnd.commit), "commit"
        except _git.GitReadError as e:
            log.warning("deliverable: git read failed in %s: %s", ws.root, e)
    try:
        return _git.read_working_tree(ws), "working_tree"
    except OSError as e:  # pragma: no cover - defensive
        log.warning("deliverable: working tree unreadable in %s: %s", ws.root, e)
        return {}, "working_tree"


def round_outputs(ws: Workspace, rnd: RoundRecord | None) -> Path | None:
    """Where the round's build outputs are: ``artifacts/rNN/``, or — a run recorded before
    rounds kept their own (2026-09-22) — ``artifacts/`` itself, but only for the round
    that run's finalise rebuilt there: the legacy ``RunRecord.best_round``, or — a record
    rewritten by a build that dropped that key — the round its old deliverable packaged."""
    if rnd is None:
        return None
    kept = ws.round_artifacts(rnd.index)
    if kept.is_dir():
        return kept
    return ws.artifacts if legacy_canonical_round(ws) == rnd.index else None


def legacy_canonical_round(ws: Workspace) -> int | None:
    """The round a run recorded before rounds kept ``artifacts/rNN/`` rebuilt into ``artifacts/``
    (its record's ``best_round``, else its deliverable's round); None for a run that keeps its
    rounds — there ``artifacts/`` is the LAST build's, no round's by name."""
    legacy = (read_json_or_none(ws.record_path) or {}).get("best_round")
    if isinstance(legacy, int):
        return legacy
    if any(ws.artifacts.glob("r[0-9][0-9]")):
        return None
    old = load_deliverable(ws)
    return old.round if old is not None else None


def texture_report_for(ws: Workspace, glb: Path) -> dict | None:
    """The texture report (raw ``artifacts/textures/texturing.json``) of a pass that started
    from these exact GLB bytes, else None.  The report names the GLB it consumed; a pack made
    mid-round, or for another round, is not this round's."""
    from codeverse3d.texturing.run import report_path

    rep = read_json_or_none(report_path(ws)) or {}
    src = ws.rebase(str(rep["glb_in"])) if rep.get("glb_in") else None
    if src is None or not src.is_file() or not glb.is_file() or sha256_file(src) != sha256_file(glb):
        return None
    return rep


def texture_shipped_for(ws: Workspace, record: RunRecord, out: Path | None) -> bool:
    """Did a SHIPPED texture pass start from the round whose build outputs are ``out``
    (``round_outputs``)?  Old layout (``out`` is ``artifacts/``): the one canonical pack
    belonged to the one round finalise rebuilt, so the record's flag answers."""
    if out is None:
        return False
    if out == ws.artifacts:
        return bool((record.extra.get("texturing") or {}).get("shipped"))
    return bool((texture_report_for(ws, out / "object.glb") or {}).get("shipped"))


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
    tex = record.extra.get("texturing") or {}
    if texture_shipped_for(ws, record, out):
        w.add_file(ws.artifacts / "object_textured.glb", "object_textured.glb", "model")
        tex_dir = ws.root / str(tex.get("textures_dir") or "artifacts/textures")
        for p in sorted(tex_dir.glob("*.png")) if tex_dir.is_dir() else []:
            w.add_file(p, f"textures/{p.name}", "texture")


def _copy_sheet(ws: Workspace, rnd: RoundRecord | None, w: _Writer) -> None:
    if rnd is None or rnd.renders is None or not rnd.renders.contact_sheet:
        return
    sheet = ws.rebase(rnd.renders.contact_sheet)
    if sheet.is_file():
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


def build_deliverable(ws: Workspace, record: RunRecord, round_index: int | None, *, clean: bool = True) -> RunDeliverable:
    """(Re)build ``<run>/deliverable/`` for round ``round_index`` and return the typed manifest.

    Idempotent: the folder is rebuilt from scratch every time, so a second call
    on an unchanged run produces byte-identical content."""
    rnd = next((r for r in record.rounds if r.index == round_index), None)
    previous = load_deliverable(ws) if ws.deliverable_manifest_path.is_file() else None
    out = round_outputs(ws, rnd)  # before the wipe: an old run may be found by its old manifest
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
        files=sorted(w.files, key=lambda f: f.path), total_bytes=w.total, skipped=w.skipped,
        generated_at=datetime.now(UTC),
    )
    manifest.generated_at = _stable_timestamp(previous, manifest)
    write_json_atomic(ws.deliverable_manifest_path, manifest.model_dump(mode="json"))
    return manifest  # exactly what is on disk: len(files) and total_bytes == sum(f.bytes)


def load_deliverable(ws: Workspace) -> RunDeliverable | None:
    """``deliverable/manifest.json`` (the round ``codeverse3d.addons.select`` packaged), else None."""
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
