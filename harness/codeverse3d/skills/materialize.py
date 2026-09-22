"""Write the routed bundles into a workspace, as real files, twice.

WHY twice: the discovery roots were read out of the shipped binaries, not assumed.
gemini-cli, codex and agy all read ``<ws>/.agents/skills/<name>/SKILL.md``; claude-code
2.1 reads ``<ws>/.claude/skills/<name>/SKILL.md`` and has **no** ``.agents`` skill root at
all (its ``.agents`` strings are plugin-migration paths).  So the same bytes go to both.

WHY copies and not symlinks: codex refuses them outright ("Symbolic links are not allowed
in skills").  A 2-10 KB duplicate in a disposable workspace costs nothing.

WHY ``os.utime`` at the end: the read probe in ``telemetry.py`` says a bundle was opened
when ``atime > mtime``.  That only means anything if the two start equal, so
materialisation stamps them equal instead of trusting whatever the copy left behind.

WHY a CONTROL bundle goes in beside them: because ``atime > mtime`` turned out to be a
much weaker signal than the design assumed, and measured on 2026-08-25 it fires with
nobody reading anything.  Two independent causes, both verified:

* ``Workspace.changed_files`` runs ``git add -A -N`` then ``git diff --numstat`` after
  every agent session, and git reads each untracked file to compute the diff.  On a real
  git workspace that alone flips all four files of a bundle to "read".
* all four CLIs (codex 0.149.0, claude-code 2.1.245, agy 1.1.20, gemini-cli 0.53.0) open
  ``references/*.md`` while activating a skill, including on a task whose description does
  not match and with the prompt telling them not to activate anything.

Neither can be argued away, so the probe carries its own falsification: one extra bundle
that is never routed, never indexed and never mentioned.  If IT comes back "read", the
session's atime evidence proves nothing and ``deep_read_rate`` reports ``None`` instead of
a confident 100%.
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

from codeverse3d.skills.config import SKILLS_ONLY_ENV, skills_max, skills_only, skills_unverified
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
#: the AGENTS.md/GEMINI.md/CLAUDE.md body files a workspace already has
BODY_FILES = ("AGENTS.md", "GEMINI.md", "CLAUDE.md")
MARK_BEGIN = "<!-- 3dcode:skills -->"
MARK_END = "<!-- /3dcode:skills -->"

#: the never-routed bundle whose atime falsifies the probe.  Named so it sorts away from
#: the real ones, and worded so an agent that does read it has been told it is a control.
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
    """Materialise the control bundle into both roots, stamped like the real ones."""
    written: list[Path] = []
    for rel in SKILL_ROOTS:
        d = Path(ws_root) / rel / CONTROL_NAME
        (d / "references").mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(CONTROL_SKILL_MD)
        (d / "references" / "control.md").write_text(CONTROL_REFERENCE_MD)
        written += [d / "SKILL.md", d / "references" / "control.md"]
    now = time.time()
    for p in written:
        os.utime(p, (now, now))
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
    """Copy each bundle into both discovery roots; returns every file written.

    Stale bundles from a previous round are removed, so the workspace always shows the
    set this round actually routed — otherwise the read probe would credit a skill that
    a later round no longer attaches.
    """
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
    """Replace (or append) the marked skills section in every agent body file.

    A marked section rather than a rewrite of the body: ``agents/materialize.py`` writes
    the body once per run, but the routed set changes per round, and rewriting the whole
    file from here would duplicate its knowledge of what belongs in it.
    """
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
    max_skills: int | None = None,
    allow_unverified: bool | None = None,
    only: frozenset[str] | None = None,
) -> SkillsMaterialized:
    """Route → write → return what a session will see.  The one entry point tracks call.

    The caller decides whether the feature is on (``config.skills_enabled``); this
    function assumes it is, so a test can attach without setting the environment.

    ``only`` (env ``C3D_SKILLS_ONLY``) restricts the LIBRARY before routing, which is
    what an effect A/B needs: the delta then belongs to one bundle instead of to whatever
    set of five the router happened to pick.  Restricting the library — rather than
    filtering ``sel`` afterwards — also stops the cap from spending a slot on a bundle
    that is not under test and then dropping the one that is.
    """
    picked = skills_only() if only is None else only
    if picked:
        from codeverse3d.skills import all_skills

        lib = dict(all_skills() if library is None else library)
        library = {n: s for n, s in lib.items() if n in picked}
        if not library:
            log.warning("%s=%s matches no bundle in the library; this session attaches nothing",
                        SKILLS_ONLY_ENV, ",".join(sorted(picked)))
    sel: list[Selection] = select(
        track, language, kind,
        signals=plan_signals(plan), findings=findings, library=library,
        max_skills=skills_max() if max_skills is None else max_skills,
        allow_unverified=skills_unverified() if allow_unverified is None else allow_unverified,
    )
    out = SkillsMaterialized(listed=[s.name for s in sel], selections=list(sel),
                             reasons={s.name: s.reason for s in sel}, attached_at=time.time())
    if not sel or single_shot:
        # an empty selection is a legal desired set: the sweep must still run, or last
        # round's bundles stay live where the native CLIs discover skills by directory
        with suppress(OSError):
            materialize_skills(ws_root, [])
    if not sel:
        write_index(ws_root, "")
        return out

    if single_shot:
        # No read loop: a pointer to a file it cannot open would be pure cost.
        name, text = inline_body(sel)
        out.inlined = name
        out.index_tokens = 0
        if not name:
            out.warnings.append("single-shot: every routed body is over the inline cap; nothing inlined")
        write_index(ws_root, "")
        return out

    try:
        paths = materialize_skills(ws_root, [s.skill for s in sel])
    except OSError as e:  # a workspace we cannot write is a run problem, not a skills problem
        out.warnings.append(f"could not materialise skills: {e}")
        log.warning("skills not materialised into %s: %s", ws_root, e)
        return out
    out.paths = [str(p) for p in paths]
    out.index_tokens = index_tokens(sel)
    write_index(ws_root, index_block(sel))
    return out


__all__ = ["BODY_FILES", "CONTROL_NAME", "MARK_BEGIN", "MARK_END", "SKILL_ROOTS",
           "attach_skills", "materialize_skills", "write_control", "write_index"]
