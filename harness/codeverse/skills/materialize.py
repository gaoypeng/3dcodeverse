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
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse.skills.config import skills_max, skills_unverified
from codeverse.skills.model import Selection, Skill, SkillsMaterialized
from codeverse.skills.prompting import (
    AGENTS_SKILL_ROOT,
    CLAUDE_SKILL_ROOT,
    index_block,
    index_tokens,
    inline_body,
)
from codeverse.skills.router import plan_signals, select

log = logging.getLogger(__name__)

SKILL_ROOTS = (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT)
#: the AGENTS.md/GEMINI.md/CLAUDE.md body files a workspace already has
BODY_FILES = ("AGENTS.md", "GEMINI.md", "CLAUDE.md")
MARK_BEGIN = "<!-- 3dcv:skills -->"
MARK_END = "<!-- /3dcv:skills -->"


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
    wanted = {s.name for s in skills}
    written: list[Path] = []
    for rel in SKILL_ROOTS:
        base = root / rel
        base.mkdir(parents=True, exist_ok=True)
        for old in base.iterdir():
            if old.is_dir() and old.name not in wanted:
                shutil.rmtree(old, ignore_errors=True)
        for s in skills:
            written += _copy_bundle(s, base)
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
    agent_kind: str,
    plan: Any | None = None,
    findings: Any = (),
    single_shot: bool = False,
    library: dict[str, Skill] | None = None,
    max_skills: int | None = None,
    allow_unverified: bool | None = None,
) -> SkillsMaterialized:
    """Route → write → return what a session will see.  The one entry point tracks call.

    The caller decides whether the feature is on (``config.skills_enabled``); this
    function assumes it is, so a test can attach without setting the environment.
    """
    sel: list[Selection] = select(
        track, language, kind,
        signals=plan_signals(plan), findings=findings, library=library,
        max_skills=skills_max() if max_skills is None else max_skills,
        allow_unverified=skills_unverified() if allow_unverified is None else allow_unverified,
    )
    out = SkillsMaterialized(listed=[s.name for s in sel], selections=list(sel),
                             reasons={s.name: s.reason for s in sel})
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
    out.index_tokens = index_tokens(sel, agent_kind)
    write_index(ws_root, index_block(sel, agent_kind))
    return out


__all__ = ["BODY_FILES", "MARK_BEGIN", "MARK_END", "SKILL_ROOTS", "attach_skills",
           "materialize_skills", "write_index"]
