"""Write the routed skill bundles into a workspace as real files, in both discovery roots.

Copies, not symlinks (codex refuses symlinks).  A never-routed CONTROL bundle goes in beside
them: if the control reads as "read" the atime probe in ``telemetry.py`` proves nothing
(git and every CLI touch bundle files regardless).  ``os.utime`` stamps atime == mtime.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from codeverse3d.config import get_settings
from codeverse3d.skills.model import Selection, Skill, SkillsMaterialized
from codeverse3d.skills.prompting import (
    AGENTS_SKILL_ROOT,
    CLAUDE_SKILL_ROOT,
    index_block,
    index_tokens,
    inline_body,
)
from codeverse3d.skills.registry import plan_signals, select

log = logging.getLogger(__name__)

SKILL_ROOTS = (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT)
#: the AGENTS.md/CLAUDE.md body files a workspace already has
BODY_FILES = ("AGENTS.md", "CLAUDE.md")
MARK_BEGIN = "<!-- 3dcode:skills -->"
MARK_END = "<!-- /3dcode:skills -->"

#: the never-routed bundle whose atime falsifies the read probe
CONTROL_NAME = "zz-c3d-read-control"
CONTROL_SKILL_MD = f"""---
name: {CONTROL_NAME}
description: Measurement control for the harness read probe. Never applies to any task; do
  not use it. If you are reading this, note only that you opened it.
license: Apache-2.0
metadata:
  evidence: measured
  verified: "2026-08-25"
  evidence_note: "Not advice. A control file whose access time falsifies the read probe."
---

# Not a skill

This file exists so the harness can tell "the agent read a skill" apart from "something
touched the file". It contains no guidance. Nothing routes it and nothing lists it.
"""
CONTROL_REFERENCE_MD = ("Control reference. If this file's access time moved, the read probe "
                        "cannot distinguish a real read this session.\n")


def write_control(ws_root: Path) -> list[Path]:
    """The control bundle, in both roots; ``materialize_skills`` stamps it with the rest."""
    written: list[Path] = []
    for rel in SKILL_ROOTS:
        d = Path(ws_root) / rel / CONTROL_NAME
        (d / "references").mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(CONTROL_SKILL_MD)
        (d / "references" / "control.md").write_text(CONTROL_REFERENCE_MD)
        written += [d / "SKILL.md", d / "references" / "control.md"]
    return written


def _copy_bundle(skill: Skill, dest_root: Path) -> list[Path]:
    dest = dest_root / skill.name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    shutil.copyfile(skill.path, dest / "SKILL.md")
    written.append(dest / "SKILL.md")
    for rel in skill.references:
        src = skill.dir / rel
        if not src.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
        written.append(out)
    return written


def materialize_skills(ws_root: Path, skills: Sequence[Skill]) -> list[Path]:
    """Copy each bundle into both discovery roots, removing stale bundles; returns every file written."""
    root = Path(ws_root)
    wanted = {s.name for s in skills} | {CONTROL_NAME}
    written: list[Path] = []
    for rel in SKILL_ROOTS:
        base = root / rel
        base.mkdir(parents=True, exist_ok=True)
        for old in base.iterdir():
            if old.is_dir() and old.name not in wanted:
                shutil.rmtree(old, ignore_errors=True)
        for s in skills:
            written += _copy_bundle(s, base)
    written += write_control(root)
    now = time.time()
    for p in written:
        os.utime(p, (now, now))  # atime == mtime: the read probe's zero point
    return written


def write_index(ws_root: Path, text: str) -> list[str]:
    """Replace (or append) the marked skills section in every agent body file."""
    block = f"{MARK_BEGIN}\n{text.strip()}\n{MARK_END}\n" if text.strip() else ""
    out: list[str] = []
    for name in BODY_FILES:
        p = Path(ws_root) / name
        if not p.is_file():
            continue
        body = p.read_text()
        if MARK_BEGIN in body and MARK_END in body:
            head, rest = body.split(MARK_BEGIN, 1)
            _, tail = rest.split(MARK_END, 1)
            body = head + block + tail.lstrip("\n")
        elif block:
            body = body.rstrip("\n") + "\n\n" + block
        p.write_text(body)
        out.append(str(p))
    return out


def attach_skills(
    ws_root: Path,
    *,
    track: str,
    language: str,
    kind: str,
    plan: Any | None = None,
    findings: Any = (),
    single_shot: bool = False,
    library: dict[str, Skill] | None = None,
    only: frozenset[str] | None = None,
) -> SkillsMaterialized:
    """Route → write → return what a session will see.  The one entry point tracks call.

    ``only`` restricts the LIBRARY before routing (an A/B isolates one bundle; filtering
    ``sel`` afterwards would let the cap waste a slot on a bundle not under test).
    """
    cfg = get_settings()
    picked = frozenset(n.strip() for n in cfg.skills_only.split(",") if n.strip()) if only is None else only
    if picked:
        from codeverse3d.skills import all_skills

        lib = dict(all_skills() if library is None else library)
        library = {n: s for n, s in lib.items() if n in picked}
        if not library:
            log.warning("C3D_SKILLS_ONLY=%s matches no bundle in the library; this session attaches nothing",
                        ",".join(sorted(picked)))
    sel: list[Selection] = select(
        track, language, kind, signals=plan_signals(plan), findings=findings, library=library)
    out = SkillsMaterialized(listed=[s.name for s in sel], selections=list(sel),
                             reasons={s.name: s.reason for s in sel}, attached_at=time.time())
    if not sel or single_shot:
        # an empty selection must still sweep, or last round's bundles stay discoverable
        with suppress(OSError):
            materialize_skills(ws_root, [])
    if not sel:
        write_index(ws_root, "")
        return out

    if single_shot:
        # no read loop: a pointer to a file it cannot open would be pure cost
        name, text = inline_body(sel)
        out.inlined = name
        out.index_tokens = 0
        if not name:
            log.warning("single-shot: every routed body is over the inline cap; nothing inlined")
        write_index(ws_root, "")
        return out

    try:
        materialize_skills(ws_root, [s.skill for s in sel])
    except OSError as e:  # a workspace we cannot write is a run problem, not a skills problem
        log.warning("skills not materialised into %s: %s", ws_root, e)
        return out
    out.index_tokens = index_tokens(sel)
    write_index(ws_root, index_block(sel))
    return out


__all__ = ["BODY_FILES", "CONTROL_NAME", "MARK_BEGIN", "MARK_END", "SKILL_ROOTS",
           "attach_skills", "materialize_skills", "write_control", "write_index"]
