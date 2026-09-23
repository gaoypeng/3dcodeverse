"""The one walk over a battery's run records, and the one "which prompt is this" rule.

The walk is ``record.unique_files`` (symlinked cells followed and counted once, the
``_assets`` / ``_cand`` sub-workspaces skipped) — each hand-rolled copy of it has at some
point counted a run twice, or (a bare ``rglob``) missed every symlinked cell."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.proc import read_json_or_none
from codeverse3d.record.record import unique_files


def records(root: Path, *, track: str | None = None) -> list[tuple[Path, dict]]:
    """``(record.json path, raw record)`` for every run under ``root``, once per run on
    disk; an unreadable file is skipped, and ``track`` keeps only that track's runs."""
    out = []
    for rec in unique_files(root, "record.json"):
        data = read_json_or_none(rec)
        if data is not None and (track is None or (data.get("spec") or {}).get("track") == track):
            out.append((rec, data))
    return out


def prompt_of(path: Path, root: Path | None = None) -> str:
    """The battery cell a file belongs to: the directory after ``cells/`` (``ab_plan``,
    ``compare_backends``) or ``runs/`` (``bench run``).  With ``root`` only the part below
    it is read, and a file in no such layout is named by its first directory there;
    otherwise by its parent directory."""
    parts = path.relative_to(root).parts if root is not None else path.parts
    for anchor in ("cells", "runs"):
        if anchor in parts:
            return parts[parts.index(anchor) + 1]
    return parts[0] if root is not None and len(parts) > 1 else path.parent.name
